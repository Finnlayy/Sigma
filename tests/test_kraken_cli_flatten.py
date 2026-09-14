"""Flatten orchestration tests — CLI recipe only, verify/retry."""
from __future__ import annotations

import json

import pytest

from app.core import blueprint as bp
from app.execution.KrakenCliBridge import KrakenCliBridge
from app.execution.kraken_cli_flatten import flatten_futures_book, flatten_all


def test_flatten_futures_empty_ok(monkeypatch):
    calls = []

    def runner(argv, timeout):
        calls.append(list(argv))
        if "positions" in argv:
            return json.dumps({"positions": []}), "", 0
        return "{}", "", 0

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=runner,
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = flatten_futures_book(bridge, reason="unit")
    assert res.ok
    assert any("cancel-all" in a for a in calls)


def test_flatten_futures_closes_then_empty(monkeypatch):
    state = {"round": 0}

    def runner(argv, timeout):
        if "cancel-all" in argv:
            return "{}", "", 0
        if "positions" in argv:
            state["round"] += 1
            if state["round"] <= 1:
                return json.dumps({
                    "positions": [
                        {"symbol": "PF_XBTUSD", "side": "long", "size": 1.0},
                    ],
                }), "", 0
            return json.dumps({"positions": []}), "", 0
        # market close
        return "txid=FLAT-1", "", 0

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=runner,
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    # capital check for futures paper add_order
    monkeypatch.setattr(
        "app.execution.kraken_paper_sot.fetch_paper_capital",
        lambda *a, **k: type("S", (), {"ok": True, "available": True, "balances": {"USD": 10000}})(),
    )
    res = flatten_futures_book(bridge, reason="unit")
    assert res.ok


def test_flatten_futures_residual_fails(monkeypatch):
    def runner(argv, timeout):
        if "cancel-all" in argv:
            return "{}", "", 0
        if "positions" in argv:
            return json.dumps({
                "positions": [{"symbol": "PF_ETHUSD", "side": "short", "size": 2.0}],
            }), "", 0
        return "EOrder:Insufficient funds", "", 1

    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=runner,
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    monkeypatch.setattr(
        "app.execution.kraken_paper_sot.fetch_paper_capital",
        lambda *a, **k: type("S", (), {"ok": True, "available": True, "balances": {"USD": 10000}})(),
    )
    res = flatten_futures_book(bridge, reason="unit")
    assert res.ok is False
    assert res.error_code == "FLATTEN_RESIDUAL"


def test_close_all_market_delegates_to_flatten(monkeypatch):
    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        futures=True,
        runner=lambda a, t: (json.dumps({"positions": []}), "", 0),
    )
    monkeypatch.setattr(bridge, "_cli_available", lambda: True)
    res = bridge.close_all_market(reason="deadman")
    assert res.error_code != "CLI_UNSUPPORTED"
