"""
=========================================================
Datei:      sigma/signals/hilbert_phasor.py
Zweck:      Deterministische I/Q-Approximation (KB §9.3): I = vom
            SMA-Drift bereinigter Preis, Q = geglaettete Preisdifferenz
            (90°-Quadratur) mit Equal-Power-Skalierung auf die
            I-RMS. Nur numpy/math — keine externen DSP-Bibliotheken.
            Amplitude = sqrt(I^2+Q^2), Winkel = atan2(Q,I) in Grad.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

import numpy as np

DEFAULT_SMOOTH = 4
DEFAULT_DETREND = 48
EPS = 1e-9


@dataclass(frozen=True)
class Phasor:
    """Ein Zeiger: I (In-Phase), Q (Quadratur), Amplitude, Winkel in Grad."""

    i: float
    q: float
    amplitude: float
    angle_deg: float

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def hilbert_phasor(
    prices: Sequence[float],
    smooth: int = DEFAULT_SMOOTH,
    detrend: int = DEFAULT_DETREND,
) -> Phasor:
    """Letzter Phasor einer Preisserie (prefix-only, deterministisch):
    I = Preis minus lokaler SMA-Mittelwert; Q = SMA(Delta Preis), skaliert
    auf gleiche RMS-Leistung wie I (stabile Amplitude bei periodischen
    Serien; Winkel dreht gleichmaessig). Leere/kurze Serie -> Null-Phasor."""
    if smooth < 1 or detrend < 1:
        raise ValueError("smooth und detrend muessen >= 1 sein")
    s = np.asarray([float(x) for x in prices], dtype=float)
    n = int(s.size)
    if n < 2:
        return Phasor(i=0.0, q=0.0, amplitude=0.0, angle_deg=0.0)
    w = min(detrend, n)
    i_comp = float(s[-1] - np.mean(s[-w:]))
    deltas = np.diff(s)
    qw = min(smooth, int(deltas.size))
    q_raw = float(np.mean(deltas[-qw:])) if qw > 0 else 0.0
    i_win = s[-w:] - np.mean(s[-w:])
    q_win = deltas[-w:]
    rms_i = float(np.sqrt(np.mean(i_win ** 2)))
    rms_q = float(np.sqrt(np.mean(q_win ** 2))) if q_win.size else 0.0
    scale = (rms_i / rms_q) if rms_q > EPS else 0.0
    q_comp = q_raw * scale
    amplitude = math.hypot(i_comp, q_comp)
    angle = math.degrees(math.atan2(q_comp, i_comp))
    return Phasor(
        i=round(i_comp, 10),
        q=round(q_comp, 10),
        amplitude=round(amplitude, 10),
        angle_deg=round(angle, 6),
    )


def phasor_series(
    prices: Sequence[float],
    smooth: int = DEFAULT_SMOOTH,
    detrend: int = DEFAULT_DETREND,
) -> List[Phasor]:
    """Phasor je Zeitpunkt (prefix-only: Ergebnis bis k haengt nur von
    Preisen <= k ab; deterministisch)."""
    return [
        hilbert_phasor(list(prices[: k + 1]), smooth=smooth, detrend=detrend)
        for k in range(len(prices))
    ]


__all__ = ["DEFAULT_DETREND", "DEFAULT_SMOOTH", "EPS", "Phasor",
           "hilbert_phasor", "phasor_series"]
