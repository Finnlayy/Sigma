"""
=========================================================
Datei:      app/tv/symbol_map.py
Zweck:      §3.2 — Symbol-Mapping Sigma <-> TradingView <-> Kraken.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Schema-Standardisierung)
=========================================================
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from app.core import blueprint as bp

# Kanonische Basis-Aliase (Kraken nennt BTC "XBT")
_BASE_ALIASES: Dict[str, str] = {
    "BTC": "XBT", "XBT": "XBT", "ETH": "ETH", "SOL": "SOL", "XRP": "XRP",
    "ADA": "ADA", "DOT": "DOT", "LINK": "LINK", "AVAX": "AVAX", "DOGE": "XDG",
}

# Rückrichtung: Venue-Basis -> kanonische Sigma-Basis (XBT -> BTC, XDG -> DOGE)
_CANONICAL_BASE: Dict[str, str] = {
    "XBT": "BTC", "XDG": "DOGE", "BTC": "BTC", "ETH": "ETH", "SOL": "SOL",
    "XRP": "XRP", "ADA": "ADA", "DOT": "DOT", "LINK": "LINK", "AVAX": "AVAX",
}
_QUOTE_ALIASES: Dict[str, str] = {
    "USD": "USD", "USDT": "USD", "USDC": "USD", "EUR": "EUR",
}

# Vollständige Overrides aus config §9 (Spot + Futures)
TV_OVERRIDES: Dict[str, str] = {
    **{k: v for k, v in bp.EXCHANGE_SPOT["symbol_mappings"].items()},
    **{k: v for k, v in bp.EXCHANGE_FUTURES["symbol_mappings"].items()},
}


def split_pair(symbol: str) -> Tuple[str, str]:
    """`BTC/USD` | `BTCUSD` | `KRAKEN:XBTUSD` -> ('XBT', 'USD')."""
    raw = symbol.strip().upper()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    raw = raw.replace(".P", "").replace("PI_", "").replace("PF_", "")
    if "/" in raw:
        base, quote = raw.split("/", 1)
    else:
        for q in ("USDT", "USDC", "USD", "EUR"):
            if raw.endswith(q):
                base, quote = raw[: -len(q)], q
                break
        else:
            base, quote = raw, "USD"
    return _BASE_ALIASES.get(base, base), _QUOTE_ALIASES.get(quote, quote)


def to_kraken_pair(symbol: str) -> str:
    """Sigma-Symbol -> Kraken CLI `--pair` (z. B. `XBTUSD`)."""
    base, quote = split_pair(symbol)
    return f"{base}{quote}"


def to_sigma_symbol(pair: str) -> str:
    """Kraken-Pair -> kanonische Sigma-Form (`XBTUSD` -> `BTC/USD`,
    `PI_XBTUSD` -> `PI_BTC/USD`).

    Der Execution-Port darf nie rohe Venue-Ticker nach Scout/Academy
    durchreichen, sonst divergieren Loop C (Sigma-Form) und Loop A.
    """
    raw = pair.strip().upper()
    futures = raw.startswith("PI_") or raw.startswith("PF_")
    raw = raw.replace("PI_", "").replace("PF_", "").replace(".P", "")
    for q in ("USDT", "USDC", "USD", "EUR"):
        if raw.endswith(q):
            base, quote = raw[: -len(q)], q
            break
    else:
        base, quote = raw, "USD"
    base = _CANONICAL_BASE.get(base, base)
    return f"{'PI_' if futures else ''}{base}/{quote}"


def to_tradingview(symbol: str, *, exchange: str = "KRAKEN", futures: bool = False) -> str:
    """Sigma-Symbol -> TV-Ticker (`KRAKEN:XBTUSD`, Perp `KRAKEN:XBTUSD.P`)."""
    raw = symbol.strip().upper()
    if raw in TV_OVERRIDES:
        return TV_OVERRIDES[raw]
    base, quote = split_pair(symbol)
    suffix = ".P" if futures else ""
    return f"{exchange}:{base}{quote}{suffix}"


def to_scraper_ticker(symbol: str, *, exchange: str = "KRAKEN") -> Tuple[str, str]:
    """-> ('KRAKEN', 'XBTUSD') für `GET /api/ohlcv/{exchange}/{ticker}` (:8001)."""
    tv = to_tradingview(symbol, exchange=exchange)
    ex, _, ticker = tv.partition(":")
    return ex, ticker


def is_allowed(symbol: str, *, futures: bool = False) -> bool:
    """§9 allowed_symbols-Gate (Spot vs. Futures)."""
    pair = to_kraken_pair(symbol)
    if futures:
        return any(pair in s or s.endswith(pair) for s in bp.EXCHANGE_FUTURES["allowed_symbols"])
    return pair in bp.EXCHANGE_SPOT["allowed_symbols"]


def market_type(symbol: str) -> str:
    raw = symbol.upper()
    return "FUTURES" if raw.endswith(".P") or raw.startswith("PI_") else "SPOT"


def notional_limits(symbol: str) -> Dict[str, float]:
    cfg = bp.EXCHANGE_FUTURES if market_type(symbol) == "FUTURES" else bp.EXCHANGE_SPOT
    return {
        "max_order_notional_usd": float(cfg["max_order_notional_usd"]),
        "max_daily_notional_usd": float(cfg["max_daily_notional_usd"]),
        "max_leverage": float(cfg.get("max_leverage", 1)),
    }
