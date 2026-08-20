"""
Reads/writes the market list from config.json instead of an Excel CONFIG
sheet - this is the settings-page backing store for the dashboard.

Same validation rules as the Excel version's config_reader.py (blank
platform/URL rows rejected, unknown Display Type / Time Range fall back to
AUTO with a warning, duplicate URLs flagged but kept) and the same
ordering rule: markets appear in the report in the exact order they're
listed here - reordering in the settings page is the only thing that
changes report order, there's no separate "sort order" field.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List

from .models import ConfigRow

VALID_PLATFORMS = {"polymarket", "kalshi"}
VALID_DISPLAY_TYPES = {"auto", "chart", "table"}
VALID_TIME_RANGES = {"auto", "24h", "7d", "30d", "90d", "all", "current"}

DEFAULT_ROWS = [
    {"enabled": True, "platform": "Polymarket",
     "url": "https://polymarket.com/event/fed-decision-in-september-762",
     "display_type": "AUTO", "title_override": "", "time_range": "AUTO",
     "notes": "Multi-outcome FOMC decision market"},
    {"enabled": True, "platform": "Polymarket",
     "url": "https://polymarket.com/event/fed-rate-hike-in-2026",
     "display_type": "AUTO", "title_override": "", "time_range": "AUTO",
     "notes": "Binary Yes/No market"},
    {"enabled": True, "platform": "Kalshi",
     "url": "https://kalshi.com/markets/kxhighny/highest-temperature-in-nyc-today",
     "display_type": "AUTO", "title_override": "", "time_range": "AUTO",
     "notes": "Daily weather market - resolves to today's open event automatically"},
]


@dataclass
class ConfigIssue:
    row_number: int
    message: str
    severity: str = "error"


@dataclass
class ConfigReadResult:
    rows: List[ConfigRow] = field(default_factory=list)
    issues: List[ConfigIssue] = field(default_factory=list)


def ensure_config_exists(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_ROWS, f, indent=2)


def load_raw_rows(path: str) -> list:
    """Returns the raw list of dicts as stored on disk - this is what the
    settings page reads/edits/writes directly, before validation."""
    ensure_config_exists(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return data


def save_raw_rows(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def read_config(path: str) -> ConfigReadResult:
    """Validates the raw rows and turns them into ConfigRow objects for
    the fetch pipeline, in the exact order given."""
    raw_rows = load_raw_rows(path)
    result = ConfigReadResult()
    seen_urls = {}

    for idx, raw in enumerate(raw_rows, start=1):
        url = str(raw.get("url") or "").strip()
        if not url:
            result.issues.append(ConfigIssue(idx, "Missing URL - row skipped"))
            continue

        platform = str(raw.get("platform") or "").strip()
        if platform.lower() not in VALID_PLATFORMS:
            result.issues.append(ConfigIssue(
                idx, f"Unrecognized Platform {platform!r} (expected Polymarket or "
                     f"Kalshi) - row skipped",
            ))
            continue

        display_type = str(raw.get("display_type") or "AUTO").strip() or "AUTO"
        if display_type.lower() not in VALID_DISPLAY_TYPES:
            result.issues.append(ConfigIssue(
                idx, f"Unrecognized Display Type {display_type!r}; using AUTO",
                severity="warning",
            ))
            display_type = "AUTO"

        time_range = str(raw.get("time_range") or "AUTO").strip() or "AUTO"
        if time_range.lower() not in VALID_TIME_RANGES:
            result.issues.append(ConfigIssue(
                idx, f"Unrecognized Time Range {time_range!r}; using AUTO",
                severity="warning",
            ))
            time_range = "AUTO"

        if url in seen_urls:
            result.issues.append(ConfigIssue(
                idx, f"Duplicate URL (also row {seen_urls[url]}); keeping both, "
                     f"but you probably only want one",
                severity="warning",
            ))
        else:
            seen_urls[url] = idx

        result.rows.append(ConfigRow(
            row_number=idx,
            enabled=bool(raw.get("enabled", False)),
            platform=platform,
            url=url,
            display_type=display_type.upper(),
            title_override=str(raw.get("title_override") or "").strip(),
            time_range=time_range.upper(),
            notes=str(raw.get("notes") or "").strip(),
        ))

    return result
