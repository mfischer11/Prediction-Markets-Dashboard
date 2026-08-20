import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.market_parser import UrlParseError, parse_market_url, parse_notes_overrides
from src.models import Platform


class TestUrlParsing(unittest.TestCase):
    def test_polymarket_event_url(self):
        p = parse_market_url("https://polymarket.com/event/will-the-fed-cut-rates-in-2026")
        self.assertEqual(p.platform, Platform.POLYMARKET)
        self.assertEqual(p.event_slug, "will-the-fed-cut-rates-in-2026")
        self.assertIsNone(p.market_slug)

    def test_polymarket_event_with_market_slug(self):
        p = parse_market_url(
            "https://polymarket.com/event/election-winner-2026/will-candidate-x-win"
        )
        self.assertEqual(p.event_slug, "election-winner-2026")
        self.assertEqual(p.market_slug, "will-candidate-x-win")

    def test_polymarket_market_url(self):
        p = parse_market_url("https://polymarket.com/market/some-market-slug")
        self.assertEqual(p.market_slug, "some-market-slug")
        self.assertIsNone(p.event_slug)

    def test_kalshi_markets_url(self):
        p = parse_market_url(
            "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc-today"
        )
        self.assertEqual(p.platform, Platform.KALSHI)
        self.assertEqual(p.series_ticker, "KXHIGHNY")
        self.assertEqual(p.url_slug, "highest-temperature-in-nyc-today")
        self.assertIsNone(p.override_ticker)

    def test_kalshi_markets_url_with_trailing_ticker(self):
        p = parse_market_url(
            "https://kalshi.com/markets/kxh200ms/h200-monthly/kxh200ms-26aug"
        )
        self.assertEqual(p.series_ticker, "KXH200MS")
        self.assertEqual(p.url_slug, "h200-monthly")
        self.assertEqual(p.override_ticker, "KXH200MS-26AUG")

    def test_kalshi_events_url(self):
        p = parse_market_url("https://kalshi.com/events/KXHIGHNY-25JUN01")
        self.assertEqual(p.override_ticker, "KXHIGHNY-25JUN01")

    def test_invalid_url_raises(self):
        with self.assertRaises(UrlParseError):
            parse_market_url("not a url")

    def test_unsupported_host_raises(self):
        with self.assertRaises(UrlParseError):
            parse_market_url("https://example.com/market/abc")

    def test_empty_url_raises(self):
        with self.assertRaises(UrlParseError):
            parse_market_url("")

    def test_kalshi_series_page_only_raises(self):
        with self.assertRaises(UrlParseError):
            parse_market_url("https://kalshi.com/markets/kxhighny")

    def test_notes_override_ticker(self):
        overrides = parse_notes_overrides("some note; ticker=KXHIGHNY-25JUN01")
        self.assertEqual(overrides["ticker"], "KXHIGHNY-25JUN01")

    def test_notes_override_market_id(self):
        overrides = parse_notes_overrides("market_id=12345")
        self.assertEqual(overrides["market_id"], "12345")
    def test_notes_no_overrides(self):
        self.assertEqual(parse_notes_overrides(""), {})
        self.assertEqual(parse_notes_overrides("just a plain note"), {})

    def test_polymarket_override_carried_from_notes(self):
        p = parse_market_url(
            "https://polymarket.com/event/some-event", notes="market_id=99999"
        )
        self.assertEqual(p.override_market_id, "99999")


if __name__ == "__main__":
    unittest.main()
