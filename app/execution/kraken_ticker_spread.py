"""
=========================================================
Datei:      app/execution/kraken_ticker_spread.py
Zweck:      Pre-trade spread from CLI ``futures/ticker`` or spot ``ticker``.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

from app.execution.KrakenCliBridge import KrakenCliBridge

logger = logging.getLogger("app.execution.kraken_ticker_spread")

DEFAULT_TTL_S = 5.0
_CACHE: Dict[str, Tuple[float, float]] = {}  # key → (mono, spread_bps)


def clear_ticker_spread_cache() -> None:
    _CACHE.clear()


def parse_ticker_spread_bps(stdout: str) -> Optional[float]:
    """Extract mid-spread in bps from CLI ticker JSON."""
    text = (stdout or "").strip()
    if not text:
        return None
    payload = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    if not isinstance(payload, dict):
        return None

    ticker: Dict[str, Any] = payload
    if isinstance(payload.get("ticker"), dict):
        ticker = payload["ticker"]
    # Spot ticker may nest under result/pair
    if "a" in ticker and "b" in ticker and "ask" not in ticker:
        # classic kraken ticker arrays
        try:
            ask = float(ticker["a"][0]) if isinstance(ticker["a"], list) else float(ticker["a"])
            bid = float(ticker["b"][0]) if isinstance(ticker["b"], list) else float(ticker["b"])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
    else:
        try:
            ask = float(ticker.get("ask") if ticker.get("ask") is not None else ticker.get("a"))
            bid = float(ticker.get("bid") if ticker.get("bid") is not None else ticker.get("b"))
        except (TypeError, ValueError):
            return None
    mid = (ask + bid) / 2.0
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * 10_000.0


def fetch_spread_bps(
    bridge: Optional[KrakenCliBridge],
    pair: str,
    *,
    futures: bool = False,
    ttl_s: float = DEFAULT_TTL_S,
    force: bool = False,
    default_bps: float = 3.0,
) -> float:
    """Return spread_bps from CLI ticker; fall back to ``default_bps`` on failure."""
    key = f"{'fut' if futures else 'spot'}:{pair}"
    now = time.monotonic()
    if not force and key in _CACHE and (now - _CACHE[key][0]) < max(0.0, ttl_s):
        return _CACHE[key][1]

    if bridge is None or not pair:
        return default_bps

    leaf = "futures/ticker" if futures else "ticker"
    res = bridge.run_leaf(leaf, pair, confirmed=False, json_output=True)
    if not res.ok:
        logger.debug("ticker spread unavailable for %s: %s", pair, res.error_code)
        return default_bps
    bps = parse_ticker_spread_bps(res.stdout)
    if bps is None:
        return default_bps
    _CACHE[key] = (now, float(bps))
    return float(bps)
