"""
=========================================================
Datei:      sigma/signals/daily_open_envelope.py
Zweck:      00:00-UTC-Tagesanker + volumenverankerte Huellkurve
            (KB §9.1): Top-N-Volumen-Kerzen -> obere/untere
            Regressionslinie, Tages-Drift, Outside-Inside-Reversal.
            Deterministisch, kein ML. < N Bars -> fail-closed.
            Nur closed bars, kein Look-ahead (prefix-only).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

DAY_SECONDS = 86400
DEFAULT_MIN_BARS = 6
DEFAULT_TOP_N = 2


@dataclass(frozen=True)
class DailyEnvelopeSignal:
    """Signal der 00:00-UTC-Huellkurve. valid=False bei zu wenigen Bars
    (duenne Sessions -> fail-closed, kein Signal)."""

    valid: bool
    day_anchor_ts: int
    envelope_high: Optional[float]
    envelope_low: Optional[float]
    slope_pct: Optional[float]      # Drift der oberen Linie in %/h
    outside_inside_reversal: bool
    outside_side: str
    bars_used: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def evaluate(
    candles: Sequence[Mapping[str, Any]],
    *,
    top_n: int = DEFAULT_TOP_N,
    min_bars: int = DEFAULT_MIN_BARS,
) -> DailyEnvelopeSignal:
    """Wertet die Huellkurve des UTC-Tages der letzten geschlossenen Kerze
    aus. Alle Berechnungen sind prefix-only: Ergebnis bis Bar k aendert sich
    nicht durch spaetere Bars. Outside-Inside-Reversal: Bar k-1 schliesst
    ausserhalb, Bar k schliesst gruen und wieder innerhalb."""
    closed = _closed_bars(candles)
    if len(closed) < min_bars:
        return _fail(closed, reason="insufficient_bars")
    if top_n < 1:
        raise ValueError("top_n muss >= 1 sein")
    last = closed[-1]
    ts = _ts(last)
    day_start = ts - (ts % DAY_SECONDS)
    day_bars = [b for b in closed if _ts(b) >= day_start and _ts(b) < day_start + DAY_SECONDS]
    if len(day_bars) < min_bars:
        return _fail(closed, reason="insufficient_day_bars")

    # Prefix-only Huelle je Bar i (nur Bars 0..i des Tages)
    envs: List[Dict[str, float]] = []
    for i in range(len(day_bars)):
        envs.append(_envelope(day_bars[: i + 1], top_n=top_n, day_start=day_start))

    if len(day_bars) >= 2:
        prev = day_bars[-2]
        cur = day_bars[-1]
        prev_env = envs[-2]
        cur_env = envs[-1]
        prev_out_high = prev_env["high"] is not None and _c(prev) > prev_env["high"]
        prev_out_low = prev_env["low"] is not None and _c(prev) < prev_env["low"]
        prev_outside = prev_out_high or prev_out_low
        cur_inside = (
            cur_env["high"] is not None and cur_env["low"] is not None
            and cur_env["low"] <= _c(cur) <= cur_env["high"]
        )
        cur_green = _c(cur) > _o(cur)
        reversal = bool(prev_outside and cur_inside and cur_green)
        outside_side = "high" if prev_out_high else ("low" if prev_out_low else "")
    else:
        reversal = False
        outside_side = ""

    env = envs[-1]
    close_last = _c(last)
    slope_pct = None
    if env["slope"] is not None and env["high"] and env["high"] > 0:
        slope_pct = round(env["slope"] * 3600.0 / env["high"], 8)

    return DailyEnvelopeSignal(
        valid=True,
        day_anchor_ts=int(day_start),
        envelope_high=env["high"],
        envelope_low=env["low"],
        slope_pct=slope_pct,
        outside_inside_reversal=reversal,
        outside_side=outside_side,
        bars_used=len(day_bars),
        reason="ok",
    )


def _envelope(
    day_bars: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
    day_start: int,
) -> Dict[str, Optional[float]]:
    """Obere/untere Regressionslinie durch die Top-N-Volumen-Kerzen."""
    if not day_bars:
        return {"high": None, "low": None, "slope": None}
    ordered = sorted(
        enumerate(day_bars),
        key=lambda t: (float(t[1].get("v", t[1].get("volume", 0.0)) or 0.0), t[0]),
        reverse=True,
    )[:top_n]
    xs = [float(_ts(b) - day_start) for _, b in ordered]
    highs = [_h(b) for _, b in ordered]
    lows = [_l(b) for _, b in ordered]
    if len(ordered) < 2:
        return {
            "high": float(highs[0]),
            "low": float(lows[0]),
            "slope": 0.0,
        }
    x_last = float(_ts(day_bars[-1]) - day_start)
    high_at_last, slope_high = _linreg(xs, highs, x_last)
    low_at_last, _ = _linreg(xs, lows, x_last)
    return {"high": high_at_last, "low": low_at_last, "slope": slope_high}


def _linreg(
    xs: Sequence[float], ys: Sequence[float], x_target: float
) -> tuple:
    """Einfache lineare Regression (deterministisch, kein ML)."""
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom <= 0:
        return y_mean, 0.0
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
    intercept = y_mean - slope * x_mean
    return intercept + slope * x_target, slope


def _fail(closed: Sequence[Mapping[str, Any]], *, reason: str) -> DailyEnvelopeSignal:
    return DailyEnvelopeSignal(
        valid=False, day_anchor_ts=0, envelope_high=None, envelope_low=None,
        slope_pct=None, outside_inside_reversal=False, outside_side="",
        bars_used=len(closed), reason=reason,
    )


def _closed_bars(candles: Sequence[Mapping[str, Any]]) -> list:
    rows = list(candles)
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _ts(c: Mapping[str, Any]) -> float:
    ts = float(c.get("ts", c.get("time", 0.0)) or 0.0)
    if ts >= 1e12:  # Millisekunden -> Sekunden
        ts /= 1000.0
    return ts


def _o(c: Mapping[str, Any]) -> float:
    return float(c.get("o", c.get("open", 0.0)) or 0.0)


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


__all__ = ["DAY_SECONDS", "DEFAULT_MIN_BARS", "DEFAULT_TOP_N",
           "DailyEnvelopeSignal", "evaluate"]
