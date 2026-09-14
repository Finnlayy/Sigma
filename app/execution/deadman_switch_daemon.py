"""
=========================================================
Datei:      app/execution/deadman_switch_daemon.py
Zweck:      §20 / Masterprompt §4.C — Heartbeat-Wächter.
            Timeout 1800s (30 min ohne Kraken-Ping): offene Entry-Limits canceln, wenn ein natives
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


class TestDeadmanBridge:
    """pytest-only stand-in — never issues cancel/flatten against Kraken."""

    live_enabled = False

    def cancel_open_limit_orders(self, reason: str = "deadman"):
        logger.info("test deadman ignored cancel_open_limit_orders (%s)", reason)
        return {"ok": True, "mode": "test", "reason": reason}

    def close_all_market(self, reason: str = "deadman_no_native_stop"):
        logger.info("test deadman ignored close_all_market (%s)", reason)
        return {"ok": True, "mode": "test", "reason": reason}


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
        self._flatten_latched = False
        self.state.last_beat = self._now()

    def _now(self) -> float:
        from app.core.exchange_clock import get_exchange_clock

        return get_exchange_clock().now()

    # ------------------------------------------------------------- heartbeat
    def beat(self, *, has_native_stop_loss: Optional[bool] = None,
             rearm: bool = False) -> None:
        """Core-Puls. Wird vom Tier-1-Scheduler alle 15–20s gesetzt — nicht vom UI.

        Auto-pulse refreshes last_beat (liveness) but does not clear a flatten
        latch. Operator ``POST /deadman/beat`` passes ``rearm=True``.
        """
        self.state.last_beat = self._now()
        self.state.triggered = False
        if rearm:
            self._flatten_latched = False
        if has_native_stop_loss is not None:
            self.state.has_native_stop_loss = bool(has_native_stop_loss)

    @property
    def age_seconds(self) -> float:
        return self._now() - self.state.last_beat

    @property
    def expired(self) -> bool:
        return self.state.armed and self.age_seconds > self.timeout_seconds

    # ---------------------------------------------------------------- action
    def evaluate(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Pure Entscheidung — testbar ohne Loop."""
        now = now if now is not None else self._now()
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
        if self._flatten_latched:
            decision["latched"] = True
            decision["action"] = self.state.last_action or decision["action"]
            return decision
        self._flatten_latched = True
        self.state.triggered = True
        self.state.trigger_count += 1
        self.state.last_action = decision["action"]
        logger.critical("DEADMAN triggered after %.1fs -> %s", decision["age_s"], decision["action"])
        try:
            from app.core.error_engine import (KrakenDeadmanTimeoutException,
                                               get_error_engine)

            get_error_engine().record(
                KrakenDeadmanTimeoutException(
                    f"Kraken heartbeat timeout after {decision['age_s']:.1f}s",
                    context={"action": decision["action"]},
                ),
                subsystem="deadman_switch",
            )
        except Exception as exc:  # pragma: no cover - Safety action has priority
            logger.warning("deadman diagnostic record failed: %s", exc)
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
            if self.expired:
                self.trigger()
            else:
                self._flatten_latched = False
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
        rtt = None
        kraken_ok = False
        try:
            from app.core.exchange_clock import get_exchange_clock

            clock = get_exchange_clock()
            rtt = clock.last_rtt_ms
            kraken_ok = bool(clock.synced) and clock.status().last_error is None and rtt is not None
        except Exception:
            pass
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
            "auto_pulse": True,
            "pulse_source": "kraken:/0/public/Time",
            "kraken_rtt_ms": None if rtt is None else round(float(rtt), 1),
            "kraken_ok": kraken_ok,
            "bridge_wired": self.bridge is not None,
            "safety_wired": self.safety is not None,
            "flatten_latched": self._flatten_latched,
        }


def pulse_deadman_from_kraken(deadman: Optional[DeadmanSwitchDaemon] = None,
                              clock=None) -> Dict[str, Any]:
    """Erneuert den Deadman nur bei erfolgreichem Kraken-Time-Ping (Netz/CLI-Stream)."""
    from app.core.exchange_clock import get_exchange_clock

    dm = deadman if deadman is not None else get_deadman()
    clk = clock if clock is not None else get_exchange_clock()
    result = clk.ping()
    if result.get("ok"):
        dm.beat()
        result["beat"] = True
    else:
        result["beat"] = False
    result["age_s"] = round(dm.age_seconds, 1)
    result["expired"] = dm.expired
    return result


_daemon: Optional[DeadmanSwitchDaemon] = None


def get_deadman(**kwargs) -> DeadmanSwitchDaemon:
    global _daemon
    if _daemon is None:
        _daemon = DeadmanSwitchDaemon(**kwargs)
    return _daemon


def set_deadman(deadman: Optional[DeadmanSwitchDaemon]) -> None:
    global _daemon
    _daemon = deadman
