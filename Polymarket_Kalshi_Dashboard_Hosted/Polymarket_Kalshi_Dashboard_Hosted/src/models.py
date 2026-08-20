"""
Normalized internal data model.

The Excel rendering layer only ever touches these dataclasses - it never
knows whether a Market came from Polymarket or Kalshi. All platform-specific
parsing/quirks live inside src/polymarket.py and src/kalshi.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple


class Platform(str, Enum):
    POLYMARKET = "Polymarket"
    KALSHI = "Kalshi"


class MarketStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    SETTLED = "SETTLED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_URL = "INVALID_URL"
    API_ERROR = "API_ERROR"
    DISABLED = "DISABLED"


class DisplayType(str, Enum):
    AUTO = "AUTO"
    CHART = "CHART"
    TABLE = "TABLE"


@dataclass
class Outcome:
    """A single outcome within a market (e.g. 'Yes', 'Trump', 'Over 2.5')."""
    name: str
    probability: Optional[float] = None       # 0..1
    prior_probability: Optional[float] = None  # 0..1, for computing change
    volume: Optional[float] = None
    bid: Optional[float] = None                # 0..1, best bid price
    ask: Optional[float] = None                # 0..1, best ask price
    change_24h: Optional[float] = None         # probability-point delta, e.g. 0.032 = +3.2pp
    change_7d: Optional[float] = None          # probability-point delta


@dataclass
class HistoryPoint:
    timestamp: datetime
    probability: float  # 0..1, probability of the primary/first outcome


@dataclass
class ConfigRow:
    """One row from the CONFIG sheet, as the user maintains it."""
    row_number: int
    enabled: bool
    platform: str
    url: str
    display_type: str = "AUTO"
    title_override: str = ""
    time_range: str = "AUTO"
    notes: str = ""


@dataclass
class Market:
    """Normalized, platform-agnostic market representation."""
    platform: Platform
    url: str
    market_id: str = ""
    event_id: str = ""
    title: str = ""
    description: str = ""
    status: MarketStatus = MarketStatus.ACTIVE
    status_detail: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    outcomes: List[Outcome] = field(default_factory=list)
    historical_series: List[HistoryPoint] = field(default_factory=list)
    volume: Optional[float] = None
    volume_24hr: Optional[float] = None
    liquidity: Optional[float] = None
    open_interest: Optional[float] = None
    last_updated: Optional[datetime] = None

    # From CONFIG sheet, carried through for rendering
    config_row: Optional[ConfigRow] = None
    display_type: DisplayType = DisplayType.AUTO
    time_range: str = "30D"

    def is_binary(self) -> bool:
        """True if this looks like a plain Yes/No market."""
        if len(self.outcomes) != 2:
            return False
        names = {o.name.strip().lower() for o in self.outcomes}
        return names == {"yes", "no"}

    def resolved_display_type(self) -> DisplayType:
        """AUTO -> CHART for binary markets with history, else TABLE."""
        if self.display_type != DisplayType.AUTO:
            return self.display_type
        if self.is_binary() and len(self.historical_series) >= 2:
            return DisplayType.CHART
        if self.is_binary() and not self.outcomes:
            return DisplayType.CHART
        return DisplayType.TABLE

    def current_probability(self) -> Optional[float]:
        """Probability of the first ('Yes') outcome, for binary markets."""
        if not self.outcomes:
            return None
        return self.outcomes[0].probability

    def sorted_outcomes(self) -> List[Outcome]:
        return sorted(
            self.outcomes,
            key=lambda o: (o.probability if o.probability is not None else -1),
            reverse=True,
        )


@dataclass
class MarketResult:
    """Wraps a Market plus the config row it came from, for report building."""
    config_row: ConfigRow
    market: Optional[Market]
    status: MarketStatus
    status_detail: str = ""
    duration_seconds: float = 0.0
