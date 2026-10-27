"""Matching rules can be tested without a camera or runtime instance."""

import unittest

from recognition_policy import match_face_profiles


class FaceMatchingTests(unittest.TestCase):
    def test_empty_profiles_return_no_identity(self):
        result = match_face_profiles([1.0, 0.0], {})
        self.assertIsNone(result.best_profile)
        self.assertEqual(result.decision, "unknown")

    def test_ranking_uses_distance_not_insertion_order(self):
        profiles = {
            "B": {"name": "B", "embedding": [0.0, 1.0]},
            "A": {"name": "A", "embedding": [1.0, 0.0]},
        }
        result = match_face_profiles([1.0, 0.0], profiles)
        self.assertIs(result.best_profile, profiles["A"])
        self.assertIs(result.second_profile, profiles["B"])
        self.assertEqual(result.decision, "matched")

    def test_live_search_excludes_disabled_and_benchmark_profiles(self):
        profiles = {
            "disabled": {"embedding": [1.0, 0.0], "recognition_enabled": False},
            "benchmark": {"embedding": [1.0, 0.0], "source": "LFW benchmark"},
        }
        self.assertIsNone(match_face_profiles([1.0, 0.0], profiles).best_profile)

    def test_static_test_can_include_benchmark_profiles(self):
        profile = {"embedding": [1.0, 0.0], "source": "LFW benchmark"}
        result = match_face_profiles([1.0, 0.0], {"test": profile}, live_only=False)
        self.assertIs(result.best_profile, profile)
        self.assertEqual(result.decision, "matched")

    def test_single_profile_still_requires_absolute_distance_threshold(self):
        profile = {"embedding": [1.0, 0.0]}
        result = match_face_profiles([0.0, 1.0], {"A": profile})
        self.assertEqual(result.margin, float("inf"))
        self.assertEqual(result.decision, "unknown")

    def test_invalid_profile_without_embeddings_is_ignored(self):
        result = match_face_profiles([1.0, 0.0], {"empty": {"name": "empty"}})
        self.assertIsNone(result.best_profile)


if __name__ == "__main__":
    unittest.main()
