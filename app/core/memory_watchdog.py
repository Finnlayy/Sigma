"""
=========================================================
Datei:      app/core/memory_watchdog.py
Zweck:      §21 / Masterprompt Loop E — 4-Stufen RAM-Guard.
            60 % GC+malloc_trim · 72 % DuckDB-Release · 85 % Chromium-Reaper ·
            92 % Emergency-Halt. Disruptive Stufen nur ohne laufenden TV-Job;
            GC/Checkpoint laufen immer — sonst OOM bei offenen Paper-Positionen.
            Messbasis: max(cgroup, Prozessbaum-RSS / 4G), nie Host-RAM.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Noir (Resilienz)
=========================================================
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.core import blueprint as bp

logger = logging.getLogger("app.core.memory_watchdog")

try:
    _PAGE_SIZE = int(os.sysconf("SC_PAGE_SIZE") or 4096)
except (OSError, ValueError, TypeError, AttributeError):
    _PAGE_SIZE = 4096

_HEADLESS_MARKERS = ("--headless", "chrome-headless-shell", "ms-playwright")
_BROWSER_TOKENS = ("chrome", "chromium", "headless_shell")
_SKIP_TOKENS = ("chrome_crashpad", "nacl_helper", "type=gpu-process", "type=utility")


def parse_memory_bytes(value: str) -> int:
    """Parse ``4G`` / ``2GB`` / ``256MB`` / raw integer bytes."""
    raw = str(value or "").strip().upper().replace(" ", "")
    if not raw or raw == "MAX":
        return 0
    suffixes = (
        ("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024),
        ("T", 1024 ** 4), ("G", 1024 ** 3), ("M", 1024 ** 2), ("K", 1024),
        ("B", 1),
    )
    for suffix, mul in suffixes:
        if raw.endswith(suffix):
            return int(float(raw[:-len(suffix)]) * mul)
    return int(float(raw))


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


def _rss_bytes(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/statm", encoding="utf-8") as fh:
            resident = int(fh.read().split()[1])
        return resident * _PAGE_SIZE
    except (OSError, IndexError, ValueError):
        return 0


def _child_map() -> Dict[int, List[int]]:
    by_ppid: Dict[int, List[int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return by_ppid
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            rparen = stat.rfind(")")
            ppid = int(stat[rparen + 2:].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        by_ppid.setdefault(ppid, []).append(pid)
    return by_ppid


def descendant_pids(root: Optional[int] = None) -> List[int]:
    root = int(root or os.getpid())
    children = _child_map()
    out: List[int] = []
    stack = [root]
    seen = {root}
    while stack:
        pid = stack.pop()
        out.append(pid)
        for child in children.get(pid, ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return out


def read_process_tree_rss_bytes(root: Optional[int] = None) -> int:
    """Resident set of this process plus every descendant (Chromium included)."""
    return sum(_rss_bytes(pid) for pid in descendant_pids(root))


def read_host_memory_percent() -> float:
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


def read_memory_snapshot() -> Dict[str, Any]:
    """Pressure against the 4G service budget, not against host RAM.

    Host-wide percent hid leaks on large machines: Sigma could grow to OOM
    while the watchdog still reported 40 %.
    """
    budget = parse_memory_bytes(bp.MEMORY_CGROUP_MAX)
    cgroup = read_cgroup_memory()
    rss = read_process_tree_rss_bytes()
    rss_pct = (rss / budget * 100.0) if budget > 0 else 0.0
    host_pct = read_host_memory_percent()
    candidates: List[Tuple[str, float]] = []
    if cgroup is not None:
        candidates.append(("cgroup", float(cgroup["percent"])))
    if rss_pct > 0:
        candidates.append(("rss", rss_pct))
    if not candidates:
        candidates.append(("host", host_pct))
    source, percent = max(candidates, key=lambda item: item[1])
    return {
        "percent": percent,
        "source": source,
        "rss_bytes": rss,
        "budget_bytes": budget,
        "cgroup": cgroup,
        "host_percent": host_pct,
        "rss_percent": rss_pct,
    }


def read_memory_percent() -> float:
    """RAM-Auslastung — cgroup oder Prozessbaum gegen MEMORY_CGROUP_MAX."""
    return float(read_memory_snapshot()["percent"])


def malloc_trim() -> str:
    """Give glibc arenas back to the OS. gc.collect() alone does not."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim.argtypes = [ctypes.c_size_t]
        libc.malloc_trim.restype = ctypes.c_int
        rc = int(libc.malloc_trim(0))
        return f"malloc_trim={rc}"
    except Exception as exc:
        return f"malloc_trim skipped: {exc}"


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _ppid(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat = fh.read()
        rparen = stat.rfind(")")
        return int(stat[rparen + 2:].split()[1])
    except (OSError, IndexError, ValueError):
        return 0


def is_headless_browser_cmd(cmdline: str) -> bool:
    low = (cmdline or "").lower()
    if not low or any(token in low for token in _SKIP_TOKENS):
        return False
    if not any(token in low for token in _BROWSER_TOKENS) and "ms-playwright" not in low:
        return False
    return any(marker in low for marker in _HEADLESS_MARKERS)


def headless_browser_pids(*, orphans_only: bool = False) -> List[int]:
    """Playwright/Chromium headless PIDs. ``orphans_only`` keeps a live TV job alive."""
    pids: List[int] = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return pids
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == os.getpid():
            continue
        if not is_headless_browser_cmd(_cmdline(pid)):
            continue
        if orphans_only and _ppid(pid) != 1:
            continue
        pids.append(pid)
    return pids


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
    """Eskaliert GC immer; Chromium-Kill und Worker-Restart nur wenn idle."""

    def __init__(self, store=None, idle_provider: Optional[Callable[[], bool]] = None,
                 telemetry=None, safety_guard=None,
                 stages: Optional[List[float]] = None,
                 worker_restart: Optional[Callable[[], Any]] = None,
                 action_cooldown_s: Optional[float] = None,
                 pressure_hook: Optional[Callable[[int], Any]] = None,
                 stage_cooldowns: Optional[Iterable[float]] = None):
        self.store = store
        self.telemetry = telemetry
        self.safety = safety_guard
        self.idle_provider = idle_provider or (lambda: True)
        self.worker_restart = worker_restart
        self.pressure_hook = pressure_hook
        self.action_cooldown_s = (
            float(action_cooldown_s)
            if action_cooldown_s is not None
            else float(bp.MEMORY_STAGE_COOLDOWN_S[0])
        )
        self.stages = list(stages or bp.MEMORY_STAGES_PCT)
        self.actions = list(bp.MEMORY_STAGE_ACTIONS)
        self.stage_cooldowns = [
            float(v) for v in (stage_cooldowns or bp.MEMORY_STAGE_COOLDOWN_S)
        ]
        self.history: List[WatchdogEvent] = []
        self.last_percent = 0.0
        self.last_stage = 0
        self.last_source = "rss"
        self.chromium_zombies_reaped = 0
        self._last_action_ts = 0.0
        self._last_executed_stage = 0
        self._last_housekeep_ts = 0.0
        self._stage4_latched = False
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def _cooldown_for(self, stage: int) -> float:
        if stage <= 0:
            return self.action_cooldown_s
        idx = min(stage, len(self.stage_cooldowns)) - 1
        if 0 <= idx < len(self.stage_cooldowns):
            return self.stage_cooldowns[idx]
        return self.action_cooldown_s

    # ------------------------------------------------------------- decision
    def stage_for(self, percent: float) -> int:
        return bp.memory_stage(percent)

    def check(self, percent: Optional[float] = None, *, force: bool = False) -> Dict[str, Any]:
        snap = None if percent is not None else read_memory_snapshot()
        pct = percent if percent is not None else float(snap["percent"])
        self.last_percent = pct
        if snap is not None:
            self.last_source = str(snap.get("source") or "rss")
        stage = self.stage_for(pct)
        self.last_stage = stage
        now = time.monotonic()
        if stage == 0:
            self._last_executed_stage = 0
            self._stage4_latched = False
            housekeep = self._maybe_housekeep(now, force=force)
            return {
                "stage": 0, "percent": round(pct, 2), "action": "none",
                "executed": bool(housekeep), "housekeep": housekeep,
                "source": self.last_source,
            }
        idle = bool(self.idle_provider())
        idle_min = int(bp.MEMORY_IDLE_MIN_STAGE)
        if (bp.MEMORY_IDLE_ONLY and stage >= idle_min and not idle
                and not force and stage < 4):
            safe_stage = max(1, idle_min - 1)
            if (safe_stage <= self._last_executed_stage
                    and now - self._last_action_ts < self._cooldown_for(safe_stage)
                    and not force):
                return {
                    "stage": stage, "percent": round(pct, 2),
                    "action": self.actions[stage - 1], "executed": False,
                    "reason": "busy", "source": self.last_source,
                }
            detail = self._execute(safe_stage, pct)
            self._last_action_ts = now
            self._last_executed_stage = max(self._last_executed_stage, safe_stage)
            event = WatchdogEvent(stage, self.actions[safe_stage - 1], pct, detail=detail)
            self.history.append(event)
            self.history = self.history[-50:]
            return {
                **event.to_dict(), "executed": True, "percent": round(pct, 2),
                "reason": "busy_partial", "source": self.last_source,
            }
        if stage >= 4 and self._stage4_latched and not force:
            return {
                "stage": stage, "percent": round(pct, 2),
                "action": self.actions[stage - 1], "executed": False,
                "reason": "stage4_latched", "source": self.last_source,
            }
        if (stage <= self._last_executed_stage
                and now - self._last_action_ts < self._cooldown_for(stage)
                and not force):
            return {
                "stage": stage, "percent": round(pct, 2),
                "action": self.actions[stage - 1], "executed": False,
                "reason": "cooldown", "source": self.last_source,
            }
        detail = self._execute(stage, pct)
        self._last_action_ts = now
        self._last_executed_stage = stage
        if stage >= 4:
            self._stage4_latched = True
        event = WatchdogEvent(stage, self.actions[stage - 1], pct, detail=detail)
        self.history.append(event)
        self.history = self.history[-50:]
        return {**event.to_dict(), "executed": True, "percent": round(pct, 2),
                "source": self.last_source}

    def _maybe_housekeep(self, now: float, *, force: bool = False) -> str:
        if not force and now - self._last_housekeep_ts < float(bp.MEMORY_HOUSEKEEP_S):
            return ""
        self._last_housekeep_ts = now
        collected = gc.collect()
        trim = malloc_trim()
        detail = f"housekeep gc={collected}; {trim}"
        logger.info("memory housekeep (%.1f%%): %s", self.last_percent, detail)
        return detail

    # -------------------------------------------------------------- actions
    def _execute(self, stage: int, pct: float) -> str:
        action = self.actions[min(stage, len(self.actions)) - 1]
        logger.warning("memory %.1f%% -> stage %d (%s)", pct, stage, action)
        detail = ""
        if stage >= 1:
            collected = gc.collect()
            detail = f"gc collected {collected}; {malloc_trim()}"
        if stage >= 2:
            detail = f"{detail}; {self._duckdb_checkpoint()}"
            detail = f"{detail}; {self._reap_chromium(orphans_only=True)}"
            detail = f"{detail}; {self._run_pressure_hook(stage)}"
        if stage >= 3:
            detail = f"{detail}; {self._reap_chromium(orphans_only=False)}"
        if stage >= 4:
            detail = f"{detail}; {self._emergency_halt()}"
        return detail

    def _run_pressure_hook(self, stage: int) -> str:
        if self.pressure_hook is None:
            return "no pressure hook"
        try:
            result = self.pressure_hook(stage)
            return str(result or "pressure hook ok")
        except Exception as exc:
            return f"pressure hook failed: {exc}"

    def _duckdb_checkpoint(self) -> str:
        if self.store is None:
            return "no store"
        try:
            release = getattr(self.store, "release_memory", None)
            if release is not None:
                return str(release() or "duckdb release ok")
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

    def _reap_chromium(self, orphans_only: bool = False) -> str:
        """Verwaiste Playwright/Chromium-Prozesse einsammeln."""
        try:
            pids = headless_browser_pids(orphans_only=orphans_only)
            reaped = 0
            remaining: List[int] = []
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    remaining.append(pid)
                    reaped += 1
                except (ProcessLookupError, PermissionError):
                    continue
            if remaining:
                time.sleep(0.15)
                for pid in remaining:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        continue
            self.chromium_zombies_reaped += reaped
            kind = "orphan" if orphans_only else "headless"
            return f"reaped {reaped} {kind} chromium pids"
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
    async def run(self, interval_seconds: int = 20) -> None:  # pragma: no cover
        logger.info("memory watchdog online (stages %s, cgroup max %s, idle>=%s)",
                    self.stages, bp.MEMORY_CGROUP_MAX, bp.MEMORY_IDLE_MIN_STAGE)
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
        live = read_memory_snapshot()
        percent = self.last_percent or float(live["percent"])
        return {
            "percent": round(percent, 2),
            "stage": self.last_stage,
            "source": self.last_source or live.get("source"),
            "rss_bytes": int(live.get("rss_bytes") or 0),
            "budget_bytes": int(live.get("budget_bytes") or 0),
            "host_percent": round(float(live.get("host_percent") or 0.0), 2),
            "rss_percent": round(float(live.get("rss_percent") or 0.0), 2),
            "stages_pct": self.stages,
            "actions": self.actions,
            "cgroup_memory_max": bp.MEMORY_CGROUP_MAX,
            "cgroup": live.get("cgroup"),
            "idle_only": bp.MEMORY_IDLE_ONLY,
            "idle_min_stage": bp.MEMORY_IDLE_MIN_STAGE,
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
