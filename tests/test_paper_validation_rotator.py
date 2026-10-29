"""Paper-validation rotator: name-dedupe, cohort of 6, 6h rotate, resume, gates."""
from __future__ import annotations

from types import SimpleNamespace

from app.core import blueprint as bp
from app.execution.paper_validation_lake import PaperValidationLake
from sigma.orchestration.paper_validation_rotator import (
    PaperValidationRotator,
    dedupe_latest_versions,
    select_cohort,
    set_paper_validation_rotator,
)


def _row(sid, name, *, created="2026-01-01", version=1, script="PUB;x",
         mode="kraken_paper", archived=None, status="inactive"):
    return {
        "id": sid,
        "name": name,
        "createdAt": created,
        "version": version,
        "tv_script_id": script,
        "parameters": {"tv_script_id": script},
        "executionMode": mode,
        "status": status,
        "archivedAt": archived,
        "assetPair": "BTC/USD",
        "interval": 15,
    }


class FakeStore:
    def __init__(self, strategies):
        self.strategies = list(strategies)
        self.cohorts = []
        self.runs = []

    def list_strategies(self, include_archived=False):
        rows = self.strategies
        if not include_archived:
            rows = [r for r in rows if r.get("status") != "archived" and not r.get("archivedAt")]
        return rows

    def upsert_paper_validation_cohort(self, record):
        cid = record["cohort_id"]
        self.cohorts = [c for c in self.cohorts if c.get("cohort_id") != cid]
        self.cohorts.append(dict(record))

    def get_active_paper_validation_cohort(self):
        active = [c for c in self.cohorts if c.get("status") == bp.PAPER_VALIDATION_STATE_ACTIVE]
        return dict(active[-1]) if active else None

    def upsert_paper_validation_run(self, record):
        key = (record.get("cohort_id"), record.get("strategy_id"))
        self.runs = [r for r in self.runs if (r.get("cohort_id"), r.get("strategy_id")) != key]
        self.runs.append(dict(record))

    def list_paper_validation_runs(self, limit=50):
        return list(reversed(self.runs))[:limit]


class FakeProvisioner:
    def __init__(self):
        self.calls = []
        self.records = {}

    def upsert(self, sid, symbol, interval, enable=False):
        self.calls.append(("upsert", sid, enable))
        self.records[sid] = SimpleNamespace(
            strategy_id=sid, symbol=symbol, interval=interval, enabled=enable,
        )
        return {"strategy_id": sid, "enabled": enable}

    def disable(self, sid, reason=""):
        self.calls.append(("disable", sid, reason))
        rec = self.records.get(sid)
        if rec is not None:
            rec.enabled = False
        return {"strategy_id": sid, "enabled": False}

    def get(self, sid):
        return self.records.get(sid)

    def snapshot(self):
        return {"enabled_count": sum(1 for r in self.records.values() if r.enabled)}

    def webhook_url(self):
        return "https://abc.ngrok-free.app/api/v1/signal/ingest"

    def refresh_webhooks(self, strategy_ids=None):
        self.calls.append(("refresh", list(strategy_ids or [])))
        return []


class FakeLife:
    def __init__(self):
        self.started = []

    def start(self, strategy_id, symbol, **kwargs):
        self.started.append((strategy_id, kwargs.get("trigger_path"), kwargs.get("execution_mode")))
        return SimpleNamespace(ok=True)


def _rotator(tmp_path, strategies, *, now=1_700_000_000.0, safety=None, **kwargs):
    session = tmp_path / "tv_storage_state.json"
    session.write_text("{}", encoding="utf-8")
    clock = {"t": now}

    def public_url():
        return SimpleNamespace(
            url="https://abc.ngrok-free.app", source="ngrok", changed=False,
        )

    return PaperValidationRotator(
        store=FakeStore(strategies),
        provisioner=kwargs.get("provisioner") or FakeProvisioner(),
        lifecycle=kwargs.get("lifecycle") or FakeLife(),
        lake=kwargs.get("lake") or PaperValidationLake(base_dir=str(tmp_path / "lake")),
        safety=safety,
        public_url_fn=kwargs.get("public_url_fn") or public_url,
        session_path=str(session),
        enabled=True,
        flatten=kwargs.get("flatten") or (lambda ids: None),
        clock=lambda: clock["t"],
        academy_data_dir=str(tmp_path),
        **{k: v for k, v in kwargs.items() if k not in (
            "provisioner", "lifecycle", "lake", "flatten", "public_url_fn",
        )},
    ), clock


def test_dedupe_keeps_newest_created_at():
    rows = [
        _row("old", "Alpha Grid", created="2025-01-01", version=3),
        _row("new", "alpha  grid", created="2026-06-01", version=1),
        _row("mid", "Alpha Grid", created="2026-01-01", version=9),
    ]
    out = dedupe_latest_versions(rows)
    assert [r["id"] for r in out] == ["new"]


def test_select_cohort_size_six_never_tested_first():
    cands = [_row(f"s{i}", f"Name {i}") for i in range(10)]
    picked = select_cohort(cands, [], size=6)
    assert len(picked) == 6
    assert [r["id"] for r in picked] == [f"s{i}" for i in range(6)]
    history = [{"strategy_id": "s0", "ended_at": 100.0}, {"strategy_id": "s1", "ended_at": 50.0}]
    picked2 = select_cohort(cands, history, size=10)
    assert [r["id"] for r in picked2[:8]] == [f"s{i}" for i in range(2, 10)]
    assert [r["id"] for r in picked2[8:]] == ["s1", "s0"]


def test_select_skips_live_and_missing_script():
    rows = [
        _row("live", "L", mode="live"),
        _row("noscript", "N", script=""),
        _row("ok", "O"),
    ]
    rows[1]["tv_script_id"] = ""
    rows[1]["parameters"] = {}
    picked = select_cohort(rows, [], size=6)
    assert [r["id"] for r in picked] == ["ok"]


def test_tick_arms_six_and_rotates_exactly_once_after_six_hours(tmp_path):
    strategies = [_row(f"s{i}", f"Strat {i}") for i in range(8)]
    rot, clock = _rotator(tmp_path, strategies)
    first = rot.tick(clock["t"])
    assert first["state"] == bp.PAPER_VALIDATION_STATE_ACTIVE
    assert first["reason"] == "armed"
    assert len(first["cohort"]["strategy_ids"]) == 6
    mid = rot.tick(clock["t"] + 6 * 3600 - 1)
    assert mid["reason"] == "active"
    assert mid["cohort"]["cohort_id"] == first["cohort"]["cohort_id"]
    later = rot.tick(clock["t"] + 6 * 3600)
    assert later["reason"] == "armed"
    assert later["cohort"]["cohort_id"] != first["cohort"]["cohort_id"]
    assert rot.last_reason == "armed"


def test_resume_active_cohort_from_store(tmp_path):
    strategies = [_row(f"s{i}", f"Strat {i}") for i in range(6)]
    store = FakeStore(strategies)
    store.upsert_paper_validation_cohort({
        "cohort_id": "pv_resume",
        "started_at": 10.0,
        "ends_at": 10.0 + 6 * 3600,
        "status": bp.PAPER_VALIDATION_STATE_ACTIVE,
        "strategy_ids": ["s0", "s1"],
    })
    session = tmp_path / "tv.json"
    session.write_text("{}", encoding="utf-8")
    rot = PaperValidationRotator(
        store=store,
        provisioner=FakeProvisioner(),
        lifecycle=FakeLife(),
        lake=PaperValidationLake(base_dir=str(tmp_path / "lake")),
        public_url_fn=lambda: SimpleNamespace(url="https://abc.ngrok-free.app", source="ngrok", changed=False),
        session_path=str(session),
        enabled=True,
        flatten=lambda ids: None,
        clock=lambda: 20.0,
        academy_data_dir=str(tmp_path),
    )
    status = rot.status()
    assert status["state"] == bp.PAPER_VALIDATION_STATE_ACTIVE
    assert status["cohort"]["cohort_id"] == "pv_resume"


def test_gates_fail_closed_no_session_no_url_kill(tmp_path):
    strategies = [_row("s0", "A")]
    kill = SimpleNamespace(check=lambda: SimpleNamespace(allowed=False, code="KILL_SWITCH"))
    rot, clock = _rotator(tmp_path, strategies, safety=kill)
    out = rot.tick(clock["t"])
    assert out["state"] == bp.PAPER_VALIDATION_STATE_IDLE
    assert "KILL_SWITCH" in out["reason"]
    assert rot.provisioner.calls == []

    rot2, clock2 = _rotator(tmp_path, strategies)
    rot2.session_path = str(tmp_path / "missing.json")
    out2 = rot2.tick(clock2["t"])
    assert "tv_session_missing" in out2["reason"]

    rot3, clock3 = _rotator(
        tmp_path, strategies,
        public_url_fn=lambda: SimpleNamespace(url="http://127.0.0.1:8000", source="env", changed=False),
    )
    out3 = rot3.tick(clock3["t"])
    assert "public_url_loopback" in out3["reason"]


def test_url_change_reupserts_active_cohort(tmp_path):
    strategies = [_row(f"s{i}", f"Strat {i}") for i in range(3)]
    prov = FakeProvisioner()
    changed = {"v": False}

    def public_url():
        return SimpleNamespace(
            url="https://abc.ngrok-free.app", source="ngrok", changed=changed["v"],
        )

    rot, clock = _rotator(tmp_path, strategies, provisioner=prov, public_url_fn=public_url)
    rot.tick(clock["t"])
    changed["v"] = True
    rot.tick(clock["t"] + 10)
    assert ("refresh", ["s0", "s1", "s2"]) in prov.calls


def test_set_rotator_singleton(tmp_path):
    rot, _ = _rotator(tmp_path, [_row("s0", "A")])
    set_paper_validation_rotator(rot)
    from sigma.orchestration.paper_validation_rotator import get_paper_validation_rotator
    assert get_paper_validation_rotator() is rot
    set_paper_validation_rotator(None)
