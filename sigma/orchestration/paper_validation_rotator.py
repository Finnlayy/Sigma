"""
=========================================================
Datei:      sigma/orchestration/paper_validation_rotator.py
Zweck:      6 Strategien × 6h Paper-Validierung via TV-Alerts + Self-Webhook.
=========================================================
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.core import blueprint as bp
from app.core.duckdb_store import get_store
from app.core.public_url import is_loopback, resolve_public_url
from app.execution.paper_validation_lake import get_paper_validation_lake
from app.optimizer.academy_progress import persist_paper_validation_runs
from app.services.strategy_lifecycle_service import get_lifecycle_service
from app.tv.alert_provisioner import get_alert_provisioner

logger = logging.getLogger("sigma.orchestration.paper_validation")


def normalize_strategy_name(name: str) -> str:
    return " ".join(str(name or "").casefold().split())


def _created_key(row: Dict[str, Any]) -> Tuple[str, int]:
    created = str(row.get("createdAt") or row.get("created_at") or "")
    version = int(row.get("version") or 1)
    return (created, version)


def dedupe_latest_versions(strategies: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same display name -> keep newest (createdAt, version)."""
    winners: Dict[str, Dict[str, Any]] = {}
    for row in strategies:
        key = normalize_strategy_name(str(row.get("name") or row.get("id") or ""))
        if not key:
            continue
        prev = winners.get(key)
        if prev is None or _created_key(row) >= _created_key(prev):
            winners[key] = row
    return list(winners.values())


def _has_tv_script(row: Dict[str, Any]) -> bool:
    params = row.get("parameters") or {}
    if not isinstance(params, dict):
        params = {}
    return bool(row.get("tv_script_id") or params.get("tv_script_id"))


def _is_live(row: Dict[str, Any]) -> bool:
    mode = str(row.get("executionMode") or row.get("execution_mode") or "").lower()
    return mode == bp.ExecutionMode.LIVE.value


def select_cohort(
    candidates: Sequence[Dict[str, Any]],
    history: Sequence[Dict[str, Any]],
    size: int = bp.PAPER_VALIDATION_COHORT_SIZE,
    *,
    last_ended: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Never-tested first, then least-recently-tested. Skip live + no tv_script_id."""
    ended = dict(last_ended or {})
    for run in history:
        sid = str(run.get("strategy_id") or "")
        ts = run.get("ended_at")
        if not sid or ts is None:
            continue
        try:
            val = float(ts)
        except (TypeError, ValueError):
            continue
        ended[sid] = max(val, ended.get(sid, 0.0))
    eligible = [
        row for row in candidates
        if _has_tv_script(row)
        and not _is_live(row)
        and str(row.get("status") or "") != "archived"
        and not row.get("archivedAt")
    ]
    eligible.sort(key=lambda row: (
        0 if str(row.get("id")) not in ended else 1,
        ended.get(str(row.get("id")), 0.0),
        str(row.get("id")),
    ))
    cap = max(0, int(size))
    return eligible[:cap]


class PaperValidationRotator:
    def __init__(
        self,
        *,
        store=None,
        provisioner=None,
        lifecycle=None,
        lake=None,
        safety=None,
        loop_e=None,
        public_url_fn: Optional[Callable[..., Any]] = None,
        session_path: Optional[str] = None,
        enabled: Optional[bool] = None,
        cohort_size: int = bp.PAPER_VALIDATION_COHORT_SIZE,
        rotation_hours: float = bp.PAPER_VALIDATION_ROTATION_HOURS,
        flatten: Optional[Callable[[List[str]], None]] = None,
        clock: Callable[[], float] = time.time,
        academy_data_dir: Optional[str] = None,
    ) -> None:
        self.store = store
        self._provisioner = provisioner
        self._lifecycle = lifecycle
        self._lake = lake
        self._safety = safety
        self._loop_e = loop_e
        self._public_url_fn = public_url_fn or resolve_public_url
        self.session_path = session_path or bp.PATH_TV_STORAGE_STATE
        self._enabled_override = enabled
        self.cohort_size = int(cohort_size)
        self.rotation_hours = float(rotation_hours)
        self._flatten = flatten
        self._clock = clock
        self._academy_data_dir = academy_data_dir
        self.state = bp.PAPER_VALIDATION_STATE_IDLE
        self.active_cohort: Optional[Dict[str, Any]] = None
        self.last_reason = ""
        self._last_webhook = ""
        self._armed_once = False

    @property
    def provisioner(self):
        if self._provisioner is None:
            self._provisioner = get_alert_provisioner()
        return self._provisioner

    @property
    def lake(self):
        if self._lake is None:
            self._lake = get_paper_validation_lake()
        return self._lake

    def enabled(self) -> bool:
        if self._enabled_override is not None:
            return bool(self._enabled_override)
        return os.environ.get(bp.PAPER_VALIDATION_ENABLED_ENV, "1") != "0"

    def gates(self, now: Optional[float] = None) -> Dict[str, Any]:
        reasons: List[str] = []
        if not self.enabled():
            reasons.append("disabled")
        if self._safety is not None and hasattr(self._safety, "check"):
            verdict = self._safety.check()
            allowed = bool(getattr(verdict, "allowed", True))
            if not allowed:
                reasons.append(str(getattr(verdict, "code", "SAFETY") or "SAFETY"))
        if not os.path.exists(self.session_path):
            reasons.append("tv_session_missing")
        pub = self._public_url_fn() if self._public_url_fn else resolve_public_url()
        url = getattr(pub, "url", None) or (pub.get("url") if isinstance(pub, dict) else str(pub))
        source = getattr(pub, "source", "") or (pub.get("source") if isinstance(pub, dict) else "")
        changed = bool(getattr(pub, "changed", False) or (pub.get("changed") if isinstance(pub, dict) else False))
        if is_loopback(str(url)):
            reasons.append("public_url_loopback")
        return {
            "ok": not reasons,
            "reasons": reasons,
            "public_url": str(url or ""),
            "public_source": source,
            "url_changed": changed,
            "now": float(now if now is not None else self._clock()),
        }

    def cohort_id_for(self, strategy_id: str) -> str:
        active = self._ensure_active()
        ids = list((active or {}).get("strategy_ids") or [])
        if strategy_id in ids:
            return str(active.get("cohort_id") or "")
        return ""

    def status(self) -> Dict[str, Any]:
        active = self._ensure_active()
        now = self._clock()
        remaining = 0.0
        if active and active.get("ends_at"):
            remaining = max(0.0, float(active["ends_at"]) - now)
        return {
            "state": self.state,
            "reason": self.last_reason,
            "cohort": active,
            "remaining_s": remaining,
            "cohort_size": self.cohort_size,
            "rotation_hours": self.rotation_hours,
            "enabled": self.enabled(),
        }

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        store = self._store()
        if store is None or not hasattr(store, "list_paper_validation_runs"):
            return []
        return store.list_paper_validation_runs(limit=limit)

    def tick(self, now: Optional[float] = None, *, force: bool = False) -> Dict[str, Any]:
        ts = float(now if now is not None else self._clock())
        self._ensure_active()
        gate = self.gates(ts)
        if self.state == bp.PAPER_VALIDATION_STATE_ACTIVE and self.active_cohort:
            if gate.get("url_changed"):
                self._reupsert_webhooks()
            due = float(self.active_cohort.get("ends_at") or 0.0) <= ts
            if due or force:
                self._close_cohort(ts)
            else:
                self.last_reason = "active"
                return {**self.status(), "gates": gate}
        if not gate["ok"]:
            self.last_reason = ",".join(gate["reasons"])
            return {**self.status(), "gates": gate}
        self._arm_cohort(ts)
        return {**self.status(), "gates": gate}

    def _arm_cohort(self, now: float) -> None:
        self.state = bp.PAPER_VALIDATION_STATE_ARMING
        strategies = self._list_strategies()
        latest = dedupe_latest_versions(strategies)
        history = self.history(limit=500)
        remaining = self._slot_budget()
        size = min(self.cohort_size, remaining)
        selected = select_cohort(latest, history, size)
        if not selected:
            self.state = bp.PAPER_VALIDATION_STATE_IDLE
            self.last_reason = "no_candidates"
            return
        cohort_id = f"pv_{uuid.uuid4().hex[:12]}"
        hours = self.rotation_hours
        ends = now + hours * 3600.0
        ids = [str(row["id"]) for row in selected]
        record = {
            "cohort_id": cohort_id,
            "started_at": now,
            "ends_at": ends,
            "status": bp.PAPER_VALIDATION_STATE_ACTIVE,
            "strategy_ids": ids,
        }
        self._persist_cohort(record)
        for row in selected:
            sid = str(row["id"])
            symbol = str(row.get("assetPair") or "BTC/USD")
            interval = row.get("interval") or 15
            try:
                self.provisioner.upsert(sid, symbol, interval, enable=True)
            except Exception as exc:
                logger.warning("alert upsert %s failed: %s", sid, exc)
            self._start_lifecycle(sid, symbol, interval)
            self._write_run_open(cohort_id, row, now)
        self.active_cohort = record
        self.state = bp.PAPER_VALIDATION_STATE_ACTIVE
        self.last_reason = "armed"
        self._armed_once = True
        webhook_fn = getattr(self.provisioner, "webhook_url", None)
        self._last_webhook = str(webhook_fn() if callable(webhook_fn) else "")

    def _close_cohort(self, now: float) -> None:
        active = self.active_cohort or {}
        cohort_id = str(active.get("cohort_id") or "")
        ids = list(active.get("strategy_ids") or [])
        self.state = bp.PAPER_VALIDATION_STATE_CLOSING
        for sid in ids:
            try:
                self.provisioner.disable(sid, reason="paper_validation_rotate")
            except Exception as exc:
                logger.warning("alert disable %s failed: %s", sid, exc)
        self._flatten_cohort(ids)
        written: List[str] = []
        try:
            written = self.lake.flush(cohort_id)
        except Exception as exc:
            logger.warning("lake flush failed: %s", exc)
        summaries = []
        for sid in ids:
            stats = self.lake.summary(sid, cohort_id) if hasattr(self.lake, "summary") else self.lake.counts(sid, cohort_id)
            run = {
                "cohort_id": cohort_id,
                "strategy_id": sid,
                "ended_at": now,
                "signals": int(stats.get("signals") or 0),
                "fills": int(stats.get("fills") or 0),
                "rejected": int(stats.get("rejected") or 0),
                "net_pnl_pct": float(stats.get("net_pnl_pct") or 0.0),
                "profit_factor": float(stats.get("profit_factor") or 0.0),
                "win_rate": float(stats.get("win_rate") or 0.0),
                "status": "closed",
            }
            self._persist_run(run)
            summaries.append(run)
            self._ingest_loop_e(sid, stats)
        try:
            persist_paper_validation_runs(summaries, data_dir=self._academy_data_dir)
        except Exception as exc:
            logger.warning("academy paper-validation persist failed: %s", exc)
        closed = dict(active)
        closed["status"] = "closed"
        closed["ended_at"] = now
        closed["parquet"] = written
        self._persist_cohort(closed)
        self.active_cohort = None
        self.state = bp.PAPER_VALIDATION_STATE_IDLE
        self.last_reason = "rotated"
        logger.info("paper validation cohort %s closed (%d strategies)", cohort_id, len(ids))

    def _reupsert_webhooks(self) -> None:
        ids = list((self.active_cohort or {}).get("strategy_ids") or [])
        if hasattr(self.provisioner, "refresh_webhooks"):
            self.provisioner.refresh_webhooks(ids)
            return
        for sid in ids:
            rec = self.provisioner.get(sid) if hasattr(self.provisioner, "get") else None
            if rec is None:
                continue
            symbol = rec.symbol if hasattr(rec, "symbol") else rec.get("symbol")
            interval = rec.interval if hasattr(rec, "interval") else rec.get("interval")
            try:
                self.provisioner.upsert(sid, symbol, interval, enable=True)
            except Exception as exc:
                logger.warning("webhook re-upsert %s failed: %s", sid, exc)

    def _slot_budget(self) -> int:
        tier_cap = int(bp.TV_SUBSCRIPTION_TIERS.get(bp.TV_SUBSCRIPTION_TIER_DEFAULT, 400))
        soft = min(int(bp.PAPER_VALIDATION_ALERT_SOFT_CAP), tier_cap)
        enabled = 0
        try:
            enabled = int(self.provisioner.snapshot().get("enabled_count") or 0)
        except Exception:
            enabled = 0
        return max(0, soft - enabled)

    def _list_strategies(self) -> List[Dict[str, Any]]:
        store = self._store()
        if store is None or not hasattr(store, "list_strategies"):
            return []
        return store.list_strategies(include_archived=False)

    def _store(self):
        if self.store is not None:
            return self.store
        try:
            self.store = get_store()
        except Exception:
            return None
        return self.store

    def _ensure_active(self) -> Optional[Dict[str, Any]]:
        if self.active_cohort:
            return self.active_cohort
        store = self._store()
        if store is None or not hasattr(store, "get_active_paper_validation_cohort"):
            return None
        row = store.get_active_paper_validation_cohort()
        if row:
            self.active_cohort = row
            self.state = bp.PAPER_VALIDATION_STATE_ACTIVE
        return self.active_cohort

    def _persist_cohort(self, record: Dict[str, Any]) -> None:
        store = self._store()
        if store is not None and hasattr(store, "upsert_paper_validation_cohort"):
            store.upsert_paper_validation_cohort(record)

    def _persist_run(self, record: Dict[str, Any]) -> None:
        store = self._store()
        if store is not None and hasattr(store, "upsert_paper_validation_run"):
            store.upsert_paper_validation_run(record)

    def _write_run_open(self, cohort_id: str, row: Dict[str, Any], now: float) -> None:
        self._persist_run({
            "cohort_id": cohort_id,
            "strategy_id": row["id"],
            "symbol": row.get("assetPair") or "",
            "interval_min": int(row.get("interval") or 15),
            "started_at": now,
            "ended_at": None,
            "signals": 0,
            "fills": 0,
            "rejected": 0,
            "status": "active",
        })

    def _start_lifecycle(self, strategy_id: str, symbol: str, interval: Any) -> None:
        life = self._lifecycle
        if life is None:
            try:
                life = get_lifecycle_service()
                self._lifecycle = life
            except Exception:
                return
        if life is None or not hasattr(life, "start"):
            return
        try:
            life.start(
                strategy_id, symbol,
                trigger_path=bp.TriggerPath.SCOUT_INCUBATOR.value,
                execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
                timeframe=str(interval),
                initiator="paper_validation",
            )
        except Exception as exc:
            logger.warning("lifecycle start %s failed: %s", strategy_id, exc)

    def _flatten_cohort(self, ids: List[str]) -> None:
        if self._flatten is not None:
            try:
                self._flatten(ids)
            except Exception as exc:
                logger.warning("flatten cohort failed: %s", exc)
            return
        try:
            # AppState is created after this module; import is the documented seam.
            from app.server.main import state
            paper = getattr(state, "paper", None)
        except Exception:
            paper = None
        if paper is None or not hasattr(paper, "cancel_all"):
            return
        for sid in ids:
            try:
                paper.cancel_all(sid)
            except Exception as exc:
                logger.warning("flatten %s failed: %s", sid, exc)

    def _ingest_loop_e(self, strategy_id: str, stats: Dict[str, Any]) -> None:
        if self._loop_e is None or not hasattr(self._loop_e, "ingest_result"):
            return
        try:
            self._loop_e.ingest_result(
                strategy_id, "BTC/USD", 15, "ranging_chop",
                float(stats.get("net_pnl_pct") or 0.0),
            )
        except Exception as exc:
            logger.warning("loop-e ingest %s failed: %s", strategy_id, exc)


_rotator: Optional[PaperValidationRotator] = None


def get_paper_validation_rotator(**kwargs) -> PaperValidationRotator:
    global _rotator
    if _rotator is None:
        _rotator = PaperValidationRotator(**kwargs)
    return _rotator


def set_paper_validation_rotator(rotator: Optional[PaperValidationRotator]) -> None:
    global _rotator
    _rotator = rotator
