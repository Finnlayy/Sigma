"""
=========================================================
Datei:      sigma/strategies/dynamic_channel_dca.py
Zweck:      Channel-DCA innerhalb der HTF-Range. Paper-only Loop A.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template)
=========================================================
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from sigma.signals.dual_hurst import evaluate_dual_hurst
from sigma.signals.htf_features import dealing_range_eq
from sigma.signals.session_clock import SessionClock
from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent

GENE_BOUNDS = {
    "channel_low_eq": (0.10, 0.35),
    "channel_high_eq": (0.65, 0.90),
    "step_pct": (0.004, 0.02),
}


class DynamicChannelDCA(BaseStrategy):
    STRATEGY_ID = "dynamic_channel_dca"

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
        htf = ctx.get("htf_candles") or []
        ltf = ctx.get("ltf_candles") or ctx.get("candles") or []
        dual = evaluate_dual_hurst(
            htf, ltf,
            htf_interval_min=int(ctx.get("htf_interval_min") or 60),
            now=ctx.get("now"),
        )
        if not dual.htf_ready:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper", details={"reason": dual.reason},
            )
        if not htf:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper", details={"reason": "missing_data"},
            )
        last = htf[-1]
        highs = [float(c.get("h", c.get("high", 0.0)) or 0.0) for c in htf[-20:]]
        lows = [float(c.get("l", c.get("low", 0.0)) or 0.0) for c in htf[-20:]]
        eq = dealing_range_eq(max(highs or [0]), min(lows or [0]), float(last.get("c", last.get("close", 0.0)) or 0.0))
        low_cut = float(ctx.get("channel_low_eq") or 0.25)
        high_cut = float(ctx.get("channel_high_eq") or 0.75)
        action, side = "FLAT", ""
        if eq is not None and eq <= low_cut and dual.htf_regime != "MEAN_REVERTING":
            action, side = "BUY", "buy"
        elif eq is not None and eq >= high_cut and dual.htf_regime != "MEAN_REVERTING":
            action, side = "SELL", "sell"
        price = float(last.get("c", last.get("close", 0.0)) or 0.0)
        return StrategyIntent(
            strategy_id=self.STRATEGY_ID,
            symbol=symbol,
            action=action,
            side=side,
            volume=0.0 if action == "FLAT" else float(ctx.get("volume") or 0.0),
            price=price,
            pair=symbol,
            execution_mode="kraken_paper",
            details={"eq_pos": eq, "dual": dual.to_dict(), "loop_a": "paper_only"},
        )
