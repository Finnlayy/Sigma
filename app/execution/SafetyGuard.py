"""
=========================================================
Datei:      app/execution/SafetyGuard.py
Zweck:      L4 Safety-Gate (§4.2 Schritt 1-2, §4.4, §17.1/17.2)
            KILL_SWITCH / PAUSE / Daily-Loss / Consecutive-Errors /
            Symbol-Halt / Webhook-Secret / Stale-Signal.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Noir (Diablo) / Risk Gate
=========================================================
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.execution.safety")


class SafetyBlocked(Exception):
    """Signal darf nicht ausgeführt werden. `status_code` für die HTTP-Schicht."""

    def __init__(self, reason: str, code: str, status_code: int = bp.WEBHOOK_BLOCKED_STATUS):
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.status_code = status_code


@dataclass
class SafetyVerdict:
    allowed: bool
    code: str = "OK"
    reason: str = "clear"
    status_code: int = 200
    details: Dict[str, Any] = field(default_factory=dict)

    def raise_if_blocked(self) -> None:
        if not self.allowed:
            raise SafetyBlocked(self.reason, self.code, self.status_code)


class SafetyGuard:
    """Datei-Signale + Tageslimits. Hart, still, ohne Netzwerk."""

    def __init__(self, config: Optional[SigmaConfig] = None, redis_client=None, store=None):
        self.config = config or load_config()
        self.redis = redis_client
        self.store = store
        self._consecutive_errors = 0
        self._daily_pnl_usd = 0.0
        self._daily_key = _today()
        self._pnl_refs: set[str] = set()

    # ----------------------------------------------------------- file signals
    @property
    def kill_switch_active(self) -> bool:
        return os.path.exists(self.config.kill_switch_file)

    @property
    def pause_active(self) -> bool:
        return os.path.exists(self.config.pause_signal_file)

    def engage_kill_switch(self, reason: str = "operator") -> None:
        _touch(self.config.kill_switch_file, reason)
        logger.critical("KILL_SWITCH engaged: %s", reason)

    def release_kill_switch(self) -> None:
        _remove(self.config.kill_switch_file)

    def engage_pause(self, reason: str = "operator") -> None:
        _touch(self.config.pause_signal_file, reason)

    def release_pause(self) -> None:
        _remove(self.config.pause_signal_file)

    # --------------------------------------------------------------- counters
    def record_error(self) -> int:
        self._consecutive_errors += 1
        return self._consecutive_errors

    def record_success(self) -> None:
        self._consecutive_errors = 0

    def record_pnl(self, pnl_usd: float, *, reference_id: str = "") -> float:
        self._roll_day()
        if reference_id and reference_id in self._pnl_refs:
            return self._daily_pnl_usd
        self._daily_pnl_usd += float(pnl_usd)
        if reference_id:
            self._pnl_refs.add(reference_id)
        return self._daily_pnl_usd

    @property
    def daily_pnl_usd(self) -> float:
        self._roll_day()
        return self._daily_pnl_usd

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    def _roll_day(self) -> None:
        today = _today()
        if today != self._daily_key:
            self._daily_key = today
            self._daily_pnl_usd = 0.0
            self._consecutive_errors = 0
            self._pnl_refs.clear()

    # ------------------------------------------------------------ §17.1 auth
    def verify_webhook_secret(self, provided: Optional[str]) -> SafetyVerdict:
        expected = self.config.webhook_secret
        if not expected:
            if getattr(self.config, "live_trading", False):
                return SafetyVerdict(
                    False, "UNAUTHORIZED", "webhook secret required in live trading",
                    bp.WEBHOOK_UNAUTHORIZED_STATUS)
            logger.warning("%s not set — webhook auth disabled (dev only)", bp.WEBHOOK_SECRET_ENV)
            return SafetyVerdict(True, "OK", "no secret configured")
        if not provided or not hmac.compare_digest(str(provided), str(expected)):
            return SafetyVerdict(False, "UNAUTHORIZED", "webhook secret mismatch",
                                 bp.WEBHOOK_UNAUTHORIZED_STATUS)
        return SafetyVerdict(True, "OK", "secret ok")

    # --------------------------------------------------------- §17.2 freshness
    def check_signal_freshness(self, timestamp: float, interval_seconds: int = 60,
                               now: Optional[float] = None) -> SafetyVerdict:
        if now is None:
            from app.core.exchange_clock import get_exchange_clock

            now = get_exchange_clock().now()
        if bp.is_stale_signal(timestamp, now, interval_seconds):
            age = now - bp.normalize_timestamp(timestamp)
            return SafetyVerdict(False, "STALE_SIGNAL", f"signal age {age:.0f}s exceeds limit", 400)
        return SafetyVerdict(True, "OK", "fresh")

    # ------------------------------------------------------------- main gate
    def check(self, *, symbol: Optional[str] = None, open_positions: int = 0,
              symbol_halted: bool = False) -> SafetyVerdict:
        """§4.2 Schritt 1+2 — Reihenfolge ist normativ."""
        if self.kill_switch_active:
            return SafetyVerdict(False, "KILL_SWITCH", "KILL_SWITCH file present",
                                 bp.WEBHOOK_BLOCKED_STATUS,
                                 {"halt_action": self.config.halt_action})
        if self.pause_active:
            return SafetyVerdict(False, "PAUSED", "PAUSE file present", bp.WEBHOOK_BLOCKED_STATUS)
        if self.daily_pnl_usd <= -abs(self.config.max_daily_loss_usd):
            return SafetyVerdict(False, "DAILY_LOSS_LIMIT",
                                 f"daily pnl {self.daily_pnl_usd:.2f} <= -{self.config.max_daily_loss_usd:.2f}",
                                 bp.WEBHOOK_BLOCKED_STATUS)
        if self._consecutive_errors >= self.config.max_consecutive_errors:
            return SafetyVerdict(False, "CONSECUTIVE_ERRORS",
                                 f"{self._consecutive_errors} consecutive execution errors",
                                 bp.WEBHOOK_BLOCKED_STATUS)
        if symbol_halted:
            return SafetyVerdict(False, "SYMBOL_HALTED", f"halt:symbol:{symbol} active", 409)
        if open_positions >= self.config.max_open_positions:
            return SafetyVerdict(False, "MAX_OPEN_POSITIONS",
                                 f"{open_positions} >= {self.config.max_open_positions}", 409)
        return SafetyVerdict(True, "OK", "clear")

    def snapshot(self) -> Dict[str, Any]:
        return {
            "kill_switch": self.kill_switch_active,
            "pause": self.pause_active,
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "max_daily_loss_usd": self.config.max_daily_loss_usd,
            "consecutive_errors": self._consecutive_errors,
            "max_consecutive_errors": self.config.max_consecutive_errors,
            "max_open_positions": self.config.max_open_positions,
            "halt_action": self.config.halt_action,
            "live_trading": self.config.live_trading,
        }


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _touch(path: str, reason: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"{reason}\n{time.time()}\n")


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


_guard: Optional[SafetyGuard] = None


def get_safety_guard(config: Optional[SigmaConfig] = None) -> SafetyGuard:
    global _guard
    if _guard is None:
        _guard = SafetyGuard(config)
    return _guard


def set_safety_guard(guard: Optional[SafetyGuard]) -> None:
    global _guard
    _guard = guard
