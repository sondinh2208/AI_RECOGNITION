"""Entry point giao diện HTML/CSS desktop của FaceCheck."""

from __future__ import annotations

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
URL = f"http://{HOST}:{PORT}"


def _make_server():
    config = uvicorn.Config(
        "web_backend.app:app", host=HOST, port=PORT,
        log_level="warning", access_log=False,
    )
    return uvicorn.Server(config)


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


def main():
    listener = _reserve_server_socket()
    owns_server = listener is not None
    uvicorn_server = None
    server_thread = None

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
        print(f"[WEB UI] Chưa cài pywebview; mở trình duyệt tại {URL}")
        webbrowser.open(URL)
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
    window = webview.create_window(
        "FaceCheck - HỆ THỐNG ĐIỂM DANH & QUẢN TRỊ AI",
        URL, width=1440, height=900, min_size=(1150, 720),
        background_color="#F6F8FC",
    )
    try:
        webview.start(debug=False)
    finally:
        if owns_server:
            uvicorn_server.should_exit = True
            server_thread.join(timeout=5)
            listener.close()


if __name__ == "__main__":
    main()
