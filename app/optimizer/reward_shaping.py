"""
=========================================================
Datei:      app/optimizer/reward_shaping.py
Zweck:      §21 / Masterprompt §3.C — Multi-Faktor Belohnungs- und
            Strafen-Matrix. R_total -> Note S/A/B/C/F -> XP/Strike ->
            Budget-Multiplier bzw. Quarantäne.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Portfolio) / Noir (Strafmaß)
=========================================================
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.optimizer.reward")


@dataclass
class RewardOutcome:
    strategy_id: str
    reward: float
    grade: str
    xp_delta: int
    strike_delta: int
    xp: int
    strikes: int
    budget_multiplier: float
    quarantine: bool
    components: Dict[str, float] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {**{k: v for k, v in self.__dict__.items() if k != "components"},
                "components": {k: round(v, 6) for k, v in self.components.items()}}


@dataclass
class StrategyScore:
    xp: int = 0
    strikes: int = 0
    trades: int = 0
    reward_sum: float = 0.0
    grades: List[str] = field(default_factory=list)


class RewardShapingEngine:
    """Belohnt Qualität, bestraft Churn — und zieht bei 3 Strikes den Stecker."""

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 strikes_to_quarantine: int = bp.STRIKES_TO_QUARANTINE):
        self.weights = dict(weights or bp.REWARD_WEIGHTS)
        self.strikes_to_quarantine = strikes_to_quarantine
        self._scores: Dict[str, StrategyScore] = {}

    # ------------------------------------------------------------- scoring
    def score_trade(self, strategy_id: str, *, pnl_pct: float, mfe_pct: float,
                    mae_pct: float, duration_bars: int = 0, expected_bars: int = 20,
                    fee_usd: float = 0.0, notional_usd: float = 0.0) -> RewardOutcome:
        time_decay = max(0.0, (duration_bars - expected_bars) / max(expected_bars, 1))
        fee_churn = (fee_usd / notional_usd * 100.0) if notional_usd > 0 else abs(fee_usd)
        reward = bp.reward_total(pnl=pnl_pct, mfe=mfe_pct, mae=mae_pct,
                                 time_decay=time_decay, fee_churn=fee_churn)
        grade = self.grade_for(reward, pnl_pct)

        score = self._scores.setdefault(strategy_id, StrategyScore())
        score.trades += 1
        score.reward_sum += reward
        score.grades.append(grade)

        xp_delta = strike_delta = 0
        if grade in ("S", "A"):
            xp_delta = 2 if grade == "S" else 1
            score.xp += xp_delta
            score.strikes = max(0, score.strikes - 1)      # gute Arbeit tilgt einen Strike
        elif grade in ("C", "F"):
            strike_delta = 1
            score.strikes += 1

        quarantine = score.strikes >= self.strikes_to_quarantine
        multiplier = 0.0 if quarantine else bp.budget_multiplier_for_grade(grade, score.strikes)
        if quarantine:
            logger.warning("strategy %s hit %d strikes -> quarantine", strategy_id, score.strikes)

        return RewardOutcome(
            strategy_id=strategy_id, reward=reward, grade=grade,
            xp_delta=xp_delta, strike_delta=strike_delta, xp=score.xp, strikes=score.strikes,
            budget_multiplier=multiplier, quarantine=quarantine,
            components={"pnl": pnl_pct, "mfe_mae": mfe_pct / (abs(mae_pct) + bp.REWARD_EPSILON),
                        "time_decay": time_decay, "fee_churn": fee_churn},
        )

    @staticmethod
    def grade_for(reward: float, pnl_pct: float) -> str:
        """Note aus R_total, mit PnL-Vorzeichen als Härtefall-Korrektur."""
        if pnl_pct <= 0 and reward < 0:
            return "F"
        if reward >= 4.0:
            return "S"
        if reward >= 2.0:
            return "A"
        if reward >= 0.5:
            return "B"
        if reward >= 0.0:
            return "C"
        return "F"

    # --------------------------------------------------------------- state
    def score(self, strategy_id: str) -> Dict[str, Any]:
        s = self._scores.get(strategy_id, StrategyScore())
        avg = s.reward_sum / s.trades if s.trades else 0.0
        return {
            "strategy_id": strategy_id, "xp": s.xp, "strikes": s.strikes,
            "trades": s.trades, "avg_reward": round(avg, 4),
            "recent_grades": s.grades[-10:],
            "budget_multiplier": 0.0 if s.strikes >= self.strikes_to_quarantine
            else bp.budget_multiplier_for_grade(s.grades[-1] if s.grades else "B", s.strikes),
            "quarantined": s.strikes >= self.strikes_to_quarantine,
        }

    def matrix(self) -> List[Dict[str, Any]]:
        """Futter für das RewardXPMatrixPanel (§8)."""
        return [self.score(sid) for sid in self._scores]

    def reset(self, strategy_id: str) -> None:
        self._scores.pop(strategy_id, None)


_engine: Optional[RewardShapingEngine] = None


def get_reward_engine() -> RewardShapingEngine:
    global _engine
    if _engine is None:
        _engine = RewardShapingEngine()
    return _engine
