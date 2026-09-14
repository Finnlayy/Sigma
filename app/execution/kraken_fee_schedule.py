"""
=========================================================
Datei:      app/execution/kraken_fee_schedule.py
Zweck:      TTL cache for ``futures/feeschedules`` → maker/taker rates.
            Fail-open log to config defaults — never silent forever invent.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.execution.KrakenCliBridge import KrakenCliBridge

logger = logging.getLogger("app.execution.kraken_fee_schedule")

DEFAULT_TTL_S = 3600.0  # fee schedules change rarely


@dataclass
class FeeScheduleRates:
    maker_fee_rate: float
    taker_fee_rate: float
    source: str
    ok: bool
    schedule_name: str = ""
    error: str = ""


_CACHE: Optional[FeeScheduleRates] = None
_CACHE_MONO: float = 0.0
_LAST_FAIL_LOG: float = 0.0


def clear_fee_schedule_cache() -> None:
    global _CACHE, _CACHE_MONO
    _CACHE = None
    _CACHE_MONO = 0.0


def parse_feeschedules_stdout(
    stdout: str,
    *,
    prefer_name_substr: str = "Linear Multi-Collateral Rebate",
) -> Tuple[Optional[float], Optional[float], str]:
    """Parse CLI feeschedules JSON → (maker_rate, taker_rate, schedule_name).

    Kraken futures feeschedules express makerFee/takerFee in **percent**
    (0.02 = 2 bps = 0.0002 fraction). Convert to fraction for FeeEngine.
    """
    text = (stdout or "").strip()
    if not text:
        return None, None, ""
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
        return None, None, ""
    schedules = payload.get("feeSchedules") or payload.get("feeschedules") or []
    if not isinstance(schedules, list) or not schedules:
        return None, None, ""

    chosen = None
    for sched in schedules:
        if not isinstance(sched, dict):
            continue
        name = str(sched.get("name") or "")
        if prefer_name_substr.lower() in name.lower():
            chosen = sched
            break
    if chosen is None:
        chosen = next((s for s in schedules if isinstance(s, dict)), None)
    if chosen is None:
        return None, None, ""

    tiers = chosen.get("tiers") or []
    if not isinstance(tiers, list) or not tiers:
        return None, None, str(chosen.get("name") or "")
    # Lowest volume tier = base retail rate
    tier0 = tiers[0] if isinstance(tiers[0], dict) else {}
    try:
        maker_pct = float(tier0.get("makerFee"))
        taker_pct = float(tier0.get("takerFee"))
    except (TypeError, ValueError):
        return None, None, str(chosen.get("name") or "")
    # percent → fraction
    return maker_pct / 100.0, taker_pct / 100.0, str(chosen.get("name") or "")


def fetch_futures_fee_rates(
    bridge: Optional[KrakenCliBridge],
    *,
    ttl_s: float = DEFAULT_TTL_S,
    force: bool = False,
    fallback_maker: float = 0.0002,
    fallback_taker: float = 0.0005,
) -> FeeScheduleRates:
    """Refresh maker/taker from CLI. On failure: log (throttled) + return config fallback."""
    global _CACHE, _CACHE_MONO, _LAST_FAIL_LOG
    now = time.monotonic()
    if (
        not force
        and _CACHE is not None
        and _CACHE.ok
        and (now - _CACHE_MONO) < max(0.0, ttl_s)
    ):
        return _CACHE

    if bridge is None:
        rates = FeeScheduleRates(
            maker_fee_rate=fallback_maker, taker_fee_rate=fallback_taker,
            source="config_fallback", ok=False, error="bridge_unavailable",
        )
        _log_fail_open(rates.error)
        _CACHE, _CACHE_MONO = rates, now
        return rates

    res = bridge.run_leaf("futures/feeschedules", confirmed=False, json_output=True)
    if not res.ok:
        rates = FeeScheduleRates(
            maker_fee_rate=fallback_maker, taker_fee_rate=fallback_taker,
            source="config_fallback", ok=False,
            error=res.error_code or "feeschedules_failed",
        )
        _log_fail_open(rates.error)
        _CACHE, _CACHE_MONO = rates, now
        return rates

    maker, taker, name = parse_feeschedules_stdout(res.stdout)
    if maker is None or taker is None:
        rates = FeeScheduleRates(
            maker_fee_rate=fallback_maker, taker_fee_rate=fallback_taker,
            source="config_fallback", ok=False, error="feeschedules_parse_empty",
        )
        _log_fail_open(rates.error)
        _CACHE, _CACHE_MONO = rates, now
        return rates

    rates = FeeScheduleRates(
        maker_fee_rate=float(maker), taker_fee_rate=float(taker),
        source="futures/feeschedules", ok=True, schedule_name=name,
    )
    _CACHE, _CACHE_MONO = rates, now
    return rates


def _log_fail_open(error: str) -> None:
    global _LAST_FAIL_LOG
    now = time.monotonic()
    if now - _LAST_FAIL_LOG < 300.0:
        return
    _LAST_FAIL_LOG = now
    logger.warning(
        "futures feeschedules unavailable (%s) — using config fee rates (fail-open, not silent)",
        error,
    )
