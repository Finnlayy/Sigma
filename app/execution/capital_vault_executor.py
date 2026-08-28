"""Safety-gated Kraken spot purchase used by the Capital Flywheel."""
from __future__ import annotations

from typing import Any, Dict


class CapitalVaultExecutor:
    def __init__(self, bridge: Any, depth_adapter: Any, *, enabled: bool = False) -> None:
        self.bridge = bridge
        self.depth_adapter = depth_adapter
        self.enabled = bool(enabled)

    def __call__(self, asset: str, amount_eur: float) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "ok": True,
                "executed": False,
                "mode": "bookkeeping_only",
                "reason": "SIGMA_FLYWHEEL_SPOT_EXECUTION disabled",
            }
        if amount_eur <= 0:
            return {"ok": False, "executed": False, "reason": "amount_eur must be > 0"}
        if not bool(getattr(self.bridge, "live_enabled", False)):
            return {
                "ok": False,
                "executed": False,
                "reason": "live execution requires SIGMA_LIVE_TRADING and LIVE_APPROVED",
            }

        pair = f"{asset.upper()}EUR"
        snapshot = self.depth_adapter.fetch(pair)
        price = snapshot.mid
        if price <= 0:
            return {"ok": False, "executed": False, "reason": "invalid Kraken mid price"}
        volume = amount_eur / price
        result = self.bridge.add_order(
            pair=pair,
            side="buy",
            volume=volume,
            ordertype="market",
            strategy_id="capital_flywheel",
        )
        return {
            "ok": bool(getattr(result, "ok", False)),
            "executed": bool(getattr(result, "ok", False)),
            "mode": getattr(result, "mode", ""),
            "order_id": getattr(result, "txid", ""),
            "pair": pair,
            "volume": volume,
            "reference_price": price,
            "error_code": getattr(result, "error_code", ""),
        }
