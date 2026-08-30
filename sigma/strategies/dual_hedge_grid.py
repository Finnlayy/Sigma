"""
=========================================================
Datei:      sigma/strategies/dual_hedge_grid.py
Zweck:      London Judas-Sweep → Dual-Hedge / Fade. Paper-only Loop A.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template)
=========================================================
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from sigma.signals.htf_features import extract_htf_flags
from sigma.signals.session_clock import SessionClock
from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent

GENE_BOUNDS = {
    "grid_steps": (2, 6),
    "fade_atr_mult": (0.8, 2.0),
}


class DualHedgeGrid(BaseStrategy):
    STRATEGY_ID = "dual_hedge_grid"

    def plan(self, ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
        if not ctx or not ctx.get("symbol"):
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol="", action="FLAT",
                execution_mode="kraken_paper", details={"reason": "missing_data"},
            )
        symbol = str(ctx["symbol"])
        session = ctx.get("session") or SessionClock().evaluate(ctx.get("now")).to_dict()
        if session.get("liquidity_gap"):
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper", details={"reason": "utc_21_gap"},
            )
        name = str(session.get("session") or "")
        if "LONDON" not in name and not ctx.get("force_london"):
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper", details={"reason": "not_london"},
            )
        candles = ctx.get("ltf_candles") or ctx.get("candles") or ctx.get("htf_candles") or []
        flags = extract_htf_flags(
            candles,
            interval_min=int(ctx.get("ltf_interval_min") or 15),
            now=ctx.get("now"),
        )
        if not flags.get("valid"):
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper", details={"reason": flags.get("reason") or "invalid"},
            )
        action, side = "FLAT", ""
        if flags.get("liquidity_sweep"):
            if flags.get("sweep_side") == "low":
                action, side = "BUY", "buy"
            elif flags.get("sweep_side") == "high":
                action, side = "SELL", "sell"
        last = candles[-1] if candles else {}
        return StrategyIntent(
            strategy_id=self.STRATEGY_ID,
            symbol=symbol,
            action=action,
            side=side,
            volume=0.0 if action == "FLAT" else float(ctx.get("volume") or 0.0),
            price=float(last.get("c", last.get("close", 0.0)) or 0.0),
            pair=symbol,
            execution_mode="kraken_paper",
            details={"flags": flags, "session": name, "loop_a": "paper_only", "hedge": True},
        )
