"""Strategy scorecard ampel: slots, initialize PF gate, idle batch, snapshot."""
from __future__ import annotations

from types import SimpleNamespace

from app.core import blueprint as bp
from app.core.duckdb_store import DuckDBStore
from app.optimizer.StrategyAllocator import StrategyAllocator
from app.optimizer.strategy_scorecard import (
    StrategyScorecard, aggregate_lamp, pf_from_job_result, set_strategy_scorecard,
)


class FakeQueue:
    def __init__(self):
        self.jobs = []

    def submit(self, kind, **kw):
        job = SimpleNamespace(
            job_id=f"j{len(self.jobs)+1}", kind=kind, status="queued",
            result={}, **{k: v for k, v in kw.items() if k not in ("kind",)},
        )
        job.to_dict = lambda j=job: {
            "job_id": j.job_id, "kind": j.kind, "status": j.status,
            "strategy_id": getattr(j, "strategy_id", ""),
        }
        self.jobs.append(job)
        return job

    def get(self, job_id):
        return next((j for j in self.jobs if j.job_id == job_id), None)


def _store(tmp_path):
    store = DuckDBStore(str(tmp_path / "score.duckdb"))
    store.upsert_strategy({
        "id": "s1", "name": "CISD", "code": "// pine secret",
        "assetPair": "BTC/USD", "interval": 15, "status": "inactive",
        "executionMode": "paper", "parameters": {},
    })
    return store


def test_user_slot_beats_academy_and_lock_blocks_allocator(tmp_path):
    store = _store(tmp_path)
    card = StrategyScorecard(store=store)
    academy = card.propose_academy_slot("s1", symbol="ETH/USD", timeframe=15)
    assert academy["lamp"] == bp.StrategyLamp.YELLOW.value
    assert academy["origin"] == bp.SLOT_ORIGIN_ACADEMY
    user = card.upsert_user_slot("s1", symbol="ETH/USD", timeframe=15)
    assert user["origin"] == bp.SLOT_ORIGIN_USER
    assert user["lamp"] == bp.StrategyLamp.GREEN_SOLID.value
    again = card.propose_academy_slot("s1", symbol="ETH/USD", timeframe=15)
    assert again["origin"] == bp.SLOT_ORIGIN_USER
    locked = card.upsert_user_slot("s1", symbol="ETH/USD", timeframe=15, locked=True)
    assert locked["lamp"] == bp.StrategyLamp.RED_GLOW.value
    alloc = StrategyAllocator(lock_provider=card.is_locked)
    verdict = alloc.evaluate("s1", "ETH/USD", 15, bp.Regime.RANGING_CHOP.value)
    assert verdict["allow"] is False and verdict["locked"] is True


def test_initialize_pf_gate_releases_green_glow(tmp_path):
    store = _store(tmp_path)
    queue = FakeQueue()
    card = StrategyScorecard(store=store, queue=queue)
    out = card.start_initialize("s1", origin="idle")
    assert out["ok"] is True
    assert len(queue.jobs) == 2
    assert queue.jobs[0].kind == "pull_parameters"
    assert queue.jobs[1].kind == "backtest"
    fail = card.complete_initialize("s1", profit_factor=1.1)
    assert fail["released"] is False
    assert card.header("s1")["lamp"] == bp.StrategyLamp.YELLOW.value
    ok = card.complete_initialize("s1", profit_factor=1.6, trade_count=12, win_rate=0.55)
    assert ok["released"] is True
    assert card.header("s1")["lamp"] == bp.StrategyLamp.GREEN_GLOW.value
    assert card.header("s1")["stage1_done"] is True


def test_idle_batch_skips_live_and_busy_tv(tmp_path):
    store = _store(tmp_path)
    card = StrategyScorecard(
        store=store, queue=FakeQueue(),
        live_trading_provider=lambda: True,
        idle_provider=lambda: True,
    )
    assert card.idle_stage1_tick()["reason"] == "live"
    card.live_trading_provider = lambda: False
    card.idle_provider = lambda: False
    assert card.idle_stage1_tick()["reason"] == "tv_busy"
    card.idle_provider = lambda: True
    ran = card.idle_stage1_tick()
    assert ran["skipped"] is False and ran["strategy_id"] == "s1"
    card.mark_options_opened("s1")
    card.complete_initialize("s1", profit_factor=2.0)
    assert card.idle_stage1_tick()["reason"] == "none"


def test_library_snapshot_omits_pine_code(tmp_path):
    store = _store(tmp_path)
    card = StrategyScorecard(store=store)
    card.upsert_user_slot("s1", symbol="BTC/USD", timeframe=15)
    snap = card.library_snapshot()
    row = snap["strategies"][0]
    assert "code" not in row
    assert row["id"] == "s1"
    assert row["lamp"] == bp.StrategyLamp.GREEN_SOLID.value
    assert row["best_symbol"] == "BTC/USD"
    detail = card.scorecard("s1")
    assert "code" not in detail["strategy"]
    assert detail["ok"] is True


def test_validate_promotes_user_slots_when_gate_passes(tmp_path):
    store = _store(tmp_path)
    card = StrategyScorecard(
        store=store,
        ga_runner=lambda cfg: {"shadowGate": {"passed": True}, "bestIndividual": {"profitFactor": 1.9}},
    )
    card.upsert_user_slot("s1", symbol="BTC/USD", timeframe=15)
    out = card.start_validate("s1")
    assert out["ok"] is True
    slot = store.get_strategy_slot("s1", "BTC/USD", 15, "")
    assert slot["lamp"] == bp.StrategyLamp.GREEN_GLOW.value


def test_pf_from_job_result_reads_summary():
    assert pf_from_job_result({"backtest": {"summary": {"profitFactor": 1.7}}}) == 1.7
    assert pf_from_job_result({}) == 0.0


def test_aggregate_lamp_blocked_favorites_are_red():
    header = {"stage1_done": False, "pf_after_fees": 0, "lamp": "gray"}
    slots = [{"favorite": True, "locked": True, "lamp": "red_glow", "origin": "user"}]
    assert aggregate_lamp(header, slots) == bp.StrategyLamp.RED_GLOW.value


def test_set_scorecard_singleton_roundtrip(tmp_path):
    store = _store(tmp_path)
    card = StrategyScorecard(store=store)
    set_strategy_scorecard(card)
    from app.optimizer.strategy_scorecard import get_strategy_scorecard
    assert get_strategy_scorecard() is card
    set_strategy_scorecard(None)
