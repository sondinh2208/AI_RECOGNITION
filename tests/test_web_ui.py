"""Read-only smoke tests for the HTML UI bridge."""

from __future__ import annotations

import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from types import SimpleNamespace

import httpx
import numpy as np


os.environ["FACECHECK_WEB_SKIP_AI"] = "1"
os.environ["FACECHECK_DISPLAY_TOKEN"] = "test-display-token"

from web_backend.app import app, display_app  # noqa: E402
from web_runtime import WebFaceCheckRuntime  # noqa: E402
from web_admin import (  # noqa: E402
    _parse_args as parse_web_admin_args,
    _reserve_server_socket,
    _window_specs,
)
from facecheck import parse_args as parse_launcher_args  # noqa: E402
from ai_engine import (  # noqa: E402
    analyze_face_quality, check_face_constraints, measure_face_brightness,
)
from mixins.attendance import (  # noqa: E402
    AttendanceMixin, is_high_confidence_recognition,
)


class RecognitionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = AttendanceMixin.__new__(AttendanceMixin)
        self.policy._clear_strict_confirmation()

    def test_very_strong_match_can_use_fast_confirmation(self):
        self.assertTrue(is_high_confidence_recognition(0.38, 0.1695))
        self.assertFalse(is_high_confidence_recognition(0.41, 0.1695))
        self.assertFalse(is_high_confidence_recognition(0.38, 0.09))

    def test_noisy_frame_does_not_erase_valid_strict_vote(self):
        now = time.time()
        first = self.policy._record_strict_vote("NV001", 0.45, 0.10, now=now)
        noisy = self.policy._record_strict_noise(now=now + 0.1)
        second = self.policy._record_strict_vote("NV001", 0.44, 0.11, now=now + 0.2)

        self.assertEqual(first["votes"], 1)
        self.assertEqual(noisy["votes"], 1)
        self.assertEqual(noisy["attempts"], 2)
        self.assertEqual(second["votes"], 2)
        self.assertEqual(second["attempts"], 3)


class WebUiSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.display_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=display_app), base_url="http://display"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.display_client.aclose()

    async def test_spa_shell_contains_all_existing_pages(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        for page in ("dashboard", "enrollment", "recognition", "employees", "history"):
            self.assertIn(f'data-page="{page}"', html)
        self.assertEqual(html.count('id="cameraSurface"'), 1)
        self.assertIn('type="module" src="/js/app.js"', html)
        self.assertIn('/css/kiosk.css', html)
        self.assertIn('id="startEnrollmentScan"', html)

    async def test_read_only_api_contracts(self):
        state = (await self.client.get("/api/state")).json()
        self.assertIn(state["recognition"]["state"], {
            "idle", "validating", "recognizing", "strict_confirmation",
            "success", "duplicate", "ambiguous", "unknown", "error",
        })
        self.assertIn("capture_ready", state["enrollment"])
        self.assertIn("sample_count", state["enrollment"])
        self.assertIn("sample_target", state["enrollment"])
        self.assertIn("brightness", state["camera"])
        self.assertIn("light_ok", state["camera"])

        forbidden = await self.display_client.get("/frame.jpg")
        self.assertEqual(forbidden.status_code, 403)
        display_health = await self.display_client.get(
            "/health", headers={"x-facecheck-token": "test-display-token"}
        )
        self.assertEqual(display_health.status_code, 200)

        employees = (await self.client.get(
            "/api/employees", params={"page": 1, "page_size": 2}
        )).json()
        self.assertLessEqual(len(employees["items"]), 2)
        self.assertIn("enabled_total", employees)
        for employee in employees["items"]:
            self.assertIn("?v=", employee["image_url"])
            image_response = await self.client.get(employee["image_url"])
            if image_response.status_code == 200:
                self.assertIn("no-store", image_response.headers["cache-control"])

        history = (await self.client.get(
            "/api/attendance", params={"page": 1, "page_size": 5}
        )).json()
        self.assertLessEqual(len(history["items"]), 5)

    async def test_frontend_contains_no_ai_decision_parameters(self):
        response = await self.client.get("/js/app.js")
        source = response.text
        self.assertIn('["attendance", "add_employee"].includes(mode)', source)
        self.assertIn('const simulatedAdminView = viewRole === "admin"', source)
        self.assertIn(
            'const active = kioskView && visible && ["attendance", "add_employee"].includes(mode)',
            source,
        )
        self.assertNotIn('else if (!simulatedAdminView', source)
        self.assertIn('$("#enrollmentForm").reset()', source)
        for forbidden in (
            "ARCFACE_THRESHOLD", "ARCFACE_TOP2_MARGIN",
            "ARCFACE_UNCERTAIN_THRESHOLD", "classify_recognition_score",
        ):
            self.assertNotIn(forbidden, source)


class RuntimeLifecycleTests(unittest.TestCase):
    def test_employee_list_prioritizes_enabled_then_sorts_name_a_to_z(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            "NV004": {"id": "NV004", "name": "Đặng An", "recognition_enabled": False},
            "NV003": {"id": "NV003", "name": "Bình", "recognition_enabled": True},
            "NV002": {"id": "NV002", "name": "An", "recognition_enabled": False},
            "NV001": {"id": "NV001", "name": "Ánh", "recognition_enabled": True},
        }

        data = runtime.list_employees(page=1, page_size=10)

        self.assertEqual(
            [(item["name"], item["recognition_enabled"]) for item in data["items"]],
            [("Ánh", True), ("Bình", True), ("An", False), ("Đặng An", False)],
        )

    def test_one_hundred_employees_use_ten_pages_at_ten_per_page(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            f"NV{index:03d}": {
                "id": f"NV{index:03d}",
                "name": f"Nhân viên {index:03d}",
                "recognition_enabled": True,
            }
            for index in range(100)
        }

        data = runtime.list_employees(page=1, page_size=10)

        self.assertEqual(data["total"], 100)
        self.assertEqual(data["pages"], 10)

    def test_admin_activity_restarts_continuous_enrollment_timer(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "add_employee"
        runtime._enrollment_continuous_session = True
        old_timer = Mock()
        runtime._enrollment_idle_timer = old_timer
        fake_timer = Mock()

        with patch("web_runtime.threading.Timer", return_value=fake_timer) as timer_class:
            runtime.note_enrollment_activity()

        old_timer.cancel.assert_called_once()
        timer_class.assert_called_once()
        fake_timer.start.assert_called_once()
        self.assertIs(runtime._enrollment_idle_timer, fake_timer)
        self.assertIsNotNone(runtime._enrollment_idle_deadline)

    def test_next_enrollment_requires_explicit_scan_start(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "add_employee"
        runtime._enrollment_continuous_session = True
        runtime._enrollment_scan_armed = False

        with patch.object(runtime, "_restart_enrollment_idle_timer"), patch.object(
            runtime, "_reset_enrollment_scan"
        ) as reset_scan:
            state = runtime.start_enrollment_scan()

        self.assertTrue(runtime._enrollment_scan_armed)
        self.assertTrue(state["enrollment"]["scan_armed"])
        reset_scan.assert_called_once()

    def test_continuous_enrollment_returns_to_attendance_after_timeout(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "add_employee"
        runtime._enrollment_continuous_session = True
        runtime.switch_mode = Mock()

        runtime._auto_return_to_attendance()

        runtime.switch_mode.assert_called_once_with("attendance")

    def test_camera_clients_share_one_jpeg_encode(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "attendance"
        runtime.kiosk_latest_frame = np.zeros((48, 64, 3), dtype=np.uint8)
        runtime._current_frame_id = 1

        def slow_encode(*_args, **_kwargs):
            time.sleep(0.03)
            return True, np.array([1, 2, 3], dtype=np.uint8)

        with patch("web_runtime.cv2.imencode", side_effect=slow_encode) as encoder:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: runtime.camera_jpeg_frame(), range(2)))

            runtime._current_frame_id = 2
            throttled_result = runtime.camera_jpeg_frame()

        self.assertEqual(encoder.call_count, 1)
        self.assertEqual(results, [(1, b"\x01\x02\x03")] * 2)
        self.assertEqual(throttled_result, (1, b"\x01\x02\x03"))

    def test_navigation_does_not_reload_models_or_reopen_camera(self):
        runtime = WebFaceCheckRuntime(load_ai=True)
        runtime._load_ai_models = Mock()
        runtime._start_camera = Mock()
        runtime.start()
        runtime.switch_mode("attendance")
        runtime.switch_mode("idle")
        runtime.switch_mode("add_employee")
        runtime.switch_mode("idle")
        runtime._load_ai_models.assert_called_once_with()
        runtime._start_camera.assert_called_once_with()

    def test_cancel_captured_enrollment_waits_for_face_to_leave(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.enrollment_capture_ready = True
        runtime.enrollment_captured_samples = [object()]
        runtime.enrollment_captured_frame = object()
        runtime.enrollment_captured_face = object()

        state = runtime.reset_enrollment()

        self.assertFalse(runtime.enrollment_capture_ready)
        self.assertEqual(runtime.enrollment_captured_samples, [])
        self.assertIsNone(runtime.enrollment_captured_frame)
        self.assertIsNone(runtime.enrollment_captured_face)
        self.assertTrue(runtime._enrollment_waiting_for_face_leave)
        self.assertEqual(state["enrollment"]["state"], "idle")
        self.assertIn("Đã hủy ảnh quét", state["enrollment"]["message"])

    def test_cancel_enrollment_is_rejected_while_saving(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime._enrollment_saving = True

        with self.assertRaisesRegex(ValueError, "đang lưu khuôn mặt"):
            runtime.reset_enrollment()

    def test_success_state_is_cleared_when_next_enrollment_can_begin(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.enrollment_result = {
            "state": "success",
            "title": "Lưu khuôn mặt thành công",
            "message": "Đã lưu",
        }

        runtime._on_enrollment_face_left()

        self.assertEqual(runtime.enrollment_result["state"], "idle")
        self.assertIn("Giữ yên khuôn mặt", runtime.enrollment_result["message"])

    def test_processed_face_does_not_block_next_person(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        first = (100, 80, 260, 300, 0.95)
        second = (350, 90, 500, 310, 0.94)
        runtime._kiosk_active_face_box = first[:4]
        runtime._hold_result_and_suppress_active_face(now=10.0)

        eligible = runtime._filter_processed_kiosk_faces(
            [first, second], now=11.6
        )

        self.assertEqual(eligible, [second])

    def test_processed_face_suppression_expires_without_empty_frame(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        face = (100, 80, 260, 300, 0.95)
        runtime._kiosk_active_face_box = face[:4]
        runtime._hold_result_and_suppress_active_face(now=10.0)

        eligible = runtime._filter_processed_kiosk_faces([face], now=13.1)

        self.assertEqual(eligible, [face])
        self.assertEqual(runtime._kiosk_suppressed_face_tracks, [])

    def test_result_resume_keeps_processed_face_suppressed(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime._kiosk_active_face_box = (100, 80, 260, 300)
        runtime._hold_result_and_suppress_active_face(now=10.0)

        runtime._resume_kiosk_after_result()

        self.assertFalse(runtime._kiosk_waiting_for_departure)
        self.assertEqual(runtime.recognition_state["state"], "idle")
        self.assertEqual(len(runtime._kiosk_suppressed_face_tracks), 1)


class WebAdminSingleInstanceTests(unittest.TestCase):
    def test_normal_admin_window_never_uses_kiosk_view(self):
        specs = _window_specs(simulate_pi=False)

        self.assertEqual(len(specs), 1)
        self.assertIn("view=admin", specs[0]["url"])
        self.assertNotIn("view=kiosk", specs[0]["url"])

    def test_display_simulation_cli_aliases(self):
        self.assertTrue(parse_web_admin_args(["--simulate-display"]).simulate_display)
        self.assertTrue(parse_web_admin_args(["--simulate-pi"]).simulate_display)
        args = parse_launcher_args(["usb", "--simulate-display"])
        self.assertEqual(args.camera, "usb")
        self.assertTrue(args.simulate_display)

    def test_pi_simulation_uses_one_kiosk_and_one_admin_window(self):
        specs = _window_specs(simulate_pi=True)

        self.assertEqual(len(specs), 2)
        self.assertIn("view=kiosk", specs[0]["url"])
        self.assertEqual((specs[0]["width"], specs[0]["height"]), (480, 320))
        self.assertNotIn("view=kiosk", specs[1]["url"])
        self.assertIn("view=admin", specs[1]["url"])

    def test_port_is_reserved_before_backend_startup(self):
        first = _reserve_server_socket(host="127.0.0.1", port=0)
        self.assertIsNotNone(first)
        port = first.getsockname()[1]
        try:
            second = _reserve_server_socket(host="127.0.0.1", port=port)
            self.assertIsNone(second)
        finally:
            first.close()


class EnrollmentFaceQualityTests(unittest.TestCase):
    @staticmethod
    def _face_detection(score=0.95):
        point = lambda x, y: SimpleNamespace(x=x, y=y)
        return SimpleNamespace(
            bounding_box=SimpleNamespace(
                origin_x=100, origin_y=60, width=200, height=240
            ),
            categories=[SimpleNamespace(score=score)],
            keypoints=[
                point(0.35, 0.30), point(0.65, 0.30),
                point(0.50, 0.45), point(0.50, 0.60),
            ],
        )

    def test_strict_enrollment_rejects_occluded_mouth_region(self):
        results = SimpleNamespace(detections=[self._face_detection()])
        featureless_mouth_frame = np.full((400, 400, 3), 128, dtype=np.uint8)

        valid, message = analyze_face_quality(
            (100, 60, 300, 300), results, 400, 400,
            frame=featureless_mouth_frame, strict_mode=True,
        )

        self.assertFalse(valid)
        self.assertEqual(message, "Khuon mat chua ro rang")

    def test_non_strict_pose_check_remains_available_for_attendance(self):
        results = SimpleNamespace(detections=[self._face_detection()])

        valid, message = analyze_face_quality(
            (100, 60, 300, 300), results, 400, 400
        )

        self.assertTrue(valid)
        self.assertEqual(message, "")

    def test_face_brightness_rejects_dark_and_overexposed_frames(self):
        face_box = (100, 60, 300, 300)
        dark_ok, dark_value = measure_face_brightness(
            np.full((400, 400, 3), 20, dtype=np.uint8), face_box
        )
        normal_ok, normal_value = measure_face_brightness(
            np.full((400, 400, 3), 120, dtype=np.uint8), face_box
        )
        overexposed_ok, _ = measure_face_brightness(
            np.full((400, 400, 3), 250, dtype=np.uint8), face_box
        )

        self.assertFalse(dark_ok)
        self.assertLess(dark_value, normal_value)
        self.assertTrue(normal_ok)
        self.assertFalse(overexposed_ok)

    def test_dark_face_cannot_lock_enrollment_scan(self):
        results = SimpleNamespace(detections=[self._face_detection()])
        dark_frame = np.full((400, 400, 3), 20, dtype=np.uint8)

        _color, message, locked = check_face_constraints(
            [(100, 60, 300, 300, 0.95)],
            (50, 40, 350, 340),
            results,
            400,
            400,
            frame=dark_frame,
            strict_mode=True,
        )

        self.assertFalse(locked)
        self.assertEqual(message, "Khuon mat chua ro rang")


if __name__ == "__main__":
    unittest.main()
