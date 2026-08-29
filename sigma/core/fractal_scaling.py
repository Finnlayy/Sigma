"""
=========================================================
Datei:      sigma/core/fractal_scaling.py
Zweck:      CE50 / EQ-Position / Closed-Bar-Slice / Discount-Zone.
            Kein Hurst-Ersatz. Keine Orders.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math) / Blanche (Fraktal)
=========================================================
"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from sigma.core.math_engine import clamp


def candle_high(row: Mapping[str, Any]) -> float:
    return float(row.get("h", row.get("high", 0.0)) or 0.0)


def candle_low(row: Mapping[str, Any]) -> float:
    return float(row.get("l", row.get("low", 0.0)) or 0.0)


def candle_close(row: Mapping[str, Any]) -> float:
    return float(row.get("c", row.get("close", 0.0)) or 0.0)


class SigmaFractalCore:
    """O(1) zone math. Open-bar data is sliced away by the caller via ``ready``."""

    @staticmethod
    def closed_bars(
        candles: Optional[Sequence[Mapping[str, Any]]],
        *,
        ready: bool,
    ) -> List[Mapping[str, Any]]:
        """Return only closed HTF bars. If the last bar is open, drop it."""
        if not candles:
            return []
        rows = list(candles)
        if ready:
            return rows
        return rows[:-1]

    @staticmethod
    def ce50(range_high: float, range_low: float) -> Optional[float]:
        """50% consequent encroachment. None if the range is degenerate."""
        high = float(range_high)
        low = float(range_low)
        if high <= low:
            return None
        mid = (high + low) / 2.0
        return clamp(mid, low, high)

    @staticmethod
    def eq_pos(range_high: float, range_low: float, price: float) -> Optional[float]:
        """Equilibrium position in [0, 1]. None if the range is degenerate."""
        high = float(range_high)
        low = float(range_low)
        span = high - low
        if span <= 0.0:
            return None
        return clamp((float(price) - low) / span, 0.0, 1.0)

    @staticmethod
    def discount_zone(price: float, ce50: Optional[float]) -> bool:
        """True when price sits strictly below the 50% CE magnet."""
        if ce50 is None:
            return False
        return float(price) < float(ce50)
