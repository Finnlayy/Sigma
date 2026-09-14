"""
=========================================================
Datei:      app/execution/kraken_paper_sot.py
Zweck:      Single-Book capital SoT — parse CLI paper status|balance
            (spot ``paper`` or futures ``futures paper``).
            Never invent balances. Fail-closed when CLI offline.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core import blueprint as bp
from app.execution.KrakenCliBridge import (
    KrakenCliBridge,
    normalize_kraken_asset,
    parse_balance_stdout,
)

logger = logging.getLogger("app.execution.kraken_paper_sot")

# Short cache so UI polls (5–12s) do not hammer the CLI.
DEFAULT_TTL_S = 3.0


@dataclass
class PaperCapitalSnapshot:
    ok: bool
    available: bool
    balances: Dict[str, float] = field(default_factory=dict)
    current_value: Optional[float] = None
    starting_balance: Optional[float] = None
    starting_currency: str = "USD"
    unrealized_pnl: Optional[float] = None
    total_trades: Optional[int] = None
    fee_rate: Optional[float] = None
    slippage_rate: Optional[float] = None
    workspace: str = ""
    error: str = ""
    source: str = ""  # "paper_status" | "futures_paper_status" | …
    book: str = "spot"  # "spot" | "futures"
    ts: float = field(default_factory=time.time)
    argv: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "available": self.available,
            "balances": dict(self.balances),
            "current_value": self.current_value,
            "starting_balance": self.starting_balance,
            "starting_currency": self.starting_currency,
            "unrealized_pnl": self.unrealized_pnl,
            "total_trades": self.total_trades,
            "fee_rate": self.fee_rate,
            "slippage_rate": self.slippage_rate,
            "workspace": self.workspace,
            "error": self.error,
            "source": self.source,
            "book": self.book,
            "ts": self.ts,
            "cli_offline": not self.available,
        }


_CACHE: Dict[str, PaperCapitalSnapshot] = {}
_CACHE_MONO: Dict[str, float] = {}


def clear_paper_capital_cache() -> None:
    _CACHE.clear()
    _CACHE_MONO.clear()


def parse_paper_status_stdout(stdout: str) -> Dict[str, Any]:
    """Parse ``kraken [futures] paper status -o json`` into a flat capital dict.

    Spot fields: current_value, starting_balance, …
    Futures fields: equity, starting_collateral, collateral, pnl → normalized.
    """
    text = (stdout or "").strip()
    if not text:
        return {}
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
        return {}

    # Normalize futures paper status onto spot-shaped keys used by UI/Lab.
    mode = str(payload.get("mode") or "")
    if "futures" in mode or (
        "equity" in payload and "current_value" not in payload
    ):
        out = dict(payload)
        if out.get("current_value") is None and out.get("equity") is not None:
            out["current_value"] = out["equity"]
        if out.get("starting_balance") is None:
            if out.get("starting_collateral") is not None:
                out["starting_balance"] = out["starting_collateral"]
            elif out.get("collateral") is not None:
                out["starting_balance"] = out["collateral"]
        if not out.get("starting_currency") and out.get("currency"):
            out["starting_currency"] = out["currency"]
        if out.get("unrealized_pnl") is None and out.get("pnl") is not None:
            out["unrealized_pnl"] = out["pnl"]
        if out.get("total_trades") is None and out.get("total_fills") is not None:
            out["total_trades"] = out["total_fills"]
        return out
    return payload


def parse_paper_balance_stdout(stdout: str) -> Dict[str, float]:
    """Parse ``kraken [futures] paper balance -o json`` → {USD: …, BTC: …}.

    Spot shape::
        {"balances": {"BTC": {"available": "…", "total": "…"}, …}, "mode": "paper"}

    Futures paper shape::
        {"available_margin": …, "collateral": …, "currency": "USD", …}
    """
    text = (stdout or "").strip()
    if not text:
        return {}
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
        return parse_balance_stdout(stdout)

    if isinstance(payload.get("balances"), dict):
        out: Dict[str, float] = {}
        for key, value in payload["balances"].items():
            asset = normalize_kraken_asset(str(key))
            if not asset:
                continue
            if isinstance(value, dict):
                raw = value.get("total")
                if raw is None:
                    raw = value.get("available")
                if raw is None:
                    raw = value.get("balance")
            else:
                raw = value
            try:
                out[asset] = out.get(asset, 0.0) + float(str(raw).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return out

    # Futures paper balance: single collateral currency
    mode = str(payload.get("mode") or "")
    if "futures" in mode or "collateral" in payload or "available_margin" in payload:
        ccy = normalize_kraken_asset(str(payload.get("currency") or "USD")) or "USD"
        for key in ("collateral", "available_margin", "equity"):
            if payload.get(key) is not None:
                try:
                    return {ccy: float(payload[key])}
                except (TypeError, ValueError):
                    continue

    return parse_balance_stdout(stdout)


def fetch_paper_capital(
    bridge: Optional[KrakenCliBridge],
    *,
    ttl_s: float = DEFAULT_TTL_S,
    force: bool = False,
    futures: Optional[bool] = None,
) -> PaperCapitalSnapshot:
    """Read paper capital from CLI. Never returns homemade seed balances.

    ``futures=True`` forces the futures paper book even if bridge.futures is False.
    ``futures=None`` follows ``bridge.futures``.
    """
    use_futures = bool(getattr(bridge, "futures", False) if futures is None else futures)
    book = "futures" if use_futures else "spot"
    now = time.monotonic()
    if (
        not force
        and book in _CACHE
        and (now - _CACHE_MONO.get(book, 0.0)) < max(0.0, ttl_s)
    ):
        return _CACHE[book]

    if bridge is None:
        snap = PaperCapitalSnapshot(
            ok=False, available=False, error="bridge_unavailable", source="", book=book,
        )
        _CACHE[book] = snap
        _CACHE_MONO[book] = now
        return snap

    # Prefer a bridge whose futures flag matches the requested book.
    # Non-KrakenCliBridge fakes (tests) are used as-is — they own paper_status.
    status_bridge = bridge
    if isinstance(bridge, KrakenCliBridge):
        if use_futures and not bridge.futures:
            status_bridge = KrakenCliBridge(
                getattr(bridge, "config", None),
                telemetry=getattr(bridge, "telemetry", None),
                runner=getattr(bridge, "_runner", None),
                binary=getattr(bridge, "binary", None),
                execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
                futures=True,
            )
        elif not use_futures and bridge.futures:
            status_bridge = KrakenCliBridge(
                getattr(bridge, "config", None),
                telemetry=getattr(bridge, "telemetry", None),
                runner=getattr(bridge, "_runner", None),
                binary=getattr(bridge, "binary", None),
                execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
                futures=False,
            )

    try:
        status_res = status_bridge.paper_status()
    except AttributeError:
        snap = PaperCapitalSnapshot(
            ok=False, available=False, error="bridge_missing_paper_status",
            source="", book=book,
        )
        _CACHE[book] = snap
        _CACHE_MONO[book] = now
        return snap
    if not status_res.ok:
        snap = PaperCapitalSnapshot(
            ok=False,
            available=bool(getattr(status_bridge, "_cli_available", lambda: False)()),
            error=status_res.error_code or "paper_status_failed",
            source="futures_paper_status" if use_futures else "paper_status",
            book=book,
            argv=list(status_res.argv),
        )
        _CACHE[book] = snap
        _CACHE_MONO[book] = now
        return snap

    status = parse_paper_status_stdout(status_res.stdout)
    try:
        bal_res = status_bridge.paper_balance()
    except AttributeError:
        bal_res = type("R", (), {"ok": False, "stdout": "", "argv": []})()
    balances: Dict[str, float] = {}
    if getattr(bal_res, "ok", False):
        balances = parse_paper_balance_stdout(bal_res.stdout)

    current_value = status.get("current_value")
    try:
        current_value_f = float(current_value) if current_value is not None else None
    except (TypeError, ValueError):
        current_value_f = None

    if not balances and current_value_f is not None:
        ccy = str(status.get("starting_currency") or status.get("currency") or "USD").upper()
        balances = {normalize_kraken_asset(ccy) or "USD": current_value_f}

    starting = status.get("starting_balance")
    try:
        starting_f = float(starting) if starting is not None else None
    except (TypeError, ValueError):
        starting_f = None

    upnl = status.get("unrealized_pnl")
    try:
        upnl_f = float(upnl) if upnl is not None else None
    except (TypeError, ValueError):
        upnl_f = None

    trades = status.get("total_trades")
    try:
        trades_i = int(trades) if trades is not None else None
    except (TypeError, ValueError):
        trades_i = None

    fee_rate = status.get("fee_rate")
    try:
        fee_rate_f = float(fee_rate) if fee_rate is not None else None
    except (TypeError, ValueError):
        fee_rate_f = None

    slip = status.get("slippage_rate")
    try:
        slip_f = float(slip) if slip is not None else None
    except (TypeError, ValueError):
        slip_f = None

    src = (
        ("futures_paper_status+balance" if bal_res.ok else "futures_paper_status")
        if use_futures
        else ("paper_status+paper_balance" if bal_res.ok else "paper_status")
    )
    snap = PaperCapitalSnapshot(
        ok=True,
        available=True,
        balances=balances,
        current_value=current_value_f,
        starting_balance=starting_f,
        starting_currency=str(status.get("starting_currency") or status.get("currency") or "USD"),
        unrealized_pnl=upnl_f,
        total_trades=trades_i,
        fee_rate=fee_rate_f,
        slippage_rate=slip_f,
        workspace=str(status.get("workspace") or ""),
        source=src,
        book=book,
        argv=list(status_res.argv),
    )
    _CACHE[book] = snap
    _CACHE_MONO[book] = now
    return snap
