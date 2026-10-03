"""Entry point giao diện HTML/CSS desktop của FaceCheck."""

from __future__ import annotations

import socket
import threading
import time
import webbrowser

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


def _wait_for_server(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    uvicorn_server = _make_server()
    server_thread = threading.Thread(
        target=uvicorn_server.run, daemon=True, name="facecheck-web-server"
    )
    server_thread.start()
    if not _wait_for_server():
        raise RuntimeError("Backend FaceCheck không khởi động trong thời gian cho phép")
    try:
        import webview
    except ImportError:
        print(f"[WEB UI] Chưa cài pywebview; mở trình duyệt tại {URL}")
        webbrowser.open(URL)
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=0.5)
        except KeyboardInterrupt:
            uvicorn_server.should_exit = True
            server_thread.join(timeout=5)
        return
    window = webview.create_window(
        "FaceCheck - HỆ THỐNG ĐIỂM DANH & QUẢN TRỊ AI",
        URL, width=1440, height=900, min_size=(1150, 720),
        background_color="#F6F8FC",
    )
    try:
        webview.start(debug=False)
    finally:
        uvicorn_server.should_exit = True
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
