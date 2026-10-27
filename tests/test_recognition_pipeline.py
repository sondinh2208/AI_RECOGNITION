"""Exercise the live recognition path without loading models or opening hardware."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from mixins.attendance import AttendanceMixin
from recognition_policy import classify_recognition_score


class RecognitionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.policy = AttendanceMixin.__new__(AttendanceMixin)
        self.policy._reset_query_aggregation()
        self.policy._kiosk_unknown_attempts = 0
        self.policy.embeddings_cache = {
            "A": {"name": "A", "embedding": [1.0, 0.0]},
            "B": {"name": "B", "embedding": [0.0, 1.0]},
        }
        for name in (
            "_on_kiosk_recognition_success", "_on_kiosk_recognition_failed",
            "_on_kiosk_recognition_ambiguous", "_on_kiosk_query_aggregation_pending",
            "_on_kiosk_unknown_confirmation_pending",
        ):
            setattr(self.policy, name, Mock())
        self.policy._safe_after = lambda delay, callback, *args: callback(*args)
        self.represent = Mock(return_value=[{"embedding": [1.0, 0.0]}])
        self.image = np.zeros((80, 80, 3), dtype=np.uint8)
        self.fake_deepface = SimpleNamespace(
            DeepFace=SimpleNamespace(represent=self.represent)
        )

    def run_frame(self):
        with patch.dict("sys.modules", {"deepface": self.fake_deepface}), patch(
            "mixins.attendance.align_face_crop", return_value=(self.image, 0.0)
        ):
            self.policy._ai_worker_recognize(self.image)

    def test_success_requires_two_query_frames(self):
        self.run_frame()
        self.policy._on_kiosk_recognition_success.assert_not_called()
        self.policy._on_kiosk_query_aggregation_pending.assert_called_once_with(1)
        self.run_frame()
        self.policy._on_kiosk_recognition_success.assert_called_once()
        self.policy._on_kiosk_recognition_failed.assert_not_called()

    def test_similar_profiles_are_ambiguous_even_with_low_distance(self):
        self.policy.embeddings_cache["B"]["embedding"] = [1.0, 0.1]
        self.run_frame()
        self.run_frame()
        self.policy._on_kiosk_recognition_ambiguous.assert_called_once()
        self.policy._on_kiosk_recognition_success.assert_not_called()

    def test_unknown_requires_two_completed_query_comparisons(self):
        self.represent.return_value = [{"embedding": [-1.0, -1.0]}]
        self.run_frame()
        self.run_frame()
        self.policy._on_kiosk_unknown_confirmation_pending.assert_called_once_with(1)
        self.policy._on_kiosk_recognition_failed.assert_not_called()
        self.run_frame()
        self.run_frame()
        self.policy._on_kiosk_recognition_failed.assert_called_once()
        self.policy._on_kiosk_recognition_success.assert_not_called()

    def test_empty_database_fails_closed(self):
        self.policy.embeddings_cache = {}
        self.policy._reload_embeddings_cache = Mock()
        self.run_frame()
        self.run_frame()
        self.policy._on_kiosk_recognition_failed.assert_called_once()
        self.policy._on_kiosk_recognition_success.assert_not_called()

    def test_static_image_reports_match_without_live_access_callback(self):
        self.policy.face_model = None
        self.policy.device = "cpu"
        self.policy._show_recognition_test_result = Mock()
        with patch("mixins.attendance.np.fromfile", return_value=np.array([1])), patch(
            "mixins.attendance.cv2.imdecode", return_value=self.image
        ), patch(
            "mixins.attendance.detect_faces", return_value=[(5, 5, 70, 70, 0.99)]
        ), patch.object(self.policy, "_extract_face_embedding", return_value=[1.0, 0.0]):
            self.policy._worker_recognition_test_image("test.jpg")
        result, error = self.policy._show_recognition_test_result.call_args.args
        self.assertIsNone(error)
        self.assertEqual(result["best_profile"]["name"], "A")
        self.assertIn("ĐẠT NGƯỠNG", result["conclusion"])
        self.policy._on_kiosk_recognition_success.assert_not_called()

    def test_model_exception_clears_partial_query(self):
        self.run_frame()
        self.represent.side_effect = RuntimeError("test model failure")
        self.run_frame()
        self.assertEqual(self.policy._kiosk_query_embeddings, [])
        self.policy._on_kiosk_recognition_failed.assert_called_once()

    def test_expired_partial_query_cannot_be_reused(self):
        self.assertIsNone(self.policy._aggregate_current_query([1.0, 0.0], now=1.0))
        self.assertIsNone(self.policy._aggregate_current_query([1.0, 0.0], now=3.0))
        self.assertIsNotNone(self.policy._aggregate_current_query([1.0, 0.0], now=3.4))

    def test_changed_face_cannot_share_partial_query(self):
        self.policy._kiosk_active_face_box = (0, 0, 80, 80)
        self.policy._aggregate_current_query([1.0, 0.0], now=1.0)
        self.policy._kiosk_active_face_box = (200, 0, 280, 80)
        self.assertIsNone(self.policy._aggregate_current_query([0.0, 1.0], now=1.4))

    def test_threshold_and_margin_are_both_required(self):
        for distance, margin, expected in (
            (0.460, 0.120, "matched"),
            (0.461, 0.200, "ambiguous"),
            (0.200, 0.119, "ambiguous"),
            (0.620, 0.200, "ambiguous"),
            (0.621, 0.200, "unknown"),
        ):
            with self.subTest(distance=distance, margin=margin):
                self.assertEqual(classify_recognition_score(distance, margin), expected)


if __name__ == "__main__":
    unittest.main()
