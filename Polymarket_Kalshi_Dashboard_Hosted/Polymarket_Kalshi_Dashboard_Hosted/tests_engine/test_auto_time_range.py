import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import resolve_auto_time_range


class TestAutoTimeRange(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    def test_non_auto_passes_through_unchanged(self):
        self.assertEqual(resolve_auto_time_range("30D", None, self.now), "30D")
        self.assertEqual(resolve_auto_time_range("CURRENT", None, self.now), "CURRENT")
        self.assertEqual(resolve_auto_time_range("current", None, self.now), "CURRENT")

    def test_auto_with_no_start_date_falls_back_to_30d(self):
        self.assertEqual(resolve_auto_time_range("AUTO", None, self.now), "30D")

    def test_auto_blank_defaults_to_auto_behavior(self):
        start = self.now - timedelta(hours=6)
        self.assertEqual(resolve_auto_time_range("", start, self.now), "24H")
        self.assertEqual(resolve_auto_time_range(None, start, self.now), "24H")

    def test_auto_bucket_boundaries(self):
        cases = [
            (timedelta(hours=6), "24H"),
            (timedelta(days=1), "24H"),
            (timedelta(days=3), "7D"),
            (timedelta(days=7), "7D"),
            (timedelta(days=15), "30D"),
            (timedelta(days=30), "30D"),
            (timedelta(days=60), "90D"),
            (timedelta(days=90), "90D"),
            (timedelta(days=200), "ALL"),
        ]
        for age, expected in cases:
            start = self.now - age
            self.assertEqual(
                resolve_auto_time_range("AUTO", start, self.now), expected,
                msg=f"age={age} expected={expected}",
            )

    def test_naive_datetime_treated_as_utc(self):
        start = (self.now - timedelta(hours=3)).replace(tzinfo=None)
        self.assertEqual(resolve_auto_time_range("AUTO", start, self.now), "24H")


if __name__ == "__main__":
    unittest.main()
