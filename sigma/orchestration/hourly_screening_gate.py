"""
=========================================================
Datei:      sigma/orchestration/hourly_screening_gate.py
Zweck:      1h-Screening-Zustandsautomat (KB §6): exakt ein Scan pro
            geschlossener 1h-BTC-Bar; Minutenphasen 00-05 SCAN_AND_DEPLOY,
            05-48 ACTIVE_EXECUTION, 48-55 PRE_CLOSE_UNWIND, 55-60 IDLE_WAIT.
            Idempotent (letzter Scan persistiert, to_dict/Restore).
            UTC-Basis, nur closed bars. Keine Orders, kein Deploy.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Orchestrierung) / Noir (Fail-Closed)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

SCAN_AND_DEPLOY = "SCAN_AND_DEPLOY"
ACTIVE_EXECUTION = "ACTIVE_EXECUTION"
PRE_CLOSE_UNWIND = "PRE_CLOSE_UNWIND"
IDLE_WAIT = "IDLE_WAIT"

PHASE_SCAN_MAX_MINUTE = 5
PHASE_ACTIVE_MAX_MINUTE = 48
PHASE_UNWIND_MAX_MINUTE = 55


@dataclass(frozen=True)
class HourlyGateResult:
    """Ergebnis einer Gate-Abfrage."""

    bar_ts: int
    minute_utc: int
    phase: str
    scan_allowed: bool
    last_scan_bar_ts: Optional[int]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class HourlyScreeningGate:
    """Merkt sich den letzten verarbeiteten Bar-Zeitstempel; wiederholter
    Aufruf in derselben Stunde -> kein erneuter Scan (Idempotenz)."""

    def __init__(self, last_scan_bar_ts: Optional[int] = None) -> None:
        self.last_scan_bar_ts: Optional[int] = last_scan_bar_ts

    # ------------------------------------------------------------------ API

    def evaluate(self, bar_ts: int, now_ts: Optional[float] = None) -> HourlyGateResult:
        """Phase aus der UTC-Minute der geschlossenen Bar; scan_allowed nur in
        SCAN_AND_DEPLOY UND wenn diese Bar noch nicht gescannt wurde."""
        bar_ts = int(bar_ts)
        minute = _utc_minute(bar_ts)
        phase = phase_for_minute(minute)
        already_scanned = self.last_scan_bar_ts == bar_ts
        scan_allowed = phase == SCAN_AND_DEPLOY and not already_scanned
        if scan_allowed:
            reason = "scan_ok"
        elif phase != SCAN_AND_DEPLOY:
            reason = "outside_scan_window"
        else:
            reason = "bar_already_scanned"
        return HourlyGateResult(
            bar_ts=bar_ts,
            minute_utc=minute,
            phase=phase,
            scan_allowed=scan_allowed,
            last_scan_bar_ts=self.last_scan_bar_ts,
            reason=reason,
        )

    def mark_scanned(self, bar_ts: int) -> None:
        """Persistiert den gescannten Bar-Zeitstempel (idempotent)."""
        self.last_scan_bar_ts = int(bar_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {"last_scan_bar_ts": self.last_scan_bar_ts}

    @classmethod
    def restore(cls, state: Mapping[str, Any]) -> "HourlyScreeningGate":
        last = state.get("last_scan_bar_ts")
        return cls(last_scan_bar_ts=int(last) if last is not None else None)


def phase_for_minute(minute_utc: int) -> str:
    """Minutenphase (UTC): 00-05 SCAN, 05-48 ACTIVE, 48-55 UNWIND, 55-60 IDLE."""
    minute = int(minute_utc) % 60
    if minute <= PHASE_SCAN_MAX_MINUTE:
        return SCAN_AND_DEPLOY
    if minute < PHASE_ACTIVE_MAX_MINUTE:
        return ACTIVE_EXECUTION
    if minute < PHASE_UNWIND_MAX_MINUTE:
        return PRE_CLOSE_UNWIND
    return IDLE_WAIT


def _utc_minute(ts: int) -> int:
    """UTC-Minute einer Bar-Zeit. Millisekunden-Zeitstempel werden normiert."""
    t = int(ts)
    if t >= 1e12:
        t //= 1000
    return (t % 3600) // 60


__all__ = [
    "ACTIVE_EXECUTION", "HourlyGateResult", "HourlyScreeningGate", "IDLE_WAIT",
    "PRE_CLOSE_UNWIND", "SCAN_AND_DEPLOY", "phase_for_minute",
]
