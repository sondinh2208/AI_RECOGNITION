"""Nguon camera co giao dien giong cv2.VideoCapture, uu tien frame MJPEG moi nhat."""

from __future__ import annotations

import threading
import time
import urllib.request

import cv2
import numpy as np


class LatestMjpegCapture:
    """Doc MJPEG o luong rieng de FFmpeg khong tich frame cu gay delay."""

    def __init__(self, url: str, open_timeout: float = 5.0):
        self.url = url
        self._condition = threading.Condition()
        self._frame = None
        self._jpeg = None
        self._sequence = 0
        self._last_read_sequence = 0
        self._running = True
        self._response = None
        self._opened = threading.Event()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="mjpeg-latest-frame",
            daemon=True,
        )
        self._thread.start()
        self._opened.wait(open_timeout)

    def _reader_loop(self):
        while self._running:
            request = urllib.request.Request(
                self.url,
                headers={"Cache-Control": "no-cache", "Connection": "close"},
            )
            try:
                with urllib.request.urlopen(request, timeout=4.0) as response:
                    self._response = response
                    buffer = bytearray()
                    while self._running:
                        chunk = response.read(4096)
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
                            frame = cv2.imdecode(
                                np.frombuffer(jpeg, dtype=np.uint8),
                                cv2.IMREAD_COLOR,
                            )
                            if frame is None:
                                continue
                            with self._condition:
                                self._frame = frame
                                self._jpeg = jpeg
                                self._sequence += 1
                                self._opened.set()
                                self._condition.notify_all()
            # release() dong socket de danh thuc read(); http.client co the nem
            # AttributeError neu file socket bi xoa dung luc dang doc chunked.
            except (OSError, TimeoutError, ValueError, AttributeError):
                pass
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
            return self._sequence, self._jpeg

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
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def set(self, _property, _value) -> bool:
        return False


def open_camera_capture(source, backend=None):
    if isinstance(source, str) and source.lower().startswith(("http://", "https://")):
        return LatestMjpegCapture(source)
    if backend is not None and backend != cv2.CAP_ANY:
        return cv2.VideoCapture(source, backend)
    return cv2.VideoCapture(source)
