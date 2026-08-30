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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sigma.execution.risk_guards import GridDepthVerdict, assert_grid_depth
from sigma.strategies.base_strategy import StrategyIntent

# ---------------------------------------------------------------------------
# Konstanten (KB §5.1 / §8)
# ---------------------------------------------------------------------------

LADDER_TTL_SECONDS = 7200  # max. 2 h je Bot-Lauf
SPREAD_FEE_FLOOR = 0.001   # erster Step nie kleiner als Spread+Fees (0,10 %)
MIN_MEME_DEPTH = 0.06      # MP-01-Hartregel: Meme-Perp >= 6 % Gesamt-Tiefe


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LadderRung:
    """Eine Sprosse der DCA-Leiter."""

    index: int
    price: float
    volume: float                 # relatives Volumen (entry = 1.0)
    margin_pct: float             # Margin-Anteil in % des Entry (Dezimal)
    cumulative_depth_pct: float   # kumulierte Distanz vom Entry in % (Dezimal)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class DcaLadder:
    """Komplette Micro-DCA-Leiter inkl. abgeleiteter Kennzahlen."""

    entry_price: float
    side: str
    n_safety: int
    step_pct: float
    step_mult: float
    volume_mult: float
    base_margin_pct: float
    rungs: List[LadderRung] = field(default_factory=list)
    ttl_seconds: int = LADDER_TTL_SECONDS

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

    def average_fill_price(self, filled: Optional[Sequence[int]] = None) -> float:
        """Echtgewichteter Avg-Preis ueber gefuellte Sprossen."""
        return average_fill_price(self.rungs if filled is None else [
            self.rungs[i] for i in filled if 0 <= i < len(self.rungs)
        ])

    @property
    def take_profit(self) -> float:
        """TP 1,5 % ueber/unter dem Avg-Preis (nicht Entry)."""
        return take_profit_price(self.average_fill_price(), self.side)

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
        d = dict(self.__dict__)
        d["rungs"] = [r.to_dict() for r in self.rungs]
        d["total_depth_pct"] = self.total_depth_pct
        d["first_step_pct"] = self.first_step_pct
        d["average_fill_price"] = self.average_fill_price()
        d["take_profit"] = self.take_profit
        return d


@dataclass(frozen=True)
class LadderValidation:
    """Validierungsresultat: MP-01-Tiefen-Guard + Spread/Fee-Floor."""

    ok: bool
    depth: Dict[str, Any]
    first_step_pct: float
    floor_pct: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Pure Funktionen
# ---------------------------------------------------------------------------


def _cumulative_distances(n_safety: int, step_pct: float, step_mult: float) -> List[float]:
    """Kumulierte Distanzen der Safety-Sprossen: Schrittweite waechst
    geometrisch um step_mult (distance_k = step_pct * step_mult^(k-1))."""
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
    n_safety: int = 6,
    step_pct: float = 0.002,
    step_mult: float = 1.10,
    base_margin_pct: float,
    volume_mult: float = 1.15,
) -> DcaLadder:
    """Baut eine Micro-DCA-Leiter: Entry-Sprosse + n_safety Safety-Sprossen.
    Long: Sprossen unter Entry; Short: Sprossen ueber Entry. Schrittweite
    waechst geometrisch um step_mult, Volumen um volume_mult."""
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
            volume=1.0,
            margin_pct=float(base_margin_pct),
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
                volume=volume,
                margin_pct=round(base_margin_pct * volume, 10),
                cumulative_depth_pct=round(cum, 10),
            )
        )
    return DcaLadder(
        entry_price=float(entry_price),
        side="buy" if is_long else "sell",
        n_safety=n_safety,
        step_pct=float(step_pct),
        step_mult=float(step_mult),
        volume_mult=float(volume_mult),
        base_margin_pct=float(base_margin_pct),
        rungs=rungs,
    )


def dynamic_step_from_range(
    high_2h: float,
    low_2h: float,
    current_price: float,
    n_safety: int,
    range_factor: float = 0.618,
) -> float:
    """Dynamischer Step aus der 1-2h-Range (KB §5.1):
    (Range / Preis * 0.618) / Stufen. Range/Preis als Dezimal-Abstand."""
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
        v = float(rung.volume)
        tot_v += v
        tot_pv += float(rung.price) * v
    if tot_v <= 0:
        return 0.0
    return round(tot_pv / tot_v, 10)


def take_profit_price(avg_price: float, side: str, tp_pct: float = 0.015) -> float:
    """TP relativ zum AVG-Preis (nicht Entry): long +tp_pct, short -tp_pct."""
    if avg_price <= 0:
        raise ValueError("avg_price muss > 0 sein")
    side_n = (side or "").lower()
    if side_n in ("buy", "long"):
        return round(avg_price * (1.0 + tp_pct), 10)
    if side_n in ("sell", "short"):
        return round(avg_price * (1.0 - tp_pct), 10)
    raise ValueError(f"side muss buy/long/sell/short sein, ist {side!r}")


def ttl_expired(opened_ts: float, now_ts: float, ttl_seconds: int = LADDER_TTL_SECONDS) -> bool:
    """True, wenn die TTL (Default 2 h) abgelaufen ist."""
    return (now_ts - opened_ts) > ttl_seconds


def validate_ladder(
    ladder: DcaLadder,
    symbol_spec: Optional[Any],
    *,
    spread_pct: float = 0.0,
    fee_floor_pct: float = SPREAD_FEE_FLOOR,
) -> LadderValidation:
    """MP-01-Guard-Integration (assert_grid_depth importiert, NICHT nachgebaut):
    Gesamt-Tiefe >= 6 % (Meme-Perp) UND erster Step >= Spread + Fee-Floor."""
    if spread_pct < 0 or fee_floor_pct <= 0:
        raise ValueError("spread_pct >= 0 und fee_floor_pct > 0 erforderlich")
    depth_verdict: GridDepthVerdict = assert_grid_depth(
        ladder.total_depth_pct, symbol_spec, min_meme_depth=MIN_MEME_DEPTH
    )
    first_step = ladder.first_step_pct
    floor = spread_pct + fee_floor_pct
    first_ok = first_step >= floor
    ok = depth_verdict.ok and first_ok
    reason = (
        "depth_rejected" if not depth_verdict.ok
        else "first_step_below_spread_fee_floor" if not first_ok
        else "ladder_ok"
    )
    return LadderValidation(
        ok=ok,
        depth=depth_verdict.to_dict(),
        first_step_pct=first_step,
        floor_pct=round(floor, 10),
        reason=reason,
    )


__all__ = [
    "DcaLadder",
    "LADDER_TTL_SECONDS",
    "LadderRung",
    "LadderValidation",
    "MIN_MEME_DEPTH",
    "SPREAD_FEE_FLOOR",
    "average_fill_price",
    "build_ladder",
    "dynamic_step_from_range",
    "take_profit_price",
    "ttl_expired",
    "validate_ladder",
]
