"""
=========================================================
Datei:      sigma/ports/polymarket_port.py
Zweck:      Optionaler Polymarket-Layer-0-Port (KB §7): Strikes,
            Yes-Preise, Volumen, Zeitstempel. Nur liquide Maerkte.
            Ohne Konfiguration available=False (fail-closed, kein
            synthetischer Payload). Kein Netz in Tests — Port wird
            injiziert. KEINE Credentials, kein Polymarket-Handel.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feed-Seam)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

MIN_VOLUME_USD = 1_000_000.0  # nur liquide Maerkte (KB §7 Punkt 4)


@dataclass(frozen=True)
class PolymarketOdds:
    """Validierte, liquide Ereignis-Quoten."""

    event_slug: str
    strikes: List[float]            # aufsteigende Schwellen
    yes_prices: List[float]         # Yes-Preise je Strike (kum. Wahrscheinlichkeit)
    volume_usd: float
    ts: float
    synthetic: bool = False
    quotes: Optional[Dict[str, float]] = None  # Term-Struktur 1h/2h/4h/EOD

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class PolymarketPort:
    """Optionaler Feed-Seam. available nur mit konfiguriertem Endpoint.
    fetch_event_odds validiert Struktur + Liquiditaet; alles andere ist
    fail-closed (available=False / reason)."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        min_volume_usd: float = MIN_VOLUME_USD,
        timeout_s: float = 5.0,
    ) -> None:
        self.endpoint = endpoint
        self.min_volume_usd = float(min_volume_usd)
        self.timeout_s = float(timeout_s)

    @property
    def available(self) -> bool:
        return bool(self.endpoint)

    def fetch_event_odds(self, event_slug: str) -> Dict[str, Any]:
        """Holt und validiert die Quoten eines Events. Ohne Konfiguration oder
        bei Fehlern -> {"available": False, "reason": ...} (fail-closed)."""
        if not self.available:
            return {"available": False, "reason": "not_configured"}
        try:
            import httpx

            resp = httpx.get(
                f"{self.endpoint.rstrip('/')}/events/{event_slug}",
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # fail-closed: kein Feed -> keine Daten
            return {"available": False, "reason": f"fetch_error:{type(exc).__name__}"}
        return validate_odds_payload(payload, min_volume_usd=self.min_volume_usd)


def validate_odds_payload(
    payload: Optional[Mapping[str, Any]],
    *,
    min_volume_usd: float = MIN_VOLUME_USD,
) -> Dict[str, Any]:
    """Validiert ein injiziertes Payload-Dict (Tests: kein Netzwerk).
    Fehlende Felder, synthetic=True, zu wenig Volumen -> available=False."""
    if not payload:
        return {"available": False, "reason": "missing_payload"}
    if payload.get("synthetic") or str(payload.get("source") or "").lower() in (
        "synthetic", "seed",
    ):
        return {"available": False, "reason": "synthetic_or_degraded"}
    strikes = payload.get("strikes")
    yes_prices = payload.get("yes_prices")
    try:
        strikes_l = [float(x) for x in (strikes or [])]
        yes_l = [float(x) for x in (yes_prices or [])]
    except (TypeError, ValueError):
        return {"available": False, "reason": "malformed_numbers"}
    if len(strikes_l) < 2 or len(yes_l) != len(strikes_l):
        return {"available": False, "reason": "missing_or_mismatched_fields"}
    if any(y < 0.0 or y > 1.0 for y in yes_l):
        return {"available": False, "reason": "price_out_of_range"}
    if any(b <= a for a, b in zip(strikes_l, strikes_l[1:])):
        return {"available": False, "reason": "strikes_not_ordered"}
    volume = float(payload.get("volume_usd", 0.0) or 0.0)
    if volume < min_volume_usd:
        return {"available": False, "reason": "insufficient_liquidity"}
    ts = float(payload.get("ts", payload.get("timestamp", 0.0)) or 0.0)
    raw_quotes = payload.get("quotes")
    quotes = None
    if isinstance(raw_quotes, dict):
        try:
            quotes = {str(k): float(v) for k, v in raw_quotes.items()}
        except (TypeError, ValueError):
            quotes = None  # kaputte Term-Struktur -> nur Dichte, keine Trajektorie
    return {
        "available": True,
        "odds": PolymarketOdds(
            event_slug=str(payload.get("event_slug", payload.get("event_id", ""))),
            strikes=strikes_l,
            yes_prices=yes_l,
            volume_usd=volume,
            ts=ts,
            synthetic=False,
            quotes=quotes,
        ).to_dict(),
    }


__all__ = ["MIN_VOLUME_USD", "PolymarketOdds", "PolymarketPort",
           "validate_odds_payload"]
