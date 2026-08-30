"""
=========================================================
Datei:      sigma/signals/polymarket_layer0.py
Zweck:      Optional Layer-0 Pre-Regime aus Polymarket. Fail-closed ohne Feed.
            Keine synthetischen Odds, keine Live-Gates.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Pre-Regime)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class PolymarketLayer0:
    valid: bool
    reason: str
    regime_hint: str = ""
    implied_prob: Optional[float] = None
    event_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "regime_hint": self.regime_hint,
            "implied_prob": self.implied_prob,
            "event_id": self.event_id,
            "details": dict(self.details),
        }


def layer0_pre_regime(payload: Optional[Mapping[str, Any]] = None) -> PolymarketLayer0:
    """Loop-C optional pre-regime. Missing/malformed payload → invalid empty."""
    if not payload:
        return PolymarketLayer0(False, "missing_data")
    event_id = str(payload.get("event_id") or payload.get("id") or "")
    raw_prob = payload.get("implied_prob", payload.get("probability"))
    try:
        prob = float(raw_prob) if raw_prob is not None else None
    except (TypeError, ValueError):
        return PolymarketLayer0(False, "malformed_probability", event_id=event_id)
    if prob is None or not 0.0 <= prob <= 1.0:
        return PolymarketLayer0(False, "missing_data", event_id=event_id)
    if payload.get("degraded") or str(payload.get("source") or "").lower() in {"synthetic", "seed"}:
        return PolymarketLayer0(False, "synthetic_or_degraded", event_id=event_id)
    hint = "RISK_ON" if prob >= 0.60 else ("RISK_OFF" if prob <= 0.40 else "NEUTRAL")
    return PolymarketLayer0(
        valid=True,
        reason="ok",
        regime_hint=hint,
        implied_prob=round(prob, 4),
        event_id=event_id,
        details={"title": str(payload.get("title") or "")},
    )
