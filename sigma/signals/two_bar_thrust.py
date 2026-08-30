"""
=========================================================
Datei:      sigma/signals/two_bar_thrust.py
Zweck:      Two-Bar-Bullish-Thrust (KB §4.3): 1 Baer + 2 Bullen,
            Bull-Bodys > Baer-Body, Close > High[2]. Kontext-Flags
            (Support/EMA/Sweep) sind Evidenz, keine harten Gates.
            Nur closed bars, kein Look-ahead, keine Orders.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from sigma.signals.htf_features import sweep_mss_flags

SUPPORT_TOLERANCE_PCT = 0.005  # 0,5 % Toleranz fuer Support-Konfluenz


@dataclass(frozen=True)
class TwoBarThrustSignal:
    """Signal + Evidenzfelder. signal=True nur bei erfuelltem Muster."""

    signal: bool
    direction: str
    bear_body: float
    bull_body_sum: float
    close_above_bear_high: bool
    stop_price: Optional[float]
    # Evidenz-Kontext (KEINE harten Bedingungen):
    support_confluence: bool
    ema_aligned: bool
    session_sweep: bool
    closed_bars_used: int

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def evaluate(
    candles: Sequence[Mapping[str, Any]],
    *,
    support_price: Optional[float] = None,
    ema20: Optional[float] = None,
    sweep: Optional[bool] = None,
) -> TwoBarThrustSignal:
    """Wertet das Drei-Bar-Muster auf der letzten geschlossenen Kerze aus.
    Eine als offen markierte letzte Bar wird ignoriert. Kontextfelder werden
    separat berechnet und togglen das Signal nicht."""
    closed = _closed_bars(candles)
    if len(closed) < 3:
        return TwoBarThrustSignal(
            signal=False, direction="", bear_body=0.0, bull_body_sum=0.0,
            close_above_bear_high=False, stop_price=None,
            support_confluence=False, ema_aligned=False, session_sweep=False,
            closed_bars_used=len(closed),
        )
    a, b, c = closed[-3], closed[-2], closed[-1]  # a=Bar[2] (alt), c=Bar[0] (neu)
    body = lambda x: abs(_c(x) - _o(x))
    bear_body = body(a)
    bull_body_sum = body(b) + body(c)
    is_bearish_a = _c(a) < _o(a)
    is_bullish_b = _c(b) > _o(b)
    is_bullish_c = _c(c) > _o(c)
    close_above = _c(c) > _h(a)
    pattern = is_bearish_a and is_bullish_b and is_bullish_c \
        and bull_body_sum > bear_body and close_above
    stop = min(_l(b), _l(c)) if pattern else None
    support_confluence = False
    if support_price is not None and support_price > 0:
        low_c = _l(c)
        support_confluence = abs(low_c - support_price) / support_price <= SUPPORT_TOLERANCE_PCT
    ema_aligned = ema20 is not None and _c(c) > ema20
    if sweep is None:
        sweep = bool(sweep_mss_flags(closed).get("liquidity_sweep", False))
    return TwoBarThrustSignal(
        signal=pattern,
        direction="bullish" if pattern else "",
        bear_body=bear_body,
        bull_body_sum=bull_body_sum,
        close_above_bear_high=close_above,
        stop_price=stop,
        support_confluence=support_confluence,
        ema_aligned=ema_aligned,
        session_sweep=bool(sweep),
        closed_bars_used=len(closed),
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


__all__ = ["TwoBarThrustSignal", "evaluate"]
