"""
=========================================================
Datei:      app/core/memory_watchdog.py
Zweck:      §21 / Masterprompt Loop E — 4-Stufen RAM-Guard.
            75 % GC · 85 % DuckDB-Checkpoint · 92 % Chromium-Zombie-Reaper ·
            96 % Emergency-Halt. Idle-only: greift nie mitten im Trade.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Noir (Resilienz)
=========================================================
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.core.memory_watchdog")


def read_cgroup_memory() -> Optional[Dict[str, float]]:
    """Read cgroup-v2 usage for this service when a finite limit is active."""
    try:
        cgroup_dir = "/sys/fs/cgroup"
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            for line in fh:
                hierarchy, controllers, relative = line.strip().split(":", 2)
                if hierarchy == "0" and controllers == "":
                    candidate = os.path.realpath(
                        os.path.join(cgroup_dir, relative.lstrip("/"))
                    )
                    if candidate == cgroup_dir or candidate.startswith(cgroup_dir + os.sep):
                        cgroup_dir = candidate
                    break
        with open(os.path.join(cgroup_dir, "memory.current"), encoding="utf-8") as fh:
            current = float(fh.read().strip())
        with open(os.path.join(cgroup_dir, "memory.max"), encoding="utf-8") as fh:
            raw_max = fh.read().strip()
        if raw_max == "max":
            return None
        maximum = float(raw_max)
        if maximum <= 0:
            return None
        return {
            "current_bytes": current,
            "max_bytes": maximum,
            "percent": current / maximum * 100.0,
        }
    except (OSError, TypeError, ValueError):
        return None


def read_memory_percent() -> float:
    """RAM-Auslastung — finite cgroup first, host RAM as fallback."""
    cgroup = read_cgroup_memory()
    if cgroup is not None:
        return cgroup["percent"]
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            info = {}
            for line in fh:
                key, _, rest = line.partition(":")
                info[key.strip()] = float(rest.strip().split()[0])
        total = info.get("MemTotal", 0.0)
        available = info.get("MemAvailable", info.get("MemFree", 0.0))
        if total > 0:
            return (total - available) / total * 100.0
    except Exception:
        pass
    try:
        import psutil  # type: ignore

        return float(psutil.virtual_memory().percent)
    except Exception:
        return 0.0


@dataclass
class WatchdogEvent:
    stage: int
    action: str
    percent: float
    ts: float = field(default_factory=time.time)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class MemoryWatchdog:
    """Eskaliert erst, wenn das System idle ist — Trades haben Vorrang."""

    def __init__(self, store=None, idle_provider: Optional[Callable[[], bool]] = None,
                 telemetry=None, safety_guard=None,
                 stages: Optional[List[float]] = None,
                 worker_restart: Optional[Callable[[], Any]] = None,
                 action_cooldown_s: float = 300.0):
        self.store = store
        self.telemetry = telemetry
        self.safety = safety_guard
        self.idle_provider = idle_provider or (lambda: True)
        self.worker_restart = worker_restart
        self.action_cooldown_s = action_cooldown_s
        self.stages = list(stages or bp.MEMORY_STAGES_PCT)
        self.actions = list(bp.MEMORY_STAGE_ACTIONS)
        self.history: List[WatchdogEvent] = []
        self.last_percent = 0.0
        self.last_stage = 0
        self.chromium_zombies_reaped = 0
        self._last_action_ts = 0.0
        self._last_executed_stage = 0
        self._stage4_latched = False
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------- decision
    def stage_for(self, percent: float) -> int:
        return bp.memory_stage(percent)

    def check(self, percent: Optional[float] = None, *, force: bool = False) -> Dict[str, Any]:
        pct = percent if percent is not None else read_memory_percent()
        self.last_percent = pct
        stage = self.stage_for(pct)
        self.last_stage = stage
        if stage == 0:
            self._last_executed_stage = 0
            self._stage4_latched = False
            return {"stage": 0, "percent": round(pct, 2), "action": "none", "executed": False}
        idle = bool(self.idle_provider())
        if bp.MEMORY_IDLE_ONLY and not idle and not force and stage < 4:
            return {"stage": stage, "percent": round(pct, 2),
                    "action": self.actions[stage - 1], "executed": False, "reason": "busy"}
        now = time.monotonic()
        if stage >= 4 and self._stage4_latched and not force:
            return {"stage": stage, "percent": round(pct, 2),
                    "action": self.actions[stage - 1], "executed": False,
                    "reason": "stage4_latched"}
        if (stage <= self._last_executed_stage
                and now - self._last_action_ts < self.action_cooldown_s
                and not force):
            return {"stage": stage, "percent": round(pct, 2),
                    "action": self.actions[stage - 1], "executed": False,
                    "reason": "cooldown"}
        detail = self._execute(stage, pct)
        self._last_action_ts = now
        self._last_executed_stage = stage
        if stage >= 4:
            self._stage4_latched = True
        event = WatchdogEvent(stage, self.actions[stage - 1], pct, detail=detail)
        self.history.append(event)
        self.history = self.history[-50:]
        return {**event.to_dict(), "executed": True, "percent": round(pct, 2)}

    # -------------------------------------------------------------- actions
    def _execute(self, stage: int, pct: float) -> str:
        action = self.actions[stage - 1]
        logger.warning("memory %.1f%% -> stage %d (%s)", pct, stage, action)
        if stage >= 1:
            collected = gc.collect()
            detail = f"gc collected {collected}"
        if stage >= 2:
            detail = f"{detail}; {self._duckdb_checkpoint()}"
        if stage >= 3:
            detail = f"{detail}; {self._reap_chromium()}"
        if stage >= 4:
            detail = f"{detail}; {self._emergency_halt()}"
        return detail

    def _duckdb_checkpoint(self) -> str:
        if self.store is None:
            return "no store"
        try:
            checkpoint = getattr(self.store, "checkpoint", None)
            if checkpoint is not None:
                checkpoint()
                return "duckdb checkpoint ok"
            conn = getattr(self.store, "conn", None) or getattr(self.store, "_conn", None)
            if conn is not None:
                conn.execute("CHECKPOINT")
                return "duckdb checkpoint ok"
        except Exception as exc:
            return f"duckdb checkpoint failed: {exc}"
        return "duckdb checkpoint skipped"

    def _reap_chromium(self) -> str:
        """Verwaiste Playwright/Chromium-Prozesse einsammeln."""
        try:
            out = subprocess.run(["pgrep", "-f", "chrome.*--headless"],
                                 capture_output=True, text=True, timeout=10)
            pids = [p for p in out.stdout.split() if p.isdigit()]
            reaped = 0
            for pid in pids:
                try:
                    os.kill(int(pid), 15)
                    reaped += 1
                except (ProcessLookupError, PermissionError):
                    continue
            self.chromium_zombies_reaped += reaped
            return f"reaped {reaped} chromium pids"
        except Exception as exc:
            return f"reaper unavailable: {exc}"

    def _emergency_halt(self) -> str:
        msgs = []
        if self.telemetry is not None:
            try:
                self.telemetry.set_state("EMERGENCY_HALT", reason="memory_watchdog_stage_4")
                msgs.append("telemetry EMERGENCY_HALT")
            except Exception as exc:  # pragma: no cover
                msgs.append(f"telemetry failed: {exc}")
        if self.safety is not None:
            try:
                self.safety.engage_pause("memory_watchdog_stage_4")
                msgs.append("PAUSE engaged")
            except Exception as exc:  # pragma: no cover
                msgs.append(f"pause failed: {exc}")
        if self.worker_restart is not None:
            try:
                result = self.worker_restart()
                msgs.append(str(result or "worker restarted"))
            except Exception as exc:  # pragma: no cover
                msgs.append(f"worker restart failed: {exc}")
        return "; ".join(msgs) or "halt requested"

    # ------------------------------------------------------------ lifecycle
    async def run(self, interval_seconds: int = 30) -> None:  # pragma: no cover
        logger.info("memory watchdog online (stages %s, cgroup max %s)",
                    self.stages, bp.MEMORY_CGROUP_MAX)
        while not self._stop.is_set():
            try:
                self.check()
            except Exception as exc:
                logger.error("watchdog check failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    def start(self, **kwargs) -> None:  # pragma: no cover
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self.run(**kwargs))

    async def stop(self) -> None:  # pragma: no cover
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    def snapshot(self) -> Dict[str, Any]:
        cgroup = read_cgroup_memory()
        return {
            "percent": round(self.last_percent or read_memory_percent(), 2),
            "stage": self.last_stage,
            "stages_pct": self.stages,
            "actions": self.actions,
            "cgroup_memory_max": bp.MEMORY_CGROUP_MAX,
            "cgroup": cgroup,
            "idle_only": bp.MEMORY_IDLE_ONLY,
            "chromium_zombies_reaped": self.chromium_zombies_reaped,
            "history": [e.to_dict() for e in self.history[-10:]],
        }


_watchdog: Optional[MemoryWatchdog] = None


def get_memory_watchdog(**kwargs) -> MemoryWatchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = MemoryWatchdog(**kwargs)
    return _watchdog


def set_memory_watchdog(watchdog: Optional[MemoryWatchdog]) -> None:
    global _watchdog
    _watchdog = watchdog
