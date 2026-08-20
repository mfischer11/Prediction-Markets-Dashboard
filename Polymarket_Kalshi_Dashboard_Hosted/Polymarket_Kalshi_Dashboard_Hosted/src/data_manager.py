"""
Orchestrates a full report run:
  1. Read CONFIG rows from the config workbook (never written back to).
  2. For each enabled row, parse the URL, call the right platform adapter,
     and normalize the result into a Market.
  3. Record lightweight history (first/last seen, status) in a local SQLite
     database so changes can be detected over time.
  4. Return everything the Excel layer needs: per-market results + run stats.

A single broken market never aborts the run - every failure is caught,
classified, and reported in STATUS instead.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import requests

from . import kalshi, polymarket
from .cache import DiskCache
from .market_parser import UrlParseError, parse_market_url
from .models import ConfigRow, Market, MarketResult, MarketStatus, Platform
from .utils import build_session


@dataclass
class RunStats:
    configured: int = 0
    enabled: int = 0
    successful: int = 0
    expired_or_closed: int = 0
    failed: int = 0
    invalid_url: int = 0
    charts: int = 0
    tables: int = 0
    duration_seconds: float = 0.0


def init_history_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_history (
            url TEXT PRIMARY KEY,
            platform TEXT,
            market_id TEXT,
            title TEXT,
            status TEXT,
            outcome_structure TEXT,
            first_seen TEXT,
            last_seen TEXT,
            last_successful_retrieval TEXT
        )
        """
    )
    conn.commit()
    return conn


def record_history(conn: sqlite3.Connection, result: MarketResult) -> None:
    now = datetime.now(timezone.utc).isoformat()
    url = result.config_row.url
    m = result.market
    outcome_structure = ""
    if m and m.outcomes:
        outcome_structure = ",".join(o.name for o in m.outcomes)

    cur = conn.execute("SELECT first_seen FROM market_history WHERE url = ?", (url,))
    row = cur.fetchone()
    first_seen = row[0] if row else now

    conn.execute(
        """
        INSERT INTO market_history
            (url, platform, market_id, title, status, outcome_structure,
             first_seen, last_seen, last_successful_retrieval)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            platform=excluded.platform,
            market_id=excluded.market_id,
            title=excluded.title,
            status=excluded.status,
            outcome_structure=excluded.outcome_structure,
            last_seen=excluded.last_seen,
            last_successful_retrieval=CASE
                WHEN excluded.status NOT IN ('API_ERROR', 'NOT_FOUND', 'INVALID_URL')
                THEN excluded.last_successful_retrieval
                ELSE market_history.last_successful_retrieval
            END
        """,
        (
            url,
            result.config_row.platform,
            m.market_id if m else "",
            m.title if m else "",
            result.status.value,
            outcome_structure,
            first_seen,
            now,
            now,
        ),
    )
    conn.commit()


def fetch_one_market(
    row: ConfigRow,
    session: requests.Session,
    cache: DiskCache,
    logger: logging.Logger,
) -> MarketResult:
    start = time.monotonic()
    try:
        parsed = parse_market_url(row.url, notes=row.notes)
    except UrlParseError as exc:
        logger.warning("[%s] Invalid URL: %s", row.url, exc)
        return MarketResult(
            config_row=row, market=None, status=MarketStatus.INVALID_URL,
            status_detail=str(exc), duration_seconds=time.monotonic() - start,
        )

    try:
        if parsed.platform == Platform.POLYMARKET:
            market = polymarket.resolve_and_fetch(parsed, row.time_range, session, cache, logger)
        else:
            market = kalshi.resolve_and_fetch(parsed, row.time_range, session, cache, logger)
    except (polymarket.MarketNotFound, kalshi.MarketNotFound) as exc:
        logger.warning("[%s] Not found: %s", row.url, exc)
        return MarketResult(
            config_row=row, market=None, status=MarketStatus.NOT_FOUND,
            status_detail=str(exc), duration_seconds=time.monotonic() - start,
        )
    except requests.Timeout as exc:
        logger.error("[%s] Timeout: %s", row.url, exc)
        return MarketResult(
            config_row=row, market=None, status=MarketStatus.API_ERROR,
            status_detail=f"Request timed out: {exc}",
            duration_seconds=time.monotonic() - start,
        )
    except requests.RequestException as exc:
        logger.error("[%s] API error: %s", row.url, exc)
        return MarketResult(
            config_row=row, market=None, status=MarketStatus.API_ERROR,
            status_detail=str(exc), duration_seconds=time.monotonic() - start,
        )
    except Exception as exc:  # noqa: BLE001 - never let one market crash the run
        logger.exception("[%s] Unexpected error", row.url)
        return MarketResult(
            config_row=row, market=None, status=MarketStatus.API_ERROR,
            status_detail=f"Unexpected error: {exc}",
            duration_seconds=time.monotonic() - start,
        )

    market.display_type = _resolve_display_type_enum(row.display_type)
    # market.time_range was already resolved (AUTO -> concrete bucket) inside
    # the platform adapter, which knows the market's actual start date -
    # don't clobber that with the raw, possibly-still-"AUTO" config value.
    market.config_row = row

    status = market.status
    detail = market.status_detail
    return MarketResult(
        config_row=row, market=market, status=status, status_detail=detail,
        duration_seconds=time.monotonic() - start,
    )


def _resolve_display_type_enum(value: str):
    from .models import DisplayType
    try:
        return DisplayType(value.strip().upper())
    except (ValueError, AttributeError):
        return DisplayType.AUTO


def run_all(
    rows: List[ConfigRow],
    cache_path: str,
    db_path: str,
    logger: logging.Logger,
) -> tuple[List[MarketResult], RunStats]:
    stats = RunStats(configured=len(rows))
    session = build_session()
    cache = DiskCache(cache_path)
    conn = init_history_db(db_path)

    results: List[MarketResult] = []
    run_start = time.monotonic()

    enabled_rows = [r for r in rows if r.enabled]
    stats.enabled = len(enabled_rows)

    for row in enabled_rows:
        result = fetch_one_market(row, session, cache, logger)
        results.append(result)
        record_history(conn, result)

        if result.status == MarketStatus.INVALID_URL:
            stats.invalid_url += 1
        elif result.status in (MarketStatus.CLOSED, MarketStatus.SETTLED,
                                MarketStatus.NOT_FOUND):
            stats.expired_or_closed += 1
        elif result.status == MarketStatus.API_ERROR:
            stats.failed += 1
        else:
            stats.successful += 1

        if result.market is not None:
            if result.market.resolved_display_type().value == "CHART":
                stats.charts += 1
            else:
                stats.tables += 1

    cache.save()
    conn.close()
    stats.duration_seconds = time.monotonic() - run_start
    return results, stats
