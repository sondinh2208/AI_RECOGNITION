from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import start_wifi_facecheck as launcher


class _CameraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({
                "ok": True,
                "camera": "OV3660",
                "ip": "127.0.0.1",
                "target_fps": 10,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def log_message(self, _format, *_args):
        pass


class WifiLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _CameraHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_camera_health_accepts_facecheck_camera(self):
        host = f"127.0.0.1:{self.server.server_port}"
        health = launcher.camera_health(host)
        self.assertEqual(health["camera"], "OV3660")
        self.assertEqual(health["target_fps"], 10)

    def test_discovery_uses_preferred_host_before_lan_scan(self):
        host = f"127.0.0.1:{self.server.server_port}"
        with patch.object(launcher, "local_ipv4_networks") as network:
            discovered = launcher.discover_camera(host, scan_lan=True)
        self.assertEqual(discovered[0], host)
        network.assert_not_called()

    def test_discovery_uses_host_loaded_from_dotenv(self):
        host = f"127.0.0.1:{self.server.server_port}"
        with patch.object(launcher, "local_ipv4_networks") as networks:
            discovered = launcher.discover_camera(
                None,
                scan_lan=True,
                environment={"FACECHECK_CAMERA_HOST": host},
            )
        self.assertEqual(discovered[0], host)
        networks.assert_not_called()

    def test_discovery_scans_every_active_network(self):
        networks = [
            launcher.ipaddress.ip_network("192.168.175.0/30"),
            launcher.ipaddress.ip_network("127.0.0.0/30"),
        ]
        health = {"ok": True, "camera": "OV3660"}
        with (
            patch.object(launcher, "local_ipv4_networks", return_value=networks),
            patch.object(
                launcher,
                "camera_health",
                side_effect=lambda host, timeout=0.7: health if host == "127.0.0.1" else None,
            ),
        ):
            discovered = launcher.discover_camera(None, scan_lan=True, environment={})
        self.assertEqual(discovered, ("127.0.0.1", health))

    def test_invalid_health_payload_is_rejected(self):
        with patch.object(launcher, "camera_health", return_value=None):
            self.assertIsNone(launcher.discover_camera("bad-host", scan_lan=False))


if __name__ == "__main__":
    unittest.main()
