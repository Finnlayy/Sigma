"""
=========================================================
Datei:      sigma/signals/dual_hurst.py
Zweck:      DFA-Hurst auf HTF- und LTF-Closes. Nur geschlossene HTF-Kerzen.
            Komplementär: HTF H>0.55 und LTF H<0.45.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Regime) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.core.exchange_clock import get_exchange_clock
from app.quant.RegimeEngine import dfa_hurst
from app.tv.interval_map import to_seconds


@dataclass(frozen=True)
class DualHurst:
    htf_hurst: float
    ltf_hurst: float
    htf_regime: str
    ltf_regime: str
    htf_ready: bool
    complementary: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


HURST_TREND = 0.55
HURST_REVERT = 0.45


def _closes(candles: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for c in candles or []:
        px = c.get("c", c.get("close"))
        try:
            val = float(px)
        except (TypeError, ValueError):
            continue
        if val > 0:
            out.append(val)
    return out


def _bar_close_ts(candle: Mapping[str, Any], interval_min: int) -> float:
    raw = candle.get("ts") or candle.get("time") or 0.0
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if ts > 1e11:
        ts /= 1000.0
    return ts + float(to_seconds(interval_min))


def htf_ready(
    htf_candles: Sequence[Mapping[str, Any]],
    interval_min: int,
    now: Optional[float] = None,
) -> bool:
    if not htf_candles:
        return False
    clock_now = now
    if clock_now is None:
        try:
            clock_now = float(get_exchange_clock().now())
        except Exception:
            import time
            clock_now = time.time()
    last = htf_candles[-1]
    return _bar_close_ts(last, interval_min) <= float(clock_now) + 1e-6


def evaluate_dual_hurst(
    htf_candles: Optional[Sequence[Mapping[str, Any]]],
    ltf_candles: Optional[Sequence[Mapping[str, Any]]],
    *,
    htf_interval_min: int = 60,
    now: Optional[float] = None,
) -> DualHurst:
    if not htf_candles or not ltf_candles:
        return DualHurst(0.5, 0.5, "RANDOM_WALK", "RANDOM_WALK", False, False, "missing_data")
    ready = htf_ready(htf_candles, htf_interval_min, now=now)
    if not ready:
        return DualHurst(0.5, 0.5, "RANDOM_WALK", "RANDOM_WALK", False, False, "htf_open")
    htf = dfa_hurst(_closes(htf_candles))
    ltf = dfa_hurst(_closes(ltf_candles))
    hh = float(htf.get("hurst_exponent") or 0.5)
    lh = float(ltf.get("hurst_exponent") or 0.5)
    complementary = hh > HURST_TREND and lh < HURST_REVERT
    return DualHurst(
        htf_hurst=hh,
        ltf_hurst=lh,
        htf_regime=str(htf.get("regime") or "RANDOM_WALK"),
        ltf_regime=str(ltf.get("regime") or "RANDOM_WALK"),
        htf_ready=True,
        complementary=complementary,
        reason="complementary" if complementary else "not_complementary",
    )
