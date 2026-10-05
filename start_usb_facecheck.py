"""Khoi dong cau noi ESP32 USB-OTG va giao dien FaceCheck cung luc."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HEALTH_URL = "http://127.0.0.1:8766/health"
STREAM_URL = "http://127.0.0.1:8766/stream"


def bridge_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
            status = json.load(response)
        return bool(status.get("ok"))
    except Exception:
        return False


def main() -> int:
    bridge = None
    if not bridge_ready():
        bridge = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "tools" / "usb_camera_bridge.py")],
            cwd=ROOT,
        )
        print("[START] Dang cho camera USB-OTG...")
        for _ in range(30):
            if bridge.poll() is not None:
                print("[START] Cau noi USB da dung bat thuong.")
                return bridge.returncode or 1
            if bridge_ready():
                break
            time.sleep(0.5)
        else:
            bridge.terminate()
            print("[START] Khong nhan duoc camera USB sau 15 giay.")
            return 1

    print(f"[START] Camera san sang: {STREAM_URL}")
    environment = os.environ.copy()
    environment["FACECHECK_CAMERA_URL"] = STREAM_URL
    environment["FACECHECK_CAMERA_PROFILE"] = "embedded_ov3660"
    try:
        return subprocess.call(
            [sys.executable, str(ROOT / "web_admin.py")],
            cwd=ROOT,
            env=environment,
        )
    finally:
        if bridge is not None and bridge.poll() is None:
            bridge.terminate()
            try:
                bridge.wait(timeout=3)
            except subprocess.TimeoutExpired:
                bridge.kill()


if __name__ == "__main__":
    raise SystemExit(main())
