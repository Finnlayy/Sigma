"""
=========================================================
Datei:      sigma/core/math_engine.py
Zweck:      Geteilte Vektor-Mathematik — Sharpe, Sortino, clamp, NaN-Penalty.
            Degenerate / non-finite Werte werden mit -1e9 bestraft (GA-Gate).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Mathematik)
=========================================================

Nicht in den GA-Hot-Path eingehängt: GeneticOptimizer behält seine eigenen
DSR/Fitness-Formeln. Diese Funktionen sind die Library-API und ein
gemeinsamer Baustein für Scorecard / neue Templates.
"""
from __future__ import annotations

import math
from typing import Iterable, Union

import numpy as np

NAN_PENALTY = -1e9

Number = Union[int, float]


def clamp(value: Number, low: Number, high: Number) -> float:
    """Inclusive clamp. Non-finite input → ``low`` (fail-closed, no NaN leak)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float(low)
    if not math.isfinite(x):
        return float(low)
    return float(min(high, max(low, x)))


def nan_penalty(value: Number, penalty: float = NAN_PENALTY) -> float:
    """Map NaN / Inf / uncastable values to ``penalty`` (default -1e9)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float(penalty)
    if not math.isfinite(x):
        return float(penalty)
    return x


def _finite_returns(returns: Iterable[Number]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=float)
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


def sharpe(returns: Iterable[Number], periods_per_year: float = 365 * 24 * 4) -> float:
    """Sample Sharpe (ddof=1), annualisiert. Zu kurz / sd=0 → 0.0 (kein Fake)."""
    arr = _finite_returns(returns)
    if arr.size < 2:
        return 0.0
    sd = float(arr.std(ddof=1))
    if sd == 0.0:
        return 0.0
    return float(arr.mean() / sd * math.sqrt(float(periods_per_year)))


def sortino(returns: Iterable[Number], periods_per_year: float = 365 * 24 * 4) -> float:
    """Sortino; ohne Downside-Observations fällt auf Sharpe zurück (wie BacktestEngine)."""
    arr = _finite_returns(returns)
    if arr.size < 2:
        return 0.0
    neg = arr[arr < 0.0]
    if neg.size == 0:
        return sharpe(arr, periods_per_year)
    dvar = float(np.sum(neg * neg) / arr.size)
    dsd = math.sqrt(dvar)
    if dsd == 0.0:
        return 0.0
    return float(arr.mean() / dsd * math.sqrt(float(periods_per_year)))


def sharpe_or_penalty(returns: Iterable[Number],
                      periods_per_year: float = 365 * 24 * 4,
                      penalty: float = NAN_PENALTY) -> float:
    return nan_penalty(sharpe(returns, periods_per_year), penalty)


def sortino_or_penalty(returns: Iterable[Number],
                       periods_per_year: float = 365 * 24 * 4,
                       penalty: float = NAN_PENALTY) -> float:
    return nan_penalty(sortino(returns, periods_per_year), penalty)
