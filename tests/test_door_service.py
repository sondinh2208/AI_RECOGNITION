from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from door_service import DoorService


class FakeClock:
    def __init__(self):
        self.monotonic_value = 100.0
        self.epoch_value = 1_800_000_000.0

    def monotonic(self):
        return self.monotonic_value

    def epoch(self):
        return self.epoch_value

    def advance(self, seconds):
        self.monotonic_value += seconds
        self.epoch_value += seconds


class FakeMQTTClient:
    def __init__(self):
        self.published = []
        self.subscriptions = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos):
        self.subscriptions.append((topic, qos))


class DoorServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.client = FakeMQTTClient()
        self.service = DoorService(
            host="broker.example",
            port=8883,
            username="user",
            password="password",
            mqtt_client=self.client,
            monotonic=self.clock.monotonic,
            epoch_time=self.clock.epoch,
            cooldown_seconds=8.0,
            face_exit_seconds=1.5,
            ack_timeout_seconds=12.0,
        )

    def connect(self):
        self.service._on_connect(self.client, None, None, 0)

    def ack(self, command_id, state, reason=None):
        payload = {
            "device_id": "door-01",
            "command_id": command_id,
            "command": "OPEN",
            "state": state,
        }
        if reason:
            payload["reason"] = reason
        message = SimpleNamespace(
            topic=self.service.ack_topic,
            payload=json.dumps(payload).encode("utf-8"),
        )
        self.service._on_message(self.client, None, message)

    def test_open_is_short_lived_non_retained_and_single_flight(self):
        self.connect()

        first = self.service.request_open("NV001")
        second = self.service.request_open("NV001")

        self.assertTrue(first["accepted"])
        self.assertEqual(second, {"accepted": False, "reason": "command_pending"})
        topic, raw_payload, qos, retain = self.client.published[0]
        payload = json.loads(raw_payload)
        self.assertEqual(topic, "smartdoor/v1/door-01/command")
        self.assertEqual(qos, 1)
        self.assertFalse(retain)
        self.assertEqual(payload["expires_at"] - payload["issued_at"], 10)
        self.assertEqual(payload["employee_id"], "NV001")

    def test_finished_requires_face_exit_and_cooldown_before_rearming(self):
        self.connect()
        command = self.service.request_open("NV001")
        self.ack(command["command_id"], "started")
        self.ack(command["command_id"], "finished")

        self.assertEqual(
            self.service.request_open("NV002")["reason"],
            "waiting_for_face_exit",
        )
        self.service.observe_faces(1)
        self.service.observe_faces(0)
        self.clock.advance(1.4)
        self.service.observe_faces(0)
        self.assertTrue(self.service.snapshot()["waiting_for_face_exit"])
        self.clock.advance(0.2)
        self.service.observe_faces(0)
        self.assertFalse(self.service.snapshot()["waiting_for_face_exit"])
        self.assertEqual(self.service.request_open("NV002")["reason"], "door_cooldown")

        self.clock.advance(6.5)
        self.assertTrue(self.service.request_open("NV002")["accepted"])

    def test_ack_timeout_fails_closed_until_face_exit(self):
        self.connect()
        command = self.service.request_open("NV001")
        self.clock.advance(12.1)

        self.assertTrue(self.service.check_timeout())
        state = self.service.snapshot()
        self.assertIsNone(state["pending_command_id"])
        self.assertEqual(state["last_error"], "ack_timeout")
        self.assertTrue(state["waiting_for_face_exit"])
        self.assertEqual(state["last_ack"]["command_id"], command["command_id"])

    def test_disconnected_service_fails_closed(self):
        self.assertEqual(
            self.service.request_open("NV001"),
            {"accepted": False, "reason": "mqtt_disconnected"},
        )


if __name__ == "__main__":
    unittest.main()
