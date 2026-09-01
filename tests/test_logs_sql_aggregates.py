"""SQL aggregates for GET /api/logs must match the Python scan contract."""
from __future__ import annotations

from app.core.duckdb_store import DuckDBStore


def _trade(tid, sid, mode, pnl, status="closed", notional=100.0):
    return {
        "trade_id": tid, "strategy_id": sid, "status": status,
        "execution_mode": mode, "symbol": "BTC/USD",
        "direction": "LONG", "side": "buy",
        "net_pnl_usd": pnl, "notional_usd": notional,
    }


def test_closed_trade_stats_match_python_paper_or_empty(tmp_path):
    store = DuckDBStore(str(tmp_path / "logs.duckdb"))
    store.upsert_trade(_trade("a", "s1", "paper", 10.0))
    store.upsert_trade(_trade("b", "s1", "", 3.0))
    store.upsert_trade(_trade("c", "s1", "live", 99.0))
    store.upsert_trade(_trade("d", "s1", "paper", 5.0, status="open"))
    stats = store.closed_trade_stats("paper")
    assert stats["count"] == 2
    assert stats["pnl"] == 13.0
    assert store.sum_closed_pnl("paper") == 13.0


def test_strategy_closed_aggregates_match_python_scan(tmp_path):
    store = DuckDBStore(str(tmp_path / "agg.duckdb"))
    store.upsert_trade(_trade("a", "s1", "paper", 10.0, notional=200.0))
    store.upsert_trade(_trade("b", "s1", "paper", -4.0, notional=50.0))
    store.upsert_trade(_trade("c", "s1", "paper", 0.0, notional=10.0))
    store.upsert_trade(_trade("d", "s2", "live", 7.0, notional=30.0))
    store.upsert_trade(_trade("e", "s1", "paper", 1.0, status="open", notional=9.0))

    rows = {r["strategy_id"]: r for r in store.strategy_closed_aggregates()}
    s1, s2 = rows["s1"], rows["s2"]
    assert s1["trades"] == 3
    assert s1["wins"] == 1
    assert s1["realized"] == 6.0
    assert s1["volume"] == 260.0
    assert s2["trades"] == 1
    assert s2["wins"] == 1
    assert s2["realized"] == 7.0

    scanned = store.trades(status="closed", limit=10000)
    by = {}
    for t in scanned:
        sid = str(t.get("strategy_id") or "")
        rec = by.setdefault(sid, {"realized": 0.0, "trades": 0, "wins": 0, "volume": 0.0})
        pnl = float(t.get("net_pnl_usd") or 0.0)
        rec["realized"] += pnl
        rec["trades"] += 1
        rec["wins"] += 1 if pnl > 0 else 0
        rec["volume"] += float(t.get("notional_usd") or 0.0)
    for sid, rec in by.items():
        assert rows[sid]["realized"] == rec["realized"]
        assert rows[sid]["trades"] == rec["trades"]
        assert rows[sid]["wins"] == rec["wins"]
        assert rows[sid]["volume"] == rec["volume"]
