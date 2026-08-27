"""
=========================================================
Datei:      tests/test_autopsy_zones_30.py
Zweck:      Phase-1-Acceptance: 30-Trade-Pytest, 5 Zonen, float tol < 1e-4
Knoten:     Jaune (Carrera-Engine) / Test Suite
=========================================================
"""
import math

import pytest

from app.execution.AutopsyProcessor import (
    calculate_r_multiples,
    classify_autopsy_zone,
    process_trade_autopsy,
    stop_slippage_bps,
)

TOL = 1e-4

# 30 deterministische Trades: alle 5 Zonen (frozen v1.2.0-Order: STOP_LOSS vor BAD)
TRADES_30 = [
    # --- 10x GOOD (Winner, capture >= 0.55)
    {"pnl_r": 2.10, "mfe_r": 3.00, "exit": "TAKE_PROFIT", "cr": 0.70},
    {"pnl_r": 1.45, "mfe_r": 2.00, "exit": "TAKE_PROFIT", "cr": 0.72},
    {"pnl_r": 0.80, "mfe_r": 1.40, "exit": "TAKE_PROFIT", "cr": 0.57},
    {"pnl_r": 3.20, "mfe_r": 4.10, "exit": "TAKE_PROFIT", "cr": 0.78},
    {"pnl_r": 1.05, "mfe_r": 1.90, "exit": "TAKE_PROFIT", "cr": 0.55},  # exakt Grenze
    {"pnl_r": 0.60, "mfe_r": 1.00, "exit": "TRAILING_STOP", "cr": 0.60},
    {"pnl_r": 2.55, "mfe_r": 2.90, "exit": "TAKE_PROFIT", "cr": 0.88},
    {"pnl_r": 1.90, "mfe_r": 3.50, "exit": "TAKE_PROFIT", "cr": 0.54 + 0.02},
    {"pnl_r": 4.00, "mfe_r": 5.00, "exit": "TAKE_PROFIT", "cr": 0.80},
    {"pnl_r": 0.70, "mfe_r": 1.20, "exit": "TRAILING_STOP", "cr": 0.58},
    # --- 8x WATCH (Winner, capture < 0.55)
    {"pnl_r": 0.90, "mfe_r": 3.00, "exit": "TAKE_PROFIT", "cr": 0.30},
    {"pnl_r": 1.20, "mfe_r": 4.00, "exit": "TRAILING_STOP", "cr": 0.30},
    {"pnl_r": 0.40, "mfe_r": 0.80, "exit": "TAKE_PROFIT", "cr": 0.50},
    {"pnl_r": 1.50, "mfe_r": 5.00, "exit": "TIME_EXIT", "cr": 0.30},
    {"pnl_r": 0.20, "mfe_r": 0.40, "exit": "TAKE_PROFIT", "cr": 0.50},
    {"pnl_r": 2.20, "mfe_r": 6.00, "exit": "TRAILING_STOP", "cr": 0.36},
    {"pnl_r": 0.55, "mfe_r": 1.10, "exit": "TAKE_PROFIT", "cr": 0.50},
    {"pnl_r": 1.10, "mfe_r": 3.30, "exit": "TIME_EXIT", "cr": 0.33},
    # --- 6x CLEAN_LOSS (Loser, STOP_LOSS — v1.2.0: auch bei hohem MFE!)
    {"pnl_r": -1.00, "mfe_r": 0.85, "exit": "STOP_LOSS", "cr": 0.0},
    {"pnl_r": -0.50, "mfe_r": 0.20, "exit": "STOP_LOSS", "cr": 0.0},
    {"pnl_r": -1.00, "mfe_r": 0.60, "exit": "STOP_LOSS", "cr": 0.0},
    {"pnl_r": -0.90, "mfe_r": 0.05, "exit": "STOP_LOSS", "cr": 0.0},
    {"pnl_r": -1.00, "mfe_r": 0.99, "exit": "STOP_LOSS", "cr": 0.0},
    {"pnl_r": -0.70, "mfe_r": 0.30, "exit": "STOP_LOSS", "cr": 0.0},
    # --- 3x BAD (Loser, mfe_r > 0.5, KEIN Stop-Loss-Exit)
    {"pnl_r": -0.40, "mfe_r": 0.90, "exit": "TIME_EXIT", "cr": 0.0},
    {"pnl_r": -0.80, "mfe_r": 1.20, "exit": "TRAILING_STOP", "cr": 0.0},
    {"pnl_r": -0.20, "mfe_r": 0.51, "exit": "EMERGENCY_CANCEL", "cr": 0.0},
    # --- 3x NEUTRAL_LOSS (Loser, sonst)
    {"pnl_r": -0.30, "mfe_r": 0.20, "exit": "TIME_EXIT", "cr": 0.0},
    {"pnl_r": -0.95, "mfe_r": 0.10, "exit": "EMERGENCY_CANCEL", "cr": 0.0},
    {"pnl_r": -0.55, "mfe_r": 0.40, "exit": "TRAILING_STOP", "cr": 0.0},
]
assert len(TRADES_30) == 30


def test_30_trade_zone_distribution():
    """Phase 1: alle 5 Zonen müssen in den 30 Trades erscheinen."""
    seen = set()
    for i, t in enumerate(TRADES_30):
        zone = classify_autopsy_zone(t["pnl_r"], t["mfe_r"], t["exit"], t["cr"])
        seen.add(zone)
        _expect_zone(i, t, zone)
    assert seen == {"GOOD", "WATCH", "CLEAN_LOSS", "BAD", "NEUTRAL_LOSS"}


def _expect_zone(i, t, zone):
    if t["pnl_r"] > 0:
        expected = "GOOD" if t["cr"] >= 0.55 else "WATCH"
    elif t["exit"] == "STOP_LOSS":
        expected = "CLEAN_LOSS"  # frozen v1.2.0-Präzedenz
    elif t["mfe_r"] > 0.5:
        expected = "BAD"
    else:
        expected = "NEUTRAL_LOSS"
    assert zone == expected, f"Trade {i}: erwartet {expected}, erhalten {zone}"


def test_r_multiple_float_tolerance_under_1e_4():
    for i, t in enumerate(TRADES_30):
        stop = 0.02
        m = calculate_r_multiples(pnl_pct=t["pnl_r"] * stop,
                                  mfe_pct=t["mfe_r"] * stop,
                                  mae_pct=-0.005,
                                  stop_distance_pct=stop)
        assert abs(m["pnl_r"] - t["pnl_r"]) < TOL, f"Trade {i} pnl_r"
        assert abs(m["mfe_r"] - t["mfe_r"]) < TOL, f"Trade {i} mfe_r"
        if t["pnl_r"] > 0 and t["mfe_r"] > 0:
            expected_cr = t["pnl_r"] / t["mfe_r"]
            assert abs(m["capture_ratio"] - expected_cr) < TOL, f"Trade {i} capture"


def test_v164_delta_order_explizit():
    """Delta v1.6.4 (opt-in): BAD vor STOP_LOSS."""
    zone = classify_autopsy_zone(-1.0, 0.85, "STOP_LOSS", 0.0, order="v1.6.4")
    assert zone == "BAD"
    # frozen Default bleibt v1.2.0
    assert classify_autopsy_zone(-1.0, 0.85, "STOP_LOSS", 0.0) == "CLEAN_LOSS"


def test_idempotent_autopsy_event():
    trade = {
        "trade_id": "trd_test_42",
        "instance_id": "TEST__BTC-USDT__15m__PAPER",
        "strategy_id": "TEST",
        "strategy_name": "Test",
        "symbol": "BTC/USD",
        "execution_mode": "paper",
        "direction": "LONG",
        "exit_reason": "STOP_LOSS",
        "net_pnl_usd": -12.5,
        "gross_pnl_usd": -12.0,
        "fees_usd": 0.5,
        "stop_slippage_bps": 8.2,
        "hold_seconds": 940.5,
        "r_multiples": calculate_r_multiples(-0.02, 0.004, -0.02, 0.02),
    }
    e1 = process_trade_autopsy(trade)
    e2 = process_trade_autopsy({**trade, "event_id": e1["event_id"]})
    assert e1["event_id"] == e2["event_id"]
    assert e1["autopsy_zone"] == "CLEAN_LOSS"
    assert e1["natural_key"] == f"autopsy:{trade['trade_id']}"


def test_stop_slippage_bps_math():
    assert abs(stop_slippage_bps(49000.0, 48800.0, "LONG") - (200.0 / 49000.0 * 10000.0)) < TOL
    assert stop_slippage_bps(49000.0, 49100.0, "SHORT") == pytest.approx(100.0 / 49000.0 * 10000.0, abs=TOL)
