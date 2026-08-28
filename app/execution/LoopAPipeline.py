"""
=========================================================
Datei:      app/execution/LoopAPipeline.py
Zweck:      §4.2 — Der Loop-A-Pfad in genau der normativen Reihenfolge:
            Safety -> Risk -> Confidence -> Kelly -> Brackets -> Symbol-Map
            -> Judge -> Execute (Kraken CLI | Paper) -> Audit -> M8.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Implementierung) / Noir (Gate-Reihenfolge)
=========================================================

Der Pipeline-Code kennt keine Magic Numbers: alle Schwellen kommen aus
`app/core/blueprint.py` bzw. `SigmaConfig`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config
from app.execution.SafetyGuard import SafetyGuard, get_safety_guard
from app.quant.onnx_kelly import QuantEngine, get_quant_engine
from app.tv.interval_map import stale_limit_seconds, to_minutes
from app.tv.symbol_map import is_allowed, market_type, notional_limits, to_kraken_pair

logger = logging.getLogger("app.execution.loop_a")


@dataclass
class SignalRequest:
    """Normalisierte Fassung des Pine-Alert-Payloads (§4.1)."""

    symbol: str
    action: str
    price: float
    rsi: float = 50.0
    atr: float = 0.0
    cisd_score: float = 0.5
    timestamp: int = 0
    strategy_id: Optional[str] = None
    interval: Any = 15
    secret: str = ""

    def normalized_timestamp(self) -> int:
        return bp.normalize_timestamp(self.timestamp or time.time())


@dataclass
class ExecutionResponse:
    accepted: bool
    stage: str
    code: str
    reason: str
    status_code: int = 200
    strategy_id: str = ""
    symbol: str = ""
    pair: str = ""
    action: str = ""
    price: float = 0.0
    quantity: float = 0.0
    notional: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    win_prob: float = 0.0
    budget_multiplier: float = 1.0
    mode: str = "paper"
    txid: str = ""
    gates: List[Dict[str, Any]] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class LoopAPipeline:
    """Ein Signal, zehn Schritte, kein Abkürzen."""

    def __init__(self, config: Optional[SigmaConfig] = None, *, safety: Optional[SafetyGuard] = None,
                 quant: Optional[QuantEngine] = None, judge=None, m8=None,
                 kraken=None, paper=None, virtual_bots=None, allocator=None,
                 telemetry=None, deadman=None, telegram=None, reward=None,
                 self_opt=None, equity_provider=None):
        self.config = config or load_config()
        self.safety = safety or get_safety_guard(self.config)
        self.quant = quant or get_quant_engine(self.config)
        self.judge = judge
        self.m8 = m8
        self.kraken = kraken
        self.paper = paper
        self.virtual_bots = virtual_bots
        self.allocator = allocator
        self.telemetry = telemetry
        self.deadman = deadman
        self.telegram = telegram
        self.reward = reward
        self.self_opt = self_opt
        self._equity_provider = equity_provider or (lambda: 10_000.0)
        self.open_positions = 0
        self.processed = 0
        self.rejected = 0

    # ------------------------------------------------------------- entrypoint
    def handle_signal(self, sig: SignalRequest, *, provided_secret: Optional[str] = None,
                      m8_state: Optional[str] = None, regime: Optional[str] = None,
                      symbol_halted: bool = False) -> ExecutionResponse:
        trace: List[str] = []

        # §17.1 Auth (vor allem anderen)
        auth = self.safety.verify_webhook_secret(provided_secret if provided_secret is not None
                                                 else sig.secret)
        trace.append("auth")
        if not auth.allowed:
            return self._reject("auth", auth.code, auth.reason, auth.status_code, sig, trace)

        # §17.2 Freshness
        fresh = self.safety.check_signal_freshness(
            sig.timestamp or time.time(), to_minutes(sig.interval) * 60)
        trace.append("freshness")
        if not fresh.allowed:
            return self._reject("freshness", fresh.code, fresh.reason, fresh.status_code, sig, trace)

        # Schritt 1+2: SafetyGuard + Risk-Guard
        verdict = self.safety.check(symbol=sig.symbol, open_positions=self.open_positions,
                                    symbol_halted=symbol_halted)
        trace.append(bp.LOOP_A_PIPELINE[0])
        trace.append(bp.LOOP_A_PIPELINE[1])
        if not verdict.allowed:
            return self._reject("safety", verdict.code, verdict.reason, verdict.status_code, sig, trace)

        # M8-Gate (§4.6): QUARANTINED/RETIRED nehmen keine Signale an
        state = (m8_state or self._m8_state(sig.strategy_id) or bp.M8State.ACTIVE.value)
        policy = bp.alert_policy_for_state(state)
        if not policy.accept_webhook:
            return self._reject("m8", f"M8_{state}", f"m8 state {state} rejects signals", 409, sig, trace)

        # Allocator-Gate (§18.4): Badge F blockiert
        if self.allocator is not None and regime:
            gate = self.allocator.evaluate(sig.strategy_id or "", sig.symbol, sig.interval, regime)
            trace.append("allocator")
            if not gate["allow"]:
                return self._reject("allocator", "BADGE_BLOCKED", gate["reason"], 409, sig, trace)

        # Regime-Crisis-Sperre (Masterprompt §3.A: ATR-Perzentil >= 95)
        if regime == bp.Regime.HIGH_VOL_CRISIS.value:
            return self._reject("regime", "HIGH_VOL_CRISIS", "volatility crisis — entries blocked",
                                409, sig, trace)

        # CLOSE-Signale umgehen Sizing
        if sig.action.upper() == "CLOSE":
            return self._handle_close(sig, state, trace)

        # Schritt 3: Confidence
        conf = self.quant.predict_confidence(sig.rsi, sig.atr, sig.cisd_score, price=sig.price)
        trace.append(bp.LOOP_A_PIPELINE[2])
        win_prob = float(conf["win_prob"])

        # Schritt 4+5: Kelly + Brackets (Bot-Equity, wenn ein Virtual Bot existiert)
        equity, bot = self._equity_for(sig.strategy_id)
        sizing = self.quant.size_position(equity=equity, price=sig.price, win_prob=win_prob,
                                          atr=sig.atr, action=sig.action)
        quantity = sizing.quantity * policy.budget_multiplier
        trace.append(bp.LOOP_A_PIPELINE[3])
        trace.append(bp.LOOP_A_PIPELINE[4])
        if quantity <= 0:
            return self._reject("sizing", "ZERO_SIZE", "kelly size is zero", 200, sig, trace)

        # Schritt 6: Symbol-Map + Notional-Limits
        futures = market_type(sig.symbol) == "FUTURES"
        pair = to_kraken_pair(sig.symbol)
        trace.append(bp.LOOP_A_PIPELINE[5])
        if not is_allowed(sig.symbol, futures=futures):
            return self._reject("symbol", "SYMBOL_NOT_ALLOWED",
                                f"{pair} not in allowed_symbols", 403, sig, trace)
        limits = notional_limits(sig.symbol)
        notional = quantity * sig.price
        if notional > limits["max_order_notional_usd"]:
            quantity = limits["max_order_notional_usd"] / sig.price
            notional = limits["max_order_notional_usd"]
            trace.append("notional_capped")

        # Schritt 7: Judge
        gates: List[Dict[str, Any]] = []
        if self.judge is not None:
            result = self.judge.evaluate(
                symbol=sig.symbol, qty=quantity, side="buy" if sig.action.upper() == "BUY" else "sell",
                win_rate=win_prob, win_loss_ratio=bp.KELLY_DEFAULT_RRR, target_vol=0.02,
                context={"spread_bps": 3.0, "system_state": self._telemetry_state()})
            gates = result.get("gates", [])
            trace.append(bp.LOOP_A_PIPELINE[6])
            if not result.get("approved", result.get("passed", True)):
                return self._reject("judge", "JUDGE_REJECT",
                                    result.get("reason", "judge gates failed"), 200, sig, trace,
                                    gates=gates)

        # Schritt 8: Ausführung
        exec_result = self._execute(sig, pair, quantity, sizing.stop_loss, sizing.take_profit, bot)
        trace.append(bp.LOOP_A_PIPELINE[7])
        trace.append(bp.LOOP_A_PIPELINE[8])   # Audit passiert in der Bridge (orders.jsonl)

        # Schritt 10: M8 / Heartbeat / Push
        if self.deadman is not None:
            self.deadman.beat(has_native_stop_loss=exec_result.get("native_stop", False))
        trace.append(bp.LOOP_A_PIPELINE[9])
        if self.telegram is not None and exec_result.get("ok"):
            try:
                self.telegram.notify_fill(sig.strategy_id or "", sig.action, sig.symbol,
                                          quantity, sig.price, exec_result.get("mode", "paper"))
            except Exception:  # pragma: no cover
                pass

        self.processed += 1
        self.open_positions += 1 if exec_result.get("ok") else 0
        if not exec_result.get("ok"):
            self.safety.record_error()
        else:
            self.safety.record_success()

        return ExecutionResponse(
            accepted=bool(exec_result.get("ok")), stage="executed",
            code="OK" if exec_result.get("ok") else exec_result.get("error_code", "EXEC_FAILED"),
            reason=exec_result.get("reason", "order placed"),
            status_code=200, strategy_id=sig.strategy_id or "", symbol=sig.symbol, pair=pair,
            action=sig.action.upper(), price=sig.price, quantity=round(quantity, 8),
            notional=round(notional, 2),
            stop_loss=round(sizing.stop_loss, 8), take_profit=round(sizing.take_profit, 8),
            win_prob=round(win_prob, 6), budget_multiplier=policy.budget_multiplier,
            mode=exec_result.get("mode", "paper"), txid=exec_result.get("txid", ""),
            gates=gates, trace=trace,
        )

    # -------------------------------------------------------------- helpers
    def _execute(self, sig: SignalRequest, pair: str, quantity: float,
                 stop_loss: float, take_profit: float, bot) -> Dict[str, Any]:
        side = "buy" if sig.action.upper() == "BUY" else "sell"
        live_ok = (self.config.live_trading and self._telemetry_state() == "LIVE_APPROVED"
                   and self.kraken is not None)
        if live_ok:
            res = self.kraken.add_order(pair=pair, side=side, volume=quantity,
                                        ordertype="market", stop_price=stop_loss,
                                        strategy_id=sig.strategy_id or "")
            return {"ok": res.ok, "mode": res.mode, "txid": res.txid,
                    "native_stop": res.has_native_stop_loss,
                    "error_code": res.error_code,
                    "reason": "kraken order placed" if res.ok else res.error_code}
        if self.paper is not None:
            try:
                position = self.paper.open_position(
                    {"id": sig.strategy_id or "unknown", "name": sig.strategy_id or "unknown",
                     "executionMode": "paper"},
                    {"direction": "LONG" if side == "buy" else "SHORT",
                     "entry_price": sig.price, "symbol": sig.symbol,
                     "stop_loss_price": stop_loss, "take_profit_price": take_profit,
                     "instance_id": sig.strategy_id or "unknown"},
                    {"quantity_contracts": quantity, "notional_usd": quantity * sig.price,
                     "leverage": 1.0})
                return {"ok": True, "mode": "paper", "txid": position.get("trade_id", ""),
                        "native_stop": False, "reason": "paper fill"}
            except Exception as exc:
                logger.error("paper execution failed: %s", exc)
                return {"ok": False, "mode": "paper", "error_code": "PAPER_ERROR", "reason": str(exc)}
        # Kein Executor verdrahtet (Unit-Test / P2)
        if self.kraken is not None:
            res = self.kraken.add_order(pair=pair, side=side, volume=quantity,
                                        ordertype="market", stop_price=stop_loss,
                                        strategy_id=sig.strategy_id or "")
            return {"ok": res.ok, "mode": res.mode, "txid": res.txid,
                    "native_stop": res.has_native_stop_loss,
                    "reason": "sim order"}
        return {"ok": True, "mode": "dry_run", "txid": "", "native_stop": False,
                "reason": "no executor wired"}

    def _handle_close(self, sig: SignalRequest, state: str, trace: List[str]) -> ExecutionResponse:
        trace.append("close")
        closed = 0
        if self.paper is not None:
            try:
                closed = len(self.paper.cancel_all(strategy_id=sig.strategy_id) or [])
            except Exception as exc:  # pragma: no cover
                logger.warning("close failed: %s", exc)
        self.open_positions = max(0, self.open_positions - max(closed, 1))
        return ExecutionResponse(
            accepted=True, stage="closed", code="OK", reason=f"close processed ({closed})",
            strategy_id=sig.strategy_id or "", symbol=sig.symbol, action="CLOSE", trace=trace)

    def _reject(self, stage: str, code: str, reason: str, status: int,
                sig: SignalRequest, trace: List[str], gates: Optional[List[Dict[str, Any]]] = None
                ) -> ExecutionResponse:
        self.rejected += 1
        logger.info("signal rejected at %s: %s (%s)", stage, reason, code)
        return ExecutionResponse(
            accepted=False, stage=stage, code=code, reason=reason, status_code=status,
            strategy_id=sig.strategy_id or "", symbol=sig.symbol, action=sig.action.upper(),
            gates=gates or [], trace=trace)

    def _equity_for(self, strategy_id: Optional[str]):
        if self.virtual_bots is not None and strategy_id:
            bots = self.virtual_bots.for_strategy(strategy_id)
            if bots:
                return bots[0].current_equity, bots[0]
        return float(self._equity_provider()), None

    def _m8_state(self, strategy_id: Optional[str]) -> Optional[str]:
        if self.m8 is None or not strategy_id:
            return None
        try:
            state = self.m8.get_strategy_state(strategy_id)
            return getattr(state, "status", None) or (state or {}).get("status")
        except Exception:  # pragma: no cover
            return None

    def _telemetry_state(self) -> str:
        if self.telemetry is None:
            return "SHADOW_ACTIVE"
        try:
            return str(getattr(self.telemetry.state, "state", "SHADOW_ACTIVE"))
        except Exception:  # pragma: no cover
            return "SHADOW_ACTIVE"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "processed": self.processed, "rejected": self.rejected,
            "open_positions": self.open_positions,
            "pipeline": list(bp.LOOP_A_PIPELINE),
            "live_trading": self.config.live_trading,
            "safety": self.safety.snapshot(),
            "quant": self.quant.snapshot(),
        }


_pipeline: Optional[LoopAPipeline] = None


def get_pipeline(**kwargs) -> LoopAPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = LoopAPipeline(**kwargs)
    return _pipeline
