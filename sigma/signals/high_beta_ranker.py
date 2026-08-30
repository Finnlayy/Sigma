"""
=========================================================
Datei:      sigma/signals/high_beta_ranker.py
Zweck:      High-Beta-Symbol-Ranker (KB §6): signed r/beta gegen
            Dual-Dirigent BTC/ETH, RVOL, Spread-Penalty, pos_EQ,
            24h-Relativstaerke; getrennte Long-/Short-Rankings.
            Inverse Longs + decoupled + thin book + unlock fail-closed.
            Erweitert correlation_scout (keine zweite r/beta-Logik).
            Nur closed 1h-Bars; kein Look-ahead; keine Orders.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Scout) / Jaune (Contract)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sigma.execution.risk_guards import liquidation_proximity_pct
from sigma.signals.correlation_scout import _closes, _corr_beta, _returns

# Hard-Filter-Schwellen (KB §6, Defaults als Benennungskonstanten)
R_THRESHOLD = 0.75        # |r| >= 0.75 (signiert)
BETA_THRESHOLD = 1.5      # |beta| >= 1.5
RVOL_THRESHOLD = 1.5      # Volumen-Ratio >= 1.5
SPREAD_CAP = 0.0008       # 0,08 % Spread-Cap
DECOUPLED_THRESHOLD = 0.30  # |r| < 0.30 -> decoupled (fail-closed)
SNIPER_BETA = 2.8         # Empfehlung sniper_hedge
SNIPER_RVOL = 2.5
SNIPER_MAX_LIQ_DISTANCE = 0.10  # "Liq-Puffer klein" fuer Sniper-Modus
# MP-15: extreme beta/RVOL -> fraktaler Einzeltrade (KB §5.5, 20-50x);
# moderat -> Sniper/DCA-Pfade.
FRACTAL_BETA = 3.5
FRACTAL_RVOL = 3.0
POS_EQ_CONSOLIDATION_MIN = 0.40
POS_EQ_CONSOLIDATION_MAX = 0.65
POS_EQ_CHASING = 0.90
WINDOW = 48
EQ_WINDOW = 20


@dataclass(frozen=True)
class RankedSymbol:
    """Ein bewertetes Symbol. direction LONG/SHORT/FLAT; entry_ready=False
    in der Chasing-Zone; reasons traegt alle Filter-/Blacklist-Gruende."""

    symbol: str
    conductor: str
    r: float
    beta: float
    rvol: float
    spread_pct: float
    spread_penalty: float
    perf_24h_pct: Optional[float]
    pos_eq: Optional[float]
    direction: str
    score: float
    recommendation: str
    entry_ready: bool
    post_breakout_consolidation: bool
    weekend_paper_only: bool
    needs_hitl: bool
    liq_distance_pct: Optional[float]
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class RankerResult:
    """Ergebnis eines 1h-Scans: getrennte Long-/Short-Rankings."""

    bar_ts: int
    long_rank: List[RankedSymbol]
    short_rank: List[RankedSymbol]
    filtered: List[RankedSymbol]
    conductors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_ts": self.bar_ts,
            "long_rank": [r.to_dict() for r in self.long_rank],
            "short_rank": [r.to_dict() for r in self.short_rank],
            "filtered": [r.to_dict() for r in self.filtered],
            "conductors": list(self.conductors),
        }


class HighBetaRanker:
    """Erweitert den CorrelationScout-Vertrag: signed r/beta pro Symbol gegen
    BTC UND ETH; Dirigent = Benchmark mit hoeherem |r| (KB §6 Dual-Dirigent)."""

    def __init__(
        self,
        *,
        r_threshold: float = R_THRESHOLD,
        beta_threshold: float = BETA_THRESHOLD,
        rvol_threshold: float = RVOL_THRESHOLD,
        spread_cap: float = SPREAD_CAP,
        window: int = WINDOW,
    ) -> None:
        self.r_threshold = r_threshold
        self.beta_threshold = beta_threshold
        self.rvol_threshold = rvol_threshold
        self.spread_cap = spread_cap
        self.window = max(8, int(window))

    def rank(
        self,
        series: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
        *,
        bar_ts: int,
        conductor_symbols: Sequence[str] = ("BTC/USD", "ETH/USD"),
        weekend: bool = False,
        unlock_symbols: Sequence[str] = (),
        thin_book_symbols: Sequence[str] = (),
        spreads: Optional[Mapping[str, float]] = None,
        liq_distances: Optional[Mapping[str, float]] = None,
    ) -> RankerResult:
        """Rankt alle Symbole gegen den staerker korrelierten Dirigenten.
        Nur geschlossene Bars (letzte offene Bar wird ignoriert)."""
        unlock = {str(s) for s in unlock_symbols}
        thin = {str(s) for s in thin_book_symbols}
        if not series:
            return RankerResult(bar_ts=int(bar_ts), long_rank=[], short_rank=[],
                                filtered=[], conductors=list(conductor_symbols))

        rows: List[RankedSymbol] = []
        for symbol, candles in series.items():
            if symbol in conductor_symbols:
                continue
            closed = _closed_bars(candles)
            closes = _closes(closed)
            if len(closes) < 2:
                rows.append(_flat_row(symbol, ["short_series"]))
                continue

            # Dual-Dirigent: r/beta gegen BTC und ETH, Dirigent = max |r|
            best: Optional[Dict[str, float]] = None
            for cond in conductor_symbols:
                cond_closes = _closes(_closed_bars(series.get(cond) or []))
                n = min(len(closes), len(cond_closes))
                if n < 8:
                    continue
                r_lead = _returns(cond_closes[-n:])
                r_alt = _returns(closes[-n:])
                corr, beta = _corr_beta(r_lead, r_alt)
                if best is None or abs(corr) > abs(best["r"]):
                    best = {"conductor": str(cond), "r": corr, "beta": beta}
            if best is None:
                rows.append(_flat_row(symbol, ["no_conductor_lead"]))
                continue

            r = best["r"]
            beta = best["beta"]
            conductor = best["conductor"]
            reasons: List[str] = []

            rvol = _rvol(closed)
            if rvol < self.rvol_threshold:
                reasons.append("rvol_too_low")
            spread = float((spreads or {}).get(symbol, 0.0) or 0.0)
            if spread > self.spread_cap or symbol in thin:
                reasons.append("thin_book")
            if symbol in unlock:
                reasons.append("unlock_window")
            if abs(r) < DECOUPLED_THRESHOLD:
                reasons.append("decoupled")
            perf = _perf_24h(closes)
            pos_eq = _pos_eq(closed, window=EQ_WINDOW)
            consolidation = (
                pos_eq is not None
                and POS_EQ_CONSOLIDATION_MIN <= pos_eq <= POS_EQ_CONSOLIDATION_MAX
            )
            chasing = pos_eq is not None and pos_eq > POS_EQ_CHASING
            if chasing:
                reasons.append("chasing_zone")
            liq = float((liq_distances or {}).get(symbol, 0.0) or 0.0)
            # MP-01-Guard (importiert, nicht nachgebaut): < 5 % Liq-Distanz
            # -> needs_hitl (HITL-Eskalation, nie Freigabe)
            hitl = False
            if liq > 0 and 0 < liq < 1:
                hitl = liquidation_proximity_pct(
                    1.0, 1.0 - liq, "long"
                ).needs_hitl

            score, direction, rec = self._score_symbol(
                r, beta, rvol, perf, spread, liq
            )
            # Inverse Longs verboten (KB §6 Hartregel): r<0 wird NIEMALS
            # automatisch gelongt, nur weil der Dirigent schwach ist.
            # Ein legitimer Short-Kandidat (|r| >= 0.75, beta <= -1.5) ist
            # davon unbenommen — das Label blockt nur die Long-Seite.
            is_short_candidate = (
                abs(r) >= self.r_threshold and beta <= -self.beta_threshold
            )
            if r < 0 and not is_short_candidate:
                reasons.append("inverse_long_blocked")
            # 24h-Relativstaerke-Vorselektion: ohne positiven (Long) bzw.
            # negativen (Short) 24h-Move keine Leader-Wertung (fail-closed).
            if direction == "FLAT" and abs(r) >= self.r_threshold \
                    and abs(beta) >= self.beta_threshold:
                reasons.append("no_24h_relative_strength")

            rows.append(RankedSymbol(
                symbol=str(symbol),
                conductor=conductor,
                r=round(r, 4),
                beta=round(beta, 4),
                rvol=round(rvol, 4),
                spread_pct=round(spread, 6),
                spread_penalty=round(spread * 10.0, 6),
                perf_24h_pct=round(perf, 6) if perf is not None else None,
                pos_eq=round(pos_eq, 4) if pos_eq is not None else None,
                direction=direction,
                score=round(score, 6),
                recommendation=rec,
                entry_ready=not chasing,
                post_breakout_consolidation=consolidation,
                weekend_paper_only=bool(weekend),
                needs_hitl=hitl,
                liq_distance_pct=round(liq, 6) if liq > 0 else None,
                reasons=reasons,
            ))

        long_rank = sorted(
            (r for r in rows if r.direction == "LONG" and not r.reasons),
            key=lambda r: r.score, reverse=True,
        )
        short_rank = sorted(
            (r for r in rows if r.direction == "SHORT" and not r.reasons),
            key=lambda r: r.score, reverse=True,
        )
        filtered = sorted(
            (r for r in rows if r.reasons or r.direction == "FLAT"),
            key=lambda r: (r.symbol,),
        )
        return RankerResult(
            bar_ts=int(bar_ts),
            long_rank=long_rank,
            short_rank=short_rank,
            filtered=filtered,
            conductors=[str(c) for c in conductor_symbols],
        )

    # ------------------------------------------------------------- internal

    def _score_symbol(
        self,
        r: float,
        beta: float,
        rvol: float,
        perf_24h: Optional[float],
        spread: float,
        liq_distance: float,
    ) -> tuple:
        """Richtung signiert (KB §6):
        - LONG: r >= 0.75 UND beta >= 1.5 (beide positiv, BTC-Rueckenwind).
        - SHORT: |r| >= 0.75 UND beta <= -1.5 (starke gegenlaeufige Kopplung;
          Karte MP-05 §5 „positiv r mit negativem beta“ ist mit Same-Window-
          Schaetzern nicht konstruierbar, da sign(r) == sign(beta) — KB §6
          Bucket 2 „r negativ -> Short-Kandidat“ ist die kanonische Lesart).
        - r < 0 wird NIEMALS gelongt (inverse_long_blocked, Caller).
        Score je Richtung getrennt, Spread als Penalty."""
        spread_penalty = spread * 10.0
        if r >= self.r_threshold and beta >= self.beta_threshold:
            if perf_24h is None or perf_24h <= 0:
                return 0.0, "FLAT", "dca"  # keine 24h-Relativstaerke
            rs = 1.0 + max(0.0, min(perf_24h, 0.5))
            score = beta * rvol * r * rs - spread_penalty
            rec = _recommendation(beta, rvol, liq_distance)
            return max(0.0, score), "LONG", rec
        if abs(r) >= self.r_threshold and beta <= -self.beta_threshold:
            if perf_24h is None or perf_24h >= 0:
                return 0.0, "FLAT", "dca"
            rs = 1.0 + max(0.0, min(abs(perf_24h), 0.5))
            score = abs(beta) * rvol * abs(r) * rs - spread_penalty
            rec = _recommendation(abs(beta), rvol, liq_distance)
            return max(0.0, score), "SHORT", rec
        return 0.0, "FLAT", "dca"


def _recommendation(beta: float, rvol: float, liq_distance: float) -> str:
    """Extrem (MP-15, fractal_directional) -> Sniper -> DCA: nur bei
    hohem beta+RVOL und kleinem Liq-Puffer wird ueberhaupt gestaffelt;
    sonst dca."""
    if beta >= FRACTAL_BETA and rvol >= FRACTAL_RVOL:
        if liq_distance <= 0 or liq_distance <= SNIPER_MAX_LIQ_DISTANCE:
            return "fractal_directional"
    if beta >= SNIPER_BETA and rvol >= SNIPER_RVOL:
        if liq_distance <= 0 or liq_distance <= SNIPER_MAX_LIQ_DISTANCE:
            return "sniper_hedge"
    return "dca"


def _rvol(candles: Sequence[Mapping[str, Any]]) -> float:
    """Volumen-Ratio: letzte geschlossene Bar / Mittel der vorherigen."""
    vols = [float(c.get("v", c.get("volume", 0.0)) or 0.0) for c in candles]
    if len(vols) < 3:
        return 0.0
    last = vols[-1]
    base = sum(vols[:-1]) / (len(vols) - 1)
    if base <= 0:
        return 0.0
    return last / base


def _perf_24h(closes: Sequence[float]) -> Optional[float]:
    """24h-Performance (25 closes bei 1h-Bars). None bei zu kurzer Serie."""
    if len(closes) < 25:
        return None
    prev = closes[-25]
    if prev <= 0:
        return None
    return closes[-1] / prev - 1.0


def _pos_eq(candles: Sequence[Mapping[str, Any]], window: int) -> Optional[float]:
    """Position im Dealing-Range der letzten window Bars (skaleninvariant)."""
    if len(candles) < 2:
        return None
    seg = candles[-window:]
    highs = [float(c.get("h", c.get("high", 0.0)) or 0.0) for c in seg]
    lows = [float(c.get("l", c.get("low", 0.0)) or 0.0) for c in seg]
    close = float(seg[-1].get("c", seg[-1].get("close", 0.0)) or 0.0)
    hi, lo = max(highs), min(lows)
    if hi <= lo:
        return None
    return max(0.0, min(1.0, (close - lo) / (hi - lo)))


def _closed_bars(candles: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    rows = list(candles)
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _flat_row(symbol: str, reasons: Sequence[str]) -> RankedSymbol:
    return RankedSymbol(
        symbol=str(symbol), conductor="", r=0.0, beta=0.0, rvol=0.0,
        spread_pct=0.0, spread_penalty=0.0, perf_24h_pct=None, pos_eq=None,
        direction="FLAT", score=0.0, recommendation="dca", entry_ready=False,
        post_breakout_consolidation=False, weekend_paper_only=False,
        needs_hitl=False, liq_distance_pct=None, reasons=list(reasons),
    )


__all__ = [
    "BETA_THRESHOLD", "DECOUPLED_THRESHOLD", "HighBetaRanker", "POS_EQ_CHASING",
    "POS_EQ_CONSOLIDATION_MAX", "POS_EQ_CONSOLIDATION_MIN", "R_THRESHOLD",
    "RVOL_THRESHOLD", "RankedSymbol", "RankerResult", "SNIPER_BETA",
    "SNIPER_MAX_LIQ_DISTANCE", "SNIPER_RVOL", "SPREAD_CAP",
]
