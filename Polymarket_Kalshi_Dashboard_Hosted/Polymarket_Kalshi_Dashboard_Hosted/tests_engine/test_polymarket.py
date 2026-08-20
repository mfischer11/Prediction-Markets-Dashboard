import os
import sys
import unittest

import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import polymarket
from src.cache import DiskCache
from src.market_parser import parse_market_url
from src.models import DisplayType, MarketStatus
from src.utils import build_session

BINARY_MARKET_OBJ = {
    "id": "239826",
    "question": "Will X happen by 2026?",
    "slug": "will-x-happen-by-2026",
    "description": "Resolves YES if X happens.",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.63", "0.37"]',
    "bestBid": 0.62,
    "bestAsk": 0.64,
    "clobTokenIds": '["1111111111111111111111", "2222222222222222222222"]',
    "volumeNum": 15000.5,
    "volume24hr": 320.0,
    "liquidityNum": 5000.0,
    "active": True,
    "closed": False,
    "startDate": "2026-01-01T00:00:00Z",
    "endDate": "2026-12-31T00:00:00Z",
    "updatedAt": "2026-08-19T10:00:00Z",
}

GROUP_EVENT_OBJ = {
    "id": "9999",
    "title": "Who will win the election?",
    "slug": "who-will-win-the-election",
    "description": "Multi-candidate election market.",
    "closed": False,
    "volume": 50000.0,
    "markets": [
        {
            "groupItemTitle": "Candidate A",
            "outcomePrices": '["0.55", "0.45"]',
            "volumeNum": 20000,
        },
        {
            "groupItemTitle": "Candidate B",
            "outcomePrices": '["0.30", "0.70"]',
            "volumeNum": 15000,
        },
        {
            "groupItemTitle": "Candidate C",
            "outcomePrices": '["0.15", "0.85"]',
            "volumeNum": 15000,
        },
    ],
}

# Mirrors the real "Lead Bank in Anthropic's IPO?" / election-winner style
# events: real, named outcomes alongside unfilled placeholder slots that
# Polymarket's own site never displays (active: false, liquidity: 0).
PLACEHOLDER_EVENT_OBJ = {
    "id": "8888",
    "title": "Lead bank in Anthropic's IPO?",
    "slug": "lead-bank-in-anthropics-ipo",
    "closed": False,
    "markets": [
        {"groupItemTitle": "Morgan Stanley", "active": True,
         "outcomePrices": '["0.68", "0.32"]', "volumeNum": 25000, "liquidityNum": 4000},
        {"groupItemTitle": "Goldman Sachs", "active": True,
         "outcomePrices": '["0.18", "0.82"]', "volumeNum": 8000, "liquidityNum": 1200},
        {"groupItemTitle": "Bank A", "active": False,
         "outcomePrices": '["0", "1"]', "volumeNum": 0, "liquidityNum": 0},
        {"groupItemTitle": "Bank B", "active": False,
         "outcomePrices": '["0", "1"]', "volumeNum": 0, "liquidityNum": 0},
        {"groupItemTitle": "Other", "active": False,
         "outcomePrices": '["0", "1"]', "volumeNum": 0, "liquidityNum": 0},
    ],
}

# Edge case: every sub-market is an inactive placeholder (shouldn't happen
# in practice, but the fallback must not produce an empty table).
ALL_PLACEHOLDER_EVENT_OBJ = {
    "id": "7776",
    "title": "Brand new event, no real entrants yet",
    "slug": "brand-new-event",
    "closed": False,
    "markets": [
        {"groupItemTitle": "Option A", "active": False,
         "outcomePrices": '["0", "1"]', "volumeNum": 0},
        {"groupItemTitle": "Option B", "active": False,
         "outcomePrices": '["0", "1"]', "volumeNum": 0},
    ],
}

ROLLING_EVENT_OBJ = {
    "id": "8888",
    "title": "Next model released by...?",
    "slug": "next-model-released-by",
    "description": "Rolling release-date market.",
    "closed": False,
    "volume": 90000.0,
    "markets": [
        {"groupItemTitle": "By June 2026", "closed": True,
         "outcomePrices": '["0.02", "0.98"]', "volumeNum": 5000},
        {"groupItemTitle": "By July 2026", "closed": True,
         "outcomePrices": '["0.01", "0.99"]', "volumeNum": 4000},
        {"groupItemTitle": "By August 2026", "closed": False,
         "outcomePrices": '["0.60", "0.40"]', "volumeNum": 8000},
        {"groupItemTitle": "By September 2026", "closed": False,
         "outcomePrices": '["0.20", "0.80"]', "volumeNum": 3000},
    ],
}

ALL_CLOSED_EVENT_OBJ = {
    "id": "7777",
    "title": "Fully concluded event",
    "slug": "fully-concluded-event",
    "closed": True,
    "markets": [
        {"groupItemTitle": "Outcome A", "closed": True,
         "outcomePrices": '["1.00", "0.00"]', "volumeNum": 1000},
        {"groupItemTitle": "Outcome B", "closed": True,
         "outcomePrices": '["0.00", "1.00"]', "volumeNum": 500},
    ],
}

PRICE_HISTORY_RESP = {
    "history": [
        {"t": 1755000000, "p": 0.55},
        {"t": 1755086400, "p": 0.60},
        {"t": 1755172800, "p": 0.63},
    ]
}


class TestPolymarketAdapter(unittest.TestCase):
    def setUp(self):
        self.session = build_session()
        cache_path = f"/tmp/test_pm_cache_{self._testMethodName}.json"
        if os.path.exists(cache_path):
            os.remove(cache_path)
        self.cache = DiskCache(cache_path)

    @responses.activate
    def test_binary_market_via_market_slug(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/markets/slug/will-x-happen-by-2026",
            json=BINARY_MARKET_OBJ,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://clob.polymarket.com/prices-history",
            json=PRICE_HISTORY_RESP,
            status=200,
        )
        parsed = parse_market_url(
            "https://polymarket.com/market/will-x-happen-by-2026"
        )
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        self.assertEqual(market.title, "Will X happen by 2026?")
        self.assertTrue(market.is_binary())
        self.assertAlmostEqual(market.current_probability(), 0.63)
        self.assertEqual(market.status, MarketStatus.ACTIVE)
        self.assertEqual(len(market.historical_series), 3)
        self.assertEqual(market.resolved_display_type(), DisplayType.CHART)
        self.assertAlmostEqual(market.outcomes[0].bid, 0.62)
        self.assertAlmostEqual(market.outcomes[0].ask, 0.64)
        # All PRICE_HISTORY_RESP points are far in the past relative to
        # "now" at test time, so both cutoffs land on the most recent of
        # them (probability 0.63) - same as the current probability,
        # giving a deterministic zero delta that still proves the wiring
        # (fetch -> compute -> populate) works end to end.
        self.assertIsNotNone(market.outcomes[0].change_24h)
        self.assertAlmostEqual(market.outcomes[0].change_24h, 0.0)
        self.assertIsNotNone(market.outcomes[0].change_7d)
        # No's change is the exact negative of Yes's.
        self.assertAlmostEqual(market.outcomes[1].change_24h, -market.outcomes[0].change_24h)

    @responses.activate
    def test_group_event_becomes_table(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/events/slug/who-will-win-the-election",
            json=GROUP_EVENT_OBJ,
            status=200,
        )
        parsed = parse_market_url(
            "https://polymarket.com/event/who-will-win-the-election"
        )
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        self.assertEqual(len(market.outcomes), 3)
        self.assertEqual(market.resolved_display_type(), DisplayType.TABLE)
        # Sorted descending by probability
        sorted_names = [o.name for o in market.sorted_outcomes()]
        self.assertEqual(sorted_names, ["Candidate A", "Candidate B", "Candidate C"])

    @responses.activate
    def test_group_event_outcomes_get_price_change(self):
        event_with_tokens = {
            "id": "5555",
            "title": "Who will win?",
            "slug": "who-will-win",
            "closed": False,
            "markets": [
                {
                    "groupItemTitle": "Candidate A",
                    "outcomePrices": '["0.60", "0.40"]',
                    "volumeNum": 5000,
                    "clobTokenIds": '["tokA1", "tokA2"]',
                },
                {
                    "groupItemTitle": "Candidate B",
                    "outcomePrices": '["0.40", "0.60"]',
                    "volumeNum": 3000,
                    "clobTokenIds": '["tokB1", "tokB2"]',
                },
            ],
        }
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/events/slug/who-will-win",
            json=event_with_tokens,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://clob.polymarket.com/prices-history",
            json=PRICE_HISTORY_RESP,
            status=200,
        )
        parsed = parse_market_url("https://polymarket.com/event/who-will-win")
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        for outcome in market.outcomes:
            self.assertIsNotNone(outcome.change_24h)
            self.assertIsNotNone(outcome.change_7d)

    @responses.activate
    def test_closed_market_detected(self):
        closed_obj = dict(BINARY_MARKET_OBJ, closed=True)
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/markets/slug/will-x-happen-by-2026",
            json=closed_obj,
            status=200,
        )
        parsed = parse_market_url("https://polymarket.com/market/will-x-happen-by-2026")
        market = polymarket.resolve_and_fetch(parsed, "CURRENT", self.session, self.cache)
        self.assertEqual(market.status, MarketStatus.CLOSED)

    @responses.activate
    def test_market_not_found_404(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/markets/slug/does-not-exist",
            json={"error": "not found"},
            status=404,
        )
        parsed = parse_market_url("https://polymarket.com/market/does-not-exist")
        with self.assertRaises(polymarket.MarketNotFound):
            polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

    @responses.activate
    def test_forced_table_override(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/markets/slug/will-x-happen-by-2026",
            json=BINARY_MARKET_OBJ,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://clob.polymarket.com/prices-history",
            json=PRICE_HISTORY_RESP,
            status=200,
        )
        parsed = parse_market_url("https://polymarket.com/market/will-x-happen-by-2026")
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)
        market.display_type = DisplayType.TABLE
        self.assertEqual(market.resolved_display_type(), DisplayType.TABLE)

    @responses.activate
    def test_rolling_event_excludes_closed_submarkets(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/events/slug/next-model-released-by",
            json=ROLLING_EVENT_OBJ,
            status=200,
        )
        parsed = parse_market_url("https://polymarket.com/event/next-model-released-by")
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        names = [o.name for o in market.outcomes]
        self.assertEqual(len(market.outcomes), 2)
        self.assertNotIn("By June 2026", names)
        self.assertNotIn("By July 2026", names)
        self.assertIn("By August 2026", names)
        self.assertIn("By September 2026", names)

    @responses.activate
    def test_fully_concluded_event_falls_back_to_all_submarkets(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/events/slug/fully-concluded-event",
            json=ALL_CLOSED_EVENT_OBJ,
            status=200,
        )
        parsed = parse_market_url("https://polymarket.com/event/fully-concluded-event")
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        # Every sub-market is closed, so nothing is filtered out - showing
        # the final resolved state beats showing an empty table.
        self.assertEqual(len(market.outcomes), 2)

    @responses.activate
    def test_placeholder_submarkets_excluded(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/events/slug/lead-bank-in-anthropics-ipo",
            json=PLACEHOLDER_EVENT_OBJ,
            status=200,
        )
        parsed = parse_market_url(
            "https://polymarket.com/event/lead-bank-in-anthropics-ipo"
        )
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        names = [o.name for o in market.outcomes]
        self.assertEqual(len(market.outcomes), 2)
        self.assertIn("Morgan Stanley", names)
        self.assertIn("Goldman Sachs", names)
        self.assertNotIn("Bank A", names)
        self.assertNotIn("Bank B", names)
        self.assertNotIn("Other", names)

    @responses.activate
    def test_all_placeholder_submarkets_falls_back_rather_than_empty(self):
        responses.add(
            responses.GET,
            "https://gamma-api.polymarket.com/events/slug/brand-new-event",
            json=ALL_PLACEHOLDER_EVENT_OBJ,
            status=200,
        )
        parsed = parse_market_url("https://polymarket.com/event/brand-new-event")
        market = polymarket.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        # No real entrants exist yet, so falling back to the placeholders
        # avoids showing an empty table.
        self.assertEqual(len(market.outcomes), 2)


if __name__ == "__main__":
    unittest.main()
