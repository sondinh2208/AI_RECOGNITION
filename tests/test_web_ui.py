"""Read-only smoke tests for the HTML UI bridge."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

import httpx
import numpy as np


os.environ["FACECHECK_WEB_SKIP_AI"] = "1"

from web_backend.app import app  # noqa: E402
from web_runtime import WebFaceCheckRuntime  # noqa: E402
from web_admin import _reserve_server_socket  # noqa: E402
from ai_engine import (  # noqa: E402
    analyze_face_quality, check_face_constraints, measure_face_brightness,
)


class WebUiSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_spa_shell_contains_all_existing_pages(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        for page in ("dashboard", "enrollment", "recognition", "employees", "history"):
            self.assertIn(f'data-page="{page}"', html)
        self.assertEqual(html.count('id="cameraSurface"'), 1)
        self.assertIn('type="module" src="/js/app.js"', html)

    async def test_read_only_api_contracts(self):
        state = (await self.client.get("/api/state")).json()
        self.assertIn(state["recognition"]["state"], {
            "idle", "validating", "recognizing", "strict_confirmation",
            "success", "duplicate", "ambiguous", "unknown", "error",
        })
        self.assertIn("capture_ready", state["enrollment"])
        self.assertIn("brightness", state["camera"])
        self.assertIn("light_ok", state["camera"])

        employees = (await self.client.get(
            "/api/employees", params={"page": 1, "page_size": 2}
        )).json()
        self.assertLessEqual(len(employees["items"]), 2)
        self.assertIn("enabled_total", employees)

        history = (await self.client.get(
            "/api/attendance", params={"page": 1, "page_size": 5}
        )).json()
        self.assertLessEqual(len(history["items"]), 5)

    async def test_frontend_contains_no_ai_decision_parameters(self):
        response = await self.client.get("/js/app.js")
        source = response.text
        for forbidden in (
            "ARCFACE_THRESHOLD", "ARCFACE_TOP2_MARGIN",
            "ARCFACE_UNCERTAIN_THRESHOLD", "classify_recognition_score",
        ):
            self.assertNotIn(forbidden, source)


class RuntimeLifecycleTests(unittest.TestCase):
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
