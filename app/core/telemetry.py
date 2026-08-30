"""
=========================================================
Datei:      app/core/telemetry.py
Zweck:      System-State-Machine (M-00), Resource Guard (M-11),
            Storage-Tiering (M-01), Watchdog (M-17)
Knoten:     Noir (Diablo-Judge) / Core
=========================================================
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("app.core.telemetry")

VALID_STATES = ("SHADOW_ACTIVE", "LIVE_APPROVED", "EMERGENCY_HALT")
VALID_BREAKERS = ("NORMAL", "TRIPPED", "HALTED")
# Parquet file counts change on compact/seed, not every SSE tick (2s).
_L2_STATS_TTL_S = 5.0


@dataclass
class SystemState:
    state: str = "SHADOW_ACTIVE"
    circuit_breaker: str = "NORMAL"
    active_path: str = "FAST_PATH_RL"
    can_execute_orders: bool = True
    last_trip_reason: Optional[str] = None
    state_changed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "circuit_breaker": self.circuit_breaker,
            "active_path": self.active_path,
            "can_execute_orders": self.can_execute_orders,
            "last_trip_reason": self.last_trip_reason,
            "state_changed_at": self.state_changed_at,
        }


class TelemetryCenter:
    """Holds global M-00 state machine + resource/storage telemetry for SSE."""

    def __init__(self):
        self.system = SystemState()
        self._last_heartbeat = time.time()
        self._lock = threading.Lock()
        self.dropped_events = 0
        self.events_processed = 0
        self.active_threads = 4
        self.l1_ringbuffer_bytes = 0
        self.l1_capacity_bytes = 32 * 1024 * 1024
        self.l3_rclone_sync_status = "DISABLED"
        self.ingestion_rate_events_per_sec = 0.0
        self.avg_latency_microseconds = 0.0
        # (time.monotonic(), files, mb) — shared across SSE clients
        self._l2_stats_cache: Optional[tuple[float, int, float]] = None

    # ------------------------------------------------------------- M-00 state
    def set_state(self, new_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        if new_state not in VALID_STATES:
            raise ValueError(f"Unknown system state '{new_state}'. Valid: {VALID_STATES}")
        with self._lock:
            self.system.state = new_state
            self.system.state_changed_at = time.time()
            if new_state == "EMERGENCY_HALT":
                self.system.circuit_breaker = "TRIPPED"
                self.system.can_execute_orders = False
                self.system.last_trip_reason = reason or "Manual EMERGENCY_HALT directive"
            elif new_state == "SHADOW_ACTIVE":
                self.system.circuit_breaker = "NORMAL"
                self.system.can_execute_orders = True
                self.system.last_trip_reason = None
            elif new_state == "LIVE_APPROVED":
                self.system.circuit_breaker = "NORMAL"
                self.system.can_execute_orders = True
            return self.system.to_dict()

    def trip_breaker(self, reason: str) -> None:
        with self._lock:
            self.system.circuit_breaker = "TRIPPED"
            self.system.last_trip_reason = reason
            self.system.can_execute_orders = False

    # -------------------------------------------------------------- M-17 beat
    def beat(self) -> None:
        self._last_heartbeat = time.time()
        self.events_processed += 1

    def build_frame(self, store=None, log_bus=None) -> Dict[str, Any]:
        mem = _mem_usage_percent()
        l2_files, l2_mb = self._l2_storage(store)
        return {
            "timestamp": time.time(),
            "state_machine": self.system.to_dict(),
            "resource_guard": {
                "cpu_percent": round(_cpu_percent(), 1),
                "memory_percent": round(mem, 1),
                "load_shedding_level": "NORMAL" if mem < 85 else "WARNING",
                "dropped_events": self.dropped_events,
                "active_threads": self.active_threads,
            },
            "storage_tiering": {
                "l1_shm_ringbuffer_bytes": int(self.l1_ringbuffer_bytes),
                "l1_capacity_bytes": int(self.l1_capacity_bytes),
                "l2_duckdb_parquet_files": l2_files,
                "l2_total_mb": l2_mb,
                "l3_rclone_sync_status": self.l3_rclone_sync_status,
                "ingestion_rate_events_per_sec": round(self.ingestion_rate_events_per_sec, 1),
                "avg_latency_microseconds": round(self.avg_latency_microseconds, 1),
            },
            "watchdog": {
                "watchdog_running": True,
                "heartbeat_healthy": (time.time() - self._last_heartbeat) < 10.0,
                "seconds_since_last_heartbeat": round(time.time() - self._last_heartbeat, 2),
                "circuit_breaker": self.system.circuit_breaker,
            },
            "recent_logs": (log_bus.recent_logs_list(25) if log_bus else []),
        }

    def _l2_storage(self, store) -> tuple[int, float]:
        """Parquet file count/MB for SSE — skip lake_summary() OHLCV scans.

        GET /api/quant/telemetry/stream calls build_frame every sse_interval
        (2s). The frame only uses total_files / total_size_mb, but
        lake_summary() also COUNT(*) + GROUP BY ohlcv and holds DuckDBStore._lock
        — stalling the 1m evaluate path. Two lake_summary() calls per frame
        @ 80k 1m bars: ~9.1 ms vs parquet walk ~0.13 ms (~70×). 5s TTL so
        multiple SSE clients share one walk.
        """
        now = time.monotonic()
        cached = self._l2_stats_cache
        if cached is not None and (now - cached[0]) < _L2_STATS_TTL_S:
            return cached[1], cached[2]
        files, mb = _read_l2_storage(store)
        self._l2_stats_cache = (now, files, mb)
        return files, mb


def _cpu_percent() -> float:
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = list(map(int, parts[1:8]))
        idle = vals[3]
        total = sum(vals)
        if not hasattr(_cpu_percent, "_prev"):  # type: ignore[attr-defined]
            _cpu_percent._prev = (idle, total)  # type: ignore[attr-defined]
        prev_idle, prev_total = _cpu_percent._prev  # type: ignore[attr-defined]
        _cpu_percent._prev = (idle, total)  # type: ignore[attr-defined]
        dt = total - prev_total
        return max(0.0, min(100.0, (1 - (idle - prev_idle) / dt) * 100)) if dt > 0 else 5.0
    except Exception:
        return 12.0


def _mem_usage_percent() -> float:
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])
        return round(100.0 * (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"], 1)
    except Exception:
        return 38.0


def _read_l2_storage(store) -> tuple[int, float]:
    if store is None:
        return 0, 0.0
    try:
        stats = getattr(store, "parquet_file_stats", None)
        if callable(stats):
            files, mb = stats()
            return int(files), float(mb)
        summary = store.lake_summary()
        return int(summary.get("total_files") or 0), float(summary.get("total_size_mb") or 0.0)
    except Exception:
        return 0, 0.0


_center: Optional[TelemetryCenter] = None


def get_telemetry_center() -> TelemetryCenter:
    global _center
    if _center is None:
        _center = TelemetryCenter()
    return _center
