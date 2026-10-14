from __future__ import annotations

import unittest

from tools.ws_camera_bridge import LatestFrameStore


JPEG = b"\xff\xd8test-frame\xff\xd9"


class LatestFrameStoreSessionTests(unittest.TestCase):
    def test_superseded_session_cannot_disconnect_replacement(self) -> None:
        store = LatestFrameStore()

        first = store.begin_session()
        second = store.begin_session()
        store.end_session(first)

        status = store.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["active_connections"], 1)
        self.assertEqual(status["session_id"], second)

    def test_only_current_session_can_publish_frames(self) -> None:
        store = LatestFrameStore()

        first = store.begin_session()
        second = store.begin_session()

        self.assertFalse(store.update(JPEG, session_id=first))
        self.assertTrue(store.update(JPEG, session_id=second))
        self.assertEqual(store.status()["frames"], 1)

        store.end_session(second)
        self.assertFalse(store.status()["connected"])


if __name__ == "__main__":
    unittest.main()
