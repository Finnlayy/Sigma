"""
=========================================================
Datei:      app/core/rate_limiter.py
Zweck:      §26 — Multi-Provider Rate Limiter (Token-Bucket pro Provider)
Knoten:     Ciel (Sigma Core)
=========================================================

Drei Provider mit unterschiedlicher Physik:

* **kraken_api** — Call-Counter mit Decay (max 15.0, -0.50/s). Ab 80 %
  Auslastung werden Hintergrund-Polls pausiert; 3.0 Tokens bleiben als
  Notfallreserve fuer Kill-Switch/Cancel-All frei.
* **tradingview_subscription** — kein Ratelimit, sondern ein Alert-Slot-
  Kontingent je Tier (essential = 5) inkl. Rotations-Queue.
* **telegram_bot** — 1 Nachricht/s.

HTTP 429 fuehrt zu exponentiellem Backoff 10s -> 30s -> 60s.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.core.rate_limiter")


class RateLimitExceeded(RuntimeError):
    """Kontingent erschoepft — Aufrufer muss ``retry_after_s`` abwarten."""

    def __init__(self, provider: str, retry_after_s: float) -> None:
        super().__init__(f"{provider}: rate limited, retry in {retry_after_s:.2f}s")
        self.provider = provider
        self.retry_after_s = retry_after_s


@dataclass
class DecayBucket:
    """Kraken-Style Counter: steigt pro Call, faellt mit fester Rate."""

    max_counter: float
    decay_per_s: float
    reserve: float = 0.0
    counter: float = 0.0
    updated: float = field(default_factory=time.monotonic)

    def _decay(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated)
        self.counter = max(0.0, self.counter - elapsed * self.decay_per_s)
        self.updated = now

    def level(self, now: Optional[float] = None) -> float:
        self._decay(now if now is not None else time.monotonic())
        return self.counter

    def utilisation(self, now: Optional[float] = None) -> float:
        return self.level(now) / self.max_counter if self.max_counter else 0.0

    def acquire(self, cost: float = 1.0, *, emergency: bool = False,
                now: Optional[float] = None) -> bool:
        ts = now if now is not None else time.monotonic()
        self._decay(ts)
        ceiling = self.max_counter if emergency else self.max_counter - self.reserve
        if self.counter + cost > ceiling:
            return False
        self.counter += cost
        return True

    def retry_after(self, cost: float = 1.0, *, emergency: bool = False,
                    now: Optional[float] = None) -> float:
        ts = now if now is not None else time.monotonic()
        self._decay(ts)
        ceiling = self.max_counter if emergency else self.max_counter - self.reserve
        deficit = (self.counter + cost) - ceiling
        if deficit <= 0:
            return 0.0
        return deficit / self.decay_per_s if self.decay_per_s > 0 else float("inf")


@dataclass
class IntervalBucket:
    """Fixe Mindestpause zwischen zwei Calls (Telegram: 1 msg/s)."""

    min_interval_s: float
    last_call: Optional[float] = None

    def acquire(self, now: Optional[float] = None) -> bool:
        ts = now if now is not None else time.monotonic()
        if self.last_call is not None and ts - self.last_call < self.min_interval_s:
            return False
        self.last_call = ts
        return True

    def retry_after(self, now: Optional[float] = None) -> float:
        ts = now if now is not None else time.monotonic()
        if self.last_call is None:
            return 0.0
        return max(0.0, self.min_interval_s - (ts - self.last_call))


class AlertSlotRegistry:
    """§26 — TV-Alert-Kontingent je Abo-Tier inkl. Rotations-Queue."""

    def __init__(self, tier: str = bp.TV_SUBSCRIPTION_TIER_DEFAULT,
                 rotation_enabled: bool = bp.TV_ALERT_ROTATION_ENABLED) -> None:
        if tier not in bp.TV_SUBSCRIPTION_TIERS:
            raise ValueError(f"unknown tradingview tier: {tier}")
        self.tier = tier
        self.max_slots = bp.TV_SUBSCRIPTION_TIERS[tier]
        self.rotation_enabled = rotation_enabled
        self._active: Dict[str, float] = {}   # strategy_id -> score
        self._queue: List[str] = []

    @property
    def active(self) -> Dict[str, float]:
        return dict(self._active)

    @property
    def queue(self) -> List[str]:
        return list(self._queue)

    @property
    def free_slots(self) -> int:
        return max(0, self.max_slots - len(self._active))

    def request_slot(self, strategy_id: str, score: float = 0.0) -> Dict[str, Any]:
        if strategy_id in self._active:
            self._active[strategy_id] = score
            return {"granted": True, "rotated_out": None, "queued": False}
        if self.free_slots > 0:
            self._active[strategy_id] = score
            return {"granted": True, "rotated_out": None, "queued": False}
        if not self.rotation_enabled:
            self._enqueue(strategy_id)
            return {"granted": False, "rotated_out": None, "queued": True}
        weakest = min(self._active, key=lambda sid: self._active[sid])
        if self._active[weakest] >= score:
            self._enqueue(strategy_id)
            return {"granted": False, "rotated_out": None, "queued": True}
        del self._active[weakest]
        self._enqueue(weakest)
        self._active[strategy_id] = score
        if strategy_id in self._queue:
            self._queue.remove(strategy_id)
        return {"granted": True, "rotated_out": weakest, "queued": False}

    def _enqueue(self, strategy_id: str) -> None:
        if strategy_id not in self._queue:
            self._queue.append(strategy_id)

    def release_slot(self, strategy_id: str) -> Optional[str]:
        """Gibt einen Slot frei und zieht den naechsten Kandidaten nach."""
        self._active.pop(strategy_id, None)
        if self._queue and self.free_slots > 0:
            promoted = self._queue.pop(0)
            self._active[promoted] = 0.0
            return promoted
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "max_active_alerts": self.max_slots,
            "active": self._active,
            "free_slots": self.free_slots,
            "rotation_enabled": self.rotation_enabled,
            "rotation_queue": list(self._queue),
        }


class ProviderRateLimiter:
    """Kanonischer Einstiegspunkt fuer alle ausgehenden Provider-Calls."""

    def __init__(self, *, tv_tier: str = bp.TV_SUBSCRIPTION_TIER_DEFAULT,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.kraken = DecayBucket(
            max_counter=bp.KRAKEN_MAX_CALL_COUNTER,
            decay_per_s=bp.KRAKEN_COUNTER_DECAY_PER_S,
            reserve=bp.KRAKEN_EMERGENCY_RESERVE_TOKENS,
            updated=clock(),
        )
        self.telegram = IntervalBucket(
            min_interval_s=1.0 / bp.TELEGRAM_MAX_MESSAGES_PER_S
        )
        self.alerts = AlertSlotRegistry(tier=tv_tier)
        self._backoff_step: Dict[str, int] = {}
        self._blocked_until: Dict[str, float] = {}

    # ----------------------------------------------------------- calls ---
    def acquire(self, provider: str, cost: float = 1.0, *,
                emergency: bool = False, background: bool = False) -> None:
        """Erwirbt ein Kontingent oder wirft ``RateLimitExceeded``."""
        with self._lock:
            now = self._clock()
            blocked = self._blocked_until.get(provider)
            if blocked is not None and now < blocked and not emergency:
                raise RateLimitExceeded(provider, blocked - now)

            if provider == "kraken_api":
                if background and not emergency and self.soft_cap_reached(now=now):
                    raise RateLimitExceeded(
                        provider, self.kraken.retry_after(cost, now=now) or 1.0
                    )
                if not self.kraken.acquire(cost, emergency=emergency, now=now):
                    raise RateLimitExceeded(
                        provider, self.kraken.retry_after(cost, emergency=emergency, now=now)
                    )
            elif provider == "telegram_bot":
                if not self.telegram.acquire(now=now):
                    raise RateLimitExceeded(provider, self.telegram.retry_after(now=now))
            else:
                raise ValueError(f"unknown provider: {provider}")

    def try_acquire(self, provider: str, cost: float = 1.0, **kwargs: Any) -> bool:
        try:
            self.acquire(provider, cost, **kwargs)
            return True
        except RateLimitExceeded:
            return False

    def soft_cap_reached(self, *, now: Optional[float] = None) -> bool:
        return self.kraken.utilisation(now if now is not None else self._clock()) >= bp.KRAKEN_SOFT_CAP_PCT

    # --------------------------------------------------------- backoff ---
    def note_429(self, provider: str) -> float:
        """HTTP 429 registrieren; liefert die Backoff-Dauer (10/30/60 s)."""
        with self._lock:
            step = self._backoff_step.get(provider, 0)
            delay = bp.HTTP_429_BACKOFF_S[min(step, len(bp.HTTP_429_BACKOFF_S) - 1)]
            self._backoff_step[provider] = step + 1
            self._blocked_until[provider] = self._clock() + delay
            logger.warning("provider %s hit 429 — backoff %.0fs", provider, delay)
            return delay

    def note_success(self, provider: str) -> None:
        with self._lock:
            self._backoff_step.pop(provider, None)
            self._blocked_until.pop(provider, None)

    def blocked_for(self, provider: str) -> float:
        blocked = self._blocked_until.get(provider)
        if blocked is None:
            return 0.0
        return max(0.0, blocked - self._clock())

    # ------------------------------------------------------ telemetrie ---
    def status(self) -> Dict[str, Any]:
        now = self._clock()
        return {
            "kraken_api": {
                "counter": round(self.kraken.level(now), 3),
                "max_counter": self.kraken.max_counter,
                "decay_per_second": self.kraken.decay_per_s,
                "reserve_emergency_tokens": self.kraken.reserve,
                "utilisation": round(self.kraken.utilisation(now), 3),
                "soft_cap_pct": bp.KRAKEN_SOFT_CAP_PCT,
                "soft_cap_reached": self.soft_cap_reached(now=now),
                "blocked_for_s": round(self.blocked_for("kraken_api"), 2),
            },
            "tradingview_subscription": self.alerts.as_dict(),
            "telegram_bot": {
                "max_messages_per_second": bp.TELEGRAM_MAX_MESSAGES_PER_S,
                "retry_after_s": round(self.telegram.retry_after(now), 3),
                "blocked_for_s": round(self.blocked_for("telegram_bot"), 2),
            },
            "backoff_ladder_s": list(bp.HTTP_429_BACKOFF_S),
        }


_LIMITER: Optional[ProviderRateLimiter] = None


def get_rate_limiter() -> ProviderRateLimiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = ProviderRateLimiter()
    return _LIMITER


def set_rate_limiter(limiter: Optional[ProviderRateLimiter]) -> None:
    global _LIMITER
    _LIMITER = limiter
