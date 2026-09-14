"""
=========================================================
Datei:      app/optimizer/gene_schema.py
Zweck:      §5 Loop B — Parameter-CSV (TV Pine `input.*`) -> GeneSchema.
            Ersetzt `genes_to_params` auf lokale Archetypen durch
            `genes_to_pine_inputs` (Strategy = TradingView).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Optimizer
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from app.backtest.tv_csv import parse_parameter_csv, params_to_csv


@dataclass
class Gene:
    name: str
    value: Any
    low: float = 0.0
    high: float = 0.0
    step: float = 1.0
    kind: str = "int"          # int | float | bool | categorical
    choices: List[Any] = field(default_factory=list)

    def clamp(self, value: Any) -> Any:
        if self.kind == "bool":
            return bool(value)
        if self.kind == "categorical":
            return value if value in self.choices else self.value
        v = float(value)
        v = min(max(v, self.low), self.high)
        if self.step:
            v = round(round(v / self.step) * self.step, 8)
        return int(round(v)) if self.kind == "int" else float(v)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class GeneSchema:
    """Genraum, abgeleitet aus den echten Pine-Inputs der TV-Strategie."""

    def __init__(self, genes: Optional[List[Gene]] = None):
        self.genes: Dict[str, Gene] = {g.name: g for g in (genes or [])}

    # ------------------------------------------------------------ factories
    @classmethod
    def from_parameter_csv(cls, src: Any, bounds: Optional[Mapping[str, tuple]] = None) -> "GeneSchema":
        params = parse_parameter_csv(src)
        return cls.from_params(params, bounds)

    @classmethod
    def from_params(cls, params: Mapping[str, Any],
                    bounds: Optional[Mapping[str, tuple]] = None) -> "GeneSchema":
        bounds = bounds or {}
        genes: List[Gene] = []
        for name, value in params.items():
            if name in bounds:
                low, high = float(bounds[name][0]), float(bounds[name][1])
                kind = "float" if isinstance(value, float) else "int"
                step = 0.1 if kind == "float" else 1
                genes.append(Gene(name, value, low, high, step, kind))
                continue
            genes.append(_infer_gene(name, value))
        return cls(genes)

    # ---------------------------------------------------------------- usage
    def names(self) -> List[str]:
        return list(self.genes)

    def defaults(self) -> Dict[str, Any]:
        return {name: g.value for name, g in self.genes.items()}

    def clamp_all(self, candidate: Mapping[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name, value in candidate.items():
            gene = self.genes.get(name)
            out[name] = gene.clamp(value) if gene else value
        return out

    def genes_to_pine_inputs(self, genome: Mapping[str, Any]) -> Dict[str, Any]:
        """GA-Genom -> TV-Properties (kein lokaler Archetyp mehr)."""
        merged = {**self.defaults(), **dict(genome)}
        return self.clamp_all(merged)

    def to_parameter_csv(self, genome: Mapping[str, Any]) -> str:
        return params_to_csv(self.genes_to_pine_inputs(genome))

    def search_space_size(self) -> float:
        size = 1.0
        for gene in self.genes.values():
            if gene.kind == "bool":
                size *= 2
            elif gene.kind == "categorical":
                size *= max(1, len(gene.choices))
            elif gene.step:
                size *= max(1.0, math.floor((gene.high - gene.low) / gene.step) + 1)
        return size

    def to_dict(self) -> Dict[str, Any]:
        return {"genes": [g.to_dict() for g in self.genes.values()],
                "search_space": self.search_space_size()}


def _infer_gene(name: str, value: Any) -> Gene:
    """Bounds aus Namens-Heuristik + Wert — konservativ, nie exotisch."""
    lname = name.lower()
    if isinstance(value, bool):
        return Gene(name, value, kind="bool")
    # TV exportiert Pine-Booleans als 0/1 — an der Namenskonvention erkennbar
    if value in (0, 1) and any(lname.startswith(tok) for tok in ("use", "enable", "is", "show", "allow")):
        return Gene(name, bool(value), kind="bool")
    if isinstance(value, str):
        return Gene(name, value, kind="categorical", choices=[value])
    numeric = float(value)
    if any(tok in lname for tok in ("period", "length", "ema", "sma", "bars", "lookback")):
        low, high, step, kind = max(2.0, numeric * 0.3), max(5.0, numeric * 3.0), 1, "int"
    elif any(tok in lname for tok in ("multiplier", "mult", "factor", "ratio", "atr")):
        low, high, step, kind = max(0.2, numeric * 0.3), max(1.0, numeric * 3.0), 0.1, "float"
    elif any(tok in lname for tok in ("percent", "pct", "threshold", "level")):
        low, high, step, kind = max(0.0, numeric * 0.3), max(1.0, numeric * 2.0), 0.5, "float"
    else:
        low, high = min(numeric * 0.5, numeric * 1.5), max(numeric * 0.5, numeric * 1.5)
        step, kind = (1, "int") if float(numeric).is_integer() else (0.1, "float")
    if kind == "int":
        return Gene(name, int(numeric), float(int(low)), float(int(high)), step, kind)
    return Gene(name, float(numeric), round(low, 4), round(high, 4), step, kind)
