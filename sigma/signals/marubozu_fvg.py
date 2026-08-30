"""
=========================================================
Datei:      sigma/signals/marubozu_fvg.py
Zweck:      Marubozu (Body/Range >= 0.80) + 3-Bar-FVG mit ATR-
            normalisierter Gap-Groesse und CE50. FVG-Flags und CE50
            kommen aus htf_features / fractal_scaling — KEINE zweite
            FVG-Logik. Nur closed bars, kein Look-ahead.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature) / Jaune (Math)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from sigma.core.fractal_scaling import SigmaFractalCore
from sigma.signals.htf_features import atr_wilder, fvg_flags

MARUBOZU_MIN_BODY_RATIO = 0.80
ATR_PERIOD = 14


@dataclass(frozen=True)
class MarubozuFvgSignal:
    """Marubozu + FVG-Zone. valid=False bei zu wenig Bars (fail-closed)."""

    valid: bool
    marubozu: bool
    body_ratio: float
    direction: str
    fvg_bullish: bool
    fvg_bearish: bool
    gap_low: Optional[float]
    gap_high: Optional[float]
    gap_atr_ratio: Optional[float]
    ce50: Optional[float]
    atr: Optional[float]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def evaluate(
    candles: Sequence[Mapping[str, Any]],
    *,
    atr_period: int = ATR_PERIOD,
) -> MarubozuFvgSignal:
    """Marubozu auf der letzten geschlossenen Kerze + FVG der letzten 3
    Kerzen. Gap-Groesse in ATR-Einheiten (skaleninvariant), CE50 ueber
    SigmaFractalCore (kein Duplikat)."""
    closed = _closed_bars(candles)
    if len(closed) < max(3, atr_period + 1):
        return MarubozuFvgSignal(
            valid=False, marubozu=False, body_ratio=0.0, direction="",
            fvg_bullish=False, fvg_bearish=False, gap_low=None, gap_high=None,
            gap_atr_ratio=None, ce50=None, atr=None, reason="insufficient_bars",
        )
    last = closed[-1]
    o, h, l, c = _o(last), _h(last), _l(last), _c(last)
    rng = h - l
    body = abs(c - o)
    body_ratio = (body / rng) if rng > 0 else (1.0 if body > 0 else 0.0)
    marubozu = rng > 0 and body_ratio >= MARUBOZU_MIN_BODY_RATIO
    direction = "bullish" if (marubozu and c > o) else (
        "bearish" if (marubozu and c < o) else ""
    )
    flags = fvg_flags(closed[-3:])
    raw_low = flags.get("gap_low")
    raw_high = flags.get("gap_high")
    # fvg_flags benennt die Felder nach der Quell-Bar (c.low / a.high), nicht
    # nach Preisniveau — Zone kanonisch als (min, max) normalisieren.
    gap_low = gap_high = None
    if raw_low is not None and raw_high is not None:
        gap_low = min(float(raw_low), float(raw_high))
        gap_high = max(float(raw_low), float(raw_high))
    atr = atr_wilder(closed, period=atr_period)
    gap_atr_ratio = None
    if gap_low is not None and gap_high is not None and atr and atr > 0:
        gap_atr_ratio = (float(gap_high) - float(gap_low)) / atr
    ce50 = SigmaFractalCore.ce50(float(gap_high), float(gap_low)) \
        if gap_high is not None and gap_low is not None else None
    return MarubozuFvgSignal(
        valid=True,
        marubozu=marubozu,
        body_ratio=round(body_ratio, 6),
        direction=direction,
        fvg_bullish=bool(flags.get("bullish_fvg")),
        fvg_bearish=bool(flags.get("bearish_fvg")),
        gap_low=float(gap_low) if gap_low is not None else None,
        gap_high=float(gap_high) if gap_high is not None else None,
        gap_atr_ratio=round(gap_atr_ratio, 6) if gap_atr_ratio is not None else None,
        ce50=float(ce50) if ce50 is not None else None,
        atr=float(atr) if atr is not None else None,
        reason="ok",
    )


def _closed_bars(candles: Sequence[Mapping[str, Any]]) -> list:
    rows = list(candles)
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _o(c: Mapping[str, Any]) -> float:
    return float(c.get("o", c.get("open", 0.0)) or 0.0)


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


__all__ = ["ATR_PERIOD", "MARUBOZU_MIN_BODY_RATIO", "MarubozuFvgSignal", "evaluate"]
