"""
=========================================================
Datei:      app/core/exchange_clock.py
Zweck:      §23.1 / Axiom 4 — Kraken Server-Time als Single Source of Truth
Knoten:     Ciel (Sigma Core)
=========================================================

Kanonisch: Deadman, EOD-Cron, Scheduler-Tiers und der Stale-Signal-Gate
benutzen ``exchange_clock.now()`` statt ``time.time()``.

    time_offset = t_kraken - t_host
    now()       = time.time() + time_offset

Der Fetcher ist injizierbar (Tests / Offline-Betrieb). Ohne erfolgreichen
Sync bleibt ``offset = 0.0`` und ``synced`` ist ``False`` — das System
laeuft weiter, meldet den Zustand aber ehrlich an die Telemetrie.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.core.exchange_clock")

TimeFetcher = Callable[[], float]


def _default_fetcher() -> float:
    """Holt ``unixtime`` von ``GET /0/public/Time``."""
    import httpx  # lokal importiert: Clock ist auch ohne httpx nutzbar

    resp = httpx.get(bp.KRAKEN_TIME_URL, timeout=bp.CLOCK_SYNC_TIMEOUT_S)
    resp.raise_for_status()
    payload = resp.json()
    errors = payload.get("error") or []
    if errors:
        raise RuntimeError(f"kraken time error: {errors}")
    unixtime = payload["result"]["unixtime"]
    return float(unixtime)


@dataclass
class ClockStatus:
    synced: bool
    offset_s: float
    last_sync_ts: Optional[float]
    last_error: Optional[str]
    drift_warning: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "synced": self.synced,
            "offset_s": round(self.offset_s, 4),
            "last_sync_ts": self.last_sync_ts,
            "last_error": self.last_error,
            "drift_warning": self.drift_warning,
            "resync_interval_s": bp.CLOCK_RESYNC_INTERVAL_S,
            "source": bp.KRAKEN_TIME_URL,
        }


class ExchangeClock:
    """Kraken-Serverzeit mit stuendlichem Re-Sync (§23.1)."""

    def __init__(
        self,
        fetcher: Optional[TimeFetcher] = None,
        *,
        host_time: Callable[[], float] = time.time,
        resync_interval_s: float = bp.CLOCK_RESYNC_INTERVAL_S,
    ) -> None:
        self._fetcher = fetcher or _default_fetcher
        self._host_time = host_time
        self._resync_interval_s = float(resync_interval_s)
        self._lock = threading.Lock()
        self._offset = 0.0
        self._last_sync: Optional[float] = None
        self._last_error: Optional[str] = None
        self._synced = False

    # ------------------------------------------------------------ sync ---
    def sync(self, *, force: bool = False) -> ClockStatus:
        with self._lock:
            host_now = self._host_time()
            if (
                not force
                and self._last_sync is not None
                and host_now - self._last_sync < self._resync_interval_s
            ):
                return self.status()
            try:
                kraken_now = float(self._fetcher())
            except Exception as exc:  # pragma: no cover - Fehlerpfad geloggt
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("exchange_clock sync failed: %s", self._last_error)
                return self.status()

            self._offset = kraken_now - self._host_time()
            self._last_sync = self._host_time()
            self._last_error = None
            self._synced = True
            if abs(self._offset) >= bp.CLOCK_MAX_OFFSET_WARN_S:
                logger.warning(
                    "exchange_clock drift %.3fs (>= %.1fs) — Host-NTP pruefen",
                    self._offset, bp.CLOCK_MAX_OFFSET_WARN_S,
                )
            return self.status()

    def maybe_resync(self) -> ClockStatus:
        """Tier-1-tauglich: synchronisiert nur, wenn das Intervall abgelaufen ist."""
        return self.sync(force=False)

    # ------------------------------------------------------------ time ---
    def now(self) -> float:
        return self._host_time() + self._offset

    def now_ms(self) -> int:
        return int(self.now() * 1000.0)

    @property
    def offset_s(self) -> float:
        return self._offset

    @property
    def synced(self) -> bool:
        return self._synced

    # ------------------------------------------------------------ gate ---
    def signal_age_s(self, signal_ts: float) -> float:
        """Alter eines Webhook-Timestamps; akzeptiert Sekunden **und** ms."""
        ts = float(signal_ts)
        if ts > 1e11:  # Millisekunden-Payload (TV ``{{timenow}}``)
            ts /= 1000.0
        return self.now() - ts

    def is_signal_stale(
        self, signal_ts: float, max_latency_s: float = bp.STALE_SIGNAL_MAX_LATENCY_S
    ) -> bool:
        age = self.signal_age_s(signal_ts)
        if age < -bp.CLOCK_MAX_OFFSET_WARN_S:
            # Zukunfts-Timestamp jenseits der Toleranz => ebenfalls untauglich.
            return True
        return age > float(max_latency_s)

    def assert_fresh(
        self, signal_ts: float, max_latency_s: float = bp.STALE_SIGNAL_MAX_LATENCY_S
    ) -> None:
        if self.is_signal_stale(signal_ts, max_latency_s):
            raise StaleSignalError(
                f"{bp.STALE_SIGNAL_REJECT_CODE}: age={self.signal_age_s(signal_ts):.1f}s "
                f"> {max_latency_s:.0f}s"
            )

    # ----------------------------------------------------------- state ---
    def status(self) -> ClockStatus:
        return ClockStatus(
            synced=self._synced,
            offset_s=self._offset,
            last_sync_ts=self._last_sync,
            last_error=self._last_error,
            drift_warning=abs(self._offset) >= bp.CLOCK_MAX_OFFSET_WARN_S,
        )


class StaleSignalError(RuntimeError):
    """§23.1 — Signal ist gegen die Kraken-Zeit zu alt."""

    code = bp.STALE_SIGNAL_REJECT_CODE


_CLOCK: Optional[ExchangeClock] = None


def get_exchange_clock() -> ExchangeClock:
    global _CLOCK
    if _CLOCK is None:
        _CLOCK = ExchangeClock()
    return _CLOCK


def set_exchange_clock(clock: Optional[ExchangeClock]) -> None:
    """Test-Hook / DI."""
    global _CLOCK
    _CLOCK = clock
