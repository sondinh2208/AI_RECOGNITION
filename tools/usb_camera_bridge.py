"""Nhan JPEG tu ESP32-S3 USB-OTG va phat MJPEG cuc bo cho FaceCheck."""

from __future__ import annotations

import argparse
import json
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial
from serial.tools import list_ports

try:
    import cv2
    import numpy as np
except ImportError:  # Cau noi van chay neu dung rieng ngoai moi truong FaceCheck.
    cv2 = None
    np = None



MAGIC_V1 = b"FCAM"
MAGIC_V2 = b"FCM2"
MAGICS = (MAGIC_V1, MAGIC_V2)
MAX_JPEG_BYTES = 500_000
FRAME_HEADER = struct.Struct("<III")


class FrameStore:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.jpeg: bytes | None = None
        self.frame_id = 0
        self.received_at = 0.0
        self.frames = 0
        self.corrupt_frames = 0
        self.started_at = time.monotonic()
        self.port = ""
        self.connected = False
        self.protocol = ""
        self.filtered_frames = 0
        self._last_y: float | None = None
        self._last_cr: float | None = None
        self._last_cb: float | None = None
        self._pending_y: float | None = None
        self._pending_cr: float | None = None
        self._pending_cb: float | None = None

    def accept_visual_frame(self, jpeg: bytes) -> bool:
        """Bo frame chop sang hoac chop mau don le, khong tao them hang doi/tre hinh.

        Neu anh sang/mau sac thay doi that, frame ke tiep se co muc tuong tu
        va duoc chap nhan ngay (do tre toi da 1 frame = 125 ms o 8 FPS).
        """
        if cv2 is None or np is None:
            return True
        encoded = np.frombuffer(jpeg, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            return False
        preview = cv2.resize(image, (32, 24), interpolation=cv2.INTER_AREA)
        ycrcb = cv2.cvtColor(preview, cv2.COLOR_BGR2YCrCb)
        y = float(np.median(ycrcb[:, :, 0]))
        cr = float(np.median(ycrcb[:, :, 1]))
        cb = float(np.median(ycrcb[:, :, 2]))

        if self._last_y is None:
            self._last_y = y
            self._last_cr = cr
            self._last_cb = cb
            return True

        # Nguong phoi sang 42/255 va lech mau Cr/Cb 14/255 chi bat cac cu nhay
        # bat thuong, khong anh huong den nguoi chuyen dong binh thuong.
        y_diff = abs(y - self._last_y)
        color_diff = max(abs(cr - self._last_cr), abs(cb - self._last_cb))

        if y_diff <= 42.0 and color_diff <= 14.0:
            self._last_y = y
            self._last_cr = cr
            self._last_cb = cb
            self._pending_y = None
            self._pending_cr = None
            self._pending_cb = None
            return True

        # Hai frame lien tiep cung sang/toi hoac doi mau: day la thay doi moi truong that.
        if (
            self._pending_y is not None
            and abs(y - self._pending_y) <= 18.0
            and abs(cr - self._pending_cr) <= 8.0
            and abs(cb - self._pending_cb) <= 8.0
        ):
            self._last_y = y
            self._last_cr = cr
            self._last_cb = cb
            self._pending_y = None
            self._pending_cr = None
            self._pending_cb = None
            return True

        self._pending_y = y
        self._pending_cr = cr
        self._pending_cb = cb
        self.filtered_frames += 1
        return False


    def update(self, frame_id: int, jpeg: bytes) -> None:
        with self.condition:
            self.jpeg = jpeg
            self.frame_id += 1
            self.received_at = time.monotonic()
            self.frames += 1
            self.condition.notify_all()

    def fps(self) -> float:
        elapsed = time.monotonic() - self.started_at
        return self.frames / elapsed if elapsed > 0 else 0.0

    def discard_corrupt(self) -> int:
        with self.condition:
            self.corrupt_frames += 1
            return self.corrupt_frames


STORE = FrameStore()


def find_usb_port(preferred: str | None) -> str | None:
    if preferred:
        return preferred
    ports = list(list_ports.comports())
    for port in ports:
        if port.device.upper() == "COM12":
            # COM12 la cong bootloader USB-Serial/JTAG de nap firmware, khong phai cong camera runtime.
            continue
        text = f"{port.description} {port.manufacturer or ''}".lower()
        if port.vid == 0x303A or "usb serial device" in text or "espressif" in text:
            return port.device
    return None


def read_exact(device: serial.Serial, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = device.read(length - len(data))
        if not chunk:
            raise TimeoutError("USB camera khong gui du du lieu")
        data.extend(chunk)
    return bytes(data)


def seek_magic(device: serial.Serial) -> bytes:
    window = bytearray()
    while True:
        value = device.read(1)
        if not value:
            raise TimeoutError("Khong nhan duoc frame tu USB camera")
        window.extend(value)
        if len(window) > 4:
            del window[:-4]
        magic = bytes(window)
        if magic in MAGICS:
            return magic


def serial_worker(preferred_port: str | None) -> None:
    while True:
        port = find_usb_port(preferred_port)
        if not port:
            STORE.connected = False
            print("[USB-BRIDGE] Chua tim thay cong USB-OTG; dang thu lai...")
            time.sleep(2)
            continue
        try:
            print(f"[USB-BRIDGE] Dang mo {port}...")
            # Tao cong o trang thai dong de dat control-line truoc khi open.
            # USB-Serial/JTAG cua ESP32-S3 co the vao bootloader neu pyserial
            # mo cong voi RTS va DTR cung duoc assert theo gia tri mac dinh.
            device = serial.Serial()
            device.port = port
            device.baudrate = 2_000_000
            device.timeout = 5
            device.write_timeout = 5
            device.rts = False
            device.dtr = True
            device.open()
            with device:
                device.rts = False
                device.dtr = True
                device.reset_input_buffer()
                STORE.port = port
                STORE.connected = True
                STORE.started_at = time.monotonic()
                STORE.frames = 0
                STORE.corrupt_frames = 0
                STORE.filtered_frames = 0
                STORE._last_y = None
                STORE._last_cr = None
                STORE._last_cb = None
                STORE._pending_y = None
                STORE._pending_cr = None
                STORE._pending_cb = None
                print(f"[USB-BRIDGE] Da ket noi {port}")
                while True:
                    try:
                        magic = seek_magic(device)
                        STORE.protocol = "crc32" if magic == MAGIC_V2 else "legacy"
                        if magic == MAGIC_V2:
                            frame_id, length, expected_crc = FRAME_HEADER.unpack(
                                read_exact(device, FRAME_HEADER.size)
                            )
                        else:
                            frame_id, length = struct.unpack("<II", read_exact(device, 8))
                            expected_crc = None
                        if not 512 <= length <= MAX_JPEG_BYTES:
                            raise ValueError(f"Kich thuoc JPEG khong hop le: {length}")
                        jpeg = read_exact(device, length)
                        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                            raise ValueError("Frame JPEG bi loi; dang dong bo lai")
                        actual_crc = zlib.crc32(jpeg) & 0xFFFFFFFF
                        if expected_crc is not None and actual_crc != expected_crc:
                            discarded = STORE.discard_corrupt()
                            print(
                                "[USB-BRIDGE] Bo frame hong "
                                f"id={frame_id}: CRC {actual_crc:08x} != "
                                f"{expected_crc:08x} (tong={discarded})"
                            )
                            continue
                        if not STORE.accept_visual_frame(jpeg):
                            continue
                        STORE.update(frame_id, jpeg)
                    except TimeoutError:
                        # Giu cong mo: dong/mo CDC lien tuc co the reset hoac lam
                        # gian doan ESP32 khi camera dang khoi tao frame dau tien.
                        continue
                    except ValueError as error:
                        print(f"[USB-BRIDGE] {error}")
                        continue
        except (OSError, serial.SerialException) as error:
            STORE.connected = False
            print(f"[USB-BRIDGE] Mat ket noi: {error}")
            time.sleep(1)


class CameraHandler(BaseHTTPRequestHandler):
    server_version = "FaceCheckUSB/1.0"

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            body = (
                b"<!doctype html><meta charset='utf-8'><title>FaceCheck USB</title>"
                b"<style>body{margin:0;background:#111827;color:white;font-family:Arial;text-align:center}"
                b"img{max-width:95vw;max-height:85vh;margin-top:16px}</style>"
                b"<h2>FaceCheck USB Camera</h2><img src='/stream'>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/capture"):
            with STORE.condition:
                jpeg = STORE.jpeg
            if not jpeg:
                self.send_error(503, "Chua co frame USB")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)
            return
        if self.path.startswith("/health"):
            age_ms = int((time.monotonic() - STORE.received_at) * 1000) if STORE.received_at else None
            body = json.dumps({
                "ok": STORE.connected and STORE.jpeg is not None and (age_ms or 0) < 3000,
                "transport": "usb-otg",
                "protocol": STORE.protocol,
                "port": STORE.port,
                "frames": STORE.frames,
                "corrupt_frames": STORE.corrupt_frames,
                "filtered_frames": STORE.filtered_frames,
                "fps": round(STORE.fps(), 1),
                "frame_age_ms": age_ms,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last_id = -1
            try:
                while True:
                    with STORE.condition:
                        STORE.condition.wait_for(
                            lambda: STORE.frame_id != last_id and STORE.jpeg is not None,
                            timeout=2,
                        )
                        jpeg = STORE.jpeg
                        frame_id = STORE.frame_id
                    if jpeg is None or frame_id == last_id:
                        continue
                    last_id = frame_id
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode("ascii")
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="FaceCheck ESP32 USB camera bridge")
    parser.add_argument("--port", help="Cong USB-OTG, vi du COM12; bo trong de tu tim")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8766)
    args = parser.parse_args()

    threading.Thread(target=serial_worker, args=(args.port,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.http_port), CameraHandler)
    print(f"[USB-BRIDGE] Camera cuc bo: http://{args.host}:{args.http_port}/")
    print(f"[USB-BRIDGE] Stream cho FaceCheck: http://{args.host}:{args.http_port}/stream")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
