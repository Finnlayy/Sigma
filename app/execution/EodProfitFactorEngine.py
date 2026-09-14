"""
=========================================================
Datei:      app/execution/EodProfitFactorEngine.py
Zweck:      EOD Profit-Factor-Engine (Blueprint v1.2.0 'Still Missing' → umgesetzt)
            Aggregiert Tages-PF aus geschlossenen Trades, schreibt daily_pnl
            und treibt das M8 EOD-Gate (3 Tage PF<1 → THROTTLED, 7 → QUARANTINED).
Knoten:     Jaune (Carrera-Engine)
=========================================================
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Dict, List, Optional

from app.core.duckdb_store import DuckDBStore

logger = logging.getLogger("app.execution.eod_engine")

INF_PF = 999999.0


def compute_daily_profit_factor(closed_trades: List[Dict[str, Any]],
                                day_label: str) -> Dict[str, Any]:
    """PF = Sum(Winners) / |Sum(Losser)|. Keine Trades -> has_trades=False."""
    gross_win = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    net = 0.0
    n = 0
    for t in closed_trades:
        if _day_of(t) != day_label:
            continue
        n += 1
        net += float(t.get("net_pnl_usd") or 0.0)
        if float(t.get("net_pnl_usd") or 0.0) >= 0:
            gross_win += float(t.get("net_pnl_usd") or 0.0)
            wins += 1
        else:
            gross_loss += abs(float(t.get("net_pnl_usd")))
            losses += 1
    if gross_loss > 0:
        pf = gross_win / gross_loss
    else:
        pf = INF_PF if gross_win > 0 else 1.0
    return {
        "day": day_label,
        "gross_win": round(gross_win, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(pf, 6),
        "net_pnl_usd": round(net, 6),
        "trades_count": n,
        "wins": wins,
        "losses": losses,
        "has_trades": n > 0,
    }


def _day_of(trade: Dict[str, Any]) -> str:
    ts = trade.get("exit_time") or trade.get("entry_time") or ""
    return str(ts)[:10]


class EodProfitFactorEngine:
    def __init__(self, store: DuckDBStore, state_engine):
        self.store = store
        self.state_engine = state_engine

    async def run_for_day(self, day_label: Optional[str] = None) -> List[Dict[str, Any]]:
        """EOD-Lauf über alle registrierten Strategien (00:05 UTC Cron / m8-ctl)."""
        day_label = day_label or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        states = await self.state_engine.scan_states()
        results = []
        for instance_id, _state in states.items():
            # Nur echte Strategie-Instanzen (kein halt-key)
            row = self.store._one(
                "SELECT id, name FROM strategies WHERE id = ? OR id LIKE ?",
                [instance_id, f"%{instance_id}"],
            )
            sid = row["id"] if row else instance_id
            trades = self.store.trades(strategy_id=sid, status="closed", limit=5000)
            daily = compute_daily_profit_factor(trades, day_label)
            # Idempotenz: nur einmal pro Tag pro Strategie
            last = self.store._one(
                "SELECT day FROM daily_pnl WHERE strategy_id = ?", [instance_id]
            )
            self.store.upsert_daily_pnl({**daily, "strategy_id": instance_id})
            try:
                state = await self.state_engine.update_eod_profit_factor(
                    instance_id,
                    daily["profit_factor"] if daily["has_trades"] else None,
                    daily["trades_count"],
                    day_label=day_label,
                )
            except Exception as exc:
                logger.warning("EOD gate failed for %s: %s", instance_id, exc)
                state = None
            results.append({
                "instance_id": instance_id,
                "day": day_label,
                **daily,
                "new_status": state.status if state else None,
                "consecutive_low_pf_days": state.consecutive_low_pf_days if state else None,
            })
            if daily["has_trades"]:
                logger.info(
                    "EOD %s: %s PF=%.3f (%d trades) → %s",
                    day_label, instance_id, daily["profit_factor"],
                    daily["trades_count"],
                    state.status if state else "n/a",
                )
        return results
