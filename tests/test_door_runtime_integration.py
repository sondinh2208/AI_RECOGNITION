from __future__ import annotations

import time
import tempfile
import unittest
from unittest.mock import Mock, patch

from web_runtime import WebFaceCheckRuntime


class DoorRuntimeIntegrationTests(unittest.TestCase):
    def make_runtime(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.door_service = Mock()
        runtime.door_service.request_open.return_value = {
            "accepted": True,
            "command_id": "face-test",
        }
        runtime.door_service.snapshot.return_value = {
            "state": "ready",
            "connected": True,
        }
        return runtime

    def test_final_recognition_requests_exactly_one_open(self):
        runtime = self.make_runtime()
        employee = {"id": "NV001", "name": "Test User"}

        with patch.object(runtime, "_add_attendance_record"):
            runtime._on_kiosk_recognition_success(employee, 0.25)

        runtime.door_service.request_open.assert_called_once_with("NV001")
        runtime.door_service.notify_face_display.assert_called_with("verified")

    def test_face_delete_notifies_oled_only_after_successful_storage(self):
        runtime = self.make_runtime()
        with tempfile.TemporaryDirectory() as folder, patch("web_runtime.DATA_FACES_DIR", folder), patch.object(runtime, "_read_profiles", return_value={"TEST": {}}), patch.object(runtime, "_write_profiles") as write:
            runtime.delete_employee("TEST")
            write.assert_called_once_with({})
        runtime.door_service.notify_face_display.assert_called_with("deleted")

    def test_failed_face_delete_does_not_report_success_on_oled(self):
        runtime = self.make_runtime()
        with tempfile.TemporaryDirectory() as folder, patch("web_runtime.DATA_FACES_DIR", folder), patch.object(runtime, "_read_profiles", return_value={"TEST": {}}), patch.object(runtime, "_write_profiles", side_effect=OSError("storage failure")):
            with self.assertRaises(OSError):
                runtime.delete_employee("TEST")
        runtime.door_service.notify_face_display.assert_not_called()

    def test_oled_enrollment_tracks_capture_and_save_without_opening(self):
        runtime = self.make_runtime()
        runtime.current_page = "add_employee"
        runtime._enrollment_scan_armed = True
        runtime._sync_oled_display()
        runtime.door_service.notify_face_display.assert_called_with("enrolling")
        runtime.enrollment_capture_ready = True
        runtime._sync_oled_display()
        runtime.door_service.notify_face_display.assert_called_with("captured")
        runtime.enrollment_result = {"state": "saving"}
        runtime._sync_oled_display()
        runtime.door_service.notify_face_display.assert_called_with("saving")
        runtime.enrollment_result = {"state": "success"}
        runtime._sync_oled_display()
        runtime.door_service.notify_face_display.assert_called_with("saved")
        runtime.door_service.request_open.assert_not_called()

    def test_failed_recognition_updates_oled_without_opening(self):
        runtime = self.make_runtime()
        with patch.object(runtime, "_add_attendance_record"):
            runtime._on_kiosk_recognition_failed(0.8)
        runtime.door_service.notify_face_display.assert_called_with("denied")
        runtime.door_service.request_open.assert_not_called()

    def test_attendance_duplicate_still_uses_door_anti_repeat_policy(self):
        runtime = self.make_runtime()
        runtime._attendance_cooldowns["NV001"] = (time.time(), "10:00:00")

        with patch.object(runtime, "_add_attendance_record") as add_record:
            runtime._on_kiosk_recognition_success({"id": "NV001"}, 0.25)

        add_record.assert_not_called()
        runtime.door_service.request_open.assert_called_once_with("NV001")

    def test_runtime_lifecycle_starts_and_stops_door_service(self):
        runtime = self.make_runtime()
        with patch.object(runtime, "_read_profiles", return_value={}):
            runtime.start()
        runtime.shutdown()

        runtime.door_service.start.assert_called_once_with()
        runtime.door_service.shutdown.assert_called_once_with()

    def test_api_snapshot_exposes_door_state(self):
        runtime = self.make_runtime()

        self.assertEqual(runtime.snapshot()["door"]["state"], "ready")

    def test_fingerprint_event_is_added_to_common_attendance_history_once(self):
        runtime = self.make_runtime()
        runtime.attendance_history = []
        event = {
            "event_id": "evt-fingerprint-001",
            "event": "fingerprint_granted",
            "source": "fingerprint",
            "fingerprint_id": 2,
            "occurred_at": 1_800_000_000,
        }

        with patch.object(runtime, "_save_attendance_history"):
            runtime._on_door_event(event)
            runtime._on_door_event(event)

        self.assertEqual(len(runtime.attendance_history), 1)
        row = runtime.attendance_history[0]
        self.assertEqual(row["id"], "VT002")
        self.assertEqual(row["name"], "Vân tay #2")
        self.assertEqual(row["status"], "Thành công")
        self.assertEqual(row["door_event_id"], "evt-fingerprint-001")

    def test_denied_fingerprint_is_added_as_failed_history(self):
        runtime = self.make_runtime()
        runtime.attendance_history = []

        with patch.object(runtime, "_save_attendance_history"):
            runtime._on_door_event({
                "event_id": "evt-fingerprint-002",
                "event": "fingerprint_denied",
                "source": "fingerprint",
            })

        self.assertEqual(runtime.attendance_history[0]["status"], "Thất bại")
        self.assertEqual(runtime.attendance_history[0]["source"], "fingerprint")


if __name__ == "__main__":
    unittest.main()
