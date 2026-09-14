"""
=========================================================
Datei:      app/execution/kraken_paper_history_mirror.py
Zweck:      DuckDB journal mirror from CLI ``paper history`` SoT.
            Never invents fills — only rows the CLI returns.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core import blueprint as bp
from app.execution.KrakenCliBridge import KrakenCliBridge

logger = logging.getLogger("app.execution.kraken_paper_history_mirror")

DEFAULT_TTL_S = 30.0

_CACHE_ROWS: Dict[str, List[Dict[str, Any]]] = {}
_CACHE_MONO: Dict[str, float] = {}


def clear_history_mirror_cache() -> None:
    _CACHE_ROWS.clear()
    _CACHE_MONO.clear()


def parse_paper_history_stdout(stdout: str) -> List[Dict[str, Any]]:
    """Parse ``kraken [futures] paper history|fills -o json`` → list of trade dicts."""
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
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("trades", "fills", "history", "executions"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [t for t in rows if isinstance(t, dict)]
    return []


def cli_trade_to_journal_row(
    raw: Dict[str, Any],
    *,
    market_type: str = "spot",
) -> Optional[Dict[str, Any]]:
    """Map one CLI history/fill row → DuckDB ``trades`` upsert shape (mirror only)."""
    trade_id = str(
        raw.get("id") or raw.get("fill_id") or raw.get("exec_id") or raw.get("trade_id") or ""
    ).strip()
    if not trade_id:
        return None
    pair = str(raw.get("pair") or raw.get("symbol") or "").strip()
    # XBTUSD / PF_XBTUSD → journal readability; keep raw if unknown.
    symbol = _normalize_pair(pair) if pair else ""
    side = str(raw.get("side") or raw.get("direction") or "").lower()
    status_raw = str(raw.get("status") or "filled").lower()
    status = "closed" if status_raw in {"filled", "closed", "canceled", "cancelled"} else "open"
    try:
        price = float(raw.get("price")) if raw.get("price") is not None else None
    except (TypeError, ValueError):
        price = None
    try:
        volume = float(
            raw.get("volume") if raw.get("volume") is not None
            else raw.get("size") if raw.get("size") is not None
            else raw.get("qty")
        ) if (raw.get("volume") is not None or raw.get("size") is not None
              or raw.get("qty") is not None) else None
    except (TypeError, ValueError):
        volume = None
    try:
        fee = float(raw.get("fee")) if raw.get("fee") is not None else None
    except (TypeError, ValueError):
        fee = None
    try:
        cost = float(raw.get("cost")) if raw.get("cost") is not None else None
    except (TypeError, ValueError):
        cost = None
    ts = raw.get("time") or raw.get("timestamp") or raw.get("fillTime")
    mkt = "futures" if market_type == "futures" else "spot"
    src = "kraken_futures_paper_history" if mkt == "futures" else "kraken_paper_history"
    return {
        "trade_id": trade_id,
        "instance_id": None,
        "strategy_id": "_cli_paper_mirror",
        "strategy_name": src,
        "symbol": symbol or pair,
        "execution_mode": bp.ExecutionMode.KRAKEN_PAPER.value,
        "market_type": mkt,
        "direction": side,
        "side": side,
        "status": status,
        "entry_time": ts,
        "exit_time": ts if status == "closed" else None,
        "entry_price": price,
        "exit_price": price if status == "closed" else None,
        "quantity": volume,
        "margin_usd": None,
        "leverage": 1.0,
        "notional_usd": cost,
        "gross_pnl_usd": None,  # single-leg fill — PnL not invented
        "fees_usd": fee,
        "funding_usd": None,
        "net_pnl_usd": None,
        "pnl_r": None,
        "mfe_r": None,
        "mae_r": None,
        "capture_ratio": None,
        "autopsy_zone": None,
        "exit_reason": f"cli_{src}_mirror",
        "stop_slippage_bps": None,
        "fee_hurdle_multiple": None,
        "hold_seconds": None,
        "_cli_order_id": raw.get("order_id"),
        "_source": src,
    }


def _normalize_pair(pair: str) -> str:
    p = pair.upper().replace("/", "")
    futures = p.startswith("PF_") or p.startswith("PI_")
    p = p.replace("PF_", "").replace("PI_", "")
    aliases = {
        "XBTUSD": "BTC/USD",
        "XXBTZUSD": "BTC/USD",
        "ETHUSD": "ETH/USD",
        "XETHZUSD": "ETH/USD",
        "SOLUSD": "SOL/USD",
    }
    if p in aliases:
        base = aliases[p]
        return f"PF_{base}" if futures else base
    if p.endswith("USD") and len(p) > 3:
        base = p[:-3]
        if base in {"XBT", "XXBT"}:
            base = "BTC"
        elif base.startswith("X") and len(base) == 4:
            base = base[1:]
        out = f"{base}/USD"
        return f"PF_{out}" if futures else out
    return pair


def fetch_paper_history_rows(
    bridge: Optional[KrakenCliBridge],
    *,
    ttl_s: float = DEFAULT_TTL_S,
    force: bool = False,
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Return (ok, raw_cli_trades, error). Cached briefly."""
    book = "futures" if getattr(bridge, "futures", False) else "spot"
    now = time.monotonic()
    if (
        not force
        and book in _CACHE_ROWS
        and (now - _CACHE_MONO.get(book, 0.0)) < max(0.0, ttl_s)
    ):
        return True, list(_CACHE_ROWS[book]), ""

    if bridge is None:
        return False, [], "bridge_unavailable"

    # Futures paper: prefer fills (trade SoT); fall back to history.
    if getattr(bridge, "futures", False):
        fills_argv = [bridge.binary, "futures", "paper", "fills"] + bridge._json_flag()
        fills_res = bridge._run_paper_read(fills_argv)
        if fills_res.ok:
            rows = parse_paper_history_stdout(fills_res.stdout)
            _CACHE_ROWS[book] = rows
            _CACHE_MONO[book] = now
            return True, rows, ""
        # fall through to paper history

    res = bridge.paper_history()
    if not res.ok:
        return False, [], res.error_code or "paper_history_failed"

    rows = parse_paper_history_stdout(res.stdout)
    _CACHE_ROWS[book] = rows
    _CACHE_MONO[book] = now
    return True, rows, ""


def mirror_paper_history_to_store(
    store: Any,
    bridge: Optional[KrakenCliBridge],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Upsert CLI paper history into DuckDB as journal mirror (not capital SoT)."""
    ok, rows, err = fetch_paper_history_rows(bridge, force=force)
    mkt = "futures" if getattr(bridge, "futures", False) else "spot"
    src = "futures_paper_fills" if mkt == "futures" else "paper_history"
    if not ok:
        return {"ok": False, "mirrored": 0, "error": err, "source": src}
    upsert = getattr(store, "upsert_trade", None)
    if upsert is None:
        return {"ok": False, "mirrored": 0, "error": "store_missing_upsert_trade"}
    mirrored = 0
    for raw in rows:
        journal = cli_trade_to_journal_row(raw, market_type=mkt)
        if journal is None:
            continue
        # Strip private helper keys before upsert
        row = {k: v for k, v in journal.items() if not k.startswith("_")}
        try:
            upsert(row)
            mirrored += 1
        except Exception as exc:  # pragma: no cover - store shape drift
            logger.warning("paper history mirror upsert failed for %s: %s",
                           journal.get("trade_id"), exc)
    return {
        "ok": True,
        "mirrored": mirrored,
        "cli_trades": len(rows),
        "error": "",
        "source": src,
    }
