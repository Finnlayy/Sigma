"""
=========================================================
Datei:      sigma/strategies/base_strategy.py
Zweck:      BaseStrategy.plan(ctx) -> StrategyIntent
            Intents sind Schema-A-fähig; Live nur über Loop A / Kraken CLI.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Allokation) / Jaune (Contract)
=========================================================
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass
class StrategyIntent:
    """Library-Intent. Wird von Loop A als Schema-A / SignalRequest konsumiert."""

    strategy_id: str
    symbol: str
    action: str = "FLAT"
    side: str = ""
    volume: float = 0.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pair: str = ""
    execution_mode: str = "kraken_paper"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class BaseStrategy(ABC):
    """Strategy ≡ TradingView Pine v6. Templates planen nur Intents, sie füllen nicht."""

    @abstractmethod
    def plan(self, ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
        """Leitet aus Kontext einen Intent ab. Fehlender Kontext → FLAT / volume 0."""
