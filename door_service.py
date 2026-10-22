"""MQTT door controller with fail-closed anti-repeat state management."""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt


DEVICE_EVENT_TYPES = {
    "fingerprint_granted",
    "fingerprint_denied",
    "button_open",
    "sensor_online",
    "sensor_offline",
    "fingerprint_enrolled",
    "fingerprint_deleted",
}


class DoorService:
    """Publish short-lived OPEN commands and track their ACK lifecycle."""

    @classmethod
    def from_environment(cls, on_change=None, on_event=None):
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
            event_history_path=Path("data/door_events.json"),
            fingerprint_registry_path=Path("data/fingerprints.json"),
            on_change=on_change,
            on_event=on_event,
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
        management_timeout_seconds: float = 120.0,
        event_history_path: str | Path | None = None,
        fingerprint_registry_path: str | Path | None = None,
        max_events: int = 100,
        on_change: Callable[[], None] | None = None,
        on_event: Callable[[dict], None] | None = None,
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
        self.management_timeout_seconds = max(
            self.ack_timeout_seconds, float(management_timeout_seconds)
        )
        self._on_change = on_change
        self._on_event = on_event
        self._monotonic = monotonic
        self._epoch_time = epoch_time
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watchdog_thread = None
        self._event_history_path = Path(event_history_path) if event_history_path else None
        self._fingerprint_registry_path = (
            Path(fingerprint_registry_path) if fingerprint_registry_path else None
        )
        self._max_events = max(1, int(max_events))
        self.recent_events = []
        self.fingerprint_registry = {}
        self.device_status = None

        self.configured = bool(self.host and self.username and self.password)
        self.enabled = bool(enabled and self.configured)
        self.connected = False
        self.pending = None
        self.pending_management = None
        self.last_management = None
        self.cooldown_until = 0.0
        self.waiting_for_face_exit = False
        self.face_absent_since = None
        self.last_ack = None
        self.last_error = None if self.configured else "missing_mqtt_configuration"

        self.command_topic = f"smartdoor/v1/{self.device_id}/command"
        self.ack_topic = f"smartdoor/v1/{self.device_id}/ack"
        self.event_topic = f"smartdoor/v1/{self.device_id}/event"
        self.status_topic = f"smartdoor/v1/{self.device_id}/status"

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

        self._load_events()
        self._load_fingerprint_registry()

    def _load_fingerprint_registry(self) -> None:
        path = self._fingerprint_registry_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.fingerprint_registry = {
                    str(key): value
                    for key, value in payload.items()
                    if isinstance(value, dict)
                }
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[DOOR] Khong doc duoc danh sach van tay: {exc}")

    def _save_fingerprint_registry(self) -> None:
        path = self._fingerprint_registry_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self.fingerprint_registry, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            print(f"[DOOR] Khong luu duoc danh sach van tay: {exc}")

    @staticmethod
    def _decode_fingerprint_slots(encoded: str) -> set[int]:
        """Decode the 128-slot bitmap returned by the controller."""
        if not isinstance(encoded, str) or len(encoded) != 32:
            raise ValueError("invalid_fingerprint_inventory")
        try:
            bitmap = bytes.fromhex(encoded)
        except ValueError as exc:
            raise ValueError("invalid_fingerprint_inventory") from exc
        return {
            fingerprint_id
            for fingerprint_id in range(1, 128)
            if bitmap[fingerprint_id // 8] & (1 << (fingerprint_id % 8))
        }

    def _reconcile_fingerprint_inventory(self, encoded: str) -> dict:
        present_ids = self._decode_fingerprint_slots(encoded)
        now = datetime.now(timezone.utc).isoformat()
        discovered = 0
        missing = 0
        for fingerprint_id in present_ids:
            key = str(fingerprint_id)
            current = self.fingerprint_registry.get(key)
            if current is None:
                self.fingerprint_registry[key] = {
                    "fingerprint_id": fingerprint_id,
                    "employee_id": "",
                    "name": f"Vân tay #{fingerprint_id}",
                    "role": "",
                    "department": "",
                    "status": "unassigned",
                    "sensor_present": True,
                    "first_seen_at": now,
                }
                discovered += 1
            else:
                current["sensor_present"] = True
                if not current.get("employee_id"):
                    current["status"] = "unassigned"
        for key, current in self.fingerprint_registry.items():
            try:
                fingerprint_id = int(key)
            except ValueError:
                continue
            if fingerprint_id not in present_ids:
                current["sensor_present"] = False
                current["status"] = "missing_on_sensor"
                missing += 1
        self._save_fingerprint_registry()
        return {
            "sensor_count": len(present_ids),
            "discovered_count": discovered,
            "missing_count": missing,
            "synced_at": now,
        }

    def _load_events(self) -> None:
        if self._event_history_path is None or not self._event_history_path.exists():
            return
        try:
            payload = json.loads(self._event_history_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                self.recent_events = [item for item in payload if isinstance(item, dict)][
                    -self._max_events:
                ]
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[DOOR] Khong doc duoc lich su su kien: {exc}")

    def _save_events(self) -> None:
        if self._event_history_path is None:
            return
        try:
            self._event_history_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._event_history_path.with_suffix(
                self._event_history_path.suffix + ".tmp"
            )
            temporary.write_text(
                json.dumps(self.recent_events, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._event_history_path)
        except OSError as exc:
            print(f"[DOOR] Khong luu duoc lich su su kien: {exc}")

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
            now = self._monotonic()
            if self.pending_management and now >= self.pending_management["deadline"]:
                timed_out = self.pending_management
                self.pending_management = None
                self.last_management = {
                    **timed_out,
                    "state": "failed",
                    "reason": "ack_timeout",
                }
                target = (
                    f" cho ID #{timed_out['fingerprint_id']}"
                    if timed_out.get("fingerprint_id") else ""
                )
                print(f"[DOOR] Timeout {timed_out['command']}{target}.")
                self._notify_change()
                return True
            if not self.pending or now < self.pending["deadline"]:
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
            client.subscribe(self.event_topic, qos=1)
            client.subscribe(self.status_topic, qos=1)
            print(
                f"[DOOR] Da ket noi; subscribe ACK/event/status cho {self.device_id}"
            )
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
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        if message.topic == self.event_topic:
            self._handle_device_event(payload)
            return
        if message.topic == self.status_topic:
            self._handle_device_status(payload)
            return
        if message.topic != self.ack_topic:
            return

        changed = False
        with self._lock:
            state = payload.get("state")
            if state not in {"started", "finished", "failed"}:
                return

            command_id = payload.get("command_id")
            if (
                self.pending_management
                and command_id == self.pending_management["command_id"]
            ):
                pending = self.pending_management
                pending["state"] = state
                if state == "started":
                    pending["deadline"] = (
                        self._monotonic() + self.management_timeout_seconds
                    )
                self.last_management = {**pending, **dict(payload)}
                if state == "finished":
                    if pending["command"] == "FINGERPRINT_SYNC":
                        try:
                            summary = self._reconcile_fingerprint_inventory(
                                payload.get("fingerprint_slots", "")
                            )
                            self.last_management.update(summary)
                        except ValueError as exc:
                            self.last_management.update({
                                "state": "failed", "reason": str(exc),
                            })
                    else:
                        key = str(pending["fingerprint_id"])
                    if pending["command"] == "FINGERPRINT_ENROLL":
                        self.fingerprint_registry[key] = {
                            "fingerprint_id": pending["fingerprint_id"],
                            "employee_id": pending.get("employee_id", ""),
                            "name": pending.get("name", ""),
                            "role": pending.get("role", ""),
                            "department": pending.get("department", ""),
                            "status": "active",
                            "sensor_present": True,
                            "registered_at": datetime.now(timezone.utc).isoformat(),
                        }
                    elif pending["command"] == "FINGERPRINT_DELETE":
                        self.fingerprint_registry.pop(key, None)
                    self._save_fingerprint_registry()
                    self.pending_management = None
                elif state == "failed":
                    # The fingerprint sensor is the source of truth for slots.
                    # If enrollment discovers an occupied slot missing from the
                    # server registry, restore it as unassigned. This exposes the
                    # orphaned slot in the UI so it can be deleted explicitly.
                    reason = str(payload.get("reason") or "")
                    key = str(pending.get("fingerprint_id", ""))
                    if (
                        pending["command"] == "FINGERPRINT_ENROLL"
                        and reason == "fingerprint_id_exists"
                        and key not in self.fingerprint_registry
                    ):
                        self.fingerprint_registry[key] = {
                            "fingerprint_id": pending["fingerprint_id"],
                            "employee_id": "",
                            "name": f"Vân tay #{pending['fingerprint_id']}",
                            "role": "",
                            "department": "",
                            "status": "observed",
                            "first_seen_at": datetime.now(timezone.utc).isoformat(),
                        }
                        self._save_fingerprint_registry()
                    self.pending_management = None
                changed = True
            elif self.pending and command_id == self.pending["command_id"]:
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
            else:
                return

        print(f"[DOOR] ACK {payload.get('command_id')}: {payload.get('state')}")
        if changed:
            self._notify_change()

    def _handle_device_event(self, payload: dict) -> None:
        if payload.get("device_id") != self.device_id:
            return
        event_id = str(payload.get("event_id") or "").strip()
        event_type = str(payload.get("event") or "").strip()
        if not event_id or event_type not in DEVICE_EVENT_TYPES:
            return

        event = {
            "schema_version": 1,
            "device_id": self.device_id,
            "event_id": event_id[:48],
            "event": event_type,
            "source": str(payload.get("source") or "")[:32],
        }
        for field in ("uptime_ms", "occurred_at"):
            value = payload.get(field)
            if isinstance(value, int) and value >= 0:
                event[field] = value
        fingerprint_id = payload.get("fingerprint_id")
        if event_type in {
            "fingerprint_granted", "fingerprint_enrolled", "fingerprint_deleted"
        } and isinstance(fingerprint_id, int):
            if 1 <= fingerprint_id <= 127:
                event["fingerprint_id"] = fingerprint_id

        with self._lock:
            if any(item.get("event_id") == event_id for item in self.recent_events):
                return
            event["received_at"] = datetime.now(timezone.utc).isoformat()
            self.recent_events.append(event)
            del self.recent_events[:-self._max_events]
            self._save_events()
            if event_type == "fingerprint_granted" and "fingerprint_id" in event:
                key = str(event["fingerprint_id"])
                if key not in self.fingerprint_registry:
                    self.fingerprint_registry[key] = {
                        "fingerprint_id": event["fingerprint_id"],
                        "employee_id": "",
                        "name": f"Vân tay #{event['fingerprint_id']}",
                        "status": "observed",
                        "first_seen_at": event["received_at"],
                    }
                    self._save_fingerprint_registry()
            elif event_type == "fingerprint_deleted" and "fingerprint_id" in event:
                self.fingerprint_registry.pop(str(event["fingerprint_id"]), None)
                self._save_fingerprint_registry()
            elif event_type == "fingerprint_enrolled" and "fingerprint_id" in event:
                key = str(event["fingerprint_id"])
                if key not in self.fingerprint_registry:
                    self.fingerprint_registry[key] = {
                        "fingerprint_id": event["fingerprint_id"],
                        "employee_id": "",
                        "name": f"Vân tay #{event['fingerprint_id']}",
                        "status": "observed",
                        "first_seen_at": event["received_at"],
                    }
                    self._save_fingerprint_registry()
        print(f"[DOOR] Event {event_type}: {event_id}")
        if self._on_event is not None:
            try:
                self._on_event(dict(event))
            except Exception as exc:  # pragma: no cover - history must not kill MQTT
                print(f"[DOOR] Khong the dua su kien vao lich su chung: {exc}")
        self._notify_change()

    def _handle_device_status(self, payload: dict) -> None:
        if payload.get("device_id") != self.device_id:
            return
        with self._lock:
            self.device_status = {
                **payload,
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
        self._notify_change()

    def events(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in reversed(self.recent_events)]

    def fingerprints(self) -> list[dict]:
        with self._lock:
            return sorted(
                (dict(item) for item in self.fingerprint_registry.values()),
                key=lambda item: int(item.get("fingerprint_id", 0)),
            )

    def fingerprint(self, fingerprint_id: int) -> dict | None:
        with self._lock:
            item = self.fingerprint_registry.get(str(int(fingerprint_id)))
            return dict(item) if item else None

    def update_fingerprint_identity(
        self, fingerprint_id: int, employee_id: str, name: str,
        role: str | None = None, department: str | None = None,
    ) -> dict:
        fingerprint_id = int(fingerprint_id)
        if not 1 <= fingerprint_id <= 127:
            raise ValueError("invalid_fingerprint_id")
        employee_id = str(employee_id or "").strip()[:40]
        name = str(name or "").strip()[:80]
        if not employee_id:
            raise ValueError("fingerprint_employee_required")
        if not name:
            raise ValueError("fingerprint_name_required")
        with self._lock:
            key = str(fingerprint_id)
            current = self.fingerprint_registry.get(key)
            if current is None:
                raise KeyError("fingerprint_id_not_found")
            current_employee_id = str(current.get("employee_id", "") or "").strip()
            if current_employee_id and current_employee_id.casefold() != employee_id.casefold():
                raise ValueError("fingerprint_reassignment_forbidden")
            assigned_employee_id = current_employee_id or employee_id
            updated = {
                **current,
                "fingerprint_id": fingerprint_id,
                "employee_id": assigned_employee_id,
                "name": name,
                "role": str(current.get("role", "") if role is None else role).strip()[:80],
                "department": str(current.get("department", "") if department is None else department).strip()[:80],
                "status": "active",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.fingerprint_registry[key] = updated
            self._save_fingerprint_registry()
        self._notify_change()
        return dict(updated)

    def _request_fingerprint_management(
        self,
        command: str,
        fingerprint_id: int,
        *,
        employee_id: str = "",
        name: str = "",
        role: str = "",
        department: str = "",
    ) -> dict:
        try:
            fingerprint_id = int(fingerprint_id)
        except (TypeError, ValueError):
            return {"accepted": False, "reason": "invalid_fingerprint_id"}
        if not 1 <= fingerprint_id <= 127:
            return {"accepted": False, "reason": "invalid_fingerprint_id"}

        with self._lock:
            if not self.enabled or not self.connected:
                return {"accepted": False, "reason": "mqtt_disconnected"}
            if self.pending_management:
                return {"accepted": False, "reason": "management_command_pending"}
            key = str(fingerprint_id)
            if command == "FINGERPRINT_ENROLL" and key in self.fingerprint_registry:
                return {"accepted": False, "reason": "fingerprint_id_exists"}
            if command == "FINGERPRINT_DELETE" and key not in self.fingerprint_registry:
                return {"accepted": False, "reason": "fingerprint_id_not_found"}

            command_id = f"fp-{uuid.uuid4().hex[:12]}"
            issued_at = int(self._epoch_time())
            payload = {
                "command": command,
                "command_id": command_id,
                "device_id": self.device_id,
                "fingerprint_id": fingerprint_id,
                "issued_at": issued_at,
                "expires_at": issued_at + 15,
                "source": "server_management",
            }
            try:
                info = self.client.publish(
                    self.command_topic,
                    json.dumps(payload, separators=(",", ":")),
                    qos=1,
                    retain=False,
                )
            except Exception:
                return {"accepted": False, "reason": "publish_error"}
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return {"accepted": False, "reason": "publish_failed"}

            self.pending_management = {
                "command_id": command_id,
                "command": command,
                "fingerprint_id": fingerprint_id,
                "employee_id": str(employee_id).strip()[:40],
                "name": str(name).strip()[:80],
                "role": str(role).strip()[:80],
                "department": str(department).strip()[:80],
                "state": "sent",
                "sent_at": self._monotonic(),
                "deadline": self._monotonic() + self.management_timeout_seconds,
            }
            self.last_management = dict(self.pending_management)
        self._notify_change()
        return {"accepted": True, "command_id": command_id}

    def request_fingerprint_enroll(
        self, fingerprint_id: int, employee_id: str = "", name: str = "",
        role: str = "", department: str = "",
    ) -> dict:
        return self._request_fingerprint_management(
            "FINGERPRINT_ENROLL",
            fingerprint_id,
            employee_id=employee_id,
            name=name,
            role=role,
            department=department,
        )

    def request_fingerprint_delete(self, fingerprint_id: int) -> dict:
        return self._request_fingerprint_management(
            "FINGERPRINT_DELETE", fingerprint_id
        )

    def request_fingerprint_sync(self) -> dict:
        with self._lock:
            if not self.enabled or not self.connected:
                return {"accepted": False, "reason": "mqtt_disconnected"}
            if self.pending_management:
                return {"accepted": False, "reason": "management_command_pending"}
            command_id = f"fp-{uuid.uuid4().hex[:12]}"
            issued_at = int(self._epoch_time())
            payload = {
                "command": "FINGERPRINT_SYNC",
                "command_id": command_id,
                "device_id": self.device_id,
                "issued_at": issued_at,
                "expires_at": issued_at + 15,
                "source": "server_management",
            }
            try:
                info = self.client.publish(
                    self.command_topic, json.dumps(payload, separators=(",", ":")),
                    qos=1, retain=False,
                )
            except Exception:
                return {"accepted": False, "reason": "publish_error"}
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return {"accepted": False, "reason": "publish_failed"}
            self.pending_management = {
                "command_id": command_id,
                "command": "FINGERPRINT_SYNC",
                "state": "sent",
                "sent_at": self._monotonic(),
                "deadline": self._monotonic() + self.management_timeout_seconds,
            }
            self.last_management = dict(self.pending_management)
        self._notify_change()
        return {"accepted": True, "command_id": command_id}

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
            sensor_event = next(
                (
                    item for item in reversed(self.recent_events)
                    if item.get("event") in {"sensor_online", "sensor_offline"}
                ),
                None,
            )
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
                "fingerprint_management": (
                    dict(self.pending_management)
                    if self.pending_management
                    else (dict(self.last_management) if self.last_management else None)
                ),
                "fingerprint_count": sum(
                    item.get("sensor_present", True)
                    for item in self.fingerprint_registry.values()
                ),
                "device_status": dict(self.device_status) if self.device_status else None,
                "event_count": len(self.recent_events),
                "last_event": dict(self.recent_events[-1]) if self.recent_events else None,
                "fingerprint_sensor_state": (
                    sensor_event.get("event") if sensor_event else "unknown"
                ),
            }
