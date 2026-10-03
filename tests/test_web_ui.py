"""Read-only smoke tests for the HTML UI bridge."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock

import httpx


os.environ["FACECHECK_WEB_SKIP_AI"] = "1"

from web_backend.app import app  # noqa: E402
from web_runtime import WebFaceCheckRuntime  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
