"""
=========================================================
Datei:      sigma/signals/quantum_wave_collider.py
Zweck:      15m Dealing-Range / FVG / CE50 → Wave-Regime.
            IDLE | COLLAPSED_INTO_ZONE | INVALIDATED | HTF_OPEN.
            Keine Orders. Loop-A-Gate nur über den Orchestrator.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Regime) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sigma.core.fractal_scaling import (
    SigmaFractalCore,
    candle_close,
    candle_high,
    candle_low,
)
from sigma.signals.dual_hurst import htf_ready
from sigma.signals.htf_features import fvg_flags

if TYPE_CHECKING:  # pragma: no cover - reine Annotations-Typen (Import-Zyklus vermeiden)
    from sigma.execution.universe import ExecutionUniverse

RANGE_LOOKBACK = 20

STATUS_IDLE = "IDLE"
STATUS_COLLAPSED = "COLLAPSED_INTO_ZONE"
STATUS_INVALIDATED = "INVALIDATED"
STATUS_HTF_OPEN = "HTF_OPEN"


@dataclass(frozen=True)
class WaveCollapseState:
    status: str
    range_high: Optional[float]
    range_low: Optional[float]
    ce50: Optional[float]
    eq_pos: Optional[float]
    bullish_fvg: bool
    discount: bool
    fvg_touch: bool
    live_gate: bool
    valid: bool
    reason: str
    interval_min: int

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class WaveScreenCandidate:
    """Tradabler Kollaps: universe.is_tradable(symbol) && COLLAPSED_INTO_ZONE."""

    symbol: str
    state: WaveCollapseState
    tradable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tradable": self.tradable,
            "state": self.state.to_dict(),
        }


@dataclass(frozen=True)
class WaveScreen:
    """Ergebnis des Universe-Screens. Kandidaten sind tradable Kollapse.

    ``defaults`` = universe.list_symbols(): leere Kandidatenliste heißt,
    der Scout fällt auf das Universe zurück (nie auf market_symbols).
    ``states`` enthält alle bewerteten Serien (Observability, auch
    nicht-tradable Kollapse — die sind dort als COLLAPSED sichtbar,
    landen aber nicht in ``candidates``).
    """

    candidates: Tuple[WaveScreenCandidate, ...] = ()
    states: Mapping[str, WaveCollapseState] = field(default_factory=dict)
    defaults: Tuple[str, ...] = ()
    leader: str = "BTC/USD"
    interval_min: int = 15
    now: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "states": {s: st.to_dict() for s, st in self.states.items()},
            "defaults": list(self.defaults),
            "leader": self.leader,
            "interval_min": self.interval_min,
            "now": self.now,
        }


def _empty(status: str, reason: str, interval_min: int) -> WaveCollapseState:
    return WaveCollapseState(
        status=status,
        range_high=None,
        range_low=None,
        ce50=None,
        eq_pos=None,
        bullish_fvg=False,
        discount=False,
        fvg_touch=False,
        live_gate=False,
        valid=False,
        reason=reason,
        interval_min=int(interval_min),
    )


def _structure_and_current(
    closed: Sequence[Mapping[str, Any]],
) -> Tuple[List[Mapping[str, Any]], Optional[Mapping[str, Any]]]:
    if not closed:
        return [], None
    if len(closed) == 1:
        return list(closed), closed[0]
    return list(closed[:-1]), closed[-1]


def _dealing_range(structure: Sequence[Mapping[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    window = list(structure[-RANGE_LOOKBACK:]) if structure else []
    if not window:
        return None, None
    highs = [candle_high(row) for row in window]
    lows = [candle_low(row) for row in window]
    range_high = max(highs)
    range_low = min(lows)
    if range_high <= range_low:
        return None, None
    return range_high, range_low


def _fvg_touch(last: Mapping[str, Any], flags: Mapping[str, Any]) -> bool:
    if not flags.get("bullish_fvg"):
        return False
    gap_low = flags.get("gap_low")
    gap_high = flags.get("gap_high")
    if gap_low is None or gap_high is None:
        return False
    low = float(gap_low)
    high = float(gap_high)
    if high < low:
        low, high = high, low
    last_low = candle_low(last)
    last_high = candle_high(last)
    return last_low <= high and last_high >= low


class QuantumWaveCollider:
    """ICT-zone regime. Metrics always from the closed-bar slice (zero look-ahead)."""

    def __init__(self, core: Optional[SigmaFractalCore] = None) -> None:
        self.core = core or SigmaFractalCore()

    def evaluate(
        self,
        btc_htf: Optional[Sequence[Mapping[str, Any]]],
        alt_htf: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        interval_min: int = 15,
        now: Optional[float] = None,
    ) -> WaveCollapseState:
        if not btc_htf:
            return _empty(STATUS_IDLE, "missing_data", interval_min)

        ready = htf_ready(btc_htf, interval_min, now=now)
        closed_btc = self.core.closed_bars(btc_htf, ready=ready)
        if not closed_btc:
            return _empty(STATUS_HTF_OPEN if not ready else STATUS_IDLE, "htf_open", interval_min)

        structure, current = _structure_and_current(closed_btc)
        range_high, range_low = _dealing_range(structure)
        mid = self.core.ce50(range_high, range_low) if range_high is not None and range_low is not None else None
        flags = fvg_flags(structure) if len(structure) >= 3 else {
            "bullish_fvg": False, "gap_low": None, "gap_high": None,
        }

        price_row = current
        if alt_htf:
            alt_ready = htf_ready(alt_htf, interval_min, now=now)
            closed_alt = self.core.closed_bars(alt_htf, ready=alt_ready)
            if closed_alt:
                price_row = closed_alt[-1]
        if price_row is None:
            return _empty(STATUS_IDLE, "missing_data", interval_min)

        price = candle_close(price_row)
        eq = self.core.eq_pos(range_high, range_low, price) if range_high is not None and range_low is not None else None
        discount = self.core.discount_zone(price, mid)
        touch = _fvg_touch(price_row, flags)
        btc_close = candle_close(current) if current is not None else price

        state = WaveCollapseState(
            status=STATUS_IDLE,
            range_high=range_high,
            range_low=range_low,
            ce50=mid,
            eq_pos=eq,
            bullish_fvg=bool(flags.get("bullish_fvg")),
            discount=discount,
            fvg_touch=touch,
            live_gate=False,
            valid=True,
            reason="idle",
            interval_min=int(interval_min),
        )
        if not ready:
            return WaveCollapseState(
                **{**state.to_dict(), "status": STATUS_HTF_OPEN, "valid": False, "reason": "htf_open"},
            )
        if range_low is not None and btc_close < float(range_low):
            return WaveCollapseState(
                **{**state.to_dict(), "status": STATUS_INVALIDATED, "reason": "range_low_breach"},
            )
        if discount and touch and bool(flags.get("bullish_fvg")):
            return WaveCollapseState(
                **{**state.to_dict(), "status": STATUS_COLLAPSED, "reason": "collapsed_into_zone"},
            )
        return state

    def screen(
        self,
        htf_series: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        universe: "ExecutionUniverse",
        leader: str = "BTC/USD",
        interval_min: int = 15,
        now: Optional[float] = None,
    ) -> WaveScreen:
        """Tradable Watchlist über dem Execution-Universe.

        Kandidat ist nur, wer ``universe.is_tradable(symbol)`` UND
        ``status == COLLAPSED_INTO_ZONE`` ist. Ein Symbol, das Loop C
        kennt, aber die Venue nicht nimmt (z. B. SOL/USD bei der
        heutigen Allowlist), bleibt still — kein Academy-Task.
        ``defaults`` = universe.list_symbols() ist der Scout-Fallback
        bei leerem Screen (nie market_symbols).
        """
        states: Dict[str, WaveCollapseState] = {}
        candidates: List[WaveScreenCandidate] = []
        for symbol, candles in (htf_series or {}).items():
            if not candles:
                continue
            state = self.evaluate(candles, interval_min=interval_min, now=now)
            states[symbol] = state
            if state.status != STATUS_COLLAPSED:
                continue
            tradable = bool(universe.is_tradable(symbol))
            if tradable:
                candidates.append(
                    WaveScreenCandidate(symbol=symbol, state=state, tradable=True)
                )
        return WaveScreen(
            candidates=tuple(candidates),
            states=states,
            defaults=tuple(universe.list_symbols()),
            leader=leader,
            interval_min=int(interval_min),
            now=now,
        )
