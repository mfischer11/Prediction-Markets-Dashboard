"""Shared utilities: logging setup and a resilient HTTP session."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Polymarket-Kalshi-Report/1.0 (personal monitoring tool)"


def setup_logging(log_dir: str, debug: bool = False) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(log_dir, f"report_{date_str}.log")

    logger = logging.getLogger("market_report")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def build_session(total_retries: int = 3, backoff_factor: float = 0.75) -> requests.Session:
    """A requests Session with sane timeouts, retries, and backoff for
    transient errors (429, 500, 502, 503, 504) as recommended by both
    Polymarket's and Kalshi's public API guidance."""
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def http_get_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
    logger: Optional[logging.Logger] = None,
) -> Any:
    """GET a URL and return parsed JSON, or raise requests.HTTPError /
    ValueError. Logs timing and outcome if a logger is given."""
    start = time.monotonic()
    resp = session.get(url, params=params, timeout=timeout)
    elapsed = time.monotonic() - start
    if logger:
        logger.debug(
            "GET %s params=%s -> %s (%.2fs)", url, params, resp.status_code, elapsed
        )
    resp.raise_for_status()
    return resp.json()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compute_price_change(
    points: Optional[List[Any]],
    current_prob: Optional[float],
    hours: float,
    reference_time: Optional[datetime] = None,
) -> Optional[float]:
    """Probability-point change over the last `hours` hours (e.g. 24 or
    168 for 24h/7d), computed as current minus the most recent history
    point at or before that cutoff. `points` just needs objects with
    `.timestamp` and `.probability` attributes (HistoryPoint satisfies
    this; kept loosely typed here to avoid a models.py<->utils.py import
    cycle).

    Returns None - rather than a misleading guess - when there's no data
    old enough to compare against (e.g. a market younger than the
    requested window), or when current_prob itself is unknown."""
    if current_prob is None or not points:
        return None
    now = reference_time or now_utc()
    cutoff = now - timedelta(hours=hours)
    candidates = [p for p in points if p.timestamp <= cutoff]
    if not candidates:
        return None
    baseline = max(candidates, key=lambda p: p.timestamp)
    if baseline.probability is None:
        return None
    return current_prob - baseline.probability


def apply_price_changes(outcome: Any, points: Optional[List[Any]],
                         complement: Optional[Any] = None) -> None:
    """Populates change_24h/change_7d on an Outcome from its own history.
    If `complement` is given (the other side of a binary Yes/No pair),
    its change is derived as the exact negative rather than fetched
    separately - the two sides always sum to 1 by construction on both
    platforms, so this is exact, not an approximation. Shared by both the
    Polymarket and Kalshi adapters."""
    ch24 = compute_price_change(points, outcome.probability, 24)
    ch7d = compute_price_change(points, outcome.probability, 24 * 7)
    outcome.change_24h = ch24
    outcome.change_7d = ch7d
    if complement is not None:
        complement.change_24h = -ch24 if ch24 is not None else None
        complement.change_7d = -ch7d if ch7d is not None else None


def resolve_auto_time_range(
    time_range: Optional[str],
    market_start: Optional[datetime],
    reference_time: Optional[datetime] = None,
) -> str:
    """Turns 'AUTO' into a concrete bucket (24H/7D/30D/90D/ALL) based on how
    long the market has actually been open, so nobody has to guess a Time
    Range per market. Non-AUTO values (including CURRENT) pass through
    unchanged. Falls back to 30D if AUTO is requested but the market's
    start date isn't known."""
    tr = (time_range or "AUTO").strip().upper()
    if tr != "AUTO":
        return tr

    if market_start is None:
        return "30D"

    now = reference_time or now_utc()
    if market_start.tzinfo is None:
        market_start = market_start.replace(tzinfo=timezone.utc)
    span_days = (now - market_start).total_seconds() / 86400.0

    if span_days <= 1:
        return "24H"
    if span_days <= 7:
        return "7D"
    if span_days <= 30:
        return "30D"
    if span_days <= 90:
        return "90D"
    return "ALL"


def parse_iso8601(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        return datetime.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
