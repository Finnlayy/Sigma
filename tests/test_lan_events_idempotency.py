"""
=========================================================
Datei:      tests/test_lan_events_idempotency.py
Zweck:      Phase-3-Acceptance: Pub/Sub wake -> idempotentes
            TradeAutopsyEvent -> React-Handoff (SSE-Payload-Shape)
Knoten:     Jaune (Carrera-Engine) / Test Suite
=========================================================
"""
import asyncio

import pytest

from app.core.event_bus import EventBus
from app.execution.AutopsyProcessor import process_trade_autopsy


@pytest.fixture
def bus():
    return EventBus()


@pytest.mark.asyncio
async def test_wake_publish_delivers_to_subscriber(bus):
    queue = bus.subscribe(EventBus.TOPIC_WAKE)
    bus.publish_sync(EventBus.TOPIC_WAKE, {
        "instance_id": "STRAT_X__BTC-USDT__15m__PAPER",
        "reason": "BUDGET_ZERO_QUARANTINE",
    })
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["instance_id"] == "STRAT_X__BTC-USDT__15m__PAPER"
    assert event["reason"] == "BUDGET_ZERO_QUARANTINE"
    assert "event_id" in event
    assert "published_at" in event


@pytest.mark.asyncio
async def test_duplicate_wake_is_dropped(bus):
    queue = bus.subscribe(EventBus.TOPIC_WAKE)
    eid = bus.publish_sync(EventBus.TOPIC_WAKE, {
        "instance_id": "STRAT_Y", "reason": "EOD_7D_PF_QUARANTINE",
        "event_id": "fixed-id-123",
    })
    first = await asyncio.wait_for(queue.get(), timeout=1.0)
    # Exakt gleiche event_id erneut -> Idempotenz-Drop
    bus.publish_sync(EventBus.TOPIC_WAKE, {
        "instance_id": "STRAT_Y", "reason": "EOD_7D_PF_QUARANTINE",
        "event_id": "fixed-id-123",
    })
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.3)
    assert first["event_id"] == "fixed-id-123"
    assert eid == "fixed-id-123"


@pytest.mark.asyncio
async def test_autopsy_event_natural_key_idempotency(bus):
    """Doppelte Autopsie desselben Trades (z.B. Retry) wird nat-key-gedroppt."""
    queue = bus.subscribe(EventBus.TOPIC_AUTOPSY)
    trade = {
        "trade_id": "trd_dup_1",
        "instance_id": "STRAT_Z",
        "strategy_id": "STRAT_Z",
        "symbol": "ETH/USD",
        "direction": "SHORT",
        "exit_reason": "TAKE_PROFIT",
        "net_pnl_usd": 3.2,
        "gross_pnl_usd": 3.5,
        "fees_usd": 0.3,
        "stop_slippage_bps": 0.0,
        "hold_seconds": 600.0,
        "r_multiples": {"pnl_r": 1.6, "mfe_r": 2.0, "mae_r": -0.4, "capture_ratio": 0.8},
    }
    e1 = process_trade_autopsy(trade)
    bus.publish_sync(EventBus.TOPIC_AUTOPSY, e1)
    first = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert first["autopsy_zone"] == "GOOD"
    assert first["capture_ratio"] == 0.8
    # Retry desselben Trades
    e2 = process_trade_autopsy(trade)
    bus.publish_sync(EventBus.TOPIC_AUTOPSY, e2)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.3)


@pytest.mark.asyncio
async def test_log_bus_produces_execution_log_rows(bus):
    bus.log("info", "Pipeline start", category="SYSTEM")
    bus.log("trade", "PAPER FILL LONG 0.001 BTC/USD @ 97000", category="TRADE",
            strategy_id="STRAT_X")
    bus.log("error", "Redis timeout", category="ERROR")
    rows = bus.to_log_rows(50)
    assert len(rows) == 3
    assert rows[0]["level"] == "info"
    assert rows[1]["level"] == "trade"
    assert rows[2]["level"] == "error"
    assert rows[1]["strategyId"] == "STRAT_X"
