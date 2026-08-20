"""
Simple local disk cache (JSON file) with per-entry TTL.

Used for things that don't need to be fetched fresh every run:
  - URL -> market/event ID resolution (slugs rarely change)
  - Event/series metadata used only for display (title, category)

Current probabilities / prices are NEVER cached here - callers must fetch
those fresh every run so a stale number can't be mistaken for current data.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional


class DiskCache:
    def __init__(self, path: str):
        self.path = path
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, self.path)

    def get(self, key: str, max_age_seconds: Optional[float] = None) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        if max_age_seconds is not None:
            age = time.time() - entry.get("_cached_at", 0)
            if age > max_age_seconds:
                return None
        return entry.get("value")

    def set(self, key: str, value: Any) -> None:
        self._data[key] = {"value": value, "_cached_at": time.time()}

    def __len__(self) -> int:
        return len(self._data)
