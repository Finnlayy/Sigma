"""
=========================================================
Datei:      app/optimizer/strategy_scorecard.py
Zweck:      Strategie-Ampel, User/Akademie-Slots, Stage-1 Initialize,
            Idle-Batch, Validate → GA. Markt-Ampel (RegimeEngine) bleibt getrennt.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Allokation) / Blanche (Academy)
=========================================================
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.optimizer.scorecard")

Lamp = bp.StrategyLamp


def _now() -> float:
    return time.time()


def lookback_window(days: int = bp.INITIALIZE_LOOKBACK_DAYS) -> Dict[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(days))
    return {
        "from": start.strftime("%Y-%m-%d"),
        "to": end.strftime("%Y-%m-%d"),
    }


def pf_from_job_result(result: Optional[Dict[str, Any]]) -> float:
    payload = result or {}
    backtest = payload.get("backtest") if isinstance(payload.get("backtest"), dict) else payload
    summary = backtest.get("summary") if isinstance(backtest, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    raw = summary.get("profitFactor", payload.get("profitFactor", 0.0))
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_lamp(header: Dict[str, Any], slots: List[Dict[str, Any]]) -> str:
    """Header-Ampel aus Stage-1 und Slot-Zuständen."""
    favorites = [s for s in slots if s.get("favorite")]
    blocked = [s for s in slots if s.get("locked") or s.get("lamp") == Lamp.RED_GLOW.value]
    if favorites and all(
        s.get("locked") or s.get("lamp") == Lamp.RED_GLOW.value for s in favorites
    ):
        return Lamp.RED_GLOW.value
    if header.get("stage1_done") and float(header.get("pf_after_fees") or 0) >= bp.INITIALIZE_RELEASE_PF:
        return Lamp.GREEN_GLOW.value
    if any(s.get("lamp") == Lamp.GREEN_GLOW.value for s in slots):
        return Lamp.GREEN_GLOW.value
    if any(s.get("origin") == bp.SLOT_ORIGIN_USER
           and s.get("lamp") == Lamp.GREEN_SOLID.value for s in slots):
        return Lamp.GREEN_SOLID.value
    if any(s.get("lamp") == Lamp.YELLOW.value for s in slots):
        return Lamp.YELLOW.value
    if blocked and not favorites:
        return Lamp.RED_GLOW.value
    if header.get("lamp"):
        return str(header["lamp"])
    return Lamp.GRAY.value


class StrategyScorecard:
    """Persistente Scorecard: Slots + Initialize/Validate/Idle Stage-1."""

    def __init__(self, store=None, queue=None, allocator=None,
                 ga_runner: Optional[Callable[..., Any]] = None,
                 live_trading_provider: Optional[Callable[[], bool]] = None,
                 idle_provider: Optional[Callable[[], bool]] = None):
        self.store = store
        self.queue = queue
        self.allocator = allocator
        self.ga_runner = ga_runner
        self.live_trading_provider = live_trading_provider or (lambda: False)
        self.idle_provider = idle_provider or (lambda: True)

    # --------------------------------------------------------------- header
    def header(self, strategy_id: str) -> Dict[str, Any]:
        row = self.store.get_scorecard_header(strategy_id) if self.store else None
        return row or {
            "strategy_id": strategy_id, "lamp": Lamp.GRAY.value,
            "initialized_at": None, "stage1_done": False,
            "options_opened_at": None, "last_init_job_id": "",
            "last_pull_job_id": "", "last_validate_job_id": "",
            "pf_after_fees": 0.0, "net_pnl": 0.0, "trade_count": 0,
            "win_rate": 0.0, "updated_at": None,
        }

    def _save_header(self, header: Dict[str, Any]) -> Dict[str, Any]:
        slots = self.store.list_strategy_slots(header["strategy_id"]) if self.store else []
        header["lamp"] = aggregate_lamp(header, slots)
        header["updated_at"] = _now()
        if self.store:
            self.store.upsert_scorecard_header(header)
        return header

    def mark_options_opened(self, strategy_id: str) -> Dict[str, Any]:
        header = self.header(strategy_id)
        if not header.get("options_opened_at"):
            header["options_opened_at"] = _now()
            self._save_header(header)
        return header

    def kpis(self, strategy_id: str) -> Dict[str, Any]:
        header = self.header(strategy_id)
        live = {}
        if self.store and hasattr(self.store, "strategy_trade_kpis"):
            live = self.store.strategy_trade_kpis(strategy_id)
        n = int(live.get("trade_count") or header.get("trade_count") or 0)
        wr = float(live.get("win_rate") if live.get("trade_count") else header.get("win_rate") or 0.0)
        pf = float(live.get("profit_factor") if live.get("trade_count") else header.get("pf_after_fees") or 0.0)
        pnl = float(live.get("net_pnl") if live.get("trade_count") else header.get("net_pnl") or 0.0)
        return {
            "trade_count": n,
            "win_rate": round(wr, 4),
            "profit_factor": round(pf, 4),
            "net_pnl": round(pnl, 2),
        }

    # ----------------------------------------------------------------- slots
    def is_locked(self, strategy_id: str, symbol: str, timeframe: Any,
                  regime: str = "") -> bool:
        if not self.store:
            return False
        for slot in self.store.list_strategy_slots(strategy_id):
            if not (slot.get("locked") or slot.get("lamp") == Lamp.RED_GLOW.value):
                continue
            if slot["symbol"] != symbol:
                continue
            if str(slot["timeframe"]) != str(timeframe):
                continue
            if slot.get("regime") and slot["regime"] != (regime or ""):
                continue
            return True
        return False

    def upsert_user_slot(self, strategy_id: str, *, symbol: str, timeframe: Any,
                         regime: str = "", favorite: bool = True,
                         locked: bool = False) -> Dict[str, Any]:
        lamp = Lamp.RED_GLOW.value if locked else Lamp.GREEN_SOLID.value
        slot = {
            "strategy_id": strategy_id, "symbol": symbol,
            "timeframe": str(timeframe), "regime": regime or "",
            "origin": bp.SLOT_ORIGIN_USER, "lamp": lamp,
            "locked": bool(locked), "favorite": bool(favorite),
            "updated_at": _now(),
        }
        if self.store:
            existing = self.store.get_strategy_slot(strategy_id, symbol, timeframe, regime or "")
            if existing:
                slot["pf_after_fees"] = existing.get("pf_after_fees") or 0.0
                slot["last_job_id"] = existing.get("last_job_id") or ""
                slot["verified_at"] = existing.get("verified_at")
                if existing.get("lamp") == Lamp.GREEN_GLOW.value and not locked:
                    slot["lamp"] = Lamp.GREEN_GLOW.value
            self.store.upsert_strategy_slot(slot)
        self._save_header(self.header(strategy_id))
        return slot

    def propose_academy_slot(self, strategy_id: str, *, symbol: str, timeframe: Any,
                             regime: str = "", favorite: bool = True) -> Dict[str, Any]:
        existing = None
        if self.store:
            existing = self.store.get_strategy_slot(strategy_id, symbol, timeframe, regime or "")
        if existing and existing.get("origin") == bp.SLOT_ORIGIN_USER:
            return existing
        slot = {
            "strategy_id": strategy_id, "symbol": symbol,
            "timeframe": str(timeframe), "regime": regime or "",
            "origin": bp.SLOT_ORIGIN_ACADEMY, "lamp": Lamp.YELLOW.value,
            "locked": False, "favorite": bool(favorite), "updated_at": _now(),
        }
        if existing:
            slot["pf_after_fees"] = existing.get("pf_after_fees") or 0.0
            slot["last_job_id"] = existing.get("last_job_id") or ""
            slot["verified_at"] = existing.get("verified_at")
            if existing.get("lamp") == Lamp.GREEN_GLOW.value:
                slot["lamp"] = Lamp.GREEN_GLOW.value
        if self.store:
            self.store.upsert_strategy_slot(slot)
        header = self.header(strategy_id)
        if header.get("lamp") in (Lamp.GRAY.value, "", None):
            header["lamp"] = Lamp.YELLOW.value
        self._save_header(header)
        return slot

    def verify_slots(self, strategy_id: str, *, profit_factor: float,
                     job_id: str = "") -> List[Dict[str, Any]]:
        passed = profit_factor >= bp.INITIALIZE_RELEASE_PF
        out: List[Dict[str, Any]] = []
        if not self.store:
            return out
        for slot in self.store.list_strategy_slots(strategy_id):
            if slot.get("locked"):
                out.append(slot)
                continue
            slot["pf_after_fees"] = profit_factor
            slot["last_job_id"] = job_id or slot.get("last_job_id") or ""
            if passed:
                slot["lamp"] = Lamp.GREEN_GLOW.value
                slot["verified_at"] = _now()
            elif profit_factor <= 0:
                slot["lamp"] = Lamp.RED_GLOW.value
            elif slot.get("origin") == bp.SLOT_ORIGIN_USER:
                slot["lamp"] = Lamp.GREEN_SOLID.value
            else:
                slot["lamp"] = Lamp.YELLOW.value
            self.store.upsert_strategy_slot(slot)
            out.append(slot)
        self._save_header(self.header(strategy_id))
        return out

    def put_slots(self, strategy_id: str, slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for raw in slots:
            out.append(self.upsert_user_slot(
                strategy_id,
                symbol=str(raw.get("symbol") or ""),
                timeframe=raw.get("timeframe") or "",
                regime=str(raw.get("regime") or ""),
                favorite=bool(raw.get("favorite", True)),
                locked=bool(raw.get("locked")),
            ))
        return out

    # ------------------------------------------------------------ initialize
    def start_initialize(self, strategy_id: str, *, origin: str = "user") -> Dict[str, Any]:
        strategy = self.store.get_strategy(strategy_id) if self.store else None
        if strategy is None:
            return {"ok": False, "error": "unknown strategy", "strategy_id": strategy_id}
        symbol = strategy.get("assetPair") or "BTC/USD"
        interval = strategy.get("interval") or 15
        header = self.header(strategy_id)
        header["lamp"] = Lamp.YELLOW.value
        header["initialized_at"] = header.get("initialized_at") or _now()
        if origin != "user":
            self.propose_academy_slot(strategy_id, symbol=symbol, timeframe=interval, favorite=True)
        pull = backtest = None
        if self.queue is not None:
            from app.tv.worker import JOB_KIND_BACKTEST, JOB_KIND_PULL_PARAMS

            pull = self.queue.submit(
                JOB_KIND_PULL_PARAMS, strategy_id=strategy_id,
                symbol=symbol, interval=interval,
            )
            backtest = self.queue.submit(
                JOB_KIND_BACKTEST, strategy_id=strategy_id,
                symbol=symbol, interval=interval,
                window=lookback_window(),
                params=strategy.get("parameters") or {},
            )
            header["last_pull_job_id"] = getattr(pull, "job_id", "") or ""
            header["last_init_job_id"] = getattr(backtest, "job_id", "") or ""
        self._save_header(header)
        return {
            "ok": True, "strategy_id": strategy_id, "origin": origin,
            "lamp": header["lamp"],
            "pull_job": pull.to_dict() if pull is not None and hasattr(pull, "to_dict") else None,
            "backtest_job": backtest.to_dict() if backtest is not None and hasattr(backtest, "to_dict") else None,
            "window": lookback_window(),
        }

    def complete_initialize(self, strategy_id: str, *, profit_factor: float,
                            net_pnl: float = 0.0, trade_count: int = 0,
                            win_rate: float = 0.0, job_id: str = "") -> Dict[str, Any]:
        header = self.header(strategy_id)
        header["pf_after_fees"] = float(profit_factor)
        header["net_pnl"] = float(net_pnl)
        header["trade_count"] = int(trade_count)
        header["win_rate"] = float(win_rate)
        header["last_init_job_id"] = job_id or header.get("last_init_job_id") or ""
        if profit_factor >= bp.INITIALIZE_RELEASE_PF:
            header["stage1_done"] = True
            header["initialized_at"] = header.get("initialized_at") or _now()
            header["lamp"] = Lamp.GREEN_GLOW.value
        elif profit_factor > 0:
            header["stage1_done"] = False
            header["lamp"] = Lamp.YELLOW.value
        else:
            header["stage1_done"] = False
            header["lamp"] = Lamp.RED_GLOW.value
        self._save_header(header)
        self.verify_slots(strategy_id, profit_factor=profit_factor, job_id=job_id)
        return {"ok": True, "header": self.header(strategy_id),
                "released": bool(self.header(strategy_id).get("stage1_done"))}

    def harvest_jobs(self, strategy_id: str = "") -> List[Dict[str, Any]]:
        if self.queue is None or self.store is None:
            return []
        headers = [self.header(strategy_id)] if strategy_id else self.store.list_scorecard_headers()
        harvested = []
        for header in headers:
            job_id = header.get("last_init_job_id") or ""
            if not job_id or header.get("stage1_done"):
                continue
            job = self.queue.get(job_id) if hasattr(self.queue, "get") else None
            if job is None:
                continue
            status = getattr(job, "status", None) or (job.get("status") if isinstance(job, dict) else "")
            if status not in ("done", "failed"):
                continue
            if status == "failed":
                harvested.append(self.complete_initialize(
                    header["strategy_id"], profit_factor=0.0, job_id=job_id))
                continue
            result = getattr(job, "result", None) or (job.get("result") if isinstance(job, dict) else {})
            pf = pf_from_job_result(result if isinstance(result, dict) else {})
            summary = ((result or {}).get("backtest") or {}).get("summary") or {}
            harvested.append(self.complete_initialize(
                header["strategy_id"],
                profit_factor=pf,
                net_pnl=float(summary.get("totalReturnUSD") or 0.0),
                trade_count=int(summary.get("totalTrades") or 0),
                win_rate=float(summary.get("winRate") or 0.0) / 100.0,
                job_id=job_id,
            ))
        return harvested

    def start_validate(self, strategy_id: str) -> Dict[str, Any]:
        strategy = self.store.get_strategy(strategy_id) if self.store else None
        if strategy is None:
            return {"ok": False, "error": "unknown strategy"}
        header = self.header(strategy_id)
        header["last_validate_job_id"] = f"val_{strategy_id}_{int(_now())}"
        cfg = {
            "baselineStrategyId": strategy_id,
            "baselineStrategyName": strategy.get("name"),
            "assetPair": strategy.get("assetPair") or "BTC/USD",
            "interval": strategy.get("interval") or 15,
            "populationSize": bp.GA_MAX_POPULATION,
            "maxGenerations": bp.GA_MAX_GENERATIONS,
        }
        result: Any = {"queued": True}
        if self.ga_runner is not None:
            result = self.ga_runner(cfg)
            if isinstance(result, dict):
                gate = result.get("shadowGate") or {}
                passed = bool(gate.get("passed"))
                best = result.get("bestIndividual") or {}
                pf = float((best.get("inSampleSummary") or {}).get("profitFactor")
                           or best.get("profitFactor") or header.get("pf_after_fees") or 0)
                if passed:
                    header["stage1_done"] = True
                    header["pf_after_fees"] = max(pf, header.get("pf_after_fees") or 0)
                    self._save_header(header)
                    self.verify_slots(strategy_id, profit_factor=max(pf, bp.INITIALIZE_RELEASE_PF),
                                      job_id=header["last_validate_job_id"])
        self._save_header(header)
        return {
            "ok": True, "strategy_id": strategy_id,
            "job_id": header["last_validate_job_id"],
            "lamp": self.header(strategy_id)["lamp"],
            "result": result if isinstance(result, dict) else {"ok": True},
        }

    def next_idle_candidate(self) -> Optional[str]:
        if not self.store:
            return None
        for strategy in self.store.list_strategies(include_archived=False):
            header = self.header(strategy["id"])
            if header.get("stage1_done") or header.get("options_opened_at"):
                continue
            return strategy["id"]
        return None

    def idle_stage1_tick(self, *, live_trading: Optional[bool] = None,
                         tv_idle: Optional[bool] = None) -> Dict[str, Any]:
        live = self.live_trading_provider() if live_trading is None else live_trading
        idle = self.idle_provider() if tv_idle is None else tv_idle
        if live:
            return {"skipped": True, "reason": "live"}
        if not idle:
            return {"skipped": True, "reason": "tv_busy"}
        self.harvest_jobs()
        sid = self.next_idle_candidate()
        if not sid:
            return {"skipped": True, "reason": "none"}
        out = self.start_initialize(sid, origin="idle")
        return {**out, "skipped": False, "reason": "initialized"}

    # -------------------------------------------------------------- snapshots
    def _primary_badge(self, strategy_id: str) -> str:
        if self.allocator is None:
            return ""
        rows = self.allocator.badge_matrix(strategy_id)
        if not rows:
            return ""
        ranked = sorted(rows, key=lambda r: (
            0 if r.get("rating") == "S" else 1, -(r.get("profit_factor") or 0)))
        return str(ranked[0].get("badge") or "")

    def _best_pair(self, strategy_id: str, strategy: Dict[str, Any]) -> Dict[str, str]:
        slots = self.store.list_strategy_slots(strategy_id) if self.store else []
        ranked = sorted(
            [s for s in slots if s.get("favorite")],
            key=lambda s: bp.STRATEGY_LAMP_RANK.get(s.get("lamp") or "gray", 0),
            reverse=True,
        )
        if ranked:
            return {"symbol": ranked[0]["symbol"], "timeframe": ranked[0]["timeframe"]}
        return {
            "symbol": strategy.get("assetPair") or "",
            "timeframe": str(strategy.get("interval") or ""),
        }

    def library_snapshot(self) -> Dict[str, Any]:
        self.harvest_jobs()
        strategies = self.store.list_strategies(include_archived=False) if self.store else []
        rows = []
        for strategy in strategies:
            sid = strategy["id"]
            header = self.header(sid)
            kpis = self.kpis(sid)
            pair = self._best_pair(sid, strategy)
            rows.append({
                "id": sid,
                "name": strategy.get("name"),
                "status": strategy.get("status"),
                "executionMode": strategy.get("executionMode") or "paper",
                "assetPair": strategy.get("assetPair"),
                "interval": strategy.get("interval"),
                "tv": bool(strategy.get("tv_script_id")),
                "lamp": header.get("lamp") or Lamp.GRAY.value,
                "kpis": kpis,
                "primary_badge": self._primary_badge(sid),
                "best_symbol": pair["symbol"],
                "best_tf": pair["timeframe"],
                "stage1_done": bool(header.get("stage1_done")),
            })
        return {"strategies": rows, "count": len(rows)}

    def scorecard(self, strategy_id: str, *, mark_opened: bool = True) -> Dict[str, Any]:
        self.harvest_jobs(strategy_id)
        if mark_opened:
            self.mark_options_opened(strategy_id)
        strategy = self.store.get_strategy(strategy_id) if self.store else None
        if strategy is None:
            return {"ok": False, "error": "unknown strategy"}
        header = self.header(strategy_id)
        slots = self.store.list_strategy_slots(strategy_id) if self.store else []
        badges = self.allocator.badge_matrix(strategy_id) if self.allocator else []
        slim = {k: v for k, v in strategy.items() if k != "code"}
        return {
            "ok": True,
            "strategy": slim,
            "header": header,
            "kpis": self.kpis(strategy_id),
            "slots": slots,
            "badges": badges,
            "lamp": header.get("lamp") or Lamp.GRAY.value,
        }


_scorecard: Optional[StrategyScorecard] = None


def get_strategy_scorecard(**kwargs) -> StrategyScorecard:
    global _scorecard
    if _scorecard is None:
        _scorecard = StrategyScorecard(**kwargs)
    return _scorecard


def set_strategy_scorecard(card: Optional[StrategyScorecard]) -> None:
    global _scorecard
    _scorecard = card
