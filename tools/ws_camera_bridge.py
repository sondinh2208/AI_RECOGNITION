"""Nhan JPEG binary tu ESP32 qua WebSocket va phat frame moi nhat noi bo."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect


MAX_JPEG_BYTES = 512_000


def local_camera_token() -> str:
    configured = os.getenv("FACECHECK_CAMERA_TOKEN", "").strip()
    if configured:
        return configured
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            if raw_line.startswith("FACECHECK_CAMERA_TOKEN="):
                return raw_line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


@dataclass
class LatestFrameStore:
    condition: threading.Condition = field(default_factory=threading.Condition)
    jpeg: bytes | None = None
    sequence: int = 0
    received_at: float = 0.0
    connected: bool = False
    active_connections: int = 0
    total_frames: int = 0
    session_frames: int = 0
    recent_frames: deque = field(default_factory=deque)
    invalid_frames: int = 0
    started_at: float = field(default_factory=time.monotonic)
    session_id: int = 0

    def begin_session(self) -> int:
        with self.condition:
            self.session_id += 1
            self.active_connections = 1
            self.connected = True
            self.started_at = time.monotonic()
            self.session_frames = 0
            self.recent_frames.clear()
            self.condition.notify_all()
            return self.session_id

    def end_session(self, session_id: int) -> None:
        with self.condition:
            # A superseded socket must never mark the replacement as offline.
            if session_id != self.session_id:
                return
            self.active_connections = 0
            self.connected = False
            self.condition.notify_all()

    def update(self, jpeg: bytes, session_id: int | None = None) -> bool:
        if (
            len(jpeg) < 4
            or len(jpeg) > MAX_JPEG_BYTES
            or not jpeg.startswith(b"\xff\xd8")
            or not jpeg.endswith(b"\xff\xd9")
        ):
            with self.condition:
                self.invalid_frames += 1
            return False
        with self.condition:
            if session_id is not None and session_id != self.session_id:
                return False
            self.jpeg = jpeg
            self.sequence += 1
            self.total_frames += 1
            self.session_frames += 1
            self.received_at = time.monotonic()
            self.recent_frames.append(self.received_at)
            while self.recent_frames and self.received_at - self.recent_frames[0] > 5.0:
                self.recent_frames.popleft()
            self.condition.notify_all()
        return True

    def snapshot(self) -> tuple[int, bytes | None]:
        with self.condition:
            return self.sequence, self.jpeg

    def status(self) -> dict:
        with self.condition:
            age_ms = (
                round((time.monotonic() - self.received_at) * 1000)
                if self.received_at
                else None
            )
            now = time.monotonic()
            while self.recent_frames and now - self.recent_frames[0] > 5.0:
                self.recent_frames.popleft()
            recent_elapsed = (
                self.recent_frames[-1] - self.recent_frames[0]
                if len(self.recent_frames) > 1 else 0.0
            )
            recent_fps = (
                (len(self.recent_frames) - 1) / recent_elapsed
                if recent_elapsed > 0 else 0.0
            )
            return {
                "ok": self.connected and age_ms is not None and age_ms < 3000,
                "service": "FaceCheck WebSocket Camera",
                "connected": self.connected,
                "active_connections": self.active_connections,
                "session_id": self.session_id,
                "frames": self.total_frames,
                "invalid_frames": self.invalid_frames,
                "frame_age_ms": age_ms,
                "average_fps": round(recent_fps, 2),
            }


STORE = LatestFrameStore()
CAMERA_TOKEN = ""
CAMERA_CONNECTION_LOCK = asyncio.Lock()
ACTIVE_CAMERA: WebSocket | None = None


async def camera_socket(websocket: WebSocket):
    global ACTIVE_CAMERA
    supplied = websocket.query_params.get("token", "")
    if not CAMERA_TOKEN or not secrets.compare_digest(supplied, CAMERA_TOKEN):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    async with CAMERA_CONNECTION_LOCK:
        previous = ACTIVE_CAMERA
        ACTIVE_CAMERA = websocket
        session_id = STORE.begin_session()
    if previous is not None and previous is not websocket:
        try:
            await previous.close(code=1012, reason="Camera session replaced")
        except (RuntimeError, WebSocketDisconnect):
            pass
    print(
        f"[WS-CAMERA] ESP32 ket noi tu {websocket.client} | session={session_id}",
        flush=True,
    )
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            payload = message.get("bytes")
            if payload is not None:
                STORE.update(payload, session_id=session_id)
    except WebSocketDisconnect:
        pass
    finally:
        async with CAMERA_CONNECTION_LOCK:
            if ACTIVE_CAMERA is websocket:
                ACTIVE_CAMERA = None
        STORE.end_session(session_id)
        print(f"[WS-CAMERA] ESP32 ngat ket noi | session={session_id}", flush=True)


class LocalCameraHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if self.path == "/health":
            import json
            body = json.dumps(STORE.status()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/stream":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        last_sequence = -1
        try:
            while True:
                with STORE.condition:
                    STORE.condition.wait_for(
                        lambda: STORE.sequence != last_sequence or not STORE.connected,
                        timeout=1.0,
                    )
                    sequence, jpeg = STORE.sequence, STORE.jpeg
                if jpeg is None or sequence == last_sequence:
                    continue
                last_sequence = sequence
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(jpeg)).encode("ascii")
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


app = Starlette(routes=[WebSocketRoute("/camera", camera_socket)])


def main() -> None:
    global CAMERA_TOKEN
    parser = argparse.ArgumentParser(description="FaceCheck WebSocket camera bridge")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--local-port", type=int, default=8769)
    parser.add_argument("--token", default=local_camera_token())
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("FACECHECK_CAMERA_TOKEN khong duoc de trong")
    CAMERA_TOKEN = args.token
    local_server = ThreadingHTTPServer(("127.0.0.1", args.local_port), LocalCameraHandler)
    threading.Thread(
        target=local_server.serve_forever,
        name="ws-camera-local-http",
        daemon=True,
    ).start()
    print(
        f"[WS-CAMERA] Cho ESP32 tai ws://<IP-laptop>:{args.port}/camera",
        flush=True,
    )
    print(
        f"[WS-CAMERA] Stream noi bo: http://127.0.0.1:{args.local_port}/stream",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
