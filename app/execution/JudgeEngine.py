"""
=========================================================
Datei:      app/execution/JudgeEngine.py
Zweck:      M8 Judge (Noir) — 8 Reject-Gates & Fractional Kelly Sizer (Modul 09)
Knoten:     Noir (Diablo-Judge)
=========================================================
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.execution.judge_engine")

DEFAULT_ADV: Dict[str, float] = {
    "BTC/USD": 50_000.0,   # BTC / Tag (Referenz)
    "ETH/USD": 900_000.0,
    "SOL/USD": 2_500_000.0,
    "XRP/USD": 60_000_000.0,
}


class JudgeEngine:
    """8-Gate-Prüfstand + Half-Kelly Vol-Targeting (Modul 09)."""

    def __init__(self, config=None):
        from app.core.config import load_config

        self.config = config or load_config()

    def evaluate(self, symbol: str, qty: float, side: str,
                 win_rate: float, win_loss_ratio: float, target_vol: float,
                 context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx = context or {}
        realized_vol = float(ctx.get("realized_vol") or 0.024)      # annualisiert
        spread_bps = float(ctx.get("spread_bps") or 3.0)
        portfolio_beta = float(ctx.get("portfolio_beta") or 0.65)
        rolling_drawdown_pct = float(ctx.get("rolling_drawdown_pct") or 3.2)
        sentiment_score = float(ctx.get("sentiment_score") or 0.0)
        hurst_regime = str(ctx.get("hurst_regime") or "RANDOM_WALK")
        system_state = str(ctx.get("system_state") or "SHADOW_ACTIVE")

        adv = float(ctx.get("adv") or DEFAULT_ADV.get(symbol, 50_000.0))
        order_ratio = abs(qty) / adv if adv > 0 else 0.0

        gates: List[Dict[str, Any]] = []

        def add(name: str, passed: bool, reason: str):
            gates.append({"gate": f"Gate {len(gates) + 1}", "name": name,
                          "passed": bool(passed), "reason": reason})

        add("Volatility Threshold Guard",
            realized_vol <= 0.045,
            f"Current vol {realized_vol * 100:.1f}% <= Max 4.5%"
            if realized_vol <= 0.045 else
            f"Current vol {realized_vol * 100:.1f}% > Max 4.5%")

        add("Spread & Liquidity Filter",
            spread_bps <= 8.0,
            f"Spread {spread_bps:.1f} bps <= Max 8.0 bps"
            if spread_bps <= 8.0 else f"Spread {spread_bps:.1f} bps > Max 8.0 bps")

        add("Cross-Correlation Guard",
            portfolio_beta <= 0.85,
            f"Systemic portfolio beta {portfolio_beta:.2f} <= Max 0.85"
            if portfolio_beta <= 0.85 else
            f"Systemic portfolio beta {portfolio_beta:.2f} > Max 0.85")

        add("Maximum Drawdown Floor",
            rolling_drawdown_pct <= 12.0,
            f"Rolling drawdown {rolling_drawdown_pct:.1f}% <= Cutoff 12.0%"
            if rolling_drawdown_pct <= 12.0 else
            f"Rolling drawdown {rolling_drawdown_pct:.1f}% > Cutoff 12.0%")

        add("Order Size & ADV Limit",
            order_ratio <= 0.025,
            f"Order ratio {order_ratio * 100:.3f}% of ADV <= Max 2.5%"
            if order_ratio <= 0.025 else
            f"Order ratio {order_ratio * 100:.3f}% of ADV > Max 2.5%")

        add("FinBERT News Shock Filter",
            -0.35 <= sentiment_score <= 0.35 or abs(sentiment_score) <= 0.5,
            "Sentiment score neutral/safe"
            if abs(sentiment_score) <= 0.5 else
            f"Sentiment shock {sentiment_score:+.2f} — News-Gate aktiv")

        add("Regime Alignment Check",
            hurst_regime != "MEAN_REVERTING" or side == "SELL",
            f"Strategy matches DFA Hurst trend ({hurst_regime})"
            if hurst_regime != "MEAN_REVERTING" or side == "SELL" else
            "Momentum-Long gegen mean-reverting Hurst-Regime")

        add("Global Circuit Breaker",
            system_state != "EMERGENCY_HALT",
            f"System directive state: {system_state}"
            if system_state != "EMERGENCY_HALT" else
            "EMERGENCY_HALT aktiv — alle Orders gesperrt")

        passed = all(g["passed"] for g in gates)

        # Fractional Kelly (Half-Kelly) + Vol-Targeting
        kelly_full = win_rate - (1.0 - win_rate) / max(win_loss_ratio, 1e-9)
        kelly_half = max(0.0, kelly_full) / 2.0
        vol_target_fraction = target_vol / realized_vol if realized_vol > 0 else kelly_half
        recommended = min(kelly_half, max(vol_target_fraction, 0.0)) if vol_target_fraction > 0 else kelly_half
        recommended = max(0.0, min(recommended, 0.5))

        return {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "m8_verdict": {"passed": passed, "gates": gates,
                           "rejected_by": [g["name"] for g in gates if not g["passed"]]},
            "kelly_sizing": {
                "kelly_full": round(kelly_full, 4),
                "kelly_half": round(kelly_half, 4),
                "vol_target_fraction": round(vol_target_fraction, 4),
                "recommended_fraction": round(recommended, 4),
                "win_rate": win_rate,
                "win_loss_ratio": win_loss_ratio,
                "target_vol": target_vol,
            },
            "inputs": {
                "realized_vol": realized_vol,
                "spread_bps": spread_bps,
                "portfolio_beta": portfolio_beta,
                "rolling_drawdown_pct": rolling_drawdown_pct,
                "sentiment_score": sentiment_score,
                "hurst_regime": hurst_regime,
                "system_state": system_state,
            },
        }
