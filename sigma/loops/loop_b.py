"""
=========================================================
Datei:      sigma/loops/loop_b.py
Zweck:      LoopBPort.optimize(strategy_id, gene_bounds) -> BacktestResult
            Adapter über GeneticOptimizer + TvMcpBacktest. Kein 24/7-Poll.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Loop B Port)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class BacktestResult:
    ok: bool = False
    strategy_id: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class LoopBPort:
    """On-demand GA / Playwright-Compile. Ohne Kerzen → empty, kein Fake-Sharpe."""

    def __init__(self, optimizer: Any = None, adapter: Any = None) -> None:
        self.optimizer = optimizer
        self.adapter = adapter

    def optimize(
        self,
        strategy_id: str,
        gene_bounds: Optional[Mapping[str, Any]] = None,
        *,
        candles: Optional[List[Dict[str, Any]]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> BacktestResult:
        if not candles:
            return BacktestResult(
                ok=False, strategy_id=strategy_id, reason="missing_candles",
            )
        cfg = dict(config or {})
        cfg.setdefault("id", strategy_id)
        if gene_bounds:
            cfg["customParameters"] = dict(gene_bounds)
        ga = self.optimizer
        if ga is None:
            from app.optimizer.GeneticOptimizer import GeneticOptimizer

            ga = GeneticOptimizer()
        raw = ga.run(cfg, candles)
        summary = {}
        if isinstance(raw, dict):
            summary = raw.get("summary") or raw.get("best") or {}
        return BacktestResult(
            ok=True, strategy_id=strategy_id, summary=summary if isinstance(summary, dict) else {},
            raw=raw if isinstance(raw, dict) else {"result": raw},
        )

    def paper_hypotheses(
        self,
        htf_candles: Optional[List[Dict[str, Any]]] = None,
        ltf_candles: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """H1–H5 paper-only. H3/H4 stay off until they replicate."""
        from sigma.strategies.h_tests import H_DEFAULTS, run_paper_hypotheses

        rows = run_paper_hypotheses(htf_candles, ltf_candles, **kwargs)
        return {
            "ok": True,
            "mode": "paper",
            "live": False,
            "defaults": dict(H_DEFAULTS),
            "results": [r.to_dict() for r in rows],
        }
