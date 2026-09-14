"""
=========================================================
Datei:      sigma/signals/htf_features.py
Zweck:      FVG / EQ-Dealing-Range / MSS-Sweep als gespeicherte Flags.
            Niemals Loop-A-Gates. H4/H5 müssen zuerst in Paper bestehen.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature-Stub)
=========================================================
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from sigma.signals.dual_hurst import htf_ready


def dealing_range_eq(high: float, low: float, price: float) -> Optional[float]:
    """pos_t = (price - low) / (high - low). None if range is degenerate."""
    span = float(high) - float(low)
    if span <= 0:
        return None
    return (float(price) - float(low)) / span


def atr_wilder(
    candles: Sequence[Mapping[str, Any]], period: int = 14
) -> Optional[float]:
    """Wilder-RMA-ATR (kanonischer Helfer fuer MP-03/MP-04, skaleninvariante
    FVG-/Volatilitaets-Normierung). None, wenn zu wenige Bars (fail-closed)."""
    if not candles:
        return None
    period = max(1, int(period))
    trs: List[float] = []
    prev_close: Optional[float] = None
    for c in candles:
        h, l = _h(c), _l(c)
        close = _c(c)
        if prev_close is not None:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
        prev_close = close
    if len(trs) < period:
        return None
    rma = sum(trs[:period]) / period
    for tr in trs[period:]:
        rma = (rma * (period - 1) + tr) / period
    return rma


def fvg_flags(candles: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Three-bar fair-value-gap flags. Research-only; NSR gate = need 2-bar confirm."""
    if len(candles) < 3:
        return {"bullish_fvg": False, "bearish_fvg": False, "confirmed": False}
    a, b, c = candles[-3], candles[-2], candles[-1]
    a_h, a_l = _h(a), _l(a)
    c_h, c_l = _h(c), _l(c)
    bullish = a_h < c_l
    bearish = a_l > c_h
    return {
        "bullish_fvg": bullish,
        "bearish_fvg": bearish,
        "confirmed": False,  # 1-bar FVG stays research-only (NSR ~36.6%)
        "gap_low": c_l if bullish else (c_h if bearish else None),
        "gap_high": a_h if bullish else (a_l if bearish else None),
    }


def sweep_mss_flags(candles: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if len(candles) < 5:
        return {"liquidity_sweep": False, "mss": False}
    highs = [_h(c) for c in candles[-5:]]
    lows = [_l(c) for c in candles[-5:]]
    prior_high = max(highs[:-1])
    prior_low = min(lows[:-1])
    last_h, last_l, last_c = highs[-1], lows[-1], _c(candles[-1])
    sweep_high = last_h > prior_high and last_c < prior_high
    sweep_low = last_l < prior_low and last_c > prior_low
    mss = (last_c > prior_high) or (last_c < prior_low)
    return {
        "liquidity_sweep": bool(sweep_high or sweep_low),
        "sweep_side": "high" if sweep_high else ("low" if sweep_low else ""),
        "mss": bool(mss and not (sweep_high or sweep_low)),
    }


def extract_htf_flags(
    candles: Optional[Sequence[Mapping[str, Any]]],
    *,
    interval_min: int = 60,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Stored flags only. valid=False if HTF bar still open."""
    if not candles:
        return {"valid": False, "reason": "missing_data", "live_gate": False}
    if not htf_ready(candles, interval_min, now=now):
        return {"valid": False, "reason": "htf_open", "live_gate": False}
    last = candles[-1]
    eq = dealing_range_eq(_h(last), _l(last), _c(last))
    flags = {
        "valid": True,
        "live_gate": False,
        "eq_pos": eq,
        **fvg_flags(candles),
        **sweep_mss_flags(candles),
    }
    return flags


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)
