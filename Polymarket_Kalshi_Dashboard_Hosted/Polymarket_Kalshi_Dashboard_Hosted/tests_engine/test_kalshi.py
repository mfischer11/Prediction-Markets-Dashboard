import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import kalshi
from src.cache import DiskCache
from src.market_parser import parse_market_url
from src.models import DisplayType, MarketStatus
from src.utils import build_session

BASE = "https://api.elections.kalshi.com/trade-api/v2"

SINGLE_MARKET_EVENT = {
    "event": {
        "event_ticker": "KXHIGHNY-26AUG19",
        "title": "Highest temperature in NYC today?",
        "sub_title": "",
        "markets": [
            {
                "ticker": "KXHIGHNY-26AUG19-B85",
                "event_ticker": "KXHIGHNY-26AUG19",
                "status": "active",
                "last_price_dollars": "0.4200",
                "yes_bid_dollars": "0.4100",
                "yes_ask_dollars": "0.4300",
                "volume_fp": "1200.00",
                "volume_24h_fp": "300.00",
                "liquidity_dollars": "800.00",
                "open_interest_fp": "500.00",
                "open_time": "2026-08-19T00:00:00Z",
                "close_time": "2026-08-19T23:59:00Z",
                "updated_time": "2026-08-19T10:00:00Z",
            }
        ],
    }
}

MULTI_MARKET_EVENT = {
    "event": {
        "event_ticker": "KXPRES-26",
        "title": "Who will win the presidency?",
        "sub_title": "",
        "markets": [
            {
                "ticker": "KXPRES-26-A",
                "status": "active",
                "yes_sub_title": "Candidate A",
                "last_price_dollars": "0.5000",
                "volume_fp": "9000.00",
            },
            {
                "ticker": "KXPRES-26-B",
                "status": "active",
                "yes_sub_title": "Candidate B",
                "last_price_dollars": "0.3000",
                "volume_fp": "6000.00",
            },
        ],
    }
}

# Mirrors the real GTA 6 release-date event: several past-dated,
# closed/settled/deactivated thresholds alongside the currently-open one.
ROLLING_KALSHI_EVENT = {
    "event": {
        "event_ticker": "KXGTA6",
        "title": "GTA 6 release date",
        "sub_title": "",
        "markets": [
            {"ticker": "KXGTA6-JUL26", "status": "settled",
             "yes_sub_title": "Before July 2026", "last_price_dollars": "1.0000",
             "volume_fp": "500.00"},
            {"ticker": "KXGTA6-AUG26", "status": "closed",
             "yes_sub_title": "Before August 2026", "last_price_dollars": "1.0000",
             "volume_fp": "300.00"},
            {"ticker": "KXGTA6-OLD", "status": "deactivated",
             "yes_sub_title": "DEACTIVATED", "last_price_dollars": "0.0000",
             "volume_fp": "10.00"},
            {"ticker": "KXGTA6-NOV26", "status": "active",
             "yes_sub_title": "Before November 2026", "last_price_dollars": "0.6500",
             "volume_fp": "12000.00"},
        ],
    }
}

# Every sub-market in this event has already resolved - filtering should
# fall back to showing all of them rather than an empty table.
ALL_SETTLED_KALSHI_EVENT = {
    "event": {
        "event_ticker": "KXOLDEVENT",
        "title": "Fully concluded Kalshi event",
        "sub_title": "",
        "markets": [
            {"ticker": "KXOLDEVENT-A", "status": "settled",
             "yes_sub_title": "Outcome A", "last_price_dollars": "1.0000"},
            {"ticker": "KXOLDEVENT-B", "status": "settled",
             "yes_sub_title": "Outcome B", "last_price_dollars": "0.0000"},
        ],
    }
}

CANDLESTICK_RESP = {
    "markets": [
        {
            "market_ticker": "KXHIGHNY-26AUG19-B85",
            "candlesticks": [
                {"end_period_ts": 1755000000, "price": {"close_dollars": "0.40"}},
                {"end_period_ts": 1755086400, "price": {"close_dollars": "0.42"}},
            ],
        }
    ]
}

OPEN_MARKETS_SINGLE_EVENT_RESP = {
    "markets": [
        {"ticker": "KXHIGHNY-26AUG19-B85", "event_ticker": "KXHIGHNY-26AUG19",
         "status": "active"},
        {"ticker": "KXHIGHNY-26AUG19-B90", "event_ticker": "KXHIGHNY-26AUG19",
         "status": "active"},
    ]
}

OPEN_MARKETS_MULTI_EVENT_RESP = {
    "markets": [
        {"ticker": "KXHIGHNY-26AUG19-B85", "event_ticker": "KXHIGHNY-26AUG19",
         "status": "active"},
        {"ticker": "KXHIGHNY-26AUG20-B85", "event_ticker": "KXHIGHNY-26AUG20",
         "status": "active"},
    ]
}

OPEN_MARKETS_EMPTY_RESP = {"markets": []}

OPEN_MARKETS_UNRELATED_RESP = {
    # Simulates the observed Kalshi bug: the series_ticker filter returns
    # markets from a totally different (unrelated) series instead of an
    # empty list. These must be filtered out client-side.
    "markets": [
        {"ticker": "KXMVECROSSCATEGORY-SHARD1-ABC123-XYZ",
         "event_ticker": "KXMVECROSSCATEGORY-SHARD1-ABC123", "status": "active"},
        {"ticker": "KXMVECROSSCATEGORY-SHARD1-DEF456-XYZ",
         "event_ticker": "KXMVECROSSCATEGORY-SHARD1-DEF456", "status": "active"},
    ]
}


class TestKalshiAdapter(unittest.TestCase):
    def setUp(self):
        self.session = build_session()
        cache_path = f"/tmp/test_kalshi_cache_{self._testMethodName}.json"
        if os.path.exists(cache_path):
            os.remove(cache_path)
        self.cache = DiskCache(cache_path)

    @responses.activate
    def test_binary_market_via_explicit_ticker(self):
        responses.add(responses.GET, f"{BASE}/events/KXHIGHNY-26AUG19",
                       json=SINGLE_MARKET_EVENT, status=200)
        responses.add(responses.GET, f"{BASE}/markets/candlesticks",
                       json=CANDLESTICK_RESP, status=200)

        parsed = parse_market_url("https://kalshi.com/events/KXHIGHNY-26AUG19")
        market = kalshi.resolve_and_fetch(parsed, "7D", self.session, self.cache)

        self.assertTrue(market.is_binary())
        self.assertAlmostEqual(market.current_probability(), 0.42)
        self.assertEqual(market.status, MarketStatus.ACTIVE)
        self.assertEqual(market.resolved_display_type(), DisplayType.CHART)
        self.assertEqual(len(market.historical_series), 2)
        # Both CANDLESTICK_RESP points are far in the past relative to
        # "now" at test time, so the cutoff lands on the most recent of
        # them (0.42) - same as current probability, giving a
        # deterministic zero delta that still proves the wiring works.
        self.assertIsNotNone(market.outcomes[0].change_24h)
        self.assertAlmostEqual(market.outcomes[0].change_24h, 0.0)
        self.assertIsNotNone(market.outcomes[0].change_7d)
        self.assertAlmostEqual(market.outcomes[1].change_24h, -market.outcomes[0].change_24h)

    @responses.activate
    def test_7d_change_uses_wider_window_than_exactly_7d(self):
        # Reproduces the real bug: a request scoped to *exactly* 7 days
        # back returns candles whose oldest point lands just inside that
        # boundary (discrete candle bucketing), never at-or-before it, so
        # a naive "fetch 7D, look for a point <= 168h ago" approach always
        # finds nothing. This mocks that precisely: the "narrow" request
        # (period_interval matching a 7D bucket) gets only an
        # inside-the-boundary point; the "wide" request (30D bucket) gets
        # a genuinely-old point too. The fix must be requesting the wide
        # window, not the narrow one, for this to resolve to a real value.
        responses.add(responses.GET, f"{BASE}/events/KXHIGHNY-26AUG19",
                       json=SINGLE_MARKET_EVENT, status=200)

        now = datetime.now(timezone.utc)
        just_inside_7d = now - timedelta(hours=167, minutes=30)  # 167.5h - inside 168h cutoff
        genuinely_10d_old = now - timedelta(days=10)

        def candlestick_callback(request):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(request.url).query)
            period_interval = qs.get("period_interval", [""])[0]
            if period_interval == "240":  # 30D bucket's period_interval
                points = [genuinely_10d_old, now - timedelta(hours=1)]
            else:  # 7D bucket (period_interval=60) or anything narrower
                points = [just_inside_7d, now - timedelta(hours=1)]
            candles = [
                {"end_period_ts": int(t.timestamp()), "price": {"close_dollars": "0.50"}}
                for t in points
            ]
            body = {"markets": [{"market_ticker": "KXHIGHNY-26AUG19-B85",
                                  "candlesticks": candles}]}
            return (200, {}, __import__("json").dumps(body))

        responses.add_callback(responses.GET, f"{BASE}/markets/candlesticks",
                                callback=candlestick_callback,
                                content_type="application/json")

        parsed = parse_market_url("https://kalshi.com/events/KXHIGHNY-26AUG19")
        market = kalshi.resolve_and_fetch(parsed, "7D", self.session, self.cache)

        # If the code were still requesting a literal 7D-only window for
        # the change calculation, this would be None (the real bug).
        self.assertIsNotNone(market.outcomes[0].change_7d)

    @responses.activate
    def test_multi_market_event_becomes_table(self):
        responses.add(responses.GET, f"{BASE}/events/KXPRES-26",
                       json=MULTI_MARKET_EVENT, status=200)
        candlesticks_for_both = {
            "markets": [
                {"market_ticker": "KXPRES-26-A", "candlesticks": [
                    {"end_period_ts": 1755000000, "price": {"close_dollars": "0.45"}},
                    {"end_period_ts": 1755086400, "price": {"close_dollars": "0.50"}},
                ]},
                {"market_ticker": "KXPRES-26-B", "candlesticks": [
                    {"end_period_ts": 1755000000, "price": {"close_dollars": "0.25"}},
                    {"end_period_ts": 1755086400, "price": {"close_dollars": "0.30"}},
                ]},
            ]
        }
        responses.add(responses.GET, f"{BASE}/markets/candlesticks",
                       json=candlesticks_for_both, status=200)

        parsed = parse_market_url("https://kalshi.com/events/KXPRES-26")
        market = kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        self.assertEqual(len(market.outcomes), 2)
        self.assertEqual(market.resolved_display_type(), DisplayType.TABLE)
        top = market.sorted_outcomes()[0]
        self.assertEqual(top.name, "Candidate A")
        # Table markets don't otherwise fetch history - confirm each
        # surviving outcome got its own change computed.
        for outcome in market.outcomes:
            self.assertIsNotNone(outcome.change_24h)
            self.assertIsNotNone(outcome.change_7d)

    @responses.activate
    def test_rolling_kalshi_event_excludes_stale_submarkets(self):
        responses.add(responses.GET, f"{BASE}/events/KXGTA6",
                       json=ROLLING_KALSHI_EVENT, status=200)

        parsed = parse_market_url("https://kalshi.com/events/KXGTA6")
        market = kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        names = [o.name for o in market.outcomes]
        self.assertEqual(len(market.outcomes), 1)
        self.assertNotIn("Before July 2026", names)
        self.assertNotIn("Before August 2026", names)
        self.assertNotIn("DEACTIVATED", names)
        self.assertIn("Before November 2026", names)

    @responses.activate
    def test_fully_settled_kalshi_event_falls_back_to_all_submarkets(self):
        responses.add(responses.GET, f"{BASE}/events/KXOLDEVENT",
                       json=ALL_SETTLED_KALSHI_EVENT, status=200)

        parsed = parse_market_url("https://kalshi.com/events/KXOLDEVENT")
        market = kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)

        # Every sub-market is settled, so nothing is filtered out - showing
        # the final resolved state beats showing an empty table.
        self.assertEqual(len(market.outcomes), 2)

    @responses.activate
    def test_slug_resolution_uses_the_one_currently_open_event(self):
        # This is the common daily-recurring-market case: no title matching
        # needed at all - there's exactly one open event in the series.
        responses.add(responses.GET, f"{BASE}/markets",
                       json=OPEN_MARKETS_SINGLE_EVENT_RESP, status=200)
        responses.add(responses.GET, f"{BASE}/events/KXHIGHNY-26AUG19",
                       json=SINGLE_MARKET_EVENT, status=200)
        responses.add(responses.GET, f"{BASE}/markets/candlesticks",
                       json=CANDLESTICK_RESP, status=200)

        parsed = parse_market_url(
            "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc-today"
        )
        market = kalshi.resolve_and_fetch(parsed, "CURRENT", self.session, self.cache)
        self.assertEqual(market.title, "Highest temperature in NYC today?")

    @responses.activate
    def test_slug_disambiguates_between_multiple_open_events_by_title(self):
        responses.add(responses.GET, f"{BASE}/markets",
                       json=OPEN_MARKETS_MULTI_EVENT_RESP, status=200)
        responses.add(responses.GET, f"{BASE}/events/KXHIGHNY-26AUG19",
                       json=SINGLE_MARKET_EVENT, status=200)
        responses.add(responses.GET, f"{BASE}/events/KXHIGHNY-26AUG20",
                       json={"event": {"event_ticker": "KXHIGHNY-26AUG20",
                                        "title": "Highest temperature in NYC tomorrow?",
                                        "markets": []}}, status=200)
        responses.add(responses.GET, f"{BASE}/markets/candlesticks",
                       json=CANDLESTICK_RESP, status=200)

        parsed = parse_market_url(
            "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc-today"
        )
        market = kalshi.resolve_and_fetch(parsed, "CURRENT", self.session, self.cache)
        self.assertEqual(market.title, "Highest temperature in NYC today?")

    @responses.activate
    def test_url_with_trailing_ticker_segment_used_directly(self):
        # /markets/{series}/{slug}/{ticker} - the last segment IS the real
        # ticker, so this should never touch /markets?series_ticker=...
        responses.add(responses.GET, f"{BASE}/events/KXH200MS-26AUG",
                       json={"event": {"event_ticker": "KXH200MS-26AUG",
                                        "title": "H200 monthly",
                                        "markets": [{
                                            "ticker": "KXH200MS-26AUG-T5",
                                            "status": "active",
                                            "last_price_dollars": "0.6000",
                                        }]}}, status=200)
        responses.add(responses.GET, f"{BASE}/markets/candlesticks",
                       json={"markets": []}, status=200)

        parsed = parse_market_url(
            "https://kalshi.com/markets/kxh200ms/h200-monthly/kxh200ms-26aug"
        )
        self.assertEqual(parsed.override_ticker, "KXH200MS-26AUG")
        market = kalshi.resolve_and_fetch(parsed, "CURRENT", self.session, self.cache)
        self.assertEqual(market.title, "H200 monthly")

    @responses.activate
    def test_guessed_ticker_404_falls_back_to_slug_resolution(self):
        # The URL-derived ticker guess doesn't exist (stale URL, wrong
        # guess, etc.) - should transparently fall back to the series/slug
        # lookup instead of failing outright.
        responses.add(responses.GET, f"{BASE}/events/KXH200MS-26AUG",
                       json={}, status=404)
        responses.add(responses.GET, f"{BASE}/markets",
                       json={"markets": [
                           {"ticker": "KXH200MS-26SEP-T5",
                            "event_ticker": "KXH200MS-26SEP", "status": "active"},
                       ]}, status=200)
        responses.add(responses.GET, f"{BASE}/events/KXH200MS-26SEP",
                       json={"event": {"event_ticker": "KXH200MS-26SEP",
                                        "title": "H200 monthly (September)",
                                        "markets": [{
                                            "ticker": "KXH200MS-26SEP-T5",
                                            "status": "active",
                                            "last_price_dollars": "0.5500",
                                        }]}}, status=200)
        responses.add(responses.GET, f"{BASE}/markets/candlesticks",
                       json={"markets": []}, status=200)

        parsed = parse_market_url(
            "https://kalshi.com/markets/kxh200ms/h200-monthly/kxh200ms-26aug"
        )
        market = kalshi.resolve_and_fetch(parsed, "CURRENT", self.session, self.cache)
        self.assertEqual(market.title, "H200 monthly (September)")

    @responses.activate
    def test_explicit_notes_ticker_404_does_not_fall_back(self):
        # An explicit ticker= override from Notes is a deliberate pin -
        # if it 404s, that should surface as a clear error, not silently
        # resolve to some other event.
        responses.add(responses.GET, f"{BASE}/events/KXWRONG-99",
                       json={}, status=404)
        parsed = parse_market_url(
            "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc-today",
            notes="ticker=KXWRONG-99",
        )
        with self.assertRaises(kalshi.MarketNotFound):
            kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)

    @responses.activate
    def test_event_not_found_404(self):
        responses.add(responses.GET, f"{BASE}/events/NOPE-99", json={}, status=404)
        parsed = parse_market_url("https://kalshi.com/events/NOPE-99")
        with self.assertRaises(kalshi.MarketNotFound):
            kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)

    @responses.activate
    def test_no_open_markets_in_series_raises_with_helpful_message(self):
        responses.add(responses.GET, f"{BASE}/markets",
                       json=OPEN_MARKETS_EMPTY_RESP, status=200)
        parsed = parse_market_url(
            "https://kalshi.com/markets/kxhighny/some-slug-that-doesnt-exist"
        )
        with self.assertRaises(kalshi.MarketNotFound) as ctx:
            kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)
        self.assertIn("ticker=", str(ctx.exception))

    @responses.activate
    def test_unrelated_markets_from_series_filter_bug_are_ignored(self):
        # Kalshi's series_ticker filter has been observed to return
        # markets from a completely different series instead of an empty
        # result. Those must never be mistaken for a real match.
        responses.add(responses.GET, f"{BASE}/markets",
                       json=OPEN_MARKETS_UNRELATED_RESP, status=200)
        parsed = parse_market_url(
            "https://kalshi.com/markets/kxh200ms/h200-monthly/kxh200ms-26aug"
        )
        # Ticker guess also fails, forcing the slug-based fallback.
        responses.add(responses.GET, f"{BASE}/events/KXH200MS-26AUG",
                       json={}, status=404)
        with self.assertRaises(kalshi.MarketNotFound) as ctx:
            kalshi.resolve_and_fetch(parsed, "30D", self.session, self.cache)
        self.assertIn("ticker=", str(ctx.exception))
        self.assertNotIn("KXMVECROSSCATEGORY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
