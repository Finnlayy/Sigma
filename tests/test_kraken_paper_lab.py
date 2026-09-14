"""§32 — Kraken Paper Trading Lab & Graduation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import blueprint as bp
from app.execution.KrakenCliBridge import KrakenCliBridge
from app.execution.kraken_paper_engine import (KrakenPaperEngine, PaperOnlyViolation,
                                               PaperTrade, get_paper_engine,
                                               set_paper_engine)
from app.server.main import app


# --------------------------------------------------------------- blueprint --
def test_blueprint_paper_constants():
    assert bp.KRAKEN_PAPER_ENABLED is True
    assert bp.PAPER_GRADUATION_MIN_TRADES == 20
    assert bp.PAPER_GRADUATION_MIN_PROFIT_FACTOR == 1.6
    assert bp.PAPER_GRADUATION_MIN_WIN_RATE_PCT == 55.0
    assert bp.KRAKEN_PAPER_INITIAL_BALANCE_USD == 10_000.0


def test_section_32_not_pending():
    assert not any(s.startswith("32 ") for s in bp.DOCS_PENDING_SECTIONS)


def test_paper_panel_registered():
    assert "PaperLabPanel" in bp.ALL_TERMINAL_PANELS
    assert "PAPER_LAB" in bp.ALL_TERMINAL_PRESETS


# ------------------------------------------------------------ dual-mode CLI --
def _bridge(mode=bp.ExecutionMode.KRAKEN_PAPER.value, **kw):
    return KrakenCliBridge(execution_mode=mode, **kw)


def test_bridge_rejects_unknown_mode():
    with pytest.raises(ValueError):
        KrakenCliBridge(execution_mode="casino")


def test_paper_prefix_spot_and_futures():
    assert _bridge()._prefix() == [bp.KRAKEN_CLI_BINARY, "paper"]
    assert _bridge(futures=True)._prefix() == [bp.KRAKEN_CLI_BINARY, "futures", "paper"]
    assert KrakenCliBridge()._prefix() == [bp.KRAKEN_CLI_BINARY, "trade"]


def test_paper_order_argv_and_simulated_fill(monkeypatch):
    bridge = _bridge(futures=True)
    monkeypatch.setattr(bridge, "_cli_available", lambda: False)
    res = bridge.add_order(
        pair="PF_XBTUSD", side="buy", volume=1.0, ordertype="limit",
        price=68000.0, stop_price=67000.0, strategy_id="s1")
    assert res.ok and res.mode == "paper"
    assert res.txid.startswith("PAPER-")
    assert res.argv[:4] == [bp.KRAKEN_CLI_BINARY, "futures", "paper", "buy"]
    assert "--price=68000.0" in res.argv and "--stop-price=67000.0" in res.argv
    assert res.has_native_stop_loss is True


def test_paper_order_uses_runner_when_cli_present(monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return ("txid = OQ1234-PAPER", "", 0)

    b = _bridge(runner=runner)
    monkeypatch.setattr(b, "_cli_available", lambda: True)
    res = b.add_order(pair="XBTUSD", side="sell", volume=0.5, strategy_id="s2")
    assert res.ok and res.mode == "paper" and calls
    assert calls[0][:2] == [bp.KRAKEN_CLI_BINARY, "paper"]


def test_paper_order_error_is_flagged(monkeypatch):
    b = _bridge(runner=lambda argv, t: ("", "EGeneral:Invalid arguments", 1))
    monkeypatch.setattr(b, "_cli_available", lambda: True)
    res = b.add_order(pair="XBTUSD", side="buy", volume=1, strategy_id="s3")
    assert res.ok is False and res.error_code


def test_paper_balance_simulated(monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "_cli_available", lambda: False)
    res = bridge.balance()
    assert res.ok and "10000" in res.stdout


def test_live_mode_argv_unchanged():
    argv = KrakenCliBridge().add_order(pair="XBTUSD", side="buy", volume=0.01,
                                       leverage=5, strategy_id="s").argv
    assert argv[:3] == [bp.KRAKEN_CLI_BINARY, "trade", "add-order"]


# ------------------------------------------------------------------ engine --
def _engine(**kw):
    return KrakenPaperEngine(bridge=_bridge(futures=True), auto_graduate=False, **kw)


def _fill(engine, sid="s1", pnl=10.0, n=1):
    for _ in range(n):
        engine.record_fill(PaperTrade(strategy_id=sid, symbol="PF_XBTUSD", side="buy",
                                      quantity=1.0, entry_price=100.0, exit_price=110.0,
                                      pnl_eur=pnl))


def test_engine_refuses_live_bridge():
    eng = KrakenPaperEngine(bridge=KrakenCliBridge())
    with pytest.raises(PaperOnlyViolation):
        eng.submit_order("s1", "XBTUSD", "buy", 1.0)


def test_engine_stats_accumulate():
    eng = _engine()
    _fill(eng, pnl=10.0, n=3)
    _fill(eng, pnl=-4.0, n=1)
    st = eng.stats("s1")
    assert st["trades"] == 4 and st["wins"] == 3 and st["losses"] == 1
    assert st["win_rate_pct"] == 75.0
    assert st["profit_factor"] == 7.5
    assert st["net_pnl_eur"] == 26.0


def test_graduation_blocked_below_min_trades():
    eng = _engine()
    _fill(eng, pnl=10.0, n=5)
    status = eng.graduation_status("s1")
    assert status["eligible"] is False
    assert status["failed_gates"] == ["min_paper_trades"]
    assert eng.graduate("s1")["promoted"] is False


def test_graduation_blocked_by_win_rate_and_pf():
    eng = _engine()
    _fill(eng, pnl=5.0, n=8)
    _fill(eng, pnl=-5.0, n=12)
    status = eng.graduation_status("s1")
    assert status["gates"]["min_paper_trades"]["passed"] is True
    assert "min_paper_win_rate_pct" in status["failed_gates"]
    assert "min_paper_profit_factor" in status["failed_gates"]


def test_graduation_passes_all_gates():
    eng = _engine()
    _fill(eng, pnl=10.0, n=12)
    _fill(eng, pnl=-5.0, n=8)
    status = eng.graduation_status("s1")
    assert status["eligible"] is True and status["stage"] == 2
    out = eng.graduate("s1")
    assert out["promoted"] and out["execution_mode"] == bp.ExecutionMode.LIVE.value
    assert eng.execution_mode_for("s1") == "live"
    assert eng.graduation_status("s1")["stage"] == 3


def test_force_graduation_and_demote():
    eng = _engine()
    _fill(eng, pnl=1.0, n=2)
    assert eng.graduate("s1", force=True)["promoted"] is True
    eng.demote("s1")
    assert eng.execution_mode_for("s1") == bp.ExecutionMode.KRAKEN_PAPER.value


def test_auto_graduation():
    eng = KrakenPaperEngine(bridge=_bridge(), auto_graduate=True)
    _fill(eng, pnl=10.0, n=12)
    out = None
    for _ in range(8):
        out = eng.record_fill(PaperTrade(strategy_id="s1", symbol="PF_XBTUSD",
                                         side="buy", quantity=1, entry_price=1,
                                         exit_price=1, pnl_eur=-5.0))
    assert out["graduation"]["graduated"] is True


def test_academy_hook_is_non_fatal():
    class Boom:
        def record_trade(self, *a, **k):
            raise RuntimeError("nope")

    eng = KrakenPaperEngine(bridge=_bridge(), academy=Boom(), auto_graduate=False)
    _fill(eng)
    assert eng.stats("s1")["trades"] == 1


def test_submit_order_returns_paper_receipt(monkeypatch):
    eng = _engine()
    monkeypatch.setattr(eng.bridge, "_cli_available", lambda: False)
    out = eng.submit_order("s1", "PF_XBTUSD", "buy", 1.0)
    assert out["ok"] and out["mode"] == "paper" and out["order_id"].startswith("PAPER-")


def test_panel_state_shape():
    eng = _engine()
    _fill(eng, n=2)
    state = eng.panel_state()
    assert state["graduation"]["min_paper_trades"] == 20
    assert state["strategies"][0]["strategy_id"] == "s1"
    assert len(state["trades"]) == 2


# --------------------------------------------------------------------- API --
@pytest.fixture()
def client():
    set_paper_engine(KrakenPaperEngine(bridge=_bridge(futures=True),
                                       auto_graduate=False))
    yield TestClient(app)
    set_paper_engine(None)


def test_api_paper_lab_state(client):
    body = client.get("/api/v1/paper-lab").json()
    assert body["enabled"] is True and body["strategies"] == []


def test_api_order_fill_and_promote_flow(client):
    r = client.post("/api/v1/paper-lab/order", json={
        "strategy_id": "sX", "symbol": "PF_XBTUSD", "side": "buy", "volume": 1})
    assert r.status_code == 200 and r.json()["mode"] == "paper"

    for pnl in [10.0] * 12 + [-5.0] * 8:
        client.post("/api/v1/paper-lab/fill",
                    json={"strategy_id": "sX", "symbol": "PF_XBTUSD", "pnl_eur": pnl})

    detail = client.get("/api/v1/paper-lab/sX").json()
    assert detail["stats"]["trades"] == 20
    assert detail["graduation"]["eligible"] is True

    promoted = client.post("/api/v1/paper-lab/sX/promote", json={}).json()
    assert promoted["promoted"] is True
    assert client.get("/api/v1/paper-lab/sX").json()["execution_mode"] == "live"


def test_api_promote_conflict_when_gates_fail(client):
    client.post("/api/v1/paper-lab/fill",
                json={"strategy_id": "sY", "symbol": "PF_XBTUSD", "pnl_eur": 1.0})
    r = client.post("/api/v1/paper-lab/sY/promote", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "GRADUATION_GATES_FAILED"


def test_api_demote(client):
    client.post("/api/v1/paper-lab/sZ/promote", json={"force": True})
    assert client.post("/api/v1/paper-lab/sZ/demote", json={}).json()["demoted"] is True
    assert client.get("/api/v1/paper-lab/sZ").json()["execution_mode"] == "kraken_paper"


def test_singleton_get_paper_engine():
    set_paper_engine(None)
    assert get_paper_engine() is get_paper_engine()
    set_paper_engine(None)
