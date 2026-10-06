"""Paper-validation lake: signal/fill rows, parquet layout, flush on cohort end."""
from __future__ import annotations

import os

import pyarrow.parquet as pq

from app.execution.paper_validation_lake import PaperValidationLake
from app.optimizer.academy_progress import persist_paper_validation_runs


def test_record_signal_and_fill_then_flush_parquet(tmp_path):
    lake = PaperValidationLake(base_dir=str(tmp_path / "pv"))
    lake.record_signal(
        strategy_id="alpha", cohort_id="pv_1", symbol="BTC/USD",
        action="BUY", outcome="FILLED", reason="ok", code="FILLED",
        price=50000.0, idempotency_key="k1",
    )
    lake.record_signal(
        strategy_id="alpha", cohort_id="pv_1", symbol="BTC/USD",
        action="BUY", outcome="REJECTED", reason="throttle", code="VETO",
        price=50100.0, idempotency_key="k2",
    )
    lake.record_fill(
        strategy_id="alpha", cohort_id="pv_1", symbol="BTC/USD",
        side="buy", ack="FILLED", volume=0.01, price=50000.0,
        order_id="oid1", pnl_pct=0.4, idempotency_key="k1",
    )
    counts = lake.counts("alpha", "pv_1")
    assert counts["signals"] == 2
    assert counts["fills"] == 1
    assert counts["rejected"] == 1
    stats = lake.summary("alpha", "pv_1")
    assert stats["net_pnl_pct"] == 0.4
    written = lake.flush("pv_1")
    assert len(written) == 2
    sig_path = lake.path_for("alpha", "pv_1", "signals")
    fill_path = lake.path_for("alpha", "pv_1", "fills")
    assert os.path.isfile(sig_path)
    assert os.path.isfile(fill_path)
    assert sig_path.endswith("signals.parquet")
    assert "/alpha/pv_1/" in sig_path.replace("\\", "/")
    sig_table = pq.read_table(sig_path)
    assert sig_table.num_rows == 2
    assert "outcome" in sig_table.column_names
    fill_table = pq.read_table(fill_path)
    assert fill_table.num_rows == 1
    assert float(fill_table.column("pnl_pct")[0].as_py()) == 0.4


def test_unattributed_bucket_and_academy_json(tmp_path, monkeypatch):
    lake = PaperValidationLake(base_dir=str(tmp_path / "pv"))
    row = lake.record_signal(strategy_id="", cohort_id="", outcome="FILLED")
    assert row["strategy_id"] == "unattributed"
    assert row["cohort_id"] == "unattributed"
    path = persist_paper_validation_runs(
        [{"cohort_id": "pv_1", "strategy_id": "alpha", "signals": 2, "fills": 1}],
        data_dir=str(tmp_path),
    )
    assert os.path.isfile(path)
    assert path.endswith("paper_validation_runs.json")
    again = persist_paper_validation_runs(
        [{"cohort_id": "pv_2", "strategy_id": "beta", "signals": 0, "fills": 0}],
        data_dir=str(tmp_path),
    )
    text = open(again, encoding="utf-8").read()
    assert "alpha" in text and "beta" in text


def test_duckdb_paper_validation_crud(tmp_path):
    from app.core.duckdb_store import DuckDBStore

    store = DuckDBStore(str(tmp_path / "pv.duckdb"))
    store.upsert_paper_validation_cohort({
        "cohort_id": "pv_1",
        "started_at": 10.0,
        "ends_at": 100.0,
        "status": "ACTIVE",
        "strategy_ids": ["alpha", "beta"],
    })
    active = store.get_active_paper_validation_cohort()
    assert active["cohort_id"] == "pv_1"
    assert active["strategy_ids"] == ["alpha", "beta"]
    store.upsert_paper_validation_run({
        "cohort_id": "pv_1",
        "strategy_id": "alpha",
        "symbol": "BTC/USD",
        "interval_min": 15,
        "started_at": 10.0,
        "signals": 0,
        "status": "active",
    })
    store.upsert_paper_validation_run({
        "cohort_id": "pv_1",
        "strategy_id": "alpha",
        "ended_at": 100.0,
        "signals": 3,
        "fills": 1,
        "rejected": 1,
        "net_pnl_pct": 0.2,
        "profit_factor": 1.5,
        "win_rate": 1.0,
        "status": "closed",
    })
    runs = store.list_paper_validation_runs()
    assert runs[0]["strategy_id"] == "alpha"
    assert int(runs[0]["signals"]) == 3
    assert runs[0]["started_at"] == 10.0
    store.upsert_paper_validation_cohort({
        "cohort_id": "pv_1",
        "started_at": 10.0,
        "ends_at": 100.0,
        "ended_at": 100.0,
        "status": "closed",
        "strategy_ids": ["alpha", "beta"],
    })
    assert store.get_active_paper_validation_cohort() is None
    store.close()
