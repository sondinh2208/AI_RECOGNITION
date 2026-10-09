"""Luồng đọc camera mạng không đệm (Bufferless Capture) - Luôn cung cấp frame mới nhất, chống giật FPS."""

from __future__ import annotations

import os
import threading
import time
import cv2
import numpy as np


class BufferlessCapture:
    """Camera capture chạy ngầm luồng riêng (grabber thread).
    - Luôn giữ frame MỚI NHẤT, tự động loại bỏ mọi frame cũ tích lũy trong hàng đợi mạng/FFmpeg.
    - read() trả về ngay lập tức không block luồng xử lý AI hay UI.
    - Tự động kết nối lại khi Wi-Fi hoặc socket camera bị gián đoạn.
    - Tương thích 100% với giao diện cv2.VideoCapture chuẩn.
    """

    def __init__(self, source, backend=cv2.CAP_ANY):
        self._source = source
        self._backend = backend
        self._lock = threading.Lock()
        self._new_frame_event = threading.Event()
        self._running = True
        self._capture = None
        self._latest_frame = None
        self._frame_id = 0
        self._last_frame_time = 0.0
        self._width = 640
        self._height = 480
        self._fps = 30.0

        # Cấu hình FFmpeg độ trễ thấp tối đa khi nguồn là URL mạng
        if isinstance(self._source, str) and ("://" in self._source or self._source.startswith("http")):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "fflags;nobuffer|discardcorrupt|flush_packets|flags;low_delay|max_delay;50000"
            )

        self._open_internal()

        self._thread = threading.Thread(
            target=self._capture_worker,
            daemon=True,
            name=f"bufferless-cam-{id(self)}"
        )
        self._thread.start()

    def _open_internal(self) -> bool:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

        try:
            if isinstance(self._source, str):
                backend = cv2.CAP_FFMPEG if self._backend == cv2.CAP_ANY else self._backend
                self._capture = cv2.VideoCapture(self._source, backend)
                if not self._capture.isOpened():
                    self._capture = cv2.VideoCapture(self._source)
            else:
                self._capture = cv2.VideoCapture(self._source, self._backend)
                if not self._capture.isOpened() and self._backend != cv2.CAP_ANY:
                    self._capture = cv2.VideoCapture(self._source)

            if self._capture and self._capture.isOpened():
                self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # Đọc thử 1 frame để lấy kích thước ban đầu
                ret, frame = self._capture.read()
                if ret and frame is not None:
                    self._latest_frame = frame
                    self._height, self._width = frame.shape[:2]
                    self._last_frame_time = time.perf_counter()
                    self._new_frame_event.set()
                    return True
        except Exception as exc:
            print(f"[BUFFERLESS-CAM] Loi khi mo camera: {exc}")

        return bool(self._capture and self._capture.isOpened())

    def _capture_worker(self):
        """Worker thread ngầm đọc camera liên tục, giữ frame tươi mới nhất."""
        consecutive_failures = 0
        last_reconnect_at = 0.0

        while self._running:
            if self._capture is None or not self._capture.isOpened():
                now = time.perf_counter()
                if now - last_reconnect_at > 2.0:
                    last_reconnect_at = now
                    print(f"[BUFFERLESS-CAM] Dang ket noi lai camera {self._source}...")
                    self._open_internal()
                time.sleep(0.05)
                continue

            try:
                ret, frame = self._capture.read()
            except Exception:
                ret, frame = False, None

            if ret and frame is not None:
                consecutive_failures = 0
                now = time.perf_counter()
                with self._lock:
                    self._latest_frame = frame
                    self._frame_id += 1
                    self._last_frame_time = now
                    self._height, self._width = frame.shape[:2]
                self._new_frame_event.set()
            else:
                consecutive_failures += 1
                if consecutive_failures > 25:
                    now = time.perf_counter()
                    if now - last_reconnect_at > 2.0:
                        last_reconnect_at = now
                        print(f"[BUFFERLESS-CAM] Mat tin hieu camera ({consecutive_failures} frames loi), thu ket noi lai...")
                        self._open_internal()
                        consecutive_failures = 0
                time.sleep(0.01)

    def read(self, timeout=0.15) -> tuple[bool, np.ndarray | None]:
        """Trả về frame mới nhất ngay lập tức, không chờ đợi và không bị đọng hàng đợi cũ."""
        if not self._running:
            return False, None

        # Chờ có frame mới với timeout nhỏ (tránh block)
        if self._new_frame_event.wait(timeout):
            self._new_frame_event.clear()

        with self._lock:
            if self._latest_frame is not None:
                # Nếu frame còn mới (< 1.5 giây), trả về bản copy
                if time.perf_counter() - self._last_frame_time < 1.5:
                    return True, self._latest_frame.copy()

        return False, None

    def grab(self) -> bool:
        return self._latest_frame is not None

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        return self.read(timeout=0.01)

    def isOpened(self) -> bool:
        if not self._running:
            return False
        if self._capture is not None and self._capture.isOpened():
            return True
        return (time.perf_counter() - self._last_frame_time) < 3.0

    def get(self, prop_id: int):
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop_id == cv2.CAP_PROP_FPS:
            return float(self._fps)
        if self._capture is not None:
            try:
                return self._capture.get(prop_id)
            except Exception:
                pass
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            self._width = int(value)
        elif prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            self._height = int(value)
        elif prop_id == cv2.CAP_PROP_FPS:
            self._fps = float(value)
        if self._capture is not None:
            try:
                return bool(self._capture.set(prop_id, value))
            except Exception:
                pass
        return True

    def release(self):
        self._running = False
        self._new_frame_event.set()
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None


def open_capture(source, backend=cv2.CAP_ANY):
    """Mở camera phù hợp: Dùng BufferlessCapture cho luồng mạng URL để loại bỏ hoàn toàn đơ/giật lag buffer."""
    if isinstance(source, str) and ("://" in source or source.startswith("http")):
        return BufferlessCapture(source, backend=backend)
    # Với camera USB/DShow cục bộ
    if backend == cv2.CAP_ANY:
        return cv2.VideoCapture(source)
    return cv2.VideoCapture(source, backend)
