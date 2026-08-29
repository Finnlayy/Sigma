"""
=========================================================
Datei:      sigma/loops/loop_d.py
Zweck:      LoopDPort.tick() -> list[ScoutGraduation]
            Adapter über ScoutDaemon. Paper-only, niemals Live-Kapital.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Scout) / Jaune (Loop D Port)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.core import blueprint as bp


@dataclass
class ScoutGraduation:
    strategy_id: str = ""
    symbol: str = ""
    timeframe: str = ""
    status: str = "pending"
    paper_only: bool = True
    live_capital: bool = False
    ok: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class LoopDPort:
    """Paper-Incubator. Tick plant/cycled nur ScoutDaemon — kein Kraken."""

    def __init__(self, daemon: Any = None) -> None:
        self.daemon = daemon

    def _daemon(self) -> Any:
        if self.daemon is not None:
            return self.daemon
        from app.scout.ScoutDaemon import get_scout

        self.daemon = get_scout()
        return self.daemon

    def tick(
        self,
        regime: str = bp.Regime.RANGING_CHOP.value,
        strategy_ids: Optional[Iterable[str]] = None,
        limit: int = 3,
        symbols: Optional[Iterable[str]] = None,
        universe: Any = None,
    ) -> List[ScoutGraduation]:
        """Ein Paper-Scout-Tick.

        ``symbols`` = Screen-Symbole (pro Tick, Singleton bleibt
        unverändert); ohne Angabe fällt der Daemon auf seine
        Default-Watchlist zurück. Optional filtert ``universe``
        untradable Symbole raus — Scout plant nie Tasks, die Loop A
        nicht handeln kann. Paper only, niemals Live-Kapital.
        """
        daemon = self._daemon()
        ids = list(strategy_ids) if strategy_ids is not None else []
        plan_symbols = list(symbols) if symbols is not None else None
        if universe is not None and plan_symbols is not None:
            plan_symbols = [s for s in plan_symbols if universe.is_tradable(s)]
        if ids:
            if plan_symbols is not None:
                daemon.plan(ids, regime, symbols=plan_symbols)
            else:
                daemon.plan(ids, regime)
        results = daemon.cycle(regime, limit=limit)
        out: List[ScoutGraduation] = []
        for row in results or []:
            key = row.get("task") or ("", "", "")
            if isinstance(key, (tuple, list)) and len(key) >= 3:
                sid, symbol, tf = key[0], key[1], key[2]
            else:
                sid, symbol, tf = str(key), "", ""
            out.append(ScoutGraduation(
                strategy_id=str(sid),
                symbol=str(symbol),
                timeframe=str(tf),
                status=str(row.get("status") or "pending"),
                paper_only=True,
                live_capital=False,
                ok=bool(row.get("ok")),
                details=dict(row),
            ))
        return out
