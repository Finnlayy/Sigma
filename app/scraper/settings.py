"""
=========================================================
Datei:      app/scraper/settings.py
Zweck:      §6 / §9 — Konfiguration des Scraper-Sidecars.
            Reihenfolge: Blueprint-Default -> SIGMA_*-Env.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from app.core import blueprint as bp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ScraperSettings:
    """Laufzeit-Konfiguration des Sidecars (alle Werte via `SIGMA_SCRAPER_*` überschreibbar)."""

    host: str = field(default_factory=lambda: _env("SIGMA_SCRAPER_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("SIGMA_SCRAPER_PORT", bp.PORT_SCRAPER))
    vendor_path: str = field(default_factory=lambda: _env(
        "SIGMA_SCRAPER_VENDOR_PATH", os.path.join(REPO_ROOT, "vendor", "tradingview-scraper")))

    # Netz / Robustheit
    timeout_s: float = field(default_factory=lambda: _env_float(
        "SIGMA_SCRAPER_TIMEOUT_S", float(bp.SCRAPER_TIMEOUT_S)))
    max_retries: int = field(default_factory=lambda: _env_int("SIGMA_SCRAPER_MAX_RETRIES", 2))
    retry_backoff_s: float = field(default_factory=lambda: _env_float("SIGMA_SCRAPER_RETRY_BACKOFF_S", 1.5))
    breaker_failures: int = field(default_factory=lambda: _env_int("SIGMA_SCRAPER_BREAKER_FAILURES", 3))
    breaker_cooldown_s: float = field(default_factory=lambda: _env_float(
        "SIGMA_SCRAPER_BREAKER_COOLDOWN_S", 60.0))

    # Cache (TTL pro Ressourcen-Klasse, Sekunden)
    ohlcv_ttl_s: float = field(default_factory=lambda: _env_float("SIGMA_SCRAPER_OHLCV_TTL_S", 20.0))
    meta_ttl_s: float = field(default_factory=lambda: _env_float("SIGMA_SCRAPER_META_TTL_S", 300.0))
    market_ttl_s: float = field(default_factory=lambda: _env_float("SIGMA_SCRAPER_MARKET_TTL_S", 120.0))
    cache_max_entries: int = field(default_factory=lambda: _env_int("SIGMA_SCRAPER_CACHE_MAX", 512))

    # Rate Limit (Token-Bucket gegen TradingView, §26 Vorgriff)
    rate_limit_per_min: float = field(default_factory=lambda: _env_float("SIGMA_SCRAPER_RATE_PER_MIN", 60.0))
    rate_limit_burst: float = field(default_factory=lambda: _env_float("SIGMA_SCRAPER_RATE_BURST", 15.0))

    # Betriebsmodus
    offline: bool = field(default_factory=lambda: _env_bool("SIGMA_SCRAPER_OFFLINE", False))
    allow_synthetic_fallback: bool = field(
        default_factory=lambda: _env_bool("SIGMA_SCRAPER_SYNTHETIC_FALLBACK", True))
    mount_vendor_app: bool = field(default_factory=lambda: _env_bool("SIGMA_SCRAPER_MOUNT_VENDOR", True))
    max_candles: int = field(default_factory=lambda: _env_int("SIGMA_SCRAPER_MAX_CANDLES", 5000))

    @property
    def cors_origins(self) -> List[str]:
        raw = _env("SIGMA_SCRAPER_CORS", "*")
        return [o.strip() for o in raw.split(",") if o.strip()]

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "vendor_path": self.vendor_path,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
            "breaker_failures": self.breaker_failures,
            "breaker_cooldown_s": self.breaker_cooldown_s,
            "ohlcv_ttl_s": self.ohlcv_ttl_s,
            "meta_ttl_s": self.meta_ttl_s,
            "market_ttl_s": self.market_ttl_s,
            "rate_limit_per_min": self.rate_limit_per_min,
            "offline": self.offline,
            "allow_synthetic_fallback": self.allow_synthetic_fallback,
            "max_candles": self.max_candles,
        }


_settings: ScraperSettings | None = None


def get_settings(refresh: bool = False) -> ScraperSettings:
    global _settings
    if _settings is None or refresh:
        _settings = ScraperSettings()
    return _settings
