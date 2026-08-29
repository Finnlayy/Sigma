"""
=========================================================
Datei:      sigma/signals/timeframe_ladder.py
Zweck:      HTF/LTF-Paarung — 4–6x Bias (Elder) und 12–16x Execution (ICT).
            Konstanten hier, nicht an Call-Sites. Offene Paare → reject.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Schema) / Jaune (Library)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

from app.tv.interval_map import to_minutes

Interval = Union[int, str]

# Bias screen (Elder Triple Screen / MTF) — evidence: strong tradition
BIAS_RATIO_MIN = 4.0
BIAS_RATIO_MAX = 6.0
# Execution ladder (ICT) — evidence: weak but consistent; paper until H3
EXEC_RATIO_MIN = 12.0
EXEC_RATIO_MAX = 16.0

# Canonical minutes: M1=1, M5=5, M15=15, H1=60, H4=240, D=1440
BIAS_PAIRS: Tuple[Tuple[int, int], ...] = (
    (60, 15),     # H1 ← M15  = 4x
    (240, 60),    # H4 ← H1   = 4x
    (1440, 240),  # D  ← H4   = 6x
)
# M15 ← M5 = 3x is below the bias band — not a sole pair
EXEC_PAIRS: Tuple[Tuple[int, int], ...] = (
    (60, 5),      # H1 → M5   = 12x
    (15, 1),      # M15 → M1  = 15x
    (240, 15),    # H4 → M15  = 16x
)


@dataclass(frozen=True)
class TimeframePair:
    bias_minutes: int
    exec_minutes: int
    ratio: float
    kind: str  # "bias" | "execution"

    def to_dict(self) -> dict:
        return {
            "bias_minutes": self.bias_minutes,
            "exec_minutes": self.exec_minutes,
            "ratio": self.ratio,
            "kind": self.kind,
        }


def _ratio(higher: int, lower: int) -> float:
    if lower <= 0:
        return 0.0
    return float(higher) / float(lower)


def _lookup(pairs: Tuple[Tuple[int, int], ...], higher: int, lower: int) -> bool:
    return (higher, lower) in pairs


def bias_tf(exec_tf: Interval) -> Optional[int]:
    """HTF bias minutes for an execution TF, if it sits on a 4–6x pair."""
    exec_m = to_minutes(exec_tf)
    for higher, lower in BIAS_PAIRS:
        if lower == exec_m:
            return higher
    return None


def exec_tf(bias_tf_value: Interval) -> Optional[int]:
    """LTF execution minutes for a bias TF on the 4–6x ladder."""
    bias_m = to_minutes(bias_tf_value)
    for higher, lower in BIAS_PAIRS:
        if higher == bias_m:
            return lower
    return None


def execution_ladder_tf(bias_tf_value: Interval) -> Optional[int]:
    """LTF minutes on the 12–16x ICT ladder (H3 default off)."""
    bias_m = to_minutes(bias_tf_value)
    for higher, lower in EXEC_PAIRS:
        if higher == bias_m:
            return lower
    return None


def classify_pair(higher: Interval, lower: Interval) -> Optional[TimeframePair]:
    h, lo = to_minutes(higher), to_minutes(lower)
    if h <= lo:
        return None
    ratio = _ratio(h, lo)
    if _lookup(BIAS_PAIRS, h, lo) or BIAS_RATIO_MIN <= ratio <= BIAS_RATIO_MAX:
        if _lookup(BIAS_PAIRS, h, lo) or (BIAS_RATIO_MIN <= ratio <= BIAS_RATIO_MAX):
            if _lookup(BIAS_PAIRS, h, lo):
                return TimeframePair(h, lo, ratio, "bias")
            if abs(ratio - 3.0) < 0.15:
                return None  # M15/M5 ≈ 3x — reject as sole pair
            return TimeframePair(h, lo, ratio, "bias")
    if _lookup(EXEC_PAIRS, h, lo) or EXEC_RATIO_MIN <= ratio <= EXEC_RATIO_MAX:
        return TimeframePair(h, lo, ratio, "execution")
    return None


def reject_unpaired(higher: Interval, lower: Interval) -> bool:
    """True if the pair is outside both allowed bands."""
    return classify_pair(higher, lower) is None


def session_exec_pair(session: str, *, use_ict_ladder: bool = False) -> TimeframePair:
    """NY: H1/M15 bias (or H1/M5 ICT). London: H1 fade on M15 (or M5 ICT)."""
    name = (session or "").upper()
    if use_ict_ladder:
        if "LONDON" in name:
            return TimeframePair(60, 5, 12.0, "execution")
        return TimeframePair(60, 5, 12.0, "execution")
    if "LONDON" in name:
        return TimeframePair(60, 15, 4.0, "bias")
    if "NEW_YORK" in name or "NY" in name:
        return TimeframePair(60, 15, 4.0, "bias")
    return TimeframePair(240, 60, 4.0, "bias")
