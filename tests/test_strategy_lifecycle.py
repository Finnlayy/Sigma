"""§31 — Die 3 Trigger-Pfade zur Strategie-Platzierung (StrategyLifecycleService)."""
from __future__ import annotations

import time

import pytest

from app.core import blueprint as bp
from app.execution.VirtualBotEngine import VirtualBotEngine
from app.execution.capital_flywheel_engine import CapitalFlywheelEngine
from app.quant.glint_orderbook_verifier import (GlintOrderbookVerifier,
                                                OrderbookSnapshot)
from app.services.strategy_lifecycle_service import (LifecycleError, PAUSED,
                                                     QUARANTINED, RUNNING,
                                                     StrategyLifecycleService)


class _FakeAlerts:
    def __init__(self) -> None:
        self.calls = []

    def upsert(self, strategy_id, symbol, interval=15, *, enable=False):
        self.calls.append(("upsert", strategy_id, symbol, str(interval), enable))
        return {"strategy_id": strategy_id, "symbol": symbol, "enabled": enable,
                "webhook_url": "http://localhost:8000/api/v1/signal/ingest"}

    def enable(self, strategy_id, reason=""):
        self.calls.append(("enable", strategy_id, reason))
        return {"strategy_id": strategy_id, "enabled": True}

    def disable(self, strategy_id, reason=""):
        self.calls.append(("disable", strategy_id, reason))
        return {"strategy_id": strategy_id, "enabled": False}


class _FakeJob:
    def __init__(self, kind: str) -> None:
        self.job_id = f"tvj_{kind}"
        self.kind = kind


class _FakeQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs = []
        self.fail = fail

    def submit(self, kind, **payload):
        if self.fail:
            raise RuntimeError("worker offline")
        self.jobs.append((kind, payload))
        return _FakeJob(kind)


class _FakeSafety:
    def __init__(self, kill=False, paused=False) -> None:
        self._snap = {"kill_switch": kill, "paused": paused}

    def snapshot(self):
        return dict(self._snap)


def _service(*, funded: float = 5_000.0, queue=None, safety=None,
             verifier=None) -> StrategyLifecycleService:
    flywheel = CapitalFlywheelEngine()
    if funded:
        flywheel.deposit(funded)
    return StrategyLifecycleService(
        virtual_bots=VirtualBotEngine(), alert_provisioner=_FakeAlerts(),
        tv_queue=queue or _FakeQueue(), flywheel=flywheel,
        safety=safety or _FakeSafety(), verifier=verifier or GlintOrderbookVerifier(),
    )


def _book(bid: float, ask: float) -> OrderbookSnapshot:
    return OrderbookSnapshot("XBTUSD", [(99.99, bid)], [(100.01, ask)], time.time())


# ------------------------------------------------------------- Pfad 1 (§31.1)

def test_manual_path_runs_all_five_steps():
    svc = _service()
    record = svc.start("cisd_v6", "XBTUSD", budget_eur=250.0, fixed_leverage=5)
    assert record.ok is True and record.state == RUNNING
    assert [s.step for s in record.steps][1:] == list(bp.LIFECYCLE_STEPS)
    assert record.execution_mode == "live"
    assert record.fixed_leverage == 5
    assert record.as_dict()["badge"] == "[ 5x HEBEL ]"
    assert record.bot_id


def test_manual_path_can_choose_paper_mode():
    svc = _service()
    record = svc.start("cisd_v6", "XBTUSD", execution_mode="kraken_paper")
    assert record.ok is True
    assert record.execution_mode == bp.ExecutionMode.KRAKEN_PAPER.value
    budget_step = next(s for s in record.steps if s.step == bp.LIFECYCLE_STEPS[0])
    assert budget_step.data["budget_eur"] == 0.0    # kein Live-Kapital gebunden


def test_manual_path_blocked_by_kill_switch():
    svc = _service(safety=_FakeSafety(kill=True))
    record = svc.start("cisd_v6", "XBTUSD")
    assert record.ok is False and record.code == "KILL_SWITCH_ACTIVE"
    assert record.state == PAUSED


def test_budget_reservation_failure_stops_pipeline():
    svc = _service(funded=100.0)
    record = svc.start("cisd_v6", "XBTUSD", budget_eur=500.0)
    assert record.ok is False and record.code == "INSUFFICIENT_FREE_FUTURES"
    assert len(record.steps) == 1                   # preflight, dann Abbruch


# ------------------------------------------------------------- Pfad 2 (§31.2)

def test_autonomous_path_requires_glint_and_orderbook():
    svc = _service()
    weak = svc.start("cisd_v6", "XBTUSD",
                     trigger_path=bp.TriggerPath.AUTONOMOUS_REGIME.value,
                     glint_score=6.0, orderbook=_book(80, 20))
    assert weak.code == "GLINT_SCORE_TOO_LOW"

    blind = svc.start("cisd_v6", "XBTUSD",
                      trigger_path=bp.TriggerPath.AUTONOMOUS_REGIME.value,
                      glint_score=9.0)
    assert blind.code == "ORDERBOOK_AUDIT_MISSING"


def test_autonomous_path_vetoed_by_liquidity_trap():
    svc = _service()
    record = svc.start("cisd_v6", "XBTUSD",
                       trigger_path=bp.TriggerPath.AUTONOMOUS_REGIME.value,
                       glint_score=9.0, orderbook=_book(10, 90))
    assert record.ok is False
    assert record.code == bp.ORDERBOOK_WALL_REJECT


def test_autonomous_path_happy_path_goes_live():
    svc = _service()
    record = svc.start("cisd_v6", "XBTUSD",
                       trigger_path=bp.TriggerPath.AUTONOMOUS_REGIME.value,
                       glint_score=8.0, orderbook=_book(90, 10))
    assert record.ok is True
    assert record.execution_mode == "live"
    preflight = record.steps[0]
    assert preflight.data["confluence"]["verdict"] == \
        bp.ConfluenceVerdict.CONFLUENCE_CONFIRMED.value


def test_autonomous_path_cannot_be_paper():
    svc = _service()
    with pytest.raises(LifecycleError) as exc:
        svc.start("cisd_v6", "XBTUSD",
                  trigger_path=bp.TriggerPath.AUTONOMOUS_REGIME.value,
                  execution_mode="kraken_paper", glint_score=9.0)
    assert exc.value.code == "EXECUTION_MODE_NOT_ALLOWED"


# ------------------------------------------------------------- Pfad 3 (§31.3)

def test_scout_path_is_paper_only_and_needs_no_budget():
    svc = _service(funded=0.0)
    record = svc.start("scout_candidate", "SOLUSD",
                       trigger_path=bp.TriggerPath.SCOUT_INCUBATOR.value,
                       budget_eur=250.0)
    assert record.ok is True
    assert record.execution_mode == bp.ExecutionMode.KRAKEN_PAPER.value
    assert bp.TRIGGER_PATH_MODES["SCOUT_INCUBATOR"] == ("kraken_paper",)


def test_scout_path_rejects_live_request():
    svc = _service()
    with pytest.raises(LifecycleError) as exc:
        svc.start("scout_candidate", "SOLUSD",
                  trigger_path=bp.TriggerPath.SCOUT_INCUBATOR.value,
                  execution_mode="live")
    assert exc.value.code == "EXECUTION_MODE_NOT_ALLOWED"


def test_unknown_trigger_path_is_rejected():
    svc = _service()
    with pytest.raises(LifecycleError):
        svc.start("x", "XBTUSD", trigger_path="TELEPATHY")


# ------------------------------------------------------- Schritt-Fehlerpfade

def test_pine_job_failure_rolls_back_budget():
    svc = _service(queue=_FakeQueue(fail=True))
    free_before = svc.flywheel.free_futures_eur
    record = svc.start("cisd_v6", "XBTUSD", budget_eur=250.0)
    assert record.ok is False and record.code == "ERR_TV_PINE_COMPILE_ERROR"
    assert any(s.step == "rollback" for s in record.steps)
    assert svc.flywheel.free_futures_eur == free_before


# ------------------------------------------------------------ State Machine

def test_state_machine_pause_resume_quarantine():
    svc = _service()
    svc.start("cisd_v6", "XBTUSD")
    assert svc.status("cisd_v6")["state"] == RUNNING

    paused = svc.pause("cisd_v6", "operator")
    assert paused["state"] == PAUSED
    assert all(card["runner_status"] == PAUSED for card in paused["bots"])

    resumed = svc.resume("cisd_v6")
    assert resumed["state"] == RUNNING

    quarantined = svc.quarantine("cisd_v6", "3 strikes")
    assert quarantined["state"] == QUARANTINED
    with pytest.raises(LifecycleError) as exc:
        svc.resume("cisd_v6")
    assert exc.value.code == "QUARANTINE_LOCKED"


def test_transition_on_unknown_strategy_is_404():
    svc = _service()
    with pytest.raises(LifecycleError) as exc:
        svc.pause("never_started")
    assert exc.value.status_code == 404


def test_snapshot_exposes_paths_and_steps():
    svc = _service()
    svc.start("cisd_v6", "XBTUSD")
    snap = svc.snapshot()
    assert set(snap["trigger_paths"]) == {p.value for p in bp.TriggerPath}
    assert snap["steps"] == list(bp.LIFECYCLE_STEPS)
    assert snap["states"] == [RUNNING, PAUSED, QUARANTINED]
    assert snap["active"]["cisd_v6"] == RUNNING


def test_bot_card_shows_lifecycle_metadata():
    svc = _service()
    record = svc.start("cisd_v6", "XBTUSD", fixed_leverage=5)
    card = svc.virtual_bots.get(record.bot_id).to_card()
    assert card["fixed_leverage"] == 5
    assert card["leverage_badge"] == "[ 5x HEBEL ]"
    assert card["execution_mode"] == "live"
    assert card["trigger_path"] == bp.TriggerPath.MANUAL.value


# ------------------------------------------------------------------- API ---

def test_lifecycle_api_roundtrip():
    from fastapi.testclient import TestClient

    import app.server.routes_sigma as routes
    from app.server.main import app
    from app.services.strategy_lifecycle_service import set_lifecycle_service

    set_lifecycle_service(_service())
    routes._FLYWHEEL = None
    client = TestClient(app)
    try:
        started = client.post("/api/strategies/api_strat/start",
                              json={"symbol": "XBTUSD", "budget_eur": 100.0,
                                    "fixed_leverage": 2})
        assert started.status_code == 200, started.json()
        assert started.json()["state"] == RUNNING

        assert client.get("/api/strategies/api_strat/lifecycle").json()["ok"] is True
        assert client.get("/api/strategies/ghost/lifecycle").status_code == 404

        paused = client.post("/api/strategies/api_strat/pause",
                             json={"reason": "operator"})
        assert paused.json()["state"] == PAUSED
        assert client.post("/api/strategies/api_strat/quarantine",
                           json={"reason": "risk"}).json()["state"] == QUARANTINED
        blocked = client.post("/api/strategies/api_strat/resume", json={})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "QUARANTINE_LOCKED"

        snapshot = client.get("/api/v1/lifecycle").json()
        assert snapshot["active"]["api_strat"] == QUARANTINED
    finally:
        set_lifecycle_service(None)


def test_section_31_is_no_longer_pending():
    assert not any(s.startswith("31 ") for s in bp.DOCS_PENDING_SECTIONS)
