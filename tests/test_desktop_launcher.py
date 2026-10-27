import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

import desktop_launcher as desktop


class DesktopLauncherTests(unittest.TestCase):
    def test_backend_environment_uses_discovered_camera_and_keeps_token(self):
        with patch.object(desktop, "load_local_environment", return_value={
            "FACECHECK_DISPLAY_TOKEN": "secret", "OPENCV_FFMPEG_CAPTURE_OPTIONS": "old",
        }):
            env = desktop.backend_environment("172.20.10.2", {"ip": "172.20.10.2"})
        self.assertEqual(env["FACECHECK_CAMERA_URL"], "http://172.20.10.2:81/stream")
        self.assertEqual(env["FACECHECK_DISPLAY_TOKEN"], "secret")
        self.assertEqual(env["FACECHECK_CAMERA_PROFILE"], "embedded_ov3660")
        self.assertNotIn("OPENCV_FFMPEG_CAPTURE_OPTIONS", env)

    def test_mdns_is_preserved_for_runtime_recovery(self):
        with patch.object(desktop, "load_local_environment", return_value={}):
            env = desktop.backend_environment("facecheck-cam.local", {"ip": "192.168.1.16"})
        self.assertEqual(env["FACECHECK_CAMERA_URL"], "http://facecheck-cam.local:81/stream")

    def test_probe_has_machine_readable_output_and_failure_exit_code(self):
        for result, expected_code in ((None, 1), (("172.20.10.2", {"ok": True}), 0)):
            output = io.StringIO()
            def probe(*args, **kwargs):
                print("diagnostic")
                return result
            with patch.object(desktop, "wait_for_camera", side_effect=probe), \
                    patch.object(desktop, "load_local_environment", return_value={}), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(desktop.probe_camera(), expected_code)
            self.assertEqual(json.loads(output.getvalue()), json.loads(json.dumps(result)))

    def test_existing_backend_does_not_spawn_or_discover(self):
        app = desktop.DesktopLauncher.__new__(desktop.DesktopLauncher)
        app.process = None
        app.close = Mock()
        with patch.object(desktop, "facecheck_running", return_value=True), \
                patch.object(desktop, "activate_existing") as activate, \
                patch.object(desktop, "hidden_process") as spawn:
            app.start()
        activate.assert_called_once()
        app.close.assert_called_once()
        spawn.assert_not_called()

    def test_probe_timeout_stops_only_owned_child_and_offers_retry(self):
        app = desktop.DesktopLauncher.__new__(desktop.DesktopLauncher)
        app.closed = False
        app.phase = "probe"
        app.started = 0
        app.process = Mock()
        app.process.poll.return_value = None
        app.error = Mock()
        with patch.object(desktop.time, "monotonic", return_value=80), \
                patch.object(desktop, "stop_owned_process") as stop:
            app.tick()
        stop.assert_called_once_with(app.process)
        self.assertEqual(app.error.call_args.args[0], "Chưa tìm thấy camera")

    def test_probe_failure_never_opens_backend(self):
        app = desktop.DesktopLauncher.__new__(desktop.DesktopLauncher)
        app.closed = False
        app.phase = "probe"
        app.process = Mock()
        app.process.poll.return_value = 1
        app.process.communicate.return_value = (b"null\n", None)
        app.error = Mock()
        with patch.object(desktop, "hidden_process") as spawn:
            app.tick()
        app.error.assert_called_once()
        spawn.assert_not_called()


    def test_discovered_camera_starts_logged_hidden_backend(self):
        app = desktop.DesktopLauncher.__new__(desktop.DesktopLauncher)
        app.closed = False
        app.phase = "probe"
        app.process = Mock()
        app.process.poll.return_value = 0
        app.process.communicate.return_value = (
            json.dumps(["172.20.10.2", {"ip": "172.20.10.2"}]).encode(), None)
        app.log = io.StringIO()
        app.root = Mock()
        app.status = Mock()
        app.detail = Mock()
        backend = Mock()
        with patch.object(desktop, "facecheck_running", return_value=False), \
                patch.object(desktop, "python_executable", return_value="venv-python.exe"), \
                patch.object(desktop, "backend_environment", return_value={"camera": "wifi"}), \
                patch.object(desktop, "hidden_process", return_value=backend) as spawn:
            app.tick()
        self.assertEqual(app.phase, "backend")
        self.assertIs(app.process, backend)
        self.assertEqual(spawn.call_args.args[0][-1], str(desktop.ROOT / "web_admin.py"))
        self.assertIs(spawn.call_args.kwargs["stdout"], app.log)
        self.assertEqual(spawn.call_args.kwargs["env"], {"camera": "wifi"})

    def test_healthy_backend_hides_startup_window(self):
        app = desktop.DesktopLauncher.__new__(desktop.DesktopLauncher)
        app.closed = False
        app.phase = "backend"
        app.process = Mock()
        app.process.poll.return_value = None
        app.root = Mock()
        app.progress = Mock()
        app.status = Mock()
        app.detail = Mock()
        with patch.object(desktop, "facecheck_running", return_value=True):
            app.tick()
        self.assertEqual(app.phase, "running")
        app.root.after.assert_any_call(350, app.root.withdraw)
        app.progress.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
