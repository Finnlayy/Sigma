"""
=========================================================
Datei:      sigma/signals/volatility_throttle.py
Zweck:      ATR-Ratio vs 24h-Baseline → SLEEP | NORMAL | AGGRESSIVE_HARVEST.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math) / Noir (Fee-Drag)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.quant.regime_detector import true_ranges

SLEEP_RATIO = 0.70
NORMAL_RATIO_MAX = 1.40
MAX_BOTS_CAP = 8
BASELINE_WINDOW = 96  # 96 * 15m ≈ 24h


@dataclass(frozen=True)
class ThrottleState:
    current_btc_atr: float
    baseline_btc_atr: float
    volatility_ratio: float
    allowed_concurrent_bots: int
    cooldown_seconds: int
    mode: str  # SLEEP | NORMAL | AGGRESSIVE_HARVEST

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class VolatilityThrottleGate:
    def __init__(
        self,
        baseline_window: int = BASELINE_WINDOW,
        max_bots_cap: int = MAX_BOTS_CAP,
    ) -> None:
        self.baseline_window = int(baseline_window)
        self.max_bots_cap = int(max_bots_cap)

    def evaluate(
        self,
        candles: Optional[Sequence[Mapping[str, Any]]],
        *,
        force_sleep: bool = False,
    ) -> ThrottleState:
        if force_sleep or not candles or len(candles) < 16:
            return ThrottleState(0.0, 0.0, 0.0, 0, 1800, "SLEEP")
        mapped = [_bar(c) for c in candles]
        trs = true_ranges(mapped)
        if len(trs) < 14:
            return ThrottleState(0.0, 0.0, 0.0, 0, 1800, "SLEEP")
        atr_series = _rma(trs, 14)
        if not atr_series:
            return ThrottleState(0.0, 0.0, 0.0, 0, 1800, "SLEEP")
        current = float(atr_series[-1])
        window = atr_series[-self.baseline_window:] if len(atr_series) >= 2 else atr_series
        baseline = sum(window) / len(window) if window else 0.0
        if baseline <= 0.0:
            return ThrottleState(current, 0.0, 0.0, 0, 1800, "SLEEP")
        ratio = current / baseline
        if ratio < SLEEP_RATIO:
            return ThrottleState(
                round(current, 6), round(baseline, 6), round(ratio, 4),
                0, 1800, "SLEEP",
            )
        if ratio <= NORMAL_RATIO_MAX:
            return ThrottleState(
                round(current, 6), round(baseline, 6), round(ratio, 4),
                3, 300, "NORMAL",
            )
        return ThrottleState(
            round(current, 6), round(baseline, 6), round(ratio, 4),
            self.max_bots_cap, 60, "AGGRESSIVE_HARVEST",
        )


def _bar(row: Mapping[str, Any]) -> Dict[str, float]:
    return {
        "ts": float(row.get("ts") or 0.0),
        "o": float(row.get("o", row.get("open", 0.0)) or 0.0),
        "h": float(row.get("h", row.get("high", 0.0)) or 0.0),
        "l": float(row.get("l", row.get("low", 0.0)) or 0.0),
        "c": float(row.get("c", row.get("close", 0.0)) or 0.0),
        "v": float(row.get("v", row.get("volume", 0.0)) or 0.0),
    }


def _rma(values: Sequence[float], period: int) -> List[float]:
    if len(values) < period or period < 1:
        return []
    seed = sum(values[:period]) / period
    out = [seed]
    alpha = 1.0 / period
    for v in values[period:]:
        seed = seed + alpha * (float(v) - seed)
        out.append(seed)
    return out
