"""Send-only, bounded Telegram photo alerts; ESP32 is the only bot poller."""

import json
import os
import queue
import threading
import time
import urllib.request
import uuid
from collections import deque
from datetime import datetime

import cv2
from config import KIOSK_FAILED_RETRY_SECONDS


class TelegramFaceAlerts:
    def __init__(self, token="", chat_id="", *, sender=None, clock=time.monotonic):
        self.enabled = bool(token and chat_id)
        self._token, self._chat_id = token, chat_id
        self._sender = sender or self._send_photo
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = deque(maxlen=3)
        self._last_failure = None
        self._last_alert = None
        self._queue = queue.Queue(maxsize=2)
        self._stop = threading.Event()
        self._thread = None
        self._sent = 0
        self._send_errors = 0

    @classmethod
    def from_environment(cls):
        enabled = os.getenv("FACECHECK_TELEGRAM_FACE_ALERTS", "0").lower() in {"1", "true", "yes"}
        return cls(
            os.getenv("FACECHECK_TELEGRAM_BOT_TOKEN", "").strip() if enabled else "",
            os.getenv("FACECHECK_TELEGRAM_CHAT_ID", "").strip() if enabled else "",
        )

    def start(self):
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telegram-photo-alert", daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._discard_pending()

    def _discard_pending(self):
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                return

    def success(self):
        with self._lock:
            self._failures.clear()
            self._last_failure = None

    def failure(self, face_image):
        """Count spaced final decisions, without requiring the face to leave."""
        if not self.enabled:
            return False
        now = self._clock()
        with self._lock:
            if self._last_failure is not None and now - self._last_failure < KIOSK_FAILED_RETRY_SECONDS:
                return False
            self._last_failure = now
            while self._failures and now - self._failures[0] > 60:
                self._failures.popleft()
            self._failures.append(now)
            if len(self._failures) < 3 or (self._last_alert is not None and now - self._last_alert < 60):
                return False
            if face_image is None or not getattr(face_image, "size", 0):
                return False
            caption = (
                "CẢNH BÁO FACECHECK\nCó ít nhất 3 lượt xác thực khuôn mặt thất bại "
                "trong 60 giây tại cửa chính.\n"
                f"Thời gian: {datetime.now():%H:%M:%S %d/%m/%Y}.\n"
                "Ảnh từ lượt thất bại vừa xảy ra. Danh tính chưa được xác minh."
            )
            try:
                self._queue.put_nowait((now, face_image.copy(), caption))
            except queue.Full:
                return False
            self._last_alert = now
            self._failures.clear()
            return True

    def snapshot(self):
        with self._lock:
            return {"enabled": self.enabled, "sent": self._sent,
                    "send_errors": self._send_errors, "pending": self._queue.qsize()}

    def _send_photo(self, jpeg, caption):
        boundary = "facecheck-" + uuid.uuid4().hex
        parts = []
        for name, value in (("chat_id", self._chat_id), ("caption", caption)):
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                          f'{value}\r\n').encode("utf-8"))
        parts.extend([
            (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="alert.jpg"\r\n'
             'Content-Type: image/jpeg\r\n\r\n').encode("ascii"),
            jpeg, f"\r\n--{boundary}--\r\n".encode("ascii"),
        ])
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/sendPhoto",
            data=b"".join(parts), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            if not json.load(response).get("ok"):
                raise RuntimeError("telegram_photo_failed")

    def _run(self):
        while not self._stop.is_set():
            try:
                queued_at, image, caption = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._clock() - queued_at > 60 or self._stop.is_set():
                    continue
                height, width = image.shape[:2]
                if max(height, width) > 640:
                    scale = 640 / max(height, width)
                    image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
                ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    raise RuntimeError("jpeg_encode_failed")
                # Do not blindly retry a timeout: Telegram may already have sent it.
                self._sender(encoded.tobytes(), caption)
                with self._lock:
                    self._sent += 1
            except Exception:
                with self._lock:
                    self._send_errors += 1
                print("[TELEGRAM] Gui canh bao anh that bai; xem lai ket noi/cau hinh.")
            finally:
                image = None
                encoded = None
                self._queue.task_done()
