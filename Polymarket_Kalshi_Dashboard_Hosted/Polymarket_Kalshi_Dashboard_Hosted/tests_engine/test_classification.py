import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import DisplayType, HistoryPoint, Market, MarketStatus, Outcome, Platform
from datetime import datetime, timezone


def make_market(outcomes, history=None, display_type=DisplayType.AUTO):
    return Market(
        platform=Platform.POLYMARKET,
        url="https://polymarket.com/market/x",
        title="Test market",
        outcomes=outcomes,
        historical_series=history or [],
        display_type=display_type,
    )


class TestClassification(unittest.TestCase):
    def test_binary_with_history_is_chart(self):
        m = make_market(
            [Outcome("Yes", 0.6), Outcome("No", 0.4)],
            history=[
                HistoryPoint(datetime.now(timezone.utc), 0.5),
                HistoryPoint(datetime.now(timezone.utc), 0.6),
            ],
        )
        self.assertEqual(m.resolved_display_type(), DisplayType.CHART)

    def test_multi_outcome_is_table(self):
        m = make_market([Outcome("A", 0.5), Outcome("B", 0.3), Outcome("C", 0.2)])
        self.assertEqual(m.resolved_display_type(), DisplayType.TABLE)

    def test_forced_chart_override(self):
        m = make_market(
            [Outcome("A", 0.5), Outcome("B", 0.3), Outcome("C", 0.2)],
            display_type=DisplayType.CHART,
        )
        self.assertEqual(m.resolved_display_type(), DisplayType.CHART)

    def test_forced_table_override(self):
        m = make_market(
            [Outcome("Yes", 0.6), Outcome("No", 0.4)],
            history=[HistoryPoint(datetime.now(timezone.utc), 0.6)] * 5,
            display_type=DisplayType.TABLE,
        )
        self.assertEqual(m.resolved_display_type(), DisplayType.TABLE)

    def test_binary_case_insensitive_yes_no(self):
        m = make_market([Outcome("yes", 0.6), Outcome("NO", 0.4)])
        self.assertTrue(m.is_binary())

    def test_non_binary_names_not_binary(self):
        m = make_market([Outcome("Team A", 0.6), Outcome("Team B", 0.4)])
        self.assertFalse(m.is_binary())

    def test_sorted_outcomes_descending(self):
        m = make_market([Outcome("A", 0.1), Outcome("B", 0.7), Outcome("C", 0.2)])
        names = [o.name for o in m.sorted_outcomes()]
        self.assertEqual(names, ["B", "C", "A"])

    def test_current_probability_is_first_outcome(self):
        m = make_market([Outcome("Yes", 0.42), Outcome("No", 0.58)])
        self.assertAlmostEqual(m.current_probability(), 0.42)

    def test_empty_outcomes_no_probability(self):
        m = make_market([])
        self.assertIsNone(m.current_probability())
        self.assertFalse(m.is_binary())


if __name__ == "__main__":
    unittest.main()
