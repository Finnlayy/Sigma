"""
=========================================================
Datei:      app/core/event_bus.py
Zweck:      In-Process Pub/Sub + Idempotenz-Registry (Phase 3 LAN events)
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================
Flow (Blueprint Phase 3):
  Pub/Sub wake (strategies:wake_up) -> idempotent TradeAutopsyEvent -> React
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, AsyncIterator, Dict, Deque, List, Optional, Set

logger = logging.getLogger("app.core.event_bus")


class EventBus:
    """Async in-process topic bus with per-subscriber queues and idempotency."""

    TOPIC_WAKE = "strategies:wake_up"
    TOPIC_AUTOPSY = "autopsy:events"
    TOPIC_TRADE_CLOSED = "trades:closed"
    TOPIC_TELEMETRY = "telemetry:beat"
    TOPIC_STATE_CHANGE = "m8:state:change"
    TOPIC_LOG = "logs"

    def __init__(self, idempotency_memory: int = 4096, log_memory: int = 250):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._seen_event_ids: Set[str] = set()
        self._seen_order: Deque[str] = deque()
        self._idempotency_memory = idempotency_memory
        self.recent_logs: Deque[Dict[str, Any]] = deque(maxlen=log_memory)

    # ------------------------------------------------------------------ publish
    def publish_sync(self, topic: str, event: Dict[str, Any]) -> str:
        """Synchronous publish (safe to call from sync context)."""
        event_id = event.get("event_id") or uuid.uuid4().hex
        stamped = {**event, "event_id": event_id, "published_at": time.time()}
        if self._is_duplicate(stamped):
            return event_id
        self._remember(event_id, stamped.get("natural_key"))
        handlers = self._subscribers.get(topic, [])
        for queue in handlers:
            try:
                queue.put_nowait(stamped)
            except asyncio.QueueFull:
                pass
        return event_id

    async def publish(self, topic: str, event: Dict[str, Any]) -> str:
        return self.publish_sync(topic, event)

    # --------------------------------------------------------------- subscribe
    def subscribe(self, topic: str, maxsize: int = 512) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.setdefault(topic, []).append(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        handlers = self._subscribers.get(topic, [])
        if queue in handlers:
            handlers.remove(queue)

    async def iter_queue(self, queue: asyncio.Queue) -> AsyncIterator[Dict[str, Any]]:
        while True:
            item = await queue.get()
            yield item

    # -------------------------------------------------------------- idempotency
    def _is_duplicate(self, event: Dict[str, Any]) -> bool:
        event_id = event.get("event_id")
        if event_id and event_id in self._seen_event_ids:
            return True
        natural = event.get("natural_key")
        if natural and f"nat:{natural}" in self._seen_event_ids:
            return True
        return False

    def _remember(self, event_id: str, natural_key: Optional[str]) -> None:
        self._seen_event_ids.add(event_id)
        self._seen_order.append(event_id)
        while len(self._seen_order) > self._idempotency_memory:
            old = self._seen_order.popleft()
            self._seen_event_ids.discard(old)
        if natural_key:
            self._seen_event_ids.add(f"nat:{natural_key}")

    def is_duplicate(self, topic: str, event_id: str) -> bool:
        return event_id in self._seen_event_ids

    # ------------------------------------------------------------------- logs
    def log(self, level: str, message: str, category: str = "SYSTEM",
            payload: Optional[Dict[str, Any]] = None, strategy_id: Optional[str] = None) -> None:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level,
            "category": category,
            "message": message,
            "strategyId": strategy_id,
            "payload": payload or {},
        }
        self.recent_logs.append(entry)
        self.publish_sync(self.TOPIC_LOG, entry)
        logger.log(
            {"info": logging.INFO, "warn": logging.WARNING,
             "error": logging.ERROR, "trade": logging.INFO}[level],
            "%s [%s] %s", category, level, message,
        )

    def recent_logs_list(self, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self.recent_logs)[-limit:]

    def to_log_rows(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Rows for /api/logs (ExecutionLog contract)."""
        rows = []
        for entry in self.recent_logs_list(limit):
            rows.append({
                "id": entry["id"],
                "timestamp": entry["iso"],
                "level": "error" if entry["level"] == "error"
                         else "warn" if entry["level"] == "warn"
                         else "trade" if entry["category"] == "TRADE" else "info",
                "message": entry["message"],
                "strategyId": entry.get("strategyId"),
            })
        return rows


_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
