"""
=========================================================
Datei:      app/tv/interval_map.py
Zweck:      §3.2 — Interval-Mapping: Sigma-Minuten <-> TV-Interval <-> Scraper.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Schema-Standardisierung)
=========================================================
"""
from __future__ import annotations

from typing import Dict, Union

from app.core import blueprint as bp

Interval = Union[int, str]

# TV-Chart-Interval-Codes: Minuten als Zahl, Stunden als Minuten, D/W/M als Buchstabe
_TV_CODES: Dict[int, str] = {
    1: "1", 3: "3", 5: "5", 10: "10", 15: "15", 30: "30",
    60: "60", 120: "120", 240: "240", 1440: "D", 10080: "W",
}
_SCRAPER_CODES: Dict[int, str] = {
    1: "1m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 30: "30m",
    60: "1h", 120: "2h", 240: "4h", 1440: "1d", 10080: "1w",
}


def to_minutes(interval: Interval) -> int:
    """`15` | `'15'` | `'15m'` | `'4h'` | `'D'` -> Minuten."""
    if isinstance(interval, (int, float)):
        return int(interval)
    raw = str(interval).strip().lower()
    if not raw:
        return 15
    if raw in ("d", "1d", "day"):
        return 1440
    if raw in ("w", "1w", "week"):
        return 10080
    if raw in ("m", "1m month", "month"):
        return 43200
    if raw.endswith("m") and raw[:-1].isdigit():
        return int(raw[:-1])
    if raw.endswith("h") and raw[:-1].isdigit():
        return int(raw[:-1]) * 60
    if raw.isdigit():
        return int(raw)
    return 15


def to_tv_interval(interval: Interval) -> str:
    minutes = to_minutes(interval)
    return _TV_CODES.get(minutes, str(minutes))


def to_scraper_timeframe(interval: Interval) -> str:
    minutes = to_minutes(interval)
    if minutes in _SCRAPER_CODES:
        return _SCRAPER_CODES[minutes]
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def to_seconds(interval: Interval) -> int:
    return to_minutes(interval) * 60


def style_for_interval(interval: Interval) -> str:
    """Masterprompt §3.D — Style/Campaign-Horizont aus dem Timeframe."""
    minutes = to_minutes(interval)
    if minutes <= 3:
        return "STYLE_MICRO_SCALP"
    if minutes <= 15:
        return "STYLE_INTRADAY_MOMENT"
    if minutes <= 240:
        return "STYLE_SWING_CAMPAIGN"
    return "STYLE_POSITION_INVEST"


def style_spec(interval: Interval):
    name = style_for_interval(interval)
    for spec in bp.STYLES:
        if spec.style == name:
            return spec
    return bp.STYLES[1]


def stale_limit_seconds(interval: Interval) -> int:
    """§17.2 — max(2 * interval, 120)."""
    return max(bp.SIGNAL_STALE_INTERVAL_FACTOR * to_seconds(interval), bp.SIGNAL_STALE_MIN_SECONDS)
