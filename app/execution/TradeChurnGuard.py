"""
=========================================================
Datei:      app/execution/TradeChurnGuard.py
Zweck:      Schutz vor Over-Trading, Micro-Chatter & Fee Drag
            180s Hold · 300s Cooldown · 12 Trades/Tag · 2.5x Fee Hurdle
Knoten:     Jaune (Carrera-Engine)
=========================================================
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger("app.execution.trade_churn_guard")


@dataclass
class ChurnGuardConfig:
    min_holding_seconds: int = 180
    cooldown_seconds: int = 300
    max_daily_trades: int = 12
    min_fee_hurdle_multiple: float = 2.5
    round_trip_fee_pct: float = 0.0010  # ~0.10% taker/taker Referenz


class TradeChurnGuard:
    def __init__(self, config: Optional[ChurnGuardConfig] = None):
        self.config = config or ChurnGuardConfig()
        self.last_close_timestamp: Dict[str, float] = {}
        self.last_entry_timestamp: Dict[str, float] = {}
        self.daily_trade_counts: Dict[str, Tuple[int, str]] = {}

    # -------------------------------------------------------------- validators
    def validate_entry_signal(self, instance_id: str, entry_price: float,
                              take_profit_price: float,
                              taker_fee_rate: float = 0.0005,
                              slippage_pct: float = 0.0,
                              now: Optional[float] = None) -> Tuple[bool, str]:
        """Vollständiger Entry-Check: Cooldown, Hold, Daily Cap, Fee Hurdle."""
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))

        # 1. Cooldown seit letztem Close dieser Instanz
        last_close = self.last_close_timestamp.get(instance_id)
        if last_close is not None and (now - last_close) < self.config.cooldown_seconds:
            remaining = int(self.config.cooldown_seconds - (now - last_close))
            return False, f"COOLDOWN REJECT: {remaining}s Restcooldown nach letztem Close."

        # 2. Daily Trade Cap
        count, count_day = self.daily_trade_counts.get(instance_id, (0, day))
        if count_day != day:
            count = 0
        if count >= self.config.max_daily_trades:
            return False, (f"DAILY CAP REJECT: {count}/{self.config.max_daily_trades} "
                           f"Trades bereits heute.")

        # 3. Fee Hurdle: TP-Distanz muss >= 2.5x round-trip Fee+Slippage
        if entry_price > 0 and take_profit_price:
            tp_distance_pct = abs(take_profit_price - entry_price) / entry_price
            round_trip_cost_pct = 2.0 * taker_fee_rate + slippage_pct
            hurdle = round_trip_cost_pct * self.config.min_fee_hurdle_multiple
            if tp_distance_pct < hurdle:
                return False, (
                    f"FEE HURDLE REJECT: TP-Distanz {tp_distance_pct * 100:.4f}% < "
                    f"{self.config.min_fee_hurdle_multiple}x Kosten {hurdle * 100:.4f}%."
                )
        return True, "OK"

    def check_min_holding(self, instance_id: str, entry_ts: float,
                          now: Optional[float] = None) -> Tuple[bool, str]:
        now = now or time.time()
        held = now - entry_ts
        if held < self.config.min_holding_seconds:
            return False, f"HOLD REJECT: nur {int(held)}s gehalten (< {self.config.min_holding_seconds}s)."
        return True, "OK"

    # ---------------------------------------------------------------- recording
    def record_entry(self, instance_id: str, now: Optional[float] = None) -> None:
        now = now or time.time()
        self.last_entry_timestamp[instance_id] = now
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        count, count_day = self.daily_trade_counts.get(instance_id, (0, day))
        if count_day != day:
            count = 0
        self.daily_trade_counts[instance_id] = (count + 1, day)

    def record_close(self, instance_id: str, now: Optional[float] = None) -> None:
        self.last_close_timestamp[instance_id] = now or time.time()

    def daily_count(self, instance_id: str) -> int:
        count, count_day = self.daily_trade_counts.get(instance_id, (0, ""))
        if count_day != time.strftime("%Y-%m-%d", time.gmtime()):
            return 0
        return count
