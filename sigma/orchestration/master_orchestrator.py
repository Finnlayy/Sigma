"""
=========================================================
Datei:      sigma/orchestration/master_orchestrator.py
Zweck:      C-Snapshot + Dual-Hurst + Wave-Regime + Ladder + Session/Gap.
            htf_ready fail-closed. INVALIDATED → unwind. E dann A.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Orchestrierung) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from sigma.signals.dual_hurst import evaluate_dual_hurst
from sigma.signals.polymarket_layer0 import layer0_pre_regime
from sigma.signals.quantum_wave_collider import (
    STATUS_INVALIDATED,
    QuantumWaveCollider,
    WaveScreen,
)
from sigma.signals.session_clock import SessionClock
from sigma.signals.timeframe_ladder import session_exec_pair
from sigma.signals.volatility_throttle import VolatilityThrottleGate
from sigma.orchestration.multi_asset_router import MultiAssetRouter
from sigma.strategies.dual_hedge_grid import DualHedgeGrid
from sigma.strategies.dynamic_channel_dca import DynamicChannelDCA
from sigma.strategies.htf_trend_ltf_reversion import HtfTrendLtfReversion

logger = logging.getLogger("sigma.orchestration")


class MasterOrchestrator:
    """Closed-graph conductor. Fail-closed on open HTF / SLEEP / 21:00 UTC gap / wave INVALIDATED."""

    def __init__(
        self,
        *,
        ports: Optional[Dict[str, Any]] = None,
        universe: Optional[Any] = None,
        hydrate_cooldown_s: float = 30.0,
    ) -> None:
        self.ports = ports or {}
        self.ticks = 0
        self.clock = SessionClock()
        self.throttle = VolatilityThrottleGate()
        self.router = MultiAssetRouter()
        self.collider = QuantumWaveCollider()
        self.templates = {
            "htf_trend_ltf_reversion": HtfTrendLtfReversion(),
            "dynamic_channel_dca": DynamicChannelDCA(),
            "dual_hedge_grid": DualHedgeGrid(),
        }
        from sigma.execution.universe import default_execution_universe

        # Venue = Source of Truth fürs tradable Universe — nicht Scraper,
        # nicht config.market_symbols.
        self.universe = universe if universe is not None else default_execution_universe()
        # Rate-Limit-Schutz: Lücken-Hydrate nur im Cooldown-Raster.
        self.hydrate_cooldown_s = float(hydrate_cooldown_s)
        self._last_hydrate_ts = 0.0

    def tick(self, snapshot: Optional[Any] = None, *, now: Optional[float] = None) -> Dict[str, Any]:
        self.ticks += 1
        snap = snapshot if snapshot is not None else self._poll_c()
        series = getattr(snap, "series", None) if snap is not None else None
        if series is None and isinstance(snap, dict):
            series = snap.get("series")
        htf_series = _htf_series(snap)
        session = self.clock.evaluate(now)
        pair = session_exec_pair(session.session, use_ict_ladder=False)
        leader = "BTC/USD"
        ltf = (series or {}).get(leader) or _first_series(series)
        htf = (htf_series or {}).get(leader) or ltf
        dual = evaluate_dual_hurst(htf, ltf, htf_interval_min=pair.bias_minutes, now=now)
        btc = (series or {}).get(leader) or []
        throttle = self.throttle.evaluate(btc, force_sleep=session.liquidity_gap)
        poly = layer0_pre_regime(self.ports.get("polymarket"))
        wave = self.collider.evaluate(
            htf,
            _alt_htf(htf_series, series, leader),
            interval_min=pair.bias_minutes,
            now=now,
        )
        # Wave-Screen über dem Execution-Universe: tradable Kollapse →
        # Loop D Scout / Academy; leerer Screen fällt auf Universe-Defaults.
        # Paper-only — kein Deploy-Trigger, auch im Unwind/Idle-Pfad.
        screen = self._screen_pipeline(htf_series, leader, pair.bias_minutes, now)
        self._screen_to_loop_d_academy(screen, session.session)
        if not dual.htf_ready:
            return self._idle("htf_not_ready", session, throttle, dual, pair, poly, wave, screen)
        if throttle.mode == "SLEEP" or session.liquidity_gap:
            return self._unwind_only(session, throttle, dual, pair, poly, wave, screen)
        if wave.status == STATUS_INVALIDATED:
            return self._unwind_only(session, throttle, dual, pair, poly, wave, screen)
        routes = self.router.route(series, session=session, now=now, use_ict_ladder=False, leader=leader)
        cap = int(throttle.allowed_concurrent_bots)
        deployed: List[Dict[str, Any]] = []
        for route in routes[: max(0, cap)]:
            if route.paper_only:
                self._loop_d_paper(route.symbol, session.session)
                deployed.append({**route.to_dict(), "path": "loop_d_paper"})
                continue
            intent = self._plan(route.symbol, session, series, htf_series, pair, wave)
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
            "wave": wave.to_dict(),
            "screen": screen.to_dict(),
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

    def _plan(self, symbol, session, series, htf_series, pair, wave=None):
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
            "wave": wave.to_dict() if wave is not None and hasattr(wave, "to_dict") else (wave or {}),
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

    def _idle(self, reason, session, throttle, dual, pair, poly, wave=None,
              screen: Optional[WaveScreen] = None) -> Dict[str, Any]:
        return {
            "ok": True,
            "status": reason,
            "phase": 3,
            "ticks": self.ticks,
            "deployed": 0,
            "session": session.to_dict(),
            "throttle": throttle.to_dict(),
            "dual": dual.to_dict(),
            "wave": wave.to_dict() if wave is not None and hasattr(wave, "to_dict") else {},
            "screen": screen.to_dict() if screen is not None and hasattr(screen, "to_dict") else {},
            "pair": pair.to_dict(),
            "polymarket": poly.to_dict(),
            "routes": [],
        }

    def _unwind_only(self, session, throttle, dual, pair, poly, wave=None,
                     screen: Optional[WaveScreen] = None) -> Dict[str, Any]:
        out = self._idle("unwind_only", session, throttle, dual, pair, poly, wave, screen)
        out["status"] = "unwind_only"
        return out

    # ------------------------------------------------------------ wave screen
    def _screen_pipeline(self, htf_series, leader: str, interval_min: int,
                         now: Optional[float]) -> WaveScreen:
        """Universe-Watchlist aus C-Cache + parallelem Lücken-Hydrate.

        cached = vorhandene htf_series aus poll_pair() (nur wanted);
        missing = Universe-Symbole ohne Serie → hydrate_htf (Worker-Cap 4,
        Fail-Closed: synthetic/degraded wird verworfen). Sidecar down →
        leere Serie → leerer Screen → Scout fällt auf Universe-Defaults.
        """
        wanted = list(self.universe.list_symbols())
        cached = {
            s: list(c) for s, c in (htf_series or {}).items()
            if s in wanted and c
        }
        missing = [s for s in wanted if s not in cached]
        if missing and self._hydrate_due():
            filled = self._hydrate_missing(missing, interval_min)
            self._last_hydrate_ts = time.time()
            cached = {**cached, **filled}
        return self.collider.screen(
            cached,
            universe=self.universe,
            leader=leader,
            interval_min=interval_min,
            now=now,
        )

    def _hydrate_due(self) -> bool:
        if self.hydrate_cooldown_s <= 0:
            return True
        return (time.time() - self._last_hydrate_ts) >= self.hydrate_cooldown_s

    def _hydrate_missing(self, symbols: List[str], interval_min: int) -> Dict[str, Any]:
        port = self.ports.get("loop_c")
        if port is None or not symbols:
            return {}
        try:
            if hasattr(port, "hydrate_htf"):
                return port.hydrate_htf(symbols, interval_min=interval_min, workers=4) or {}
        except Exception as exc:
            logger.info("wave screen hydrate failed: %s", exc)
        return {}

    def _screen_to_loop_d_academy(self, screen: WaveScreen, regime: str) -> Dict[str, Any]:
        """Loop D Scout auf Screen-Symbolen (leer → Universe-Defaults) +
        Academy-Watchlist (nur tradable Kandidaten). Paper only."""
        symbols = [c.symbol for c in screen.candidates] or list(screen.defaults)
        outcome: Dict[str, Any] = {"symbols": symbols}
        port_d = self.ports.get("loop_d")
        if port_d is not None:
            try:
                port_d.tick(
                    regime=regime,
                    strategy_ids=["htf_trend_ltf_reversion"],
                    limit=1,
                    symbols=symbols,
                    universe=self.universe,
                )
                outcome["loop_d"] = "planned"
            except Exception as exc:
                logger.info("wave screen loop_d failed: %s", exc)
                outcome["loop_d"] = "failed"
        academy = self.ports.get("academy")
        if academy is not None:
            try:
                prev = list(getattr(academy, "wave_watchlist", None) or [])
                watch = academy.ingest_wave_screen(screen.candidates, defaults=list(screen.defaults))
                # Drills nur, wenn sich die Watchlist geändert hat — sonst
                # schreibt jeder Tick die gleichen DR-01..05 in den Store.
                if watch != prev:
                    academy.drill_watchlist(["htf_trend_ltf_reversion"])
                outcome["academy_watchlist"] = list(watch)
            except Exception as exc:
                logger.info("wave screen academy failed: %s", exc)
                outcome["academy"] = "failed"
        return outcome


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


def _alt_htf(htf_series, series, leader: str) -> Optional[list]:
    rows = htf_series or {}
    for symbol, candles in rows.items():
        if symbol != leader and candles:
            return list(candles)
    live = series or {}
    for symbol, candles in live.items():
        if symbol != leader and candles:
            return list(candles)
    return None
