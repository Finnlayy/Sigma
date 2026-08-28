"""
=========================================================
Datei:      app/tv/scraper_client.py
Zweck:      §6 Loop C — HTTP-Client für das Scraper-Sidecar auf :8001.
            Normalisiert auf Alpha-Candles {ts,o,h,l,c,v}, kennt den
            Sigma-Envelope (`source`/`degraded`) des Overlays und
            fällt bei Ausfall nie stumm auf falsche Daten zurück.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Datenabfrage)
=========================================================
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config
from app.tv.interval_map import to_scraper_timeframe
from app.tv.symbol_map import to_scraper_ticker

logger = logging.getLogger("app.tv.scraper_client")

SOURCE_VENDOR = "tv_scraper"
SOURCE_SYNTHETIC = "synthetic"


class ScraperUnavailable(RuntimeError):
    """Sidecar nicht erreichbar — Aufrufer entscheidet über Fallback."""


class TradingViewScraperClient:
    """Dünner Wrapper um das vendored `tradingview-scraper` FastAPI-Sidecar."""

    def __init__(self, config: Optional[SigmaConfig] = None, base_url: Optional[str] = None,
                 transport=None):
        self.config = config or load_config()
        self.base_url = (base_url or self.config.tv_scraper_url).rstrip("/")
        self.timeout_s = self.config.tv_scraper_timeout_s
        self._transport = transport          # Test-Seam: callable(url, params) -> dict
        self._last_meta: Dict[str, Any] = {}
        self._health_cache: Tuple[float, Dict[str, Any]] = (0.0, {})

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if self._transport is not None:
            return self._transport(url, params or {})
        try:
            import httpx  # type: ignore

            resp = httpx.get(url, params=params or {}, timeout=self.timeout_s)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise ScraperUnavailable(f"{url}: {exc}") from exc

    @staticmethod
    def _meta(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"source": "unknown", "degraded": True}
        return {
            "source": payload.get("source", SOURCE_VENDOR),
            "degraded": bool(payload.get("degraded", False)),
            "cached": bool(payload.get("cached", False)),
            "age_s": payload.get("age_s"),
            "upstream_error": payload.get("upstream_error"),
        }

    @property
    def last_meta(self) -> Dict[str, Any]:
        """Herkunft der zuletzt gelieferten Daten (`tv_scraper` | `cache_stale` | `synthetic`)."""
        return dict(self._last_meta)

    # ----------------------------------------------------------------- ohlcv
    def fetch_ohlc(self, symbol: str, interval_min: int = 15, count: int = 500,
                   *, exchange: str = "KRAKEN") -> List[Dict[str, float]]:
        candles, _ = self.fetch_ohlc_with_meta(symbol, interval_min, count, exchange=exchange)
        return candles

    def fetch_ohlc_with_meta(self, symbol: str, interval_min: int = 15, count: int = 500,
                             *, exchange: str = "KRAKEN"
                             ) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
        ex, ticker = to_scraper_ticker(symbol, exchange=exchange)
        return self.fetch_ticker_ohlc(ex, ticker, interval_min, count)

    def fetch_ticker_ohlc(
        self,
        exchange: str,
        ticker: str,
        interval_min: int = 15,
        count: int = 500,
    ) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
        """Fetch an exact TradingView ticker without crypto pair normalization."""
        ex = exchange.strip().upper()
        symbol = ticker.strip().upper()
        path = bp.SCRAPER_ENDPOINTS["ohlcv"].format(exchange=ex, ticker=symbol)
        payload = self._get(path, {"timeframe": to_scraper_timeframe(interval_min), "candles": count})
        self._last_meta = self._meta(payload)
        return normalize_ohlc(payload), dict(self._last_meta)

    def fetch_indicators(self, symbol: str, interval_min: int = 1440,
                         *, exchange: str = "KRAKEN") -> Dict[str, Any]:
        ex, ticker = to_scraper_ticker(symbol, exchange=exchange)
        path = bp.SCRAPER_ENDPOINTS["indicators"].format(exchange=ex, ticker=ticker)
        payload = self._get(path, {"timeframe": to_scraper_timeframe(interval_min)})
        self._last_meta = self._meta(payload)
        if isinstance(payload, dict):
            return payload.get("indicator") or payload.get("data") or {}
        return {}

    def fetch_overview(self, symbol: str, *, exchange: str = "KRAKEN") -> Dict[str, Any]:
        ex, ticker = to_scraper_ticker(symbol, exchange=exchange)
        payload = self._get(bp.SCRAPER_ENDPOINTS["overview"].format(exchange=ex, ticker=ticker))
        self._last_meta = self._meta(payload)
        return payload.get("data", payload) if isinstance(payload, dict) else {}

    # --------------------------------------------------------------- markets
    def movers(self, market: str = "crypto", category: str = "gainers",
               limit: int = 25) -> List[Dict[str, Any]]:
        payload = self._get(bp.SCRAPER_ENDPOINTS["movers"],
                            {"market": market, "category": category, "limit": limit})
        self._last_meta = self._meta(payload)
        return _rows(payload)

    def screener(self, **params: Any) -> List[Dict[str, Any]]:
        payload = self._get(bp.SCRAPER_ENDPOINTS["screener"], params)
        self._last_meta = self._meta(payload)
        return _rows(payload)

    def download_url(self, kind: str, *, exchange: str = "", ticker: str = "",
                     fmt: str = "csv", **params: Any) -> str:
        """Direkte CSV/JSON-URL für Offline-Exporte (`/api/download/*`, §6)."""
        base = f"{self.base_url}{bp.SCRAPER_ENDPOINTS['download']}/{kind}"
        if exchange and ticker:
            base = f"{base}/{exchange.upper()}/{ticker.upper()}"
        query = "&".join([f"fmt={fmt}"] + [f"{k}={v}" for k, v in params.items()])
        return f"{base}?{query}"

    # ---------------------------------------------------------------- health
    def health(self, *, max_age_s: float = 5.0) -> Dict[str, Any]:
        """Gepollt von `/api/v1/health` — Ergebnis wird kurz gecacht."""
        ts, cached = self._health_cache
        if cached and (time.time() - ts) < max_age_s:
            return cached
        try:
            payload = self._get("/health")
            out = {
                "ok": True,
                "base_url": self.base_url,
                "degraded": bool(payload.get("degraded", False)) if isinstance(payload, dict) else False,
                "vendor": (payload or {}).get("vendor", {}) if isinstance(payload, dict) else {},
                "cache": (payload or {}).get("cache", {}) if isinstance(payload, dict) else {},
                "rate_limit": (payload or {}).get("rate_limit", {}) if isinstance(payload, dict) else {},
            }
        except ScraperUnavailable as exc:
            out = {"ok": False, "base_url": self.base_url, "degraded": True, "error": str(exc)}
        self._health_cache = (time.time(), out)
        return out

    def ping(self) -> bool:
        return bool(self.health().get("ok"))


def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data", payload.get("rows", []))
    else:
        data = payload
    if isinstance(data, dict):
        data = data.get("data", [])
    return [row for row in (data or []) if isinstance(row, dict)]


def normalize_ohlc(payload: Any) -> List[Dict[str, float]]:
    """Scraper-Antwort -> Alpha-Candles. Timestamps immer Unix-Sekunden."""
    rows: List[Dict[str, Any]]
    if isinstance(payload, dict):
        rows = payload.get("ohlc") or payload.get("data") or payload.get("candles") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out: List[Dict[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp", row.get("ts", row.get("time", 0)))
        ts = float(ts)
        if ts > bp.TIMESTAMP_MS_THRESHOLD:
            ts /= 1000.0
        try:
            out.append({
                "ts": float(ts),
                "o": float(row.get("open", row.get("o", 0.0))),
                "h": float(row.get("high", row.get("h", 0.0))),
                "l": float(row.get("low", row.get("l", 0.0))),
                "c": float(row.get("close", row.get("c", 0.0))),
                "v": float(row.get("volume", row.get("v", 0.0)) or 0.0),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda c: c["ts"])
    return out


_client: Optional[TradingViewScraperClient] = None


def get_scraper_client(config: Optional[SigmaConfig] = None) -> TradingViewScraperClient:
    global _client
    if _client is None:
        _client = TradingViewScraperClient(config)
    return _client


def set_scraper_client(client: Optional[TradingViewScraperClient]) -> None:
    """Test-Seam."""
    global _client
    _client = client
