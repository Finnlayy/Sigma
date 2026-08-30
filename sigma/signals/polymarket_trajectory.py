"""
=========================================================
Datei:      sigma/signals/polymarket_trajectory.py
Zweck:      Term-Struktur (KB §7 Punkt 3+5+6): mu(T)-Kurve ueber
            T+1h/T+2h/T+4h/EOD, delta_mu/delta_T, Bias-Klassifikation,
            optimales Entry-Fenster T_opt ~ Expiry x 0,75, Spaet-
            Fenster-Sperre (< Expiry x 0,25 -> kein Entry).
            Fail-closed bei fehlenden Horizonten. Keine Orders.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Layer 0)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

HORIZONS_HOURS = ("1h", "2h", "4h", "EOD")
EOD_HOURS = 24.0

# Bias-Schwellen (KB §7): delta_mu pro Stunde als Dezimal (0.03 = 3 %/h)
DELTA_STRONG = 0.03
DELTA_WEAK = 0.01

STRONG_BULLISH = "STRONG_BULLISH"
BULLISH = "BULLISH"
CHOP = "CHOP"
BEARISH = "BEARISH"
STRONG_BEARISH = "STRONG_BEARISH"


@dataclass(frozen=True)
class TrajectoryResult:
    """Term-Struktur + Fenster. valid=False bei fehlenden Horizonten."""

    valid: bool
    reason: str
    mu_curve: Dict[str, float] = field(default_factory=dict)
    delta_mu_per_h: Optional[float] = None
    bias: str = ""
    t_opt_ts: Optional[float] = None
    remaining_frac: Optional[float] = None
    entry_allowed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def trajectory_from_quotes(
    quotes: Dict[str, float],
    *,
    horizons: Sequence[str] = HORIZONS_HOURS,
) -> TrajectoryResult:
    """mu(T)-Kurve aus Quoten je Horizont; delta_mu/delta_T = mittlere
    Steigung pro Stunde ueber die Kurve. Bias nach KB §7-Schwellen."""
    missing = [h for h in horizons if h not in quotes]
    if missing:
        return TrajectoryResult(False, f"missing_horizons:{','.join(missing)}")
    curve: Dict[str, float] = {}
    for h in horizons:
        v = float(quotes[h])
        if not 0.0 <= v <= 1.0:
            return TrajectoryResult(False, f"out_of_range:{h}")
        curve[h] = v
    hours = [_horizon_hours(h) for h in horizons]
    total_delta = curve[horizons[-1]] - curve[horizons[0]]
    total_hours = hours[-1] - hours[0]
    slope = total_delta / total_hours if total_hours > 0 else 0.0
    return TrajectoryResult(
        valid=True,
        reason="ok",
        mu_curve=curve,
        delta_mu_per_h=round(slope, 6),
        bias=classify_bias(slope),
    )


def classify_bias(delta_mu_per_h: float) -> str:
    """KB §7: > +3 %/h STRONG_BULLISH, > +1 %/h BULLISH, < -3 %/h
    STRONG_BEARISH, < -1 %/h BEARISH, sonst CHOP."""
    if delta_mu_per_h > DELTA_STRONG:
        return STRONG_BULLISH
    if delta_mu_per_h > DELTA_WEAK:
        return BULLISH
    if delta_mu_per_h < -DELTA_STRONG:
        return STRONG_BEARISH
    if delta_mu_per_h < -DELTA_WEAK:
        return BEARISH
    return CHOP


def optimal_entry_window(
    expiry_ts: float,
    now_ts: float,
) -> TrajectoryResult:
    """T_opt = Expiry x 0,75; Remaining < Expiry x 0,25 -> kein Entry mehr
    (Spaet-Fenster-Sperre). Fail-closed bei ungueltigen Zeiten."""
    if expiry_ts <= 0 or now_ts <= 0 or now_ts >= expiry_ts:
        return TrajectoryResult(False, "invalid_times")
    remaining = expiry_ts - now_ts
    remaining_frac = remaining / expiry_ts
    t_opt = expiry_ts * 0.75
    entry_allowed = remaining_frac >= 0.25
    return TrajectoryResult(
        valid=True,
        reason="ok",
        t_opt_ts=round(t_opt, 3),
        remaining_frac=round(remaining_frac, 6),
        entry_allowed=entry_allowed,
    )


def _horizon_hours(h: str) -> float:
    if h == "EOD":
        return EOD_HOURS
    return float(h.rstrip("h"))


__all__ = [
    "BEARISH", "BULLISH", "CHOP", "DELTA_STRONG", "DELTA_WEAK", "EOD_HOURS",
    "HORIZONS_HOURS", "STRONG_BEARISH", "STRONG_BULLISH", "TrajectoryResult",
    "classify_bias", "optimal_entry_window", "trajectory_from_quotes",
]
