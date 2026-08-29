"""
=========================================================
Datei:      sigma/orchestration/master_orchestrator.py
Zweck:      C-Snapshot + Dual-Hurst + Ladder + Session/Gap.
            htf_ready fail-closed. E dann A. Weekend-Alts nur D.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Orchestrierung) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sigma.signals.dual_hurst import evaluate_dual_hurst
from sigma.signals.polymarket_layer0 import layer0_pre_regime
from sigma.signals.session_clock import SessionClock
from sigma.signals.timeframe_ladder import session_exec_pair
from sigma.signals.volatility_throttle import VolatilityThrottleGate
from sigma.orchestration.multi_asset_router import MultiAssetRouter
from sigma.strategies.dual_hedge_grid import DualHedgeGrid
from sigma.strategies.dynamic_channel_dca import DynamicChannelDCA
from sigma.strategies.htf_trend_ltf_reversion import HtfTrendLtfReversion


class MasterOrchestrator:
    """Closed-graph conductor. Fail-closed on open HTF / SLEEP / 21:00 UTC gap."""

    def __init__(self, *, ports: Optional[Dict[str, Any]] = None) -> None:
        self.ports = ports or {}
        self.ticks = 0
        self.clock = SessionClock()
        self.throttle = VolatilityThrottleGate()
        self.router = MultiAssetRouter()
        self.templates = {
            "htf_trend_ltf_reversion": HtfTrendLtfReversion(),
            "dynamic_channel_dca": DynamicChannelDCA(),
            "dual_hedge_grid": DualHedgeGrid(),
        }

    def tick(self, snapshot: Optional[Any] = None) -> Dict[str, Any]:
        self.ticks += 1
        snap = snapshot if snapshot is not None else self._poll_c()
        series = getattr(snap, "series", None) if snap is not None else None
        if series is None and isinstance(snap, dict):
            series = snap.get("series")
        htf_series = _htf_series(snap)
        session = self.clock.evaluate()
        pair = session_exec_pair(session.session, use_ict_ladder=False)
        leader = "BTC/USD"
        ltf = (series or {}).get(leader) or _first_series(series)
        htf = (htf_series or {}).get(leader) or ltf
        dual = evaluate_dual_hurst(htf, ltf, htf_interval_min=pair.bias_minutes)
        btc = (series or {}).get(leader) or []
        throttle = self.throttle.evaluate(btc, force_sleep=session.liquidity_gap)
        poly = layer0_pre_regime(self.ports.get("polymarket"))
        if not dual.htf_ready:
            return self._idle("htf_not_ready", session, throttle, dual, pair, poly)
        if throttle.mode == "SLEEP" or session.liquidity_gap:
            return self._unwind_only(session, throttle, dual, pair, poly)
        routes = self.router.route(series, session=session, use_ict_ladder=False, leader=leader)
        cap = int(throttle.allowed_concurrent_bots)
        deployed: List[Dict[str, Any]] = []
        for route in routes[: max(0, cap)]:
            if route.paper_only:
                self._loop_d_paper(route.symbol, session.session)
                deployed.append({**route.to_dict(), "path": "loop_d_paper"})
                continue
            intent = self._plan(route.symbol, session, series, htf_series, pair)
            if intent.action == "FLAT":
                deployed.append({**route.to_dict(), "path": "flat", "reason": intent.details.get("reason")})
                continue
            e_ok = self._loop_e(intent.strategy_id, route.symbol, pair.exec_minutes, dual.htf_regime)
            if not e_ok:
                deployed.append({**route.to_dict(), "path": "e_blocked"})
                continue
            receipt = self._loop_a_paper(intent)
            deployed.append({**route.to_dict(), "path": "e_then_a", "receipt": receipt})
        return {
            "ok": True,
            "status": "tick",
            "phase": 3,
            "ticks": self.ticks,
            "deployed": len([d for d in deployed if d.get("path") == "e_then_a"]),
            "session": session.to_dict(),
            "throttle": throttle.to_dict(),
            "dual": dual.to_dict(),
            "pair": pair.to_dict(),
            "polymarket": poly.to_dict(),
            "routes": deployed,
        }

    def _poll_c(self) -> Any:
        port = self.ports.get("loop_c")
        if port is None:
            from sigma.loops.loop_c import LoopCPort
            port = LoopCPort()
        if hasattr(port, "poll_pair"):
            return port.poll_pair()
        return port.poll()

    def _plan(self, symbol, session, series, htf_series, pair):
        name = session.recommended_strategy
        if name == "DUAL_HEDGE_DCA":
            tmpl = self.templates["dual_hedge_grid"]
        elif name == "MICRO_RANGE_GRID":
            tmpl = self.templates["dynamic_channel_dca"]
        else:
            tmpl = self.templates["htf_trend_ltf_reversion"]
        return tmpl.plan({
            "symbol": symbol,
            "session": session.to_dict(),
            "htf_candles": (htf_series or {}).get(symbol) or (htf_series or {}).get("BTC/USD") or [],
            "ltf_candles": (series or {}).get(symbol) or [],
            "htf_interval_min": pair.bias_minutes,
            "ltf_interval_min": pair.exec_minutes,
        })

    def _loop_e(self, strategy_id: str, symbol: str, tf: int, regime: str) -> bool:
        port = self.ports.get("loop_e")
        if port is None:
            return True
        plan = port.allocate(strategy_id, symbol, tf, regime)
        return bool(getattr(plan, "allow", False))

    def _loop_a_paper(self, intent) -> Dict[str, Any]:
        port = self.ports.get("loop_a")
        payload = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)
        payload["execution_mode"] = "kraken_paper"
        if port is None:
            return {"ok": False, "reason": "pipeline_unavailable", "mode": "kraken_paper"}
        receipt = port.ingest(payload)
        return receipt.to_dict() if hasattr(receipt, "to_dict") else {"ok": False}

    def _loop_d_paper(self, symbol: str, regime: str) -> None:
        port = self.ports.get("loop_d")
        if port is None:
            return
        try:
            port.tick(regime=regime, strategy_ids=["htf_trend_ltf_reversion"], limit=1)
        except Exception:
            return

    def _idle(self, reason, session, throttle, dual, pair, poly) -> Dict[str, Any]:
        return {
            "ok": True,
            "status": reason,
            "phase": 3,
            "ticks": self.ticks,
            "deployed": 0,
            "session": session.to_dict(),
            "throttle": throttle.to_dict(),
            "dual": dual.to_dict(),
            "pair": pair.to_dict(),
            "polymarket": poly.to_dict(),
            "routes": [],
        }

    def _unwind_only(self, session, throttle, dual, pair, poly) -> Dict[str, Any]:
        out = self._idle("unwind_only", session, throttle, dual, pair, poly)
        out["status"] = "unwind_only"
        return out


def _first_series(series) -> list:
    if not series:
        return []
    return next(iter(series.values()))


def _htf_series(snap) -> dict:
    if snap is None:
        return {}
    if hasattr(snap, "htf_series"):
        return getattr(snap, "htf_series") or {}
    if isinstance(snap, dict):
        return snap.get("htf_series") or {}
    return {}
