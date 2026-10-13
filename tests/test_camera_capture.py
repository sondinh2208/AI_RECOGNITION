import threading
import unittest
from unittest.mock import patch

import cv2

from camera_capture import LatestMjpegCapture, open_camera_capture


class CameraCaptureFactoryTests(unittest.TestCase):
    def test_latest_jpeg_returns_original_compressed_frame(self):
        capture = LatestMjpegCapture.__new__(LatestMjpegCapture)
        capture._condition = threading.Condition()
        capture._sequence = 7
        capture._jpeg = b"original-jpeg"

        self.assertEqual(capture.latest_jpeg(), (7, b"original-jpeg"))

    def test_http_source_uses_latest_frame_reader(self):
        with patch.object(LatestMjpegCapture, "__init__", return_value=None) as init:
            capture = open_camera_capture("http://camera.local:81/stream", cv2.CAP_FFMPEG)

        self.assertIsInstance(capture, LatestMjpegCapture)
        init.assert_called_once_with("http://camera.local:81/stream")

    def test_local_camera_keeps_requested_opencv_backend(self):
        sentinel = object()
        with patch("camera_capture.cv2.VideoCapture", return_value=sentinel) as video_capture:
            capture = open_camera_capture(1, cv2.CAP_DSHOW)

        self.assertIs(capture, sentinel)
        video_capture.assert_called_once_with(1, cv2.CAP_DSHOW)


if __name__ == "__main__":
    unittest.main()
