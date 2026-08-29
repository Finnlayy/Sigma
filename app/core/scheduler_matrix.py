"""
=========================================================
Datei:      app/core/scheduler_matrix.py
Zweck:      §23.2 / Axiom 5 — Tier 0-5 Cadence-Matrix auf Kraken-Zeit
Knoten:     Ciel (Sigma Core)
=========================================================

Kein globales Dauer-Polling: schwere Operationen laufen Just-in-Time
(Tier 0), alles andere in festen Cadences. Der Scheduler ist bewusst
*pull-basiert* — ``due_tasks(now)`` liefert faellige Jobs, ``run_due()``
fuehrt sie aus. Damit ist er ohne Event-Loop testbar und laesst sich
sowohl in ein asyncio-Loop als auch in einen Thread haengen.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import blueprint as bp
from app.core.exchange_clock import ExchangeClock, get_exchange_clock

logger = logging.getLogger("app.core.scheduler_matrix")

TaskFn = Callable[[], Any]

TIER_SPECS: Dict[int, bp.TierSpec] = {int(spec.tier): spec for spec in bp.SCHEDULER_MATRIX}


def _json_safe(value: Any) -> Any:
    """Starlette JSONResponse rejects NaN/Inf — emit JSON null instead."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _cron_next(cron: str, after: float) -> float:
    """Minimaler Cron-Support fuer die zwei kanonischen Ausdruecke (§23.2).

    Unterstuetzt ``M H * * *`` (taeglich) und ``M H * * D`` (woechentlich,
    D = 0..6, Sonntag = 0). Alles in UTC.
    """
    minute_s, hour_s, _dom, _mon, dow_s = cron.split()
    minute, hour = int(minute_s), int(hour_s)
    day_of_week = None if dow_s == "*" else int(dow_s)

    tm = time.gmtime(after)
    day_start = after - (tm.tm_hour * 3600 + tm.tm_min * 60 + tm.tm_sec)
    candidate = day_start + hour * 3600 + minute * 60
    for _ in range(8):
        if candidate > after:
            wd = time.gmtime(candidate).tm_wday  # Mo=0 .. So=6
            cron_wd = (wd + 1) % 7               # cron: So=0
            if day_of_week is None or cron_wd == day_of_week:
                return candidate
        candidate += 86400.0
    return candidate


@dataclass
class ScheduledTask:
    name: str
    tier: int
    fn: TaskFn
    cadence_s: Optional[float]
    cron: Optional[str]
    next_run: float
    last_run: Optional[float] = None
    last_duration_ms: Optional[float] = None
    runs: int = 0
    errors: int = 0
    last_error: Optional[str] = None
    enabled: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return _json_safe({
            "name": self.name,
            "tier": self.tier,
            "tier_label": TIER_SPECS[self.tier].label,
            "cadence_s": self.cadence_s,
            "cron": self.cron,
            "next_run": self.next_run,
            "last_run": self.last_run,
            "last_duration_ms": self.last_duration_ms,
            "runs": self.runs,
            "errors": self.errors,
            "last_error": self.last_error,
            "enabled": self.enabled,
        })


class SchedulerMatrix:
    """Tier 0-5 Scheduler; Zeitbasis ist immer die Kraken-Serverzeit."""

    def __init__(self, clock: Optional[ExchangeClock] = None) -> None:
        self._clock = clock or get_exchange_clock()
        self._tasks: Dict[str, ScheduledTask] = {}
        self._events: List[Dict[str, Any]] = []

    # -------------------------------------------------------- registry ---
    def register(
        self,
        name: str,
        tier: int | bp.SchedulerTier,
        fn: TaskFn,
        *,
        cadence_s: Optional[float] = None,
        cron: Optional[str] = None,
        start_immediately: bool = False,
    ) -> ScheduledTask:
        tier_int = int(tier)
        if tier_int not in TIER_SPECS:
            raise ValueError(f"unknown scheduler tier: {tier}")
        spec = TIER_SPECS[tier_int]
        cadence = cadence_s if cadence_s is not None else spec.cadence_s
        cron_expr = cron if cron is not None else spec.cron
        if tier_int == int(bp.SchedulerTier.T0_EVENT):
            if cadence is not None or cron_expr is not None:
                raise ValueError("Tier 0 ist event-driven — keine Cadence erlaubt")
        elif cadence is None and cron_expr is None:
            raise ValueError(f"tier {tier_int} braucht cadence_s oder cron")

        now = self._clock.now()
        if tier_int == int(bp.SchedulerTier.T0_EVENT):
            next_run = float("inf")
        elif cron_expr:
            next_run = _cron_next(cron_expr, now)
        else:
            next_run = now if start_immediately else now + float(cadence or 0.0)

        task = ScheduledTask(
            name=name, tier=tier_int, fn=fn, cadence_s=cadence,
            cron=cron_expr, next_run=next_run,
        )
        self._tasks[name] = task
        return task

    def unregister(self, name: str) -> None:
        self._tasks.pop(name, None)

    def get(self, name: str) -> Optional[ScheduledTask]:
        return self._tasks.get(name)

    @property
    def tasks(self) -> Tuple[ScheduledTask, ...]:
        return tuple(self._tasks.values())

    # ------------------------------------------------------ scheduling ---
    def due_tasks(self, now: Optional[float] = None) -> List[ScheduledTask]:
        ts = self._clock.now() if now is None else now
        due = [
            t for t in self._tasks.values()
            if t.enabled and t.tier != int(bp.SchedulerTier.T0_EVENT) and t.next_run <= ts
        ]
        return sorted(due, key=lambda t: (t.tier, t.next_run))

    def _reschedule(self, task: ScheduledTask, ran_at: float) -> None:
        if task.cron:
            task.next_run = _cron_next(task.cron, ran_at)
        elif task.cadence_s:
            nxt = task.next_run + task.cadence_s
            # Nach langem Stillstand nicht nachholen, sondern neu ausrichten.
            if nxt <= ran_at:
                nxt = ran_at + task.cadence_s
            task.next_run = nxt

    def _execute(self, task: ScheduledTask, ts: float) -> Dict[str, Any]:
        started = time.perf_counter()
        result: Dict[str, Any] = {"task": task.name, "tier": task.tier, "ts": ts}
        try:
            task.fn()
            result["status"] = "ok"
        except Exception as exc:
            task.errors += 1
            task.last_error = f"{type(exc).__name__}: {exc}"
            result["status"] = "error"
            result["error"] = task.last_error
            logger.warning("scheduler task %s failed: %s", task.name, task.last_error)
        finally:
            task.runs += 1
            task.last_run = ts
            task.last_duration_ms = (time.perf_counter() - started) * 1000.0
            result["duration_ms"] = task.last_duration_ms
        self._events.append(result)
        del self._events[:-200]
        return result

    def run_due(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        ts = self._clock.now() if now is None else now
        results = []
        for task in self.due_tasks(ts):
            results.append(self._execute(task, ts))
            self._reschedule(task, ts)
        return results

    def fire_event(self, name: str) -> Dict[str, Any]:
        """Tier 0 — Just-in-Time-Ausloesung (Signal, Kill-Switch, OB-Audit)."""
        task = self._tasks.get(name)
        if task is None:
            raise KeyError(f"unknown task: {name}")
        if task.tier != int(bp.SchedulerTier.T0_EVENT):
            raise ValueError(f"task {name} ist Tier {task.tier}, nicht event-driven")
        return self._execute(task, self._clock.now())

    # ------------------------------------------------------ telemetrie ---
    def telemetry(self) -> Dict[str, Any]:
        now = self._clock.now()
        tiers: List[Dict[str, Any]] = []
        for spec in bp.SCHEDULER_MATRIX:
            tier_tasks = [t for t in self._tasks.values() if t.tier == int(spec.tier)]
            tiers.append({
                "tier": int(spec.tier),
                "label": spec.label,
                "cadence_s": spec.cadence_s,
                "cron": spec.cron,
                "spec_tasks": list(spec.tasks),
                "registered": [t.as_dict() for t in tier_tasks],
            })
        return _json_safe({
            "timezone": bp.SCHEDULER_TIMEZONE,
            "now": now,
            "clock": self._clock.status().as_dict(),
            "tiers": tiers,
            "recent_events": self._events[-25:],
        })


_SCHEDULER: Optional[SchedulerMatrix] = None


def get_scheduler() -> SchedulerMatrix:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = SchedulerMatrix()
    return _SCHEDULER


def set_scheduler(scheduler: Optional[SchedulerMatrix]) -> None:
    global _SCHEDULER
    _SCHEDULER = scheduler


def install_canonical_tasks(
    scheduler: Optional[SchedulerMatrix] = None,
    *,
    deadman=None,
    memory=None,
    contagion=None,
    contagion_feed=None,
    flywheel=None,
    fill_reconciler=None,
    glint_event: Optional[TaskFn] = None,
    scorecard=None,
    scout=None,
    allocator=None,
    academy=None,
    scraper=None,
    lake=None,
    orchestrator=None,
    webhook_event: Optional[TaskFn] = None,
    playwright_event: Optional[TaskFn] = None,
) -> SchedulerMatrix:
    """Wire T0–T5 jobs so loops A–E are on the scheduler graph.

    Fehlende Sidecars/Services: no-op oder lazy Import (wie contagion/flywheel).
    Loop C sidecar down → degraded empty snapshot, kein Synthetic im Prod-Pfad.
    """
    sched = scheduler or get_scheduler()
    if sched.get("glint_orderbook_verify") is None:
        sched.register(
            "glint_orderbook_verify",
            bp.SchedulerTier.T0_EVENT,
            glint_event or (lambda: None),
        )
    if sched.get("webhook_execution") is None:
        sched.register(
            "webhook_execution",
            bp.SchedulerTier.T0_EVENT,
            webhook_event or (lambda: None),
        )
    if sched.get("playwright_compile") is None:
        sched.register(
            "playwright_compile",
            bp.SchedulerTier.T0_EVENT,
            playwright_event or (lambda: None),
        )
    if sched.get("deadman_heartbeat") is None:
        if deadman is None:
            from app.execution.deadman_switch_daemon import get_deadman

            deadman = get_deadman()
        from app.execution.deadman_switch_daemon import pulse_deadman_from_kraken

        dm = deadman

        def _kraken_pulse() -> None:
            pulse_deadman_from_kraken(deadman=dm)

        sched.register(
            "deadman_heartbeat",
            bp.SchedulerTier.T1_FAST_PULSE,
            _kraken_pulse,
            cadence_s=float(bp.DEADMAN_HEARTBEAT_SECONDS_MAX),
            start_immediately=True,
        )
        _kraken_pulse()
    if sched.get("memory_watchdog") is None:
        if memory is None:
            from app.core.memory_watchdog import get_memory_watchdog

            memory = get_memory_watchdog()
        sched.register(
            "memory_watchdog",
            bp.SchedulerTier.T1_FAST_PULSE,
            memory.check,
            cadence_s=float(bp.DEADMAN_HEARTBEAT_SECONDS_MAX),
            start_immediately=True,
        )
    if fill_reconciler is not None and sched.get("kraken_fill_reconcile") is None:
        sched.register(
            "kraken_fill_reconcile",
            bp.SchedulerTier.T1_FAST_PULSE,
            fill_reconciler.poll,
            cadence_s=float(bp.DEADMAN_HEARTBEAT_SECONDS_MAX),
            start_immediately=False,
        )
    if contagion is not None and contagion_feed is not None \
            and sched.get("macro_radar_scraper") is None:
        def _macro_radar() -> None:
            contagion.evaluate(contagion_feed.snapshot())

        sched.register(
            "macro_radar_scraper",
            bp.SchedulerTier.T2_MID,
            _macro_radar,
            start_immediately=False,
        )
    if sched.get("scorecard_stage1_idle") is None:
        card = scorecard
        if card is None:
            from app.optimizer.strategy_scorecard import get_strategy_scorecard

            card = get_strategy_scorecard()
        sched.register(
            "scorecard_stage1_idle",
            bp.SchedulerTier.T2_MID,
            card.idle_stage1_tick,
            cadence_s=float(bp.SCOUT_INCUBATOR_CYCLE_MINUTES) * 60.0,
            start_immediately=False,
        )
    if flywheel is not None and sched.get("flywheel_sweep") is None:
        sched.register(
            "flywheel_sweep",
            bp.SchedulerTier.T4_DAILY,
            flywheel.sweep,
        )
    if sched.get("loop_c_feed_poll") is None:
        def _loop_c_feed_poll() -> None:
            from sigma.loops.loop_c import LoopCPort

            LoopCPort(scraper=scraper, store=lake).poll_pair()

        sched.register(
            "loop_c_feed_poll",
            bp.SchedulerTier.T2_MID,
            _loop_c_feed_poll,
            start_immediately=False,
        )
    if sched.get("scout_incubator_cycle") is None:
        def _scout_incubator_cycle() -> None:
            from sigma.loops.loop_d import LoopDPort

            LoopDPort(daemon=scout).tick()

        sched.register(
            "scout_incubator_cycle",
            bp.SchedulerTier.T2_MID,
            _scout_incubator_cycle,
            cadence_s=float(bp.SCOUT_INCUBATOR_CYCLE_MINUTES) * 60.0,
            start_immediately=False,
        )
    if sched.get("master_orchestrator_tick") is None:
        orch = orchestrator

        def _master_orchestrator_tick() -> None:
            conductor = orch
            if conductor is None:
                from sigma.orchestration import MasterOrchestrator

                conductor = MasterOrchestrator()
            conductor.tick()

        sched.register(
            "master_orchestrator_tick",
            bp.SchedulerTier.T2_MID,
            _master_orchestrator_tick,
            start_immediately=False,
        )
    if sched.get("strategy_allocator") is None:
        def _strategy_allocator() -> None:
            from sigma.loops.loop_e import LoopEPort

            LoopEPort(allocator=allocator, academy=academy).allocate()

        sched.register(
            "strategy_allocator",
            bp.SchedulerTier.T3_REGIME,
            _strategy_allocator,
            start_immediately=False,
        )
    if sched.get("regime_recheck") is None:
        def _regime_recheck() -> None:
            from sigma.loops.loop_c import LoopCPort

            LoopCPort(scraper=scraper, store=lake).poll_pair()

        sched.register(
            "regime_recheck",
            bp.SchedulerTier.T3_REGIME,
            _regime_recheck,
            start_immediately=False,
        )
    if sched.get("academy_badge_recalibration") is None:
        def _academy_badge_recalibration() -> None:
            from sigma.loops.loop_e import LoopEPort

            LoopEPort(allocator=allocator, academy=academy).recalibrate_badges()

        sched.register(
            "academy_badge_recalibration",
            bp.SchedulerTier.T5_WEEKLY,
            _academy_badge_recalibration,
        )
    return sched
