"""
=========================================================
Datei:      app/scraper/synthetic.py
Zweck:      §6 — Deterministischer Offline-Feed. Greift nur,
            wenn der vendored Scraper/TradingView nicht
            erreichbar ist; jede Antwort wird als
            `source: "synthetic"` markiert, damit Loop C/D
            niemals synthetische Daten für Live-Entscheidungen
            hält (`SIGMA_MARKET_SOURCE` Dev-Flag, §6).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from typing import Any, Dict, List

BASE_PRICES: Dict[str, float] = {
    "XBTUSD": 64000.0, "BTCUSD": 64000.0, "ETHUSD": 3100.0, "SOLUSD": 148.0,
    "XRPUSD": 0.62, "ADAUSD": 0.45, "DOTUSD": 6.4, "LINKUSD": 14.2,
    "AVAXUSD": 27.0, "XDGUSD": 0.16,
}


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:12], 16)


def base_price(ticker: str) -> float:
    key = ticker.upper()
    if key in BASE_PRICES:
        return BASE_PRICES[key]
    return 10.0 + (_seed(key) % 9000) / 100.0


def candles(exchange: str, ticker: str, timeframe_minutes: int, count: int,
            *, now: float | None = None) -> List[Dict[str, Any]]:
    """Reproduzierbarer Random-Walk mit Regime-Wellen — gleiche Inputs, gleiche Kerzen."""
    now = now if now is not None else time.time()
    step = max(1, timeframe_minutes) * 60
    anchor = int(now // step) * step
    rng = random.Random(_seed(exchange, ticker, str(timeframe_minutes)))
    price = base_price(ticker)
    vol = price * 0.0035
    rows: List[Dict[str, Any]] = []
    t0 = anchor - (count - 1) * step
    for i in range(count):
        phase = i / 96.0
        drift = 0.0009 * math.sin(phase * math.tau) * price
        o = price
        c = max(price * 0.4, o + drift + rng.gauss(0, vol))
        h = max(o, c) * (1.0 + abs(rng.gauss(0, 0.0018)))
        low = min(o, c) * (1.0 - abs(rng.gauss(0, 0.0018)))
        v = abs(rng.gauss(1.0, 0.35)) * 120.0
        rows.append({
            "index": i,
            "timestamp": t0 + i * step,
            "open": round(o, 8),
            "high": round(h, 8),
            "low": round(low, 8),
            "close": round(c, 8),
            "volume": round(v, 4),
        })
        price = c
    return rows


def indicators(exchange: str, ticker: str, timeframe: str = "1d") -> Dict[str, Any]:
    rng = random.Random(_seed(exchange, ticker, timeframe, "ind"))
    price = base_price(ticker)
    return {
        "close": round(price, 4),
        "RSI": round(rng.uniform(28.0, 72.0), 2),
        "ATR": round(price * rng.uniform(0.004, 0.02), 6),
        "EMA50": round(price * rng.uniform(0.97, 1.03), 4),
        "EMA200": round(price * rng.uniform(0.90, 1.10), 4),
        "ADX": round(rng.uniform(10.0, 45.0), 2),
        "volume": round(rng.uniform(1e5, 1e7), 2),
        "Recommend.All": round(rng.uniform(-1.0, 1.0), 3),
    }


def overview(exchange: str, ticker: str) -> Dict[str, Any]:
    price = base_price(ticker)
    return {
        "symbol": f"{exchange.upper()}:{ticker.upper()}",
        "description": f"{ticker.upper()} synthetic overview",
        "type": "crypto",
        "exchange": exchange.upper(),
        "currency": "USD",
        "last_price": round(price, 4),
    }


def movers(market: str = "crypto", category: str = "gainers", limit: int = 25) -> List[Dict[str, Any]]:
    rng = random.Random(_seed(market, category, "movers"))
    universe = list(BASE_PRICES.keys())
    rows: List[Dict[str, Any]] = []
    for i in range(min(limit, len(universe) * 3)):
        ticker = universe[i % len(universe)]
        change = rng.uniform(0.5, 18.0) if category == "gainers" else -rng.uniform(0.5, 18.0)
        rows.append({
            "name": ticker,
            "close": round(base_price(ticker) * (1 + change / 100.0), 4),
            "change": round(change, 2),
            "volume": round(rng.uniform(1e5, 5e8), 2),
            "market": market,
        })
    rows.sort(key=lambda r: r["change"], reverse=(category != "losers"))
    return rows[:limit]


def screener(market: str = "crypto", sort_by: str = "volume", sort_order: str = "desc",
             limit: int = 25) -> List[Dict[str, Any]]:
    rows = movers(market=market, category="most-active", limit=limit)
    reverse = sort_order.lower() != "asc"
    key = sort_by if rows and sort_by in rows[0] else "volume"
    rows.sort(key=lambda r: r.get(key, 0.0), reverse=reverse)
    return rows
