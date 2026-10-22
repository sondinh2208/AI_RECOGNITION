"""Benchmark JPEG camera frames through MQTT Cloud without reflashing the ESP32."""

from __future__ import annotations

import argparse
import os
import ssl
import statistics
import threading
import time
import uuid
import urllib.request
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt


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


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--camera", help="ESP32 MJPEG URL")
    source.add_argument(
        "--synthetic-bytes", type=int,
        help="Publish privacy-safe synthetic payloads of this size",
    )
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--target-fps", type=float, default=5.0)
    args = parser.parse_args()

    host = os.getenv("MQTT_HOST", "").strip()
    port = int(os.getenv("MQTT_PORT", "8883"))
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "")
    base_topic = os.getenv("MQTT_CAMERA_TOPIC", "facecheck/camera/frame").rstrip("/")
    if not all((host, username, password)):
        raise SystemExit("Missing MQTT Cloud credentials in .env")
    topic = f"{base_topic}/benchmark/{uuid.uuid4().hex}"

    lock = threading.Lock()
    connected = threading.Event()
    sent_at: deque[float] = deque()
    received_at: list[float] = []
    latency_ms: list[float] = []

    subscriber = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"facecheck-bench-sub-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
    )
    publisher = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"facecheck-bench-pub-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
    )
    for client in (subscriber, publisher):
        client.username_pw_set(username, password)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            client.subscribe(topic, qos=0)
            connected.set()

    def on_message(_client, _userdata, _message):
        now = time.perf_counter()
        with lock:
            received_at.append(now)
            if sent_at:
                latency_ms.append((now - sent_at.popleft()) * 1000.0)

    subscriber.on_connect = on_connect
    subscriber.on_message = on_message
    subscriber.connect(host, port, keepalive=20)
    publisher.connect(host, port, keepalive=20)
    subscriber.loop_start()
    publisher.loop_start()
    if not connected.wait(15):
        raise SystemExit("MQTT subscriber did not connect within 15 seconds")
    time.sleep(0.5)

    sent = 0
    sent_bytes = 0
    started = time.perf_counter()
    deadline = started + args.duration

    def publish(payload: bytes) -> None:
        nonlocal sent, sent_bytes
        now = time.perf_counter()
        with lock:
            sent_at.append(now)
        info = publisher.publish(topic, payload, qos=0, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            with lock:
                sent_at.pop()
            return
        sent += 1
        sent_bytes += len(payload)

    try:
        if args.synthetic_bytes:
            size = max(4, args.synthetic_bytes)
            payload = b"\xff\xd8" + bytes(size - 4) + b"\xff\xd9"
            interval = 1.0 / args.target_fps
            next_frame = time.perf_counter()
            while time.perf_counter() < deadline:
                publish(payload)
                next_frame += interval
                time.sleep(max(0.0, next_frame - time.perf_counter()))
        else:
            request = urllib.request.Request(
                args.camera, headers={"Cache-Control": "no-cache"},
            )
            buffer = bytearray()
            with urllib.request.urlopen(request, timeout=5.0) as response:
                while time.perf_counter() < deadline:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while True:
                        start = buffer.find(b"\xff\xd8")
                        if start < 0:
                            if len(buffer) > 1:
                                del buffer[:-1]
                            break
                        end = buffer.find(b"\xff\xd9", start + 2)
                        if end < 0:
                            if start:
                                del buffer[:start]
                            break
                        jpeg = bytes(buffer[start : end + 2])
                        del buffer[: end + 2]
                        publish(jpeg)
    finally:
        time.sleep(2.0)
        publisher.disconnect()
        subscriber.disconnect()
        publisher.loop_stop()
        subscriber.loop_stop()

    elapsed = max(0.001, time.perf_counter() - started - 2.0)
    with lock:
        received = len(received_at)
        gaps_ms = [
            (current - previous) * 1000.0
            for previous, current in zip(received_at, received_at[1:])
        ]
        delays = list(latency_ms)
    receive_elapsed = received_at[-1] - received_at[0] if received > 1 else 0.0
    receive_fps = (received - 1) / receive_elapsed if receive_elapsed else 0.0
    expected_gap = 1000.0 / args.target_fps
    stalls = sum(gap > max(500.0, expected_gap * 4.0) for gap in gaps_ms)
    print(f"sent={sent} received={received} loss={max(0, sent-received)}")
    print(f"source_fps={sent/elapsed:.2f} mqtt_receive_fps={receive_fps:.2f}")
    print(
        f"gap_p95_ms={percentile(gaps_ms, .95):.1f} "
        f"gap_max_ms={max(gaps_ms, default=0.0):.1f} stalls={stalls}"
    )
    print(
        f"latency_mean_ms={statistics.fmean(delays) if delays else 0.0:.1f} "
        f"latency_p95_ms={percentile(delays, .95):.1f} "
        f"latency_max_ms={max(delays, default=0.0):.1f}"
    )
    print(f"jpeg_mean_kb={(sent_bytes/sent/1024) if sent else 0.0:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
