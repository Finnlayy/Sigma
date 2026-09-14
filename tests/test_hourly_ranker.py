"""
=========================================================
Datei:      tests/test_hourly_ranker.py
Zweck:      MP-05 Hourly Gate + Ranker — Karte §5: Minutenphasen
            2/20/50/57; 2. Scan gleiche Stunde block; +r+beta long,
            gegenlaeufig short, r<0/decoupled reject; Top-Gainer +
            RVOL + pos_EQ; thin/unlock/weekend sichtbar; Rotation
            erst naechste Stunde; Shadow-Plan; Orchestrator-ctx.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Orchestrierung) / Blanche (Scout)
=========================================================
"""
from __future__ import annotations

import math
import random

import pytest

from sigma.orchestration.hourly_screening_gate import (
    ACTIVE_EXECUTION,
    IDLE_WAIT,
    PRE_CLOSE_UNWIND,
    SCAN_AND_DEPLOY,
    HourlyScreeningGate,
    phase_for_minute,
)
from sigma.orchestration.master_orchestrator import MasterOrchestrator
from sigma.orchestration.shadow_plan import (
    NIGHT_MONITORING,
    NIGHT_QUARANTINE,
    NIGHT_SYNTHESIS,
    build_shadow_plan,
    night_phase,
)
from sigma.signals.high_beta_ranker import HighBetaRanker

HOUR = 3600
T0 = 1_704_067_200  # 2024-01-01T00:00:00Z


def bar(p, *, v=100.0, ts=0):
    return {"ts": ts, "o": p, "h": p * 1.001, "l": p * 0.999, "c": p, "v": v,
            "is_closed": True}


def _ret(b):
    return (b["c"] - b["o"]) / b["o"]


def btc_series(n=60, *, start=100.0, drift=0.002, seed=7):
    rng = random.Random(seed)
    out = []
    p = start
    for i in range(n):
        r = drift + rng.uniform(-0.004, 0.004)
        o = p
        c = p * (1 + r)
        out.append({"ts": T0 + i * HOUR, "o": o, "h": max(o, c) * 1.001,
                    "l": min(o, c) * 0.999, "c": c, "v": 100.0, "is_closed": True})
        p = c
    return out


def btc_ramp_flat(n=60, *, ramp=40, seed=7):
    """BTC: 40 Rampen-Bars (0.2 % + Rauschen) + 20 flache Zigzag-Bars
    (+-0.5 um Basis, letzte = Mitte). Erzeugt fuer Alt-Assets eine
    Konsolidierung mit intakter Return-Korrelation."""
    rng = random.Random(seed)
    out = []
    p = 100.0
    for i in range(ramp):
        r = 0.002 + rng.uniform(-0.003, 0.003)
        o = p
        c = p * (1 + r)
        out.append({"ts": T0 + i * HOUR, "o": o, "h": max(o, c) * 1.001,
                    "l": min(o, c) * 0.999, "c": c, "v": 100.0, "is_closed": True})
        p = c
    base = p
    for i in range(n - ramp):
        if i == n - ramp - 1:
            c = base  # letzte Bar = Band-Mitte
        elif i % 2 == 0:
            c = base + 0.5
        else:
            c = base - 0.5
        o = out[-1]["c"]
        out.append({"ts": T0 + (ramp + i) * HOUR, "o": o,
                    "h": max(o, c) * 1.001, "l": min(o, c) * 0.999,
                    "c": c, "v": 100.0, "is_closed": True})
    return out


def make_alt(btc, factor, *, seed=1, noise=0.0, vol_mult=1.0, trend=0.0):
    """Alt mit beta~factor; perf_24h steuerbar ueber trend. Nur die letzte
    (geschlossene) Bar traegt das erhoehte Volumen (RVOL-Vertrag)."""
    rng = random.Random(seed)
    out = []
    prev = 100.0
    for i, b in enumerate(btc):
        r = factor * _ret(b) + trend + noise * rng.uniform(-1, 1)
        prev = prev * (1 + r)
        v = 100.0 * vol_mult if i == len(btc) - 1 else 100.0
        out.append({"ts": b["ts"], "o": prev / (1 + r), "h": prev * 1.001,
                    "l": prev * 0.999, "c": prev, "v": v, "is_closed": True})
    return out


def consolidation_alt(btc, *, factor=2.7, seed=1, vol=2.5):
    """Alt: folgt BTC-Returns mit beta~factor durchgehend (auch in der
    flachen Zigzag-Phase von btc_ramp_flat) -> Konsolidierung mit intakter
    Korrelation; pos_EQ ~0.5, letzte Bar mit erhoehtem Volumen."""
    rng = random.Random(seed)
    out = []
    prev = 100.0
    for i, b in enumerate(btc):
        r = factor * _ret(b)
        prev = prev * (1 + r)
        v = 100.0 * vol if i == len(btc) - 1 else 100.0
        out.append({"ts": b["ts"], "o": prev / (1 + r), "h": prev * 1.001,
                    "l": prev * 0.999, "c": prev, "v": v, "is_closed": True})
    return out


# ---------------------------------------------------------------- gate ----

def test_minute_phases_classified():
    assert phase_for_minute(2) == SCAN_AND_DEPLOY
    assert phase_for_minute(20) == ACTIVE_EXECUTION
    assert phase_for_minute(50) == PRE_CLOSE_UNWIND
    assert phase_for_minute(57) == IDLE_WAIT
    assert phase_for_minute(59) == IDLE_WAIT


def test_second_scan_same_hour_blocked_next_hour_allowed():
    gate = HourlyScreeningGate()
    bar_ts = T0 + 10 * HOUR + 60  # Minute 1
    r1 = gate.evaluate(bar_ts)
    assert r1.phase == SCAN_AND_DEPLOY
    assert r1.scan_allowed is True
    gate.mark_scanned(bar_ts)
    # gleiche Bar -> blockiert
    r2 = gate.evaluate(bar_ts)
    assert r2.scan_allowed is False
    assert r2.reason == "bar_already_scanned"
    # naechste Stunde -> frei
    r3 = gate.evaluate(bar_ts + HOUR)
    assert r3.scan_allowed is True
    # ausserhalb Scan-Fenster -> blockiert
    r4 = gate.evaluate(bar_ts + HOUR + 20 * 60)
    assert r4.phase == ACTIVE_EXECUTION
    assert r4.scan_allowed is False
    assert r4.reason == "outside_scan_window"


def test_gate_persist_restore():
    gate = HourlyScreeningGate()
    gate.mark_scanned(T0 + HOUR + 120)
    d = gate.to_dict()
    restored = HourlyScreeningGate.restore(d)
    assert restored.last_scan_bar_ts == T0 + HOUR + 120
    assert restored.evaluate(T0 + HOUR + 120).scan_allowed is False


# ------------------------------------------------------------- ranker ----

def _universe():
    btc = btc_ramp_flat()
    long_alt = consolidation_alt(btc, factor=2.7, seed=1, vol=2.5)
    short_alt = make_alt(btc, factor=-2.0, seed=2, vol_mult=2.5, trend=-0.002)
    weak_inverse = make_alt(btc, factor=-0.5, seed=3, noise=0.004, vol_mult=1.0)
    decoupled = make_alt(btc, factor=0.0, seed=4, noise=0.02, vol_mult=1.0)
    thin = consolidation_alt(btc, factor=2.7, seed=5, vol=2.0)
    return {
        "BTC/USD": btc,
        "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
        "LONGALT": long_alt,
        "SHORTALT": short_alt,
        "WEAKINV": weak_inverse,
        "DECOUPLED": decoupled,
        "THINALT": thin,
    }


def test_ranker_directions_long_short_and_rejects():
    ranker = HighBetaRanker()
    series = _universe()
    result = ranker.rank(series, bar_ts=T0 + 59 * HOUR)
    long_symbols = [r.symbol for r in result.long_rank]
    short_symbols = [r.symbol for r in result.short_rank]
    assert "LONGALT" in long_symbols
    assert "SHORTALT" in short_symbols  # gegenlaeufige Kopplung -> Short
    by_symbol = {r.symbol: r for r in result.filtered}
    assert "WEAKINV" in by_symbol
    assert "inverse_long_blocked" in by_symbol["WEAKINV"].reasons
    assert by_symbol["WEAKINV"].direction == "FLAT"  # nie Auto-Long
    assert "DECOUPLED" in by_symbol
    assert "decoupled" in by_symbol["DECOUPLED"].reasons


def test_ranker_top_gainer_rvol_and_pos_eq():
    ranker = HighBetaRanker()
    series = _universe()
    result = ranker.rank(series, bar_ts=T0 + 59 * HOUR)
    long = {r.symbol: r for r in result.long_rank}
    long_alt = long["LONGALT"]
    assert long_alt.rvol >= 1.5
    assert long_alt.perf_24h_pct is not None and long_alt.perf_24h_pct > 0
    assert long_alt.direction == "LONG"
    # Konsolidierung (pos_EQ in 0.40-0.65) -> Leader-Kennzeichnung
    assert long_alt.post_breakout_consolidation is True
    assert long_alt.entry_ready is True


def test_ranker_chasing_zone_not_marked_as_entry():
    btc = btc_series(n=60)
    # Alt mit steilem Anstieg am Ende: pos_EQ nahe 1 -> Chasing
    alt = make_alt(btc, factor=2.0, seed=11, vol_mult=2.0, trend=0.0)
    for i in range(5, 0, -1):
        last = alt[-i]
        alt[-i] = {**last, "c": last["c"] * (1.02 ** (5 - i + 1)),
                   "h": last["c"] * 1.021, "o": last["c"]}
    series = {"BTC/USD": btc, "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
              "SPIKE": alt}
    result = HighBetaRanker().rank(series, bar_ts=T0 + 59 * HOUR)
    spike = {r.symbol: r for r in result.filtered + result.long_rank}["SPIKE"]
    assert spike.pos_eq is not None and spike.pos_eq > 0.90
    assert spike.entry_ready is False
    assert "chasing_zone" in spike.reasons


def test_ranker_thin_book_unlock_weekend_visible():
    ranker = HighBetaRanker()
    series = _universe()
    result = ranker.rank(
        series, bar_ts=T0 + 59 * HOUR,
        thin_book_symbols=["THINALT"],
        unlock_symbols=["LONGALT"],
        spreads={"THINALT": 0.002},
        weekend=True,
    )
    by_symbol = {r.symbol: r for r in result.filtered}
    assert "THINALT" in by_symbol
    assert "thin_book" in by_symbol["THINALT"].reasons
    assert "LONGALT" in by_symbol  # unlock -> aus der Wertung
    assert "unlock_window" in by_symbol["LONGALT"].reasons
    # weekend-Paper-Flag bleibt sichtbar
    assert all(r.weekend_paper_only for r in result.long_rank + result.short_rank)


def test_ranker_recommendation_sniper_hedge_vs_dca():
    btc = btc_ramp_flat()
    hot = consolidation_alt(btc, factor=3.0, seed=21, vol=3.0)
    cold = consolidation_alt(btc, factor=1.6, seed=22, vol=1.6)
    series = {"BTC/USD": btc, "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
              "HOT": hot, "COLD": cold}
    result = HighBetaRanker().rank(
        series, bar_ts=T0 + 59 * HOUR,
        liq_distances={"HOT": 0.04, "COLD": 0.15},
    )
    hot_row = {r.symbol: r for r in result.long_rank}["HOT"]
    cold_row = {r.symbol: r for r in result.long_rank}["COLD"]
    assert hot_row.recommendation == "sniper_hedge"
    assert hot_row.needs_hitl is True  # 4 % Liq-Distanz -> MP-01-HITL-Flag
    assert cold_row.recommendation == "dca"


def test_ranker_rotation_only_next_hourly_scan():
    # Symbol mit zerfallendem RVOL/Volumen -> faellt beim naechsten Scan aus
    btc = btc_ramp_flat()
    fading = consolidation_alt(btc, factor=2.0, seed=31, vol=2.5)
    fresh = consolidation_alt(btc, factor=1.9, seed=32, vol=0.9)  # kalt in Scan 1
    fading[-1] = {**fading[-1], "v": 250.0}
    fresh[-1] = {**fresh[-1], "v": 90.0}
    series1 = {"BTC/USD": btc, "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
               "FADING": fading, "FRESH": fresh}
    r1 = HighBetaRanker().rank(series1, bar_ts=T0 + 59 * HOUR)
    assert "FADING" in [r.symbol for r in r1.long_rank]

    # Scan 2 (naechste Stunde): FADING abgekuehlt (Volumen weg), FRESH heiss
    fading2 = [bar(100.0, v=40.0, ts=b["ts"]) for b in btc]
    fresh2 = consolidation_alt(btc, factor=1.9, seed=33, vol=2.5)
    series2 = {"BTC/USD": btc, "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
               "FADING": fading2, "FRESH": fresh2}
    r2 = HighBetaRanker().rank(series2, bar_ts=T0 + 60 * HOUR)
    long2 = [r.symbol for r in r2.long_rank]
    assert "FRESH" in long2
    assert "FADING" not in long2  # Rotation nur im Scan-Takt, kein Tick-Redeploy


def test_ranker_ignores_open_last_bar():
    btc = btc_series(n=60)
    alt = make_alt(btc, factor=2.0, seed=41, vol_mult=2.0, trend=0.002)
    closed_series = {"BTC/USD": btc, "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
                     "ALT": alt}
    open_bar = {"ts": T0 + 60 * HOUR, "o": 100.0, "h": 105.0, "l": 99.0,
                "c": 104.0, "v": 9999.0, "is_closed": False}
    with_open = {"BTC/USD": btc + [open_bar],
                 "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
                 "ALT": alt + [open_bar]}
    r1 = HighBetaRanker().rank(closed_series, bar_ts=T0 + 59 * HOUR)
    r2 = HighBetaRanker().rank(with_open, bar_ts=T0 + 60 * HOUR)
    # Ergebnisse identisch (offene Bar wird ignoriert)
    assert [x.symbol for x in r1.long_rank] == [x.symbol for x in r2.long_rank]


# ---------------------------------------------------------- shadow plan ----

def test_shadow_plan_phases_and_content():
    # 21:30 UTC -> Quarantaene
    ts_q = T0 + 21 * HOUR + 30 * 60
    assert night_phase(ts_q) == NIGHT_QUARANTINE
    # 23:00 UTC -> Monitoring
    ts_m = T0 + 23 * HOUR
    assert night_phase(ts_m) == NIGHT_MONITORING
    # 00:45 UTC (naechster Tag) -> Synthese
    ts_s = T0 + 24 * HOUR + 45 * 60
    assert night_phase(ts_s) == NIGHT_SYNTHESIS

    plan = build_shadow_plan(
        now_ts=ts_m,
        watchlist=["LONGALT"],
        ranker_rows=[{"symbol": "LONGALT", "conductor": "BTC/USD",
                      "recommendation": "dca"}],
        session_bias="ASIA_ACCUMULATION",
        sweep_zones={"LONGALT": 98.5},
        breakout_levels={"LONGALT": 102.0},
        sentiment={"funding": 0.0008},
    )
    d = plan.to_dict()
    assert d["watchlist"] == ["LONGALT"]
    assert d["scenarios"][0]["strategy_option"] == "dca"
    assert d["scenarios"][0]["mean_reversion_bias"] is True  # Funding-Saettigung
    assert d["scenarios"][0]["sweep_zone"] == 98.5
    assert d["path_alpha"].startswith("proactive")
    assert d["path_beta"].startswith("reactive")
    # Plan loest keinen Scan aus: kein Timeout-/Scan-Feld, nur Planung
    assert "scan" not in d


# ------------------------------------------------------- orchestrator ----

def test_orchestrator_screening_ctx_passive_and_gated():
    btc = btc_ramp_flat()
    alt = consolidation_alt(btc, factor=2.0, seed=51, vol=2.5)
    series = {"BTC/USD": btc, "ETH/USD": btc_series(n=60, start=3000.0, seed=8),
              "ALT": alt}
    ranker = HighBetaRanker()
    gate = HourlyScreeningGate()

    class Ports:
        pass

    snap = type("Snap", (), {"series": series})()
    orch = MasterOrchestrator(ports={"ranker": ranker}, screening_gate=gate)
    ctx1 = orch.tick(snap, now=T0 + 59 * HOUR + 60)
    assert ctx1["screening"]["valid"] is True
    assert ctx1["screening"]["gate"]["scan_allowed"] is True
    assert ctx1["screening"]["screening"]["long_rank"]  # Ranker-Ergebnis in ctx
    # zweiter Tick derselben Bar: kein neuer Scan
    ctx2 = orch.tick(snap, now=T0 + 59 * HOUR + 120)
    assert ctx2["screening"]["gate"]["scan_allowed"] is False
    assert ctx2["screening"]["screening"] is None
    # ohne Gate: kein screening-Key (passiv)
    plain = MasterOrchestrator()
    ctx3 = plain.tick(snap, now=T0 + 59 * HOUR + 60)
    assert "screening" not in ctx3
