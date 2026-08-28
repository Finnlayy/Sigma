"""
=========================================================
Datei:      app/execution/reliable_order_dispatcher.py
Zweck:      §25 / Axiom 6 — Closed-Loop Order ACK, Idempotenz & Smart Retry
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================

Kein Fire-and-Forget. Jeder Alert durchlaeuft:

1. **Idempotenz** — ``idempotency_key`` bereits gesehen -> ``DUPLICATE_IGNORED``
2. **Execute**    — Kraken CLI mit ``fixed_leverage`` + nativem Bracket-SL
3. **ACK**        — ``order_id`` + ``FILLED`` / ``RETRY_SUCCESS`` / ``FAILED_REJECTED``
4. **Smart Retry**— max 2 Versuche, nie bei ``Insufficient funds`` / ``Invalid arguments``
5. **Ghost-Fill** — vor jedem Retry ``open-orders``/Trades-Check (<200 ms)
6. **Notify**     — Receipt nach ``orders.jsonl`` + Telegram + ``OrderReceiptsPanel``
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.execution.reliable_order_dispatcher")


def is_retryable(error_text: str) -> bool:
    """§25.4 — permanente Fehler nie erneut senden."""
    blob = (error_text or "").lower()
    return not any(pattern in blob for pattern in bp.ORDER_NON_RETRYABLE_PATTERNS)


def build_idempotency_key(strategy_id: str, ticker: str, bar_time: Any) -> str:
    return bp.IDEMPOTENCY_KEY_TEMPLATE.format(
        strategy_id=strategy_id, ticker=ticker, bar_time=bar_time
    )


@dataclass
class OrderRequest:
    idempotency_key: str
    strategy_id: str
    pair: str
    side: str
    volume: float
    ordertype: str = "market"
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    fixed_leverage: int = bp.FIXED_LEVERAGE_DEFAULT
    bot_id: str = ""
    execution_mode: str = bp.EXECUTION_MODE_DEFAULT


@dataclass
class OrderReceipt:
    idempotency_key: str
    strategy_id: str
    bot_id: str
    pair: str
    side: str
    volume: float
    ack: str
    order_id: str = ""
    attempts: int = 0
    execution_mode: str = bp.EXECUTION_MODE_DEFAULT
    fixed_leverage: int = bp.FIXED_LEVERAGE_DEFAULT
    error_code: str = ""
    detail: str = ""
    ghost_fill_detected: bool = False
    latency_ms: float = 0.0
    ts: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        return self.ack in (bp.OrderAck.FILLED.value, bp.OrderAck.RETRY_SUCCESS.value)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["success"] = self.success
        return data


class ReliableOrderDispatcher:
    """Closed-Loop-Dispatcher vor der Kraken CLI (§25)."""

    def __init__(
        self,
        bridge: Any,
        *,
        paper_bridge: Any = None,
        notifier: Optional[Callable[[OrderReceipt], None]] = None,
        receipts_log: str = bp.ORDER_RECEIPT_LOG,
        max_retries: int = bp.ORDER_MAX_RETRIES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bridge = bridge
        self.paper_bridge = paper_bridge
        self.notifier = notifier
        self.receipts_log = receipts_log
        self.max_retries = max_retries
        self._clock = clock
        self._seen: Dict[str, OrderReceipt] = {}
        self._receipts: List[OrderReceipt] = []

    # ----------------------------------------------------------- routing ---
    def _bridge_for(self, mode: str) -> Any:
        if mode == bp.ExecutionMode.KRAKEN_PAPER.value and self.paper_bridge is not None:
            return self.paper_bridge
        return self.bridge

    # ------------------------------------------------------- ghost fills ---
    def _ghost_fill(self, request: OrderRequest, bridge: Any) -> bool:
        """§25.5 — pruefen, ob der vermeintlich fehlgeschlagene Call doch lag."""
        checker = getattr(bridge, "open_orders", None) or getattr(bridge, "list_open_orders", None)
        if checker is None:
            return False
        started = time.perf_counter()
        try:
            orders = checker() or []
        except Exception as exc:  # pragma: no cover - defensiv
            logger.warning("ghost-fill check failed: %s", exc)
            return False
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > bp.ORDER_GHOST_FILL_CHECK_TIMEOUT_MS:
            logger.warning("ghost-fill check slow: %.0fms", elapsed_ms)
        for order in orders:
            if not isinstance(order, dict):
                continue
            same_pair = str(order.get("pair", "")).upper() == request.pair.upper()
            same_side = str(order.get("side", "")).lower() == request.side.lower()
            same_key = order.get("idempotency_key") == request.idempotency_key
            if same_key or (same_pair and same_side):
                return True
        return False

    # -------------------------------------------------------- dispatch  ---
    def dispatch(self, request: OrderRequest) -> OrderReceipt:
        if request.idempotency_key in self._seen:
            previous = self._seen[request.idempotency_key]
            receipt = OrderReceipt(
                idempotency_key=request.idempotency_key,
                strategy_id=request.strategy_id, bot_id=request.bot_id,
                pair=request.pair, side=request.side, volume=request.volume,
                ack=bp.OrderAck.DUPLICATE_IGNORED.value,
                order_id=previous.order_id, attempts=0,
                execution_mode=request.execution_mode,
                fixed_leverage=request.fixed_leverage,
                detail=f"bereits ausgefuehrt ({previous.ack})",
                ts=self._clock(),
            )
            self._record(receipt, remember=False)
            return receipt

        bridge = self._bridge_for(request.execution_mode)
        leverage = max(bp.FIXED_LEVERAGE_MIN,
                       min(bp.FIXED_LEVERAGE_MAX, int(request.fixed_leverage)))
        started = time.perf_counter()
        attempts = 0
        last_error = ""
        ghost = False

        while attempts <= self.max_retries:
            attempts += 1
            try:
                result = bridge.add_order(
                    pair=request.pair, side=request.side, volume=request.volume,
                    ordertype=request.ordertype, price=request.price,
                    stop_price=request.stop_loss,
                    leverage=leverage if leverage > 1 else None,
                    strategy_id=request.strategy_id,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                result = None

            if result is not None and getattr(result, "ok", False):
                ack = (bp.OrderAck.FILLED.value if attempts == 1
                       else bp.OrderAck.RETRY_SUCCESS.value)
                receipt = OrderReceipt(
                    idempotency_key=request.idempotency_key,
                    strategy_id=request.strategy_id, bot_id=request.bot_id,
                    pair=request.pair, side=request.side, volume=request.volume,
                    ack=ack, order_id=getattr(result, "txid", "") or "",
                    attempts=attempts, execution_mode=request.execution_mode,
                    fixed_leverage=leverage,
                    detail=f"mode={getattr(result, 'mode', '?')}",
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    ts=self._clock(),
                )
                self._record(receipt)
                return receipt

            if result is not None:
                last_error = getattr(result, "error_code", "") or \
                    (getattr(result, "stderr", "") or getattr(result, "stdout", ""))

            if not is_retryable(last_error):
                break
            if attempts > self.max_retries:
                break
            if self._ghost_fill(request, bridge):
                ghost = True
                break

        ack = bp.OrderAck.FAILED_REJECTED.value
        detail = last_error or "unbekannter Fehler"
        order_id = ""
        if ghost:
            ack = bp.OrderAck.FILLED.value
            detail = f"Ghost-Fill erkannt, kein Retry gesendet ({last_error})"
        receipt = OrderReceipt(
            idempotency_key=request.idempotency_key,
            strategy_id=request.strategy_id, bot_id=request.bot_id,
            pair=request.pair, side=request.side, volume=request.volume,
            ack=ack, order_id=order_id, attempts=attempts,
            execution_mode=request.execution_mode, fixed_leverage=leverage,
            error_code="" if ghost else (last_error or "EXECUTION_FAILED"),
            detail=detail, ghost_fill_detected=ghost,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            ts=self._clock(),
        )
        self._record(receipt)
        return receipt

    def veto(self, request: OrderRequest, reason: str,
             code: str = bp.ORDERBOOK_WALL_REJECT) -> OrderReceipt:
        """§24-Veto sauber als Receipt dokumentieren."""
        receipt = OrderReceipt(
            idempotency_key=request.idempotency_key,
            strategy_id=request.strategy_id, bot_id=request.bot_id,
            pair=request.pair, side=request.side, volume=request.volume,
            ack=bp.OrderAck.VETO_ORDERBOOK.value, attempts=0,
            execution_mode=request.execution_mode,
            fixed_leverage=request.fixed_leverage,
            error_code=code, detail=reason, ts=self._clock(),
        )
        self._record(receipt)
        return receipt

    # ---------------------------------------------------------- receipts ---
    def _record(self, receipt: OrderReceipt, *, remember: bool = True) -> None:
        if remember and receipt.ack != bp.OrderAck.DUPLICATE_IGNORED.value:
            self._seen[receipt.idempotency_key] = receipt
        self._receipts.append(receipt)
        del self._receipts[:-500]
        self._append_log(receipt)
        if self.notifier is not None:
            try:
                self.notifier(receipt)
            except Exception as exc:  # pragma: no cover - Notifier darf nie killen
                logger.warning("order notifier failed: %s", exc)

    def _append_log(self, receipt: OrderReceipt) -> None:
        try:
            os.makedirs(os.path.dirname(self.receipts_log) or ".", exist_ok=True)
            with open(self.receipts_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "receipt", **receipt.as_dict()}) + "\n")
        except OSError as exc:  # pragma: no cover
            logger.warning("receipt log append failed: %s", exc)

    def receipts(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [r.as_dict() for r in self._receipts[-limit:]]

    def seen(self, idempotency_key: str) -> bool:
        return idempotency_key in self._seen

    def forget(self, idempotency_key: str) -> None:
        self._seen.pop(idempotency_key, None)

    def panel_state(self) -> Dict[str, Any]:
        acks: Dict[str, int] = {}
        for receipt in self._receipts:
            acks[receipt.ack] = acks.get(receipt.ack, 0) + 1
        return {
            "max_retries": self.max_retries,
            "ghost_fill_timeout_ms": bp.ORDER_GHOST_FILL_CHECK_TIMEOUT_MS,
            "route": bp.ORDER_RECEIPTS_ROUTE,
            "ack_counts": acks,
            "receipts": self.receipts(25),
        }
