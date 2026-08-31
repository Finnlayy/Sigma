"""O(T) grouping helpers for GET /api/queue-matrices must match nested-filter math."""
from __future__ import annotations


def _trade(tid, sid, mode, pnl, notional, exit_time, status="closed",
           symbol="BTC/USD", name=None):
    return {
        "trade_id": tid, "strategy_id": sid,
        "strategy_name": name or sid, "status": status,
        "execution_mode": mode, "symbol": symbol,
        "direction": "LONG", "side": "buy",
        "net_pnl_usd": pnl, "notional_usd": notional,
        "entry_time": exit_time, "exit_time": exit_time,
        "entry_price": 100.0, "quantity": 1.0,
    }


def test_queue_mode_empty_string_is_paper():
    from app.server.main import _queue_mode

    assert _queue_mode({"execution_mode": None}) == "paper"
    assert _queue_mode({"execution_mode": ""}) == "paper"
    assert _queue_mode({"execution_mode": "live"}) == "live"
    assert _queue_mode({}) == "paper"


def test_bucket_closed_by_queue_is_o_t_and_skips_unknown_mode():
    from app.server.main import _bucket_closed_by_queue

    closed = [
        _trade("a", "s1", "paper", 10.0, 1.0, "2026-08-01 10:00:00"),
        _trade("b", "s1", "", 5.0, 1.0, "2026-08-01 11:00:00"),
        _trade("c", "s2", "live", 7.0, 1.0, "2026-08-01 12:00:00"),
        _trade("d", "s1", "other", 1.0, 1.0, "2026-08-01 13:00:00"),
    ]
    q_trades, by_sid = _bucket_closed_by_queue(closed)
    assert [t["trade_id"] for t in q_trades["paper"]] == ["a", "b"]
    assert [t["trade_id"] for t in q_trades["live"]] == ["c"]
    assert "d" not in {t["trade_id"] for t in q_trades["paper"] + q_trades["live"]}
    assert [t["trade_id"] for t in by_sid["paper"]["s1"]] == ["a", "b"]
    assert [t["trade_id"] for t in by_sid["live"]["s2"]] == ["c"]


def test_pnl_stats_zeros_count_as_losses():
    from app.server.main import _max_drawdown_frac, _pnl_stats

    rows = [
        {"net_pnl_usd": 10.0, "notional_usd": 200.0},
        {"net_pnl_usd": 5.0, "notional_usd": 50.0},
        {"net_pnl_usd": -4.0, "notional_usd": 80.0},
        {"net_pnl_usd": 0.0, "notional_usd": 30.0},
    ]
    stt = _pnl_stats(rows)
    assert stt["n"] == 4
    assert stt["wins"] == 2
    assert stt["losses"] == 2
    assert stt["realized"] == 11.0
    assert stt["volume"] == 360.0
    assert stt["gw"] == 15.0
    assert stt["gl"] == 4.0
    assert stt["pf"] == 3.75
    assert stt["best"] == 10.0
    assert stt["worst"] == -4.0
    assert stt["avg"] == 2.75
    assert stt["win_rate"] == 50.0
    assert _pnl_stats([])["best"] == 0.0
    assert _pnl_stats([])["pf"] == 0.0
    equity = [0.0, 0.0, 10.0, 15.0, 11.0]
    assert round(_max_drawdown_frac(equity) * 100.0, 4) == 26.6667
