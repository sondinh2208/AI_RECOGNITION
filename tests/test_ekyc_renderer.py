import unittest

import numpy as np

from config import STATUS_BAR_HEIGHT
from ekyc_renderer import compute_ekyc_layout, draw_bottom_status_bar


class EkycRendererLayoutTests(unittest.TestCase):
    def test_face_guide_stays_above_status_bar(self):
        width, height = 640, 480
        (_, _, _, guide_bottom), _ = compute_ekyc_layout(width, height)
        self.assertLess(guide_bottom + 5, height - STATUS_BAR_HEIGHT)

    def test_status_bar_is_opaque_and_separate_from_camera(self):
        width, height = 640, 480
        frame = np.full((height, width, 3), 220, dtype=np.uint8)
        draw_bottom_status_bar(
            frame,
            "Dua mat vao khung hinh",
            (75, 195, 140),
            width,
            height,
        )
        bar_y = height - STATUS_BAR_HEIGHT
        self.assertTupleEqual(tuple(frame[-1, -1]), (18, 24, 33))
        self.assertFalse(np.all(frame[bar_y + 2 :] == 220))


if __name__ == "__main__":
    unittest.main()
