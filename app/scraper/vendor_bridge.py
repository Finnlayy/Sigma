"""
=========================================================
Datei:      app/scraper/vendor_bridge.py
Zweck:      §6 — Adapter auf `vendor/tradingview-scraper`.
            Lazy Import, Retry/Backoff, einheitliche Fehler
            und Normalisierung auf das Sigma-Candle-Schema.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Datenabfrage)
=========================================================
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.scraper.settings import ScraperSettings, get_settings

logger = logging.getLogger("app.scraper.vendor")


class VendorUnavailable(RuntimeError):
    """Vendor-Paket fehlt oder TradingView ist nicht erreichbar."""


class VendorBridge:
    """Kapselt alle Aufrufe in den vendored Scraper (Thread-safe, serialisiert)."""

    def __init__(self, settings: Optional[ScraperSettings] = None):
        self.settings = settings or get_settings()
        self._modules: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._import_error: Optional[str] = None
        self._last_error: Optional[str] = None
        self._calls = 0
        self._failures = 0
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    # ------------------------------------------------------------- bootstrap
    def ensure_path(self) -> None:
        path = self.settings.vendor_path
        if path and path not in sys.path:
            sys.path.insert(0, path)

    def _import(self) -> Dict[str, Any]:
        if self._modules:
            return self._modules
        self.ensure_path()
        try:
            from tradingview_scraper.symbols.fundamental_graphs import FundamentalGraphs
            from tradingview_scraper.symbols.market_movers import MarketMovers
            from tradingview_scraper.symbols.overview import Overview
            from tradingview_scraper.symbols.screener import Screener
            from tradingview_scraper.symbols.stream import Streamer
            from tradingview_scraper.symbols.technicals import Indicators
        except Exception as exc:  # pragma: no cover - depends on host deps
            self._import_error = f"{type(exc).__name__}: {exc}"
            raise VendorUnavailable(f"vendor import failed: {self._import_error}") from exc

        self._modules = {
            "Streamer": Streamer,
            "Indicators": Indicators,
            "Overview": Overview,
            "MarketMovers": MarketMovers,
            "Screener": Screener,
            "FundamentalGraphs": FundamentalGraphs,
        }
        self._import_error = None
        return self._modules

    @property
    def available(self) -> bool:
        if self.settings.offline:
            return False
        try:
            self._import()
            return True
        except VendorUnavailable:
            return False

    def status(self) -> Dict[str, Any]:
        return {
            "vendor_path": self.settings.vendor_path,
            "importable": bool(self._modules) or self.available,
            "import_error": self._import_error,
            "offline_mode": self.settings.offline,
            "calls": self._calls,
            "failures": self._failures,
            "last_error": self._last_error,
            "circuit_open": self.circuit_open,
            "circuit_reopens_in_s": max(0.0, round(self._breaker_open_until - time.time(), 1)),
            "consecutive_failures": self._consecutive_failures,
        }

    @property
    def circuit_open(self) -> bool:
        """Nach `breaker_failures` Fehlschlaegen wird TradingView kurz nicht mehr angefasst."""
        return time.time() < self._breaker_open_until

    def reset_circuit(self) -> None:
        self._breaker_open_until = 0.0
        self._consecutive_failures = 0

    # ----------------------------------------------------------------- retry
    def _call(self, label: str, fn, *args, **kwargs) -> Any:
        """Serialisierter Aufruf mit `max_retries`, Backoff und Circuit-Breaker."""
        if self.circuit_open:
            raise VendorUnavailable(
                f"circuit open ({self._consecutive_failures} consecutive failures): "
                f"{self._last_error}")

        attempts = max(1, self.settings.max_retries + 1)
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            with self._lock:
                self._calls += 1
                try:
                    result = fn(*args, **kwargs)
                    self._consecutive_failures = 0
                    self._breaker_open_until = 0.0
                    return result
                except Exception as exc:  # noqa: BLE001 - upstream is untyped
                    last_exc = exc
                    self._failures += 1
                    self._last_error = f"{label}: {type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                time.sleep(self.settings.retry_backoff_s * (attempt + 1))
                logger.warning("vendor call %s failed (attempt %d/%d): %s",
                               label, attempt + 1, attempts, last_exc)

        self._consecutive_failures += 1
        if self._consecutive_failures >= max(1, self.settings.breaker_failures):
            self._breaker_open_until = time.time() + self.settings.breaker_cooldown_s
            logger.warning("vendor circuit opened for %.0fs after %d failures",
                           self.settings.breaker_cooldown_s, self._consecutive_failures)
        raise VendorUnavailable(self._last_error or f"{label} failed")

    # ----------------------------------------------------------------- ohlcv
    def ohlcv(self, exchange: str, ticker: str, timeframe: str, candles: int) -> List[Dict[str, Any]]:
        mods = self._import()
        streamer_cls = mods["Streamer"]

        def _run() -> List[Dict[str, Any]]:
            streamer = streamer_cls(export_result=True, export_type="json")
            result = streamer.stream(
                exchange=exchange.upper(), symbol=ticker.upper(),
                timeframe=timeframe, numb_price_candles=candles,
            )
            rows = (result or {}).get("ohlc", []) if isinstance(result, dict) else []
            if not rows:
                raise VendorUnavailable("empty ohlc payload")
            return rows

        return normalize_candles(self._call("ohlcv", _run))

    def indicators(self, exchange: str, ticker: str, timeframe: str = "1d") -> Dict[str, Any]:
        mods = self._import()
        scraper = mods["Indicators"]()
        payload = self._call("indicators", scraper.scrape, exchange=exchange.upper(),
                             symbol=ticker.upper(), timeframe=timeframe, allIndicators=True)
        return _unwrap(payload)

    def overview(self, exchange: str, ticker: str) -> Dict[str, Any]:
        mods = self._import()
        scraper = mods["Overview"]()
        payload = self._call("overview", scraper.get_symbol_overview,
                             symbol=f"{exchange.upper()}:{ticker.upper()}")
        return _unwrap(payload)

    def fundamentals(self, exchange: str, ticker: str) -> Dict[str, Any]:
        mods = self._import()
        scraper = mods["FundamentalGraphs"]()
        payload = self._call("fundamentals", scraper.get_fundamentals,
                             symbol=f"{exchange.upper()}:{ticker.upper()}")
        return _unwrap(payload)

    def movers(self, market: str = "crypto", category: str = "gainers", limit: int = 25) -> List[Dict[str, Any]]:
        mods = self._import()
        scraper = mods["MarketMovers"]()
        payload = self._call("movers", scraper.scrape, market=market, category=category, limit=limit)
        return _unwrap_list(payload)

    def screener(self, market: str = "crypto", sort_by: str = "volume", sort_order: str = "desc",
                 limit: int = 25, filters: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        mods = self._import()
        scraper = mods["Screener"]()
        payload = self._call("screener", scraper.screen, market=market, filters=filters or None,
                             sort_by=sort_by, sort_order=sort_order, limit=limit)
        return _unwrap_list(payload)


# ------------------------------------------------------------------ helpers

def _unwrap(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        _raise_on_bad_status(payload)
        data = payload.get("data")
        if isinstance(data, dict):
            if not data:
                raise VendorUnavailable("empty payload")
            return data
        if data is not None:
            return {"data": data}
        return payload
    return {"data": payload}


def _raise_on_bad_status(payload: Dict[str, Any]) -> None:
    """Der Vendor signalisiert Fehler teils als `status: failed|error` statt Exception."""
    status = payload.get("status")
    if status is not None and status != "success":
        raise VendorUnavailable(str(payload.get("error") or f"vendor status={status}"))


def _unwrap_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        _raise_on_bad_status(payload)
        data = payload.get("data", [])
    else:
        data = payload
    if isinstance(data, dict):
        data = data.get("data", [])
    rows = [row for row in (data or []) if isinstance(row, dict)]
    if not rows:
        raise VendorUnavailable("empty payload")
    return rows


def normalize_candles(rows: Any) -> List[Dict[str, Any]]:
    """Vendor-OHLC -> `{index,timestamp,open,high,low,close,volume}`, Unix-Sekunden, sortiert."""
    out: List[Dict[str, Any]] = []
    if isinstance(rows, dict):
        rows = rows.get("ohlc") or rows.get("data") or []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp", row.get("time", row.get("ts", 0)))
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if ts > bp.TIMESTAMP_MS_THRESHOLD:
            ts /= 1000.0
        try:
            out.append({
                "timestamp": int(ts),
                "open": float(row.get("open", row.get("o", 0.0))),
                "high": float(row.get("high", row.get("h", 0.0))),
                "low": float(row.get("low", row.get("l", 0.0))),
                "close": float(row.get("close", row.get("c", 0.0))),
                "volume": float(row.get("volume", row.get("v", 0.0)) or 0.0),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda c: c["timestamp"])
    for i, row in enumerate(out):
        row["index"] = i
    return out


_bridge: Optional[VendorBridge] = None


def get_bridge(settings: Optional[ScraperSettings] = None) -> VendorBridge:
    global _bridge
    if _bridge is None or settings is not None:
        _bridge = VendorBridge(settings)
    return _bridge
