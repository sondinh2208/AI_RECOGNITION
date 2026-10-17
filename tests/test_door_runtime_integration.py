from __future__ import annotations

import time
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


if __name__ == "__main__":
    unittest.main()
