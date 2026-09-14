"""
=========================================================
Datei:      tests/test_quantum_sniper.py
Zweck:      MP-07 Quantum-Sniper: Vollzyklus (Wave->Retest->
            Ranker->Intent), TTL 05-48, Range-Low/INVALIDATED,
            fehlende Ranker-Freigabe, Retest ohne Thrust,
            Pfad alpha/beta, geschlossene Bars (kein Look-ahead),
            Orchestrator-Regression. Nur synthetische Bars,
            kein Netz, keine Orders.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Test)
=========================================================
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from types import SimpleNamespace

from sigma.orchestration import MasterOrchestrator
from sigma.orchestration.hourly_screening_gate import HourlyScreeningGate
from sigma.signals.quantum_wave_collider import (
    STATUS_COLLAPSED,
    STATUS_INVALIDATED,
    QuantumWaveCollider,
)
from sigma.strategies.quantum_sniper_dca import (
    ENTRY_MINUTE_MAX,
    ENTRY_MINUTE_MIN,
    N_SAFETY,
    SNIPER_STRATEGY_ID,
    plan_sniper,
    retest_confirmed,
    utc_minute,
)
from sigma.strategies.dca_ladder import LADDER_TTL_SECONDS

M15 = 900
M1 = 60
# Friday 2026-08-28 15:37 UTC — NY expansion, minute 37 (ACTIVE_EXECUTION)
NY_MIN37 = datetime(2026, 8, 28, 15, 37, tzinfo=timezone.utc).timestamp()
NY_MIN50 = datetime(2026, 8, 28, 15, 50, tzinfo=timezone.utc).timestamp()


def _bar(ts, o, h, l, c, v=100.0, closed=True):
    row = {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}
    if not closed:
        row["is_closed"] = False
    return row


def _htf_collapsed(start: float = 1_700_000_000.0):
    """15m-BTC: Expansion -> FVG -> Dip in CE50 (Stil Wave-Regime-Test)."""
    rows = []
    prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]
    for i, p in enumerate(prices):
        rows.append(_bar(start + i * M15, p, p + 1.0, p - 1.0, p))
    i0 = len(rows)
    rows.append(_bar(start + i0 * M15, 112.0, 113.0, 111.0, 112.5))
    rows.append(_bar(start + (i0 + 1) * M15, 113.0, 118.0, 113.0, 117.0))
    rows.append(_bar(start + (i0 + 2) * M15, 117.0, 120.0, 116.0, 119.0))
    rows.append(_bar(start + (i0 + 3) * M15, 115.0, 115.0, 108.0, 109.0))
    return rows


def _htf_invalidated():
    rows = _htf_collapsed()
    rows.append(_bar(rows[-1]["ts"] + M15, 100.0, 101.0, 90.0, 92.0))
    return rows


def _ltf_retest(ce50: float, start: float = 1_700_001_000.0, thrust: bool = True):
    """1m-Alts: bearischer Dip unter CE50 (Bar A), dann je nach thrust
    Zwei-Bar-Thrust (B/C bullisch) oder Dead-Cat (B/C bearisch);
    letzte Kerze beruehrt die CE50-Zone."""
    rows = []
    p = 108.0
    ts = start
    for i in range(12):
        rows.append(_bar(ts, p, p + 0.3, p - 0.3, p + 0.05))
        ts += M1
        p += 0.03
    # A: bearisch, Dip unter CE50 (Retest, nicht erster Touch)
    rows.append(_bar(ts, p + 0.2, p + 0.4, ce50 - 1.6, ce50 - 1.2))
    a_high = p + 0.4
    ts += M1
    if thrust:
        # B (bull) + C (bull): Close ueber A.High, Body-Summe > A-Body
        rows.append(_bar(ts, ce50 - 1.2, ce50 + 0.3, ce50 - 1.4, ce50 + 0.1))  # B
        ts += M1
        rows.append(_bar(ts, ce50 + 0.1, ce50 + 1.3, ce50 - 0.5, ce50 + 1.1))  # C
    else:
        # kein Thrust: B + C bearisch (Dead-Cat), Close unter A.High
        rows.append(_bar(ts, ce50 - 0.5, ce50 - 0.2, ce50 - 1.5, ce50 - 1.0))  # B
        ts += M1
        rows.append(_bar(ts, ce50 - 1.0, ce50 - 0.6, ce50 - 1.7, ce50 - 1.3))  # C
    return rows, a_high


def _ltf_beta_retest(level: float, start: float = 1_700_001_000.0):
    """Pfad beta: Alt retestet das Ausbruchslevel von oben."""
    rows = []
    ts = start
    p = level + 0.5
    for i in range(12):
        rows.append(_bar(ts, p, p + 0.3, p - 0.3, p + 0.05))
        ts += M1
        p += 0.03
    rows.append(_bar(ts, level + 0.9, level + 1.1, level - 0.4, level + 0.1))
    return rows


def _wave_ctx(htf_bars, interval_min=15):
    now = float(htf_bars[-1]["ts"]) + M15
    state = QuantumWaveCollider().evaluate(htf_bars, interval_min=interval_min, now=now)
    return state.to_dict(), now


def _screening(symbol="SOL/USD", recommendation="sniper_hedge", entry_ready=True,
               beta=2.8, side="buy"):
    row = {
        "symbol": symbol, "conductor": "BTC/USD", "r": 0.85, "beta": beta,
        "rvol": 2.6, "spread_pct": 0.0004, "spread_penalty": 0.0,
        "perf_24h_pct": 12.0, "pos_eq": 0.5, "direction": "LONG",
        "score": 95.0, "recommendation": recommendation, "entry_ready": entry_ready,
        "post_breakout_consolidation": False, "weekend_paper_only": False,
        "needs_hitl": False, "liq_distance_pct": 0.2, "reasons": [],
    }
    return {
        "valid": True,
        "gate": {"scan_allowed": True, "reason": "scan_ok"},
        "screening": {
            "bar_ts": 0,
            "long_rank": [row] if side == "buy" else [],
            "short_rank": [] if side == "buy" else [row],
            "filtered": [],
            "conductors": ["BTC/USD", "ETH/USD"],
        },
    }


def _ctx(**overrides):
    htf = _htf_collapsed()
    wave, now = _wave_ctx(htf)
    ce50 = wave["ce50"]
    ltf, _ = _ltf_retest(ce50)
    ctx = {
        "symbol": "SOL/USD",
        "now": NY_MIN37,
        "session": {
            "session": "NEW_YORK_EXPANSION", "volatility_bias": "MAX_TREND",
            "recommended_strategy": "HIGH_BETA_MOMENTUM", "max_leverage": 25,
            "liquidity_gap": False, "weekend_alts_paper_only": False,
            "hour_utc": 15, "ts": NY_MIN37, "description": "",
        },
        "htf_candles": htf,
        "ltf_candles": ltf,
        "htf_interval_min": 15,
        "ltf_interval_min": 1,
        "wave": wave,
        "screening": _screening(),
        "liquidation_price": 95.0,          # weit unter range_low (99)
        "leverage": 5,
        "expected_btc_wick_pct": 0.01,
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------- Vollzyklus

def test_full_cycle_collapsed_retest_ranker_buy_intent():
    ctx = _ctx()
    intent = plan_sniper(ctx)
    assert intent.action == "BUY"
    assert intent.side == "buy"
    assert intent.strategy_id == SNIPER_STRATEGY_ID
    assert intent.execution_mode == "kraken_paper"
    assert intent.stop_loss > 0 and intent.stop_loss < intent.price
    assert intent.take_profit > intent.price
    d = intent.details
    assert d["path"] == "alpha"
    assert d["confirmed_breakout_retest"] is False
    assert d["retest"]["confirmed"] is True
    assert d["ttl_seconds"] == LADDER_TTL_SECONDS
    assert d["ladder"]["n_safety"] == N_SAFETY
    assert d["ladder"]["side"] == "buy"
    assert d["ladder"]["step_pct"] == 0.002
    assert d["risk_guards"]["hard_sl_basis"] == "liquidation_price"
    # TP relativ zum AVG (nicht Entry)
    assert intent.take_profit == pytest.approx(d["avg_fill_price"] * 1.02)


def test_retest_confirmed_helper_uses_closed_bars_only():
    ctx = _ctx()
    ce50 = ctx["wave"]["ce50"]
    ltf, _ = _ltf_retest(ce50)
    verdict = retest_confirmed(ltf, ce50)
    assert verdict.confirmed is True
    assert verdict.touched and verdict.dipped and verdict.thrust
    # offene letzte Kerze mit Extremwerten darf nichts andern (kein Look-ahead)
    open_bar = _bar(ltf[-1]["ts"] + M1, 999.0, 999.0, 1.0, 500.0, closed=False)
    assert retest_confirmed(ltf + [open_bar], ce50).confirmed is True


def test_first_touch_without_thrust_stays_flat():
    ctx = _ctx()
    ce50 = ctx["wave"]["ce50"]
    ltf, _ = _ltf_retest(ce50, thrust=False)
    ctx["ltf_candles"] = ltf
    intent = plan_sniper(ctx)
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "no_retest_confirmation"
    assert intent.details["path_beta_armed"] is False  # kein Breakout im HTF


def test_beta_path_enters_on_confirmed_breakout_retest():
    ctx = _ctx()
    wave = ctx["wave"]
    # Wave bleibt COLLAPSED (Kontext), aber HTF-Daten zeigen jetzt einen
    # Breakout: letzte geschlossene Kerze schliesst ueber range_high (120)
    htf = _htf_collapsed()
    htf.append(_bar(htf[-1]["ts"] + M15, 119.0, 121.0, 118.0, 120.5))
    ctx["htf_candles"] = htf
    # LTF: kein alpha-Retest, aber beta-Retest am Ausbruchslevel
    ltf, _ = _ltf_retest(wave["ce50"], thrust=False)
    ctx["ltf_candles"] = ltf + _ltf_beta_retest(120.0)
    ctx["screening"] = _screening(beta=2.8)
    intent = plan_sniper(ctx)
    assert intent.action == "BUY"
    assert intent.details["path"] == "beta"
    assert intent.details["confirmed_breakout_retest"] is True


def test_missing_ranker_release_and_wrong_recommendation():
    ctx = _ctx()
    ctx["screening"] = None
    intent = plan_sniper(ctx)
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "missing_ranker_release"
    ctx2 = _ctx()
    ctx2["screening"] = _screening(recommendation="dca")
    assert plan_sniper(ctx2).details["reason"] == "ranker_not_sniper"


def test_ttl_minute_50_flat_and_pre_window_flat():
    ctx = _ctx(now=NY_MIN50)
    intent = plan_sniper(ctx)
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "ttl_minute_48"
    assert intent.details["minute_utc"] == 50
    pre = datetime(2026, 8, 28, 15, 3, tzinfo=timezone.utc).timestamp()
    intent2 = plan_sniper(_ctx(now=pre))
    assert intent2.action == "FLAT"
    assert intent2.details["reason"] == "outside_execution_window"
    assert ENTRY_MINUTE_MIN == 5 and ENTRY_MINUTE_MAX == 48


def test_invalidated_wave_and_range_low_breach_flat():
    htf = _htf_invalidated()
    wave, _ = _wave_ctx(htf)
    assert wave["status"] == STATUS_INVALIDATED
    assert wave["reason"] == "range_low_breach"
    ctx = _ctx(wave=wave, htf_candles=htf)
    intent = plan_sniper(ctx)
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "wave_invalidated"


def test_open_htf_bar_never_triggers_sniper():
    # offene 15m-Kerze -> Wave HTF_OPEN (valid=False) -> kein Entry
    htf = _htf_collapsed()
    wave_closed, _ = _wave_ctx(htf)
    htf_open = htf + [_bar(htf[-1]["ts"] + M15, 200.0, 999.0, 1.0, 500.0, closed=False)]
    state = QuantumWaveCollider().evaluate(htf_open, interval_min=15, now=htf_open[-1]["ts"] + 10)
    assert state.status != STATUS_COLLAPSED or state.valid is False
    ctx = _ctx(wave=state.to_dict(), htf_candles=htf_open)
    assert plan_sniper(ctx).action == "FLAT"


def test_orchestrator_registers_sniper_without_orders():
    orch = MasterOrchestrator()
    assert "quantum_sniper_dca" in orch.templates
    # Regression: Tick ohne Gate verhaelt sich wie bisher (kein Screening-Key)
    snap = SimpleNamespace(
        series={"BTC/USD": _htf_collapsed()},
        htf_series={"BTC/USD": _htf_collapsed()},
        degraded=False,
    )
    out = orch.tick(snap, now=NY_MIN37)
    assert out["ok"] is True
    assert "screening" not in out


def test_orchestrator_routes_ny_momentum_to_sniper_fail_closed():
    from sigma.signals.session_clock import SessionClock

    orch = MasterOrchestrator()
    session = SessionClock().evaluate(NY_MIN37)  # HIGH_BETA_MOMENTUM
    # 15m-Bars, deren letzte Kerze um Minute 15 schliesst (ACTIVE_EXECUTION)
    htf = _htf_collapsed(start=1_700_001_000.0)
    wave, now = _wave_ctx(htf)
    intent = orch._plan(
        "SOL/USD",
        session,
        {"SOL/USD": _htf_collapsed()},   # 15m-Daten als series (kein 1m)
        {"BTC/USD": htf},
        SimpleNamespace(bias_minutes=60, exec_minutes=15),
        wave=SimpleNamespace(to_dict=lambda: wave),
        screening={"valid": True, "screening": None},
        now=now,
    )
    # Sniper-Pfad aktiv, aber ohne 1m/5m-Retest-Daten fail-closed FLAT —
    # der Orchestrator platziert keine Orders.
    assert intent.strategy_id == SNIPER_STRATEGY_ID
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "ltf_timeframe_not_1m_5m"


def test_utc_minute_helper():
    assert utc_minute(NY_MIN37) == 37
    assert utc_minute(None) is None
