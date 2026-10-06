"""Entry point giao diện HTML/CSS desktop của FaceCheck."""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import webbrowser
import json
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn


HOST = "127.0.0.1"
PORT = 8765
DISPLAY_PORT = 8767
URL = f"http://{HOST}:{PORT}"
KIOSK_URL = f"{URL}/?view=kiosk#recognition"
ADMIN_URL = f"{URL}/#dashboard"
SIMULATED_ADMIN_URL = f"{URL}/?view=admin#dashboard"


def _window_specs(simulate_pi=False):
    """Return pywebview window definitions without starting the UI runtime."""
    if simulate_pi:
        return [
            {
                "title": "FaceCheck - Kiosk 3.5 inch (480x320)",
                "url": KIOSK_URL,
                "width": 480,
                "height": 320,
                "min_size": (320, 240),
            },
            {
                "title": "FaceCheck - Quản trị",
                "url": SIMULATED_ADMIN_URL,
                "width": 1440,
                "height": 900,
                "min_size": (1150, 720),
            },
        ]
    return [
        {
            "title": "FaceCheck - HỆ THỐNG ĐIỂM DANH & QUẢN TRỊ AI",
            "url": URL,
            "width": 1440,
            "height": 900,
            "min_size": (1150, 720),
        }
    ]


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Khoi dong giao dien FaceCheck")
    parser.add_argument(
        "--simulate-display", "--simulate-pi",
        dest="simulate_display",
        action="store_true",
        help="Mo dong thoi man TFT 3.5 inch 480x320 va cua so quan tri tren laptop",
    )
    return parser.parse_args(argv)


def _make_server(app_name="web_backend.app:app", host=HOST, port=PORT):
    config = uvicorn.Config(
        app_name, host=host, port=port,
        log_level="warning", access_log=False,
    )
    return uvicorn.Server(config)


def _display_host():
    configured = os.getenv("FACECHECK_DISPLAY_HOST", "").strip()
    if configured:
        return configured
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.1.1", 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def _is_facecheck_server(timeout=0.4):
    """Chỉ chấp nhận tái sử dụng đúng backend FaceCheck, không chỉ kiểm tra cổng."""
    try:
        with urlopen(f"{URL}/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return (
            response.status == 200
            and payload.get("ok") is True
            and payload.get("service") == "FaceCheck Web UI"
        )
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


def _wait_for_facecheck_server(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_facecheck_server():
            return True
        time.sleep(0.2)
    return False


def _reserve_server_socket(host=HOST, port=PORT):
    """Giữ cổng trước khi Uvicorn import app để tiến trình thừa không mở camera."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((host, port))
        listener.listen(128)
        return listener
    except OSError:
        listener.close()
        return None


def main(simulate_pi=False):
    listener = _reserve_server_socket()
    owns_server = listener is not None
    uvicorn_server = None
    server_thread = None
    display_listener = None
    display_server = None
    display_thread = None

    if owns_server:
        uvicorn_server = _make_server()
        server_thread = threading.Thread(
            target=uvicorn_server.run,
            kwargs={"sockets": [listener]},
            daemon=True,
            name="facecheck-web-server",
        )
        server_thread.start()
        if not _wait_for_facecheck_server():
            uvicorn_server.should_exit = True
            server_thread.join(timeout=5)
            listener.close()
            raise RuntimeError("Backend FaceCheck không khởi động trong thời gian cho phép")
        display_host = _display_host()
        display_listener = _reserve_server_socket(display_host, DISPLAY_PORT)
        if display_listener is None:
            raise RuntimeError(
                f"Khong the mo cong man hinh {display_host}:{DISPLAY_PORT}. "
                "Hay kiem tra dia chi Wi-Fi hoac chuong trinh dang chiem cong."
            )
        display_server = _make_server(
            "web_backend.app:display_app", display_host, DISPLAY_PORT
        )
        display_thread = threading.Thread(
            target=display_server.run,
            kwargs={"sockets": [display_listener]},
            daemon=True,
            name="facecheck-display-server",
        )
        display_thread.start()
        print(f"[DISPLAY] Kenh TFT bao mat: http://{display_host}:{DISPLAY_PORT}")
    elif not _wait_for_facecheck_server(timeout=5):
        raise RuntimeError(
            f"Cổng {PORT} đang được một ứng dụng khác sử dụng. "
            "Hãy đóng ứng dụng đó rồi mở lại FaceCheck."
        )
    else:
        print("[WEB UI] FaceCheck đã chạy; tái sử dụng backend và camera hiện có.")

    try:
        import webview
    except ImportError:
        urls = [spec["url"] for spec in _window_specs(simulate_pi)]
        print(f"[WEB UI] Chưa cài pywebview; mở trình duyệt tại {', '.join(urls)}")
        for window_url in urls:
            webbrowser.open(window_url)
        if not owns_server:
            return
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=0.5)
        except KeyboardInterrupt:
            uvicorn_server.should_exit = True
            server_thread.join(timeout=5)
        finally:
            listener.close()
        return
    for spec in _window_specs(simulate_pi):
        webview.create_window(
            spec["title"],
            spec["url"],
            width=spec["width"],
            height=spec["height"],
            min_size=spec["min_size"],
            background_color="#F6F8FC",
        )
    try:
        webview.start(debug=False)
    finally:
        if owns_server:
            if display_server is not None:
                display_server.should_exit = True
            if display_thread is not None:
                display_thread.join(timeout=5)
            if display_listener is not None:
                display_listener.close()
            uvicorn_server.should_exit = True
            server_thread.join(timeout=5)
            listener.close()


if __name__ == "__main__":
    main(simulate_pi=_parse_args().simulate_display)
