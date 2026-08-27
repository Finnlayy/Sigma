"""
=========================================================
Datei:      app/tv/scraper_client.py
Zweck:      §6 Loop C — HTTP-Client für das Scraper-Sidecar auf :8001.
            Normalisiert auf Alpha-Candles {ts,o,h,l,c,v}.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Datenabfrage)
=========================================================
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config
from app.tv.interval_map import to_scraper_timeframe
from app.tv.symbol_map import to_scraper_ticker

logger = logging.getLogger("app.tv.scraper_client")


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

    # ----------------------------------------------------------------- ohlcv
    def fetch_ohlc(self, symbol: str, interval_min: int = 15, count: int = 500,
                   *, exchange: str = "KRAKEN") -> List[Dict[str, float]]:
        ex, ticker = to_scraper_ticker(symbol, exchange=exchange)
        path = bp.SCRAPER_ENDPOINTS["ohlcv"].format(exchange=ex, ticker=ticker)
        payload = self._get(path, {"timeframe": to_scraper_timeframe(interval_min), "candles": count})
        return normalize_ohlc(payload)

    def fetch_indicators(self, symbol: str, *, exchange: str = "KRAKEN") -> Dict[str, Any]:
        ex, ticker = to_scraper_ticker(symbol, exchange=exchange)
        path = bp.SCRAPER_ENDPOINTS["indicators"].format(exchange=ex, ticker=ticker)
        payload = self._get(path)
        return payload.get("indicator", payload) if isinstance(payload, dict) else {}

    def fetch_overview(self, symbol: str, *, exchange: str = "KRAKEN") -> Dict[str, Any]:
        ex, ticker = to_scraper_ticker(symbol, exchange=exchange)
        return self._get(bp.SCRAPER_ENDPOINTS["overview"].format(exchange=ex, ticker=ticker))

    def movers(self) -> Dict[str, Any]:
        return self._get(bp.SCRAPER_ENDPOINTS["movers"])

    def screener(self, **params: Any) -> Dict[str, Any]:
        return self._get(bp.SCRAPER_ENDPOINTS["screener"], params)

    # ---------------------------------------------------------------- health
    def health(self) -> Dict[str, Any]:
        try:
            self._get("/")
            return {"ok": True, "base_url": self.base_url}
        except ScraperUnavailable as exc:
            return {"ok": False, "base_url": self.base_url, "error": str(exc)}


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
