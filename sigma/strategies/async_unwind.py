"""
=========================================================
Datei:      sigma/strategies/async_unwind.py
Zweck:      MP-08 sequenzierter Unwind (KB §8 Regel 7/8):
            1) Gewinnerseite 100 % schliessen, 2) auf Pullback
            zu VWAP/EMA20 warten (max. Wartezeit, dann Zwang),
            3) Verliererseite schliessen. Net-PnL-Guard:
            Verlust > 50 % des Gewinns -> sofort schliessen,
            forced=True. Minute >= 55 -> alles flat. Nie zwei
            Seiten gleichzeitig (Slippage-Schutz). Paper-only.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template) / Noir (Slippage)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.quant.regime_detector import ema as _ema
from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent

MAX_PULLBACK_WAIT_SECONDS = 600  # max. 10 min auf Pullback warten
TTL_MINUTE = 55                  # spaetestens Minute 55 alles flat
FORCED_LOSS_RATIO = 0.50         # Verlust > 50 % des Gewinns -> forced

WAIT_NONE = "none"
WAIT_PULLBACK = "pullback_to_vwap_ema20"
WAIT_TIMEOUT = "max_wait_timeout"


# ------------------------------------------------------------------ helpers

def _closed_bars(candles: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    rows = list(candles or [])
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


def _v(c: Mapping[str, Any]) -> float:
    return float(c.get("v", c.get("volume", 0.0)) or 0.0)


def vwap(candles: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Einfacher VWAP ueber geschlossene Bars: Summe(typisch x Vol)/Vol."""
    closed = _closed_bars(candles)
    pv = 0.0
    vol = 0.0
    for bar in closed:
        typical = (_h(bar) + _l(bar) + _c(bar)) / 3.0
        v = _v(bar)
        pv += typical * v
        vol += v
    if vol <= 0:
        return None
    return round(pv / vol, 10)


def ema20(candles: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """EMA(20) auf Closes — wiederverwendet app.quant.regime_detector.ema."""
    closed = _closed_bars(candles)
    closes = [_c(c) for c in closed]
    if len(closes) < 20:
        return None
    series = _ema(closes, 20)
    return round(float(series[-1]), 10)


def pullback_level(candles: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Pullback-Ziel: VWAP oder EMA20, je naeher an der letzten Kerze."""
    closed = _closed_bars(candles)
    if not closed:
        return None
    last = _c(closed[-1])
    v = vwap(closed)
    e = ema20(closed)
    candidates = [x for x in (v, e) if x is not None and x > 0]
    if not candidates:
        return None
    return min(candidates, key=lambda x: abs(x - last))


def utc_minute(now_ts: Optional[float]) -> Optional[int]:
    if now_ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(now_ts), tz=timezone.utc).minute
    except (TypeError, ValueError, OSError):
        return None


# ------------------------------------------------------------------- plan

@dataclass(frozen=True)
class UnwindStep:
    """Ein Schritt der Unwind-Sequenz (CLOSE). wait_condition sequenziert:
    der naechste Schritt wartet, bis die Bedingung erfuellt ist."""

    index: int
    action: str
    side: str
    volume: float
    price: float  # 0.0 = Markt
    wait_condition: str
    max_wait_seconds: int
    forced: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class UnwindPlan:
    valid: bool
    reason: str
    steps: List[UnwindStep] = field(default_factory=list)
    forced: bool = False
    ttl_flat: bool = False
    winner_pnl: float = 0.0
    loser_loss: float = 0.0
    net_estimate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "steps": [s.to_dict() for s in self.steps],
            "forced": self.forced,
            "ttl_flat": self.ttl_flat,
            "winner_pnl": self.winner_pnl,
            "loser_loss": self.loser_loss,
            "net_estimate": self.net_estimate,
        }


def plan_unwind(ctx: Optional[Mapping[str, Any]]) -> UnwindPlan:
    """Sequenzierter Unwind-Plan. Reihenfolge: Gewinner -> (Pullback-
    Wartebedingung) -> Verlierer. forced=True, wenn der Verlierer-Verlust
    > 50 % des realisierten Gewinns betraegt (Backflip-Schutz: dann ohne
    Warten schliessen). Minute >= 55 -> beide Seiten sofort flat."""
    empty = UnwindPlan(False, "missing_data")
    if not ctx:
        return empty
    symbol = str(ctx.get("symbol") or "")
    winner_side = str(ctx.get("winner_side") or "").lower()
    loser_side = str(ctx.get("loser_side") or "").lower()
    if not symbol or winner_side not in ("buy", "sell") or loser_side not in ("buy", "sell"):
        return empty
    if winner_side == loser_side:
        return UnwindPlan(False, "sides_must_differ")
    winner_volume = float(ctx.get("winner_volume") or 0.0)
    loser_volume = float(ctx.get("loser_volume") or 0.0)
    if winner_volume <= 0 or loser_volume <= 0:
        return UnwindPlan(False, "missing_volumes")
    winner_pnl = float(ctx.get("winner_pnl") or 0.0)
    loser_loss = float(ctx.get("loser_loss") or 0.0)
    if winner_pnl < 0 or loser_loss < 0:
        return UnwindPlan(False, "negative_pnl_values")

    minute = utc_minute(ctx.get("now"))
    if minute is None:
        minute = ctx.get("minute")
    try:
        minute_i = int(minute)
    except (TypeError, ValueError):
        minute_i = -1
    ttl_flat = minute_i >= TTL_MINUTE

    winner_price = float(ctx.get("winner_price") or 0.0)
    loser_price = float(ctx.get("loser_price") or 0.0)
    forced = loser_loss > FORCED_LOSS_RATIO * max(winner_pnl, 1e-9)

    steps: List[UnwindStep] = []
    steps.append(UnwindStep(
        index=1,
        action="CLOSE",
        side=winner_side,
        volume=round(winner_volume, 10),
        price=round(winner_price, 10),
        wait_condition=WAIT_NONE,
        max_wait_seconds=0,
        forced=bool(ttl_flat),
        reason="ttl_minute_55" if ttl_flat else "realize_winner",
    ))
    if ttl_flat:
        # TTL: Verlierer ebenfalls sofort (kein Pullback-Warten).
        steps.append(UnwindStep(
            index=2,
            action="CLOSE",
            side=loser_side,
            volume=round(loser_volume, 10),
            price=round(loser_price, 10),
            wait_condition=WAIT_NONE,
            max_wait_seconds=0,
            forced=True,
            reason="ttl_minute_55",
        ))
    else:
        candles = ctx.get("candles") or ctx.get("ltf_candles") or []
        level = ctx.get("pullback_level")
        if level is None:
            level = pullback_level(candles)
        price = round(float(level), 10) if level is not None else 0.0
        wait = WAIT_NONE if forced else WAIT_PULLBACK
        steps.append(UnwindStep(
            index=2,
            action="CLOSE",
            side=loser_side,
            volume=round(loser_volume, 10),
            price=price,
            wait_condition=wait,
            max_wait_seconds=0 if forced else MAX_PULLBACK_WAIT_SECONDS,
            forced=forced,
            reason="forced_net_pnl_guard" if forced else "close_loser_at_pullback",
        ))
    return UnwindPlan(
        valid=True,
        reason="ttl_flat" if ttl_flat else "sequenced_unwind",
        steps=steps,
        forced=forced or ttl_flat,
        ttl_flat=ttl_flat,
        winner_pnl=round(winner_pnl, 10),
        loser_loss=round(loser_loss, 10),
        net_estimate=round(winner_pnl - loser_loss, 10),
    )


class AsyncUnwind(BaseStrategy):
    """Unwind-Template: liefert den naechsten Schritt der Sequenz als
    Intent; die vollstaendige Reihenfolge inkl. Wartebedingung steht in
    details['unwind_plan']. Platzierte nie gleichzeitig beide Seiten."""

    STRATEGY_ID = "async_unwind"

    def plan(self, ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
        if not ctx or not ctx.get("symbol"):
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol="", action="FLAT",
                execution_mode="kraken_paper", details={"reason": "missing_data"},
            )
        plan = plan_unwind(ctx)
        if not plan.valid or not plan.steps:
            return StrategyIntent(
                strategy_id=self.STRATEGY_ID, symbol=str(ctx.get("symbol") or ""),
                action="FLAT", execution_mode="kraken_paper",
                details={"reason": plan.reason or "invalid_unwind_plan"},
            )
        step = plan.steps[0]
        return StrategyIntent(
            strategy_id=self.STRATEGY_ID,
            symbol=str(ctx.get("symbol") or ""),
            action=step.action,
            side=step.side,
            volume=step.volume,
            price=step.price,
            stop_loss=0.0,
            take_profit=0.0,
            pair="",
            execution_mode="kraken_paper",
            details={
                "unwind_plan": plan.to_dict(),
                "sequence_index": step.index,
                "wait_condition": step.wait_condition,
                "forced": step.forced,
                "ttl_flat": plan.ttl_flat,
            },
        )


__all__ = [
    "FORCED_LOSS_RATIO",
    "MAX_PULLBACK_WAIT_SECONDS",
    "TTL_MINUTE",
    "AsyncUnwind",
    "UnwindPlan",
    "UnwindStep",
    "WAIT_NONE",
    "WAIT_PULLBACK",
    "WAIT_TIMEOUT",
    "ema20",
    "plan_unwind",
    "pullback_level",
    "utc_minute",
    "vwap",
]
