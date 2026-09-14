"""
=========================================================
Datei:      app/execution/kraken_cancel_after_session.py
Zweck:      L4 session start + refresh for ``futures/cancel-after`` (deadman CLI).
            Paper path is a no-op via bridge.cancel_after.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.execution.KrakenCliBridge import KrakenCliBridge

logger = logging.getLogger("app.execution.kraken_cancel_after_session")

# Skill L4 default window; refresh at half-life.
DEFAULT_TIMEOUT_S = 600
DEFAULT_REFRESH_S = 300


@dataclass
class CancelAfterSession:
    bridge: Optional[KrakenCliBridge] = None
    timeout_s: int = DEFAULT_TIMEOUT_S
    refresh_s: int = DEFAULT_REFRESH_S
    last_set_mono: float = 0.0
    last_ok: bool = False
    last_error: str = ""
    last_argv: list = field(default_factory=list)
    armed: bool = False

    def start(self, *, confirmed: bool = True) -> Dict[str, Any]:
        """Arm cancel-after at session start (live L4 only effectively mutates)."""
        return self.refresh(force=True, confirmed=confirmed)

    def refresh(self, *, force: bool = False, confirmed: bool = True) -> Dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self.armed
            and self.last_ok
            and (now - self.last_set_mono) < max(1.0, float(self.refresh_s))
        ):
            return {
                "ok": True, "skipped": True, "armed": self.armed,
                "timeout_s": self.timeout_s, "age_s": round(now - self.last_set_mono, 1),
            }
        if self.bridge is None:
            self.last_ok = False
            self.last_error = "bridge_unavailable"
            return {"ok": False, "armed": False, "error": self.last_error}

        res = self.bridge.cancel_after(self.timeout_s, confirmed=confirmed)
        self.last_set_mono = now
        self.last_ok = bool(res.ok)
        self.last_error = "" if res.ok else (res.error_code or res.stderr or "cancel_after_failed")
        self.last_argv = list(res.argv or [])
        self.armed = bool(res.ok)
        if res.ok:
            logger.info(
                "cancel-after armed timeout=%ss mode=%s argv=%s",
                self.timeout_s, res.mode, res.argv,
            )
        else:
            logger.warning("cancel-after failed: %s", self.last_error)
        return {
            "ok": res.ok,
            "armed": self.armed,
            "mode": res.mode,
            "timeout_s": self.timeout_s,
            "error": self.last_error,
            "argv": list(self.last_argv),
            "stdout": (res.stdout or "")[:500],
        }

    def snapshot(self) -> Dict[str, Any]:
        age = (time.monotonic() - self.last_set_mono) if self.last_set_mono else None
        return {
            "armed": self.armed,
            "ok": self.last_ok,
            "timeout_s": self.timeout_s,
            "refresh_s": self.refresh_s,
            "age_s": round(age, 1) if age is not None else None,
            "error": self.last_error,
            "argv": list(self.last_argv),
        }


_SESSION: Optional[CancelAfterSession] = None


def get_cancel_after_session(
    bridge: Optional[KrakenCliBridge] = None,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    refresh_s: int = DEFAULT_REFRESH_S,
) -> CancelAfterSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = CancelAfterSession(
            bridge=bridge, timeout_s=timeout_s, refresh_s=refresh_s,
        )
    elif bridge is not None:
        _SESSION.bridge = bridge
    return _SESSION


def set_cancel_after_session(session: Optional[CancelAfterSession]) -> None:
    global _SESSION
    _SESSION = session
