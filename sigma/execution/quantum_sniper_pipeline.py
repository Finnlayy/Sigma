"""
=========================================================
Datei:      sigma/execution/quantum_sniper_pipeline.py
Zweck:      MP-07 Datenfluss-Kette als reine Schicht:
            15m-Wave-Evaluation (bestehender Collider, KEIN
            Nachbau) -> bei COLLAPSED LTF-Retest-Polling
            (1m/5m) -> Ranker-Check -> Intent. Keine Exchange-
            Aufrufe, nur Paper-Pfad; jede Stufe fail-closed.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Datenfluss) / Rouge (Template)
=========================================================
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sigma.signals.quantum_wave_collider import QuantumWaveCollider
from sigma.strategies.base_strategy import StrategyIntent
from sigma.strategies.quantum_sniper_dca import (
    QuantumSniperDCA,
    SNIPER_STRATEGY_ID,
    plan_sniper,
)


def run_sniper_pipeline(
    *,
    htf_bars: Optional[Sequence[Mapping[str, Any]]] = None,
    ltf_bars: Optional[Sequence[Mapping[str, Any]]] = None,
    symbol: str = "",
    now: Optional[float] = None,
    wave_interval_min: int = 15,
    session: Optional[Mapping[str, Any]] = None,
    screening: Optional[Mapping[str, Any]] = None,
    liquidation_price: Optional[float] = None,
    leverage: Optional[int] = None,
    expected_btc_wick_pct: Optional[float] = None,
    ltf_interval_min: int = 1,
    precomputed_wave: Optional[Mapping[str, Any]] = None,
) -> StrategyIntent:
    """15m-Wave -> Retest -> Ranker -> Intent (nur Papier).

    Stufe 1: Wave nur aus geschlossenen 15m-Bars; ohne Kollaps endet die
    Kette hier (FLAT, kein Entry). Stufe 2: bei COLLAPSED prueft das
    Template LTF-Retest + Ranker + Guards. Alle Stufen fail-closed."""
    ctx: dict[str, Any] = {
        "symbol": symbol,
        "now": now,
        "session": dict(session) if isinstance(session, Mapping) else {},
        "screening": dict(screening) if isinstance(screening, Mapping) else None,
        "htf_candles": list(htf_bars or []),
        "ltf_candles": list(ltf_bars or []),
        "htf_interval_min": int(wave_interval_min),
        "ltf_interval_min": int(ltf_interval_min),
    }
    if liquidation_price is not None:
        ctx["liquidation_price"] = float(liquidation_price)
    if leverage is not None:
        ctx["leverage"] = int(leverage)
    if expected_btc_wick_pct is not None:
        ctx["expected_btc_wick_pct"] = float(expected_btc_wick_pct)

    # Stufe 1: 15m-Wave-Evaluation (bestehender Collider, kein Nachbau).
    wave = precomputed_wave
    if wave is None:
        collider = QuantumWaveCollider()
        state = collider.evaluate(
            list(htf_bars or []), interval_min=int(wave_interval_min), now=now
        )
        wave = state.to_dict() if hasattr(state, "to_dict") else dict(state)
    ctx["wave"] = dict(wave)

    # Stufe 2/3: Template-Planung (Retest + Ranker + Guards) — fail-closed.
    intent = plan_sniper(ctx)
    if intent.action != "FLAT":
        details = dict(intent.details)
        details["pipeline"] = {"wave_status": wave.get("status"), "wave_interval_min": wave_interval_min}
        intent = StrategyIntent(
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            action=intent.action,
            side=intent.side,
            volume=intent.volume,
            price=intent.price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            pair=intent.pair,
            execution_mode=intent.execution_mode,
            details=details,
        )
    return intent


def pipeline_wave_status(htf_bars: Optional[Sequence[Mapping[str, Any]]], *, now: Optional[float] = None, wave_interval_min: int = 15) -> str:
    """Nur Stufe 1 als Einzelabfrage (fail-closed: ohne Bars -> kein Status)."""
    if not htf_bars:
        return "NO_DATA"
    state = QuantumWaveCollider().evaluate(list(htf_bars), interval_min=int(wave_interval_min), now=now)
    status = getattr(state, "status", "IDLE")
    return str(status)


__all__ = ["SNIPER_STRATEGY_ID", "run_sniper_pipeline", "pipeline_wave_status"]
