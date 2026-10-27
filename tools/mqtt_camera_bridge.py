"""Nhan JPEG tu HiveMQ Cloud va phat luong noi bo cho FaceTouch Pro."""

from __future__ import annotations

import argparse
import os
import ssl
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

from ws_camera_bridge import LocalCameraHandler, STORE


def load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv()
    STORE.service = "FaceCheck MQTT Camera"
    parser = argparse.ArgumentParser(description="FaceTouch Pro MQTT camera bridge")
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", ""))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "8883")))
    parser.add_argument("--username", default=os.getenv("MQTT_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("MQTT_PASSWORD", ""))
    parser.add_argument("--topic", default=os.getenv("MQTT_CAMERA_TOPIC", "facecheck/camera/frame"))
    parser.add_argument("--local-port", type=int, default=8769)
    parser.add_argument("--connect-test", action="store_true")
    args = parser.parse_args()
    if not all((args.host, args.username, args.password, args.topic)):
        raise SystemExit("Thieu MQTT_HOST/MQTT_USERNAME/MQTT_PASSWORD/MQTT_CAMERA_TOPIC")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="facecheck-python-bridge",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(args.username, args.password)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    client.reconnect_delay_set(min_delay=1, max_delay=10)

    connected_event = threading.Event()

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code != 0:
            print(f"[MQTT-CAMERA] Ket noi bi tu choi: {reason_code}", flush=True)
            return
        with STORE.condition:
            STORE.connected = True
            STORE.active_connections = 1
            STORE.recent_frames.clear()
        client.subscribe(args.topic, qos=0)
        connected_event.set()
        print(f"[MQTT-CAMERA] Da ket noi TLS; subscribe {args.topic}", flush=True)

    def on_disconnect(_client, _userdata, _disconnect_flags, reason_code, _properties):
        with STORE.condition:
            STORE.connected = False
            STORE.active_connections = 0
            STORE.recent_frames.clear()
            STORE.condition.notify_all()
        print(f"[MQTT-CAMERA] Mat ket noi: {reason_code}", flush=True)

    def on_message(_client, _userdata, message):
        STORE.update(bytes(message.payload))

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    from http.server import ThreadingHTTPServer

    local_server = ThreadingHTTPServer(("127.0.0.1", args.local_port), LocalCameraHandler)
    threading.Thread(
        target=local_server.serve_forever,
        name="mqtt-camera-local-http",
        daemon=True,
    ).start()
    print(
        f"[MQTT-CAMERA] Stream noi bo: http://127.0.0.1:{args.local_port}/stream",
        flush=True,
    )
    client.connect(args.host, args.port, keepalive=20)
    if args.connect_test:
        client.loop_start()
        if not connected_event.wait(15):
            client.loop_stop()
            raise SystemExit("Khong ket noi duoc HiveMQ Cloud trong 15 giay")
        client.disconnect()
        client.loop_stop()
        print("[MQTT-CAMERA] Kiem tra ket noi: OK", flush=True)
        return
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
