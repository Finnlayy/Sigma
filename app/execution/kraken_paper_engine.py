"""
=========================================================
Datei:      app/execution/kraken_paper_engine.py
Zweck:      §32 / Axiom 10 — Kraken Paper Trading Lab & Graduation
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================

Hybrid-Pipeline:

    STUFE 1  TV Backtest (Loop B)   -> DSR >= 0.95, N >= 30
    STUFE 2  Kraken Live Paper      -> echter Ticker, 0 EUR Risiko
    STUFE 3  Live Production        -> nach Graduation

Graduation-Gate (§32.1/§32.3): ``min_paper_trades: 20``, ``PF >= 1.6``,
``WR >= 55 %``. Loop D (Scout) ist **paper-only** — die Engine verweigert
jede Live-Order und protokolliert Fills fuer Academy/ONNX (§32.4).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.execution.kraken_paper_engine")


class PaperOnlyViolation(RuntimeError):
    """§32.4 — Scout/Paper-Kontext darf niemals live ausfuehren."""


@dataclass
class PaperTrade:
    strategy_id: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    pnl_eur: float
    fee_eur: float = 0.0
    slippage_bps: float = 0.0
    latency_ms: float = 0.0
    order_id: str = ""
    ts: float = field(default_factory=time.time)

    @property
    def won(self) -> bool:
        return self.pnl_eur > 0

    def as_dict(self) -> Dict[str, Any]:
        return {**self.__dict__, "won": self.won}


@dataclass
class PaperStats:
    strategy_id: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    balance_usd: float = bp.KRAKEN_PAPER_INITIAL_BALANCE_USD
    graduated: bool = False
    graduated_at: Optional[float] = None

    @property
    def win_rate_pct(self) -> float:
        return (self.wins / self.trades * 100.0) if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss <= 0:
            return float("inf") if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def net_pnl(self) -> float:
        return self.gross_profit - self.gross_loss

    def as_dict(self) -> Dict[str, Any]:
        pf = self.profit_factor
        return {
            "strategy_id": self.strategy_id,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": None if pf == float("inf") else round(pf, 3),
            "net_pnl_eur": round(self.net_pnl, 2),
            "balance_usd": round(self.balance_usd, 2),
            "graduated": self.graduated,
            "graduated_at": self.graduated_at,
        }


class KrakenPaperEngine:
    """Forward-Test am echten Ticker — Stufe 2 der Hybrid-Pipeline (§32)."""

    def __init__(
        self,
        bridge: Any = None,
        *,
        min_trades: int = bp.PAPER_GRADUATION_MIN_TRADES,
        min_profit_factor: float = bp.PAPER_GRADUATION_MIN_PROFIT_FACTOR,
        min_win_rate_pct: float = bp.PAPER_GRADUATION_MIN_WIN_RATE_PCT,
        auto_graduate: bool = True,
        academy=None,
    ) -> None:
        self._bridge = bridge
        self.min_trades = min_trades
        self.min_profit_factor = min_profit_factor
        self.min_win_rate_pct = min_win_rate_pct
        self.auto_graduate = auto_graduate
        self.academy = academy
        self._stats: Dict[str, PaperStats] = {}
        self._trades: List[PaperTrade] = []

    # ----------------------------------------------------------- bridge  ---
    @property
    def bridge(self):
        if self._bridge is None:
            from app.execution.KrakenCliBridge import KrakenCliBridge
            self._bridge = KrakenCliBridge(
                execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, futures=True)
        return self._bridge

    def _assert_paper(self) -> None:
        mode = getattr(self.bridge, "execution_mode", bp.ExecutionMode.KRAKEN_PAPER.value)
        if mode != bp.ExecutionMode.KRAKEN_PAPER.value:
            raise PaperOnlyViolation(
                f"Paper-Lab mit Live-Bridge verdrahtet (mode={mode}) — §32.4 verletzt")

    # ------------------------------------------------------------ orders ---
    def submit_order(self, strategy_id: str, symbol: str, side: str, volume: float,
                     *, ordertype: str = "market", price: Optional[float] = None,
                     stop_price: Optional[float] = None) -> Dict[str, Any]:
        """Paper-Order ueber die Kraken CLI — identische Syntax wie live."""
        self._assert_paper()
        result = self.bridge.add_order(
            pair=symbol, side=side, volume=volume, ordertype=ordertype, price=price,
            stop_price=stop_price, strategy_id=strategy_id)
        stats = self._stats.setdefault(strategy_id, PaperStats(strategy_id))
        return {
            "ok": bool(getattr(result, "ok", False)),
            "mode": getattr(result, "mode", "paper"),
            "order_id": getattr(result, "txid", ""),
            "argv": list(getattr(result, "argv", [])),
            "strategy_id": strategy_id,
            "balance_usd": stats.balance_usd,
        }

    # ------------------------------------------------------------- fills ---
    def record_fill(self, trade: PaperTrade) -> Dict[str, Any]:
        """Realisierten Paper-Trade verbuchen (Academy/ONNX-Telemetrie §32.4)."""
        stats = self._stats.setdefault(trade.strategy_id, PaperStats(trade.strategy_id))
        stats.trades += 1
        if trade.won:
            stats.wins += 1
            stats.gross_profit += trade.pnl_eur
        else:
            stats.losses += 1
            stats.gross_loss += abs(trade.pnl_eur)
        stats.balance_usd += trade.pnl_eur - trade.fee_eur
        self._trades.append(trade)
        del self._trades[:-500]

        if self.academy is not None:
            try:
                self.academy.record_trade(trade.strategy_id, pnl_pct=trade.pnl_eur)
            except Exception as exc:  # pragma: no cover - Academy darf nie blocken
                logger.warning("academy record failed: %s", exc)

        outcome = self.graduation_status(trade.strategy_id)
        if self.auto_graduate and outcome["eligible"] and not stats.graduated:
            self.graduate(trade.strategy_id, reason="auto_graduation")
            outcome = self.graduation_status(trade.strategy_id)
        return {"stats": stats.as_dict(), "graduation": outcome}

    # -------------------------------------------------------- graduation ---
    def graduation_status(self, strategy_id: str) -> Dict[str, Any]:
        stats = self._stats.get(strategy_id) or PaperStats(strategy_id)
        pf = stats.profit_factor
        gates = {
            "min_paper_trades": (stats.trades >= self.min_trades,
                                 stats.trades, self.min_trades),
            "min_paper_profit_factor": (pf >= self.min_profit_factor,
                                        None if pf == float("inf") else round(pf, 3),
                                        self.min_profit_factor),
            "min_paper_win_rate_pct": (stats.win_rate_pct >= self.min_win_rate_pct,
                                       round(stats.win_rate_pct, 2),
                                       self.min_win_rate_pct),
        }
        failed = [name for name, (ok, _, _) in gates.items() if not ok]
        return {
            "strategy_id": strategy_id,
            "stage": 3 if stats.graduated else 2,
            "eligible": not failed,
            "graduated": stats.graduated,
            "failed_gates": failed,
            "gates": {name: {"passed": ok, "value": value, "required": required}
                      for name, (ok, value, required) in gates.items()},
        }

    def graduate(self, strategy_id: str, *, reason: str = "operator",
                 force: bool = False) -> Dict[str, Any]:
        """Stufe 2 -> Stufe 3. Ohne ``force`` nur bei erfuellten Gates."""
        status = self.graduation_status(strategy_id)
        if not status["eligible"] and not force:
            return {"promoted": False, "reason": "GRADUATION_GATES_FAILED",
                    "failed_gates": status["failed_gates"], "status": status}
        stats = self._stats.setdefault(strategy_id, PaperStats(strategy_id))
        stats.graduated = True
        stats.graduated_at = time.time()
        logger.info("paper strategy %s graduated to live (%s)", strategy_id, reason)
        return {"promoted": True, "reason": reason, "forced": force,
                "execution_mode": bp.ExecutionMode.LIVE.value,
                "status": self.graduation_status(strategy_id)}

    def demote(self, strategy_id: str, reason: str = "risk") -> Dict[str, Any]:
        stats = self._stats.setdefault(strategy_id, PaperStats(strategy_id))
        stats.graduated = False
        stats.graduated_at = None
        return {"demoted": True, "reason": reason,
                "execution_mode": bp.ExecutionMode.KRAKEN_PAPER.value}

    def execution_mode_for(self, strategy_id: str) -> str:
        """Welcher Modus ist fuer die Strategie zulaessig? (Dispatcher-Hook)"""
        stats = self._stats.get(strategy_id)
        if stats and stats.graduated:
            return bp.ExecutionMode.LIVE.value
        return bp.ExecutionMode.KRAKEN_PAPER.value

    # -------------------------------------------------------- telemetrie ---
    def stats(self, strategy_id: str) -> Dict[str, Any]:
        stats = self._stats.get(strategy_id) or PaperStats(strategy_id)
        return stats.as_dict()

    def trades(self, strategy_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        rows = [t for t in self._trades
                if not strategy_id or t.strategy_id == strategy_id]
        return [t.as_dict() for t in rows[-limit:]]

    def panel_state(self, limit: int = 50) -> Dict[str, Any]:
        return {
            "enabled": bp.KRAKEN_PAPER_ENABLED,
            "initial_balance_usd": bp.KRAKEN_PAPER_INITIAL_BALANCE_USD,
            "demo_futures_url": bp.KRAKEN_DEMO_FUTURES_URL,
            "commands": dict(bp.KRAKEN_PAPER_COMMANDS),
            "graduation": {
                "min_paper_trades": self.min_trades,
                "min_paper_profit_factor": self.min_profit_factor,
                "min_paper_win_rate_pct": self.min_win_rate_pct,
                "auto_graduate": self.auto_graduate,
            },
            "strategies": [self.graduation_status(sid) | {"stats": s.as_dict()}
                           for sid, s in self._stats.items()],
            "trades": self.trades(limit=limit),
        }


_ENGINE: Optional[KrakenPaperEngine] = None


def get_paper_engine(**kwargs: Any) -> KrakenPaperEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = KrakenPaperEngine(**kwargs)
    return _ENGINE


def set_paper_engine(engine: Optional[KrakenPaperEngine]) -> None:
    global _ENGINE
    _ENGINE = engine
