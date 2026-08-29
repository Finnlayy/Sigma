"""
=========================================================
Datei:      sigma/orchestration/multi_asset_router.py
Zweck:      Multi-Asset aus Loop-C-Serie. Alts erben BTC-HTF-Bias.
            Weekend-Alts nur Paper (Loop D), kein Live-A.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Router)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sigma.signals.correlation_scout import CorrelationScout
from sigma.signals.session_clock import SessionClock, SessionState
from sigma.signals.timeframe_ladder import TimeframePair, session_exec_pair

LEADERS = ("BTC/USD", "XBT/USD", "XAU/USD")


@dataclass
class RouteDecision:
    symbol: str
    inherit_leader: str
    paper_only: bool
    live_a: bool
    bucket: int
    pair: Dict[str, Any]
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class MultiAssetRouter:
    def __init__(self, scout: Optional[CorrelationScout] = None) -> None:
        self.scout = scout or CorrelationScout()

    def route(
        self,
        series: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
        *,
        session: Optional[SessionState] = None,
        now: Optional[float] = None,
        use_ict_ladder: bool = False,
        leader: str = "BTC/USD",
    ) -> List[RouteDecision]:
        sess = session or SessionClock().evaluate(now)
        pair = session_exec_pair(sess.session, use_ict_ladder=use_ict_ladder)
        if not series:
            return []
        scout = self.scout.find_high_beta_candidates(series, leader=leader)
        bucket_of = {r.symbol: r.bucket for r in scout.rows}
        out: List[RouteDecision] = []
        for symbol in series.keys():
            is_leader = symbol in LEADERS or symbol == leader
            weekend_alt = bool(sess.weekend_alts_paper_only) and not is_leader
            paper_only = weekend_alt
            live_a = not paper_only
            bucket = 0 if is_leader else int(bucket_of.get(symbol) or 3)
            reason = "leader" if is_leader else (
                "weekend_alt_paper_only" if weekend_alt else f"bucket_{bucket}"
            )
            out.append(RouteDecision(
                symbol=symbol,
                inherit_leader=leader if not is_leader else symbol,
                paper_only=paper_only,
                live_a=live_a,
                bucket=bucket,
                pair=pair.to_dict(),
                reason=reason,
                details={"session": sess.session, "scout": scout.to_dict() if not is_leader else {}},
            ))
        return out
