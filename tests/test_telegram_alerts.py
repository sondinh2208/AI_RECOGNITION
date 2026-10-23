import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from telegram_alerts import TelegramFaceAlerts


class TelegramAlertTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.sender = Mock()
        self.alerts = TelegramFaceAlerts("test-token", "123", sender=self.sender, clock=lambda: self.now[0])
        self.image = np.zeros((40, 40, 3), dtype=np.uint8)

    def next_visit(self, seconds=4.0):
        self.now[0] += seconds

    def three_failures(self):
        for _ in range(3):
            self.alerts.failure(self.image)
            self.next_visit()

    def test_rapid_duplicate_failure_callbacks_count_only_once(self):
        for _ in range(10):
            self.alerts.failure(self.image)
            self.now[0] += 0.1
        self.assertEqual(len(self.alerts._failures), 1)
        self.assertEqual(self.alerts.snapshot()["pending"], 0)

    def test_short_interval_does_not_count_as_another_attempt(self):
        self.alerts.failure(self.image)
        self.next_visit(0.2)
        self.alerts.failure(self.image)
        self.assertEqual(len(self.alerts._failures), 1)

    def test_third_visit_queues_a_copy_and_caption(self):
        self.three_failures()
        _, image, caption = self.alerts._queue.get_nowait()
        self.assertEqual(image.shape, self.image.shape)
        self.assertIsNot(image, self.image)
        self.assertIn("3 lượt", caption)
        self.assertIn("chưa được xác minh", caption)
        self.sender.assert_not_called()

    def test_three_spaced_failures_alert_without_ever_leaving_camera(self):
        self.assertFalse(self.alerts.failure(self.image))
        self.now[0] = 3.99
        self.assertFalse(self.alerts.failure(self.image))
        self.now[0] = 4.0
        self.assertFalse(self.alerts.failure(self.image))
        self.now[0] = 8.0
        self.assertTrue(self.alerts.failure(self.image))
        self.assertEqual(self.alerts.snapshot()["pending"], 1)

    def test_failed_face_is_eligible_again_after_four_seconds(self):
        from web_runtime import WebFaceCheckRuntime
        runtime = WebFaceCheckRuntime(load_ai=False)
        face = (100, 80, 260, 300, 0.95)
        runtime._kiosk_active_face_box = face[:4]
        runtime._hold_result_and_suppress_active_face(now=10, retry_delay=4)
        self.assertEqual(runtime._kiosk_result_hold_until, 14)
        self.assertEqual(runtime._filter_processed_kiosk_faces([face], now=13.99), [])
        self.assertEqual(runtime._filter_processed_kiosk_faces([face], now=14.01), [face])

    def test_manual_retry_cannot_bypass_failed_attempt_cooldown(self):
        from tests.test_door_runtime_integration import DoorRuntimeIntegrationTests
        runtime = DoorRuntimeIntegrationTests().make_runtime()
        runtime.recognition_state["state"] = "unknown"
        runtime._kiosk_result_hold_until = 14.0
        with patch("web_runtime.time.time", return_value=12.0), patch.object(runtime, "set_idle_state") as reset:
            runtime.retry_recognition()
            reset.assert_not_called()
        with patch("web_runtime.time.time", return_value=14.01), patch.object(runtime, "set_idle_state") as reset:
            runtime.retry_recognition()
            reset.assert_called_once()

    def test_failures_older_than_window_do_not_count(self):
        self.alerts.failure(self.image)
        self.next_visit(61)
        self.alerts.failure(self.image)
        self.next_visit()
        self.alerts.failure(self.image)
        self.assertEqual(self.alerts.snapshot()["pending"], 0)

    def test_success_resets_failure_window(self):
        self.alerts.failure(self.image)
        self.next_visit()
        self.alerts.failure(self.image)
        self.alerts.success()
        self.next_visit()
        self.alerts.failure(self.image)
        self.assertEqual(self.alerts.snapshot()["pending"], 0)

    def test_cooldown_and_missing_photo(self):
        self.three_failures()
        self.three_failures()
        self.assertEqual(self.alerts.snapshot()["pending"], 1)
        self.now[0] += 61
        self.next_visit()
        self.alerts.failure(None)
        self.assertEqual(self.alerts.snapshot()["pending"], 1)

    def test_worker_encodes_photo_off_thread_and_shuts_down(self):
        delivered = threading.Event()
        self.sender.side_effect = lambda *args: delivered.set()
        self.alerts.start()
        try:
            self.three_failures()
            self.assertTrue(delivered.wait(2))
            jpeg, caption = self.sender.call_args.args
            self.assertTrue(jpeg.startswith(b"\xff\xd8"))
            self.assertIn("cửa chính", caption)
        finally:
            self.alerts.shutdown()
        self.assertFalse(self.alerts._thread.is_alive())
        self.assertEqual(self.alerts.snapshot()["sent"], 1)

    def test_timeout_is_not_retried_or_logged_with_token(self):
        self.sender.side_effect = TimeoutError("sensitive-token-url")
        self.alerts.start()
        try:
            with patch("telegram_alerts.print") as output:
                self.three_failures()
                self.alerts._queue.join()
                self.assertNotIn("sensitive-token-url", str(output.call_args_list))
            self.assertEqual(self.alerts.snapshot()["send_errors"], 1)
            self.sender.assert_called_once()
        finally:
            self.alerts.shutdown()

    def test_multipart_uses_photo_endpoint_and_configured_recipient(self):
        response = Mock()
        response.read.return_value = b'{"ok":true}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("telegram_alerts.urllib.request.urlopen", return_value=response) as send:
            self.alerts._send_photo(b"jpeg-data", "Cảnh báo")
        request = send.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/sendPhoto"))
        self.assertIn(b'name="photo"', request.data)
        self.assertIn(b"jpeg-data", request.data)
        self.assertIn(b"123", request.data)

    def test_disabled_service_never_queues_or_sends(self):
        alerts = TelegramFaceAlerts()
        for _ in range(5):
            alerts.failure(self.image)
        alerts.start()
        self.assertIsNone(alerts._thread)
        self.assertEqual(alerts.snapshot()["pending"], 0)

    def test_runtime_only_counts_final_identity_failure_and_releases_crop(self):
        from tests.test_door_runtime_integration import DoorRuntimeIntegrationTests
        runtime = DoorRuntimeIntegrationTests().make_runtime()
        runtime.telegram_alerts = Mock()
        face = object()
        runtime._kiosk_alert_face = face
        runtime._on_kiosk_face_quality_rejected("Thiếu sáng")
        runtime.telegram_alerts.failure.assert_not_called()
        with patch.object(runtime, "_add_attendance_record"):
            runtime._on_kiosk_recognition_failed(0.9)
        runtime.telegram_alerts.failure.assert_called_once_with(face)
        self.assertIsNone(runtime._kiosk_alert_face)
        runtime.door_service.request_open.assert_not_called()

    def test_telegram_open_is_added_to_server_history_once(self):
        from tests.test_door_runtime_integration import DoorRuntimeIntegrationTests
        runtime = DoorRuntimeIntegrationTests().make_runtime()
        event = {"event_id": "telegram-001", "event": "telegram_open", "occurred_at": 1800000000}
        runtime.attendance_history = []
        with patch.object(runtime, "_save_attendance_history"):
            runtime._on_door_event(event)
            runtime._on_door_event(event)
        self.assertEqual(len(runtime.attendance_history), 1)
        self.assertEqual(runtime.attendance_history[0]["source"], "telegram")
