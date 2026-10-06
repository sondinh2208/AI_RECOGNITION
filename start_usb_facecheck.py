"""Khoi dong cau noi ESP32 USB-OTG va giao dien FaceCheck cung luc."""

from __future__ import annotations

import argparse
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


def load_local_environment(environment):
    """Nap cau hinh thiet bi cuc bo; tep .env khong duoc Git theo doi."""
    path = ROOT / ".env"
    if not path.exists():
        return environment
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        environment.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return environment


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Khoi dong FaceCheck voi camera ESP32-S3 qua USB-OTG",
    )
    parser.add_argument(
        "--simulate-display",
        action="store_true",
        help="Mo them cua so TFT 3.5 inch 480x320 ben canh giao dien quan tri",
    )
    return parser.parse_args(argv)


def bridge_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1) as response:
            status = json.load(response)
        return bool(status.get("ok"))
    except Exception:
        return False


def main(simulate_display=False) -> int:
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
    environment = load_local_environment(os.environ.copy())
    environment["FACECHECK_CAMERA_URL"] = STREAM_URL
    environment["FACECHECK_CAMERA_PROFILE"] = "embedded_ov3660"
    command = [sys.executable, str(ROOT / "web_admin.py")]
    if simulate_display:
        command.append("--simulate-display")
    try:
        return subprocess.call(
            command,
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
    arguments = parse_args()
    raise SystemExit(main(simulate_display=arguments.simulate_display))
