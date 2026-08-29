"""
=========================================================
Datei:      sigma/signals/scale_features.py
Zweck:      Skaleninvariante Features nur auf geschlossenen Kerzen.
            Keine Dollar-Range-Features, die TFs zerbrechen.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Feature)
=========================================================
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.quant.RegimeEngine import dfa_hurst
from app.quant.regime_detector import true_ranges
from sigma.signals.dual_hurst import htf_ready


def scale_invariant_features(
    candles: Optional[Sequence[Mapping[str, Any]]],
    *,
    interval_min: int = 15,
    now: Optional[float] = None,
    require_closed: bool = True,
) -> Dict[str, Any]:
    if not candles:
        return {"valid": False, "reason": "missing_data"}
    if require_closed and not htf_ready(candles, interval_min, now=now):
        return {"valid": False, "reason": "open_bar"}
    closes = _closes(candles)
    if len(closes) < 8:
        return {"valid": False, "reason": "short_series"}
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    z = _zscore(rets)
    mapped = [_bar(c) for c in candles]
    trs = true_ranges(mapped)
    atr = sum(trs[-14:]) / 14.0 if len(trs) >= 14 else (sum(trs) / len(trs) if trs else 0.0)
    px = closes[-1]
    atr_over_price = atr / px if px > 0 else 0.0
    vols = [_vol(c) for c in candles]
    rvol = _relative_volume(vols)
    hurst = dfa_hurst(closes)
    return {
        "valid": True,
        "log_return_z": round(z, 6),
        "atr_over_price": round(atr_over_price, 8),
        "hurst": float(hurst.get("hurst_exponent") or 0.5),
        "hurst_regime": hurst.get("regime"),
        "relative_volume": round(rvol, 4),
        "n": len(closes),
    }


def _closes(candles: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for c in candles:
        px = c.get("c", c.get("close"))
        try:
            val = float(px)
        except (TypeError, ValueError):
            continue
        if val > 0:
            out.append(val)
    return out


def _vol(c: Mapping[str, Any]) -> float:
    try:
        return float(c.get("v", c.get("volume", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bar(row: Mapping[str, Any]) -> Dict[str, float]:
    return {
        "ts": float(row.get("ts") or 0.0),
        "o": float(row.get("o", row.get("open", 0.0)) or 0.0),
        "h": float(row.get("h", row.get("high", 0.0)) or 0.0),
        "l": float(row.get("l", row.get("low", 0.0)) or 0.0),
        "c": float(row.get("c", row.get("close", 0.0)) or 0.0),
        "v": float(row.get("v", row.get("volume", 0.0)) or 0.0),
    }


def _zscore(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = sum(values) / len(values)
    var = sum((v - mu) ** 2 for v in values) / (len(values) - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd <= 0:
        return 0.0
    return (values[-1] - mu) / sd


def _relative_volume(vols: Sequence[float]) -> float:
    if len(vols) < 2:
        return 0.0
    last = vols[-1]
    base = sum(vols[:-1]) / len(vols[:-1])
    if base <= 0:
        return 0.0
    return last / base
