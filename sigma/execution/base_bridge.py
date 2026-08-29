"""
=========================================================
Datei:      sigma/execution/base_bridge.py
Zweck:      BaseExecutionBridge.submit(intent) -> ExecutionReceipt
            Kraken-CLI-Wrapper zuerst; CCXT-Stub NotImplemented / nicht live.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Execution-Contract)
=========================================================
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sigma.strategies.base_strategy import StrategyIntent


@dataclass
class ExecutionReceipt:
    """Einheitliche Quittung für Library-Bridges und Loop-A-Ports."""

    ok: bool = False
    accepted: bool = False
    order_id: str = ""
    mode: str = "paper"
    code: str = ""
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_pipeline(cls, response: Any) -> "ExecutionReceipt":
        accepted = bool(getattr(response, "accepted", False))
        return cls(
            ok=accepted,
            accepted=accepted,
            order_id=str(getattr(response, "txid", "") or ""),
            mode=str(getattr(response, "mode", "paper") or "paper"),
            code=str(getattr(response, "code", "") or ""),
            reason=str(getattr(response, "reason", "") or ""),
            details=response.to_dict() if hasattr(response, "to_dict") else {},
        )


class BaseExecutionBridge(ABC):
    """Live-Orders nur über Kraken CLI / Loop A. Andere Bridges sind nicht live."""

    live_registered: bool = False

    @abstractmethod
    def submit(self, intent: StrategyIntent) -> ExecutionReceipt:
        """Reicht einen Intent an den Executor weiter."""


class KrakenCliExecutionBridge(BaseExecutionBridge):
    """Dünner Wrapper um ``app.execution.KrakenCliBridge`` — der einzige Live-Pfad."""

    live_registered = True

    def __init__(self, bridge: Any = None) -> None:
        self.bridge = bridge

    def _bridge(self) -> Any:
        if self.bridge is not None:
            return self.bridge
        from app.execution.KrakenCliBridge import KrakenCliBridge

        self.bridge = KrakenCliBridge()
        return self.bridge

    def submit(self, intent: StrategyIntent) -> ExecutionReceipt:
        if intent is None or not intent.volume or str(intent.action).upper() == "FLAT":
            return ExecutionReceipt(ok=False, accepted=False, reason="empty_intent")
        side = (intent.side or intent.action or "").lower()
        if side in ("buy", "long"):
            side = "buy"
        elif side in ("sell", "short", "close"):
            side = "sell"
        else:
            return ExecutionReceipt(ok=False, accepted=False, reason="invalid_side")
        pair = intent.pair or intent.symbol
        result = self._bridge().add_order(
            pair=pair,
            side=side,
            volume=float(intent.volume),
            stop_price=intent.stop_loss or None,
            strategy_id=intent.strategy_id,
        )
        ok = bool(getattr(result, "ok", False))
        return ExecutionReceipt(
            ok=ok,
            accepted=ok,
            order_id=str(getattr(result, "txid", "") or ""),
            mode=str(getattr(result, "mode", "sim") or "sim"),
            code=str(getattr(result, "error_code", "") or ""),
            reason="" if ok else str(getattr(result, "error_code", "") or "rejected"),
            details=result.to_dict() if hasattr(result, "to_dict") else {},
        )


class CcxtExecutionBridge(BaseExecutionBridge):
    """Library-Interface only. Nicht live-registriert — kein Order-Pfad."""

    live_registered = False

    def submit(self, intent: StrategyIntent) -> ExecutionReceipt:
        raise NotImplementedError(
            "CCXTBridge is not live-registered; live orders go through "
            "Kraken CLI / Loop A only"
        )
