"""
=========================================================
Datei:      tests/test_fractal_directional.py
Zweck:      MP-15 Fraktaler Einzeltrade: TP-Staffel 40/30/20/10,
            Spiegelung long/short, Pflicht-Fee-BE nach TP1
            (entry x 1,0005 / 0,9995), naeherer Liq-Puffer
            schlaegt 0,6 %-Default, Kill-Switch (Minute 55 /
            Exhaustion / Sweep), Ranker- und Lead-Pflicht,
            nur geschlossene Bars. Orchestrator-Regression.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Test)
=========================================================
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sigma.execution.risk_guards import fee_covered_stop
from sigma.orchestration import MasterOrchestrator
from sigma.orchestration.hourly_screening_gate import HourlyScreeningGate
from sigma.signals.high_beta_ranker import HighBetaRanker, _recommendation
from sigma.strategies.fractal_directional import (
    ENTRY_MINUTE_MAX,
    ENTRY_MINUTE_MIN,
    FRACTAL_STRATEGY_ID,
    INITIAL_SL_PCT,
    RUNNER_QTY_PCT,
    TP1_QTY_PCT,
    TP1_TARGET_PCT,
    TP2_QTY_PCT,
    TP2_TARGET_PCT,
    TP3_QTY_PCT,
    TP3_TARGET_PCT,
    TTL_MINUTE,
    build_tranches,
    initial_sl_distance,
    plan_fractal,
)

M1 = 60
# Friday 2026-08-28 15:30 UTC — NY expansion, minute 30
NY_MIN30 = datetime(2026, 8, 28, 15, 30, tzinfo=timezone.utc).timestamp()
NY_MIN55 = datetime(2026, 8, 28, 15, 55, tzinfo=timezone.utc).timestamp()


def _bar(ts, o, h, l, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _bars(n=30, start=1_700_000_000.0, price=100.0, drift=0.0):
    """Flache (oder sanft driftende) 1m-Serie; letzter Close ist der Entry."""
    out = []
    for i in range(n):
        p = price * (1.0 + drift * i)
        out.append(_bar(start + i * M1, p, p * 1.01, p * 0.99, p))
    return out


def _screening(symbol="SOL/USD", recommendation="fractal_directional",
               entry_ready=True, beta=3.6, side="buy"):
    row = {
        "symbol": symbol, "conductor": "BTC/USD", "r": 0.85, "beta": beta,
        "rvol": 3.2, "spread_pct": 0.0004, "spread_penalty": 0.0,
        "perf_24h_pct": 15.0, "pos_eq": 0.5, "direction": "LONG",
        "score": 98.0, "recommendation": recommendation, "entry_ready": entry_ready,
        "post_breakout_consolidation": False, "weekend_paper_only": False,
        "needs_hitl": False, "liq_distance_pct": 0.02, "reasons": [],
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
    ctx = {
        "symbol": "SOL/USD",
        "now": NY_MIN30,
        "session": {
            "session": "NEW_YORK_EXPANSION", "volatility_bias": "MAX_TREND",
            "recommended_strategy": "HIGH_BETA_MOMENTUM", "max_leverage": 25,
            "liquidity_gap": False, "weekend_alts_paper_only": False,
            "hour_utc": 15, "ts": NY_MIN30, "description": "",
        },
        "ltf_candles": _bars(),
        "htf_candles": _bars(),
        "ltf_interval_min": 1,
        "htf_interval_min": 15,
        "screening": _screening(),
        "lead": {"confirmed": True, "reason": "btc_breakout_retest"},
        "lead_thrust": True,
        "wave": {"status": "COLLAPSED_INTO_ZONE", "interval_min": 15,
                 "ce50": 105.0, "range_low": 95.0, "range_high": 115.0},
        "leverage": 25,
        "ranker_max_leverage": 25,
        "liq_puffer_pct": 0.004,      # enger als 0,6 % -> gewinnt
        "liquidation_price": 90.0,
        "exhaustion": None,
        "sweep_zone": None,
    }
    ctx.update(overrides)
    return ctx


# ------------------------------------------------------------- staffel

def test_tranches_sum_100_long_ascending_short_mirrored():
    tr = build_tranches(100.0, "buy")
    assert sum(t.qty_pct for t in tr) == pytest.approx(100.0)
    assert [t.qty_pct for t in tr] == [TP1_QTY_PCT, TP2_QTY_PCT, TP3_QTY_PCT, RUNNER_QTY_PCT]
    assert [t.label for t in tr] == ["TP1", "TP2", "TP3", "RUNNER"]
    prices = [t.price for t in tr]
    assert prices[0] < prices[1] < prices[2]  # long aufsteigend
    assert tr[0].price == pytest.approx(100.0 * (1 + TP1_TARGET_PCT))
    assert tr[1].price == pytest.approx(100.0 * (1 + TP2_TARGET_PCT))
    assert tr[2].price == pytest.approx(100.0 * (1 + TP3_TARGET_PCT))
    assert tr[3].price == pytest.approx(100.0)  # Runner offen @ Entry
    # short spiegelbildlich
    ts = build_tranches(100.0, "sell")
    assert [t.price for t in ts] == [pytest.approx(100.0 * (1 - TP1_TARGET_PCT)),
                                     pytest.approx(100.0 * (1 - TP2_TARGET_PCT)),
                                     pytest.approx(100.0 * (1 - TP3_TARGET_PCT)),
                                     pytest.approx(100.0)]
    assert ts[0].price > ts[1].price > ts[2].price


def test_full_plan_long_intent_with_mandatory_fee_be():
    intent = plan_fractal(_ctx())
    assert intent.action == "BUY"
    assert intent.strategy_id == FRACTAL_STRATEGY_ID
    assert intent.execution_mode == "kraken_paper"
    d = intent.details
    # SL: Liq-Puffer 0,4 % < 0,6 % -> gewinnt
    assert d["sl_basis"] == "liq_puffer"
    assert intent.stop_loss == pytest.approx(100.0 * (1 - 0.004))
    # Pflicht nach TP1: Fee-Covered-BE (entry x 1,0005 long)
    assert d["update_sl"] == pytest.approx(fee_covered_stop(100.0, "buy"))
    assert d["update_sl"] == pytest.approx(100.0 * 1.0005)
    assert d["update_sl_reason"] == "TP1_HIT_FEE_COVERED_BREAKEVEN"
    assert d["kill_switch"]["exhaustion_armed"] is True
    assert d["kill_switch"]["ttl_minute"] == TTL_MINUTE
    # TP3 als take_profit
    assert intent.take_profit == pytest.approx(100.0 * (1 + TP3_TARGET_PCT))


def test_short_plan_mirrored_and_fee_be_below_entry():
    intent = plan_fractal(_ctx(screening=_screening(side="sell"), side="sell"))
    assert intent.action == "SELL"
    d = intent.details
    assert intent.stop_loss == pytest.approx(100.0 * (1 + 0.004))
    assert d["update_sl"] == pytest.approx(100.0 * 0.9995)  # < Entry


def test_sl_wide_liq_puffer_default_06_wins():
    intent = plan_fractal(_ctx(liq_puffer_pct=0.02))  # 2 % weiter Puffer
    assert intent.details["sl_basis"] == "default_0.6pct"
    assert intent.stop_loss == pytest.approx(100.0 * (1 - INITIAL_SL_PCT))
    assert initial_sl_distance(0.02) == INITIAL_SL_PCT
    assert initial_sl_distance(0.004) == pytest.approx(0.004)
    assert initial_sl_distance(None) == INITIAL_SL_PCT


# ------------------------------------------------------------- kill switch

def test_kill_switch_ttl_minute_55_flat():
    intent = plan_fractal(_ctx(now=NY_MIN55))
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "kill_switch_ttl_minute_55"


def test_kill_switch_exhaustion_flat():
    ctx = _ctx(exhaustion={"exhausted": True, "score": 0.9})
    intent = plan_fractal(ctx)
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "kill_switch_exhaustion"


def test_kill_switch_sweep_zone_flat():
    # Long: Close >= Sweep-Zone (Zielliquiditaet ueber TP3) -> Flat
    bars = _bars(price=100.0)
    bars[-1] = _bar(bars[-1]["ts"], 100.0, 112.0, 99.5, 111.0)  # Close 111
    intent = plan_fractal(_ctx(ltf_candles=bars, sweep_zone=110.0))
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "kill_switch_sweep_zone"


def test_no_forced_flat_before_minute_48_intact_trend():
    intent = plan_fractal(_ctx(now=NY_MIN30))
    assert intent.action == "BUY"
    intent2 = plan_fractal(_ctx(now=datetime(2026, 8, 28, 15, 47, tzinfo=timezone.utc).timestamp()))
    assert intent2.action == "BUY"


# ------------------------------------------------------------- fail-closed

def test_missing_ranker_or_wrong_rec_flat():
    ctx = _ctx(screening=None)
    assert plan_fractal(ctx).details["reason"] == "missing_ranker_release"
    ctx2 = _ctx(screening=_screening(recommendation="dca"))
    assert plan_fractal(ctx2).details["reason"] == "ranker_not_fractal"
    ctx3 = _ctx(screening=_screening(recommendation="sniper_hedge"))
    assert plan_fractal(ctx3).action == "BUY"  # sniper_hedge ist auch Fraktal-faehig


def test_missing_lead_signal_flat():
    # kein Lead-Kontext UND keine Wave-COLLAPSED+Thrust-Fallback -> FLAT
    ctx = _ctx(lead=None, lead_thrust=False, wave={"status": "IDLE"})
    assert plan_fractal(ctx).details["reason"] == "missing_lead_signal"
    # Wave-COLLAPSED ohne Thrust reicht nicht
    ctx2 = _ctx(lead=None, lead_thrust=False, wave={"status": "COLLAPSED_INTO_ZONE"})
    assert plan_fractal(ctx2).details["reason"] == "missing_lead_signal"


def test_window_and_leverage_guards():
    pre = datetime(2026, 8, 28, 15, 3, tzinfo=timezone.utc).timestamp()
    assert plan_fractal(_ctx(now=pre)).details["reason"] == "outside_execution_window"
    late = datetime(2026, 8, 28, 15, 50, tzinfo=timezone.utc).timestamp()
    assert plan_fractal(_ctx(now=late)).details["reason"] == "outside_execution_window"
    assert plan_fractal(_ctx(leverage=0)).details["reason"] == "leverage_out_of_bounds"
    assert plan_fractal(_ctx(leverage=100)).details["reason"] == "leverage_out_of_bounds"
    assert plan_fractal(_ctx(leverage=50, ranker_max_leverage=25)).details["reason"] == "leverage_above_ranker_cap"
    assert ENTRY_MINUTE_MIN == 5 and ENTRY_MINUTE_MAX == 48


def test_open_bar_ignored_no_lookahead():
    # offene letzte Bar mit Extremwerten darf den Plan nicht veraendern
    closed = _bars()
    ctx = _ctx(ltf_candles=closed)
    base = plan_fractal(ctx)
    open_bar = _bar(closed[-1]["ts"] + M1, 999.0, 999.0, 1.0, 500.0)
    open_bar["is_closed"] = False
    ctx2 = _ctx(ltf_candles=closed + [open_bar])
    leaked = plan_fractal(ctx2)
    assert leaked.action == base.action
    assert leaked.price == base.price


# ------------------------------------------------------------- ranker

def test_ranker_recommendation_fractal_tier():
    assert _recommendation(3.6, 3.2, 0.02) == "fractal_directional"
    assert _recommendation(2.8, 2.6, 0.02) == "sniper_hedge"
    assert _recommendation(2.0, 1.5, 0.02) == "dca"
    # weiter Liq-Puffer -> keine Staffel-Empfehlung
    assert _recommendation(3.6, 3.2, 0.5) == "dca"


def test_ranker_full_rank_emits_fractal_for_extreme():
    from tests.test_hourly_ranker import T0, HOUR, btc_ramp_flat, consolidation_alt
    btc = btc_ramp_flat()
    hot = consolidation_alt(btc, factor=3.0, seed=21, vol=3.0)
    series = {"BTC/USD": btc, "ETH/USD": [], "HOT": hot}
    r = HighBetaRanker().rank(series, bar_ts=T0 + 59 * HOUR, liq_distances={"HOT": 0.02})
    rows = {x.symbol: x for x in r.long_rank}
    assert "HOT" in rows


# ------------------------------------------------------------- orchestrator

def test_orchestrator_registers_fractal_and_routes_by_recommendation():
    orch = MasterOrchestrator()
    assert "fractal_directional" in orch.templates
    rec = orch._symbol_recommendation(_screening(), "SOL/USD")
    assert rec == "fractal_directional"
    rec2 = orch._symbol_recommendation(_screening(recommendation="sniper_hedge"), "SOL/USD")
    assert rec2 == "sniper_hedge"
    # Regression: Tick ohne Screening unveraendert
    snap = SimpleNamespace(
        series={"BTC/USD": _bars()},
        htf_series={"BTC/USD": _bars()},
        degraded=False,
    )
    out = orch.tick(snap, now=NY_MIN30)
    assert out["ok"] is True
    assert "screening" not in out


def test_orchestrator_plan_routes_fractal_template():
    from sigma.signals.session_clock import SessionClock

    orch = MasterOrchestrator()
    session = SessionClock().evaluate(NY_MIN30)  # HIGH_BETA_MOMENTUM
    htf = _bars(30, start=1_700_001_000.0)
    wave = {"status": "COLLAPSED_INTO_ZONE", "interval_min": 15,
            "ce50": 105.0, "range_low": 95.0, "range_high": 115.0}
    intent = orch._plan(
        "SOL/USD",
        session,
        {"SOL/USD": _bars(30, start=1_700_001_000.0, price=100.0)},
        {"BTC/USD": htf},
        SimpleNamespace(bias_minutes=60, exec_minutes=15),
        wave=SimpleNamespace(to_dict=lambda: wave),
        screening=_screening(),
        now=float(htf[-1]["ts"]) + 900,
    )
    # Fraktal-Template aktiv; ohne Lead im Orchester-Kontext fail-closed FLAT
    assert intent.strategy_id == FRACTAL_STRATEGY_ID
    assert intent.action == "FLAT"
    assert intent.details["reason"] == "missing_lead_signal"
