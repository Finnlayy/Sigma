"""
=========================================================
Datei:      sigma/signals/polymarket_layer0.py
Zweck:      Optional Layer-0 Pre-Regime aus Polymarket. Fail-closed ohne Feed.
            Keine synthetischen Odds, keine Live-Gates. MP-06: Port-
            Injektion (layer0_from_port) -> Dichte + Term-Struktur als
            Telemetrie-Kontext. Gate-Schwelle ist NICHT aktiv (Konstante
            reserviert; Feed = Telemetrie bis echter Feed + Tests).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Pre-Regime)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from sigma.signals.polymarket_density import density_from_ladder
from sigma.signals.polymarket_trajectory import trajectory_from_quotes

# KB §7 Punkt 5: reservierte Gate-Schwelle. NICHT aktiv — der Orchestrator
# darf damit erst gaten, wenn ein echter Feed + Tests vorliegen (Nutzerregel:
# Feed = Telemetrie). Bis dahin nur Kontext/Klassifikation.
POLYMARKET_GATE_THRESHOLD = 0.60


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


def layer0_from_port(
    port: Optional[Any],
    event_slug: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
) -> PolymarketLayer0:
    """MP-06: echte Port-Injektion. Ohne Port/Feed -> valid=False wie bisher.
    Mit validiertem liquiden Payload: Dichte (Strikes/Yes-Preise) und
    Term-Struktur (1h/2h/4h/EOD) als details/Bias-Kontext. Kein Gate."""
    if port is None or not getattr(port, "available", False):
        return PolymarketLayer0(False, "no_feed")
    try:
        if payload is None:
            fetched = port.fetch_event_odds(event_slug)
            if not fetched.get("available"):
                return PolymarketLayer0(False, str(fetched.get("reason", "fetch_failed")))
            payload = fetched.get("odds") or {}
    except Exception as exc:  # fail-closed: Port-Fehler -> kein Feed
        return PolymarketLayer0(False, f"port_error:{type(exc).__name__}")
    if not payload or payload.get("synthetic"):
        return PolymarketLayer0(False, "synthetic_or_degraded")
    event_id = str(payload.get("event_slug") or payload.get("event_id") or "")
    strikes = payload.get("strikes") or []
    yes_prices = payload.get("yes_prices") or []
    density = density_from_ladder(strikes, yes_prices)
    if not density.valid:
        return PolymarketLayer0(False, f"density:{density.reason}", event_id=event_id)
    quotes = payload.get("quotes") or {}
    traj = trajectory_from_quotes(quotes) if quotes else None
    details: Dict[str, Any] = {
        "density": density.to_dict(),
        "trajectory": traj.to_dict() if traj is not None else None,
        "gate_threshold_reserved": POLYMARKET_GATE_THRESHOLD,
        "gate_active": False,
    }
    hint = ""
    if traj is not None and traj.valid:
        hint = traj.bias
    # implied_prob ist eine Wahrscheinlichkeit in [0,1]: letzter Term-Struktur-
    # Wert (EOD), sonst Peak-Bin-Wahrscheinlichkeit der Dichte. Der Preis-mu
    # (in USD) bleibt in details.density.
    implied_prob = None
    if traj is not None and traj.valid and traj.mu_curve:
        curve = traj.mu_curve
        implied_prob = float(curve.get("EOD") or list(curve.values())[-1])
    if implied_prob is None and density.bins:
        implied_prob = max(b.prob for b in density.bins)
    return PolymarketLayer0(
        valid=True,
        reason="ok",
        regime_hint=hint,
        implied_prob=round(implied_prob, 4) if implied_prob is not None else None,
        event_id=event_id,
        details=details,
    )
