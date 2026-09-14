"""
=========================================================
Datei:      app/optimizer/StrategyAllocator.py
Zweck:      §18.4/18.5 Loop E — Badge-Profil + Live-Regime -> TV-Alert an/aus.
            Heuristik jetzt, ONNX-Meta-Learner sobald das Modell existiert.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Allokation) / Noir (Gate)
=========================================================
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core import blueprint as bp

logger = logging.getLogger("app.optimizer.allocator")


@dataclass
class Profile:
    """Aggregat je (strategy, symbol, timeframe, regime) — §18.3."""

    strategy_id: str
    symbol: str
    timeframe: str
    regime: str
    trade_count: int = 0
    wins: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    updated_at: float = field(default_factory=time.time)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trade_count if self.trade_count else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss <= 0:
            return float(self.gross_profit > 0) * 99.0 or 0.0
        return self.gross_profit / self.gross_loss

    @property
    def rating(self) -> str:
        return bp.badge_rating(self.trade_count, self.win_rate, self.profit_factor)[0]

    @property
    def is_allowed(self) -> bool:
        return bp.badge_rating(self.trade_count, self.win_rate, self.profit_factor)[1]

    def badge_name(self) -> str:
        sym = self.symbol.replace("/", "").upper()
        tf = str(self.timeframe).upper()
        rating = self.rating
        if rating == bp.BADGE_INSUFFICIENT:
            return bp.BADGE_INSUFFICIENT
        if rating == "S":
            prefix = "CHOP_MASTER" if self.regime == bp.Regime.RANGING_CHOP.value else "SUPER"
            return f"{prefix}_ON_{sym}_{tf}"
        if rating == "F":
            return f"POOR_ON_{sym}_{tf}"
        return f"{rating}_ON_{sym}_{tf}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "symbol": self.symbol,
            "timeframe": self.timeframe, "regime": self.regime,
            "trade_count": self.trade_count, "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "rating": self.rating, "is_allowed": self.is_allowed,
            "badge": self.badge_name(), "updated_at": self.updated_at,
        }


class StrategyAllocator:
    """Entscheidet, welche TV-Alerts im aktuellen Regime laufen dürfen."""

    def __init__(self, alert_provisioner=None, academy=None, model_path: str = bp.PATH_ONNX_ALLOCATOR,
                 lock_provider=None):
        self.alerts = alert_provisioner
        self.academy = academy
        self.model_path = model_path
        self.lock_provider = lock_provider
        self._profiles: Dict[Tuple[str, str, str, str], Profile] = {}

    # -------------------------------------------------------------- ingest
    def ingest_trade_result(self, strategy_id: str, symbol: str, timeframe: Any,
                            regime: str, pnl_pct: float) -> Dict[str, Any]:
        """Loop E Stufe 1+2 — Harvester/Profiler in einem Schritt."""
        key = (strategy_id, symbol, str(timeframe), regime)
        prof = self._profiles.get(key)
        if prof is None:
            prof = Profile(strategy_id, symbol, str(timeframe), regime)
            self._profiles[key] = prof
        prof.trade_count += 1
        if pnl_pct > 0:
            prof.wins += 1
            prof.gross_profit += pnl_pct
        else:
            prof.gross_loss += abs(pnl_pct)
        prof.updated_at = time.time()
        return prof.to_dict()

    def get_profile(self, strategy_id: str, symbol: str, timeframe: Any,
                    regime: str) -> Optional[Profile]:
        return self._profiles.get((strategy_id, symbol, str(timeframe), regime))

    def badge_matrix(self, strategy_id: str = "") -> List[Dict[str, Any]]:
        """Symbol × TF × Regime Scorecard für das AcademyBadgeMatrix-Panel."""
        rows = [p.to_dict() for p in self._profiles.values()
                if not strategy_id or p.strategy_id == strategy_id]
        return sorted(rows, key=lambda r: (r["symbol"], r["timeframe"], r["regime"]))

    # ---------------------------------------------------------------- gate
    def evaluate(self, strategy_id: str, symbol: str, timeframe: Any,
                 regime: str) -> Dict[str, Any]:
        """§18.4 Allocator-Gate — F oder not is_allowed blockiert den Start."""
        if self.lock_provider is not None:
            try:
                if self.lock_provider(strategy_id, symbol, timeframe, regime):
                    return {
                        "allow": False, "rating": "F", "badge": "LOCKED",
                        "reason": "scorecard lock", "incubating": False,
                        "trade_count": 0, "locked": True,
                    }
            except Exception as exc:  # pragma: no cover
                logger.error("allocator lock_provider failed: %s", exc)
        prof = self.get_profile(strategy_id, symbol, timeframe, regime)
        if prof is None or prof.trade_count < bp.BADGE_MIN_SAMPLE:
            # Unter N=30 kein Urteil -> Scout/Paper erlaubt, Live vorsichtig zulassen
            return {
                "allow": True, "rating": bp.BADGE_INSUFFICIENT,
                "reason": f"insufficient sample (<{bp.BADGE_MIN_SAMPLE}) — incubation",
                "badge": bp.BADGE_INSUFFICIENT, "incubating": True,
                "trade_count": prof.trade_count if prof else 0,
            }
        allow = prof.is_allowed and prof.rating != "F"
        return {
            "allow": allow, "rating": prof.rating, "badge": prof.badge_name(),
            "reason": "badge gate passed" if allow else f"rating {prof.rating} blocked",
            "incubating": False, "trade_count": prof.trade_count,
            "win_rate": round(prof.win_rate, 4), "profit_factor": round(prof.profit_factor, 4),
        }

    def apply(self, strategy_id: str, symbol: str, timeframe: Any, regime: str,
              *, runner_running: bool = True) -> Dict[str, Any]:
        """Gate auswerten und den TV-Alert entsprechend schalten."""
        verdict = self.evaluate(strategy_id, symbol, timeframe, regime)
        action = "none"
        if self.alerts is not None:
            try:
                if verdict["allow"] and runner_running:
                    self.alerts.enable(strategy_id, reason=f"allocator_{regime.lower()}")
                    action = "enable"
                elif not verdict["allow"]:
                    self.alerts.disable(strategy_id, reason=f"allocator_{verdict['rating']}")
                    action = "disable"
            except KeyError:
                action = "no_alert_record"
            except Exception as exc:  # pragma: no cover
                logger.error("allocator alert switch failed: %s", exc)
                action = f"error:{exc}"
        return {**verdict, "action": action, "regime": regime}

    def rebalance(self, live_regime: str, runners: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Periodischer Lauf über alle Runner (Loop E Cron)."""
        out = []
        for runner in runners:
            out.append(self.apply(
                runner["strategy_id"], runner.get("symbol", "BTC/USD"),
                runner.get("timeframe", 15), live_regime,
                runner_running=runner.get("running", True)))
        return out

    def export_training_dataset(self) -> List[Dict[str, Any]]:
        """§18.1 Stufe 4 — gelabelte Rows für das Meta-Learner-Training."""
        return [
            {
                "strategy_id": p.strategy_id, "symbol": p.symbol, "timeframe": p.timeframe,
                "regime": p.regime, "trade_count": p.trade_count,
                "win_rate": p.win_rate, "profit_factor": p.profit_factor,
                "label_allowed": int(p.is_allowed), "rating": p.rating,
            }
            for p in self._profiles.values() if p.trade_count >= bp.BADGE_MIN_SAMPLE
        ]

    def snapshot(self) -> Dict[str, Any]:
        ratings: Dict[str, int] = {}
        for p in self._profiles.values():
            ratings[p.rating] = ratings.get(p.rating, 0) + 1
        return {
            "profiles": len(self._profiles),
            "ratings": ratings,
            "model_path": self.model_path,
            "min_sample": bp.BADGE_MIN_SAMPLE,
        }


_allocator: Optional[StrategyAllocator] = None


def get_allocator(**kwargs) -> StrategyAllocator:
    global _allocator
    if _allocator is None:
        _allocator = StrategyAllocator(**kwargs)
    return _allocator
