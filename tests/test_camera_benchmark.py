import unittest

from tools.benchmark_wifi_camera import Measurement, choose_fps, percentile


def sample(target, actual, p95, jitter=10, stalls=0):
    return Measurement(target, actual, p95, p95, jitter, stalls, 12000)


class CameraBenchmarkTests(unittest.TestCase):
    def test_percentile_uses_observed_upper_value(self):
        self.assertEqual(percentile([100, 110, 120, 500], 0.95), 500)

    def test_choose_highest_stable_fps_up_to_consumer_limit(self):
        results = [
            sample(6, 5.8, 190),
            sample(7, 6.8, 180),
            sample(8, 7.7, 180),
            sample(9, 8.5, 170),
        ]
        self.assertEqual(choose_fps(results, max_useful_fps=8).target_fps, 8)

    def test_unstable_high_target_is_not_selected(self):
        results = [
            sample(7, 6.7, 190),
            sample(8, 4.0, 700, jitter=90, stalls=2),
        ]
        self.assertEqual(choose_fps(results).target_fps, 7)

    def test_every_repeated_trial_must_be_stable(self):
        results = [
            sample(6, 6.0, 190),
            sample(6, 5.9, 200),
            sample(8, 7.9, 180),
            sample(8, 6.8, 700, jitter=90, stalls=1),
        ]
        self.assertEqual(choose_fps(results).target_fps, 6)

    def test_empty_measurements_are_rejected(self):
        with self.assertRaises(ValueError):
            choose_fps([])


if __name__ == "__main__":
    unittest.main()
