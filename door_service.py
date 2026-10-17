"""MQTT door controller with fail-closed anti-repeat state management."""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
import uuid
from typing import Callable

import paho.mqtt.client as mqtt


class DoorService:
    """Publish short-lived OPEN commands and track their ACK lifecycle."""

    @classmethod
    def from_environment(cls, on_change=None):
        def number(name, default, cast=float):
            try:
                return cast(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FACECHECK_DOOR_ENABLED", "1").strip().lower() not in {
            "0", "false", "no", "off",
        }
        return cls(
            host=os.getenv("MQTT_HOST", ""),
            port=number("MQTT_PORT", 8883, int),
            username=os.getenv("MQTT_USERNAME", ""),
            password=os.getenv("MQTT_PASSWORD", ""),
            device_id=os.getenv("MQTT_DOOR_DEVICE_ID", "door-01"),
            enabled=enabled,
            open_duration_ms=number("FACECHECK_DOOR_OPEN_DURATION_MS", 3000, int),
            cooldown_seconds=number("FACECHECK_DOOR_COOLDOWN_SECONDS", 8.0),
            face_exit_seconds=number("FACECHECK_DOOR_FACE_EXIT_SECONDS", 1.5),
            ack_timeout_seconds=number("FACECHECK_DOOR_ACK_TIMEOUT_SECONDS", 12.0),
            on_change=on_change,
        )

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        device_id: str = "door-01",
        enabled: bool = True,
        open_duration_ms: int = 3000,
        cooldown_seconds: float = 8.0,
        face_exit_seconds: float = 1.5,
        ack_timeout_seconds: float = 12.0,
        on_change: Callable[[], None] | None = None,
        mqtt_client=None,
        monotonic: Callable[[], float] = time.monotonic,
        epoch_time: Callable[[], float] = time.time,
    ):
        self.host = host.strip()
        self.port = int(port)
        self.username = username.strip()
        self.password = password
        self.device_id = device_id.strip() or "door-01"
        self.open_duration_ms = max(500, min(int(open_duration_ms), 10000))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.face_exit_seconds = max(0.1, float(face_exit_seconds))
        self.ack_timeout_seconds = max(2.0, float(ack_timeout_seconds))
        self._on_change = on_change
        self._monotonic = monotonic
        self._epoch_time = epoch_time
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watchdog_thread = None

        self.configured = bool(self.host and self.username and self.password)
        self.enabled = bool(enabled and self.configured)
        self.connected = False
        self.pending = None
        self.cooldown_until = 0.0
        self.waiting_for_face_exit = False
        self.face_absent_since = None
        self.last_ack = None
        self.last_error = None if self.configured else "missing_mqtt_configuration"

        self.command_topic = f"smartdoor/v1/{self.device_id}/command"
        self.ack_topic = f"smartdoor/v1/{self.device_id}/ack"

        self.client = mqtt_client or mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"facecheck-door-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv311,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        if mqtt_client is None:
            self.client.username_pw_set(self.username, self.password)
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def _notify_change(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as exc:  # pragma: no cover - UI notification must not kill MQTT
                print(f"[DOOR] Khong the cap nhat UI: {exc}")

    @staticmethod
    def _reason_is_success(reason_code) -> bool:
        try:
            return int(reason_code) == 0
        except (TypeError, ValueError):
            return getattr(reason_code, "value", -1) == 0

    def start(self) -> None:
        if not self.enabled:
            reason = "da tat" if self.configured else "thieu cau hinh MQTT"
            print(f"[DOOR] MQTT door service {reason}; he thong fail-closed.")
            return
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return

        self._stop_event.clear()
        try:
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
        except Exception as exc:
            with self._lock:
                self.last_error = f"connect_error:{exc}"
            print(f"[DOOR] Khong khoi dong duoc MQTT: {exc}")
            self._notify_change()
            return

        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="door-command-watchdog",
        )
        self._watchdog_thread.start()
        print(f"[DOOR] Dang ket noi MQTT/TLS cho {self.device_id}...")

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=1.0)
        if self.enabled:
            try:
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:
                pass
        with self._lock:
            self.connected = False
        self._notify_change()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(0.25):
            self.check_timeout()

    def check_timeout(self) -> bool:
        """Fail closed if ACK is lost; exposed to make timing deterministic in tests."""
        with self._lock:
            if not self.pending or self._monotonic() < self.pending["deadline"]:
                return False
            timed_out = self.pending
            self.pending = None
            self.last_error = "ack_timeout"
            self.last_ack = {
                "command_id": timed_out["command_id"],
                "command": "OPEN",
                "state": "failed",
                "reason": "ack_timeout",
            }
            # The relay may have opened even if its ACK was lost. Do not issue
            # another command until both cooldown and face exit are satisfied.
            self.cooldown_until = self._monotonic() + self.cooldown_seconds
            self.waiting_for_face_exit = True
            self.face_absent_since = None
        print(f"[DOOR] Timeout ACK cho {timed_out['command_id']}; khoa mo lai an toan.")
        self._notify_change()
        return True

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        del userdata, flags, properties
        success = self._reason_is_success(reason_code)
        with self._lock:
            self.connected = success
            self.last_error = None if success else f"mqtt_connect_rejected:{reason_code}"
        if success:
            client.subscribe(self.ack_topic, qos=1)
            print(f"[DOOR] Da ket noi; subscribe {self.ack_topic}")
        else:
            print(f"[DOOR] MQTT tu choi ket noi: {reason_code}")
        self._notify_change()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        del client, userdata, disconnect_flags, properties
        with self._lock:
            self.connected = False
            if not self._reason_is_success(reason_code):
                self.last_error = f"mqtt_disconnected:{reason_code}"
        self._notify_change()

    def _on_message(self, client, userdata, message):
        del client, userdata
        if message.topic != self.ack_topic:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        changed = False
        with self._lock:
            if not self.pending or payload.get("command_id") != self.pending["command_id"]:
                return
            state = payload.get("state")
            if state not in {"started", "finished", "failed"}:
                return

            self.last_ack = dict(payload)
            self.pending["state"] = state
            changed = True
            if state == "finished":
                self.pending = None
                self.last_error = None
                self.cooldown_until = self._monotonic() + self.cooldown_seconds
                self.waiting_for_face_exit = True
                self.face_absent_since = None
            elif state == "failed":
                self.pending = None
                self.last_error = str(payload.get("reason") or "door_rejected_command")

        print(f"[DOOR] ACK {payload.get('command_id')}: {payload.get('state')}")
        if changed:
            self._notify_change()

    def request_open(self, employee_id: str) -> dict:
        """Try to publish one OPEN command; returns a non-throwing decision."""
        now = self._monotonic()
        with self._lock:
            if not self.enabled:
                return {"accepted": False, "reason": "disabled_or_unconfigured"}
            if not self.connected:
                return {"accepted": False, "reason": "mqtt_disconnected"}
            if self.pending:
                return {"accepted": False, "reason": "command_pending"}
            if self.waiting_for_face_exit:
                return {"accepted": False, "reason": "waiting_for_face_exit"}
            if now < self.cooldown_until:
                return {"accepted": False, "reason": "door_cooldown"}

            command_id = f"face-{uuid.uuid4().hex[:12]}"
            issued_at = int(self._epoch_time())
            payload = {
                "command": "OPEN",
                "command_id": command_id,
                "device_id": self.device_id,
                "duration_ms": self.open_duration_ms,
                "issued_at": issued_at,
                "expires_at": issued_at + 10,
                "source": "face_recognition",
                "employee_id": str(employee_id),
            }
            try:
                info = self.client.publish(
                    self.command_topic,
                    json.dumps(payload, separators=(",", ":")),
                    qos=1,
                    retain=False,
                )
            except Exception as exc:
                self.last_error = f"publish_error:{exc}"
                return {"accepted": False, "reason": "publish_error"}
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self.last_error = f"publish_failed:{info.rc}"
                return {"accepted": False, "reason": "publish_failed"}

            self.pending = {
                "command_id": command_id,
                "employee_id": str(employee_id),
                "state": "sent",
                "sent_at": now,
                "deadline": now + self.ack_timeout_seconds,
            }
            self.last_error = None

        print(f"[DOOR] Da gui OPEN {command_id} cho nhan vien {employee_id}")
        self._notify_change()
        return {"accepted": True, "command_id": command_id}

    def observe_faces(self, face_count: int) -> None:
        """Re-arm only after the camera has no faces for a continuous window."""
        changed = False
        now = self._monotonic()
        with self._lock:
            if not self.waiting_for_face_exit:
                self.face_absent_since = None
                return
            if face_count > 0:
                self.face_absent_since = None
                return
            if self.face_absent_since is None:
                self.face_absent_since = now
                return
            if now - self.face_absent_since >= self.face_exit_seconds:
                self.waiting_for_face_exit = False
                self.face_absent_since = None
                changed = True
        if changed:
            print("[DOOR] Camera da trong lien tuc; cho phep luot mo cua moi.")
            self._notify_change()

    def snapshot(self) -> dict:
        with self._lock:
            now = self._monotonic()
            cooldown_remaining = max(0.0, self.cooldown_until - now)
            if not self.configured:
                state = "unconfigured"
            elif not self.enabled:
                state = "disabled"
            elif self.pending:
                state = self.pending["state"]
            elif self.waiting_for_face_exit:
                state = "waiting_for_face_exit"
            elif cooldown_remaining > 0:
                state = "cooldown"
            elif self.connected:
                state = "ready"
            else:
                state = "disconnected"
            return {
                "enabled": self.enabled,
                "configured": self.configured,
                "connected": self.connected,
                "device_id": self.device_id,
                "state": state,
                "pending_command_id": self.pending["command_id"] if self.pending else None,
                "pending_employee_id": self.pending["employee_id"] if self.pending else None,
                "cooldown_remaining_seconds": round(cooldown_remaining, 1),
                "waiting_for_face_exit": self.waiting_for_face_exit,
                "last_ack": dict(self.last_ack) if self.last_ack else None,
                "last_error": self.last_error,
            }
