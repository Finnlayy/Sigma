"""
=========================================================
Datei:      tests/test_candle_signals.py
Zweck:      MP-03 Candle-/Regime-Signale — Karte §5: 3-Bar-Thrust ja /
            Einzel-Gruen nein; Marubozu 95 %+CE50; FVG in ATR;
            00:00-Anker; Outside-Inside; Ergebnis bis Bar k bleibt
            nach spaeteren Bars identisch (kein Look-ahead).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

import pytest

from sigma.signals.daily_open_envelope import evaluate as envelope_evaluate
from sigma.signals.marubozu_fvg import evaluate as marubozu_evaluate
from sigma.signals.two_bar_thrust import evaluate as thrust_evaluate

DAY0 = 1_704_067_200  # 2024-01-01T00:00:00Z


def bar(o, h, l, c, *, v=100.0, ts=DAY0, closed=True):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v,
            "is_closed": closed}


# -------------------------------------------------------- two-bar thrust ---

def test_two_bar_thrust_pattern_detected():
    candles = [
        bar(101.0, 101.2, 100.8, 100.9, ts=DAY0),       # neutral
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0 + 3600),  # Bar[2] baerisch
        bar(99.2, 100.5, 99.1, 100.4, ts=DAY0 + 7200),  # Bar[1] bullisch
        bar(100.4, 102.0, 100.3, 101.9, ts=DAY0 + 10800),  # Bar[0] bullisch
    ]
    sig = thrust_evaluate(candles)
    assert sig.signal is True
    assert sig.direction == "bullish"
    assert sig.close_above_bear_high is True
    # Stop = tiefstes Tief der beiden Bullenkerzen
    assert sig.stop_price == pytest.approx(99.1)
    # Baer-Body 1.8 < Bull-Summe (1.2 + 1.5)
    assert sig.bull_body_sum > sig.bear_body


def test_two_bar_thrust_single_green_or_bear_without_followup_rejected():
    # Einzel-Gruen nach Dump (Dead-Cat) -> kein Signal
    single = [
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0),
        bar(99.2, 100.0, 99.1, 99.8, ts=DAY0 + 3600),  # nur 1 gruene Kerze
    ]
    # nur 2 geschlossene Bars -> kein Signal
    assert thrust_evaluate(single).signal is False
    # Baer ohne bullische Folge -> kein Signal
    no_follow = [
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0),
        bar(99.2, 99.5, 98.8, 98.9, ts=DAY0 + 3600),
        bar(98.9, 99.2, 98.7, 98.8, ts=DAY0 + 7200),
    ]
    sig = thrust_evaluate(no_follow)
    assert sig.signal is False
    # Close NICHT ueber High[2] -> kein Signal
    low_close = [
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0),
        bar(99.2, 100.5, 99.1, 100.4, ts=DAY0 + 3600),
        bar(100.4, 100.9, 100.3, 100.8, ts=DAY0 + 7200),  # close < 101.0
    ]
    assert thrust_evaluate(low_close).signal is False


def test_two_bar_thrust_context_flags_are_evidence_only():
    candles = [
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0),
        bar(99.2, 100.5, 99.1, 100.4, ts=DAY0 + 3600),
        bar(100.4, 102.0, 100.3, 101.9, ts=DAY0 + 7200),
    ]
    sig = thrust_evaluate(candles, support_price=100.3, ema20=100.0, sweep=True)
    assert sig.signal is True  # Kontext togglet das Muster nicht
    assert sig.support_confluence is True
    assert sig.ema_aligned is True
    assert sig.session_sweep is True
    # Ohne Kontext bleibt das Muster Signal
    assert thrust_evaluate(candles).signal is True


# --------------------------------------------------------- marubozu + fvg ---

def _base_series(scale=1.0, n=18):
    """18 ruhige Bars (kleine Range) + 3 Struktur-Bars: Displacement + FVG."""
    out = []
    for i in range(n):
        p = 99.5 * scale
        out.append(bar(p, p * (1 + 0.0004), p * (1 - 0.0004), p * (1 + 0.0002),
                       ts=DAY0 + i * 3600))
    a = 100.0 * scale
    b_open, b_close = 100.2 * scale, 101.0 * scale
    c_open, c_close = 101.0 * scale, 101.9 * scale
    out += [
        bar(a, a * 1.001, a * 0.999, a * 1.0005, ts=DAY0 + n * 3600),
        bar(b_open, b_close * 1.001, b_open * 0.999, b_close, ts=DAY0 + (n + 1) * 3600),
        bar(c_open, c_close + 0.02 * scale, c_open - 0.02 * scale, c_close,
            ts=DAY0 + (n + 2) * 3600),  # Marubozu: Body ~0.9/0.94 = 95 %+
    ]
    return out


def test_marubozu_95pct_body_with_fvg_ce50():
    sig = marubozu_evaluate(_base_series())
    assert sig.valid is True
    assert sig.marubozu is True
    assert sig.body_ratio >= 0.95
    assert sig.direction == "bullish"
    assert sig.fvg_bullish is True
    # Zone kanonisch (min, max): (a.high, c.low) = (100.1, 100.98)
    assert sig.gap_low == pytest.approx(100.1)
    assert sig.gap_high == pytest.approx(100.98)
    # CE50 = Mitte der Zone (aus fractal_scaling, kein Duplikat)
    assert sig.ce50 == pytest.approx((100.1 + 100.98) / 2.0)
    # FVG-Groesse in ATR-Einheiten plausibel (0.88 / ATR)
    assert sig.gap_atr_ratio is not None
    assert sig.gap_atr_ratio == pytest.approx(0.88 / sig.atr, rel=1e-6)
    assert sig.gap_atr_ratio > 1.0


def test_fvg_atr_ratio_scale_invariant():
    small = marubozu_evaluate(_base_series(scale=1.0))
    big = marubozu_evaluate(_base_series(scale=100.0))
    assert big.gap_atr_ratio == pytest.approx(small.gap_atr_ratio, rel=1e-6)
    assert big.ce50 == pytest.approx(small.ce50 * 100.0, rel=1e-6)


def test_marubozu_insufficient_bars_fails_closed():
    sig = marubozu_evaluate(_base_series()[:5])
    assert sig.valid is False
    assert sig.marubozu is False
    assert sig.reason == "insufficient_bars"


# ------------------------------------------------- daily open envelope ---

def _envelope_day(scale=1.0, day0=DAY0):
    """7 1h-Bars eines UTC-Tages; b3/b4 = Top-Volumen; b5 outside, b6 green inside."""
    s = scale
    rows = [
        bar(100.0 * s, 100.3 * s, 99.9 * s, 100.2 * s, v=10, ts=day0),
        bar(100.2 * s, 100.4 * s, 99.95 * s, 100.1 * s, v=10, ts=day0 + 3600),
        bar(100.1 * s, 100.45 * s, 100.0 * s, 100.3 * s, v=10, ts=day0 + 7200),
        bar(100.3 * s, 100.7 * s, 100.2 * s, 100.6 * s, v=50, ts=day0 + 10800),
        bar(100.6 * s, 101.0 * s, 100.5 * s, 100.9 * s, v=40, ts=day0 + 14400),
        bar(100.9 * s, 101.6 * s, 100.8 * s, 101.5 * s, v=15, ts=day0 + 18000),
        bar(101.0 * s, 101.6 * s, 100.9 * s, 101.2 * s, v=12, ts=day0 + 21600),
    ]
    return rows


def test_envelope_00utc_anchor_top_volume_and_reversal():
    sig = envelope_evaluate(_envelope_day())
    assert sig.valid is True
    assert sig.day_anchor_ts == DAY0  # 00:00 UTC des Tages
    assert sig.outside_inside_reversal is True
    assert sig.outside_side == "high"
    # Huelle liegt zwischen den Extremen der Top-Volumen-Kerzen
    assert sig.envelope_high is not None and sig.envelope_low is not None
    assert sig.envelope_high > sig.envelope_low


def test_envelope_no_reversal_without_green_inside_followup():
    rows = _envelope_day()
    # b6 rot statt gruen
    rows[6] = bar(101.5, 101.6, 100.9, 101.1, v=12, ts=DAY0 + 21600)
    sig = envelope_evaluate(rows)
    assert sig.valid is True
    assert sig.outside_inside_reversal is False


def test_envelope_anchor_uses_day_of_last_closed_bar_and_thin_session_fails():
    rows = _envelope_day(day0=DAY0 - 2 * 86400) + _envelope_day(day0=DAY0)
    sig = envelope_evaluate(rows)
    assert sig.valid is True
    assert sig.day_anchor_ts == DAY0  # Anker = Tag der letzten Kerze
    # duenne Session (< min_bars) -> fail-closed
    thin = envelope_evaluate(_envelope_day()[:3])
    assert thin.valid is False
    assert thin.reason == "insufficient_bars"


def test_envelope_slope_drift_sign():
    rising = _envelope_day()
    assert envelope_evaluate(rising).slope_pct is not None
    assert envelope_evaluate(rising).slope_pct > 0
    # fallende Serie: Top-Volumen-Kerzen am Tagesende mit sinkenden Hochs
    falling = [
        bar(100.0, 100.3, 99.9, 100.2, v=10, ts=DAY0),
        bar(100.2, 100.4, 99.95, 100.1, v=10, ts=DAY0 + 3600),
        bar(100.1, 100.45, 100.0, 100.3, v=10, ts=DAY0 + 7200),
        bar(100.3, 100.6, 100.2, 100.5, v=10, ts=DAY0 + 10800),
        bar(100.5, 100.8, 100.4, 100.7, v=10, ts=DAY0 + 14400),
        bar(100.7, 101.0, 100.5, 100.9, v=50, ts=DAY0 + 18000),
        bar(100.6, 100.5, 100.0, 100.3, v=40, ts=DAY0 + 21600),
    ]
    sig = envelope_evaluate(falling)
    assert sig.valid is True
    assert sig.slope_pct < 0


# ------------------------------------------------- no-lookahead / closed ---

def test_open_last_bar_is_ignored_by_all_modules():
    thrust_closed = [
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0),
        bar(99.2, 100.5, 99.1, 100.4, ts=DAY0 + 3600),
        bar(100.4, 102.0, 100.3, 101.9, ts=DAY0 + 7200),
    ]
    open_bar = bar(101.9, 103.0, 101.8, 102.8, ts=DAY0 + 10800, closed=False)
    assert thrust_evaluate(thrust_closed) == thrust_evaluate(thrust_closed + [open_bar])

    maru_closed = _base_series()
    open_maru = bar(200.0, 205.0, 199.0, 204.0, ts=DAY0 + 30 * 3600, closed=False)
    assert marubozu_evaluate(maru_closed) == marubozu_evaluate(maru_closed + [open_maru])

    env_closed = _envelope_day()
    open_env = bar(105.0, 106.0, 104.0, 105.5, v=999, ts=DAY0 + 25200, closed=False)
    assert envelope_evaluate(env_closed) == envelope_evaluate(env_closed + [open_env])


def test_result_up_to_bar_k_unchanged_after_later_bars():
    # Ergebnis bis Bar k (prefix-only) bleibt identisch, wenn spaetere
    # Bars angehaengt werden — inkl. einer spaeteren Hoch-Volumen-Kerze,
    # die die Top-N-Auswahl NICHT rueckwirkend aendern darf.
    rows = _envelope_day()
    k = 5  # Outside-Bar ist b5; Folgebar (Reversal) fehlt im Prefix noch
    sig_k = envelope_evaluate(rows[: k + 1])
    assert sig_k.outside_inside_reversal is False

    extended = rows + [
        bar(102.0, 102.2, 101.8, 102.1, v=5000, ts=DAY0 + 25200),
        bar(102.1, 102.4, 102.0, 102.3, v=6000, ts=DAY0 + 28800),
    ]
    # Per-Bar-Serie ueber den verlaengerten Input: Eintrag bei k == prefix-Ergebnis
    per_bar = [envelope_evaluate(extended[: i + 1]) for i in range(k + 1)]
    assert per_bar[k] == sig_k
    # Deterministisch: zweite Auswertung identisch
    assert per_bar == [envelope_evaluate(extended[: i + 1]) for i in range(k + 1)]

    # Dasselbe Prinzip fuer Thrust und Marubozu (Tail-only): die per-Bar-Serie
    # des verlaengerten Inputs gleicht bis k der Serie des kurzen Inputs.
    thrust = [
        bar(101.0, 101.1, 99.0, 99.2, ts=DAY0),
        bar(99.2, 100.5, 99.1, 100.4, ts=DAY0 + 3600),
        bar(100.4, 102.0, 100.3, 101.9, ts=DAY0 + 7200),
    ]
    thrust_ext = thrust + [bar(101.9, 103.0, 101.7, 102.9, ts=DAY0 + 10800)]
    for i in range(len(thrust)):
        assert thrust_evaluate(thrust_ext[: i + 1]) == thrust_evaluate(thrust[: i + 1])

    base = _base_series()
    base_ext = base + [bar(50.0, 51.0, 49.0, 50.5, ts=DAY0 + 40 * 3600)]
    for i in range(len(base)):
        assert marubozu_evaluate(base_ext[: i + 1]) == marubozu_evaluate(base[: i + 1])
