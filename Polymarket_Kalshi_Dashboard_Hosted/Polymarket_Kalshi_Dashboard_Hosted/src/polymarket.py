"""
Polymarket adapter.

Data sources (both public, no API key required):
  - Gamma API   https://gamma-api.polymarket.com   -> market/event metadata,
    current outcome prices (via `outcomePrices`).
  - CLOB API    https://clob.polymarket.com         -> `/prices-history` for
    historical probability series, keyed by CLOB token id.

Verified live against the real endpoints during development (Aug 2026):
  GET https://gamma-api.polymarket.com/events?active=true&closed=false...
  GET https://gamma-api.polymarket.com/events/slug/{slug}
  GET https://gamma-api.polymarket.com/markets/slug/{slug}
  GET https://clob.polymarket.com/prices-history?market=...&interval=...&fidelity=...
"""
from __future__ import annotations

import json
import logging
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

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

METADATA_CACHE_TTL = 6 * 3600  # 6 hours - slug/id mappings rarely change

# Used specifically when fetching history to compute a 7d price change.
# Requesting a window that's exactly 7 days wide leaves no margin: the
# oldest candle returned almost always lands just *inside* that boundary
# (due to discrete candle bucketing), never exactly at-or-before it, so
# compute_price_change's "at or before 168h ago" lookup silently fails
# and returns None every time. A comfortably wider window (30D) always
# has a genuine data point past the 7-day mark whenever the market is
# actually old enough to have one.
CHANGE_FETCH_RANGE = "30D"


class PolymarketError(Exception):
    pass


class MarketNotFound(PolymarketError):
    pass


def _time_range_to_history_params(time_range: str) -> Optional[dict]:
    """Map a CONFIG 'Time Range' value onto CLOB prices-history params.
    Returns None for CURRENT (no history requested)."""
    tr = (time_range or "30D").upper()
    mapping = {
        "24H": {"interval": "1d", "fidelity": 15},
        "7D": {"interval": "1w", "fidelity": 60},
        "30D": {"interval": "1m", "fidelity": 240},
        "90D": {"interval": "max", "fidelity": 720,
                "startTs": None},  # startTs filled in by caller
        "ALL": {"interval": "max", "fidelity": 1440},
        "CURRENT": None,
    }
    return mapping.get(tr, mapping["30D"])


def _fetch_price_history(
    session: requests.Session,
    token_id: str,
    time_range: str,
    logger: Optional[logging.Logger] = None,
) -> List[HistoryPoint]:
    params_template = _time_range_to_history_params(time_range)
    if params_template is None or not token_id:
        return []

    params = {"market": token_id}
    params.update({k: v for k, v in params_template.items() if v is not None})

    if (time_range or "").upper() == "90D":
        start = datetime.now(timezone.utc) - timedelta(days=90)
        params["startTs"] = int(start.timestamp())
        params.pop("interval", None)

    try:
        data = http_get_json(session, f"{CLOB_BASE}/prices-history", params=params,
                              logger=logger)
    except (requests.RequestException, ValueError) as exc:
        if logger:
            logger.warning("Price history fetch failed for token %s: %s", token_id, exc)
        return []

    points = []
    for row in data.get("history", []):
        ts = row.get("t")
        p = row.get("p")
        if ts is None or p is None:
            continue
        points.append(HistoryPoint(
            timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
            probability=float(p),
        ))
    return points


def _parse_json_field(raw) -> list:
    """Gamma encodes `outcomes`, `outcomePrices`, `clobTokenIds` as JSON
    strings inside the JSON response (e.g. '["Yes","No"]')."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _market_to_outcomes(market_obj: dict) -> List[Outcome]:
    names = _parse_json_field(market_obj.get("outcomes"))
    prices = _parse_json_field(market_obj.get("outcomePrices"))
    best_bid = safe_float(market_obj.get("bestBid"))
    best_ask = safe_float(market_obj.get("bestAsk"))
    outcomes = []
    for i, name in enumerate(names):
        price = safe_float(prices[i]) if i < len(prices) else None
        outcome = Outcome(name=str(name), probability=price)
        if i == 0:
            # Gamma exposes a single bestBid/bestAsk pair per market,
            # which prices the first (index 0) outcome's own order book -
            # there's no equivalent data for other outcomes to report
            # here, so their bid/ask stay unset rather than guessed.
            outcome.bid = best_bid
            outcome.ask = best_ask
        outcomes.append(outcome)
    return outcomes


def _single_market_from_obj(market_obj: dict, url: str) -> Market:
    status = MarketStatus.ACTIVE
    detail = ""
    if market_obj.get("umaResolutionStatus") == "resolved" or market_obj.get("closed"):
        status = MarketStatus.CLOSED
        detail = "Market is closed" + (
            " and resolved" if market_obj.get("umaResolutionStatus") == "resolved" else ""
        )

    m = Market(
        platform=Platform.POLYMARKET,
        url=url,
        market_id=str(market_obj.get("id", "")),
        event_id="",
        title=market_obj.get("question") or market_obj.get("groupItemTitle") or "",
        description=(market_obj.get("description") or "")[:500],
        status=status,
        status_detail=detail,
        start_time=parse_iso8601(market_obj.get("startDate")),
        end_time=parse_iso8601(market_obj.get("endDate")),
        outcomes=_market_to_outcomes(market_obj),
        volume=safe_float(market_obj.get("volumeNum") or market_obj.get("volume")),
        volume_24hr=safe_float(market_obj.get("volume24hr")),
        liquidity=safe_float(market_obj.get("liquidityNum") or market_obj.get("liquidity")),
        last_updated=parse_iso8601(market_obj.get("updatedAt")),
    )
    return m


def _group_event_to_market(
    event_obj: dict,
    url: str,
    session: requests.Session,
    logger: Optional[logging.Logger] = None,
) -> Market:
    """A Polymarket 'event' containing several binary sub-markets (e.g. an
    election-winner event with one Yes/No market per candidate, or a
    rolling "by [date]" series with one sub-market per period). Folds
    these into a single multi-outcome Market for the TABLE renderer.

    Rolling/recurring event groups accumulate sub-markets over time -
    Polymarket keeps every past period's already-resolved sub-market in
    the same event's `markets` array indefinitely. Showing all of them
    means a report full of long-expired "0%" rows, so this keeps only the
    currently open sub-markets and falls back to the full list only if
    every sub-market has closed (i.e. the whole event has concluded, in
    which case showing the final outcome is more useful than nothing).

    Separately, many multi-candidate events (elections, "who will be lead
    bank" style markets) ship with pre-created placeholder slots for
    candidates who haven't entered yet - "Bank A", "Bank B", "Option A",
    "Party C", etc. These are never shown on Polymarket's own site. Live
    data confirms the discriminator: real, live sub-markets have
    `active: true`; unfilled placeholders have `active: false` (and
    `liquidity: 0`). Filtering on `active` removes them the same way
    Polymarket's own front end does.

    24h/7d price change: each surviving sub-market gets its own short
    (7-day) history fetch, one extra CLOB call per row - table markets
    don't otherwise fetch any history at all, and this is the only way
    to compute a real change rather than guess one."""
    all_sub_markets = event_obj.get("markets", []) or []
    open_sub_markets = [
        sm for sm in all_sub_markets
        if not sm.get("closed", False) and sm.get("active", True)
    ]
    sub_markets = open_sub_markets if open_sub_markets else all_sub_markets

    status = MarketStatus.CLOSED if event_obj.get("closed") else MarketStatus.ACTIVE

    outcomes: List[Outcome] = []
    for sm in sub_markets:
        prices = _parse_json_field(sm.get("outcomePrices"))
        yes_price = safe_float(prices[0]) if prices else None
        name = sm.get("groupItemTitle") or sm.get("question") or sm.get("slug") or "?"
        outcome = Outcome(
            name=name,
            probability=yes_price,
            volume=safe_float(sm.get("volumeNum") or sm.get("volume")),
            bid=safe_float(sm.get("bestBid")),
            ask=safe_float(sm.get("bestAsk")),
        )
        if status == MarketStatus.ACTIVE:
            token_ids = _parse_json_field(sm.get("clobTokenIds"))
            if token_ids:
                # Use a window comfortably wider than 7 days, not exactly
                # 7 days - see CHANGE_FETCH_RANGE's docstring for why an
                # exact-width window silently breaks the 7d delta.
                history = _fetch_price_history(session, token_ids[0], CHANGE_FETCH_RANGE, logger)
                apply_price_changes(outcome, history)
        outcomes.append(outcome)

    m = Market(
        platform=Platform.POLYMARKET,
        url=url,
        market_id="",
        event_id=str(event_obj.get("id", "")),
        title=event_obj.get("title") or "",
        description=(event_obj.get("description") or "")[:500],
        status=status,
        start_time=parse_iso8601(event_obj.get("startDate")),
        end_time=parse_iso8601(event_obj.get("endDate")),
        outcomes=outcomes,
        volume=safe_float(event_obj.get("volume")),
        volume_24hr=safe_float(event_obj.get("volume24hr")),
        liquidity=safe_float(event_obj.get("liquidity")),
        last_updated=parse_iso8601(event_obj.get("updatedAt")),
    )
    return m


def resolve_and_fetch(
    parsed: ParsedUrl,
    time_range: str,
    session: requests.Session,
    cache: DiskCache,
    logger: Optional[logging.Logger] = None,
) -> Market:
    """Resolve a parsed Polymarket URL to a live Market, including history
    for binary markets."""
    market_obj: Optional[dict] = None
    event_obj: Optional[dict] = None

    if parsed.override_market_id:
        market_obj = _get_market_by_id(session, parsed.override_market_id, logger)
    elif parsed.market_slug:
        market_obj = _get_market_by_slug(session, parsed.market_slug, logger)
    elif parsed.event_slug:
        event_obj = _get_event_by_slug(session, parsed.event_slug, logger)
        sub_markets = (event_obj or {}).get("markets", []) or []
        if len(sub_markets) == 1:
            market_obj = sub_markets[0]
            event_obj = None
    else:
        raise MarketNotFound("No slug/id available to resolve this Polymarket URL")

    if market_obj is not None:
        m = _single_market_from_obj(market_obj, parsed.raw_url)
        token_ids = _parse_json_field(market_obj.get("clobTokenIds"))
        effective_range = resolve_auto_time_range(time_range, m.start_time)
        m.time_range = effective_range
        if token_ids and m.status == MarketStatus.ACTIVE:
            m.historical_series = _fetch_price_history(
                session, token_ids[0], effective_range, logger
            )
            if m.outcomes:
                # The chart displays at effective_range, which can be as
                # narrow as 24H/7D for a young market - too tight a margin
                # to reliably compute a 7d change (see CHANGE_FETCH_RANGE).
                # Re-fetch at a wider range just for that calculation when
                # needed, rather than starving the chart's own display.
                change_points = m.historical_series
                if effective_range.upper() in ("24H", "7D"):
                    change_points = _fetch_price_history(
                        session, token_ids[0], CHANGE_FETCH_RANGE, logger
                    )
                complement = m.outcomes[1] if len(m.outcomes) > 1 else None
                apply_price_changes(m.outcomes[0], change_points, complement)
        return m

    if event_obj is not None:
        m = _group_event_to_market(event_obj, parsed.raw_url, session, logger)
        return m

    raise MarketNotFound(f"Polymarket market/event not found for {parsed.raw_url}")


def _get_market_by_slug(session, slug: str, logger) -> dict:
    try:
        data = http_get_json(session, f"{GAMMA_BASE}/markets/slug/{slug}", logger=logger)
        if isinstance(data, list):
            if not data:
                raise MarketNotFound(f"No market found for slug {slug!r}")
            return data[0]
        return data
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise MarketNotFound(f"No Polymarket market found for slug {slug!r}") from exc
        raise


def _get_market_by_id(session, market_id: str, logger) -> dict:
    try:
        return http_get_json(session, f"{GAMMA_BASE}/markets/{market_id}", logger=logger)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise MarketNotFound(f"No Polymarket market found for id {market_id!r}") from exc
        raise


def _get_event_by_slug(session, slug: str, logger) -> dict:
    try:
        return http_get_json(session, f"{GAMMA_BASE}/events/slug/{slug}", logger=logger)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise MarketNotFound(f"No Polymarket event found for slug {slug!r}") from exc
        raise
