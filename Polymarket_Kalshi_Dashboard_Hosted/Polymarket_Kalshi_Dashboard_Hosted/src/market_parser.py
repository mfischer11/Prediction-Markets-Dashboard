"""
Centralizes all URL parsing so that if Polymarket or Kalshi change their
website URL structure, this is the only file that needs fixing.

Design notes
------------
Polymarket URLs embed a human-readable *slug* that maps 1:1 onto the Gamma
API's slug lookup endpoints (`/events/slug/{slug}` and `/markets/slug/{slug}`),
so resolution is exact and requires no guessing.

Kalshi URLs embed the series ticker directly (e.g. "kxhighny" in
`kalshi.com/markets/kxhighny/...`) but the trailing slug is a human title,
not the event ticker Kalshi's API expects. There is no public "resolve a URL
slug to an event ticker" endpoint, so we resolve by listing events in that
series and matching on a normalized title. This is best-effort: for the rare
case a title changes or two events collide, the user can force an exact
match by putting `ticker=EVENT_TICKER` (or `market=MARKET_TICKER`) in the
CONFIG sheet's Notes column, which always wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .models import Platform


@dataclass
class ParsedUrl:
    platform: Platform
    raw_url: str
    # Polymarket
    event_slug: Optional[str] = None
    market_slug: Optional[str] = None
    # Kalshi
    series_ticker: Optional[str] = None
    url_slug: Optional[str] = None
    # Manual overrides (parsed out of the Notes column, not the URL)
    override_ticker: Optional[str] = None
    override_market_ticker: Optional[str] = None
    override_market_id: Optional[str] = None
    override_event_id: Optional[str] = None
    # True when override_ticker was auto-derived from the URL's own path
    # (e.g. a trailing "/kxh200ms-26aug" segment) rather than typed by the
    # user into Notes. A guessed ticker that turns out wrong should fall
    # back to slug-based resolution instead of failing outright; an
    # explicit user override should not.
    event_ticker_is_guess: bool = False


class UrlParseError(ValueError):
    pass


_NOTE_OVERRIDE_RE = re.compile(
    r"(ticker|market|market_id|event_id)\s*=\s*([A-Za-z0-9_\-\.]+)", re.IGNORECASE
)


def parse_notes_overrides(notes: str) -> dict:
    """Extract key=value manual-override hints from the CONFIG Notes column.
    Supported keys: ticker=, market=, market_id=, event_id=
    """
    overrides = {}
    if not notes:
        return overrides
    for key, value in _NOTE_OVERRIDE_RE.findall(notes):
        overrides[key.lower()] = value
    return overrides


def identify_platform(url: str) -> Optional[Platform]:
    host = (urlparse(url).netloc or "").lower()
    if "polymarket.com" in host:
        return Platform.POLYMARKET
    if "kalshi.com" in host:
        return Platform.KALSHI
    return None


def parse_market_url(url: str, notes: str = "") -> ParsedUrl:
    url = (url or "").strip()
    if not url:
        raise UrlParseError("Empty URL")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UrlParseError(f"Not a valid URL: {url!r}")

    platform = identify_platform(url)
    if platform is None:
        raise UrlParseError(
            f"Unsupported URL host {parsed.netloc!r}; only polymarket.com "
            f"and kalshi.com links are supported."
        )

    overrides = parse_notes_overrides(notes)
    segments = [seg for seg in parsed.path.split("/") if seg]

    if platform == Platform.POLYMARKET:
        return _parse_polymarket(url, segments, overrides)
    return _parse_kalshi(url, segments, overrides)


def _parse_polymarket(url: str, segments: list, overrides: dict) -> ParsedUrl:
    # Expected shapes:
    #   /event/{event-slug}
    #   /event/{event-slug}/{market-slug}
    #   /market/{market-slug}   (legacy / direct market link)
    result = ParsedUrl(platform=Platform.POLYMARKET, raw_url=url)
    result.override_market_id = overrides.get("market_id")
    result.override_event_id = overrides.get("event_id")
    result.override_market_ticker = overrides.get("market") or overrides.get("ticker")

    if not segments:
        raise UrlParseError(f"Could not find a market/event slug in {url!r}")

    if segments[0] == "event" and len(segments) >= 2:
        result.event_slug = segments[1]
        if len(segments) >= 3:
            result.market_slug = segments[2]
    elif segments[0] == "market" and len(segments) >= 2:
        result.market_slug = segments[1]
    else:
        # Fall back to treating the final path segment as a slug guess.
        result.event_slug = segments[-1]

    return result


def _parse_kalshi(url: str, segments: list, overrides: dict) -> ParsedUrl:
    # Expected shapes:
    #   /markets/{series-ticker-lower}/{event-slug}
    #   /markets/{series-ticker-lower}/{event-slug}/{market-slug}
    #   /events/{EVENT_TICKER}
    result = ParsedUrl(platform=Platform.KALSHI, raw_url=url)
    result.override_ticker = overrides.get("ticker")
    result.override_market_ticker = overrides.get("market")

    if not segments:
        raise UrlParseError(f"Could not find a market/event path in {url!r}")

    if segments[0] == "events" and len(segments) >= 2:
        # Sometimes Kalshi links directly by event ticker already.
        result.override_ticker = result.override_ticker or segments[1].upper()
        result.url_slug = segments[1]
        return result

    if segments[0] == "markets" and len(segments) >= 4:
        # Kalshi's own market-detail URLs append the actual ticker as a
        # trailing path segment, e.g.
        #   /markets/kxh200ms/h200-monthly/kxh200ms-26aug
        # where "kxh200ms-26aug" -> event ticker KXH200MS-26AUG. Use it
        # directly when present - this is usually far more reliable than
        # matching on the human-readable slug in segment [2], but it's
        # still a guess (not a guaranteed-correct API lookup key), so it's
        # flagged for a slug-based fallback if it turns out wrong.
        result.series_ticker = segments[1].upper()
        result.url_slug = segments[2]
        if not result.override_ticker:
            result.override_ticker = segments[3].upper()
            result.event_ticker_is_guess = True
        return result

    if segments[0] == "markets" and len(segments) == 3:
        result.series_ticker = segments[1].upper()
        result.url_slug = segments[2]
        return result

    if segments[0] == "markets" and len(segments) == 2:
        # /markets/{series-ticker} with no event slug - series page, not
        # a single market; not resolvable to one market.
        raise UrlParseError(
            f"URL {url!r} points at a series page, not a single market/event. "
            f"Link directly to the market instead."
        )

    raise UrlParseError(f"Unrecognized Kalshi URL shape: {url!r}")
