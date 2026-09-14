"""
=========================================================
Datei:      tests/test_power_phasor.py
Zweck:      MP-04 Power/Phasor — Karte §5: Marubozu eta~1 Q~0
            P_norm~S_norm; Dochtkerze eta<0.3, S>P; cos_phi_path
            +1/0/-1; flache Bars endlich; Phasor Amplitude+Winkel
            deterministisch; Resonanz konstruktiv/Dip; Climax-Tag.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math) / Blanche (Feature)
=========================================================
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from sigma.signals.hilbert_phasor import hilbert_phasor, phasor_series
from sigma.signals.mtf_resonance import (
    CONSTRUCTIVE_RESONANCE,
    DIP_CHARGING,
    resonance,
)
from sigma.signals.power_triangle import (
    EXPLOSIVE_EXPANSION,
    SOLID_TREND_EXPANSION,
    VOLATILITY_CLIMAX,
    WICK_REJECTION,
    classify_bar,
    cos_phi_bar,
    cos_phi_path,
    price_action_physics,
)


def candle(o, h, l, c, *, v=100.0, ts=0, closed=True):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v,
            "is_closed": closed}


def marubozu_series(n=20):
    """Ruhige Vorlauf-Bars + reine Marubozu-Kerze (keine Dochte)."""
    rows = [candle(100.0 + i * 0.01, 100.01 + i * 0.01, 99.99 + i * 0.01,
                   100.005 + i * 0.01, ts=1 + i) for i in range(n)]
    rows.append(candle(100.2, 100.6, 100.2, 100.6, ts=1 + n))  # body == range
    return rows


def wick_series(n=20):
    """Kreuzkerze mit langen Dochten, kleinem Body."""
    rows = [candle(100.0 + i * 0.01, 100.01 + i * 0.01, 99.99 + i * 0.01,
                   100.005 + i * 0.01, ts=1 + i) for i in range(n)]
    rows.append(candle(100.3, 101.3, 99.6, 100.31, ts=1 + n))
    return rows


# ------------------------------------------------------- power triangle ---

def test_marubozu_eta_near_one_q_zero_p_norm_equals_s_norm():
    bars = price_action_physics(marubozu_series())
    last = bars[-1]
    assert last.eta_efficiency == pytest.approx(1.0, abs=1e-6)
    assert last.q_norm == pytest.approx(0.0, abs=1e-6)
    assert last.q_upper_norm == pytest.approx(0.0, abs=1e-6)
    assert last.q_lower_norm == pytest.approx(0.0, abs=1e-6)
    assert last.p_norm == pytest.approx(last.s_norm, rel=1e-6)
    assert last.cos_phi_bar == pytest.approx(1.0, abs=1e-6)
    assert last.p_norm_signed > 0
    tags = classify_bar(last)
    assert SOLID_TREND_EXPANSION in tags  # weitere Tags (z. B. Explosiv) erlaubt


def test_wick_candle_eta_small_q_ordered_s_greater_p():
    bars = price_action_physics(wick_series())
    last = bars[-1]
    assert last.eta_efficiency < 0.30
    assert last.q_upper_norm > last.q_lower_norm  # laengerer oberer Docht
    assert last.s_norm > last.p_norm
    assert last.cos_phi_bar > 0  # kleiner gruener Body -> positiv
    tags = classify_bar(last)
    assert WICK_REJECTION in tags


def test_cos_phi_path_monotone_roundtrip_and_down():
    up = [100.0 + i for i in range(11)]  # 10 Steigungen
    assert cos_phi_path(up, window=10) == pytest.approx(1.0)
    down = [100.0 - i for i in range(11)]
    assert cos_phi_path(down, window=10) == pytest.approx(-1.0)
    roundtrip = [100.0, 101.0, 102.0, 101.0, 100.0]
    assert cos_phi_path(roundtrip, window=4) == pytest.approx(0.0)
    # True-Range-Variante
    highs = [x + 1.0 for x in up]
    lows = [x - 1.0 for x in up]
    assert cos_phi_path(up, window=10, use_true_range=True,
                        high=highs, low=lows) == pytest.approx(
        (up[-1] - up[-11]) / sum(
            max(h - l, abs(h - p), abs(l - p))
            for h, l, p in zip(highs[-10:], lows[-10:], up[-11:-1])
        ), rel=1e-6)


def test_flat_bars_stay_finite_no_nan():
    flat = [candle(10.0, 10.0, 10.0, 10.0, ts=i) for i in range(20)]
    bars = price_action_physics(flat)
    assert len(bars) == 20
    for b in bars:
        assert all(math.isfinite(x) for x in (
            b.true_range, b.atr, b.s_norm, b.p_norm, b.p_norm_signed,
            b.q_norm, b.q_upper_norm, b.q_lower_norm, b.q_bias,
            b.eta_efficiency, b.cos_phi_bar))
        assert b.eta_efficiency == 0.0
        assert b.cos_phi_bar == 0.0
    assert cos_phi_bar(flat[-1]) == 0.0
    assert cos_phi_path([10.0] * 10, window=5) == 0.0
    # leerer/zu kurzer Pfad -> 0.0 (fail-closed)
    assert cos_phi_path([10.0, 10.5], window=20) == 0.0


def test_volatility_climax_classification():
    rows = [candle(100.0 + i * 0.01, 100.01 + i * 0.01, 99.99 + i * 0.01,
                   100.005 + i * 0.01, ts=1 + i) for i in range(20)]
    # Explosive Kerze: Body > 1.2 * ATR, Spanne > 2.0 * ATR
    rows.append(candle(100.3, 103.5, 99.5, 103.4, ts=21))
    bars = price_action_physics(rows)
    last = bars[-1]
    assert last.s_norm > 2.0
    assert last.p_norm > 1.2
    tags = classify_bar(last)
    assert VOLATILITY_CLIMAX in tags
    assert EXPLOSIVE_EXPANSION in tags


# --------------------------------------------------------- hilbert phasor ---

def test_phasor_sine_amplitude_stable_angle_rotates_uniformly():
    prices = [100.0 + 5.0 * math.sin(2 * math.pi * i / 24) for i in range(300)]
    amps = [hilbert_phasor(prices[: k + 1]).amplitude for k in range(120, 300)]
    assert min(amps) > 3.0 and max(amps) < 6.5  # stabil um 5.0 (Amplitude)
    # Winkel dreht gleichmaessig: ueber 24 Bars genau eine Umdrehung (2pi),
    # monoton
    angs = [hilbert_phasor(prices[: k + 1]).angle_deg for k in range(150, 175)]
    unw = np.unwrap([math.radians(a) for a in angs])
    assert unw[-1] - unw[0] == pytest.approx(-2 * math.pi, abs=0.01)
    assert bool((np.diff(unw) < 0).all())


def test_phasor_deterministic_and_flat_safe():
    prices = [100.0 + 5.0 * math.sin(2 * math.pi * i / 24) for i in range(100)]
    a = hilbert_phasor(prices)
    b = hilbert_phasor(prices)
    assert a == b  # deterministisch
    flat = hilbert_phasor([10.0] * 60)
    assert flat.amplitude == 0.0
    assert flat.angle_deg == 0.0
    assert hilbert_phasor([]).amplitude == 0.0
    # prefix-only: phasor_series-Eintrag bei k == phasor(prefix k)
    series = phasor_series(prices)
    assert series[90] == hilbert_phasor(prices[:91])


# --------------------------------------------------------- mtf resonance ---

def test_resonance_constructive_and_dip():
    u = hilbert_phasor([100 + 5 * math.sin(2 * math.pi * i / 24) for i in range(60)])
    i_same = hilbert_phasor([100 + 5 * math.sin(2 * math.pi * i / 24) for i in range(60)])
    r = resonance(u, i_same)
    assert r.resonance == pytest.approx(1.0, abs=0.02)
    assert r.state == CONSTRUCTIVE_RESONANCE
    # gegenlaeufig: LTF invertiert (180 Grad) -> resonance ~ -1
    i_inv = hilbert_phasor([100 - 5 * math.sin(2 * math.pi * i / 24) for i in range(60)])
    r2 = resonance(u, i_inv, htf_bullish=True, ltf_bearish=True)
    assert r2.resonance < -0.5
    assert r2.state == DIP_CHARGING
    # ohne Kontext-Flags bleibt DIP_NEUTRAL (kein automatisches Dip-Tag)
    r3 = resonance(u, i_inv)
    assert r3.resonance < -0.5
    assert r3.state != DIP_CHARGING


def test_resonance_uses_conjugate_product_not_sum():
    # U = (1,0), I = (1,0): S = U*conj(I) = 1 -> resonance 1.
    # Naive Winkelsumme waere 0 Grad genauso — hier mit 45 Grad Differenz:
    from sigma.signals.hilbert_phasor import Phasor

    u = Phasor(i=1.0, q=0.0, amplitude=1.0, angle_deg=0.0)
    i45 = Phasor(i=math.sqrt(2) / 2, q=math.sqrt(2) / 2, amplitude=1.0, angle_deg=45.0)
    r = resonance(u, i45)
    assert r.resonance == pytest.approx(math.cos(math.radians(45)), abs=1e-6)
    assert r.delta_phi_deg == pytest.approx(-45.0, abs=1e-4)
    # delta_phi = phi_U - phi_I (Differenz, keine Summe: 0+45=45 waere falsch)
    assert abs(abs(r.delta_phi_deg) - 45.0) < 1e-6
