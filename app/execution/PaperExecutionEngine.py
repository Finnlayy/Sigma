"""
=========================================================
Datei:      app/execution/PaperExecutionEngine.py
Zweck:      Shadow-Execution (Jaune) — Paper-Fills, MFE/MAE-Tracking,
            Liquidation/Stop/TP-Auslösung, Fee-Abrechnung, Autopsy-Handoff
Knoten:     Jaune (Carrera-Engine)
=========================================================
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

from app.core.event_bus import get_event_bus
from app.execution.FeeEngine import FeeEngine
from app.execution.AutopsyProcessor import (
    calculate_r_multiples,
    process_trade_autopsy,
    stop_slippage_bps,
)

logger = logging.getLogger("app.execution.paper_engine")


async def process_paper_tick(state_engine, current_tick_price: float,
                             position: Dict[str, Any]) -> bool:
    """Module-level Hook (TDD-Schnittstelle): prüft Liquidation/Stop/TP.
    Returns True, wenn die Position durch diesen Tick geschlossen wurde."""
    engine = PaperExecutionEngine.get_instance()
    return await engine._evaluate_exit(state_engine, position, current_tick_price)


class PaperExecutionEngine:
    _instance: Optional["PaperExecutionEngine"] = None

    def __init__(self, fee_engine: Optional[FeeEngine] = None, config=None,
                 realized_pnl_handler=None):
        from app.core.config import load_config

        self.config = config or load_config()
        self.fee_engine = fee_engine or FeeEngine(
            maker_fee_rate=self.config.maker_fee_rate,
            taker_fee_rate=self.config.taker_fee_rate,
        )
        self.realized_pnl_handler = realized_pnl_handler
        self.open_positions: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get_instance(cls) -> "PaperExecutionEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------- open/close
    def open_position(self, strategy: Dict[str, Any], signal: Dict[str, Any],
                      sizing: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        trade_id = f"trd_{uuid.uuid4().hex[:10]}"
        direction = signal["direction"].upper()
        entry_price = float(signal["entry_price"])
        position = {
            "trade_id": trade_id,
            "instance_id": signal.get("instance_id") or strategy["id"],
            "strategy_id": strategy["id"],
            "strategy_name": strategy.get("name"),
            "symbol": signal.get("symbol"),
            "execution_mode": (strategy.get("executionMode") or "paper").lower(),
            "market_type": signal.get("market_type") or "PERP",
            "direction": direction,
            "side": "buy" if direction == "LONG" else "sell",
            "status": "open",
            "entry_time": now,
            "entry_price": entry_price,
            "stop_loss_price": float(signal.get("stop_loss_price") or 0.0),
            "take_profit_price": float(signal.get("take_profit_price") or 0.0),
            "quantity": float(sizing.get("quantity_contracts") or 0.0),
            "margin_usd": float(sizing.get("margin_usd") or 0.0),
            "leverage": float(sizing.get("leverage") or 1.0),
            "notional_usd": float(sizing.get("notional_usd") or 0.0),
            "estimated_liquidation_price": sizing.get("estimated_liquidation_price"),
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "funding_usd": 0.0,
            "fee_hurdle_multiple": signal.get("fee_hurdle_multiple"),
        }
        self.open_positions[trade_id] = position
        self._persist_open(position)
        get_event_bus().log(
            "trade",
            f" PAPER FILL {direction} {position['quantity']:.6f} {position['symbol']} @ {entry_price:.2f} "
            f"({strategy.get('name')})",
            category="TRADE", strategy_id=strategy["id"],
            payload={"trade_id": trade_id, "price": entry_price, "mode": position["execution_mode"]},
        )
        return position

    def _persist_open(self, p: Dict[str, Any]) -> None:
        try:
            from app.core.duckdb_store import get_store

            get_store().upsert_trade({
                **p,
                "entry_time": _iso(p["entry_time"]),
                "exit_time": None,
                "status": "open",
            })
        except Exception as exc:
            logger.warning("persist open failed: %s", exc)

    # ------------------------------------------------------------- tick logic
    async def _evaluate_exit(self, state_engine, position: Dict[str, Any],
                             current_tick_price: float) -> bool:
        direction = position["direction"]
        entry = float(position["entry_price"])
        if entry <= 0:
            return False

        if direction == "LONG":
            pnl_pct = (current_tick_price - entry) / entry
            mae_pct = min(float(position.get("mae_pct") or 0.0), pnl_pct)
            mfe_pct = max(float(position.get("mfe_pct") or 0.0), pnl_pct)
        else:
            pnl_pct = (entry - current_tick_price) / entry
            mae_pct = min(float(position.get("mae_pct") or 0.0), pnl_pct)
            mfe_pct = max(float(position.get("mfe_pct") or 0.0), pnl_pct)
        position["mfe_pct"] = mfe_pct
        position["mae_pct"] = mae_pct

        # 1. Liquidation (PAPER: zerstört Budget vollständig)
        liq_price = position.get("estimated_liquidation_price")
        if liq_price:
            if direction == "LONG" and current_tick_price <= float(liq_price):
                await self.close_position(state_engine, position, float(liq_price), "LIQUIDATION")
                return True
            if direction == "SHORT" and current_tick_price >= float(liq_price):
                await self.close_position(state_engine, position, float(liq_price), "LIQUIDATION")
                return True

        # 2. Stop Loss
        stop = float(position.get("stop_loss_price") or 0.0)
        if stop > 0:
            if direction == "LONG" and current_tick_price <= stop:
                await self.close_position(state_engine, position, stop, "STOP_LOSS")
                return True
            if direction == "SHORT" and current_tick_price >= stop:
                await self.close_position(state_engine, position, stop, "STOP_LOSS")
                return True

        # 3. Take Profit
        tp = float(position.get("take_profit_price") or 0.0)
        if tp > 0:
            if direction == "LONG" and current_tick_price >= tp:
                await self.close_position(state_engine, position, tp, "TAKE_PROFIT")
                return True
            if direction == "SHORT" and current_tick_price <= tp:
                await self.close_position(state_engine, position, tp, "TAKE_PROFIT")
                return True
        return False

    async def close_position(self, state_engine, position: Dict[str, Any],
                             exit_price: float, exit_reason: str) -> Dict[str, Any]:
        if position.get("status") == "closed":
            return position
        now = time.time()
        direction = position.get("direction") or "LONG"
        entry = float(position.get("entry_price") or 0.0)
        qty = float(position.get("quantity") or 0.0)
        # Defensive Defaults für minimale Position-Records (TDD-Schnittstelle)
        position.setdefault("trade_id", f"trd_{uuid.uuid4().hex[:10]}")
        position.setdefault("entry_time", now)
        position.setdefault("instance_id", position.get("strategy_id") or "unknown")
        position.setdefault("strategy_id", "unknown")
        position.setdefault("strategy_name", "Unknown")
        position.setdefault("symbol", "BTC/USD")
        position.setdefault("execution_mode", "paper")
        position.setdefault("market_type", "PERP")
        position.setdefault("side", "buy" if direction == "LONG" else "sell")
        position.setdefault("leverage", 1.0)
        position.setdefault("notional_usd", entry * qty if qty else 0.0)

        if direction == "LONG":
            gross = (exit_price - entry) * qty
        else:
            gross = (entry - exit_price) * qty
        notional_entry = float(position.get("notional_usd") or (entry * qty))
        notional_exit = qty * exit_price

        liq = exit_reason.upper() == "LIQUIDATION"
        if liq:
            # Liquidation vernichtet das gesamte Margin-Budget (TDD: budget -> 0.0).
            # Ohne explizite Margin im Position-Record wird das aktuelle
            # Strategie-Budget der Instanz vernichtet (frozen v1.2.0-Semantik).
            gross = -float(position.get("margin_usd") or 0.0)
            if gross == 0.0 and state_engine is not None:
                st = state_engine.get_strategy_state(position.get("instance_id"))
                if st is not None and st.current_budget_usd > 0:
                    gross = -st.current_budget_usd

        # PAPER: Stop-Fills mit simuliertem Slippage (0.5 bps)
        fill_price = exit_price
        slippage_bps = 0.0
        if exit_reason.upper() == "STOP_LOSS":
            slippage_bps = 0.5
            fill_price = exit_price * (1 - slippage_bps / 10_000.0) if direction == "LONG" \
                else exit_price * (1 + slippage_bps / 10_000.0)
            if direction == "LONG":
                gross = (fill_price - entry) * qty
            else:
                gross = (entry - fill_price) * qty
            notional_exit = qty * fill_price

        fees = self.fee_engine.calculate_net_pnl(
            entry_notional_usd=notional_entry,
            exit_notional_usd=notional_exit,
            gross_pnl_usd=gross,
            entry_execution_type="TAKER",
            exit_execution_type="TAKER",
            funding_fee_accumulated_usd=float(position.get("funding_usd") or 0.0),
        )

        stop_distance_pct = 0.0
        if position.get("stop_loss_price"):
            s = abs(entry - float(position["stop_loss_price"])) / entry
            stop_distance_pct = s
        r_metrics = calculate_r_multiples(
            pnl_pct=fees.net_pnl_usd / notional_entry if notional_entry > 0 else 0.0,
            mfe_pct=float(position.get("mfe_pct") or 0.0),
            mae_pct=float(position.get("mae_pct") or 0.0),
            stop_distance_pct=stop_distance_pct,
        ) if stop_distance_pct > 0 else {
            "pnl_r": 0.0, "mfe_r": 0.0, "mae_r": 0.0, "capture_ratio": 0.0
        }

        trade_record = {
            "trade_id": position["trade_id"],
            "instance_id": position["instance_id"],
            "strategy_id": position["strategy_id"],
            "strategy_name": position["strategy_name"],
            "symbol": position["symbol"],
            "execution_mode": position["execution_mode"],
            "market_type": position["market_type"],
            "direction": direction,
            "side": position["side"],
            "status": "closed",
            "entry_time": _iso(position["entry_time"]),
            "exit_time": _iso(now),
            "entry_price": entry,
            "exit_price": fill_price,
            "quantity": qty,
            "margin_usd": float(position.get("margin_usd") or 0.0),
            "leverage": float(position.get("leverage") or 1.0),
            "notional_usd": float(position.get("notional_usd") or 0.0),
            "gross_pnl_usd": round(fees.gross_pnl_usd, 6),
            "fees_usd": round(fees.total_fees_usd, 6),
            "funding_usd": round(fees.funding_fee_usd, 6),
            "net_pnl_usd": round(fees.net_pnl_usd, 6),
            "pnl_r": round(r_metrics["pnl_r"], 6),
            "mfe_r": round(r_metrics["mfe_r"], 6),
            "mae_r": round(r_metrics["mae_r"], 6),
            "capture_ratio": round(r_metrics["capture_ratio"], 6),
            "exit_reason": exit_reason,
            "stop_slippage_bps": round(slippage_bps, 3),
            "fee_hurdle_multiple": position.get("fee_hurdle_multiple"),
            "hold_seconds": round(now - float(position["entry_time"]), 1),
        }
        trade_record["r_multiples"] = r_metrics

        # ------------------------------------------------ M8 State + Vault
        try:
            if state_engine is not None:
                await state_engine.update_post_trade_state(
                    position["instance_id"], fees.net_pnl_usd,
                    trade_id=position["trade_id"],
                )
        except Exception as exc:
            logger.warning("M8 update failed: %s", exc)

        # ---------------------------------------------------- Autopsy (Phase 3)
        trade_record["event_id"] = f"autopsy_{position['trade_id']}"
        autopsy_event = process_trade_autopsy(trade_record, self.config)
        trade_record["autopsy_zone"] = autopsy_event["autopsy_zone"]
        bus = get_event_bus()
        bus.publish_sync(bus.TOPIC_AUTOPSY, autopsy_event)
        bus.publish_sync(bus.TOPIC_TRADE_CLOSED, {
            **trade_record,
            "natural_key": f"trade_closed:{position['trade_id']}",
        })

        try:
            from app.core.duckdb_store import get_store

            get_store().upsert_trade(trade_record)
        except Exception as exc:
            logger.warning("persist closed failed: %s", exc)

        position.update({"status": "closed", "exit_price": fill_price,
                         "exit_reason": exit_reason, "net_pnl_usd": fees.net_pnl_usd})
        self.open_positions.pop(position["trade_id"], None)
        if self.realized_pnl_handler is not None:
            try:
                self.realized_pnl_handler(trade_record)
            except Exception as exc:
                logger.warning("realized pnl handler failed: %s", exc)

        icon = "💀" if liq else ("✅" if fees.net_pnl_usd >= 0 else "❌")
        bus.log(
            "trade",
            (f"{icon} PAPER CLOSE {direction} {position['symbol']} @ {fill_price:.2f} "
             f"[{exit_reason}] net {fees.net_pnl_usd:+.4f} USD → {autopsy_event['autopsy_zone']}"),
            category="TRADE", strategy_id=position["strategy_id"],
            payload={"trade_id": position["trade_id"], "net_pnl": fees.net_pnl_usd,
                     "zone": autopsy_event["autopsy_zone"]},
        )
        return position

    # ----------------------------------------------------------------- listing
    def positions_for(self, strategy_id: str) -> list:
        return [p for p in self.open_positions.values() if p["strategy_id"] == strategy_id]

    def all_positions(self) -> list:
        return list(self.open_positions.values())

    def cancel_all(self, strategy_id: Optional[str] = None,
                   current_prices: Optional[Dict[str, float]] = None) -> int:
        """EMERGENCY cancel-all: schließt alle offenen Paper-Positionen am Markt."""
        import asyncio

        to_close = [
            p for p in list(self.open_positions.values())
            if strategy_id is None or p["strategy_id"] == strategy_id
        ]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        for p in to_close:
            price = float((current_prices or {}).get(p["symbol"])
                          or p.get("entry_price") or 0.0)
            coro = self.close_position(None, p, price, "EMERGENCY_CANCEL")
            if loop is not None:
                loop.create_task(coro)
            else:
                try:
                    asyncio.run(coro)
                except Exception:
                    self.open_positions.pop(p["trade_id"], None)
        return len(to_close)


def _iso(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
