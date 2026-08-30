"""
=========================================================
Datei:      sigma/signals/power_triangle.py
Zweck:      Leistungsdreieck (KB §9.2/§9.5): P/Q/S normiert auf
            ATR-Wilder-RMA, eta_efficiency, cos_phi_bar, cos_phi_path
            (Kaufman Efficiency Ratio), Klassifikations-Schwellen.
            Reelle Algebra, epsilon-geschuetzt, keine NaN.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math) / Blanche (Feature)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

EPS = 1e-9

# Klassifikations-Schwellen (KB §9.2/§9.5) als Benennungskonstanten
ETA_SOLID = 0.85      # eta >= 0.85 -> SOLID_TREND_EXPANSION
ETA_WICK = 0.30       # eta < 0.30  -> WICK_REJECTION
P_EXPLOSIVE = 1.2     # P_norm > 1.2 -> EXPLOSIVE_EXPANSION
S_CLIMAX = 2.0        # S_norm > 2.0 -> VOLATILITY_CLIMAX

SOLID_TREND_EXPANSION = "SOLID_TREND_EXPANSION"
WICK_REJECTION = "WICK_REJECTION"
EXPLOSIVE_EXPANSION = "EXPLOSIVE_EXPANSION"
VOLATILITY_CLIMAX = "VOLATILITY_CLIMAX"


@dataclass(frozen=True)
class PhysicsBar:
    """Skaleninvariante Physik-Features einer Kerze (KB §9.5)."""

    ts: int
    true_range: float
    atr: float
    s_norm: float            # (High-Low)/ATR
    p_norm: float            # |Close-Open|/ATR
    p_norm_signed: float     # (Close-Open)/ATR
    q_norm: float            # ((High-Low)-|Close-Open|)/ATR
    q_upper_norm: float      # (High-max(Open,Close))/ATR
    q_lower_norm: float      # (min(Open,Close)-Low)/ATR
    q_bias: float            # Q_lower - Q_upper
    eta_efficiency: float    # |Close-Open|/(High-Low) in [0,1]
    cos_phi_bar: float       # sign(Close-Open)*eta in [-1,1]

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _atr_series(trs: Sequence[float], period: int) -> List[float]:
    """Wilder-RMA der True Ranges (period=14). Vor dem Seed: Expanding Mean,
    damit jede Bar definierte Werte hat (kein None/NaN)."""
    out: List[float] = []
    if not trs:
        return out
    period = max(1, int(period))
    seed = sum(trs[:period]) / period
    rma = seed
    for i, tr in enumerate(trs):
        if i < period:
            out.append(sum(trs[: i + 1]) / (i + 1))
        elif i == period:
            out.append(seed)
        else:
            rma = (rma * (period - 1) + tr) / period
            out.append(rma)
    return out


def price_action_physics(
    candles: Sequence[Mapping[str, Any]],
    atr_period: int = 14,
) -> List[PhysicsBar]:
    """Berechnet pro geschlossener Kerze die §9.5-Features. Alle Nenner mit
    EPS-Schutz; flache Bars (H==L) liefern endliche 0-Werte, nie NaN."""
    if atr_period < 1:
        raise ValueError("atr_period muss >= 1 sein")
    rows = list(candles)
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        rows = rows[:-1]
    trs: List[float] = []
    prev_close: Optional[float] = None
    for c in rows:
        h, l = _h(c), _l(c)
        close = _c(c)
        if prev_close is not None:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        else:
            trs.append(0.0)
        prev_close = close
    atrs = _atr_series(trs, atr_period)

    out: List[PhysicsBar] = []
    for c, tr, atr in zip(rows, trs, atrs):
        o, h, l, close = _o(c), _h(c), _l(c), _c(c)
        span = h - l
        body = abs(close - o)
        denom = atr if atr > 0 else EPS
        eta = (body / span) if span > EPS else 0.0
        cos_bar = math.copysign(1.0, close - o) * eta if close != o else 0.0
        out.append(PhysicsBar(
            ts=int(c.get("ts", c.get("time", 0)) or 0),
            true_range=round(tr, 10),
            atr=round(atr, 10),
            s_norm=round(span / denom, 8),
            p_norm=round(body / denom, 8),
            p_norm_signed=round((close - o) / denom, 8),
            q_norm=round((span - body) / denom, 8),
            q_upper_norm=round(max(0.0, h - max(o, close)) / denom, 8),
            q_lower_norm=round(max(0.0, min(o, close) - l) / denom, 8),
            q_bias=round((max(0.0, min(o, close) - l) - max(0.0, h - max(o, close))) / denom, 8),
            eta_efficiency=round(eta, 8),
            cos_phi_bar=round(max(-1.0, min(1.0, cos_bar)), 8),
        ))
    return out


def cos_phi_bar(candle: Mapping[str, Any]) -> float:
    """Kerzen-Effizienz: sign(Close-Open) * eta in [-1, 1]."""
    o, h, l, close = _o(candle), _h(candle), _l(candle), _c(candle)
    span = h - l
    body = abs(close - o)
    eta = (body / span) if span > EPS else 0.0
    if close == o:
        return 0.0
    return round(max(-1.0, min(1.0, math.copysign(1.0, close - o) * eta)), 8)


def cos_phi_path(
    close: Sequence[float],
    window: int = 20,
    use_true_range: bool = False,
    high: Optional[Sequence[float]] = None,
    low: Optional[Sequence[float]] = None,
) -> float:
    """Pfad-Effizienz (Kaufman Efficiency Ratio, KB §9.2):
    (C_t - C_{t-N}) / Summe|Delta C| bzw. / Summe TR. 0.0 bei leerem Pfad
    oder Nenner 0; geclippt auf [-1, 1]."""
    closes = [float(x) for x in close]
    if window < 1 or len(closes) < window + 1:
        return 0.0
    numerator = closes[-1] - closes[-1 - window]
    if use_true_range:
        if high is None or low is None or len(high) < window + 1 or len(low) < window + 1:
            return 0.0
        highs = [float(x) for x in high]
        lows = [float(x) for x in low]
        denom = 0.0
        for i in range(len(closes) - window, len(closes)):
            h, l = highs[i], lows[i]
            prev = closes[i - 1]
            denom += max(h - l, abs(h - prev), abs(l - prev))
    else:
        denom = sum(abs(closes[i] - closes[i - 1])
                    for i in range(len(closes) - window, len(closes)))
    if denom <= EPS:
        return 0.0
    return round(max(-1.0, min(1.0, numerator / denom)), 8)


def classify_bar(physics: PhysicsBar) -> List[str]:
    """Klassifikation nach §9.2/§9.5 (alle Schwellen als Konstanten)."""
    tags: List[str] = []
    if physics.eta_efficiency >= ETA_SOLID:
        tags.append(SOLID_TREND_EXPANSION)
    if physics.eta_efficiency < ETA_WICK:
        tags.append(WICK_REJECTION)
    if physics.p_norm > P_EXPLOSIVE:
        tags.append(EXPLOSIVE_EXPANSION)
    if physics.s_norm > S_CLIMAX:
        tags.append(VOLATILITY_CLIMAX)
    return tags


def _o(c: Mapping[str, Any]) -> float:
    return float(c.get("o", c.get("open", 0.0)) or 0.0)


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


__all__ = [
    "EPS", "ETA_SOLID", "ETA_WICK", "EXPLOSIVE_EXPANSION", "P_EXPLOSIVE",
    "PhysicsBar", "S_CLIMAX", "SOLID_TREND_EXPANSION", "VOLATILITY_CLIMAX",
    "WICK_REJECTION", "classify_bar", "cos_phi_bar", "cos_phi_path",
    "price_action_physics",
]
