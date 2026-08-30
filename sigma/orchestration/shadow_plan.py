"""
=========================================================
Datei:      sigma/orchestration/shadow_plan.py
Zweck:      Nacht-Schattenplan (KB §4.6): nicht bindende Watchlist
            mit alpha/beta-Szenarien und Strategie-Optionen aus
            vorhandenen Werkzeugen (Ranker/SessionClock/Wave).
            Loest KEINEN Scan aus, kein Auto-Deploy, keine Orders.
            Rhythmus UTC: 21:00-00:30 Monitoring (21:00-22:00
            Quarantaene), 00:30-01:00 Synthese, ~01:00 Publikation.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Orchestrierung) / Noir (Fail-Closed)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

NIGHT_MONITORING = "NIGHT_MONITORING"
NIGHT_QUARANTINE = "NIGHT_QUARANTINE"
NIGHT_SYNTHESIS = "NIGHT_SYNTHESIS"
NIGHT_PUBLISHED = "NIGHT_PUBLISHED"
OUTSIDE_NIGHT = "OUTSIDE_NIGHT"

MONITOR_START_MIN = 21 * 60          # 21:00 UTC
QUARANTINE_END_MIN = 22 * 60         # 21:00-22:00 nur Beobachtung
SYNTHESIS_START_MIN = 30             # 00:30 UTC (Tag-Grenze: Minute 30)
PUBLISH_MIN = 60                     # ~01:00 UTC

# Sentiment-Saettigung (Kontext fuer MP-08, nie harter Trigger)
FUNDING_SATURATION = 0.0005          # 0,05 % je Funding-Periode
RETAIL_LONG_SATURATION = 4.0         # Retail-Long : Short > 4:1
SOCIAL_BULL_SATURATION = 0.85        # > 85 % bullisch


@dataclass(frozen=True)
class ShadowScenario:
    """Antizipiertes Szenario eines Watchlist-Symbols."""

    symbol: str
    conductor: str
    sweep_zone: Optional[float]
    breakout_level: Optional[float]
    session_bias: str
    strategy_option: str
    mean_reversion_bias: bool

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ShadowPlan:
    """Nicht bindender Nachtplan. Planung nur, KEINE Ausfuehrung."""

    phase: str
    generated_ts: float
    watchlist: List[str] = field(default_factory=list)
    scenarios: List[ShadowScenario] = field(default_factory=list)
    path_alpha: str = ""   # proaktiv (Sniper, MP-07) — nur Beschreibung
    path_beta: str = ""    # reaktiv (Bestätigung nach geschlossenem Breakout)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "generated_ts": self.generated_ts,
            "watchlist": list(self.watchlist),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "path_alpha": self.path_alpha,
            "path_beta": self.path_beta,
            "reason": self.reason,
        }


def night_phase(now_ts: float) -> str:
    """UTC-Rhythmus des Schattenplans (KB §4.6):
    21:00-22:00 Quarantaene (nur Beobachtung), 22:00-00:30 Monitoring,
    00:30-01:00 Synthese, ~01:00-02:00 publiziert, sonst ausserhalb."""
    dt = datetime.fromtimestamp(float(now_ts), tz=timezone.utc)
    minutes = dt.hour * 60 + dt.minute
    if MONITOR_START_MIN <= minutes < QUARANTINE_END_MIN:
        return NIGHT_QUARANTINE
    if minutes >= QUARANTINE_END_MIN or minutes < SYNTHESIS_START_MIN:
        return NIGHT_MONITORING
    if minutes < PUBLISH_MIN:
        return NIGHT_SYNTHESIS
    if minutes < PUBLISH_MIN + 60:
        return NIGHT_PUBLISHED
    return OUTSIDE_NIGHT


def build_shadow_plan(
    *,
    now_ts: float,
    watchlist: Sequence[str] = (),
    ranker_rows: Sequence[Mapping[str, Any]] = (),
    session_bias: Optional[str] = None,
    sweep_zones: Optional[Mapping[str, float]] = None,
    breakout_levels: Optional[Mapping[str, float]] = None,
    sentiment: Optional[Mapping[str, float]] = None,
) -> ShadowPlan:
    """Erstellt den Plan aus den Ergebnissen des letzten 1h-Screens.
    Reine Planung: loest keinen Scan aus, keine Orders, kein Deploy."""
    phase = night_phase(now_ts)
    if phase in (OUTSIDE_NIGHT, NIGHT_PUBLISHED):
        return ShadowPlan(phase=phase, generated_ts=float(now_ts), reason="no_plan_window")
    watch = [str(s) for s in watchlist]
    scenarios: List[ShadowScenario] = []
    for row in ranker_rows:
        symbol = str(row.get("symbol", ""))
        if not symbol:
            continue
        if watch and symbol not in watch:
            continue
        bias = session_bias or ""
        scenarios.append(ShadowScenario(
            symbol=symbol,
            conductor=str(row.get("conductor", "")),
            sweep_zone=(float(sweep_zones[symbol]) if sweep_zones and symbol in sweep_zones else None),
            breakout_level=(float(breakout_levels[symbol]) if breakout_levels and symbol in breakout_levels else None),
            session_bias=bias,
            strategy_option=str(row.get("recommendation", "dca")),
            mean_reversion_bias=_sentiment_bias(sentiment),
        ))
    return ShadowPlan(
        phase=phase,
        generated_ts=float(now_ts),
        watchlist=watch,
        scenarios=scenarios,
        path_alpha="proactive_sniper_edge_entry_mp07_not_binding",
        path_beta="reactive_retest_after_closed_bar_conductor_and_alt_breakout",
        reason="shadow_plan_non_binding",
    )


def _sentiment_bias(sentiment: Optional[Mapping[str, float]]) -> bool:
    """Saettigungs-Kennzeichnung (Funding extrem / Retail-Long > 4:1 /
    Social > 85 % bullisch). Nie ein harter Trigger, nur Kontext."""
    if not sentiment:
        return False
    funding = float(sentiment.get("funding", 0.0) or 0.0)
    retail = float(sentiment.get("retail_long_short_ratio", 0.0) or 0.0)
    social = float(sentiment.get("social_bullish", 0.0) or 0.0)
    return (
        abs(funding) >= FUNDING_SATURATION
        or retail >= RETAIL_LONG_SATURATION
        or social >= SOCIAL_BULL_SATURATION
    )


__all__ = [
    "FUNDING_SATURATION", "NIGHT_MONITORING", "NIGHT_PUBLISHED",
    "NIGHT_QUARANTINE", "NIGHT_SYNTHESIS", "OUTSIDE_NIGHT",
    "RETAIL_LONG_SATURATION", "SOCIAL_BULL_SATURATION", "ShadowPlan",
    "ShadowScenario", "build_shadow_plan", "night_phase",
]
