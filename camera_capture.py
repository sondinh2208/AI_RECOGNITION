"""Nguon camera co giao dien giong cv2.VideoCapture, uu tien frame MJPEG moi nhat."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import socket
import threading
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np
from frame_metrics import FrameRateMeter


def _active_ipv4_network():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        if address and not address.startswith(("127.", "169.254.")):
            return ipaddress.ip_network(f"{address}/24", strict=False)
    except OSError:
        return None
    finally:
        probe.close()
    return None


def _probe_facecheck_camera(host: str):
    try:
        with urllib.request.urlopen(f"http://{host}/health", timeout=0.45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("ok") is True and payload.get("camera") == "OV3660":
            return host
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    return None


def _discover_facecheck_camera():
    network = _active_ipv4_network()
    if network is None:
        return None
    hosts = [str(host) for host in network.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as executor:
        for found in executor.map(_probe_facecheck_camera, hosts):
            if found:
                return found
    return None


def _resolve_mjpeg_url(url: str):
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    if not hostname.lower().endswith(".local"):
        return url
    resolved = None
    try:
        resolved = socket.gethostbyname(hostname)
    except OSError:
        resolved = _discover_facecheck_camera()
    if not resolved:
        return url
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{resolved}{port}", parts.path, parts.query, parts.fragment))


class LatestMjpegCapture:
    """Doc MJPEG o luong rieng de FFmpeg khong tich frame cu gay delay."""

    def __init__(self, url: str, open_timeout: float = 5.0):
        self.url = url
        self._resolved_url = url
        self._condition = threading.Condition()
        self._frame = None
        self._jpeg = None
        self._jpeg_sequence = 0
        self._sequence = 0
        self._last_read_sequence = 0
        self._running = True
        self._response = None
        self._opened = threading.Event()
        self._receive_rate = FrameRateMeter()
        self._received_at = None
        self._max_gap_ms = 0.0
        self._decode_ms = 0.0
        self._max_decode_ms = 0.0
        self._decoded_frames = 0
        self._skipped_decode_frames = 0
        self._connections = 0
        self._receive_errors = 0
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="mjpeg-network-reader",
            daemon=True,
        )
        self._decoder_thread = threading.Thread(
            target=self._decoder_loop,
            name="mjpeg-latest-decoder",
            daemon=True,
        )
        # Doc mang phai chay doc lap voi cv2.imdecode. Neu decode/AI bi
        # scheduler tri hoan, socket van duoc rut lien tuc de ESP32 khong bi
        # ket trong send(). Ca hai tang chi giu JPEG/frame moi nhat.
        self._decoder_thread.start()
        self._reader_thread.start()
        self._opened.wait(open_timeout)

    def _store_latest_jpeg(self, jpeg: bytes) -> int:
        """Publish compressed data without waiting for JPEG decoding."""
        with self._condition:
            self._jpeg = jpeg
            self._jpeg_sequence += 1
            if hasattr(self, "_receive_rate"):
                now = time.perf_counter()
                gap = (now - self._received_at) * 1000 if self._received_at is not None else 0.0
                self._received_at = now
                self._max_gap_ms = max(self._max_gap_ms, gap)
                self._receive_rate.tick()
            sequence = self._jpeg_sequence
            self._condition.notify_all()
            return sequence

    def _decoder_loop(self):
        decoded_jpeg_sequence = 0
        while self._running:
            with self._condition:
                ready = self._condition.wait_for(
                    lambda: (
                        self._jpeg_sequence != decoded_jpeg_sequence
                        or not self._running
                    ),
                    timeout=1.0,
                )
                if not ready or not self._running:
                    continue
                # Snapshot moi nhat roi nha lock trong luc OpenCV decode.
                # Frame den trong thoi gian nay se thay frame cu, khong xep hang.
                jpeg = self._jpeg
                jpeg_sequence = self._jpeg_sequence

            decode_started = time.perf_counter()
            frame = cv2.imdecode(
                np.frombuffer(jpeg, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            skipped = max(0, jpeg_sequence - decoded_jpeg_sequence - 1)
            decoded_jpeg_sequence = jpeg_sequence
            if frame is None:
                continue
            with self._condition:
                self._decode_ms = (time.perf_counter() - decode_started) * 1000
                self._max_decode_ms = max(self._max_decode_ms, self._decode_ms)
                self._decoded_frames += 1
                self._skipped_decode_frames += skipped
                self._frame = frame
                self._sequence = jpeg_sequence
                self._opened.set()
                self._condition.notify_all()

    def _reader_loop(self):
        while self._running:
            self._resolved_url = _resolve_mjpeg_url(self.url)
            request = urllib.request.Request(
                self._resolved_url,
                headers={"Cache-Control": "no-cache", "Connection": "close"},
            )
            try:
                with urllib.request.urlopen(request, timeout=4.0) as response:
                    with self._condition:
                        self._connections += 1
                    self._response = response
                    buffer = bytearray()
                    while self._running:
                        read_available = getattr(response, "read1", None)
                        chunk = (
                            read_available(16_384)
                            if callable(read_available)
                            else response.read(4096)
                        )
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        while self._running:
                            start = buffer.find(b"\xff\xd8")
                            if start < 0:
                                if len(buffer) > 512_000:
                                    del buffer[:-2]
                                break
                            end = buffer.find(b"\xff\xd9", start + 2)
                            if end < 0:
                                if start:
                                    del buffer[:start]
                                break
                            jpeg = bytes(buffer[start : end + 2])
                            del buffer[: end + 2]
                            self._store_latest_jpeg(jpeg)
            # release() dong socket de danh thuc read(); http.client co the nem
            # AttributeError neu file socket bi xoa dung luc dang doc chunked.
            except (OSError, TimeoutError, ValueError, AttributeError):
                with self._condition:
                    self._receive_errors += 1
            finally:
                self._response = None
            if self._running:
                time.sleep(0.2)

    def isOpened(self) -> bool:
        return self._running and self._opened.is_set()

    def read(self):
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._sequence != self._last_read_sequence or not self._running,
                timeout=2.0,
            )
            if not ready or not self._running or self._frame is None:
                return False, None
            self._last_read_sequence = self._sequence
            return True, self._frame

    def latest_jpeg(self):
        """Return the newest original JPEG without decoding or re-encoding it."""
        with self._condition:
            return self._jpeg_sequence, self._jpeg

    def transport_snapshot(self):
        """Numeric diagnostics only; no image or identity data."""
        with self._condition:
            return {
                "receive_fps": round(self._receive_rate.value(), 2),
                "received_frames": self._jpeg_sequence,
                "last_frame_age_ms": round((time.perf_counter() - self._received_at) * 1000) if self._received_at is not None else None,
                "jpeg_bytes": len(self._jpeg) if self._jpeg else 0,
                "max_receive_gap_ms": round(self._max_gap_ms),
                "decode_ms": round(self._decode_ms, 2),
                "max_decode_ms": round(self._max_decode_ms, 2),
                "decoded_frames": self._decoded_frames,
                "skipped_decode_frames": self._skipped_decode_frames,
                "reconnects": max(0, self._connections - 1),
                "receive_errors": self._receive_errors,
            }

    def release(self):
        self._running = False
        response = self._response
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        with self._condition:
            self._condition.notify_all()
        current_thread = threading.current_thread()
        if self._reader_thread is not current_thread:
            self._reader_thread.join(timeout=2.0)
        if self._decoder_thread is not current_thread:
            self._decoder_thread.join(timeout=2.0)

    def set(self, _property, _value) -> bool:
        return False


def open_camera_capture(source, backend=None):
    if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
        return LatestMjpegCapture(source)
    if backend is not None and backend != cv2.CAP_ANY:
        return cv2.VideoCapture(source, backend)
    return cv2.VideoCapture(source)
