"""Gui lenh OPEN co TTL toi Door Controller va cho ACK ket thuc."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt


def load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gui OPEN toi ESP32. Chuong trinh khong chay neu thieu xac nhan an toan."
    )
    parser.add_argument("--device-id", default=None, help="Mac dinh: MQTT_DOOR_DEVICE_ID/door-01")
    parser.add_argument("--duration-ms", type=int, default=3000, help="Tu 500 den 10000 ms")
    parser.add_argument(
        "--confirm-open",
        action="store_true",
        help="Xac nhan khu vuc cua/khoa an toan de kich relay",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.confirm_open:
        raise SystemExit(
            "Da huy: lenh nay co the mo cua that. Kiem tra an toan roi them --confirm-open."
        )
    if not 500 <= args.duration_ms <= 10000:
        raise SystemExit("--duration-ms phai nam trong khoang 500..10000")

    load_dotenv()
    host = os.getenv("MQTT_HOST", "").strip()
    port = int(os.getenv("MQTT_PORT", "8883"))
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "")
    device_id = args.device_id or os.getenv("MQTT_DOOR_DEVICE_ID", "door-01")
    if not host or not username or not password:
        raise SystemExit("Thieu MQTT_HOST/MQTT_USERNAME/MQTT_PASSWORD trong .env")

    command_topic = f"smartdoor/v1/{device_id}/command"
    ack_topic = f"smartdoor/v1/{device_id}/ack"
    command_id = f"open-{uuid.uuid4().hex[:12]}"
    terminal_ack = threading.Event()
    result: dict[str, object] = {}
    ack_history: list[dict[str, object]] = []

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"facecheck-open-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(username, password)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(client, userdata, flags, reason_code, properties):
        del userdata, flags, properties
        if reason_code != 0:
            result["error"] = f"Ket noi that bai: {reason_code}"
            terminal_ack.set()
            return
        client.subscribe(ack_topic, qos=1)

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        del userdata, mid, reason_codes, properties
        now = int(time.time())
        payload = {
            "command": "OPEN",
            "command_id": command_id,
            "device_id": device_id,
            "duration_ms": args.duration_ms,
            "issued_at": now,
            "expires_at": now + 10,
        }
        info = client.publish(
            command_topic,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            result["error"] = f"Publish that bai: {info.rc}"
            terminal_ack.set()
        else:
            print(f"[OPEN] Da gui {command_id} toi {command_topic}")

    def on_message(client, userdata, message):
        del client, userdata
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if payload.get("command_id") != command_id:
            return

        ack_history.append(payload)
        state = payload.get("state", "unknown")
        print(f"[OPEN] ACK: {state}")
        if state in {"finished", "failed"}:
            result.update(payload)
            terminal_ack.set()

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        timeout = args.duration_ms / 1000 + 15
        if not terminal_ack.wait(timeout):
            raise SystemExit(f"[OPEN] Timeout: khong co ACK ket thuc sau {timeout:.1f} giay")
    finally:
        client.loop_stop()
        client.disconnect()

    if "error" in result:
        raise SystemExit(f"[OPEN] {result['error']}")
    if result.get("state") == "failed":
        raise SystemExit(f"[OPEN] ESP32 tu choi: {result.get('reason', 'unknown_reason')}")

    print("[OPEN] Hoan tat:")
    print(json.dumps(ack_history, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
