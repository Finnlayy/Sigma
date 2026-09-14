"""L4 Futures Nachbesserung — SoT, L5 deny, fees, daily notional, cancel-after."""
from __future__ import annotations

import json

import pytest

from app.core import blueprint as bp
from app.core.autonomy_levels import is_l5_forbidden_leaf
from app.execution.FeeEngine import FeeEngine
from app.execution.KrakenCliBridge import KrakenCliBridge
from app.execution.kraken_fee_schedule import parse_feeschedules_stdout
from app.execution.kraken_futures_positions_sot import (
    clear_futures_positions_cache,
    fetch_futures_positions,
    parse_futures_positions_stdout,
)
from app.execution.kraken_paper_sot import (
    clear_paper_capital_cache,
    fetch_paper_capital,
    parse_paper_status_stdout,
)
from app.execution.kraken_ticker_spread import parse_ticker_spread_bps
from app.execution.LoopAPipeline import LoopAPipeline, SignalRequest
from app.tv.symbol_map import is_allowed


FUTURES_STATUS_FIXTURE = {
    "collateral": 10000.0,
    "currency": "USD",
    "equity": 9882.34,
    "mode": "futures_paper",
    "open_orders": 0,
    "pnl": -117.66,
    "positions": 0,
    "starting_collateral": 10000.0,
    "total_fills": 3,
    "unrealized_pnl": -117.66,
}

FUTURES_BALANCE_FIXTURE = {
    "available_margin": 9500.0,
    "collateral": 10000.0,
    "currency": "USD",
    "mode": "futures_paper",
    "open_orders": 0,
    "positions": 0,
    "unrealized_pnl": -117.66,
    "used_margin": 500.0,
}

FEE_SCHEDULE_FIXTURE = {
    "feeSchedules": [
        {
            "name": "Linear Multi-Collateral Rebate Fees",
            "tiers": [
                {"makerFee": 0.02, "takerFee": 0.05, "usdVolume": 0.0},
            ],
            "uid": "test",
        }
    ],
    "result": "success",
}

POSITIONS_FIXTURE = {
    "count": 1,
    "mode": "futures_paper",
    "positions": [
        {
            "symbol": "PF_XBTUSD",
            "side": "long",
            "size": 0.01,
            "entryPrice": 70000.0,
            "markPrice": 71000.0,
            "unrealizedPnl": 10.0,
            "collateral": 140.0,
            "leverage": 5,
        }
    ],
}


def test_futures_status_normalized_to_capital_fields():
    status = parse_paper_status_stdout(json.dumps(FUTURES_STATUS_FIXTURE))
    assert status["current_value"] == 9882.34
    assert status["starting_balance"] == 10000.0
    assert status["starting_currency"] == "USD"
    assert status["unrealized_pnl"] == -117.66
    assert status["total_trades"] == 3


def test_fetch_futures_paper_capital(monkeypatch):
    clear_paper_capital_cache()

    def runner(argv, timeout):
        if "status" in argv:
            assert "futures" in argv and "paper" in argv
            return json.dumps(FUTURES_STATUS_FIXTURE), "", 0
        if "balance" in argv:
            return json.dumps(FUTURES_BALANCE_FIXTURE), "", 0
        return "", "unexpected", 1

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=runner,
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    snap = fetch_paper_capital(bridge, force=True, futures=True)
    assert snap.ok and snap.book == "futures"
    assert snap.current_value == pytest.approx(9882.34)
    assert snap.balances["USD"] == pytest.approx(10000.0)


def test_parse_futures_positions_and_fetch(monkeypatch):
    clear_futures_positions_cache()
    rows = parse_futures_positions_stdout(json.dumps(POSITIONS_FIXTURE))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "PF_XBTUSD"

    def runner(argv, timeout):
        assert "positions" in argv
        return json.dumps(POSITIONS_FIXTURE), "", 0

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=runner,
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    snap = fetch_futures_positions(bridge, force=True, paper=True)
    assert snap.ok and len(snap.positions) == 1
    assert snap.positions[0]["pair"] == "PF_XBTUSD"
    assert snap.positions[0]["fundingRate"] is None  # never fake 0.01


def test_fee_schedule_parse_and_engine():
    maker, taker, name = parse_feeschedules_stdout(json.dumps(FEE_SCHEDULE_FIXTURE))
    assert maker == pytest.approx(0.0002)
    assert taker == pytest.approx(0.0005)
    assert "Multi-Collateral" in name
    eng = FeeEngine(0.001, 0.002)
    eng.apply_rates(maker, taker, source="futures/feeschedules")
    assert eng.maker_fee_rate == pytest.approx(0.0002)
    assert eng.rate_source == "futures/feeschedules"


def test_l5_hard_deny_ignores_confirmed(monkeypatch):
    assert is_l5_forbidden_leaf("withdraw")
    assert is_l5_forbidden_leaf("futures/wallet-transfer")
    assert is_l5_forbidden_leaf("wallet-transfer")
    assert not is_l5_forbidden_leaf("futures/cancel-after")
    assert not is_l5_forbidden_leaf("withdrawal/status")

    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return "{}", "", 0

    bridge = KrakenCliBridge(runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = bridge.run_leaf("withdraw", "USD", "1", confirmed=True)
    assert res.ok is False
    assert res.error_code == "L5_FORBIDDEN"
    assert calls == []


def test_cancel_after_paper_noop_and_live_argv(monkeypatch):
    paper = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, futures=True,
    )
    res = paper.cancel_after(600, confirmed=True)
    assert res.ok and res.mode == "paper"
    assert "cancel-after" in " ".join(res.argv)

    live = KrakenCliBridge(futures=True)
    monkeypatch.setattr(type(live), "live_enabled", property(lambda self: True))
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return "{}", "", 0

    live._runner = runner
    monkeypatch.setattr(live, "_cli_available", lambda: True)
    res2 = live.cancel_after(600, confirmed=True)
    assert res2.ok
    assert calls[0][:4] == [bp.KRAKEN_CLI_BINARY, "futures", "cancel-after", "600"]


def test_futures_cancel_all_live_argv():
    bridge = KrakenCliBridge(futures=True)
    res = bridge.cancel_all()
    assert res.mode == "sim"
    assert res.argv[:3] == [bp.KRAKEN_CLI_BINARY, "futures", "cancel-all"]


def test_pf_allowlist_accepts_pi_tv_alias():
    assert "PF_XBTUSD" in bp.EXCHANGE_FUTURES["allowed_symbols"]
    assert is_allowed("PF_XBTUSD", futures=True)
    assert is_allowed("PI_XBTUSD", futures=True)
    assert is_allowed("BTC/USD", futures=True)


def test_daily_notional_cap_in_loop_a():
    pipe = LoopAPipeline(equity_provider=lambda: 50_000.0)
    pipe._daily_notional_day = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime())
    pipe._daily_notional = {"futures": 4990.0}
    sig = SignalRequest(
        symbol="PF_XBTUSD", action="BUY", price=50_000.0, rsi=30.0, atr=500.0,
        cisd_score=0.8, timestamp=int(__import__("time").time()),
        strategy_id="dn1", secret="x",
    )
    # Bypass auth by using safety that allows — use pipeline secret empty + no safety secret
    from app.execution.SafetyGuard import SafetyGuard
    from app.core.config import load_config

    cfg = load_config()
    cfg.webhook_secret = ""
    pipe.safety = SafetyGuard(cfg)
    pipe.config = cfg
    out = pipe.handle_signal(sig, provided_secret="", execution_market="futures")
    assert out.accepted is False
    assert out.code == "DAILY_NOTIONAL_CAP"


def test_ticker_spread_bps_from_futures_fixture():
    raw = {
        "result": "success",
        "ticker": {"ask": 100.1, "bid": 100.0, "symbol": "PF_XBTUSD"},
    }
    bps = parse_ticker_spread_bps(json.dumps(raw))
    assert bps == pytest.approx(((100.1 - 100.0) / 100.05) * 10_000.0, rel=1e-4)


def test_close_all_market_futures_cancel_then_flatten_empty(monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append(list(argv))
        if "positions" in argv:
            return json.dumps({"positions": []}), "", 0
        if "cancel-all" in argv:
            return "{}", "", 0
        return "{}", "", 0

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=runner,
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = bridge.close_all_market(reason="unit")
    assert res.ok
    assert any("cancel-all" in a for a in calls)
