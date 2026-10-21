"""Read-only smoke tests for the HTML UI bridge."""

from __future__ import annotations

import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from types import SimpleNamespace

import httpx
import cv2
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
    AttendanceMixin, aggregate_query_embeddings, calculate_profile_distance,
    profile_is_live_recognition_enabled,
)


class RecognitionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = AttendanceMixin.__new__(AttendanceMixin)
        self.policy._clear_strict_confirmation()

    def test_benchmark_profile_is_never_used_for_live_recognition(self):
        self.assertFalse(profile_is_live_recognition_enabled({
            "recognition_enabled": True,
            "source": "LFW benchmark",
        }))
        self.assertTrue(profile_is_live_recognition_enabled({
            "recognition_enabled": True,
            "source": None,
        }))

    def test_profile_distance_uses_centroid_instead_of_nearest_outlier(self):
        query = [1.0, 0.0]
        profile = {"embeddings": [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]}
        distance = calculate_profile_distance(query, profile)
        self.assertGreater(distance, 0.5)

    def test_two_frame_embedding_average_suppresses_opposite_noise(self):
        combined = aggregate_query_embeddings([[1.0, 0.2], [1.0, -0.2]])
        self.assertGreater(float(combined[0]), 0.999)
        self.assertAlmostEqual(float(combined[1]), 0.0, places=5)

    def test_query_aggregation_requires_two_fresh_frames(self):
        self.policy._kiosk_active_face_box = (10, 10, 100, 100)
        first = self.policy._aggregate_current_query([1.0, 0.1], now=1.0)
        second = self.policy._aggregate_current_query([1.0, -0.1], now=1.4)
        self.assertIsNone(first)
        self.assertIsNotNone(second)

    def test_strict_votes_require_real_temporal_span(self):
        for offset in (0.0, 0.1, 0.2):
            state = self.policy._record_strict_vote(
                "NV001", 0.45, 0.13, now=100.0 + offset
            )
        self.assertFalse(self.policy._strict_state_is_acceptable(state))

        self.policy._clear_strict_confirmation()
        for offset in (0.0, 0.3, 0.6):
            state = self.policy._record_strict_vote(
                "NV001", 0.45, 0.13, now=200.0 + offset
            )
        self.assertTrue(self.policy._strict_state_is_acceptable(state))

    def test_noisy_frame_breaks_strict_streak(self):
        now = time.time()
        first = self.policy._record_strict_vote("NV001", 0.45, 0.10, now=now)
        noisy = self.policy._record_strict_noise(now=now + 0.1)
        second = self.policy._record_strict_vote("NV001", 0.44, 0.11, now=now + 0.2)

        self.assertEqual(first["votes"], 1)
        self.assertEqual(noisy["votes"], 0)
        self.assertEqual(noisy["attempts"], 2)
        self.assertEqual(second["votes"], 1)
        self.assertEqual(second["attempts"], 3)

    def test_two_very_strong_frames_use_safe_fast_path(self):
        first = self.policy._record_strict_vote("NV001", 0.36, 0.15, now=10.0)
        second = self.policy._record_strict_vote("NV001", 0.38, 0.14, now=10.3)
        self.assertFalse(self.policy._strict_state_is_acceptable(first))
        self.assertTrue(self.policy._strict_state_is_acceptable(second))

    def test_isolated_low_distance_spike_cannot_accumulate(self):
        self.policy._record_strict_vote("NV001", 0.45, 0.10, now=20.0)
        self.policy._record_strict_noise(now=20.3)
        self.policy._record_strict_vote("NV001", 0.45, 0.10, now=20.6)
        self.policy._record_strict_noise(now=20.9)
        state = self.policy._record_strict_vote("NV001", 0.45, 0.10, now=21.2)
        self.assertEqual(state["votes"], 1)
        self.assertFalse(self.policy._strict_state_is_acceptable(state))


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
        for page in ("dashboard", "enrollment", "recognition", "employees", "fingerprints", "history"):
            self.assertIn(f'data-page="{page}"', html)
        self.assertEqual(html.count('id="cameraSurface"'), 1)
        self.assertIn('id="openLiveCamera"', html)
        self.assertIn('class="card recognition-monitor"', html)
        self.assertIn('kiosk-recognition-camera', html)
        self.assertIn('type="module" src="/js/app.js"', html)
        self.assertIn('/css/kiosk.css', html)
        self.assertIn('id="startEnrollmentScan"', html)
        self.assertIn('id="monitorDoorState"', html)
        self.assertIn('id="monitorFingerprintState"', html)
        self.assertNotIn('id="monitorDoorEvent"', html)
        self.assertNotIn("Camera trực tiếp đang hiển thị tại kiosk", html)
        self.assertNotIn("Sự kiện bộ điều khiển gần nhất", html)
        self.assertIn('class="fingerprint-guide"', html)
        self.assertIn('id="fingerprintPagination"', html)

    async def test_read_only_api_contracts(self):
        state = (await self.client.get("/api/state")).json()
        self.assertIn("access", state)
        self.assertIn("access_label", state["access"])
        self.assertIn(state["recognition"]["state"], {
            "idle", "validating", "recognizing", "strict_confirmation",
            "success", "duplicate", "ambiguous", "unknown", "error",
        })
        self.assertIn("capture_ready", state["enrollment"])
        self.assertIn("sample_count", state["enrollment"])
        self.assertIn("sample_target", state["enrollment"])
        self.assertIn("brightness", state["camera"])
        self.assertIn("light_ok", state["camera"])
        self.assertIn("fingerprint_sensor_state", state["door"])

        door_events = (await self.client.get("/api/door/events")).json()
        self.assertEqual(door_events["device_id"], "door-01")
        self.assertIsInstance(door_events["items"], list)

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
            if employee["image_url"]:
                self.assertIn("?v=", employee["image_url"])
                image_response = await self.client.get(employee["image_url"])
                if image_response.status_code == 200:
                    self.assertIn("no-store", image_response.headers["cache-control"])
            else:
                self.assertFalse(employee["face_registered"])

        history = (await self.client.get(
            "/api/attendance", params={"page": 1, "page_size": 5}
        )).json()
        self.assertLessEqual(len(history["items"]), 5)

    async def test_fingerprint_management_is_available_on_local_admin_server(self):
        response = await self.client.get("/api/admin/fingerprints")
        self.assertEqual(response.status_code, 200)
        self.assertIn("items", response.json())

    async def test_fingerprint_delete_requires_matching_confirmation_id(self):
        headers = {"content-type": "application/json"}
        with patch(
            "web_backend.app.runtime.door_service.fingerprint",
            return_value={"fingerprint_id": 12},
        ), patch(
            "web_backend.app.runtime.delete_fingerprint",
            return_value={"accepted": True, "command_id": "fp-test"},
        ) as delete:
            rejected = await self.client.request(
                "DELETE", "/api/admin/fingerprints/12",
                headers=headers, json={"confirmation_id": "11"},
            )
            accepted = await self.client.request(
                "DELETE", "/api/admin/fingerprints/12",
                headers=headers, json={"confirmation_id": "12"},
            )

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 202)
        delete.assert_called_once_with(12)

    async def test_fingerprint_identity_update_is_available_locally(self):
        payload = {"employee_id": "NV002", "name": "Nguyen Van B"}
        with patch(
            "web_backend.app.runtime.update_fingerprint",
            return_value={"fingerprint_id": 2, **payload},
        ) as update:
            response = await self.client.put(
                "/api/admin/fingerprints/2",
                json=payload,
            )
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with(2, payload)

    async def test_frontend_contains_no_ai_decision_parameters(self):
        response = await self.client.get("/js/app.js")
        source = response.text
        self.assertIn('["attendance", "add_employee"].includes(mode)', source)
        self.assertIn('const simulatedAdminView = viewRole === "admin"', source)
        self.assertIn('let adminLiveViewOpen = false', source)
        self.assertIn('enrollmentActive', source)
        self.assertIn(
            'const active = visible && (kioskActive || enrollmentActive || adminLiveViewOpen)',
            source,
        )
        self.assertNotIn('else if (!simulatedAdminView', source)
        self.assertIn("function clearFaceEnrollmentForm()", source)
        self.assertIn("form.reset()", source)
        for forbidden in (
            "ARCFACE_THRESHOLD", "ARCFACE_TOP2_MARGIN",
            "ARCFACE_UNCERTAIN_THRESHOLD", "classify_recognition_score",
        ):
            self.assertNotIn(forbidden, source)

    async def test_recent_history_does_not_mark_full_history_cache_fresh(self):
        source = (await self.client.get("/js/app.js")).text
        start = source.index("async function loadRecent()")
        end = source.index("\n}\n", start) + 2
        load_recent = source[start:end]
        self.assertNotIn("store.history.stale = false", load_recent)

    async def test_fingerprint_enrollment_form_is_cleared_after_finished_ack(self):
        html = (await self.client.get("/")).text
        source = (await self.client.get("/js/app.js")).text
        self.assertIn('id="fingerprintEnrollForm" autocomplete="off"', html)
        self.assertIn("function clearFingerprintEnrollForm()", source)
        self.assertIn('management.command === "FINGERPRINT_ENROLL"', source)
        self.assertIn("clearFingerprintEnrollForm();", source)

    async def test_fingerprint_ack_refreshes_data_from_every_admin_page(self):
        source = (await self.client.get("/js/app.js")).text
        self.assertIn(
            'if (!kioskView) renderFingerprintState(state.door || {});', source,
        )
        self.assertNotIn(
            'if (store.route === "fingerprints") renderFingerprintState', source,
        )

    async def test_ui_language_matches_access_management_product(self):
        html = (await self.client.get("/")).text
        router = (await self.client.get("/js/router.js")).text
        source = (await self.client.get("/js/app.js")).text

        self.assertIn(
            "Hệ thống quản lý ra vào tích hợp nhận diện khuôn mặt và cảm biến vân tay",
            html,
        )
        self.assertIn(">Giám sát cửa<", html)
        self.assertIn(">Đăng ký khuôn mặt<", html)
        self.assertIn(">Nhật ký xác thực<", html)
        self.assertIn('recognition: ["Giám sát cửa"', router)
        self.assertIn('$("#testRecognitionImage").hidden = !debugView', source)
        self.assertNotIn("Cửa đang đóng", html)
        self.assertNotIn("Đã mở cửa", html)

    async def test_idle_state_does_not_claim_physical_door_position(self):
        state = (await self.client.get("/api/state")).json()
        self.assertEqual(state["recognition"]["title"], "Chờ xác thực")
        self.assertNotIn("đóng", state["recognition"]["title"].casefold())


class RuntimeLifecycleTests(unittest.TestCase):
    def test_access_state_waits_for_face_or_fingerprint(self):
        runtime = WebFaceCheckRuntime(load_ai=False)

        access = runtime.snapshot()["access"]

        self.assertEqual(access["state"], "idle")
        self.assertEqual(access["method_label"], "Khuôn mặt hoặc vân tay")
        self.assertEqual(access["access_label"], "Sẵn sàng xác thực")

    def test_fingerprint_granted_becomes_shared_access_result(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            "NV001": {
                "id": "NV001", "name": "Đinh Sơn", "role": "Kỹ sư",
                "department": "AI",
            }
        }
        runtime.door_service.fingerprint = Mock(return_value={
            "fingerprint_id": 2, "employee_id": "NV001", "name": "Đinh Sơn",
        })
        runtime._add_attendance_record = Mock()

        runtime._on_door_event({
            "event_id": "fp-granted-1", "event": "fingerprint_granted",
            "fingerprint_id": 2,
        })
        access = runtime.snapshot()["access"]

        self.assertEqual(access["state"], "success")
        self.assertEqual(access["method"], "fingerprint")
        self.assertEqual(access["method_label"], "Vân tay · ID #2")
        self.assertEqual(access["access_label"], "Đã cho phép ra vào")
        self.assertEqual(access["employee"]["id"], "NV001")
        self.assertEqual(access["employee"]["role"], "Kỹ sư")

    def test_fingerprint_denied_never_grants_access(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime._add_attendance_record = Mock()

        runtime._on_door_event({
            "event_id": "fp-denied-1", "event": "fingerprint_denied",
        })
        access = runtime.snapshot()["access"]

        self.assertEqual(access["state"], "unknown")
        self.assertEqual(access["method"], "fingerprint")
        self.assertEqual(access["access_label"], "Không được phép ra vào")
        self.assertIsNone(access["employee"])

    def test_expired_fingerprint_result_returns_to_face_state(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime._fingerprint_access_state = {"state": "success"}
        runtime._fingerprint_access_until = 0.0

        access = runtime.snapshot()["access"]

        self.assertEqual(access["state"], "idle")
        self.assertEqual(access["method_label"], "Khuôn mặt hoặc vân tay")

    def test_employee_list_prioritizes_enabled_then_sorts_name_a_to_z(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.door_service.fingerprints = Mock(return_value=[])
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

    def test_employee_list_excludes_benchmark_profiles(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            "NV001": {"id": "NV001", "name": "Đinh Sơn"},
            "LFW001": {
                "id": "LFW001", "name": "Benchmark",
                "source": "LFW benchmark", "recognition_enabled": False,
            },
        }
        runtime.door_service.fingerprints = Mock(return_value=[])

        data = runtime.list_employees(page=1, page_size=10)

        self.assertEqual(data["all_total"], 1)
        self.assertEqual(data["items"][0]["id"], "NV001")

    def test_employee_list_merges_face_and_fingerprint_credentials(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            "NV001": {
                "id": "NV001", "name": "Đinh Sơn", "role": "Kỹ sư",
                "department": "AI", "recognition_enabled": True,
            }
        }
        runtime.door_service.fingerprints = Mock(return_value=[
            {"fingerprint_id": 2, "employee_id": "NV001", "name": "Đinh Sơn"},
        ])

        data = runtime.list_employees(page=1, page_size=10)

        self.assertEqual(data["all_total"], 1)
        self.assertEqual(data["face_total"], 1)
        self.assertEqual(data["fingerprint_total"], 1)
        self.assertEqual(data["both_total"], 1)
        self.assertTrue(data["items"][0]["face_registered"])
        self.assertEqual(data["items"][0]["fingerprint_ids"], [2])

    def test_employee_list_includes_fingerprint_only_identity(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {}
        runtime.door_service.fingerprints = Mock(return_value=[
            {
                "fingerprint_id": 7, "employee_id": "NV007",
                "name": "Nguyễn Văn A", "role": "Kỹ sư",
                "department": "Phòng AI",
            },
        ])

        data = runtime.list_employees(page=1, page_size=10)

        self.assertEqual(data["all_total"], 1)
        self.assertEqual(data["items"][0]["id"], "NV007")
        self.assertFalse(data["items"][0]["face_registered"])
        self.assertTrue(data["items"][0]["fingerprint_registered"])
        self.assertTrue(data["items"][0]["access_enabled"])
        self.assertEqual(data["items"][0]["role"], "Kỹ sư")
        self.assertEqual(data["items"][0]["department"], "Phòng AI")

    def test_new_fingerprint_profile_does_not_silently_reuse_existing_employee_id(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            "NV001": {
                "id": "NV001", "name": "Nguyễn Văn A", "role": "Kỹ sư",
                "department": "Phòng AI", "recognition_enabled": True,
            }
        }
        runtime.door_service.fingerprints = Mock(return_value=[])

        with self.assertRaisesRegex(ValueError, "employee_id_exists_select_profile"):
            runtime.enroll_fingerprint({
                "fingerprint_id": 2,
                "existing_employee_id": "",
                "employee_id": "NV001",
                "name": "Người khác",
                "role": "Nhân viên",
                "department": "Phòng IT",
            })

    def test_selected_fingerprint_profile_uses_canonical_employee_information(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {
            "NV001": {
                "id": "NV001", "name": "Nguyễn Văn A", "role": "Kỹ sư",
                "department": "Phòng AI", "recognition_enabled": True,
            }
        }
        runtime.door_service.fingerprints = Mock(return_value=[])
        runtime.door_service.request_fingerprint_enroll = Mock(
            return_value={"accepted": True, "command_id": "fp-test"},
        )

        runtime.enroll_fingerprint({
            "fingerprint_id": 2,
            "existing_employee_id": "NV001",
            "employee_id": "NV001",
            "name": "Thông tin nhập sai",
            "role": "Sai",
            "department": "Sai",
        })

        runtime.door_service.request_fingerprint_enroll.assert_called_once_with(
            2,
            employee_id="NV001",
            name="Nguyễn Văn A",
            role="Kỹ sư",
            department="Phòng AI",
        )

    def test_fingerprint_only_employee_profile_can_be_updated(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.embeddings_cache = {}
        registry = [{
            "fingerprint_id": 7, "employee_id": "NV007",
            "name": "Nguyễn Văn A", "role": "Nhân viên",
            "department": "—",
        }]
        runtime.door_service.fingerprints = Mock(side_effect=lambda: [dict(item) for item in registry])

        def update_identity(fingerprint_id, employee_id, name, role, department):
            registry[0].update({
                "fingerprint_id": fingerprint_id, "employee_id": employee_id,
                "name": name, "role": role, "department": department,
            })
            return dict(registry[0])

        runtime.door_service.update_fingerprint_identity = Mock(side_effect=update_identity)
        updated = runtime.update_employee("NV007", {
            "id": "NV007", "name": "Nguyễn Văn A",
            "role": "Kỹ sư", "department": "Phòng AI",
        })

        self.assertEqual(updated["role"], "Kỹ sư")
        self.assertEqual(updated["department"], "Phòng AI")
        runtime.door_service.update_fingerprint_identity.assert_called_once_with(
            7, "NV007", "Nguyễn Văn A", "Kỹ sư", "Phòng AI"
        )

    def test_one_hundred_employees_use_ten_pages_at_ten_per_page(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.door_service.fingerprints = Mock(return_value=[])
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

    def test_cancel_enrollment_scan_keeps_form_mode_and_disarms_camera(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "add_employee"
        runtime._enrollment_scan_armed = True
        runtime.enrollment_face_samples.append(np.zeros((2, 2, 3), dtype=np.uint8))

        state = runtime.cancel_enrollment_scan()

        self.assertEqual(runtime.current_page, "add_employee")
        self.assertFalse(runtime._enrollment_scan_armed)
        self.assertFalse(state["enrollment"]["scan_armed"])
        self.assertEqual(state["enrollment"]["title"], "Đã hủy quét khuôn mặt")
        self.assertEqual(len(runtime.enrollment_face_samples), 0)

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

    def test_attendance_preview_uses_original_camera_jpeg(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "attendance"
        runtime.camera_cap = Mock()
        runtime.camera_cap.latest_jpeg.return_value = (15, b"source-jpeg")

        with patch.object(runtime, "camera_jpeg_frame") as processed:
            result = runtime.camera_preview_jpeg_frame()

        self.assertEqual(result, (15, b"source-jpeg"))
        processed.assert_not_called()

    def test_tft_attendance_frame_fills_480_by_250_area(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "attendance"
        runtime.kiosk_latest_frame = np.zeros((250, 480, 3), dtype=np.uint8)
        runtime._current_frame_id = 7

        frame_id, payload = runtime.display_jpeg_frame()
        decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)

        self.assertEqual(frame_id, 7)
        self.assertEqual(decoded.shape[:2], (250, 480))

    def test_enrollment_preview_keeps_processed_overlays(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "add_employee"
        runtime.camera_cap = Mock()

        with patch.object(
            runtime, "camera_jpeg_frame", return_value=(9, b"processed-jpeg")
        ) as processed:
            result = runtime.camera_preview_jpeg_frame()

        self.assertEqual(result, (9, b"processed-jpeg"))
        processed.assert_called_once_with()

    def test_display_hides_intermediate_recognition_states(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "attendance"
        runtime.recognition_state = {
            "state": "recognizing",
            "title": "Đang xác thực quyền truy cập",
            "message": "Đang đối chiếu",
        }

        display = runtime.display_snapshot()

        self.assertEqual(display["state"], "idle")
        self.assertEqual(display["title"], "NHIN THANG VAO CAMERA")
        self.assertEqual(display["name"], "")

    def test_display_keeps_final_recognition_result(self):
        runtime = WebFaceCheckRuntime(load_ai=False)
        runtime.current_page = "attendance"
        runtime.recognition_state = {
            "state": "success",
            "title": "Điểm danh thành công",
            "message": "Đã mở cửa",
            "employee": {"name": "Nguyen Van A", "id": "NV001"},
        }

        display = runtime.display_snapshot()

        self.assertEqual(display["state"], "success")
        self.assertEqual(display["name"], "Nguyen Van A")
        self.assertEqual(display["employee_id"], "NV001")

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
