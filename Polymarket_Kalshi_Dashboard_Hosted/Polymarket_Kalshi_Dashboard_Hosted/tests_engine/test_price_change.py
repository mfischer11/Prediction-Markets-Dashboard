import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import HistoryPoint, Outcome
from src.utils import apply_price_changes, compute_price_change


class TestComputePriceChange(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

    def _points(self, ages_hours_and_probs):
        return [
            HistoryPoint(self.now - timedelta(hours=h), p)
            for h, p in ages_hours_and_probs
        ]

    def test_basic_24h_change(self):
        points = self._points([(48, 0.30), (25, 0.35), (23, 0.40), (1, 0.55)])
        change = compute_price_change(points, current_prob=0.60, hours=24,
                                       reference_time=self.now)
        # closest point at-or-before 24h ago is the one at 25h (0.35)
        self.assertAlmostEqual(change, 0.60 - 0.35)

    def test_basic_7d_change(self):
        points = self._points([(200, 0.10), (170, 0.20), (100, 0.40), (1, 0.55)])
        change = compute_price_change(points, current_prob=0.60, hours=24 * 7,
                                       reference_time=self.now)
        # 168h ago cutoff -> closest point at-or-before is the 170h one (0.20)
        self.assertAlmostEqual(change, 0.60 - 0.20)

    def test_negative_change(self):
        points = self._points([(30, 0.80)])
        change = compute_price_change(points, current_prob=0.55, hours=24,
                                       reference_time=self.now)
        self.assertAlmostEqual(change, 0.55 - 0.80)
        self.assertLess(change, 0)

    def test_no_data_old_enough_returns_none(self):
        # Market only has 3 hours of history - can't compute a 24h change.
        points = self._points([(3, 0.40), (1, 0.42)])
        change = compute_price_change(points, current_prob=0.42, hours=24,
                                       reference_time=self.now)
        self.assertIsNone(change)

    def test_empty_points_returns_none(self):
        self.assertIsNone(compute_price_change([], 0.5, 24, self.now))
        self.assertIsNone(compute_price_change(None, 0.5, 24, self.now))

    def test_none_current_prob_returns_none(self):
        points = self._points([(30, 0.40)])
        self.assertIsNone(compute_price_change(points, None, 24, self.now))


class TestApplyPriceChanges(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

    def test_applies_to_outcome_and_complement(self):
        points = [
            HistoryPoint(self.now - timedelta(hours=30), 0.40),
            HistoryPoint(self.now - timedelta(hours=1), 0.55),
        ]
        yes = Outcome(name="Yes", probability=0.63)
        no = Outcome(name="No", probability=0.37)
        apply_price_changes(yes, points, complement=no)

        self.assertAlmostEqual(yes.change_24h, 0.63 - 0.40)
        # No's change is the exact negative of Yes's - they're
        # complementary by construction.
        self.assertAlmostEqual(no.change_24h, -(0.63 - 0.40))

    def test_no_complement_leaves_other_outcome_untouched(self):
        points = [HistoryPoint(self.now - timedelta(hours=30), 0.40)]
        outcome = Outcome(name="Candidate A", probability=0.55)
        apply_price_changes(outcome, points)
        self.assertIsNotNone(outcome.change_24h)

    def test_missing_history_leaves_none(self):
        outcome = Outcome(name="Thin Market", probability=0.10)
        apply_price_changes(outcome, [])
        self.assertIsNone(outcome.change_24h)
        self.assertIsNone(outcome.change_7d)


if __name__ == "__main__":
    unittest.main()
