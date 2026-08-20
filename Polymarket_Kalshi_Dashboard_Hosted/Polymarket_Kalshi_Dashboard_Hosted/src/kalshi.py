"""
Kalshi adapter.

Data source (public, no API key required for reads):
  https://api.elections.kalshi.com/trade-api/v2
    (despite the "elections" subdomain, this serves ALL Kalshi markets -
    per Kalshi's own quick-start docs)

Verified live against the real endpoint during development (Aug 2026):
  GET /markets?limit=3&status=open
  GET /events/{event_ticker}?with_nested_markets=true
  GET /markets/candlesticks?market_tickers=...&start_ts=...&end_ts=...&period_interval=...
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

from .cache import DiskCache
from .market_parser import ParsedUrl
from .models import HistoryPoint, Market, MarketStatus, Outcome, Platform
from .utils import (
    apply_price_changes,
    http_get_json,
    parse_iso8601,
    resolve_auto_time_range,
    safe_float,
)

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

EVENT_LIST_CACHE_TTL = 3600  # 1 hour

# Used specifically when fetching history to compute a 7d price change.
# Requesting a window that's exactly 7 days wide leaves no margin: the
# oldest candle returned almost always lands just *inside* that boundary
# (start_ts itself, due to discrete candle bucketing), never exactly
# at-or-before it, so compute_price_change's "at or before 168h ago"
# lookup silently fails and returns None every time. A comfortably wider
# window (30D) always has a genuine data point past the 7-day mark
# whenever the market is actually old enough to have one.
CHANGE_FETCH_RANGE = "30D"


class KalshiError(Exception):
    pass


class MarketNotFound(KalshiError):
    pass


class AmbiguousMarket(KalshiError):
    pass


_STATUS_MAP = {
    "active": MarketStatus.ACTIVE,
    "initialized": MarketStatus.ACTIVE,
    "open": MarketStatus.ACTIVE,
    "closed": MarketStatus.CLOSED,
    "deactivated": MarketStatus.CLOSED,
    "settled": MarketStatus.SETTLED,
    "determined": MarketStatus.SETTLED,
}

_OPEN_STATUSES = {"active", "initialized", "open"}


def _is_open_status(status_value) -> bool:
    return (status_value or "").lower() in _OPEN_STATUSES


def _normalize_slug(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _time_range_to_candle_params(time_range: str) -> Optional[dict]:
    """Map CONFIG 'Time Range' onto /markets/candlesticks params
    (start_ts/end_ts in unix seconds, period_interval in minutes)."""
    tr = (time_range or "30D").upper()
    now = datetime.now(timezone.utc)
    windows = {
        "24H": (timedelta(hours=24), 15),
        "7D": (timedelta(days=7), 60),
        "30D": (timedelta(days=30), 240),
        "90D": (timedelta(days=90), 720),
        "ALL": (timedelta(days=730), 1440),
        "CURRENT": None,
    }
    entry = windows.get(tr, windows["30D"])
    if entry is None:
        return None
    delta, period_minutes = entry
    return {
        "start_ts": int((now - delta).timestamp()),
        "end_ts": int(now.timestamp()),
        "period_interval": period_minutes,
    }


def _fetch_candlesticks(
    session: requests.Session,
    market_ticker: str,
    time_range: str,
    logger: Optional[logging.Logger] = None,
) -> List[HistoryPoint]:
    params = _time_range_to_candle_params(time_range)
    if params is None:
        return []
    params["market_tickers"] = market_ticker
    try:
        data = http_get_json(
            session, f"{API_BASE}/markets/candlesticks", params=params, logger=logger
        )
    except (requests.RequestException, ValueError) as exc:
        if logger:
            logger.warning("Candlestick fetch failed for %s: %s", market_ticker, exc)
        return []

    points: List[HistoryPoint] = []
    for market_block in data.get("markets", []):
        if market_block.get("market_ticker") != market_ticker:
            continue
        for candle in market_block.get("candlesticks", []):
            ts = candle.get("end_period_ts")
            price_block = candle.get("price") or {}
            close = price_block.get("close_dollars")
            if close is None:
                close = price_block.get("mean_dollars")
            if ts is None or close is None:
                continue
            prob = safe_float(close)
            if prob is None:
                continue
            points.append(HistoryPoint(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                probability=prob,
            ))
    return points


def _market_yes_probability(market_obj: dict) -> Optional[float]:
    last = safe_float(market_obj.get("last_price_dollars"))
    if last:
        return last
    bid = safe_float(market_obj.get("yes_bid_dollars"))
    ask = safe_float(market_obj.get("yes_ask_dollars"))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4)
    return bid if bid is not None else ask


def _single_market_to_market(market_obj: dict, event_title: str, url: str) -> Market:
    status = _STATUS_MAP.get((market_obj.get("status") or "").lower(), MarketStatus.ACTIVE)
    yes_prob = _market_yes_probability(market_obj)
    yes_bid = safe_float(market_obj.get("yes_bid_dollars"))
    yes_ask = safe_float(market_obj.get("yes_ask_dollars"))
    outcomes = [
        Outcome(name="Yes", probability=yes_prob, bid=yes_bid, ask=yes_ask),
        Outcome(name="No", probability=(1 - yes_prob) if yes_prob is not None else None),
    ]
    m = Market(
        platform=Platform.KALSHI,
        url=url,
        market_id=market_obj.get("ticker", ""),
        event_id=market_obj.get("event_ticker", ""),
        title=event_title or market_obj.get("title") or market_obj.get("ticker", ""),
        description="",
        status=status,
        start_time=parse_iso8601(market_obj.get("open_time")),
        end_time=parse_iso8601(market_obj.get("close_time")),
        outcomes=outcomes,
        volume=safe_float(market_obj.get("volume_fp")),
        volume_24hr=safe_float(market_obj.get("volume_24h_fp")),
        liquidity=safe_float(market_obj.get("liquidity_dollars")),
        open_interest=safe_float(market_obj.get("open_interest_fp")),
        last_updated=parse_iso8601(market_obj.get("updated_time")),
    )
    return m


def _multi_market_event_to_market(
    event_obj: dict,
    markets: List[dict],
    url: str,
    session: requests.Session,
    logger: Optional[logging.Logger] = None,
) -> Market:
    # Kalshi keeps every past sub-market (e.g. one per "before <date>"
    # threshold, or a since-deactivated strike) in an event's markets list
    # indefinitely, even long after they've closed/settled/been
    # deactivated. Left unfiltered, that means a growing pile of stale
    # rows ("Before July 2026", "DEACTIVATED", ...) cluttering the table.
    # Mirrors the same fix already applied to Polymarket's rolling event
    # groups: show only currently-open sub-markets, falling back to the
    # full list only if the whole event has concluded (so a fully
    # resolved market still shows its final state instead of nothing).
    open_markets = [mk for mk in markets if _is_open_status(mk.get("status"))]
    display_markets = open_markets if open_markets else markets

    any_active = bool(open_markets)
    outcomes = []
    for mk in display_markets:
        name = mk.get("yes_sub_title") or mk.get("title") or mk.get("ticker", "?")
        prob = _market_yes_probability(mk)
        outcome = Outcome(
            name=name,
            probability=prob,
            volume=safe_float(mk.get("volume_fp")),
            bid=safe_float(mk.get("yes_bid_dollars")),
            ask=safe_float(mk.get("yes_ask_dollars")),
        )
        # 24h/7d price change: table markets don't otherwise fetch any
        # history, so this is one extra candlestick call per surviving
        # row - only for markets still open, matching the Polymarket
        # adapter's equivalent table-change fetch. Uses CHANGE_FETCH_RANGE
        # (30D), not a literal 7D window - see that constant's docstring
        # for why an exact-width window silently breaks the 7d delta.
        if mk.get("status") in ("active", "initialized", "open") and mk.get("ticker"):
            history = _fetch_candlesticks(session, mk["ticker"], CHANGE_FETCH_RANGE, logger)
            apply_price_changes(outcome, history)
        outcomes.append(outcome)
    total_volume = sum((o.volume or 0) for o in outcomes) or None

    m = Market(
        platform=Platform.KALSHI,
        url=url,
        market_id="",
        event_id=event_obj.get("event_ticker", ""),
        title=event_obj.get("title", ""),
        description=event_obj.get("sub_title", "") or "",
        status=MarketStatus.ACTIVE if any_active else MarketStatus.CLOSED,
        outcomes=outcomes,
        volume=total_volume,
    )
    return m


def _get_event(session, event_ticker: str, logger) -> dict:
    try:
        data = http_get_json(
            session,
            f"{API_BASE}/events/{event_ticker}",
            params={"with_nested_markets": "true"},
            logger=logger,
        )
        return data.get("event", data)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise MarketNotFound(f"No Kalshi event found for ticker {event_ticker!r}") from exc
        raise


def _get_market(session, market_ticker: str, logger) -> dict:
    try:
        data = http_get_json(session, f"{API_BASE}/markets/{market_ticker}", logger=logger)
        return data.get("market", data)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise MarketNotFound(f"No Kalshi market found for ticker {market_ticker!r}") from exc
        raise


def _find_event_ticker_by_slug(
    session: requests.Session,
    series_ticker: str,
    url_slug: str,
    cache: DiskCache,
    logger: Optional[logging.Logger],
) -> str:
    """Resolves a series (e.g. 'kxhighny') to the event ticker the URL's
    human-readable slug most likely means.

    For daily/recurring series like weather markets, "today's version" is
    unambiguous: there's exactly one currently-open event, so we query
    GET /markets?series_ticker=X&status=open (documented, reliable) and use
    whichever event that market belongs to - no text matching involved.

    If a series happens to have more than one open event at once, we
    narrow down using the URL slug against each candidate event's title,
    which is now a much smaller and more reliable comparison than matching
    against the series' entire history.

    Observed in practice: Kalshi's series_ticker filter on this endpoint
    can silently return unrelated markets (e.g. multivariate combo
    markets) instead of an empty list when a series has no current
    matches, rather than actually filtering. Trusting that unfiltered
    result would resolve to a completely wrong market, so every returned
    market's own event_ticker is double-checked client-side against the
    requested series prefix before being used."""
    cache_key = f"kalshi:series_open_markets:{series_ticker}"
    event_tickers = cache.get(cache_key, max_age_seconds=EVENT_LIST_CACHE_TTL)
    if event_tickers is None:
        try:
            data = http_get_json(
                session,
                f"{API_BASE}/markets",
                params={"series_ticker": series_ticker, "status": "open", "limit": 200},
                logger=logger,
            )
            markets = data.get("markets", [])
        except requests.HTTPError:
            markets = []
        # Preserve discovery order but de-duplicate, and only trust
        # markets whose event_ticker actually belongs to this series -
        # see docstring above for why this check exists.
        series_prefix = f"{series_ticker.upper()}-"
        seen = set()
        event_tickers = []
        for mk in markets:
            et = mk.get("event_ticker")
            if et and et.upper().startswith(series_prefix) and et not in seen:
                seen.add(et)
                event_tickers.append(et)
        cache.set(cache_key, event_tickers)
        cache.save()

    if not event_tickers:
        raise MarketNotFound(
            f"No currently open markets found in series {series_ticker!r}. "
            f"The market may have closed or not opened yet today. Add "
            f"'ticker=EVENT_TICKER' to the Notes column to specify one "
            f"manually if you know it."
        )

    if len(event_tickers) == 1:
        return event_tickers[0]

    # More than one open event in this series right now - narrow down by
    # matching the URL's slug against each candidate's title.
    target = _normalize_slug(url_slug)
    best = None
    for et in event_tickers:
        try:
            ev = _get_event(session, et, logger)
        except MarketNotFound:
            continue
        title_norm = _normalize_slug(ev.get("title", ""))
        if title_norm == target:
            return et
        if target and (target in title_norm or title_norm in target):
            best = best or et

    if best:
        return best
    raise MarketNotFound(
        f"Series {series_ticker!r} has {len(event_tickers)} open events right "
        f"now and none clearly matches {url_slug!r}. Add "
        f"'ticker=EVENT_TICKER' to the Notes column to specify which one."
    )


def _apply_changes_for_single_market(m: Market, ticker: str, effective_range: str,
                                      session: requests.Session,
                                      logger: Optional[logging.Logger]) -> None:
    """The chart displays at effective_range, which can be as narrow as
    24H/7D for a young market - too tight a margin to reliably compute a
    7d change (see CHANGE_FETCH_RANGE). Re-fetch at a wider range just for
    that calculation when needed, rather than starving the chart's own
    display."""
    if not m.outcomes:
        return
    change_points = m.historical_series
    if effective_range.upper() in ("24H", "7D"):
        change_points = _fetch_candlesticks(session, ticker, CHANGE_FETCH_RANGE, logger)
    complement = m.outcomes[1] if len(m.outcomes) > 1 else None
    apply_price_changes(m.outcomes[0], change_points, complement)


def resolve_and_fetch(
    parsed: ParsedUrl,
    time_range: str,
    session: requests.Session,
    cache: DiskCache,
    logger: Optional[logging.Logger] = None,
) -> Market:
    if parsed.override_market_ticker:
        market_obj = _get_market(session, parsed.override_market_ticker, logger)
        event_title = ""
        try:
            event_obj = _get_event(session, market_obj.get("event_ticker", ""), logger)
            event_title = event_obj.get("title", "")
        except MarketNotFound:
            pass
        m = _single_market_to_market(market_obj, event_title, parsed.raw_url)
        effective_range = resolve_auto_time_range(time_range, m.start_time)
        m.time_range = effective_range
        m.historical_series = _fetch_candlesticks(
            session, market_obj.get("ticker", ""), effective_range, logger
        )
        _apply_changes_for_single_market(
            m, market_obj.get("ticker", ""), effective_range, session, logger
        )
        return m

    if parsed.override_ticker:
        event_ticker = parsed.override_ticker
        try:
            event_obj = _get_event(session, event_ticker, logger)
        except MarketNotFound:
            # The ticker embedded in the URL turned out not to exist (the
            # URL may be stale, or the guess was simply wrong) - fall back
            # to the same robust slug-based lookup used when the URL had
            # no ticker at all, rather than failing outright. Only do this
            # for a *guessed* ticker; an explicit Notes override that 404s
            # should still fail loudly, since that was a deliberate pin.
            if parsed.event_ticker_is_guess and parsed.series_ticker and parsed.url_slug:
                event_ticker = _find_event_ticker_by_slug(
                    session, parsed.series_ticker, parsed.url_slug, cache, logger
                )
                event_obj = _get_event(session, event_ticker, logger)
            else:
                raise
    elif parsed.series_ticker and parsed.url_slug:
        event_ticker = _find_event_ticker_by_slug(
            session, parsed.series_ticker, parsed.url_slug, cache, logger
        )
        event_obj = _get_event(session, event_ticker, logger)
    else:
        raise MarketNotFound(f"No ticker/slug available to resolve {parsed.raw_url}")

    markets = event_obj.get("markets", []) or []
    if not markets:
        raise MarketNotFound(f"Event {event_ticker!r} has no markets")

    if len(markets) == 1:
        m = _single_market_to_market(markets[0], event_obj.get("title", ""), parsed.raw_url)
        effective_range = resolve_auto_time_range(time_range, m.start_time)
        m.time_range = effective_range
        m.historical_series = _fetch_candlesticks(
            session, markets[0].get("ticker", ""), effective_range, logger
        )
        _apply_changes_for_single_market(
            m, markets[0].get("ticker", ""), effective_range, session, logger
        )
        return m

    return _multi_market_event_to_market(event_obj, markets, parsed.raw_url, session, logger)
