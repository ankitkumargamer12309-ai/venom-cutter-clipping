import unittest
from unittest.mock import patch

import api
from api import choose_auto_duration, collapse_motion_events, score_clip_windows


class AutoDurationTests(unittest.TestCase):
    def test_high_activity_selects_short_duration(self):
        self.assertEqual(choose_auto_duration(120, [1, 2, 3, 4, 14, 15, 16, 17, 18, 19], [5, 6, 7, 8, 9, 10, 11, 12]), 15)

    def test_medium_high_activity_selects_30_seconds(self):
        self.assertEqual(choose_auto_duration(120, [1, 2, 3], [5, 6, 7, 8, 9]), 30)

    def test_medium_activity_selects_45_seconds(self):
        self.assertEqual(choose_auto_duration(120, [1, 2], [5, 6]), 45)

    def test_low_activity_selects_60_seconds(self):
        self.assertEqual(choose_auto_duration(120, [], [5]), 60)

    def test_short_video_uses_15_second_target(self):
        self.assertEqual(choose_auto_duration(12, [], []), 15)


class ClipWindowTests(unittest.TestCase):
    def test_returns_at_most_six_ranked_windows(self):
        windows = score_clip_windows(180, 30, [10, 12, 14, 50, 52, 54, 100], [11, 12, 13, 51, 53, 101])
        self.assertGreater(len(windows), 0)
        self.assertLessEqual(len(windows), 6)
        self.assertEqual(windows, sorted(windows, key=lambda item: item["start"]))

    def test_too_short_duration_has_no_windows(self):
        self.assertEqual(score_clip_windows(7, 15, [], []), [])


    def test_combined_scene_and_motion_events_are_counted(self):
        self.assertEqual(api.count_combined_events([10.0, 20.0, 30.0], [11.5, 20.5, 40.0]), 2)

    def test_combined_event_count_is_zero_without_nearby_motion(self):
        self.assertEqual(api.count_combined_events([10.0, 20.0], [15.0, 25.0]), 0)

class RateLimitCleanupTests(unittest.TestCase):
    def setUp(self):
        self.original_rate_limit = dict(api.RATE_LIMIT)
        self.original_sweep = api._last_rate_limit_sweep
        api.RATE_LIMIT.clear()
        api._last_rate_limit_sweep = 0

    def tearDown(self):
        api.RATE_LIMIT.clear()
        api.RATE_LIMIT.update(self.original_rate_limit)
        api._last_rate_limit_sweep = self.original_sweep

    def test_sweep_removes_expired_ips_and_preserves_recent_entries(self):
        now = 5000.0
        api.RATE_LIMIT["expired-client"] = [now - api.RATE_LIMIT_WINDOW - 1]
        api.RATE_LIMIT["recent-client"] = [now - 30]

        with patch.object(api.time, "time", return_value=now):
            self.assertTrue(api.check_rate_limit("new-client"))

        self.assertNotIn("expired-client", api.RATE_LIMIT)
        self.assertEqual(api.RATE_LIMIT["recent-client"], [now - 30])
        self.assertEqual(api.RATE_LIMIT["new-client"], [now])

    def test_rate_limit_still_blocks_fourth_request_in_window(self):
        now = 7000.0
        with patch.object(api.time, "time", return_value=now):
            self.assertTrue(api.check_rate_limit("same-client"))
            self.assertTrue(api.check_rate_limit("same-client"))
            self.assertTrue(api.check_rate_limit("same-client"))
            self.assertFalse(api.check_rate_limit("same-client"))


class MotionEventTests(unittest.TestCase):
    def test_collapses_adjacent_motion_samples_into_events(self):
        self.assertEqual(
            collapse_motion_events([2.0, 0.0, 0.5, 1.0, 2.8, 3.0, 4.2]),
            [0.0, 1.0, 2.0, 3.0, 4.2],
        )

    def test_nonpositive_gap_keeps_sorted_samples(self):
        self.assertEqual(collapse_motion_events([3.0, 1.0, 2.0], 0), [1.0, 2.0, 3.0])


if __name__ == "__main__":
    unittest.main()
