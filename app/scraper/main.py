"""
=========================================================
Datei:      app/scraper/main.py
Zweck:      §6 Loop C — Scraper-Sidecar auf :8001.
            Implementiert exakt `bp.SCRAPER_ENDPOINTS` über
            den vendored `tradingview-scraper` und ergänzt
            TTL-Cache, Token-Bucket, Retry, Stale-Serve und
            einen deterministischen Offline-Fallback.
Start:      uvicorn app.scraper.main:app --host 127.0.0.1 --port 8001
            (bzw. `bin/sigma-scraper`)
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Datenabfrage)
=========================================================
"""
from __future__ import annotations

import csv
import io
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.core import blueprint as bp
from app.scraper.cache import TTLCache, TokenBucket, cache_key
from app.scraper.settings import get_settings
from app.scraper import synthetic
from app.scraper.vendor_bridge import VendorUnavailable, get_bridge
from app.tv.interval_map import to_minutes, to_scraper_timeframe

logger = logging.getLogger("app.scraper")

SETTINGS = get_settings()
CACHE = TTLCache(max_entries=SETTINGS.cache_max_entries)
BUCKET = TokenBucket(SETTINGS.rate_limit_per_min, SETTINGS.rate_limit_burst)
BRIDGE = get_bridge(SETTINGS)
STARTED_AT = time.time()

SOURCE_VENDOR = "tv_scraper"
SOURCE_CACHE = "cache_stale"
SOURCE_SYNTHETIC = "synthetic"

app = FastAPI(
    title="Sigma Scraper Sidecar",
    description="Loop C market feed — vendored tradingview-scraper with cache, "
                "rate-limit and deterministic offline fallback.",
    version=bp.BLUEPRINT_VERSION,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=SETTINGS.cors_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# =============================================================================
# Interne Helfer
# =============================================================================

def _rate_limit() -> None:
    if not BUCKET.acquire():
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMITED", "retry_after_s": BUCKET.retry_after_s()},
            headers={"Retry-After": str(int(BUCKET.retry_after_s()) + 1)},
        )


def _envelope(data: Any, source: str, *, cached: bool = False, **extra: Any) -> Dict[str, Any]:
    payload = {
        "status": "success",
        "source": source,
        "cached": cached,
        "served_at": int(time.time()),
        "data": data,
    }
    payload.update(extra)
    return payload


def _resolve(kind: str, key: str, ttl_s: float, fetch, fallback) -> Dict[str, Any]:
    """Cache -> Vendor -> Stale-Cache -> Synthetic. Immer mit `source`-Marker.

    Der Cache speichert `{"source": ..., "data": ...}`, damit ein synthetischer
    Treffer beim naechsten Request nicht faelschlich als Vendor-Daten gilt.
    """
    hit = CACHE.get(key, ttl_s)
    if isinstance(hit, dict) and "data" in hit:
        return _envelope(hit["data"], hit["source"], cached=True,
                         age_s=round(CACHE.age_of(key) or 0.0, 2),
                         degraded=hit["source"] != SOURCE_VENDOR)

    if not SETTINGS.offline:
        _rate_limit()
        try:
            data = fetch()
            CACHE.set(key, {"source": SOURCE_VENDOR, "data": data})
            return _envelope(data, SOURCE_VENDOR)
        except VendorUnavailable as exc:
            logger.warning("%s upstream unavailable: %s", kind, exc)
            stale = CACHE.get_stale(key)
            if isinstance(stale, dict) and stale.get("source") == SOURCE_VENDOR:
                return _envelope(stale["data"], SOURCE_CACHE, cached=True,
                                 age_s=round(CACHE.age_of(key) or 0.0, 2),
                                 degraded=True, upstream_error=str(exc))
            if not SETTINGS.allow_synthetic_fallback:
                raise HTTPException(status_code=503, detail={
                    "code": "SCRAPER_UPSTREAM_UNAVAILABLE", "kind": kind, "error": str(exc)})

    data = fallback()
    CACHE.set(key, {"source": SOURCE_SYNTHETIC, "data": data})
    return _envelope(data, SOURCE_SYNTHETIC, degraded=True)


# =============================================================================
# Health / Diagnostics
# =============================================================================

@app.get("/health")
def health() -> Dict[str, Any]:
    """Von `TradingViewScraperClient.health()` und `/api/v1/health` (Core) gepollt."""
    vendor = BRIDGE.status()
    return {
        "status": "ok",
        "service": "sigma-tv-scraper",
        "port": SETTINGS.port,
        "uptime_s": round(time.time() - STARTED_AT, 2),
        "vendor": vendor,
        "degraded": SETTINGS.offline or not vendor["importable"],
        "cache": {"entries": len(CACHE), **CACHE.stats.as_dict()},
        "rate_limit": BUCKET.snapshot(),
        "endpoints": dict(bp.SCRAPER_ENDPOINTS),
    }


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "sigma-tv-scraper",
        "loop": "C",
        "vendor": "tradingview-scraper (MIT)",
        "endpoints": dict(bp.SCRAPER_ENDPOINTS),
        "health": "/health",
    }


@app.get("/api/config")
def config() -> Dict[str, Any]:
    return {"settings": SETTINGS.as_dict(), "vendor": BRIDGE.status()}


@app.post("/api/cache/clear")
def cache_clear() -> Dict[str, Any]:
    return {"cleared": CACHE.clear()}


# =============================================================================
# Symbol-Endpunkte (bp.SCRAPER_ENDPOINTS)
# =============================================================================

@app.get("/api/ohlcv/{exchange}/{ticker}")
def get_ohlcv(
    exchange: str,
    ticker: str,
    timeframe: str = Query("1d", description="1m|5m|15m|1h|4h|1d|1w"),
    candles: int = Query(100, ge=1),
) -> Dict[str, Any]:
    """Historische Kerzen. Antwort: `data:[{index,timestamp,open,high,low,close,volume}]`."""
    count = min(candles, SETTINGS.max_candles)
    tf = to_scraper_timeframe(timeframe)
    minutes = to_minutes(tf)
    key = cache_key("ohlcv", exchange, ticker, tf, count)

    out = _resolve(
        "ohlcv", key, SETTINGS.ohlcv_ttl_s,
        fetch=lambda: BRIDGE.ohlcv(exchange, ticker, tf, count),
        fallback=lambda: synthetic.candles(exchange, ticker, minutes, count),
    )
    rows: List[Dict[str, Any]] = out["data"]
    out.update({
        "exchange": exchange.upper(),
        "ticker": ticker.upper(),
        "timeframe": tf,
        "interval_minutes": minutes,
        "total": len(rows),
        "ohlc": rows,  # Vendor-Alias — `TradingViewScraperClient` liest beide
    })
    return out


@app.get("/api/indicators/{exchange}/{ticker}")
def get_indicators(exchange: str, ticker: str, timeframe: str = "1d") -> Dict[str, Any]:
    tf = to_scraper_timeframe(timeframe)
    key = cache_key("indicators", exchange, ticker, tf)
    out = _resolve(
        "indicators", key, SETTINGS.meta_ttl_s,
        fetch=lambda: BRIDGE.indicators(exchange, ticker, tf),
        fallback=lambda: synthetic.indicators(exchange, ticker, tf),
    )
    out["indicator"] = out["data"]  # Vendor-Alias
    out["timeframe"] = tf
    return out


@app.get("/api/overview/{exchange}/{ticker}")
def get_overview(exchange: str, ticker: str) -> Dict[str, Any]:
    key = cache_key("overview", exchange, ticker)
    return _resolve(
        "overview", key, SETTINGS.meta_ttl_s,
        fetch=lambda: BRIDGE.overview(exchange, ticker),
        fallback=lambda: synthetic.overview(exchange, ticker),
    )


@app.get("/api/fundamentals/{exchange}/{ticker}")
def get_fundamentals(exchange: str, ticker: str) -> Dict[str, Any]:
    key = cache_key("fundamentals", exchange, ticker)
    return _resolve(
        "fundamentals", key, SETTINGS.meta_ttl_s,
        fetch=lambda: BRIDGE.fundamentals(exchange, ticker),
        fallback=lambda: {"symbol": f"{exchange.upper()}:{ticker.upper()}", "fundamentals": {}},
    )


# =============================================================================
# Markt-Endpunkte
# =============================================================================

@app.get("/api/movers")
def get_movers(
    market: str = "crypto",
    category: str = "gainers",
    limit: int = Query(25, ge=1, le=250),
) -> Dict[str, Any]:
    key = cache_key("movers", market, category, limit)
    out = _resolve(
        "movers", key, SETTINGS.market_ttl_s,
        fetch=lambda: BRIDGE.movers(market, category, limit),
        fallback=lambda: synthetic.movers(market, category, limit),
    )
    out.update({"market": market, "category": category, "total": len(out["data"])})
    return out


@app.get("/api/screener")
def get_screener(
    market: str = "crypto",
    sort_by: str = "volume",
    sort_order: str = "desc",
    limit: int = Query(25, ge=1, le=250),
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_volume: Optional[float] = None,
    min_change: Optional[float] = None,
    max_change: Optional[float] = None,
) -> Dict[str, Any]:
    filters: List[Dict[str, Any]] = []
    for value, left, op in (
        (min_price, "close", "egreater"), (max_price, "close", "eless"),
        (min_volume, "volume", "egreater"), (min_change, "change", "egreater"),
        (max_change, "change", "eless"),
    ):
        if value is not None:
            filters.append({"left": left, "operation": op, "right": value})

    key = cache_key("screener", market, sort_by, sort_order, limit, json.dumps(filters, sort_keys=True))
    out = _resolve(
        "screener", key, SETTINGS.market_ttl_s,
        fetch=lambda: BRIDGE.screener(market, sort_by, sort_order, limit, filters),
        fallback=lambda: synthetic.screener(market, sort_by, sort_order, limit),
    )
    out.update({"market": market, "filters": filters, "total": len(out["data"])})
    return out


# =============================================================================
# Download-Helfer (`/api/download/*`, §6 "CSV für Offline")
# =============================================================================

def _rows_to_csv(rows: List[Dict[str, Any]], filename: str) -> StreamingResponse:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                             for k, v in row.items()})
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


def _payload_download(rows: Any, filename: str, fmt: str) -> Response:
    if fmt == "json":
        return Response(content=json.dumps(rows, indent=2, default=str),
                        media_type="application/json",
                        headers={"Content-Disposition": f"attachment; filename={filename}.json"})
    if isinstance(rows, dict):
        rows = [{"field": k, "value": v} for k, v in rows.items()]
    return _rows_to_csv(rows, f"{filename}.csv")


@app.get("/api/download/ohlcv/{exchange}/{ticker}")
def download_ohlcv(exchange: str, ticker: str, timeframe: str = "1d",
                   candles: int = 100, fmt: str = "csv") -> Response:
    payload = get_ohlcv(exchange, ticker, timeframe, candles)
    return _payload_download(payload["data"], f"{exchange}_{ticker}_{timeframe}_ohlcv", fmt)


@app.get("/api/download/indicators/{exchange}/{ticker}")
def download_indicators(exchange: str, ticker: str, timeframe: str = "1d", fmt: str = "csv") -> Response:
    payload = get_indicators(exchange, ticker, timeframe)
    return _payload_download(payload["data"], f"{exchange}_{ticker}_indicators", fmt)


@app.get("/api/download/overview/{exchange}/{ticker}")
def download_overview(exchange: str, ticker: str, fmt: str = "csv") -> Response:
    payload = get_overview(exchange, ticker)
    return _payload_download(payload["data"], f"{exchange}_{ticker}_overview", fmt)


@app.get("/api/download/movers")
def download_movers(market: str = "crypto", category: str = "gainers",
                    limit: int = 50, fmt: str = "csv") -> Response:
    payload = get_movers(market, category, limit)
    return _payload_download(payload["data"], f"{market}_{category}_movers", fmt)


@app.get("/api/download/screener")
def download_screener(market: str = "crypto", sort_by: str = "volume",
                      sort_order: str = "desc", limit: int = 50, fmt: str = "csv") -> Response:
    payload = get_screener(market, sort_by, sort_order, limit)
    return _payload_download(payload["data"], f"{market}_screener", fmt)


# =============================================================================
# Optional: das unveränderte Vendor-API unter /vendor mitservieren
# =============================================================================

def _mount_vendor() -> bool:
    if not SETTINGS.mount_vendor_app or SETTINGS.offline:
        return False
    try:
        BRIDGE.ensure_path()
        from api.main import app as vendor_app  # type: ignore

        app.mount("/vendor", vendor_app)
        logger.info("vendor app mounted at /vendor")
        return True
    except Exception as exc:  # pragma: no cover - optional convenience
        logger.info("vendor app not mounted (%s)", exc)
        return False


VENDOR_MOUNTED = _mount_vendor()


def run() -> None:  # pragma: no cover - CLI entry
    import uvicorn

    uvicorn.run("app.scraper.main:app", host=SETTINGS.host, port=SETTINGS.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    run()
