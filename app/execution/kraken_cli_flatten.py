"""
=========================================================
Datei:      app/execution/kraken_cli_flatten.py
Zweck:      Emergency flatten via official kraken-cli only
            (recipe-emergency-flatten): cancel → positions →
            reduce-only/market closes → verify → one retry.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================

Not a second ledger — pure CLI orchestration. Success only when
re-read positions (and spot margin positions) are empty.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.execution.KrakenCliBridge import KrakenCliBridge, OrderResult

logger = logging.getLogger("app.execution.kraken_cli_flatten")

QUOTE_ASSETS = frozenset({"USD", "EUR", "USDT", "USDC", "ZUSD", "ZEUR", "DAI"})


def _pos_side_to_close(side_raw: str) -> str:
    s = (side_raw or "").lower()
    if s in {"short", "sell"}:
        return "buy"
    return "sell"


def _extract_positions(snapshot: Any) -> List[Dict[str, Any]]:
    if snapshot is None:
        return []
    if isinstance(snapshot, dict):
        if not snapshot.get("ok", True) and "positions" not in snapshot:
            return []
        rows = snapshot.get("positions") or []
        return [p for p in rows if isinstance(p, dict)]
    rows = getattr(snapshot, "positions", None) or []
    if not getattr(snapshot, "ok", True) and not rows:
        return []
    return [p for p in rows if isinstance(p, dict)]


def _position_size(raw: Dict[str, Any]) -> float:
    for key in ("size", "quantity", "balance", "vol", "amount"):
        try:
            v = abs(float(raw.get(key) or 0))
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return 0.0


def _position_symbol(raw: Dict[str, Any]) -> str:
    return str(
        raw.get("symbol") or raw.get("pair") or raw.get("instrument") or ""
    ).strip()


def flatten_futures_book(bridge: KrakenCliBridge, *, reason: str = "flatten") -> OrderResult:
    """Cancel futures orders, reduce-only close each position, verify empty."""
    notes: List[str] = []
    argv_acc: List[str] = []

    cancel_res = bridge.cancel_all(reason=f"{reason}:cancel_open")
    argv_acc.extend(list(cancel_res.argv or []))
    notes.append(f"cancel_all ok={cancel_res.ok}")
    if not cancel_res.ok and not bridge.paper_mode:
        return OrderResult(
            False,
            "live" if bridge.live_enabled else "sim",
            argv=argv_acc,
            error_code=cancel_res.error_code or "FLATTEN_CANCEL_FAILED",
            stderr=cancel_res.stderr,
            stdout="; ".join(notes),
        )

    def _close_round() -> Tuple[bool, List[str]]:
        from app.execution.kraken_futures_positions_sot import fetch_futures_positions

        snap = fetch_futures_positions(bridge, force=True)
        positions = _extract_positions(snap)
        if not positions:
            return True, ["positions_empty"]
        round_ok = True
        round_notes: List[str] = []
        for raw in positions:
            symbol = _position_symbol(raw)
            size = _position_size(raw)
            if not symbol or size <= 0:
                continue
            close_side = _pos_side_to_close(
                str(raw.get("side") or raw.get("direction") or "")
            )
            order = bridge.add_order(
                pair=symbol,
                side=close_side,
                volume=size,
                ordertype="market",
                strategy_id="deadman_flatten",
                reduce_only=True,
            )
            argv_acc.extend(list(order.argv or []))
            round_ok = round_ok and bool(order.ok)
            round_notes.append(f"{symbol}:{close_side}:{size:g} ok={order.ok}")
        return round_ok, round_notes

    ok1, n1 = _close_round()
    notes.extend(n1)
    # Verify
    from app.execution.kraken_futures_positions_sot import fetch_futures_positions

    snap = fetch_futures_positions(bridge, force=True)
    residual = _extract_positions(snap)
    if residual:
        ok2, n2 = _close_round()
        notes.append("retry")
        notes.extend(n2)
        snap2 = fetch_futures_positions(bridge, force=True)
        residual = _extract_positions(snap2)
        ok1 = ok1 and ok2

    mode = "paper" if bridge.paper_mode else ("live" if bridge.live_enabled else "sim")
    if residual:
        return OrderResult(
            False,
            mode,
            argv=argv_acc,
            error_code="FLATTEN_RESIDUAL",
            stdout="; ".join(notes),
            stderr=f"futures flatten residual after retry ({reason}): {len(residual)}",
        )
    return OrderResult(
        True if (ok1 or not n1 or n1 == ["positions_empty"]) else False,
        mode,
        argv=argv_acc,
        stdout="; ".join(notes) or "futures flat",
        error_code="" if not residual else "FLATTEN_RESIDUAL",
    )


def flatten_spot_margin(bridge: KrakenCliBridge, *, reason: str = "flatten") -> OrderResult:
    """Cancel spot orders, close margin positions via CLI, verify empty."""
    notes: List[str] = []
    argv_acc: List[str] = []

    cancel_res = bridge.cancel_all(reason=f"{reason}:spot_cancel")
    argv_acc.extend(list(cancel_res.argv or []))
    notes.append(f"spot_cancel ok={cancel_res.ok}")

    def _close_round() -> Tuple[bool, List[str]]:
        pos_res = bridge.run_leaf("positions", confirmed=False, json_output=True)
        argv_acc.extend(list(pos_res.argv or []))
        if not pos_res.ok and bridge.paper_mode:
            # Paper spot has no margin positions leaf payload — treat as empty book.
            return True, ["spot_margin_empty_or_unreadable"]
        from app.execution.kraken_futures_positions_sot import parse_futures_positions_stdout

        # Reuse loose JSON list/dict parser shape
        try:
            import json

            payload = json.loads((pos_res.stdout or "").strip() or "[]")
        except Exception:
            payload = []
        if isinstance(payload, dict):
            rows = payload.get("positions") or payload.get("openPositions") or []
            if not rows and any(
                isinstance(v, dict) for v in payload.values()
            ):
                # Kraken sometimes maps txid → position
                rows = [v for v in payload.values() if isinstance(v, dict)]
        elif isinstance(payload, list):
            rows = [p for p in payload if isinstance(p, dict)]
        else:
            rows = []
        if not rows:
            return True, ["spot_margin_empty"]
        round_ok = True
        round_notes: List[str] = []
        for raw in rows:
            symbol = _position_symbol(raw) or str(raw.get("pair") or "")
            size = _position_size(raw)
            if not symbol or size <= 0:
                continue
            # Margin long → sell; short → buy
            close_side = _pos_side_to_close(str(raw.get("type") or raw.get("side") or "long"))
            order = bridge.add_order(
                pair=symbol,
                side=close_side,
                volume=size,
                ordertype="market",
                strategy_id="deadman_flatten_spot",
            )
            argv_acc.extend(list(order.argv or []))
            round_ok = round_ok and bool(order.ok)
            round_notes.append(f"spot:{symbol}:{close_side}:{size:g} ok={order.ok}")
        return round_ok, round_notes

    ok1, n1 = _close_round()
    notes.extend(n1)
    ok2, n2 = _close_round()
    if n2 != ["spot_margin_empty"] and n2 != ["spot_margin_empty_or_unreadable"]:
        notes.append("retry")
        notes.extend(n2)
        ok1 = ok1 and ok2
        # final verify
        _, n3 = _close_round()
        if n3 not in (["spot_margin_empty"], ["spot_margin_empty_or_unreadable"]):
            return OrderResult(
                False,
                "paper" if bridge.paper_mode else ("live" if bridge.live_enabled else "sim"),
                argv=argv_acc,
                error_code="FLATTEN_RESIDUAL",
                stdout="; ".join(notes),
                stderr=f"spot margin flatten residual ({reason})",
            )

    return OrderResult(
        True,
        "paper" if bridge.paper_mode else ("live" if bridge.live_enabled else "sim"),
        argv=argv_acc,
        stdout="; ".join(notes) or "spot flat",
    )


def flatten_all(bridge: KrakenCliBridge, *, reason: str = "deadman_flatten") -> OrderResult:
    """Full emergency flatten for the bridge's book (futures-primary or spot)."""
    if bridge.futures:
        fut = flatten_futures_book(bridge, reason=reason)
        # Also cancel spot resting orders (recipe step 1) without inventing spot inventory sells.
        spot_bridge = KrakenCliBridge(
            config=bridge.config,
            telemetry=bridge.telemetry,
            runner=bridge._runner,
            binary=bridge.binary,
            execution_mode=bridge.execution_mode,
            futures=False,
        )
        spot = flatten_spot_margin(spot_bridge, reason=f"{reason}:spot")
        ok = bool(fut.ok) and bool(spot.ok)
        return OrderResult(
            ok,
            fut.mode,
            argv=list(fut.argv or []) + list(spot.argv or []),
            stdout=f"futures=[{fut.stdout}]; spot=[{spot.stdout}]",
            stderr=(fut.stderr or "") + (";" + spot.stderr if spot.stderr else ""),
            error_code="" if ok else (fut.error_code or spot.error_code or "FLATTEN_PARTIAL"),
        )
    return flatten_spot_margin(bridge, reason=reason)
