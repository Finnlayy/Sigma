"""End-to-end seams for the five completed execution-plane modules."""
from __future__ import annotations

import math
import time

import pytest

from app.core import blueprint as bp
from app.core.duckdb_store import DuckDBStore
from app.core.memory_watchdog import MemoryWatchdog
from app.core.rate_limiter import ProviderRateLimiter
from app.core.telemetry import TelemetryCenter
from app.execution.KrakenCliBridge import KrakenCliBridge
from app.execution.LoopAPipeline import LoopAPipeline, SignalRequest
from app.execution.SafetyGuard import SafetyGuard
from app.execution.capital_flywheel_engine import CapitalFlywheelEngine
from app.execution.capital_vault_executor import CapitalVaultExecutor
from app.execution.kraken_fill_reconciler import (FILL_POLL_OVERLAP_S,
                                                 KrakenFillReconciler)
from app.execution.reliable_order_dispatcher import ReliableOrderDispatcher
from app.execution.reliable_order_dispatcher import OrderRequest
from app.ingestion.kraken_depth_adapter import KrakenDepthAdapter
from app.ingestion.macro_contagion_feed import MacroContagionFeed
from app.quant.epidemic_contagion_engine import (ContagionInputs,
                                                 EpidemicContagionEngine,
                                                 set_contagion_engine)
from app.quant.glint_orderbook_verifier import OrderbookSnapshot


def _kraken_depth_payload(bid: float = 80.0, ask: float = 20.0) -> dict:
    return {
        "error": [],
        "result": {
            "XXBTZUSD": {
                "bids": [["99.99", str(bid), "1"]],
                "asks": [["100.01", str(ask), "1"]],
            }
        },
    }


def test_kraken_depth_adapter_normalizes_and_rate_limits():
    limiter = ProviderRateLimiter()
    adapter = KrakenDepthAdapter(
        limiter=limiter,
        fetcher=lambda pair, count: _kraken_depth_payload(),
    )
    snapshot = adapter.fetch("BTC/USD")
    assert snapshot.symbol == "XBTUSD"
    assert snapshot.best_bid == pytest.approx(99.99)
    assert snapshot.best_ask == pytest.approx(100.01)
    assert limiter.status()["kraken_api"]["counter"] > 0
    assert 0.05 <= adapter.absorption(snapshot) <= 1.0


def test_macro_feed_builds_all_four_contagion_inputs():
    base = [100.0 * math.exp(index * 0.002) for index in range(90)]

    def series(_exchange: str, ticker: str, _count: int):
        values = list(base)
        if ticker == "USOIL":
            values[-10:] = [values[-11] * (1.0 + 0.02 * index) for index in range(1, 11)]
        if ticker == "GOLD":
            values[-1] *= 1.02
        return values

    depth = KrakenDepthAdapter(fetcher=lambda pair, count: _kraken_depth_payload(55, 45))
    inputs = MacroContagionFeed(
        series_fetcher=series,
        depth=depth,
    ).snapshot()
    assert inputs.oil_vol_zscore > 0
    assert inputs.gold_dxy_ratio_change > 0
    assert 0.0 <= inputs.cross_asset_correlation <= 1.0
    assert 0.05 <= inputs.orderbook_absorption <= 1.0


def test_flywheel_and_contagion_survive_restart(tmp_path):
    store = DuckDBStore(str(tmp_path / "runtime.duckdb"))
    flywheel = CapitalFlywheelEngine(store=store)
    flywheel.deposit(500.0)
    flywheel.register_realized_profit(100.0, strategy_id="s1")

    restored = CapitalFlywheelEngine(store=store)
    assert restored.state.futures_balance_eur == pytest.approx(550.0)
    assert restored.state.vault_balance_eur == pytest.approx(50.0)

    contagion = EpidemicContagionEngine(store=store)
    state = contagion.evaluate(ContagionInputs(
        oil_vol_zscore=2.5,
        cross_asset_correlation=0.6,
        orderbook_absorption=0.6,
    ))
    restored_contagion = EpidemicContagionEngine(store=store)
    assert restored_contagion.state.mode == state.mode
    assert restored_contagion.state.r0 == pytest.approx(state.r0)
    store.close()


def test_flywheel_executes_vault_purchase_only_after_live_gate(tmp_path):
    class Result:
        ok = True
        mode = "live"
        txid = "SPOT-1"
        error_code = ""

    class Bridge:
        live_enabled = True

        def __init__(self):
            self.calls = []

        def add_order(self, **kwargs):
            self.calls.append(kwargs)
            return Result()

    class Depth:
        def fetch(self, symbol):
            return OrderbookSnapshot(symbol, [(99.0, 1.0)], [(101.0, 1.0)], time.time())

    bridge = Bridge()
    executor = CapitalVaultExecutor(bridge, Depth(), enabled=True)
    store = DuckDBStore(str(tmp_path / "live-sweep.duckdb"))
    flywheel = CapitalFlywheelEngine(store=store, vault_executor=executor)
    flywheel.deposit(500.0)
    outcome = flywheel.register_realized_profit(100.0)
    assert outcome["execution"]["executed"] is True
    assert bridge.calls[0]["pair"] == "XBTEUR"
    assert bridge.calls[0]["volume"] == pytest.approx(0.5)

    bridge.live_enabled = False
    blocked_store = DuckDBStore(str(tmp_path / "blocked-sweep.duckdb"))
    blocked = CapitalFlywheelEngine(
        store=blocked_store,
        vault_executor=CapitalVaultExecutor(bridge, Depth(), enabled=True)
    )
    blocked.deposit(500.0)
    outcome = blocked.register_realized_profit(100.0)
    assert outcome["split"] is False
    assert blocked.state.pending_profit_eur == 100.0
    store.close()
    blocked_store.close()


def test_ambiguous_vault_purchase_requires_operator_reconciliation(tmp_path):
    class UncertainExecutor:
        enabled = True

        def __call__(self, asset, amount):
            raise TimeoutError("exchange result unknown")

    store = DuckDBStore(str(tmp_path / "reconcile.duckdb"))
    flywheel = CapitalFlywheelEngine(store=store, vault_executor=UncertainExecutor())
    flywheel.deposit(500.0)
    outcome = flywheel.register_realized_profit(100.0)
    assert outcome["split"] is False
    assert flywheel.state.reconciliation_required is True

    restored = CapitalFlywheelEngine(store=store, vault_executor=UncertainExecutor())
    assert restored.sweep()["reason"] == "vault purchase requires operator reconciliation"
    resolved = restored.reconcile_vault_purchase(executed=False)
    assert resolved["reconciled"] is True
    assert restored.state.pending_profit_eur == 100.0
    store.close()


def test_verified_live_fill_reconciler_is_durable_and_idempotent(tmp_path):
    class Bridge:
        def futures_fills(self, since=None):
            return [{
                "fill_id": "fill-1",
                "client_order_id": "strategy-1",
                "symbol": "PF_XBTUSD",
                "realized_pnl": "-12.5",
                "timestamp": time.time(),
            }]

    store = DuckDBStore(str(tmp_path / "fills.duckdb"))
    handled = []
    reconciler = KrakenFillReconciler(Bridge(), store, handled.append)
    assert reconciler.poll()["applied"] == 1
    assert reconciler.poll()["applied"] == 0
    assert len(handled) == 1
    assert handled[0]["accounting_source"] == "verified_live_fill"
    assert handled[0]["net_pnl_usd"] == pytest.approx(-12.5)
    assert store.reconciled_fill_status("fill-1") == "applied"
    store.close()


def test_fill_reconciler_resumes_from_durable_watermark(tmp_path):
    class Bridge:
        def __init__(self):
            self.since_values = []

        def futures_fills(self, since=None):
            self.since_values.append(since)
            return []

    store = DuckDBStore(str(tmp_path / "watermark.duckdb"))
    old_ts = time.time() - 7200
    store.record_reconciled_fill({
        "fill_id": "old-fill",
        "ts": old_ts,
        "strategy_id": "s1",
        "symbol": "PF_XBTUSD",
        "net_pnl_usd": 1.0,
        "payload": {},
    }, status="applied")
    bridge = Bridge()
    reconciler = KrakenFillReconciler(bridge, store, lambda trade: None)
    assert reconciler.last_poll == pytest.approx(old_ts)
    assert reconciler.poll()["applied"] == 0
    assert bridge.since_values[0] == pytest.approx(old_ts - FILL_POLL_OVERLAP_S)
    store.close()


def test_fill_reconciler_retries_pending_and_failed_without_cli_repeat(tmp_path):
    class Bridge:
        def __init__(self):
            self.rows = [{
                "fill_id": "fill-retry",
                "client_order_id": "strategy-1",
                "symbol": "PF_XBTUSD",
                "realized_pnl": "4.0",
                "timestamp": time.time() - 10,
            }]

        def futures_fills(self, since=None):
            return list(self.rows)

    handled: list[object] = []

    def handler(trade):
        if not any(item == "fail" for item in handled):
            handled.append("fail")
            raise RuntimeError("accounting unavailable")
        handled.append(trade)

    store = DuckDBStore(str(tmp_path / "retry.duckdb"))
    reconciler = KrakenFillReconciler(Bridge(), store, handler)
    first = reconciler.poll()
    assert first["pending"] == 1
    assert store.reconciled_fill_status("fill-retry") == "failed"

    reconciler.bridge.rows = []
    second = reconciler.poll()
    assert second["applied"] == 1
    assert store.reconciled_fill_status("fill-retry") == "applied"
    assert handled[-1]["fill_id"] == "fill-retry"
    store.close()


def test_flywheel_and_safety_are_idempotent_on_fill_refs(tmp_path):
    store = DuckDBStore(str(tmp_path / "idempotent.duckdb"))
    flywheel = CapitalFlywheelEngine(store=store)
    flywheel.deposit(500.0)
    first = flywheel.register_realized_profit(20.0, strategy_id="s1",
                                              external_ref="fill-dup")
    assert first["split"] is True
    second = flywheel.register_realized_profit(20.0, strategy_id="s1",
                                               external_ref="fill-dup")
    assert second["reason"] == "duplicate_external_ref"
    profits = [e for e in flywheel.state.entries if e.kind == "realized_profit"]
    assert len(profits) == 1

    restored = CapitalFlywheelEngine(store=store)
    third = restored.register_realized_profit(20.0, external_ref="fill-dup")
    assert third["reason"] == "duplicate_external_ref"

    guard = SafetyGuard()
    assert guard.record_pnl(-5.0, reference_id="fill-dup") == pytest.approx(-5.0)
    assert guard.record_pnl(-5.0, reference_id="fill-dup") == pytest.approx(-5.0)
    store.close()


def test_live_spot_and_futures_ingest_fail_closed():
    import os

    from fastapi.testclient import TestClient

    import app.execution.SafetyGuard as safety_module
    import app.server.routes_sigma as routes
    from app.server.main import app

    secret = "sigma_prod_secure_token_8849"
    previous = os.environ.get("SIGMA_WEBHOOK_SECRET")
    os.environ["SIGMA_WEBHOOK_SECRET"] = secret
    safety_module._guard = None
    contagion = EpidemicContagionEngine()
    contagion.evaluate(ContagionInputs())
    set_contagion_engine(contagion)
    routes.set_pipeline(None)
    routes.set_depth_adapter(type("_Depth", (), {
        "fetch": staticmethod(lambda symbol: OrderbookSnapshot(
            symbol, [(67_999.0, 80.0)], [(68_001.0, 20.0)], time.time()
        ))
    })())
    try:
        pipe = routes.pipeline()
        pipe.config.webhook_secret = secret
        pipe.safety.config.webhook_secret = secret
        client = TestClient(app)
        futures = client.post("/api/v1/signal/ingest", json={
            "secret": secret,
            "idempotency_key": f"sig_live_future_{int(time.time())}_gate",
            "strategy_id": "cisd_sniper_breakout_v6",
            "bot_id": "bot_xbt_01",
            "symbol": "KRAKEN:XBTUSD.P",
            "action": "BUY",
            "order_type": "MARKET",
            "price": 68_000.0,
            "stop_loss": 67_000.0,
            "take_profit": 70_000.0,
            "fixed_leverage": 5,
            "execution_mode": "live",
            "timestamp": int(time.time()),
        })
        assert futures.status_code == 503
        assert futures.json()["detail"]["code"] == "FUTURES_LIVE_BRACKET_UNAVAILABLE"

        spot = client.post("/api/v1/signal/ingest", json={
            "secret": secret,
            "idempotency_key": f"sig_live_spot_{int(time.time())}_gate",
            "strategy_id": "cisd_sniper_breakout_v6",
            "bot_id": "bot_xbt_01",
            "symbol": "KRAKEN:XBTUSD",
            "action": "BUY",
            "order_type": "MARKET",
            "price": 68_000.0,
            "stop_loss": 67_000.0,
            "take_profit": 70_000.0,
            "fixed_leverage": 5,
            "execution_mode": "live",
            "timestamp": int(time.time()),
        })
        assert spot.status_code == 503
        assert spot.json()["detail"]["code"] == "SPOT_LIVE_PNL_RECONCILIATION_UNAVAILABLE"
    finally:
        safety_module._guard = None
        routes.set_pipeline(None)
        routes.set_depth_adapter(None)
        set_contagion_engine(None)
        if previous is None:
            os.environ.pop("SIGMA_WEBHOOK_SECRET", None)
        else:
            os.environ["SIGMA_WEBHOOK_SECRET"] = previous


def _signal() -> SignalRequest:
    return SignalRequest(
        symbol="XBTUSD",
        action="BUY",
        price=50_000.0,
        rsi=28.0,
        atr=500.0,
        cisd_score=0.7,
        timestamp=int(time.time()),
        strategy_id="five_modules",
        interval=15,
        secret="runtime-secret",
    )


def _pipeline(tmp_path, contagion: EpidemicContagionEngine) -> LoopAPipeline:
    from app.core.config import load_config

    cfg = load_config()
    cfg.webhook_secret = "runtime-secret"
    cfg.kill_switch_file = str(tmp_path / "KILL_SWITCH")
    cfg.pause_signal_file = str(tmp_path / "PAUSE")
    cfg.orders_log_path = str(tmp_path / "orders.jsonl")
    bridge = KrakenCliBridge(cfg)
    dispatcher = ReliableOrderDispatcher(bridge, receipts_log=cfg.orders_log_path)
    return LoopAPipeline(
        cfg,
        safety=SafetyGuard(cfg),
        kraken=bridge,
        dispatcher=dispatcher,
        contagion=contagion,
        equity_provider=lambda: 1_000.0,
    )


def test_contagion_derisks_and_vetoes_real_pipeline(tmp_path):
    normal = EpidemicContagionEngine()
    normal.evaluate(ContagionInputs())
    normal_result = _pipeline(tmp_path, normal).handle_signal(
        _signal(), idempotency_key="normal-signal-0001"
    )

    derisk = EpidemicContagionEngine()
    derisk.evaluate(ContagionInputs(
        oil_vol_zscore=2.5,
        cross_asset_correlation=0.6,
        orderbook_absorption=0.6,
    ))
    derisk_result = _pipeline(tmp_path, derisk).handle_signal(
        _signal(), idempotency_key="derisk-signal-0001"
    )
    assert derisk_result.accepted
    assert derisk_result.quantity == pytest.approx(normal_result.quantity * 0.5)

    crisis = EpidemicContagionEngine()
    crisis.evaluate(ContagionInputs(
        oil_vol_zscore=4.0,
        gold_dxy_ratio_change=0.05,
        cross_asset_correlation=0.95,
        orderbook_absorption=0.2,
    ))
    veto = _pipeline(tmp_path, crisis).handle_signal(_signal())
    assert veto.accepted is False
    assert veto.code == bp.CONTAGION_VETO_CODE


def test_pipeline_fails_closed_without_fresh_contagion(tmp_path):
    outcome = _pipeline(tmp_path, EpidemicContagionEngine()).handle_signal(_signal())
    assert outcome.accepted is False
    assert outcome.code == "CONTAGION_DATA_STALE"
    assert outcome.status_code == 503


def test_telemetry_center_can_enable_live_bridge():
    from app.core.config import load_config

    cfg = load_config()
    cfg.live_trading = True
    telemetry = TelemetryCenter()
    telemetry.set_state("LIVE_APPROVED")
    assert KrakenCliBridge(cfg, telemetry=telemetry).live_enabled is True


def test_dispatcher_routes_futures_without_falling_back_to_spot(tmp_path):
    class Result:
        ok = True
        mode = "sim"
        txid = "FUTURES-1"
        error_code = ""
        stderr = ""
        stdout = ""

    class Bridge:
        def __init__(self):
            self.calls = []

        def add_order(self, **kwargs):
            self.calls.append(kwargs)
            return Result()

    spot, futures = Bridge(), Bridge()
    dispatcher = ReliableOrderDispatcher(
        spot,
        futures_bridge=futures,
        receipts_log=str(tmp_path / "receipts.jsonl"),
    )
    receipt = dispatcher.dispatch(OrderRequest(
        idempotency_key="futures-route-0001",
        strategy_id="s1",
        pair="PF_XBTUSD",
        side="buy",
        volume=1.0,
        market_type="futures",
    ))
    assert receipt.success
    assert spot.calls == []
    assert len(futures.calls) == 1


def test_memory_stage_four_pauses_and_restarts_worker():
    calls: list[str] = []

    class Telemetry:
        def set_state(self, state, reason=""):
            calls.append(state)

    class Safety:
        def engage_pause(self, reason):
            calls.append(reason)

    watchdog = MemoryWatchdog(
        telemetry=Telemetry(),
        safety_guard=Safety(),
        worker_restart=lambda: calls.append("restart") or "worker restarted",
    )
    outcome = watchdog.check(percent=99.0)
    assert outcome["stage"] == 4 and outcome["executed"] is True
    assert calls == ["EMERGENCY_HALT", "memory_watchdog_stage_4", "restart"]
    repeated = watchdog.check(percent=99.0)
    assert repeated["executed"] is False
    assert repeated["reason"] == "stage4_latched"
