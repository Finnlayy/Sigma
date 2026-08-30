"""
=========================================================
Datei:      sigma/loops/loop_e.py
Zweck:      LoopEPort.ingest_result / allocate() -> AlertPlan
            Adapter über AcademyRegistry + StrategyAllocator.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Allocator) / Jaune (Loop E Port)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core import blueprint as bp


@dataclass
class AlertPlan:
    allow: bool = False
    action: str = "none"
    rating: str = ""
    badge: str = ""
    reason: str = ""
    regime: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class LoopEPort:
    """Badges, XP/Strikes, TV-Alert on/off. Orchestrator geht vor Enable durch E."""

    def __init__(self, allocator: Any = None, academy: Any = None) -> None:
        self.allocator = allocator
        self.academy = academy

    def _allocator(self) -> Any:
        if self.allocator is not None:
            return self.allocator
        from app.optimizer.StrategyAllocator import get_allocator

        self.allocator = get_allocator()
        return self.allocator

    def ingest_result(
        self,
        strategy_id: str,
        symbol: str,
        timeframe: Any,
        regime: str,
        pnl_pct: float,
    ) -> Dict[str, Any]:
        return self._allocator().ingest_trade_result(
            strategy_id, symbol, timeframe, regime, pnl_pct,
        )

    def allocate(
        self,
        strategy_id: str = "",
        symbol: str = "BTC/USD",
        timeframe: Any = 15,
        regime: Optional[str] = None,
        *,
        runner_running: bool = True,
    ) -> AlertPlan:
        live_regime = regime or bp.Regime.RANGING_CHOP.value
        alloc = self._allocator()
        if not strategy_id:
            batch = alloc.rebalance(live_regime, [])
            return AlertPlan(
                allow=False, action="none", rating="", badge="",
                reason="no_strategy", regime=live_regime,
                details={"batch": batch, "matrix": alloc.badge_matrix()},
            )
        out = alloc.apply(
            strategy_id, symbol, timeframe, live_regime,
            runner_running=runner_running,
        )
        return AlertPlan(
            allow=bool(out.get("allow")),
            action=str(out.get("action") or "none"),
            rating=str(out.get("rating") or ""),
            badge=str(out.get("badge") or ""),
            reason=str(out.get("reason") or ""),
            regime=live_regime,
            details=dict(out),
        )

    def recalibrate_badges(self) -> Dict[str, Any]:
        """T5 heartbeat — liest Matrix / Academy, ändert keine Live-Size."""
        alloc = self._allocator()
        matrix = alloc.badge_matrix()
        academy_rows: Any = []
        if self.academy is not None and hasattr(self.academy, "list"):
            try:
                academy_rows = self.academy.list()
            except Exception:
                academy_rows = []
        return {
            "profiles": len(matrix),
            "matrix": matrix,
            "academy": academy_rows,
        }
