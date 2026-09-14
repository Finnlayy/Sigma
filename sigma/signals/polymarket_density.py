"""
=========================================================
Datei:      sigma/signals/polymarket_density.py
Zweck:      Implizite Dichte aus Polymarket-Strikes (KB §7):
            Bin-Wahrscheinlichkeiten (Breeden-Litzenberger-Analogie
            fuer binaere Leitern), Erwartungswert mu, Korridor,
            konservatives Platt-Scaling. Fail-closed bei fehlenden
            oder ungeordneten Daten. Kein Netz, keine Signale-Erfindung.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Layer 0)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

PLATT_A_DEFAULT = 1.0   # konservativ: Identitaet, bis Brier-Abgleich vorliegt
PLATT_B_DEFAULT = 0.0


@dataclass(frozen=True)
class PriceBin:
    """Wahrscheinlichkeit eines Preis-Bins zwischen zwei Strikes."""

    strike_low: float
    strike_high: float
    prob: float
    mid: float

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class DensityResult:
    """Implizite Dichte: Bins, mu, Korridor, kalibrierte Werte."""

    valid: bool
    reason: str
    bins: List[PriceBin] = field(default_factory=list)
    mu: Optional[float] = None
    mu_calibrated: Optional[float] = None
    corridor_low: Optional[float] = None
    corridor_high: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "bins": [b.to_dict() for b in self.bins],
            "mu": self.mu,
            "mu_calibrated": self.mu_calibrated,
            "corridor_low": self.corridor_low,
            "corridor_high": self.corridor_high,
        }


def density_from_ladder(
    strikes: Sequence[float],
    yes_prices: Sequence[float],
    *,
    platt_a: float = PLATT_A_DEFAULT,
    platt_b: float = PLATT_B_DEFAULT,
) -> DensityResult:
    """Bin-Dichten aus geordneten Strikes + Yes-Preisen (kumulierte
    Wahrscheinlichkeit P(Preis >= Strike)). Bin [K_i, K_{i+1}) =
    prob(K_i) - prob(K_{i+1}); unteres/oberes Rand-Bin symmetrisch
    ergaenzt. mu = Summe(mid x prob). Platt-Scaling verschiebt nur,
    erfindet nie Signale (Default: Identitaet)."""
    strikes_l = [float(x) for x in strikes]
    yes_l = [float(x) for x in yes_prices]
    if len(strikes_l) < 2 or len(yes_l) != len(strikes_l):
        return DensityResult(False, "missing_or_mismatched_fields")
    if any(y < 0.0 or y > 1.0 for y in yes_l):
        return DensityResult(False, "price_out_of_range")
    if any(b <= a for a, b in zip(strikes_l, strikes_l[1:])):
        return DensityResult(False, "strikes_not_ordered")

    bins: List[PriceBin] = []
    step_low = strikes_l[1] - strikes_l[0]
    step_high = strikes_l[-1] - strikes_l[-2]
    # unteres Rand-Bin [-inf, K_0)
    p_low = 1.0 - yes_l[0]
    if p_low > 0:
        bins.append(PriceBin(strike_low=strikes_l[0] - step_low,
                             strike_high=strikes_l[0], prob=p_low,
                             mid=strikes_l[0] - step_low / 2.0))
    for i in range(len(strikes_l) - 1):
        p = yes_l[i] - yes_l[i + 1]
        if p < -1e-9:
            return DensityResult(False, "non_monotonic_cumulative")
        if p > 0:
            bins.append(PriceBin(strike_low=strikes_l[i], strike_high=strikes_l[i + 1],
                                 prob=p, mid=(strikes_l[i] + strikes_l[i + 1]) / 2.0))
    # oberes Rand-Bin [K_n, +inf)
    p_high = yes_l[-1]
    if p_high > 0:
        bins.append(PriceBin(strike_low=strikes_l[-1],
                             strike_high=strikes_l[-1] + step_high,
                             prob=p_high, mid=strikes_l[-1] + step_high / 2.0))
    total = sum(b.prob for b in bins)
    if total <= 0:
        return DensityResult(False, "zero_total_probability")
    # Normalisierung (numerische Sicherheit)
    bins = [PriceBin(b.strike_low, b.strike_high, b.prob / total, b.mid) for b in bins]
    mu = sum(b.mid * b.prob for b in bins)
    peak = max(bins, key=lambda b: b.prob)
    corridor_low = min(b.strike_low for b in bins if b is peak or _adjacent(b, peak, bins))
    corridor_high = max(b.strike_high for b in bins if b is peak or _adjacent(b, peak, bins))
    mu_cal = _platt_mid(bins, platt_a, platt_b)
    return DensityResult(
        valid=True,
        reason="ok",
        bins=bins,
        mu=round(mu, 4),
        mu_calibrated=round(mu_cal, 4),
        corridor_low=round(corridor_low, 4),
        corridor_high=round(corridor_high, 4),
    )


def _adjacent(b: PriceBin, peak: PriceBin, bins: Sequence[PriceBin]) -> bool:
    """Nachbar-Bin des Peak-Bins (direkt links oder rechts)."""
    return abs(b.strike_low - peak.strike_high) < 1e-9 or abs(
        b.strike_high - peak.strike_low
    ) < 1e-9


def _platt_mid(
    bins: Sequence[PriceBin], platt_a: float, platt_b: float
) -> float:
    """Kalibrierte mu: Platt-Scaling je Bin-Wahrscheinlichkeit (nur
    verschieben, nie erfinden). Default a=1, b=0 -> Identitaet."""
    mu = 0.0
    for b in bins:
        p = b.prob
        if platt_a != 1.0 or platt_b != 0.0:
            logit = math.log(max(p, 1e-12) / max(1.0 - p, 1e-12))
            p = 1.0 / (1.0 + math.exp(-(platt_a * logit + platt_b)))
        mu += b.mid * p
    return mu


__all__ = ["DensityResult", "PLATT_A_DEFAULT", "PLATT_B_DEFAULT", "PriceBin",
           "density_from_ladder"]
