"""
=========================================================
Datei:      sigma/strategies/htf_trend_ltf_reversion.py
Zweck:      HTF-Trend-Bias + LTF-Reversion. Keine HTF-Market-Orders.
            Loop A nur Paper bis E graduert.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template)
=========================================================
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from sigma.signals.dual_hurst import evaluate_dual_hurst
from sigma.signals.htf_features import extract_htf_flags
from sigma.signals.session_clock import SessionClock
from sigma.signals.timeframe_ladder import session_exec_pair
from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent
from sigma.strategies.pine_v6_generator import generate_htf_ltf_pine

GENE_BOUNDS = {
    "atr_stop_mult": (1.0, 2.5),
    "sweep_lookback": (3, 8),
}


class HtfTrendLtfReversion(BaseStrategy):
    STRATEGY_ID = "htf_trend_ltf_reversion"

    def plan(self, ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
        empty = StrategyIntent(
            strategy_id=self.STRATEGY_ID,
            symbol="",
            action="FLAT",
            execution_mode="kraken_paper",
            details={"reason": "missing_data"},
        )
        if not ctx:
            return empty
        symbol = str(ctx.get("symbol") or "")
        if not symbol:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol="", action="FLAT",
                execution_mode="kraken_paper", details={"reason": "missing_symbol"},
            )
        session = ctx.get("session") or SessionClock().evaluate(ctx.get("now")).to_dict()
        if session.get("liquidity_gap"):
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper",
                details={"reason": "utc_21_gap", "session": session.get("session")},
            )
        weekend_alt = bool(session.get("weekend_alts_paper_only")) and not _is_leader(symbol)
        if weekend_alt:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper",
                details={"reason": "weekend_alt_paper_only"},
            )
        htf = ctx.get("htf_candles") or []
        ltf = ctx.get("ltf_candles") or ctx.get("candles") or []
        pair = session_exec_pair(
            str(session.get("session") or ""),
            use_ict_ladder=bool(ctx.get("use_ict_ladder")),
        )
        dual = evaluate_dual_hurst(
            htf, ltf,
            htf_interval_min=int(ctx.get("htf_interval_min") or pair.bias_minutes),
            now=ctx.get("now"),
        )
        if not dual.htf_ready:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper",
                details={"reason": dual.reason or "htf_open", "htf_ready": False},
            )
        if not dual.complementary:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=symbol, action="FLAT",
                execution_mode="kraken_paper",
                details={"reason": "not_complementary", "dual": dual.to_dict()},
            )
        flags = extract_htf_flags(
            htf, interval_min=int(ctx.get("htf_interval_min") or pair.bias_minutes),
            now=ctx.get("now"),
        )
        # FVG is an optional locator, never a live gate (H4 default off)
        use_fvg = bool(ctx.get("enable_fvg_locator"))
        locator = flags if use_fvg else {"live_gate": False}
        action, side = _ltf_action(session, flags, dual)
        last = (ltf or htf)[-1] if (ltf or htf) else {}
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
            details={
                "htf_ready": True,
                "dual": dual.to_dict(),
                "pair": pair.to_dict(),
                "flags": flags,
                "fvg_locator": locator,
                "template": self.STRATEGY_ID,
                "loop_a": "paper_only",
            },
        )

    def pine(self, **kwargs: Any) -> str:
        return generate_htf_ltf_pine(self.STRATEGY_ID, **kwargs)


def _is_leader(symbol: str) -> bool:
    u = symbol.upper()
    return u.startswith("BTC") or u.startswith("XBT") or u.startswith("XAU")


def _ltf_action(session: Mapping[str, Any], flags: Mapping[str, Any], dual) -> tuple:
    name = str(session.get("session") or "")
    sweep = bool(flags.get("liquidity_sweep"))
    side = str(flags.get("sweep_side") or "")
    if "LONDON" in name:
        if sweep and side == "low":
            return "BUY", "buy"
        if sweep and side == "high":
            return "SELL", "sell"
        return "FLAT", ""
    if "NEW_YORK" in name:
        if dual.htf_regime == "TRENDING" and dual.htf_hurst > 0.55:
            return ("BUY", "buy") if sweep and side == "low" else (
                ("SELL", "sell") if sweep and side == "high" else ("FLAT", "")
            )
    if sweep and side == "low":
        return "BUY", "buy"
    if sweep and side == "high":
        return "SELL", "sell"
    return "FLAT", ""
