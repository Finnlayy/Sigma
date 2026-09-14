"""
=========================================================
Datei:      app/scraper/cache.py
Zweck:      §6 — TTL-Cache + Token-Bucket-Rate-Limiter für das
            Scraper-Sidecar. TradingView darf nicht geflutet
            werden (§26 Vorgriff: Provider-Limits).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    stale_serves: int = 0

    def as_dict(self) -> Dict[str, int]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "stale_serves": self.stale_serves,
            "hit_ratio": round(self.hits / total, 4) if total else 0.0,
        }


class TTLCache:
    """LRU + TTL. Hält Werte nach Ablauf als *stale* vor (Fallback bei Upstream-Ausfall)."""

    def __init__(self, max_entries: int = 512):
        self.max_entries = max_entries
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def get(self, key: str, ttl_s: float) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.stats.misses += 1
                return None
            ts, value = entry
            if (time.time() - ts) > ttl_s:
                self.stats.misses += 1
                return None
            self._data.move_to_end(key)
            self.stats.hits += 1
            return value

    def get_stale(self, key: str) -> Optional[Any]:
        """Abgelaufener Wert — nur als Notfall-Fallback verwenden."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            self.stats.stale_serves += 1
            return entry[1]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.stats.evictions += 1

    def age_of(self, key: str) -> Optional[float]:
        with self._lock:
            entry = self._data.get(key)
            return None if entry is None else time.time() - entry[0]

    def clear(self) -> int:
        with self._lock:
            n = len(self._data)
            self._data.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class TokenBucket:
    """Klassischer Token-Bucket: `rate_per_min` Nachfüllrate, `burst` Kapazität."""

    def __init__(self, rate_per_min: float, burst: float):
        self.rate_per_s = max(rate_per_min, 0.0) / 60.0
        self.capacity = max(burst, 1.0)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self.rejected = 0
        self.granted = 0

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_s)
        self._last = now

    def acquire(self, cost: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= cost:
                self._tokens -= cost
                self.granted += 1
                return True
            self.rejected += 1
            return False

    def retry_after_s(self, cost: float = 1.0) -> float:
        with self._lock:
            self._refill()
            if self._tokens >= cost or self.rate_per_s <= 0:
                return 0.0
            return round((cost - self._tokens) / self.rate_per_s, 2)

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            self._refill()
            return {
                "tokens": round(self._tokens, 2),
                "capacity": self.capacity,
                "rate_per_min": round(self.rate_per_s * 60.0, 2),
                "granted": self.granted,
                "rejected": self.rejected,
            }


def cache_key(*parts: Any) -> str:
    return "|".join(str(p).upper() for p in parts)
