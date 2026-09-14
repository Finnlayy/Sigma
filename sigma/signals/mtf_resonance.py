"""
=========================================================
Datei:      sigma/signals/mtf_resonance.py
Zweck:      MTF-Resonanz (KB §9.3/§9.6): S = U * conj(I) — Konjugat-
            produkt, NIEMALS U*I (Winkelsumme ist referenzpunktabhaengig
            und sinnlos). resonance = cos(delta_phi); >= 0.75 konstruktiv,
            < -0.5 bei HTF-bullisch/LTF-baerisch = DIP_CHARGING.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sigma.signals.hilbert_phasor import Phasor

RESONANCE_CONSTRUCTIVE = 0.75
RESONANCE_DIP = -0.5

CONSTRUCTIVE_RESONANCE = "CONSTRUCTIVE_RESONANCE"
DIP_CHARGING = "DIP_CHARGING"
NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class ResonanceResult:
    """Konjugatprodukt S = U*conj(I): resonance = cos(delta_phi)."""

    resonance: float
    delta_phi_deg: float
    s_real: float
    s_imag: float
    state: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def resonance(
    u: Phasor,
    i: Phasor,
    *,
    htf_bullish: Optional[bool] = None,
    ltf_bearish: Optional[bool] = None,
) -> ResonanceResult:
    """Berechnet S = U * conj(I) (KB §9.6). htf_bullish/ltf_bearish sind
    optionale Kontext-Flags fuer die DIP_CHARGING-Klassifikation; ohne sie
    wird die Richtung aus dem U-Winkel abgeleitet (0..180 Grad = bullisch,
    -180..0 = baerisch)."""
    u_i, u_q = float(u.i), float(u.q)
    i_i, i_q = float(i.i), float(i.q)
    # (a+jb) * (c-jd) = (ac+bd) + j(bc-ad)
    s_real = u_i * i_i + u_q * i_q
    s_imag = u_q * i_i - u_i * i_q
    mag = math.hypot(s_real, s_imag)
    if mag <= 1e-12:
        cos_dphi = 0.0
    else:
        cos_dphi = s_real / mag
    cos_dphi = max(-1.0, min(1.0, cos_dphi))
    delta_phi = math.degrees(math.atan2(s_imag, s_real))

    if htf_bullish is None:
        htf_bullish = 0.0 <= u.angle_deg <= 180.0
    if ltf_bearish is None:
        ltf_bearish = -180.0 <= i.angle_deg < 0.0 or i.angle_deg > 180.0

    if cos_dphi >= RESONANCE_CONSTRUCTIVE:
        state = CONSTRUCTIVE_RESONANCE
        reason = "cos_dphi >= 0.75"
    elif cos_dphi < RESONANCE_DIP and htf_bullish and ltf_bearish:
        state = DIP_CHARGING
        reason = "htf_bullish_ltf_bearish_antiphase"
    else:
        state = NEUTRAL
        reason = "no_state"

    return ResonanceResult(
        resonance=round(cos_dphi, 8),
        delta_phi_deg=round(delta_phi, 6),
        s_real=round(s_real, 10),
        s_imag=round(s_imag, 10),
        state=state,
        reason=reason,
    )


__all__ = [
    "CONSTRUCTIVE_RESONANCE", "DIP_CHARGING", "NEUTRAL", "RESONANCE_CONSTRUCTIVE",
    "RESONANCE_DIP", "ResonanceResult", "resonance",
]
