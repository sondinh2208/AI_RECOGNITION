"""Theo doi status/heartbeat cua Door Controller tren MQTT Cloud."""

from __future__ import annotations

import json
import os
import ssl
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


def main() -> None:
    load_dotenv()
    host = os.getenv("MQTT_HOST", "").strip()
    port = int(os.getenv("MQTT_PORT", "8883"))
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "")

    if not host or not username or not password:
        raise SystemExit("Thieu MQTT_HOST/MQTT_USERNAME/MQTT_PASSWORD trong .env")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="facecheck-door-monitor",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(username, password)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(client, userdata, flags, reason_code, properties):
        del userdata, flags, properties
        if reason_code != 0:
            print(f"[MQTT] Ket noi that bai: {reason_code}")
            return
        print(f"[MQTT] Da ket noi TLS toi {host}:{port}")
        client.subscribe("smartdoor/v1/+/status", qos=1)
        print("[MQTT] Dang cho heartbeat: smartdoor/v1/+/status")

    def on_message(client, userdata, message):
        del client, userdata
        raw = message.payload.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            rendered = raw
        print(f"\n[{message.topic}] retained={message.retain} qos={message.qos}\n{rendered}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=30)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[MQTT] Da dung monitor.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
