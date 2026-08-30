"""
=========================================================
Datei:      sigma/signals/lead_lag_detector.py
Zweck:      Wrapper um RegimeEngine.lead_lag_matrix als AlphaSignal.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche
=========================================================
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.quant.RegimeEngine import lead_lag_matrix
from sigma.signals.base_alpha import AlphaSignal, BaseAlphaModel


class LeadLagDetector(BaseAlphaModel):
    def evaluate(self, market: Optional[Mapping[str, Any]]) -> AlphaSignal:
        if not market:
            return self.fail_closed("missing_data")
        series = market.get("series")
        symbols = market.get("symbols") or (list(series.keys()) if series else [])
        if not series or not symbols:
            return self.fail_closed("missing_data")
        closes = {sym: _closes(series.get(sym) or []) for sym in symbols}
        if any(len(v) < 8 for v in closes.values()):
            return self.fail_closed("short_series")
        matrix = lead_lag_matrix(list(symbols), closes, max_lag=int(market.get("max_lag") or 5))
        return AlphaSignal(
            score=1.0,
            action="FLAT",
            valid=True,
            reason="lead_lag",
            details={"matrix": matrix},
        )


def _closes(candles: Sequence[Mapping[str, Any]]) -> list:
    out = []
    for c in candles:
        px = c.get("c", c.get("close"))
        try:
            val = float(px)
        except (TypeError, ValueError):
            continue
        if val > 0:
            out.append(val)
    return out
