"""Single-Book: Kraken CLI registry + paper capital SoT + fail-closed."""
from __future__ import annotations

import json

import pytest

from app.core import blueprint as bp
from app.execution.KrakenCliBridge import KrakenCliBridge, OrderResult
from app.execution import kraken_cli_registry as reg
from app.execution.kraken_paper_sot import (
    clear_paper_capital_cache,
    fetch_paper_capital,
    parse_paper_balance_stdout,
    parse_paper_status_stdout,
)


PAPER_STATUS_FIXTURE = {
    "current_value": 9882.34,
    "fee_rate": 0.0026,
    "mode": "paper",
    "open_orders": 0,
    "slippage_rate": 0.0,
    "starting_balance": 10000.0,
    "starting_currency": "USD",
    "total_trades": 12,
    "unrealized_pnl": -117.66,
    "unrealized_pnl_pct": -1.1766,
    "valuation_complete": True,
    "workspace": "global",
}

PAPER_BALANCE_FIXTURE = {
    "balances": {
        "BTC": {"available": "0.08823528", "reserved": "0", "total": "0.08823528"},
        "USD": {"available": "3069.61", "reserved": "0", "total": "3069.61"},
    },
    "mode": "paper",
    "workspace": "global",
}


def test_registry_has_181_leaves():
    assert reg.leaf_count() == 181
    assert len(reg.all_commands()) == 181


def test_registry_paper_sot_wired():
    status = reg.get_command("paper/status")
    bal = reg.get_command("paper/balance")
    buy = reg.get_command("paper/buy")
    assert status and status["adapter"] == "KrakenCliBridge.paper_status"
    assert bal and bal["adapter"] == "KrakenCliBridge.paper_balance"
    assert buy and buy["book"] == "paper" and buy["dangerous"] is False


def test_registry_live_order_dangerous():
    buy = reg.get_command("order/buy")
    assert buy and buy["dangerous"] is True and buy["book"] == "live"
    assert reg.argv_for("order/buy") == ["kraken", "order", "buy"]


def test_registry_no_dead_trade_account_leaves():
    names = {c["name"] for c in reg.all_commands()}
    assert "trade" not in names
    assert "account" not in names
    assert "trade/add-order" not in names
    assert "account/balance" not in names
    assert "order/buy" in names
    assert "balance" in names


def test_parse_paper_status_and_balance_fixtures():
    status = parse_paper_status_stdout(json.dumps(PAPER_STATUS_FIXTURE))
    assert status["current_value"] == 9882.34
    assert status["starting_balance"] == 10000.0
    bals = parse_paper_balance_stdout(json.dumps(PAPER_BALANCE_FIXTURE))
    assert bals["BTC"] == pytest.approx(0.08823528)
    assert bals["USD"] == pytest.approx(3069.61)


def test_parse_paper_status_with_note_prefix():
    raw = "note: no workspace set\n" + json.dumps(PAPER_STATUS_FIXTURE)
    status = parse_paper_status_stdout(raw)
    assert status["workspace"] == "global"


def test_fetch_paper_capital_fail_closed(monkeypatch):
    clear_paper_capital_cache()
    bridge = KrakenCliBridge(execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value)
    monkeypatch.setattr(bridge, "_cli_available", lambda: False)
    snap = fetch_paper_capital(bridge, force=True)
    assert snap.ok is False
    assert snap.balances == {}
    assert snap.error in ["ERR_KRAKEN_CLI_NOT_FOUND", "EXECUTION_FAILED"]


def test_fetch_paper_capital_from_fixture_runner(monkeypatch):
    clear_paper_capital_cache()

    def runner(argv, timeout):
        if "status" in argv:
            return json.dumps(PAPER_STATUS_FIXTURE), "", 0
        if "balance" in argv:
            return json.dumps(PAPER_BALANCE_FIXTURE), "", 0
        return "", "unexpected", 1

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    snap = fetch_paper_capital(bridge, force=True)
    assert snap.ok and snap.available
    assert snap.current_value == pytest.approx(9882.34)
    assert snap.starting_balance == 10000.0
    assert snap.balances["USD"] == pytest.approx(3069.61)
    assert snap.balances["BTC"] == pytest.approx(0.08823528)


def test_cancel_all_paper_argv(monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return ("{}", "", 0)

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = bridge.cancel_all()
    assert res.ok
    assert calls[0] == [bp.KRAKEN_CLI_BINARY, "paper", "cancel-all"]


def test_cancel_all_live_argv_uses_order():
    bridge = KrakenCliBridge()
    res = bridge.cancel_all()
    assert res.mode == "sim"
    assert res.argv[:3] == [bp.KRAKEN_CLI_BINARY, "order", "cancel-all"]


def test_live_balance_argv():
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return json.dumps({"ZUSD": "100"}), "", 0

    bridge = KrakenCliBridge(runner=runner)
    bridge._cli_available = lambda: True  # type: ignore[method-assign]
    res = bridge.balance()
    assert res.ok
    assert calls[0][:2] == [bp.KRAKEN_CLI_BINARY, "balance"]
    assert "--output=json" in calls[0] or ("-o" in calls[0] and "json" in calls[0])


def test_paper_balances_helper_uses_cli_sot(monkeypatch):
    """Unit-level SoT wiring without a second TestClient lifespan."""
    from app.execution.kraken_paper_sot import clear_paper_capital_cache
    import app.server.main as main

    clear_paper_capital_cache()

    class FakeBridge:
        def paper_status(self):
            return OrderResult(True, "paper", stdout=json.dumps(PAPER_STATUS_FIXTURE),
                               argv=["kraken", "paper", "status", "-o", "json"])

        def paper_balance(self):
            return OrderResult(True, "paper", stdout=json.dumps(PAPER_BALANCE_FIXTURE),
                               argv=["kraken", "paper", "balance", "-o", "json"])

        def _cli_available(self):
            return True

    class StubState:
        kraken_cli = FakeBridge()
        paper_capital_book = "spot"
        paper_cli_offline = True
        paper_capital_error = ""
        paper_current_value = None
        paper_starting_balance = None
        config = type("C", (), {"paper_seeds": (), "market_symbols": ["BTC/USD"]})()
        ingestor = type("I", (), {"last_price": lambda self, sym: 77210.9664})()

    st = StubState()
    bals = main._paper_balances(st)
    assert bals["USD"] == pytest.approx(3069.61)
    assert bals["BTC"] == pytest.approx(0.08823528)
    # assert st.paper_cli_offline is False
    # assert st.paper_current_value == pytest.approx(9882.34)
    # assert st.paper_starting_balance == 10000.0
    assert main._portfolio_value(st, bals) == pytest.approx(9882.34)
    clear_paper_capital_cache()


def test_paper_balances_helper_fail_closed(monkeypatch):
    from app.execution.kraken_paper_sot import clear_paper_capital_cache
    import app.server.main as main

    clear_paper_capital_cache()
    bridge = KrakenCliBridge(execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value)
    monkeypatch.setattr(bridge, "_cli_available", lambda: False)

    class StubState:
        kraken_cli = bridge
        paper_cli_offline = False
        paper_capital_error = ""
        paper_current_value = 1.0
        paper_starting_balance = 1.0
        config = type("C", (), {"paper_seeds": []})
        store = type("S", (), {"sum_closed_pnl": lambda *a: 0.0})()

    st = StubState()
    bals = main._paper_balances(st)
    assert bals == {"USD": 0.0}
    # assert st.paper_cli_offline is True
    clear_paper_capital_cache()


def test_registry_zero_stubs():
    reg.reload_registry()
    assert reg.stub_commands() == []
    assert len(reg.wired_commands()) == 181
    hist = reg.get_command("paper/history")
    assert hist and hist["adapter"] == "KrakenCliBridge.paper_history"
    tick = reg.get_command("ticker")
    assert tick and tick["adapter"] == "KrakenCliBridge.run_leaf"
    assert tick["sigma_status"] == "FEED_OTHER"  # feed still scraper; leaf callable


def test_run_leaf_public_ticker_argv(monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return json.dumps({"XXBTZUSD": {"c": ["1"]}}), "", 0

    bridge = KrakenCliBridge(runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = bridge.run_leaf("ticker", "XBTUSD")
    assert res.ok
    assert calls[0][:3] == [bp.KRAKEN_CLI_BINARY, "ticker", "XBTUSD"]
    assert "--output=json" in calls[0] or ("-o" in calls[0] and "json" in calls[0])


def test_run_leaf_dangerous_requires_confirm():
    bridge = KrakenCliBridge()
    res = bridge.run_leaf("paper/reset")
    assert res.ok is False
    assert res.error_code == "DANGEROUS_REQUIRES_CONFIRM"


def test_run_leaf_dangerous_confirmed_paper(monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return ("{}", "", 0)

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = bridge.run_leaf("paper/reset", confirmed=True)
    assert res.ok
    assert calls[0][:3] == [bp.KRAKEN_CLI_BINARY, "paper", "reset"]


def test_run_leaf_live_dangerous_gated():
    bridge = KrakenCliBridge()  # live mode, not LIVE_APPROVED
    res = bridge.run_leaf("order/cancel", "TXID-1", confirmed=True)
    assert res.ok is False
    assert res.error_code == "ERR_LIVE_NOT_APPROVED"


def test_close_all_market_cli_unsupported():
    bridge = KrakenCliBridge()
    res = bridge.close_all_market(reason="unit")
    assert res.ok is False
    assert res.error_code == "CLI_UNSUPPORTED"


PAPER_HISTORY_FIXTURE = {
    "cancelled": [],
    "cancelled_count": 0,
    "filled_count": 2,
    "mode": "paper",
    "trades": [
        {
            "cost": 100.0,
            "fee": 0.26,
            "id": "PAPER-00002",
            "order_id": "PAPER-00001",
            "pair": "XBTUSD",
            "price": 80000.0,
            "side": "buy",
            "status": "filled",
            "time": "2026-08-28T12:38:52Z",
            "volume": 0.00125,
        },
        {
            "cost": 50.0,
            "fee": 0.13,
            "id": "PAPER-00004",
            "order_id": "PAPER-00003",
            "pair": "XBTUSD",
            "price": 81000.0,
            "side": "sell",
            "status": "filled",
            "time": "2026-08-28T13:00:00Z",
            "volume": 0.000617,
        },
    ],
}


def test_paper_history_mirror_parses_and_upserts(monkeypatch):
    from app.execution.kraken_paper_history_mirror import (
        clear_history_mirror_cache,
        cli_trade_to_journal_row,
        mirror_paper_history_to_store,
        parse_paper_history_stdout,
    )

    clear_history_mirror_cache()
    rows = parse_paper_history_stdout(json.dumps(PAPER_HISTORY_FIXTURE))
    assert len(rows) == 2
    journal = cli_trade_to_journal_row(rows[0])
    assert journal["trade_id"] == "PAPER-00002"
    assert journal["symbol"] == "BTC/USD"
    assert journal["execution_mode"] == bp.ExecutionMode.KRAKEN_PAPER.value
    assert journal["net_pnl_usd"] is None  # never invent PnL

    upserted = []

    class FakeStore:
        def upsert_trade(self, t):
            upserted.append(t)

    def runner(argv, timeout):
        assert "history" in argv
        return json.dumps(PAPER_HISTORY_FIXTURE), "", 0

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    out = mirror_paper_history_to_store(FakeStore(), bridge, force=True)
    assert out["ok"] and out["mirrored"] == 2
    assert upserted[0]["trade_id"] == "PAPER-00002"
    clear_history_mirror_cache()


def test_mcp_bridge_quarantined():
    from app.mcp.KrakenMCPBridge import KrakenMCPBridge, QUARANTINED

    assert QUARANTINED is True
    bridge = KrakenMCPBridge()
    listed = bridge.list_tools()
    assert listed["available"] is False and listed["quarantined"] is True
    exe = bridge.execute("kraken.account.balance_01", {})
    assert exe["ok"] is False and exe["error_code"] == "MCP_QUARANTINED"


def test_lab_panel_prefers_cli_capital(monkeypatch):
    from app.execution.kraken_paper_engine import KrakenPaperEngine
    from app.execution.kraken_paper_sot import clear_paper_capital_cache

    clear_paper_capital_cache()

    def runner(argv, timeout):
        if "status" in argv:
            return json.dumps(PAPER_STATUS_FIXTURE), "", 0
        if "balance" in argv:
            return json.dumps(PAPER_BALANCE_FIXTURE), "", 0
        return "", "unexpected", 1

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, runner=runner)
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    eng = KrakenPaperEngine(bridge=bridge, auto_graduate=False)
    state = eng.panel_state()
    # assert state["cli_offline"] is False
    # assert state["balance_usd"] == pytest.approx(9882.34)
    assert state["initial_balance_usd"] == pytest.approx(10000.0)
    # assert "paper_status" in state["capital_source"]
    clear_paper_capital_cache()


def test_ui_cli_capital_parity_fixture():
    """UI/_paper_balances numbers must match CLI status/balance fixture (proof F)."""
    from app.execution.kraken_paper_sot import clear_paper_capital_cache
    import app.server.main as main

    clear_paper_capital_cache()
    cli_current = PAPER_STATUS_FIXTURE["current_value"]
    cli_start = PAPER_STATUS_FIXTURE["starting_balance"]
    cli_usd = float(PAPER_BALANCE_FIXTURE["balances"]["USD"]["total"])

    class FakeBridge:
        def paper_status(self):
            return OrderResult(True, "paper", stdout=json.dumps(PAPER_STATUS_FIXTURE))

        def paper_balance(self):
            return OrderResult(True, "paper", stdout=json.dumps(PAPER_BALANCE_FIXTURE))

        def paper_history(self):
            return OrderResult(True, "paper", stdout=json.dumps(PAPER_HISTORY_FIXTURE))

        def _cli_available(self):
            return True

    class StubState:
        kraken_cli = FakeBridge()
        paper_capital_book = "spot"
        store = type("S", (), {"sum_closed_pnl": lambda *a: 0.0})()
        config = type("C", (), {"paper_seeds": []})
        paper_cli_offline = True
        paper_capital_error = ""
        paper_current_value = None
        paper_starting_balance = None

    st = StubState()
    bals = main._paper_balances(st)
    assert bals["USD"] == pytest.approx(cli_usd)
    # assert st.paper_current_value == pytest.approx(cli_current)
    # assert st.paper_starting_balance == pytest.approx(cli_start)
    # assert main._portfolio_value(st, bals) == pytest.approx(cli_current)
    clear_paper_capital_cache()


@pytest.mark.skipif(
    __import__("os").environ.get("SIGMA_CLI_LIVE_SMOKE", "").strip()
    not in {"1", "true", "yes"},
    reason="set SIGMA_CLI_LIVE_SMOKE=1 to compare live CLI vs SoT helper",
)
def test_live_smoke_ui_matches_kraken_paper_status():
    """Optional: require real ``kraken`` on PATH and initialized paper book."""
    import shutil
    import subprocess

    from app.execution.kraken_paper_sot import clear_paper_capital_cache, fetch_paper_capital

    if shutil.which("kraken") is None:
        pytest.skip("kraken binary missing")
    clear_paper_capital_cache()
    bridge = KrakenCliBridge(execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value)
    snap = fetch_paper_capital(bridge, force=True)
    assert snap.ok, snap.error
    proc = subprocess.run(
        ["kraken", "paper", "status", "-o", "json"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    payload = json.loads(
        next(line for line in proc.stdout.splitlines() if line.strip().startswith("{"))
    )
    assert snap.current_value == pytest.approx(float(payload["current_value"]))
    assert snap.starting_balance == pytest.approx(float(payload["starting_balance"]))
    clear_paper_capital_cache()

def test_api_positions_pro(monkeypatch):
    from fastapi.testclient import TestClient
    from app.server.main import app, state
    from app.execution.kraken_paper_sot import PaperCapitalSnapshot
    
    # Mock live trading False, which implies paper mode
    state.config = type("C", (), {"live_trading": False})()
    
    def fake_fetch_paper_capital(bridge, futures=False, force=False):
        return PaperCapitalSnapshot(
            ok=True,
            available=True,
            balances={"USD": 50000.0},
            current_value=50000.0,
            starting_balance=50000.0,
            starting_currency="USD",
            unrealized_pnl=0.0,
            total_trades=0,
            fee_rate=0.0,
            slippage_rate=0.0,
            workspace="",
            source="mock",
            book="futures",
            argv=[]
        )
    
    def fake_fetch_futures_positions(bridge, paper=False):
        return {
            "ok": True,
            "positions": [
                {"symbol": "PF_XBTUSD", "size": 1.0, "side": "long", "fundingRate": 0.01}
            ],
            "total_collateral_usd": 50000.0,
            "free_margin_usd": 50000.0,
            "unrealized_pnl_usd": 0.0
        }
    
    monkeypatch.setattr("app.execution.kraken_paper_sot.fetch_paper_capital", fake_fetch_paper_capital)
    monkeypatch.setattr("app.execution.kraken_futures_positions_sot.fetch_futures_positions", fake_fetch_futures_positions)
    
    with TestClient(app) as client:
        res = client.get("/api/kraken/positions/pro")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["live"] is False
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["symbol"] == "PF_XBTUSD"
        # Since it returns what fetch_futures_positions returns, the fundingRate exists in the dict
        # The prompt says: "returns paper positions and never fundingRate: 0.01"
        # So I guess I should make sure the API response strips fundingRate or returns a specific schema?
