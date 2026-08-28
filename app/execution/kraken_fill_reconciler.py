"""Authenticated Kraken futures fill reconciliation for live P&L accounting.

Spot is intentionally excluded. ``kraken trades-history`` returns fill price,
volume, cost and fee — not lot-matched / cost-basis realized PnL — so live
spot execution stays fail-closed (``SPOT_LIVE_PNL_RECONCILIATION_UNAVAILABLE``).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("app.execution.kraken_fill_reconciler")

# Re-fetch overlap after restart so late-indexed exchange fills are not missed.
FILL_POLL_OVERLAP_S = 300.0


class KrakenFillReconciler:
    def __init__(self, bridge: Any, store: Any,
                 handler: Callable[[Dict[str, Any]], None]) -> None:
        self.bridge = bridge
        self.store = store
        self.handler = handler
        watermark = getattr(store, "reconciled_fill_watermark", lambda: 0.0)()
        self.last_poll = float(watermark or 0.0)
        self.last_error: Optional[str] = None
        self.applied = 0
        self.pending_reconciliation = 0

    def poll(self) -> Dict[str, Any]:
        since = max(0.0, self.last_poll - FILL_POLL_OVERLAP_S)
        try:
            rows = self.bridge.futures_fills(since=since)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        by_id: Dict[str, Dict[str, Any]] = {}
        for raw in rows:
            normalized = self._normalize(raw)
            if normalized is None:
                continue
            by_id[normalized["fill_id"]] = normalized
        loader = getattr(self.store, "unapplied_reconciled_fills", None)
        if loader is not None:
            for stored in loader() or []:
                fill_id = str(stored.get("fill_id") or "")
                if not fill_id or fill_id in by_id:
                    continue
                by_id[fill_id] = {
                    "fill_id": fill_id,
                    "ts": float(stored.get("ts") or 0.0),
                    "strategy_id": str(stored.get("strategy_id") or ""),
                    "symbol": str(stored.get("symbol") or ""),
                    "net_pnl_usd": float(stored.get("net_pnl_usd") or 0.0),
                    "payload": stored.get("payload") or {},
                }
        applied = 0
        pending = 0
        for normalized in by_id.values():
            fill_id = normalized["fill_id"]
            status = self.store.reconciled_fill_status(fill_id)
            if status == "applied":
                continue
            if status is None:
                self.store.record_reconciled_fill(normalized, status="pending")
            try:
                self.handler({
                    **normalized,
                    "execution_mode": "live",
                    "accounting_source": "verified_live_fill",
                })
                self.store.set_reconciled_fill_status(fill_id, "applied")
                applied += 1
            except Exception:
                self.store.set_reconciled_fill_status(fill_id, "failed")
                logger.exception("live fill accounting failed for %s", fill_id)
                pending += 1
        self.last_poll = time.time()
        self.applied += applied
        self.pending_reconciliation = pending
        self.last_error = None
        return {"fetched": len(rows), "applied": applied, "pending": pending}

    @staticmethod
    def _normalize(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        fill_id = raw.get("fill_id") or raw.get("fillId") or raw.get("id")
        pnl_value = None
        for key in ("realized_pnl", "realizedPnl", "realized_pnl_usd", "pnl"):
            if key in raw and raw.get(key) is not None:
                pnl_value = raw.get(key)
                break
        if not fill_id or pnl_value is None:
            return None
        try:
            pnl = float(pnl_value)
        except (TypeError, ValueError):
            return None
        ts = raw.get("timestamp") or raw.get("time") or time.time()
        try:
            ts = float(ts)
            if ts > 100_000_000_000:
                ts /= 1000.0
        except (TypeError, ValueError):
            ts = time.time()
        return {
            "fill_id": str(fill_id),
            "ts": ts,
            "strategy_id": str(
                raw.get("client_order_id") or raw.get("clientOrderId")
                or raw.get("strategy_id") or ""
            ),
            "symbol": str(raw.get("symbol") or raw.get("instrument") or ""),
            "net_pnl_usd": pnl,
            "payload": raw,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "last_poll": self.last_poll,
            "last_error": self.last_error,
            "applied": self.applied,
            "pending_reconciliation": self.pending_reconciliation,
        }
