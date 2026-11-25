import unittest

from api import choose_auto_duration, score_clip_windows


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


if __name__ == "__main__":
    unittest.main()
