"""
=========================================================
Datei:      app/execution/deadman_switch_daemon.py
Zweck:      §20 / Masterprompt §4.C — Heartbeat-Wächter.
            Timeout 60s: offene Entry-Limits canceln, wenn ein natives
            Börsen-Stop-Loss existiert; sonst close_all_market.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Noir (Diablo) / Safety
=========================================================
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.execution.deadman")


@dataclass
class DeadmanState:
    last_beat: float = field(default_factory=time.time)
    armed: bool = True
    triggered: bool = False
    trigger_count: int = 0
    last_action: str = ""
    has_native_stop_loss: bool = True


class DeadmanSwitchDaemon:
    """Verliert der Core den Puls, räumt die Börse auf — nicht andersherum."""

    def __init__(self, kraken_bridge=None, safety_guard=None,
                 timeout_seconds: int = bp.DEADMAN_TIMEOUT_SECONDS,
                 heartbeat_seconds: int = bp.DEADMAN_HEARTBEAT_SECONDS_MAX):
        self.bridge = kraken_bridge
        self.safety = safety_guard
        self.timeout_seconds = timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.state = DeadmanState()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------- heartbeat
    def beat(self, *, has_native_stop_loss: Optional[bool] = None) -> None:
        self.state.last_beat = time.time()
        self.state.triggered = False
        if has_native_stop_loss is not None:
            self.state.has_native_stop_loss = bool(has_native_stop_loss)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.state.last_beat

    @property
    def expired(self) -> bool:
        return self.state.armed and self.age_seconds > self.timeout_seconds

    # ---------------------------------------------------------------- action
    def evaluate(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Pure Entscheidung — testbar ohne Loop."""
        now = now if now is not None else time.time()
        age = now - self.state.last_beat
        if not self.state.armed or age <= self.timeout_seconds:
            return {"action": "none", "age_s": round(age, 1), "expired": False}
        action = ("cancel_open_limit_orders" if self.state.has_native_stop_loss
                  else bp.DEADMAN_FALLBACK_ACTION)
        return {"action": action, "age_s": round(age, 1), "expired": True,
                "has_native_stop_loss": self.state.has_native_stop_loss}

    def trigger(self) -> Dict[str, Any]:
        decision = self.evaluate()
        if not decision["expired"]:
            return decision
        self.state.triggered = True
        self.state.trigger_count += 1
        self.state.last_action = decision["action"]
        logger.critical("DEADMAN triggered after %.1fs -> %s", decision["age_s"], decision["action"])
        if self.bridge is not None:
            try:
                if decision["action"] == "cancel_open_limit_orders":
                    result = self.bridge.cancel_open_limit_orders(reason="deadman_timeout")
                else:
                    result = self.bridge.close_all_market(reason="deadman_no_native_stop")
                decision["execution"] = result.to_dict() if hasattr(result, "to_dict") else str(result)
            except Exception as exc:  # pragma: no cover
                logger.error("deadman execution failed: %s", exc)
                decision["execution_error"] = str(exc)
        if self.safety is not None:
            try:
                self.safety.engage_pause("deadman_switch")
            except Exception:  # pragma: no cover
                pass
        return decision

    # ------------------------------------------------------------- lifecycle
    async def run(self) -> None:
        logger.info("Deadman daemon online (timeout %ss, heartbeat %ss)",
                    self.timeout_seconds, self.heartbeat_seconds)
        while not self._stop.is_set():
            if self.expired and not self.state.triggered:
                self.trigger()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.heartbeat_seconds / 2)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                self._task.cancel()
            self._task = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "armed": self.state.armed,
            "age_s": round(self.age_seconds, 1),
            "timeout_s": self.timeout_seconds,
            "heartbeat_s": self.heartbeat_seconds,
            "expired": self.expired,
            "triggered": self.state.triggered,
            "trigger_count": self.state.trigger_count,
            "last_action": self.state.last_action,
            "has_native_stop_loss": self.state.has_native_stop_loss,
        }


_daemon: Optional[DeadmanSwitchDaemon] = None


def get_deadman(**kwargs) -> DeadmanSwitchDaemon:
    global _daemon
    if _daemon is None:
        _daemon = DeadmanSwitchDaemon(**kwargs)
    return _daemon
