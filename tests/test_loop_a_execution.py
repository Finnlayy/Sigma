"""
Loop A — Safety, Kraken CLI Bridge, Virtual Bots, Deadman, Pipeline.
Alle Schwellen stammen aus app/core/blueprint.py (Spec Freeze v3.0).
"""
from __future__ import annotations

import os
import time

import pytest

from app.core import blueprint as bp
from app.core.config import load_config
from app.execution.KrakenCliBridge import KrakenCliBridge
from app.execution.LoopAPipeline import LoopAPipeline, SignalRequest
from app.execution.SafetyGuard import SafetyBlocked, SafetyGuard
from app.execution.VirtualBotEngine import VirtualBotEngine
from app.execution.deadman_switch_daemon import DeadmanSwitchDaemon


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "s3cr3t")
    c = load_config()
    c.kill_switch_file = str(tmp_path / "signals" / "KILL_SWITCH")
    c.pause_signal_file = str(tmp_path / "signals" / "PAUSE")
    c.orders_log_path = str(tmp_path / "logs" / "orders.jsonl")
    return c


@pytest.fixture()
def guard(cfg):
    return SafetyGuard(cfg)


# ------------------------------------------------------------------ safety ---

def test_kill_switch_blocks_with_503(guard):
    assert guard.check().allowed
    guard.engage_kill_switch("test")
    verdict = guard.check()
    assert not verdict.allowed
    assert verdict.code == "KILL_SWITCH"
    assert verdict.status_code == bp.WEBHOOK_BLOCKED_STATUS
    with pytest.raises(SafetyBlocked):
        verdict.raise_if_blocked()
    guard.release_kill_switch()
    assert guard.check().allowed


def test_pause_blocks_but_keeps_alerts(guard):
    guard.engage_pause("test")
    verdict = guard.check()
    assert verdict.code == "PAUSED" and verdict.status_code == 503
    # §4.6: PAUSE laesst Alerts an
    assert "pause_file" in bp.ALERT_UNCHANGED_ON_EVENTS


def test_daily_loss_limit_and_error_streak(guard):
    guard.record_pnl(-(bp.RISK_GUARD["max_daily_loss_usd"] + 1))
    assert guard.check().code == "DAILY_LOSS_LIMIT"
    guard._daily_pnl_usd = 0.0
    for _ in range(int(bp.RISK_GUARD["max_consecutive_errors"])):
        guard.record_error()
    assert guard.check().code == "CONSECUTIVE_ERRORS"
    guard.record_success()
    assert guard.check().allowed


def test_max_open_positions_and_symbol_halt(guard):
    assert guard.check(open_positions=int(bp.RISK_GUARD["max_open_positions"])).code == "MAX_OPEN_POSITIONS"
    assert guard.check(symbol="BTC/USD", symbol_halted=True).code == "SYMBOL_HALTED"


def test_webhook_secret_timing_safe(guard):
    assert guard.verify_webhook_secret("s3cr3t").allowed
    bad = guard.verify_webhook_secret("nope")
    assert not bad.allowed and bad.status_code == 401


def test_webhook_secret_live_without_secret_blocks(cfg):
    cfg.webhook_secret = ""
    cfg.live_trading = True
    verdict = SafetyGuard(cfg).verify_webhook_secret(None)
    assert not verdict.allowed
    assert verdict.code == "UNAUTHORIZED"
    assert verdict.status_code == bp.WEBHOOK_UNAUTHORIZED_STATUS


def test_webhook_secret_paper_without_secret_allows(cfg):
    cfg.webhook_secret = ""
    cfg.live_trading = False
    verdict = SafetyGuard(cfg).verify_webhook_secret(None)
    assert verdict.allowed
    assert verdict.code == "OK"


def test_signal_freshness(guard):
    now = time.time()
    assert guard.check_signal_freshness(now * 1000, 60, now=now).allowed     # ms input
    assert not guard.check_signal_freshness(now - 600, 60, now=now).allowed


# ------------------------------------------------------------- kraken bridge ---

def test_sim_mode_when_live_disabled(cfg):
    bridge = KrakenCliBridge(cfg)
    res = bridge.add_order(pair="XBTUSD", side="buy", volume=0.01, stop_price=95_000)
    assert res.ok and res.mode == "sim" and res.txid.startswith("SIM-")
    assert res.has_native_stop_loss
    assert "--close-ordertype=stop-loss" in res.argv
    assert os.path.exists(cfg.orders_log_path)


def test_live_requires_flag_and_telemetry(cfg):
    class Telemetry:
        class state:
            state = "LIVE_APPROVED"

    cfg.live_trading = True
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return "txid=OQCLML-BW3P3-BUCMWZ", "", 0

    bridge = KrakenCliBridge(cfg, telemetry=Telemetry(), runner=runner)
    assert bridge.live_enabled
    res = bridge.add_order(pair="XBTUSD", side="buy", volume=0.5, stop_price=90_000)
    assert res.ok and res.mode == "live" and res.txid == "OQCLML-BW3P3-BUCMWZ"
    assert calls and calls[0][:3] == ["kraken", "trade", "add-order"]


def test_error_text_beats_exit_code(cfg):
    class Telemetry:
        class state:
            state = "LIVE_APPROVED"

    cfg.live_trading = True
    bridge = KrakenCliBridge(cfg, telemetry=Telemetry(),
                             runner=lambda argv, t: ("EOrder:Insufficient funds", "", 0))
    res = bridge.add_order(pair="XBTUSD", side="buy", volume=1)
    assert not res.ok and res.error_code.startswith("EOrder:")


def test_futures_live_attaches_reduce_only_stop(cfg):
    class Telemetry:
        class state:
            state = "LIVE_APPROVED"

    cfg.live_trading = True
    calls = []

    def runner(argv, timeout):
        calls.append(list(argv))
        return "txid=FT-ENTRY-1", "", 0

    bridge = KrakenCliBridge(cfg, telemetry=Telemetry(), runner=runner, futures=True)
    res = bridge.add_order(pair="PF_XBTUSD", side="buy", volume=0.01,
                           stop_price=67_000, strategy_id="s1")
    assert res.ok and res.mode == "live" and res.has_native_stop_loss
    assert len(calls) == 2
    assert calls[0][:3] == ["kraken", "futures", "order"]
    assert "--reduce-only" in calls[1]
    assert any(a.startswith("--stop-price=") for a in calls[1])


def test_shadow_mode_when_telemetry_not_approved(cfg):
    class Telemetry:
        class state:
            state = "SHADOW_ACTIVE"

    cfg.live_trading = True
    bridge = KrakenCliBridge(cfg, telemetry=Telemetry())
    assert not bridge.live_enabled
    assert bridge.add_order(pair="XBTUSD", side="sell", volume=1).mode == "sim"


# -------------------------------------------------------------- virtual bots ---

class FakeAlerts:
    def __init__(self):
        self.events = []

    def enable(self, sid, reason=""):
        self.events.append(("enable", sid))

    def disable(self, sid, reason=""):
        self.events.append(("disable", sid))


class FakeVault:
    def __init__(self):
        self.credits = []

    def credit_sweep(self, sid, amount, reason=""):
        self.credits.append((sid, amount))


def test_bot_budget_is_ringfenced():
    alerts, vault = FakeAlerts(), FakeVault()
    engine = VirtualBotEngine(vault_engine=vault, alert_provisioner=alerts)
    a = engine.create_bot("strat_a", "BTC/USD", 1000.0)
    b = engine.create_bot("strat_b", "ETH/USD", 500.0)
    engine.start(a.bot_id)
    engine.start(b.bot_id)

    out = engine.apply_trade_result(a.bot_id, -600.0)
    assert any(e.startswith("quarantined") for e in out["events"])
    assert engine.get(a.bot_id).status == "QUARANTINED"
    assert ("disable", "strat_a") in alerts.events
    # Nachbar-Bot bleibt unberuehrt
    assert engine.get(b.bot_id).status == "RUNNING"
    assert engine.get(b.bot_id).current_equity == 500.0


def test_profit_sweeps_to_vault():
    vault = FakeVault()
    engine = VirtualBotEngine(vault_engine=vault, alert_provisioner=FakeAlerts())
    bot = engine.create_bot("strat_c", "BTC/USD", 1000.0)
    engine.start(bot.bot_id)
    engine.apply_trade_result(bot.bot_id, 250.0)
    assert vault.credits and vault.credits[0][1] == pytest.approx(250.0)
    assert engine.get(bot.bot_id).current_equity == pytest.approx(1000.0)
    assert engine.get(bot.bot_id).swept_to_vault == pytest.approx(250.0)


def test_sizing_uses_bot_equity_and_m8_multiplier():
    engine = VirtualBotEngine(alert_provisioner=FakeAlerts())
    bot = engine.create_bot("strat_d", "BTC/USD", 2000.0)
    engine.start(bot.bot_id)
    active = engine.size_order(bot.bot_id, price=100.0, win_prob=0.7)
    engine.apply_m8_state(bot.bot_id, "THROTTLED")
    throttled = engine.size_order(bot.bot_id, price=100.0, win_prob=0.7)
    assert throttled["quantity"] == pytest.approx(active["quantity"] * 0.5)
    assert active["equity_basis"] == 2000.0
    # THROTTLED laesst den Alert an
    assert engine.get(bot.bot_id).status == "RUNNING"

    engine.apply_m8_state(bot.bot_id, "QUARANTINED")
    assert engine.size_order(bot.bot_id, price=100.0, win_prob=0.7)["allowed"] is False


def test_bot_card_has_blueprint_fields():
    engine = VirtualBotEngine()
    card = engine.create_bot("strat_e", "XRP/USD", 300.0).to_card()
    for field in bp.STRATEGY_CARD_REQUIRED_FIELDS:
        assert field in card


# ------------------------------------------------------------------ deadman ---

class RecordingBridge:
    def __init__(self):
        self.calls = []

    def cancel_open_limit_orders(self, reason=""):
        self.calls.append(("cancel_limits", reason))
        return {"ok": True}

    def close_all_market(self, reason=""):
        self.calls.append(("close_all", reason))
        return {"ok": True}


def test_deadman_cancels_limits_when_native_stop_exists():
    bridge = RecordingBridge()
    dm = DeadmanSwitchDaemon(kraken_bridge=bridge)
    dm.beat(has_native_stop_loss=True)
    dm.state.last_beat = time.time() - (bp.DEADMAN_TIMEOUT_SECONDS + 5)
    out = dm.trigger()
    assert out["action"] == "cancel_open_limit_orders"
    assert bridge.calls[0][0] == "cancel_limits"


def test_deadman_closes_all_without_native_stop():
    bridge = RecordingBridge()
    dm = DeadmanSwitchDaemon(kraken_bridge=bridge)
    dm.beat(has_native_stop_loss=False)
    dm.state.last_beat = time.time() - (bp.DEADMAN_TIMEOUT_SECONDS + 5)
    out = dm.trigger()
    assert out["action"] == bp.DEADMAN_FALLBACK_ACTION
    assert bridge.calls[0][0] == "close_all"


def test_deadman_latches_after_first_flatten():
    bridge = RecordingBridge()
    dm = DeadmanSwitchDaemon(kraken_bridge=bridge)
    dm.beat(has_native_stop_loss=True)
    dm.state.last_beat = time.time() - (bp.DEADMAN_TIMEOUT_SECONDS + 5)
    first = dm.trigger()
    second = dm.trigger()
    assert first["expired"] is True
    assert first["action"] == "cancel_open_limit_orders"
    assert second.get("latched") is True
    assert bridge.calls == [("cancel_limits", "deadman_timeout")]


def test_deadman_quiet_while_beating():
    dm = DeadmanSwitchDaemon()
    dm.beat()
    assert dm.evaluate()["action"] == "none"
    assert not dm.expired


def test_deadman_auto_pulse_via_scheduler():
    from app.core.exchange_clock import ExchangeClock, set_exchange_clock
    from app.core.scheduler_matrix import SchedulerMatrix, install_canonical_tasks
    from app.execution.deadman_switch_daemon import pulse_deadman_from_kraken

    class _Mem:
        def check(self):
            return {"stage": 0}

    class _Host:
        def __init__(self, t=1_700_000_000.0):
            self.t = t

        def __call__(self):
            return self.t

    host = _Host()
    clock = ExchangeClock(fetcher=lambda: host.t + 0.04, host_time=host)
    dm = DeadmanSwitchDaemon()
    dm.state.last_beat = time.time() - 120
    out = pulse_deadman_from_kraken(dm, clock)
    assert out["beat"] is True and out["ok"] is True
    assert not dm.expired
    assert clock.last_rtt_ms is not None

    set_exchange_clock(clock)
    try:
        sched = SchedulerMatrix()
        install_canonical_tasks(sched, deadman=dm, memory=_Mem())
        assert sched.get("deadman_heartbeat") is not None
        assert sched.get("memory_watchdog") is not None
        snap = dm.snapshot()
        assert snap["auto_pulse"] is True
        assert snap["age_s"] < bp.DEADMAN_TIMEOUT_SECONDS
        assert snap["timeout_s"] == 1800
    finally:
        set_exchange_clock(None)


def test_deadman_skips_beat_when_kraken_unreachable():
    from app.core.exchange_clock import ExchangeClock
    from app.execution.deadman_switch_daemon import pulse_deadman_from_kraken

    def boom():
        raise RuntimeError("offline")

    clock = ExchangeClock(fetcher=boom, host_time=time.time)
    dm = DeadmanSwitchDaemon()
    dm.state.last_beat = time.time() - 90
    age0 = dm.age_seconds
    out = pulse_deadman_from_kraken(dm, clock)
    assert out["beat"] is False
    assert dm.age_seconds >= age0 - 0.05


# ----------------------------------------------------------------- pipeline ---

@pytest.fixture()
def pipeline(cfg):
    return LoopAPipeline(cfg, safety=SafetyGuard(cfg), kraken=KrakenCliBridge(cfg),
                         equity_provider=lambda: 10_000.0)


def _signal(**kw):
    base = dict(symbol="XBTUSD", action="BUY", price=50_000.0, rsi=28.0, atr=500.0,
                cisd_score=0.7, timestamp=int(time.time()), strategy_id="strat_x",
                interval=15, secret="s3cr3t")
    base.update(kw)
    return SignalRequest(**base)


def test_pipeline_happy_path_is_paper_or_sim(pipeline):
    res = pipeline.handle_signal(_signal())
    assert res.accepted
    assert res.mode in ("sim", "paper", "dry_run")
    assert res.quantity > 0 and res.stop_loss < res.price < res.take_profit
    # Reihenfolge der normativen Schritte eingehalten
    assert res.trace.index("SafetyGuard.check") < res.trace.index("calculate_kelly")


def test_pipeline_rejects_bad_secret(pipeline):
    res = pipeline.handle_signal(_signal(secret="wrong"))
    assert not res.accepted and res.status_code == 401 and res.code == "UNAUTHORIZED"


def test_pipeline_rejects_stale_signal(pipeline):
    res = pipeline.handle_signal(_signal(timestamp=int(time.time()) - 5000))
    assert not res.accepted and res.code == "STALE_SIGNAL"


def test_pipeline_respects_kill_switch(pipeline):
    pipeline.safety.engage_kill_switch("test")
    res = pipeline.handle_signal(_signal())
    assert not res.accepted and res.status_code == 503
    pipeline.safety.release_kill_switch()


def test_pipeline_blocks_quarantined_and_crisis(pipeline):
    res = pipeline.handle_signal(_signal(), m8_state="QUARANTINED")
    assert not res.accepted and res.code == "M8_QUARANTINED"
    res2 = pipeline.handle_signal(_signal(), regime=bp.Regime.HIGH_VOL_CRISIS.value)
    assert not res2.accepted and res2.code == "HIGH_VOL_CRISIS"


def test_pipeline_throttled_halves_size(cfg):
    # kleine Equity, damit der Notional-Cap nicht beide Groessen gleichmacht
    pipeline = LoopAPipeline(cfg, safety=SafetyGuard(cfg), kraken=KrakenCliBridge(cfg),
                             equity_provider=lambda: 1_000.0)
    full = pipeline.handle_signal(_signal(), m8_state="ACTIVE")
    half = pipeline.handle_signal(_signal(), m8_state="THROTTLED")
    assert half.quantity == pytest.approx(full.quantity * 0.5)
    assert half.budget_multiplier == 0.5 and half.accepted


def test_pipeline_rejects_symbol_outside_allowlist(pipeline):
    res = pipeline.handle_signal(_signal(symbol="DOGE/USD"))
    assert not res.accepted and res.code == "SYMBOL_NOT_ALLOWED"


def test_pipeline_caps_notional(pipeline):
    res = pipeline.handle_signal(_signal(price=1.0, atr=0.01))
    limit = bp.EXCHANGE_SPOT["max_order_notional_usd"]
    assert res.notional <= limit + 1e-6


def test_parse_balance_stdout_normalizes_assets():
    from app.execution.KrakenCliBridge import parse_balance_stdout

    mapped = parse_balance_stdout('{"result": {"XXBT": "0.5", "ZUSD": "1000"}}')
    assert mapped["BTC"] == 0.5
    assert mapped["USD"] == 1000.0
    table = parse_balance_stdout("XXBT    0.25\nZUSD    50")
    assert table["BTC"] == 0.25
    assert table["USD"] == 50.0
    assert parse_balance_stdout("") == {}


def test_trades_strategy_ids_in_query(tmp_path):
    from app.core.duckdb_store import DuckDBStore

    store = DuckDBStore(str(tmp_path / "in.duckdb"))
    for sid, tid in (("s1", "t1"), ("s2", "t2"), ("s3", "t3")):
        store.upsert_trade({
            "trade_id": tid, "strategy_id": sid, "status": "closed",
            "symbol": "BTC/USD", "direction": "LONG", "side": "buy",
            "net_pnl_usd": 1.0,
        })
    assert store.trades(strategy_ids=[]) == []
    rows = store.trades(strategy_ids=["s1", "s2"], status="closed")
    assert {r["strategy_id"] for r in rows} == {"s1", "s2"}
    single = store.trades(strategy_id="s3", status="closed")
    assert [r["strategy_id"] for r in single] == ["s3"]


def test_sum_closed_pnl_matches_python_or_paper(tmp_path):
    from app.core.duckdb_store import DuckDBStore

    store = DuckDBStore(str(tmp_path / "pnl.duckdb"))
    store.upsert_trade({
        "trade_id": "a", "strategy_id": "s", "status": "closed",
        "execution_mode": "paper", "symbol": "BTC/USD",
        "direction": "LONG", "side": "buy", "net_pnl_usd": 10.0,
    })
    store.upsert_trade({
        "trade_id": "b", "strategy_id": "s", "status": "closed",
        "execution_mode": "", "symbol": "BTC/USD",
        "direction": "LONG", "side": "buy", "net_pnl_usd": 3.0,
    })
    store.upsert_trade({
        "trade_id": "c", "strategy_id": "s", "status": "closed",
        "execution_mode": "live", "symbol": "BTC/USD",
        "direction": "LONG", "side": "buy", "net_pnl_usd": 99.0,
    })
    store.upsert_trade({
        "trade_id": "d", "strategy_id": "s", "status": "open",
        "execution_mode": "paper", "symbol": "BTC/USD",
        "direction": "LONG", "side": "buy", "net_pnl_usd": 5.0,
    })
    assert store.sum_closed_pnl("paper") == 13.0
    stats = store.closed_trade_stats("paper")
    assert stats["count"] == 2
    assert stats["pnl"] == 13.0


def test_closed_pnl_by_strategy_matches_python_group(tmp_path):
    from app.core.duckdb_store import DuckDBStore

    store = DuckDBStore(str(tmp_path / "by_sid.duckdb"))
    store.upsert_trade({
        "trade_id": "a", "strategy_id": "s1", "status": "closed",
        "symbol": "BTC/USD", "direction": "LONG", "side": "buy",
        "net_pnl_usd": 10.0, "notional_usd": 100.0,
    })
    store.upsert_trade({
        "trade_id": "b", "strategy_id": "s1", "status": "closed",
        "symbol": "BTC/USD", "direction": "LONG", "side": "buy",
        "net_pnl_usd": -4.0, "notional_usd": 80.0,
    })
    store.upsert_trade({
        "trade_id": "c", "strategy_id": "s2", "status": "closed",
        "symbol": "ETH/USD", "direction": "SHORT", "side": "sell",
        "net_pnl_usd": 2.5, "notional_usd": 50.0,
    })
    store.upsert_trade({
        "trade_id": "d", "strategy_id": "s1", "status": "open",
        "symbol": "BTC/USD", "direction": "LONG", "side": "buy",
        "net_pnl_usd": 99.0, "notional_usd": 9.0,
    })
    by_sid = store.closed_pnl_by_strategy()
    assert by_sid["s1"]["n"] == 2
    assert by_sid["s1"]["wins"] == 1
    assert by_sid["s1"]["realized"] == 6.0
    assert by_sid["s1"]["volume"] == 180.0
    assert by_sid["s2"]["n"] == 1
    assert by_sid["s2"]["wins"] == 1
    assert by_sid["s2"]["realized"] == 2.5
    assert "s1" in by_sid and "s2" in by_sid
