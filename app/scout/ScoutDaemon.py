"""
=========================================================
Datei:      app/scout/ScoutDaemon.py
Zweck:      §19 Loop D — Scout & Incubator.
            Unprofilierte Library-Strategien × Symbol/TF im reinen
            Paper-Modus; Ergebnisse -> Academy/Allocator-Pipeline.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Exploration) / Rouge (Priorisierung)
=========================================================

Kein Live-Kapital. Kein Kraken. Alert-Provisioning höchstens Shadow-Flag.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core import blueprint as bp

logger = logging.getLogger("app.scout.daemon")


@dataclass
class ScoutTask:
    strategy_id: str
    symbol: str
    timeframe: Any
    trades_done: int = 0
    created_at: float = field(default_factory=time.time)
    last_run: float = 0.0
    status: str = "pending"        # pending | running | graduated | retired

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.strategy_id, self.symbol, str(self.timeframe))


class ScoutDaemon:
    """Sucht Paarungen, die noch kein Badge haben, und lässt sie Papier laufen."""

    def __init__(self, allocator=None, backtest_runner=None, virtual_bots=None,
                 symbols: Optional[List[str]] = None, timeframes: Optional[List[Any]] = None,
                 target_sample: int = bp.BADGE_MIN_SAMPLE):
        self.allocator = allocator
        self.backtest_runner = backtest_runner      # callable(strategy_id, symbol, tf) -> result
        self.virtual_bots = virtual_bots
        self.symbols = symbols or ["BTC/USD", "ETH/USD", "XRP/USD"]
        self.timeframes = timeframes or [5, 15, 60]
        self.target_sample = target_sample
        self.tasks: Dict[Tuple[str, str, str], ScoutTask] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.cycles = 0

    # -------------------------------------------------------------- planning
    def plan(self, strategy_ids: Iterable[str], regime: str = bp.Regime.RANGING_CHOP.value
             ) -> List[ScoutTask]:
        """Erzeugt Tasks nur für Paarungen ohne ausreichende Stichprobe."""
        created: List[ScoutTask] = []
        for sid, symbol, tf in itertools.product(strategy_ids, self.symbols, self.timeframes):
            key = (sid, symbol, str(tf))
            if key in self.tasks:
                continue
            if self.allocator is not None:
                prof = self.allocator.get_profile(sid, symbol, tf, regime)
                if prof is not None and prof.trade_count >= self.target_sample:
                    continue    # bereits profiliert -> kein Scout nötig
            task = ScoutTask(sid, symbol, tf)
            self.tasks[key] = task
            created.append(task)
        logger.info("scout planned %d new paper pairings", len(created))
        return created

    # ------------------------------------------------------------- execution
    def run_task(self, task: ScoutTask, regime: str = bp.Regime.RANGING_CHOP.value) -> Dict[str, Any]:
        """Ein Paper-Durchlauf; Ergebnisse fließen in die Academy-Pipeline."""
        task.status = "running"
        task.last_run = time.time()
        trades: List[Dict[str, Any]] = []
        if self.backtest_runner is not None:
            try:
                result = self.backtest_runner(task.strategy_id, task.symbol, task.timeframe)
                trades = list(result.get("trades", []))
            except Exception as exc:
                task.status = "pending"
                logger.error("scout run failed for %s: %s", task.key, exc)
                return {"ok": False, "error": str(exc), "task": task.key}

        ingested = 0
        for trade in trades:
            pnl_pct = float(trade.get("pnlPercent", trade.get("pnl_pct", 0.0)))
            if self.allocator is not None:
                self.allocator.ingest_trade_result(
                    task.strategy_id, task.symbol, task.timeframe, regime, pnl_pct)
            ingested += 1
        task.trades_done += ingested

        graduated = False
        if self.allocator is not None:
            prof = self.allocator.get_profile(task.strategy_id, task.symbol, task.timeframe, regime)
            if prof is not None and prof.trade_count >= self.target_sample:
                task.status = "graduated" if prof.is_allowed else "retired"
                graduated = True
        if not graduated:
            task.status = "pending"
        return {
            "ok": True, "task": task.key, "trades_ingested": ingested,
            "total": task.trades_done, "status": task.status,
            "paper_only": True, "live_capital": False,
        }

    def cycle(self, regime: str = bp.Regime.RANGING_CHOP.value, limit: int = 3) -> List[Dict[str, Any]]:
        """Ein Scout-Durchlauf über die ältesten offenen Tasks."""
        self.cycles += 1
        pending = sorted((t for t in self.tasks.values() if t.status == "pending"),
                         key=lambda t: t.last_run)[:limit]
        return [self.run_task(t, regime) for t in pending]

    # ------------------------------------------------------------- lifecycle
    async def run(self, interval_seconds: int = 900,
                  regime_provider=None) -> None:  # pragma: no cover - Daemon-Loop
        logger.info("scout daemon online (%d symbols × %d tfs)", len(self.symbols), len(self.timeframes))
        while not self._stop.is_set():
            regime = bp.Regime.RANGING_CHOP.value
            if regime_provider is not None:
                try:
                    regime = regime_provider()
                except Exception:
                    pass
            try:
                self.cycle(regime)
            except Exception as exc:
                logger.error("scout cycle failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    def start(self, **kwargs) -> None:  # pragma: no cover
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self.run(**kwargs))

    async def stop(self) -> None:  # pragma: no cover
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    def snapshot(self) -> Dict[str, Any]:
        by_status: Dict[str, int] = {}
        for t in self.tasks.values():
            by_status[t.status] = by_status.get(t.status, 0) + 1
        return {
            "tasks": len(self.tasks), "by_status": by_status, "cycles": self.cycles,
            "symbols": self.symbols, "timeframes": self.timeframes,
            "target_sample": self.target_sample, "mode": "paper_only",
        }


_daemon: Optional[ScoutDaemon] = None


def get_scout(**kwargs) -> ScoutDaemon:
    global _daemon
    if _daemon is None:
        _daemon = ScoutDaemon(**kwargs)
    return _daemon
