"""
=========================================================
Datei:      sigma/strategies/dca_ladder.py
Zweck:      Micro-DCA-Ladder-Generator (KB §5.1): geometrisch
            wachsende Sprossen, 1,15x-Volumen, Range-basierter
            dynamischer Step (0.618), Avg/TP/TTL pure Funktionen.
            Tiefen-Guard kommt aus MP-01 (risk_guards) — KEIN
            lokaler Nachbau. Paper-only, kein Deploy.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template) / Jaune (Contract)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence

from sigma.execution.risk_guards import (
    MIN_MEME_GRID_DEPTH,
    GridDepthVerdict,
    assert_grid_depth,
)
from sigma.strategies.base_strategy import StrategyIntent

# ---------------------------------------------------------------------------
# Named constants (JULES MP-02 / KB §5.1)
# ---------------------------------------------------------------------------

DEFAULT_STEP_PCT = 0.002          # 0.20 % default step (example uses 0.15 %)
DEFAULT_STEP_MULT = 1.10          # geometric growth of successive gaps
DEFAULT_VOLUME_MULT = 1.15
DEFAULT_N_SAFETY = 6
DEFAULT_TP_PCT = 0.015            # TP vs avg fill, never vs entry
LADDER_TTL_SECONDS = 7200         # max 2 h per bot run
SPREAD_FEE_FLOOR_PCT = 0.001      # first step never < spread+fees (0.10 %)
RANGE_FACTOR = 0.618

# Alias kept for call-sites that imported the pre-MP-02 name.
SPREAD_FEE_FLOOR = SPREAD_FEE_FLOOR_PCT
MIN_MEME_DEPTH = MIN_MEME_GRID_DEPTH


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LadderRung:
    """Eine Sprosse der DCA-Leiter (MP-02 Vertrag)."""

    price: float
    margin_share: float             # Margin-Anteil (Dezimal vom Entry-Budget)
    cumulative_depth_pct: float     # Distanz vom Entry in % (Dezimal)
    volume_mult_applied: float      # relatives Volumen (Entry = 1.0)
    index: int = 0

    @property
    def volume(self) -> float:
        """Alias — Quantum-Sniper und aeltere Call-Sites nutzen .volume."""
        return self.volume_mult_applied

    @property
    def margin_pct(self) -> float:
        """Alias fuer margin_share."""
        return self.margin_share

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "price": self.price,
            "margin_share": self.margin_share,
            "cumulative_depth_pct": self.cumulative_depth_pct,
            "volume_mult_applied": self.volume_mult_applied,
            "volume": self.volume_mult_applied,
            "margin_pct": self.margin_share,
        }


@dataclass(frozen=True)
class DcaLadder:
    """Komplette Micro-DCA-Leiter inkl. Validierungs-Flags (MP-02 Vertrag)."""

    rungs: List[LadderRung]
    side: str
    avg_price: float
    tp_price: float
    ttl_seconds: int = LADDER_TTL_SECONDS
    accepted: bool = True
    reject_reason: str = ""
    # Build metadata (kept for sizing / audit; not invented on reject)
    entry_price: float = 0.0
    n_safety: int = 0
    step_pct: float = 0.0
    step_mult: float = 0.0
    volume_mult: float = 0.0
    base_margin_pct: float = 0.0

    @property
    def total_depth_pct(self) -> float:
        """Gesamt-Tiefe der Leiter in % des Entry (Dezimal, 0.06 = 6 %)."""
        if not self.rungs:
            return 0.0
        return float(self.rungs[-1].cumulative_depth_pct)

    @property
    def first_step_pct(self) -> float:
        """Abstand Entry -> erste Safety-Sprosse in % (Dezimal)."""
        if len(self.rungs) < 2:
            return 0.0
        return float(self.rungs[1].cumulative_depth_pct)

    @property
    def take_profit(self) -> float:
        return float(self.tp_price)

    @property
    def ok(self) -> bool:
        """Alias fuer accepted — aeltere Tests/Call-Sites."""
        return bool(self.accepted)

    @property
    def reason(self) -> str:
        return str(self.reject_reason)

    def average_fill_price(self, filled: Optional[Sequence[int]] = None) -> float:
        """Echtgewichteter Avg-Preis ueber gefuellte Sprossen."""
        if filled is None:
            return float(self.avg_price)
        return average_fill_price([
            self.rungs[i] for i in filled if 0 <= i < len(self.rungs)
        ])

    def ttl_expired(self, opened_ts: float, now_ts: float) -> bool:
        return ttl_expired(opened_ts, now_ts, ttl_seconds=self.ttl_seconds)

    def flat_intent(self, symbol: str, strategy_id: str = "dca_ladder") -> StrategyIntent:
        """TTL-Ende / Flat: FLAT-Intent, nie eine Order."""
        return StrategyIntent(
            strategy_id=strategy_id,
            symbol=symbol,
            action="FLAT",
            execution_mode="kraken_paper",
            details={"reason": "ttl_expired", "ttl_seconds": self.ttl_seconds},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rungs": [r.to_dict() for r in self.rungs],
            "side": self.side,
            "avg_price": self.avg_price,
            "tp_price": self.tp_price,
            "ttl_seconds": self.ttl_seconds,
            "accepted": self.accepted,
            "reject_reason": self.reject_reason,
            "entry_price": self.entry_price,
            "n_safety": self.n_safety,
            "step_pct": self.step_pct,
            "step_mult": self.step_mult,
            "volume_mult": self.volume_mult,
            "base_margin_pct": self.base_margin_pct,
            "total_depth_pct": self.total_depth_pct,
            "first_step_pct": self.first_step_pct,
            "take_profit": self.tp_price,
            "average_fill_price": self.avg_price,
        }


# ---------------------------------------------------------------------------
# Pure Funktionen
# ---------------------------------------------------------------------------


def _cumulative_distances(n_safety: int, step_pct: float, step_mult: float) -> List[float]:
    """Kumulierte Distanzen der Safety-Sprossen.

    Recurrence (documented): gap for safety rung k (k=1..n) is
    ``step_pct * step_mult**(k-1)``; cumulative depth is the running sum.
    With ``step_mult == 1`` this is a uniform ladder; with 1.10 gaps grow.
    """
    dists: List[float] = []
    cum = 0.0
    for k in range(1, n_safety + 1):
        cum += step_pct * (step_mult ** (k - 1))
        dists.append(cum)
    return dists


def build_ladder(
    entry_price: float,
    *,
    side: str = "buy",
    n_safety: int = DEFAULT_N_SAFETY,
    step_pct: float = DEFAULT_STEP_PCT,
    step_mult: float = DEFAULT_STEP_MULT,
    base_margin_pct: float,
    volume_mult: float = DEFAULT_VOLUME_MULT,
) -> DcaLadder:
    """Baut eine Micro-DCA-Leiter: Entry-Sprosse + n_safety Safety-Sprossen.

    Long: Sprossen unter Entry; Short: Sprossen ueber Entry.
    Gap_k = step_pct * step_mult**(k-1); Volumen_i = volume_mult**i.
    """
    if entry_price <= 0:
        raise ValueError("entry_price muss > 0 sein")
    if n_safety < 0 or n_safety > 64:
        raise ValueError("n_safety ausserhalb 0..64")
    if step_pct <= 0 or step_mult < 1.0 or volume_mult <= 0 or base_margin_pct <= 0:
        raise ValueError("step_pct > 0, step_mult >= 1, volume_mult > 0, base_margin_pct > 0")
    side_n = (side or "").lower()
    if side_n not in ("buy", "long", "sell", "short"):
        raise ValueError(f"side muss buy/long/sell/short sein, ist {side!r}")
    is_long = side_n in ("buy", "long")
    sign = -1.0 if is_long else 1.0

    rungs: List[LadderRung] = [
        LadderRung(
            index=0,
            price=float(entry_price),
            volume_mult_applied=1.0,
            margin_share=float(base_margin_pct),
            cumulative_depth_pct=0.0,
        )
    ]
    for i, cum in enumerate(_cumulative_distances(n_safety, step_pct, step_mult), start=1):
        price = entry_price * (1.0 + sign * cum)
        volume = volume_mult ** i
        rungs.append(
            LadderRung(
                index=i,
                price=round(price, 10),
                volume_mult_applied=volume,
                margin_share=round(base_margin_pct * volume, 10),
                cumulative_depth_pct=round(cum, 10),
            )
        )
    avg = average_fill_price(rungs)
    side_out = "buy" if is_long else "sell"
    tp = take_profit_price(avg, side_out, tp_pct=DEFAULT_TP_PCT)
    return DcaLadder(
        rungs=rungs,
        side=side_out,
        avg_price=float(avg),
        tp_price=float(tp),
        ttl_seconds=LADDER_TTL_SECONDS,
        accepted=True,
        reject_reason="",
        entry_price=float(entry_price),
        n_safety=n_safety,
        step_pct=float(step_pct),
        step_mult=float(step_mult),
        volume_mult=float(volume_mult),
        base_margin_pct=float(base_margin_pct),
    )


def dynamic_step_from_range(
    high_2h: float,
    low_2h: float,
    current_price: float,
    n_safety: int,
    range_factor: float = RANGE_FACTOR,
) -> float:
    """Dynamischer Step aus der 1-2h-Range (KB §5.1):
    ((high-low)/current_price * range_factor) / n_safety."""
    if current_price <= 0 or n_safety <= 0:
        raise ValueError("current_price > 0 und n_safety > 0 erforderlich")
    if high_2h < low_2h:
        raise ValueError("high_2h darf nicht kleiner als low_2h sein")
    if range_factor <= 0 or range_factor > 1.0:
        raise ValueError("range_factor muss in (0, 1] liegen")
    range_pct = (high_2h - low_2h) / current_price
    return round(range_pct * range_factor / n_safety, 10)


def average_fill_price(filled_rungs: Sequence[LadderRung]) -> float:
    """Echtgewichteter Avg-Preis: Summe(Preis*Volumen) / Summe(Volumen)."""
    tot_v = 0.0
    tot_pv = 0.0
    for rung in filled_rungs:
        v = float(rung.volume_mult_applied)
        tot_v += v
        tot_pv += float(rung.price) * v
    if tot_v <= 0:
        return 0.0
    return round(tot_pv / tot_v, 10)


def take_profit_price(
    avg_price: float,
    side: str,
    tp_pct: float = DEFAULT_TP_PCT,
) -> float:
    """TP relativ zum AVG-Preis (nicht Entry): long +tp_pct, short -tp_pct."""
    if avg_price <= 0:
        raise ValueError("avg_price muss > 0 sein")
    side_n = (side or "").lower()
    if side_n in ("buy", "long"):
        return round(avg_price * (1.0 + tp_pct), 10)
    if side_n in ("sell", "short"):
        return round(avg_price * (1.0 - tp_pct), 10)
    raise ValueError(f"side muss buy/long/sell/short sein, ist {side!r}")


def ttl_expired(
    opened_ts: float,
    now_ts: float,
    ttl_seconds: int = LADDER_TTL_SECONDS,
) -> bool:
    """True, wenn die TTL (Default 2 h) abgelaufen ist."""
    return (now_ts - opened_ts) > ttl_seconds


def validate_ladder(
    ladder: DcaLadder,
    symbol_spec: Optional[Any],
    *,
    spread_pct: float = 0.0,
    fee_floor_pct: float = SPREAD_FEE_FLOOR_PCT,
) -> DcaLadder:
    """MP-01-Guard + Spread/Fee-Floor. Returns the same ladder shape.

    On failure: ``accepted=False`` and ``reject_reason`` set — never invents
    a passing ladder (rungs unchanged).
    """
    if spread_pct < 0 or fee_floor_pct <= 0:
        raise ValueError("spread_pct >= 0 und fee_floor_pct > 0 erforderlich")
    depth_verdict: GridDepthVerdict = assert_grid_depth(
        ladder.total_depth_pct, symbol_spec, min_meme_depth=MIN_MEME_GRID_DEPTH
    )
    first_step = ladder.first_step_pct
    floor = spread_pct + fee_floor_pct
    first_ok = first_step >= floor
    if not depth_verdict.ok:
        return replace(ladder, accepted=False, reject_reason="depth_rejected")
    if not first_ok:
        return replace(
            ladder,
            accepted=False,
            reject_reason="first_step_below_spread_fee_floor",
        )
    return replace(ladder, accepted=True, reject_reason="")


__all__ = [
    "DEFAULT_N_SAFETY",
    "DEFAULT_STEP_MULT",
    "DEFAULT_STEP_PCT",
    "DEFAULT_TP_PCT",
    "DEFAULT_VOLUME_MULT",
    "DcaLadder",
    "LADDER_TTL_SECONDS",
    "LadderRung",
    "MIN_MEME_DEPTH",
    "RANGE_FACTOR",
    "SPREAD_FEE_FLOOR",
    "SPREAD_FEE_FLOOR_PCT",
    "average_fill_price",
    "build_ladder",
    "dynamic_step_from_range",
    "take_profit_price",
    "ttl_expired",
    "validate_ladder",
]
