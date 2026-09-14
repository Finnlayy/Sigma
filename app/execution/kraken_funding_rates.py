"""
=========================================================
Datei:      app/execution/kraken_funding_rates.py
Zweck:      ``futures/historical-funding-rates`` → latest relative rate / estimate.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.execution.KrakenCliBridge import KrakenCliBridge

logger = logging.getLogger("app.execution.kraken_funding_rates")

DEFAULT_TTL_S = 300.0


@dataclass
class FundingRateSnapshot:
    ok: bool
    symbol: str
    funding_rate: Optional[float] = None          # absolute CLI fundingRate (not invented)
    relative_funding_rate: Optional[float] = None  # fraction preferred for fee math
    rates: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    error: str = ""
    ts: float = field(default_factory=time.time)


_CACHE: Dict[str, FundingRateSnapshot] = {}
_CACHE_MONO: Dict[str, float] = {}


def clear_funding_rate_cache() -> None:
    _CACHE.clear()
    _CACHE_MONO.clear()


def parse_historical_funding_stdout(stdout: str) -> List[Dict[str, Any]]:
    text = (stdout or "").strip()
    if not text:
        return []
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
        return []
    rates = payload.get("rates")
    if isinstance(rates, list):
        return [r for r in rates if isinstance(r, dict)]
    return []


def fetch_latest_funding_rate(
    bridge: Optional[KrakenCliBridge],
    symbol: str,
    *,
    ttl_s: float = DEFAULT_TTL_S,
    force: bool = False,
) -> FundingRateSnapshot:
    """Latest historical funding row for symbol (PF_* preferred; PI_* accepted)."""
    sym = (symbol or "").strip().upper()
    if sym.startswith("PI_"):
        sym = "PF_" + sym[3:]
    now = time.monotonic()
    if (
        not force
        and sym in _CACHE
        and (now - _CACHE_MONO.get(sym, 0.0)) < max(0.0, ttl_s)
    ):
        return _CACHE[sym]

    if bridge is None or not sym:
        snap = FundingRateSnapshot(
            ok=False, symbol=sym, error="bridge_or_symbol_missing", source="",
        )
        _CACHE[sym] = snap
        _CACHE_MONO[sym] = now
        return snap

    res = bridge.run_leaf(
        "futures/historical-funding-rates", sym, confirmed=False, json_output=True,
    )
    if not res.ok:
        snap = FundingRateSnapshot(
            ok=False, symbol=sym,
            error=res.error_code or "funding_rates_failed",
            source="futures/historical-funding-rates",
        )
        _CACHE[sym] = snap
        _CACHE_MONO[sym] = now
        return snap

    rows = parse_historical_funding_stdout(res.stdout)
    if not rows:
        snap = FundingRateSnapshot(
            ok=False, symbol=sym, error="funding_rates_empty",
            source="futures/historical-funding-rates", rates=[],
        )
        _CACHE[sym] = snap
        _CACHE_MONO[sym] = now
        return snap

    latest = rows[-1]
    fr = None
    rel = None
    try:
        if latest.get("fundingRate") is not None:
            fr = float(latest["fundingRate"])
    except (TypeError, ValueError):
        fr = None
    try:
        if latest.get("relativeFundingRate") is not None:
            rel = float(latest["relativeFundingRate"])
    except (TypeError, ValueError):
        rel = None

    snap = FundingRateSnapshot(
        ok=True, symbol=sym, funding_rate=fr, relative_funding_rate=rel,
        rates=rows[-24:],  # keep a short window for callers
        source="futures/historical-funding-rates",
    )
    _CACHE[sym] = snap
    _CACHE_MONO[sym] = now
    return snap


def estimate_funding_fee_usd(
    notional_usd: float,
    relative_funding_rate: Optional[float],
    *,
    periods: int = 1,
) -> float:
    """Rough accumulated funding fee from relative rate × notional × periods."""
    if relative_funding_rate is None or notional_usd <= 0:
        return 0.0
    return float(notional_usd) * float(relative_funding_rate) * max(0, int(periods))
