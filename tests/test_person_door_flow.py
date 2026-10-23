import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from door_service import DoorService
from tests.test_door_service import FakeClock, FakeMQTTClient
from tests.test_door_runtime_integration import DoorRuntimeIntegrationTests


class PersonDoorFlowTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.client = FakeMQTTClient()
        self.service = DoorService(host="test", port=8883, username="test", password="test",
                                   mqtt_client=self.client, monotonic=self.clock.monotonic,
                                   epoch_time=self.clock.epoch, cooldown_seconds=8)
        self.service._on_connect(self.client, None, None, 0)
        self.a = (20, 30, 180, 230)
        self.b = (300, 30, 460, 230)

    def ack(self, command, state):
        payload = {"device_id": "door-01", "command_id": command["command_id"],
                   "command": "OPEN", "state": state}
        self.service._on_message(self.client, None, SimpleNamespace(
            topic=self.service.ack_topic, payload=json.dumps(payload).encode()))

    def open_a(self):
        self.service.observe_identity("A", self.a)
        command = self.service.request_open("A")
        self.assertTrue(command["accepted"])
        self.ack(command, "finished")
        return command

    def test_next_person_can_open_without_camera_ever_being_empty(self):
        self.open_a()
        self.service.observe_faces(2)
        self.service.observe_identity("B", self.b)
        self.assertEqual(self.service.request_open("B")["reason"], "door_cooldown")
        self.clock.advance(8.1)
        self.service.observe_faces(1)
        self.service.observe_primary_face(self.b)
        self.service.observe_identity("B", self.b)
        self.assertTrue(self.service.request_open("B")["accepted"])

    def test_same_person_staying_foreground_cannot_open_again_after_cooldown(self):
        self.open_a()
        for _ in range(10):
            self.clock.advance(1)
            self.service.observe_primary_face(self.a)
        self.service.observe_identity("a", self.a)
        self.assertEqual(self.service.request_open("a")["reason"], "person_already_processed")
        self.assertEqual(len(self.client.published), 1)

    def test_individual_departure_rearms_a_while_b_stays_in_frame(self):
        self.open_a()
        self.service.observe_faces(1)
        self.service.observe_primary_face(self.b)
        self.clock.advance(2)
        self.service.observe_primary_face(self.b)
        self.clock.advance(6.1)
        self.service.observe_identity("A", self.a)
        self.assertTrue(self.service.request_open("A")["accepted"])

    def test_brief_detection_gap_does_not_release_same_person_guard(self):
        self.open_a()
        self.service.observe_primary_face(None)
        self.clock.advance(0.5)
        self.service.observe_primary_face(self.a)
        self.clock.advance(8)
        self.service.observe_primary_face(self.a)
        self.service.observe_identity("A", self.a)
        self.assertEqual(self.service.request_open("A")["reason"], "person_already_processed")

    def test_verified_next_identity_can_replace_old_position(self):
        self.open_a()
        self.clock.advance(8.1)
        self.service.observe_identity("B", self.a)
        command_b = self.service.request_open("B")
        self.assertTrue(command_b["accepted"])
        self.ack(command_b, "finished")
        self.clock.advance(8.1)
        self.service.observe_primary_face(self.a)
        self.service.observe_identity("A", self.a)
        self.assertTrue(self.service.request_open("A")["accepted"])

    def test_lost_ack_still_fails_closed_even_for_different_person(self):
        self.service.observe_identity("A", self.a)
        self.service.request_open("A")
        self.clock.advance(13)
        self.service.check_timeout()
        self.clock.advance(9)
        self.service.observe_identity("B", self.b)
        self.service.observe_faces(1)
        self.assertEqual(self.service.request_open("B")["reason"], "waiting_for_face_exit")

    def test_waiting_never_auto_opens_without_a_fresh_request(self):
        self.open_a()
        self.service.observe_identity("B", self.b)
        self.service.request_open("B")
        self.clock.advance(20)
        self.service.check_timeout()
        self.assertEqual(len(self.client.published), 1)


class DoorFeedbackTests(unittest.TestCase):
    def test_oled_and_ui_follow_only_matching_controller_ack(self):
        runtime = DoorRuntimeIntegrationTests().make_runtime()
        with patch.object(runtime, "_add_attendance_record"):
            runtime._on_kiosk_recognition_success({"id": "A"}, 0.2)
        self.assertEqual(runtime.recognition_state["door_phase"], "requested")
        self.assertEqual(runtime._access_snapshot()["access_state"], "processing")
        runtime.door_service.snapshot.return_value = {"last_ack": {"command_id": "other", "state": "finished"}}
        runtime._publish()
        self.assertEqual(runtime.recognition_state["door_phase"], "requested")
        runtime.door_service.snapshot.return_value = {"last_ack": {"command_id": "face-test", "state": "finished"}}
        runtime._publish()
        self.assertEqual(runtime.recognition_state["door_phase"], "executed")
        self.assertEqual(runtime._access_snapshot()["access_label"], "Đã thực hiện mở khóa")
        runtime.door_service.notify_face_display.assert_called_with("opened")

    def test_cooldown_displays_waiting_not_opened(self):
        runtime = DoorRuntimeIntegrationTests().make_runtime()
        runtime.door_service.request_open.return_value = {"accepted": False, "reason": "door_cooldown"}
        with patch.object(runtime, "_add_attendance_record"):
            runtime._on_kiosk_recognition_success({"id": "B"}, 0.2)
        self.assertEqual(runtime.recognition_state["door_phase"], "waiting")
        self.assertEqual(runtime._access_snapshot()["access_state"], "processing")
        runtime.door_service.notify_face_display.assert_called_with("waiting")

    def test_offline_controller_does_not_display_opened(self):
        runtime = DoorRuntimeIntegrationTests().make_runtime()
        runtime.door_service.request_open.return_value = {"accepted": False, "reason": "mqtt_disconnected"}
        with patch.object(runtime, "_add_attendance_record"):
            runtime._on_kiosk_recognition_success({"id": "B"}, 0.2)
        self.assertEqual(runtime._access_snapshot()["access_state"], "error")
        runtime.door_service.notify_face_display.assert_called_with("unavailable")
