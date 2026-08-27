"""
=========================================================
Datei:      app/execution/AutopsyProcessor.py (schema 1.2.0)
Zweck:      R-Multiple-/MFE-MAE-Berechnung, Slippage-Trigger vs. Fill,
            5-Zonen-Klassifikation & idempotentes TradeAutopsyEvent
Knoten:     Jaune (Carrera-Engine)
=========================================================

Zone-Tabelle (frozen v1.2.0, STOP_LOSS VOR BAD):
  GOOD          Winner · capture_ratio >= 0.55
  WATCH         Winner · capture_ratio <  0.55
  CLEAN_LOSS    Loser · STOP_LOSS (v1.2.0-Präzedenz vor BAD)
  BAD           Loser · mfe_r > 0.5
  NEUTRAL_LOSS  Loser · sonst

Delta v1.6.4 (opt-in, config.autopsy_order='v1.6.4'): BAD vor STOP_LOSS.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

CAPTURE_RATIO_GOOD = 0.55
BAD_MFE_R_THRESHOLD = 0.5


def calculate_r_multiples(pnl_pct: float, mfe_pct: float, mae_pct: float,
                          stop_distance_pct: float) -> Dict[str, float]:
    """
    pnl_r = pnl_pct / stop_distance_pct
    mfe_r = mfe_pct / stop_distance_pct
    mae_r = mae_pct / stop_distance_pct
    capture_ratio = pnl_r / mfe_r   (nur wenn beide > 0)
    """
    if stop_distance_pct is None or stop_distance_pct <= 0:
        raise ValueError("stop_distance_pct muss > 0 sein")
    pnl_r = pnl_pct / stop_distance_pct
    mfe_r = mfe_pct / stop_distance_pct
    mae_r = mae_pct / stop_distance_pct
    capture_ratio = (pnl_r / mfe_r) if (pnl_r > 0 and mfe_r > 0) else 0.0
    return {
        "pnl_r": pnl_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "capture_ratio": capture_ratio,
    }


def calculate_stop_slippage(trigger_price: float, fill_price: float,
                            exit_reason: str, threshold_bps: float = 15.0,
                            direction: str = "LONG") -> bool:
    """True, wenn der Stop-Fill schlechter als Trigger + threshold_bps auslöst."""
    if exit_reason.upper() != "STOP_LOSS":
        return False
    if trigger_price <= 0 or fill_price <= 0:
        return False
    # LONG: Fill unter Trigger = Slippage. SHORT: Fill über Trigger = Slippage.
    if direction.upper() == "LONG":
        adverse_bps = (trigger_price - fill_price) / trigger_price * 10_000.0
    else:
        adverse_bps = (fill_price - trigger_price) / trigger_price * 10_000.0
    return adverse_bps > threshold_bps


def stop_slippage_bps(trigger_price: float, fill_price: float,
                      direction: str = "LONG") -> float:
    if trigger_price <= 0 or fill_price <= 0:
        return 0.0
    if direction.upper() == "LONG":
        return max(0.0, (trigger_price - fill_price) / trigger_price * 10_000.0)
    return max(0.0, (fill_price - trigger_price) / trigger_price * 10_000.0)


def classify_autopsy_zone(pnl_r: float, mfe_r: float, exit_reason: str,
                          capture_ratio: float, order: str = "v1.2.0") -> str:
    """5-Zonen-Klassifikation. order='v1.2.0' (frozen) | 'v1.6.4' (Delta)."""
    if pnl_r > 0:
        return "GOOD" if capture_ratio >= CAPTURE_RATIO_GOOD else "WATCH"
    if order == "v1.6.4":
        # Skeleton-Delta: BAD hat Präzedenz
        if mfe_r >= BAD_MFE_R_THRESHOLD:
            return "BAD"
        if exit_reason.upper() == "STOP_LOSS":
            return "CLEAN_LOSS"
        return "NEUTRAL_LOSS"
    # frozen v1.2.0: STOP_LOSS hat Präzedenz
    if exit_reason.upper() == "STOP_LOSS":
        return "CLEAN_LOSS"
    if mfe_r >= BAD_MFE_R_THRESHOLD:
        return "BAD"
    return "NEUTRAL_LOSS"


def process_trade_autopsy(trade: Dict[str, Any], config=None) -> Dict[str, Any]:
    """Erzeugt das idempotente TradeAutopsyEvent (Phase 3 LAN events)."""
    order = getattr(config, "autopsy_order", "v1.2.0") or "v1.2.0"
    metrics = trade.get("r_multiples") or {}
    pnl_r = float(metrics.get("pnl_r", 0.0))
    mfe_r = float(metrics.get("mfe_r", 0.0))
    capture_ratio = float(metrics.get("capture_ratio", 0.0))
    exit_reason = str(trade.get("exit_reason") or "TAKE_PROFIT")
    zone = classify_autopsy_zone(pnl_r, mfe_r, exit_reason, capture_ratio, order)

    event_id = trade.get("event_id") or f"autopsy_{trade.get('trade_id', uuid.uuid4().hex)}"
    return {
        "event_id": event_id,
        "natural_key": f"autopsy:{trade.get('trade_id')}",
        "trade_id": trade.get("trade_id"),
        "instance_id": trade.get("instance_id"),
        "strategy_id": trade.get("strategy_id"),
        "strategy_name": trade.get("strategy_name"),
        "symbol": trade.get("symbol"),
        "execution_mode": trade.get("execution_mode"),
        "direction": trade.get("direction"),
        "exit_reason": exit_reason,
        "net_pnl_usd": trade.get("net_pnl_usd"),
        "gross_pnl_usd": trade.get("gross_pnl_usd"),
        "fees_usd": trade.get("fees_usd"),
        "pnl_r": round(pnl_r, 6),
        "mfe_r": round(mfe_r, 6),
        "mae_r": round(float(metrics.get("mae_r", 0.0)), 6),
        "capture_ratio": round(capture_ratio, 6),
        "stop_slippage_bps": round(float(trade.get("stop_slippage_bps") or 0.0), 3),
        "autopsy_zone": zone,
        "hold_seconds": trade.get("hold_seconds"),
        "published_at": time.time(),
    }
