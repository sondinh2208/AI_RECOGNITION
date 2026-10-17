"""Gui PING toi Door Controller va cho ACK trung command_id."""

from __future__ import annotations

import json
import os
import ssl
import threading
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt


DEVICE_ID = os.getenv("MQTT_DOOR_DEVICE_ID", "door-01")
COMMAND_TOPIC = f"smartdoor/v1/{DEVICE_ID}/command"
ACK_TOPIC = f"smartdoor/v1/{DEVICE_ID}/ack"


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


def main() -> None:
    load_dotenv()
    host = os.getenv("MQTT_HOST", "").strip()
    port = int(os.getenv("MQTT_PORT", "8883"))
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "")
    if not host or not username or not password:
        raise SystemExit("Thieu MQTT_HOST/MQTT_USERNAME/MQTT_PASSWORD trong .env")

    command_id = f"ping-{uuid.uuid4().hex[:12]}"
    acknowledged = threading.Event()
    result: dict[str, object] = {}

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"facecheck-ping-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(username, password)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(client, userdata, flags, reason_code, properties):
        del userdata, flags, properties
        if reason_code != 0:
            result["error"] = f"Ket noi that bai: {reason_code}"
            acknowledged.set()
            return
        client.subscribe(ACK_TOPIC, qos=1)

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        del userdata, mid, reason_codes, properties
        payload = {
            "command": "PING",
            "command_id": command_id,
            "device_id": DEVICE_ID,
        }
        info = client.publish(
            COMMAND_TOPIC,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            result["error"] = f"Publish that bai: {info.rc}"
            acknowledged.set()
        else:
            print(f"[PING] Da gui {command_id} toi {COMMAND_TOPIC}")

    def on_message(client, userdata, message):
        del client, userdata
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if payload.get("command_id") != command_id:
            return
        result.update(payload)
        acknowledged.set()

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        if not acknowledged.wait(10):
            raise SystemExit("[PING] Timeout: khong nhan ACK trong 10 giay")
    finally:
        client.loop_stop()
        client.disconnect()

    if "error" in result:
        raise SystemExit(f"[PING] {result['error']}")
    print("[PING] ACK hop le:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
