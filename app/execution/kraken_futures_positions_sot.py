"""
=========================================================
Datei:      app/execution/kraken_futures_positions_sot.py
Zweck:      Futures positions SoT — CLI paper/live positions, never invent.
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

logger = logging.getLogger("app.execution.kraken_futures_positions_sot")

DEFAULT_TTL_S = 3.0


@dataclass
class FuturesPositionsSnapshot:
    ok: bool
    available: bool
    positions: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    error: str = ""
    mode: str = ""
    argv: list = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "available": self.available,
            "positions": list(self.positions),
            "source": self.source,
            "error": self.error,
            "mode": self.mode,
            "ts": self.ts,
            "cli_offline": not self.available,
        }


_CACHE: Dict[str, FuturesPositionsSnapshot] = {}
_CACHE_MONO: Dict[str, float] = {}


def clear_futures_positions_cache() -> None:
    _CACHE.clear()
    _CACHE_MONO.clear()


def _extract_json(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def parse_futures_positions_stdout(stdout: str) -> List[Dict[str, Any]]:
    """Parse ``kraken futures [paper] positions -o json`` → list of position dicts."""
    payload = _extract_json(stdout)
    if payload is None:
        return []
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("positions", "openPositions", "open_positions"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [p for p in rows if isinstance(p, dict)]
    return []


def normalize_pro_position(raw: Dict[str, Any], *, funding_rate: Optional[float] = None) -> Dict[str, Any]:
    """Map CLI futures position → Pro UI position shape (no invented funding)."""
    symbol = str(
        raw.get("symbol") or raw.get("pair") or raw.get("instrument") or ""
    ).strip()
    side_raw = str(raw.get("side") or raw.get("direction") or raw.get("type") or "").lower()
    if side_raw in {"long", "buy"}:
        pos_type = "long"
    elif side_raw in {"short", "sell"}:
        pos_type = "short"
    else:
        size_hint = raw.get("size") or raw.get("quantity") or raw.get("balance")
        try:
            pos_type = "long" if float(size_hint or 0) >= 0 else "short"
        except (TypeError, ValueError):
            pos_type = "long"

    def _f(*keys: str, default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            if raw.get(k) is not None:
                try:
                    return float(raw[k])
                except (TypeError, ValueError):
                    continue
        return default

    size = abs(_f("size", "quantity", "balance", "qty", default=0.0) or 0.0)
    entry = _f("entryPrice", "entry_price", "avgEntryPrice", "price", default=0.0) or 0.0
    mark = _f("markPrice", "mark_price", "indexPrice", "mktPrice", default=entry) or entry
    upnl = _f("unrealizedPnl", "unrealizedPnLUSD", "pnl", "unrealized_pnl", default=0.0) or 0.0
    collateral = _f(
        "collateral", "collateralUSD", "margin", "initialMargin", "margin_usd", default=0.0
    ) or 0.0
    leverage = _f("leverage", "effectiveLeverage", default=1.0) or 1.0
    notional = _f("notionalValueUSD", "notional", "value", default=None)
    if notional is None:
        notional = size * mark if mark else 0.0
    liq = _f("liquidationPrice", "liquidation_price", "liqPrice", default=None)
    fr = funding_rate
    if fr is None and raw.get("fundingRate") is not None:
        try:
            fr = float(raw["fundingRate"])
        except (TypeError, ValueError):
            fr = None

    return {
        "id": str(raw.get("id") or raw.get("position_id") or symbol or ""),
        "pair": symbol,
        "type": pos_type,
        "contractType": str(raw.get("contractType") or raw.get("tag") or "perpetual"),
        "size": round(size, 8),
        "notionalValueUSD": round(float(notional or 0.0), 2),
        "leverage": float(leverage),
        "entryPrice": round(entry, 6),
        "markPrice": round(mark, 6),
        "liquidationPrice": liq,
        "collateralUSD": round(collateral, 2),
        "marginRequirementUSD": round(collateral, 2),
        "unrealizedPnLUSD": round(upnl, 4),
        "unrealizedPnLPercent": round(
            (upnl / collateral * 100.0) if collateral else 0.0, 2
        ),
        "fundingRate": fr,  # None when CLI does not provide — never fake 0.01
        "status": str(raw.get("status") or "open"),
        "_raw": raw,
    }


def fetch_futures_positions(
    bridge: Optional[KrakenCliBridge],
    *,
    ttl_s: float = DEFAULT_TTL_S,
    force: bool = False,
    paper: Optional[bool] = None,
) -> FuturesPositionsSnapshot:
    """Read futures positions from CLI. Paper vs live from bridge or ``paper=``."""
    use_paper = bool(getattr(bridge, "paper_mode", False) if paper is None else paper)
    cache_key = "paper" if use_paper else "live"
    now = time.monotonic()
    if (
        not force
        and cache_key in _CACHE
        and (now - _CACHE_MONO.get(cache_key, 0.0)) < max(0.0, ttl_s)
    ):
        return _CACHE[cache_key]

    if bridge is None:
        snap = FuturesPositionsSnapshot(
            ok=False, available=False, error="bridge_unavailable", source="",
        )
        _CACHE[cache_key] = snap
        _CACHE_MONO[cache_key] = now
        return snap

    leaf = "futures/paper/positions" if use_paper else "futures/positions"
    try:
        if use_paper:
            argv = [bridge.binary, "futures", "paper", "positions"] + bridge._json_flag()
            res = bridge._run_paper_read(argv)
        else:
            # Live read — no LIVE_APPROVED required (query only).
            argv = [bridge.binary, "futures", "positions"] + bridge._json_flag()
            if not bridge._cli_available():
                res = type("R", (), {
                    "ok": False, "stdout": "", "argv": argv,
                    "error_code": "ERR_KRAKEN_CLI_NOT_FOUND",
                })()
            else:
                stdout, stderr, code = bridge._runner(
                    argv, bridge.config.tv_scraper_timeout_s
                )
                from app.core import blueprint as bp
                from app.execution.KrakenCliBridge import OrderResult, _extract_error

                failed = bp.kraken_output_is_error(stdout, stderr, code)
                res = OrderResult(
                    not failed, "live",
                    stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
                    error_code=_extract_error(stdout, stderr) if failed else "",
                )
    except Exception as exc:  # pragma: no cover
        snap = FuturesPositionsSnapshot(
            ok=False, available=False, error=str(exc), source=leaf,
        )
        _CACHE[cache_key] = snap
        _CACHE_MONO[cache_key] = now
        return snap

    if not getattr(res, "ok", False):
        snap = FuturesPositionsSnapshot(
            ok=False,
            available=bool(getattr(bridge, "_cli_available", lambda: False)()),
            error=getattr(res, "error_code", None) or "positions_failed",
            source=leaf,
            mode=cache_key,
            argv=list(getattr(res, "argv", []) or []),
        )
        _CACHE[cache_key] = snap
        _CACHE_MONO[cache_key] = now
        return snap

    raw_rows = parse_futures_positions_stdout(res.stdout)
    positions = [normalize_pro_position(r) for r in raw_rows]
    for p in positions:
        p.pop("_raw", None)

    snap = FuturesPositionsSnapshot(
        ok=True,
        available=True,
        positions=positions,
        source=leaf,
        mode=cache_key,
        argv=list(getattr(res, "argv", []) or []),
    )
    _CACHE[cache_key] = snap
    _CACHE_MONO[cache_key] = now
    return snap
