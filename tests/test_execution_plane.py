"""Tests fuer die Execution-Plane des Blueprints v3.6 §23-§29."""
from __future__ import annotations

import json
import os
import time

import pytest

from app.core import blueprint as bp
from app.core.exchange_clock import ExchangeClock, StaleSignalError, set_exchange_clock
from app.core.rate_limiter import (AlertSlotRegistry, ProviderRateLimiter,
                                   RateLimitExceeded)
from app.core.scheduler_matrix import SchedulerMatrix, _cron_next
from app.execution.capital_flywheel_engine import (CapitalFlywheelEngine,
                                                   FlywheelViolation)
from app.execution.fixed_leverage import (DynamicLeverageRejected, LeverageProfile,
                                          clamp_leverage, load_profile, save_profile)
from app.execution.reliable_order_dispatcher import (OrderRequest,
                                                     ReliableOrderDispatcher,
                                                     build_idempotency_key,
                                                     is_retryable)
from app.quant.epidemic_contagion_engine import (ContagionInputs,
                                                 EpidemicContagionEngine)
from app.quant.glint_orderbook_verifier import (GlintOrderbookVerifier,
                                                OrderbookSnapshot, depth_imbalance)


# ------------------------------------------------------------------ §23.1 ---

class _FakeHostClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_clock_offset_is_kraken_minus_host():
    host = _FakeHostClock(1000.0)
    clock = ExchangeClock(fetcher=lambda: 1007.0, host_time=host)
    status = clock.sync()
    assert status.synced is True
    assert clock.offset_s == pytest.approx(7.0)
    assert clock.now() == pytest.approx(1007.0)
    assert status.drift_warning is True  # 7s > 2s Warnschwelle


def test_clock_resync_respects_interval_and_force():
    host = _FakeHostClock(1000.0)
    values = iter([1000.0, 1005.0])
    clock = ExchangeClock(fetcher=lambda: next(values), host_time=host,
                          resync_interval_s=3600)
    clock.sync()
    clock.sync()                       # zu frueh -> kein zweiter Fetch
    assert clock.offset_s == pytest.approx(0.0)
    host.advance(3601)
    clock.sync()
    assert clock.offset_s == pytest.approx(5.0 - 3601)


def test_clock_ping_records_rtt_and_survives_failure():
    host = _FakeHostClock(1000.0)
    clock = ExchangeClock(fetcher=lambda: 1000.05, host_time=host)
    ok = clock.ping()
    assert ok["ok"] is True and clock.last_rtt_ms is not None

    def boom() -> float:
        raise RuntimeError("offline")

    bad = ExchangeClock(fetcher=boom, host_time=host)
    fail = bad.ping()
    assert fail["ok"] is False and bad.last_rtt_ms is None


def test_clock_survives_failed_sync():
    def boom() -> float:
        raise RuntimeError("no network")

    clock = ExchangeClock(fetcher=boom, host_time=_FakeHostClock(500.0))
    status = clock.sync()
    assert status.synced is False
    assert "RuntimeError" in (status.last_error or "")
    assert clock.now() == pytest.approx(500.0)


def test_stale_signal_gate_accepts_ms_and_rejects_old():
    host = _FakeHostClock(1_700_000_000.0)
    clock = ExchangeClock(fetcher=lambda: 1_700_000_000.0, host_time=host)
    clock.sync()
    assert clock.is_signal_stale(1_700_000_000_000) is False    # ms-Payload
    assert clock.is_signal_stale(1_700_000_000 - 5) is False
    assert clock.is_signal_stale(1_700_000_000 - 120) is True
    with pytest.raises(StaleSignalError) as exc:
        clock.assert_fresh(1_700_000_000 - 120)
    assert bp.STALE_SIGNAL_REJECT_CODE in str(exc.value)
    set_exchange_clock(None)


# ------------------------------------------------------------------ §23.2 ---

def _scheduler(host: _FakeHostClock) -> SchedulerMatrix:
    clock = ExchangeClock(fetcher=lambda: host.t, host_time=host)
    clock.sync()
    return SchedulerMatrix(clock=clock)


def test_install_canonical_tasks_registers_loop_graph():
    from app.core.scheduler_matrix import install_canonical_tasks
    from app.execution.deadman_switch_daemon import DeadmanSwitchDaemon

    class _Mem:
        def check(self):
            return {"stage": 0}

    class _Card:
        def idle_stage1_tick(self):
            return {"skipped": True}

    host = _FakeHostClock(1_700_000_000.0)
    sched = _scheduler(host)
    install_canonical_tasks(
        sched, deadman=DeadmanSwitchDaemon(), memory=_Mem(), scorecard=_Card(),
    )
    names = {task.name for task in sched.tasks}
    for expected in (
        "glint_orderbook_verify",
        "webhook_execution",
        "playwright_compile",
        "deadman_heartbeat",
        "memory_watchdog",
        "scorecard_stage1_idle",
        "loop_c_feed_poll",
        "scout_incubator_cycle",
        "master_orchestrator_tick",
        "strategy_allocator",
        "regime_recheck",
        "academy_badge_recalibration",
    ):
        assert expected in names, expected
    scout = sched.get("scout_incubator_cycle")
    assert scout is not None
    assert scout.cadence_s == float(bp.SCOUT_INCUBATOR_CYCLE_MINUTES) * 60.0
    assert sched.get("webhook_execution").tier == 0
    assert sched.get("playwright_compile").tier == 0
    assert sched.get("academy_badge_recalibration").tier == 5


def test_scheduler_matrix_covers_all_six_tiers():
    assert [int(spec.tier) for spec in bp.SCHEDULER_MATRIX] == [0, 1, 2, 3, 4, 5]
    tier1 = next(s for s in bp.SCHEDULER_MATRIX if int(s.tier) == 1)
    assert tier1.cadence_s == 20.0
    tier4 = next(s for s in bp.SCHEDULER_MATRIX if int(s.tier) == 4)
    assert tier4.cron == "05 00 * * *"      # 00:05 UTC Rebalance


def test_scheduler_runs_tier1_on_cadence():
    host = _FakeHostClock(1_700_000_000.0)
    sched = _scheduler(host)
    calls = []
    sched.register("deadman_heartbeat", 1, lambda: calls.append(host.t))
    assert sched.run_due() == []           # noch nicht faellig
    host.advance(21)
    assert len(sched.run_due()) == 1
    assert len(calls) == 1
    host.advance(5)
    assert sched.run_due() == []
    host.advance(20)
    sched.run_due()
    assert len(calls) == 2


def test_scheduler_tier0_is_event_only():
    host = _FakeHostClock(1_700_000_000.0)
    sched = _scheduler(host)
    fired = []
    sched.register("glint_orderbook_verify", 0, lambda: fired.append(1))
    host.advance(10_000)
    assert sched.run_due() == []           # Tier 0 wird nie gepollt
    result = sched.fire_event("glint_orderbook_verify")
    assert result["status"] == "ok" and fired == [1]
    with pytest.raises(ValueError):
        sched.register("bad_tier0", 0, lambda: None, cadence_s=5)


def test_scheduler_task_error_is_isolated():
    host = _FakeHostClock(1_700_000_000.0)
    sched = _scheduler(host)

    def bad() -> None:
        raise ValueError("kaputt")

    sched.register("macro_radar_scraper", 2, bad)
    host.advance(301)
    results = sched.run_due()
    assert results[0]["status"] == "error"
    task = sched.get("macro_radar_scraper")
    assert task.errors == 1 and "kaputt" in task.last_error
    assert task.next_run > host.t          # trotzdem neu eingeplant


def test_cron_next_daily_and_weekly():
    # 2023-11-14 12:00:00 UTC (Dienstag)
    base = 1_699_963_200.0
    daily = _cron_next("05 00 * * *", base)
    assert time.gmtime(daily).tm_hour == 0 and time.gmtime(daily).tm_min == 5
    assert 0 < daily - base <= 86400
    weekly = _cron_next("00 23 * * 0", base)   # Sonntag 23:00 UTC
    tm = time.gmtime(weekly)
    assert tm.tm_wday == 6 and tm.tm_hour == 23


def test_scheduler_telemetry_shape():
    host = _FakeHostClock(1_700_000_000.0)
    sched = _scheduler(host)
    sched.register("spot_rebalance", 4, lambda: None)
    sched.register("glint_orderbook_verify", 0, lambda: None)
    telemetry = sched.telemetry()
    assert telemetry["timezone"] == "UTC"
    assert len(telemetry["tiers"]) == 6
    tier4 = telemetry["tiers"][4]
    assert tier4["registered"][0]["name"] == "spot_rebalance"
    json.dumps(telemetry)
    t0 = next(tier for tier in telemetry["tiers"] if tier["tier"] == 0)
    assert t0["registered"][0]["next_run"] is None


def test_scheduler_endpoint_json_roundtrip():
    from fastapi.testclient import TestClient

    from app.core.scheduler_matrix import set_scheduler
    from app.server.main import app

    sched = _scheduler(_FakeHostClock(1_700_000_000.0))
    sched.register("glint_orderbook_verify", 0, lambda: None)
    set_scheduler(sched)
    try:
        client = TestClient(app)
        response = client.get("/api/v1/scheduler")
        assert response.status_code == 200
        payload = response.json()
        json.dumps(payload)
        t0 = next(tier for tier in payload["tiers"] if tier["tier"] == 0)
        assert t0["registered"]
        for task in t0["registered"]:
            assert task["next_run"] is None
    finally:
        set_scheduler(None)


# -------------------------------------------------------------------- §24 ---

def _book(bid_vol: float, ask_vol: float, ts: float = 0.0,
          spread_bps: float = 2.0) -> OrderbookSnapshot:
    mid = 100.0
    half = mid * spread_bps / 20_000.0
    return OrderbookSnapshot(
        symbol="XBTEUR",
        bids=[(mid - half, bid_vol), (mid * 0.995, bid_vol)],
        asks=[(mid + half, ask_vol), (mid * 1.005, ask_vol)],
        timestamp=ts,
    )


def test_depth_imbalance_formula():
    assert depth_imbalance(70, 30) == pytest.approx(0.4)
    assert depth_imbalance(0, 0) == 0.0


def test_confluence_confirmed_gives_1_25_multiplier():
    verifier = GlintOrderbookVerifier()
    res = verifier.verify(_book(80, 20), "BULLISH")
    assert res.verdict == bp.ConfluenceVerdict.CONFLUENCE_CONFIRMED.value
    assert res.size_multiplier == bp.CONFLUENCE_SIZE_MULTIPLIER
    assert res.approved is True


def test_liquidity_trap_veto_on_opposing_wall():
    verifier = GlintOrderbookVerifier()
    res = verifier.verify(_book(20, 80), "BUY")
    assert res.verdict == bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value
    assert res.reject_code == bp.ORDERBOOK_WALL_REJECT
    assert res.size_multiplier == 0.0 and res.approved is False


def test_bearish_direction_is_mirrored():
    verifier = GlintOrderbookVerifier()
    confirmed = verifier.verify(_book(20, 80), "SELL")
    assert confirmed.verdict == bp.ConfluenceVerdict.CONFLUENCE_CONFIRMED.value
    vetoed = verifier.verify(_book(80, 20), "BEARISH")
    assert vetoed.verdict == bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value


def test_wide_spread_blocks_bonus_but_not_entry():
    verifier = GlintOrderbookVerifier()
    res = verifier.verify(_book(80, 20, spread_bps=40.0), "BULLISH")
    assert res.verdict == bp.ConfluenceVerdict.NEUTRAL.value
    assert res.size_multiplier == 1.0 and res.approved is True


def test_stale_depth_snapshot_is_vetoed():
    verifier = GlintOrderbookVerifier()
    res = verifier.verify(_book(80, 20, ts=100.0), "BULLISH", now=110.0)
    assert res.verdict == bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value
    assert "alt" in res.reason


# -------------------------------------------------------------------- §25 ---

class _FakeResult:
    def __init__(self, ok: bool, txid: str = "", error_code: str = "") -> None:
        self.ok = ok
        self.txid = txid
        self.error_code = error_code
        self.mode = "sim"
        self.stdout = ""
        self.stderr = error_code


class _FakeBridge:
    def __init__(self, results, open_orders=None) -> None:
        self._results = list(results)
        self.calls = []
        self._open = open_orders or []

    def add_order(self, **kwargs):
        self.calls.append(kwargs)
        return self._results.pop(0) if self._results else _FakeResult(True, "TX")

    def open_orders(self):
        return self._open


def _request(**over) -> OrderRequest:
    base = dict(idempotency_key="sig_a_XBTEUR_1", strategy_id="a", pair="XBTEUR",
                side="buy", volume=0.01, stop_loss=90.0, fixed_leverage=5)
    base.update(over)
    return OrderRequest(**base)


def _dispatcher(bridge, tmp_path) -> ReliableOrderDispatcher:
    return ReliableOrderDispatcher(bridge, receipts_log=str(tmp_path / "orders.jsonl"))


def test_first_success_is_filled_and_passes_leverage(tmp_path):
    bridge = _FakeBridge([_FakeResult(True, "TXID-1")])
    disp = _dispatcher(bridge, tmp_path)
    receipt = disp.dispatch(_request())
    assert receipt.ack == bp.OrderAck.FILLED.value
    assert receipt.order_id == "TXID-1" and receipt.attempts == 1
    assert bridge.calls[0]["leverage"] == 5
    assert bridge.calls[0]["stop_price"] == 90.0


def test_duplicate_signal_is_ignored(tmp_path):
    bridge = _FakeBridge([_FakeResult(True, "TXID-1")])
    disp = _dispatcher(bridge, tmp_path)
    disp.dispatch(_request())
    dup = disp.dispatch(_request())
    assert dup.ack == bp.OrderAck.DUPLICATE_IGNORED.value
    assert len(bridge.calls) == 1


def test_transient_failure_retries_then_succeeds(tmp_path):
    bridge = _FakeBridge([_FakeResult(False, error_code="EService:Unavailable"),
                          _FakeResult(True, "TXID-2")])
    disp = _dispatcher(bridge, tmp_path)
    receipt = disp.dispatch(_request())
    assert receipt.ack == bp.OrderAck.RETRY_SUCCESS.value
    assert receipt.attempts == 2


def test_no_retry_on_insufficient_funds(tmp_path):
    bridge = _FakeBridge([_FakeResult(False, error_code="EOrder:Insufficient funds")])
    disp = _dispatcher(bridge, tmp_path)
    receipt = disp.dispatch(_request())
    assert receipt.ack == bp.OrderAck.FAILED_REJECTED.value
    assert receipt.attempts == 1 and len(bridge.calls) == 1
    assert is_retryable("EOrder:Insufficient funds") is False
    assert is_retryable("EGeneral:Invalid arguments") is False
    assert is_retryable("EService:Busy") is True


def test_max_two_retries(tmp_path):
    bridge = _FakeBridge([_FakeResult(False, error_code="EService:Busy")] * 5)
    disp = _dispatcher(bridge, tmp_path)
    receipt = disp.dispatch(_request())
    assert receipt.attempts == bp.ORDER_MAX_RETRIES + 1 == 3
    assert len(bridge.calls) == 3


def test_ghost_fill_prevents_double_order(tmp_path):
    bridge = _FakeBridge(
        [_FakeResult(False, error_code="EService:Timeout")] * 3,
        open_orders=[{"pair": "XBTEUR", "side": "buy"}],
    )
    disp = _dispatcher(bridge, tmp_path)
    receipt = disp.dispatch(_request())
    assert receipt.ghost_fill_detected is True
    assert receipt.ack == bp.OrderAck.FILLED.value
    assert len(bridge.calls) == 1


def test_receipts_are_written_to_jsonl(tmp_path):
    log = tmp_path / "orders.jsonl"
    bridge = _FakeBridge([_FakeResult(True, "TXID-9")])
    disp = ReliableOrderDispatcher(bridge, receipts_log=str(log))
    disp.dispatch(_request())
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert lines[0]["type"] == "receipt" and lines[0]["success"] is True
    assert disp.panel_state()["route"] == bp.ORDER_RECEIPTS_ROUTE


def test_orderbook_veto_receipt(tmp_path):
    disp = _dispatcher(_FakeBridge([]), tmp_path)
    receipt = disp.veto(_request(), "Wand im Buch")
    assert receipt.ack == bp.OrderAck.VETO_ORDERBOOK.value
    assert receipt.error_code == bp.ORDERBOOK_WALL_REJECT


def test_paper_mode_routes_to_paper_bridge(tmp_path):
    live, paper = _FakeBridge([]), _FakeBridge([_FakeResult(True, "PAPER-1")])
    disp = ReliableOrderDispatcher(live, paper_bridge=paper,
                                   receipts_log=str(tmp_path / "o.jsonl"))
    receipt = disp.dispatch(_request(execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value))
    assert receipt.order_id == "PAPER-1"
    assert live.calls == [] and len(paper.calls) == 1


def test_idempotency_key_template():
    key = build_idempotency_key("cisd_v6", "XRPUSD", 1787786800)
    assert key == "sig_cisd_v6_XRPUSD_1787786800"
    assert len(key) >= bp.IDEMPOTENCY_KEY_MIN_LENGTH


# -------------------------------------------------------------------- §26 ---

def test_kraken_bucket_decays_and_reserves_emergency_tokens():
    now = [0.0]
    lim = ProviderRateLimiter(clock=lambda: now[0])
    # 15.0 max, 3.0 Reserve -> 12 regulaere Calls
    for _ in range(12):
        lim.acquire("kraken_api")
    with pytest.raises(RateLimitExceeded):
        lim.acquire("kraken_api")
    lim.acquire("kraken_api", emergency=True)      # Kill-Switch kommt durch
    now[0] += 10.0                                  # 0.5/s -> 5 Tokens frei
    lim.acquire("kraken_api")


def test_soft_cap_pauses_background_polls():
    now = [0.0]
    lim = ProviderRateLimiter(clock=lambda: now[0])
    for _ in range(12):
        lim.acquire("kraken_api")
    assert lim.soft_cap_reached() is True
    with pytest.raises(RateLimitExceeded):
        lim.acquire("kraken_api", background=True)


def test_telegram_one_message_per_second():
    now = [0.0]
    lim = ProviderRateLimiter(clock=lambda: now[0])
    lim.acquire("telegram_bot")
    assert lim.try_acquire("telegram_bot") is False
    now[0] += 1.01
    assert lim.try_acquire("telegram_bot") is True


def test_429_backoff_ladder():
    now = [0.0]
    lim = ProviderRateLimiter(clock=lambda: now[0])
    assert lim.note_429("kraken_api") == 10.0
    assert lim.note_429("kraken_api") == 30.0
    assert lim.note_429("kraken_api") == 60.0
    assert lim.note_429("kraken_api") == 60.0
    with pytest.raises(RateLimitExceeded):
        lim.acquire("kraken_api")
    lim.note_success("kraken_api")
    lim.acquire("kraken_api")


def test_alert_slot_rotation_replaces_weakest():
    slots = AlertSlotRegistry(tier="essential")
    assert slots.max_slots == 5
    for idx in range(5):
        slots.request_slot(f"s{idx}", score=float(idx))
    assert slots.free_slots == 0
    outcome = slots.request_slot("winner", score=9.0)
    assert outcome["granted"] is True and outcome["rotated_out"] == "s0"
    assert "s0" in slots.queue
    weak = slots.request_slot("loser", score=0.1)
    assert weak["granted"] is False and weak["queued"] is True


def test_alert_slot_release_promotes_from_queue():
    slots = AlertSlotRegistry(tier="free")
    slots.request_slot("a", score=1.0)
    slots.request_slot("b", score=0.5)   # kein Slot -> Queue
    promoted = slots.release_slot("a")
    assert promoted == "b" and "b" in slots.active


def test_rate_limiter_status_shape():
    status = ProviderRateLimiter().status()
    assert status["kraken_api"]["max_counter"] == bp.KRAKEN_MAX_CALL_COUNTER
    assert status["tradingview_subscription"]["max_active_alerts"] == 5
    assert status["backoff_ladder_s"] == [10.0, 30.0, 60.0]


# -------------------------------------------------------------------- §27 ---

def test_contagion_normal_regime():
    eng = EpidemicContagionEngine()
    state = eng.evaluate(ContagionInputs(oil_vol_zscore=0.2,
                                         cross_asset_correlation=0.1,
                                         orderbook_absorption=0.9))
    assert state.mode == bp.ContagionMode.NORMAL.value
    assert state.size_multiplier == 1.0
    assert eng.treasury_allowed("SOL") is True


def test_contagion_derisk_halves_futures_sizing():
    eng = EpidemicContagionEngine()
    state = eng.evaluate(ContagionInputs(oil_vol_zscore=2.5,
                                         cross_asset_correlation=0.6,
                                         orderbook_absorption=0.6))
    assert bp.SIR_R0_DERISK_THRESHOLD <= state.r0 < bp.SIR_R0_HEDGE_THRESHOLD
    assert state.mode == bp.ContagionMode.DERISK.value
    assert eng.apply_sizing(1000.0) == 500.0
    assert eng.treasury_allowed("SOL") is False
    assert eng.treasury_allowed("XBT") is True


def test_contagion_flight_to_cash():
    eng = EpidemicContagionEngine()
    state = eng.evaluate(ContagionInputs(oil_vol_zscore=4.0,
                                         gold_dxy_ratio_change=0.05,
                                         cross_asset_correlation=0.95,
                                         orderbook_absorption=0.2))
    assert state.r0 >= bp.SIR_R0_HEDGE_THRESHOLD
    assert state.mode == bp.ContagionMode.FLIGHT_TO_CASH_AND_HEDGE.value
    assert state.veto_code == bp.CONTAGION_VETO_CODE
    assert eng.apply_sizing(1000.0) == 0.0


def test_contagion_r0_is_beta_over_gamma():
    eng = EpidemicContagionEngine()
    inputs = ContagionInputs(oil_vol_zscore=1.5, cross_asset_correlation=0.5,
                             orderbook_absorption=0.5)
    assert eng.r0(inputs) == pytest.approx(eng.beta(inputs) / eng.gamma(inputs))


# -------------------------------------------------------------------- §28 ---

def test_deposit_goes_fully_to_futures():
    fly = CapitalFlywheelEngine()
    fly.deposit(1000.0)
    assert fly.state.futures_balance_eur == 1000.0
    assert fly.state.vault_balance_eur == 0.0


def test_profit_below_trigger_is_not_split():
    fly = CapitalFlywheelEngine()
    fly.deposit(500.0)
    out = fly.register_realized_profit(5.0)
    assert out["split"] is False
    assert fly.state.vault_balance_eur == 0.0


def test_profit_split_is_fifty_fifty():
    fly = CapitalFlywheelEngine()
    fly.deposit(500.0)
    out = fly.register_realized_profit(100.0)
    assert out["split"] is True
    assert out["reinvest_eur"] == 50.0 and out["vault_eur"] == 50.0
    assert fly.state.vault_balance_eur == 50.0
    assert fly.state.futures_balance_eur == 550.0
    assert out["vault_asset"] == bp.FLYWHEEL_DEFAULT_VAULT_ASSET


def test_vault_to_futures_requires_operator():
    fly = CapitalFlywheelEngine()
    fly.deposit(100.0)
    fly.register_realized_profit(100.0)
    with pytest.raises(FlywheelViolation):
        fly.transfer_vault_to_futures(10.0)
    fly.transfer_vault_to_futures(10.0, operator_confirmed=True)
    assert fly.state.vault_balance_eur == 40.0


def test_bot_budget_reservation_respects_free_capital():
    fly = CapitalFlywheelEngine()
    fly.deposit(300.0)
    assert fly.allocate_bot_budget("s1", 250.0)["reserved"] is True
    denied = fly.allocate_bot_budget("s2", 100.0)
    assert denied["reserved"] is False
    assert denied["reason"] == "INSUFFICIENT_FREE_FUTURES"
    fly.release_bot_budget("s1", 250.0)
    assert fly.allocate_bot_budget("s2", 100.0)["reserved"] is True


def test_flywheel_ledger_is_persisted():
    rows = []

    class _Store:
        def insert_row(self, table, row):
            rows.append((table, row))

    fly = CapitalFlywheelEngine(store=_Store())
    fly.deposit(50.0)
    assert rows and rows[0][0] == bp.FLYWHEEL_LEDGER_TABLE
    assert fly.panel_state()["one_way"] is True


# -------------------------------------------------------------------- §29 ---

def test_leverage_is_clamped_to_1_5():
    assert clamp_leverage(9) == bp.FIXED_LEVERAGE_MAX == 5
    assert clamp_leverage(0) == bp.FIXED_LEVERAGE_MIN == 1
    assert clamp_leverage("3") == 3
    assert clamp_leverage(None) == bp.FIXED_LEVERAGE_DEFAULT


def test_style_defaults_match_blueprint_table():
    assert bp.STYLE_DEFAULT_LEVERAGE["STYLE_MICRO_SCALP"] == 5
    assert bp.STYLE_DEFAULT_LEVERAGE["STYLE_INTRADAY_MOMENT"] == 3
    assert bp.STYLE_DEFAULT_LEVERAGE["STYLE_SWING_CAMPAIGN"] == 2
    assert bp.DYNAMIC_LEVERAGE_REJECTED is True


def test_profile_roundtrip_and_badge(tmp_path):
    profile = LeverageProfile("cisd_sniper_breakout_v6", 5, "STYLE_MICRO_SCALP")
    path = save_profile(profile, strategies_root=str(tmp_path))
    assert os.path.exists(path)
    loaded = load_profile("cisd_sniper_breakout_v6", strategies_root=str(tmp_path))
    assert loaded.fixed_leverage == 5 and loaded.source == "profile.json"
    assert loaded.badge == "[ 5x HEBEL ]"
    assert loaded.as_dict()["cli_flag"] == "--leverage=5"


def test_missing_profile_falls_back_to_style(tmp_path):
    loaded = load_profile("unknown", strategies_root=str(tmp_path),
                          style="STYLE_SWING_CAMPAIGN")
    assert loaded.fixed_leverage == 2 and loaded.source == "style_default"


def test_dynamic_leverage_config_is_rejected(tmp_path):
    target = tmp_path / "dyn" / "profile.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"strategy_id": "dyn", "fixed_leverage": 3,
                                  "dynamic_leverage": True}))
    with pytest.raises(DynamicLeverageRejected):
        load_profile("dyn", strategies_root=str(tmp_path))


# -------------------------------------------------------------------- §30 ---

def test_extended_panels_and_presets_are_frozen():
    for panel in ("OrderbookConfluencePanel", "SchedulerTelemetryPanel",
                  "OrderReceiptsPanel", "RateLimiterPanel", "ContagionRadarPanel",
                  "FlywheelBudgetPanel", "PaperLabPanel", "DiagnosticsErrorPanel",
                  "NetronVisualizerPanel", "ProcessLogView"):
        assert panel in bp.ALL_TERMINAL_PANELS
    for preset in ("CAPITAL_OPS", "PAPER_LAB", "OBSERVABILITY", "ML_INSPECTOR"):
        assert preset in bp.ALL_TERMINAL_PRESETS
    assert bp.PORT_NETRON == 8082


# ----------------------------------------------------------------- API §30 ---

def test_execution_plane_endpoints_are_mounted():
    from fastapi.testclient import TestClient

    import app.server.routes_sigma as routes
    from app.server.main import app

    previous = routes._FLYWHEEL
    routes.set_flywheel(CapitalFlywheelEngine())
    try:
        client = TestClient(app)
        for path in ("/api/v1/scheduler", "/api/v1/rate-limiter", "/api/v1/contagion",
                     "/api/v1/flywheel", "/api/orders/receipts",
                     "/api/v1/orderbook/confluence", "/api/v1/leverage/demo_strategy"):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        json.dumps(client.get("/api/v1/scheduler").json())
        json.dumps(client.get("/api/v1/contagion").json())
    finally:
        routes.set_flywheel(previous)


def test_confluence_endpoint_vetoes_wall():
    from fastapi.testclient import TestClient

    from app.server.main import app

    client = TestClient(app)
    payload = {
        "symbol": "XBTEUR", "direction": "BUY",
        "bids": [{"price": 99.99, "volume": 1.0}],
        "asks": [{"price": 100.01, "volume": 40.0}],
    }
    body = client.post("/api/v1/orderbook/confluence", json=payload).json()
    assert body["verdict"] == bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value
    assert body["reject_code"] == bp.ORDERBOOK_WALL_REJECT


def test_flywheel_endpoints_split_profit():
    from fastapi.testclient import TestClient

    import app.server.routes_sigma as routes
    from app.server.main import app

    routes.set_operator_auth_override(lambda request: True)
    previous = routes._FLYWHEEL
    routes.set_flywheel(CapitalFlywheelEngine())
    client = TestClient(app)
    try:
        client.post("/api/v1/flywheel/deposit", json={"amount_eur": 400.0})
        out = client.post("/api/v1/flywheel/profit",
                          json={"amount_eur": 60.0, "strategy_id": "s1"}).json()
        assert out["result"]["split"] is True
        assert out["result"]["reinvest_eur"] == out["result"]["vault_eur"] == 30.0
    finally:
        routes.set_operator_auth_override(None)
        routes.set_flywheel(previous)


def test_flywheel_mutations_require_operator_token():
    from fastapi.testclient import TestClient

    from app.server.main import app

    response = TestClient(app).post(
        "/api/v1/flywheel/deposit", json={"amount_eur": 10.0}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "OPERATOR_AUTH_REQUIRED"


def test_flywheel_fails_closed_without_appstate():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.server.routes_sigma as routes

    previous = routes._FLYWHEEL
    routes.set_flywheel(None)
    try:
        bare = FastAPI()
        bare.include_router(routes.router)
        response = TestClient(bare).get("/api/v1/flywheel")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "FLYWHEEL_UNAVAILABLE"
    finally:
        routes.set_flywheel(previous)
