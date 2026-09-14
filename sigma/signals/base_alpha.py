"""
=========================================================
Datei:      sigma/signals/base_alpha.py
Zweck:      BaseAlphaModel.evaluate(market) -> AlphaSignal
            Fail-closed: fehlende Daten → ungültiges, flaches Signal.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature) / Jaune (Contract)
=========================================================
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass
class AlphaSignal:
    """Library-Signal. ``valid=False`` ist der fail-closed Default."""

    score: float = 0.0
    action: str = "FLAT"
    valid: bool = False
    reason: str = "missing_data"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class BaseAlphaModel(ABC):
    """Alpha-Contract. Keine synthetischen Marktwerte im Produktionspfad."""

    @abstractmethod
    def evaluate(self, market: Optional[Mapping[str, Any]]) -> AlphaSignal:
        """Bewertet einen Markt-Snapshot. Fehlende Serie → invalid FLAT."""

    @staticmethod
    def fail_closed(reason: str = "missing_data") -> AlphaSignal:
        return AlphaSignal(score=0.0, action="FLAT", valid=False, reason=reason)
