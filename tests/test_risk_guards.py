"""
=========================================================
Datei:      tests/test_risk_guards.py
Zweck:      MP-01 Hard Risk Guards — Vertraege laut Karte §5:
            Long/Short-Stop mit Liq-Puffer, 8 enge Sprossen reject,
            6 %-Tiefe ok, offene BTC-Bar ignorieren, 4,3 % HITL /
            12 % nicht, 29/31 min Cooldown, Fee-BE 100,05/99,95,
            Wick-Guard beta=3,5 + 10x reject.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Execution-Contract) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

import pytest

from sigma.execution.base_bridge import ExecutionReceipt
from sigma.execution.risk_guards import (
    assert_grid_depth,
    assert_leverage_for_depth,
    btc_macro_breach,
    cooldown_active,
    fee_covered_stop,
    grid_total_depth_pct,
    hard_stop_distance,
    liq_outside_wick_zone,
    liquidation_proximity_pct,
    wick_buffer_pct,
)


def bar(close: float, *, ts: int = 0, closed: bool = True) -> dict:
    return {"ts": ts, "o": close, "h": close * 1.001, "l": close * 0.999,
            "c": close, "v": 100.0, "is_closed": closed}


# ------------------------------------------------------------- hard stop ---

def test_hard_stop_long_sits_between_entry_and_liq():
    entry, liq = 100.0, 96.0  # Liq bei 0.96 x Entry
    res = hard_stop_distance(entry, liq, "long")
    # Stop = Liq * 1.005 -> ueber Liq, unter Entry
    assert res.stop_price == pytest.approx(96.0 * 1.005)
    assert res.stop_price < entry
    assert res.stop_price > liq
    assert res.side == "long"


def test_hard_stop_short_mirrors_long():
    entry, liq = 100.0, 104.0
    res = hard_stop_distance(entry, liq, "short")
    assert res.stop_price == pytest.approx(104.0 * 0.995)
    assert res.stop_price > entry
    assert res.stop_price < liq
    assert res.side == "short"


def test_hard_stop_rejects_bad_side_or_prices():
    with pytest.raises(ValueError):
        hard_stop_distance(100.0, 96.0, "diagonal")
    with pytest.raises(ValueError):
        hard_stop_distance(0.0, 96.0, "long")


# ------------------------------------------------------- grid depth gate ---

def test_grid_depth_eight_tight_rungs_rejected_for_meme_perp():
    anchor = 100.0
    # 8 Sprossen a 0,15 % Abstand -> Gesamttiefe ~1,2 % < 6 % -> reject
    ladder = [anchor * (1 - 0.0015 * i) for i in range(1, 9)]
    depth = grid_total_depth_pct(ladder, anchor, "long")
    assert depth < 0.06
    verdict = assert_grid_depth(depth, {"symbol": "SIRENPERP", "is_meme": True})
    assert not verdict.ok
    assert verdict.reason == "grid_too_shallow"


def test_grid_depth_six_percent_passes():
    anchor = 100.0
    ladder = [anchor * (1 - 0.015 * i) for i in range(1, 5)]  # ~6 % Tiefe
    depth = grid_total_depth_pct(ladder, anchor, "long")
    assert depth >= 0.06
    verdict = assert_grid_depth(depth, {"symbol": "MEMEPERP", "is_meme": True})
    assert verdict.ok
    assert verdict.depth_pct == pytest.approx(depth)


def test_grid_depth_short_side_and_non_meme():
    anchor = 100.0
    ladder = [anchor * (1 + 0.01 * i) for i in range(1, 4)]  # bis +3 %
    assert grid_total_depth_pct(ladder, anchor, "short") == pytest.approx(0.03)
    # Non-Meme: kein Mindest-Tiefen-Guard
    verdict = assert_grid_depth(0.011, {"symbol": "BTC/USD", "is_meme": False})
    assert verdict.ok
    assert verdict.reason == "no_meme_perp"


# ------------------------------------------------------- btc macro gate ---

def test_btc_breach_closes_macro_gate_on_closed_bars():
    bars = [bar(101.0, ts=1), bar(99.5, ts=2), bar(100.0, ts=3)]
    verdict = btc_macro_breach(bars, support_price=100.0, side="long")
    assert verdict.macro_gate_closed is True
    assert verdict.breached is True
    assert verdict.closed_bars_used == 3


def test_btc_open_last_bar_is_ignored():
    # letzte Bar (offen, is_closed=False) waere unter Support -> ignorieren
    bars = [bar(101.0, ts=1), bar(102.0, ts=2), bar(99.5, ts=3, closed=False)]
    verdict = btc_macro_breach(bars, support_price=100.0, side="long")
    assert verdict.macro_gate_closed is False
    assert verdict.closed_bars_used == 2


def test_btc_no_closed_bars_fails_closed():
    bars = [bar(99.0, ts=1, closed=False)]
    verdict = btc_macro_breach(bars, support_price=100.0, side="long")
    assert verdict.macro_gate_closed is True  # fail-closed
    assert verdict.reason == "no_closed_bars_fail_closed"


def test_btc_short_side_breach_above_support():
    bars = [bar(100.0, ts=1), bar(100.5, ts=2)]
    verdict = btc_macro_breach(bars, support_price=100.0, side="short")
    assert verdict.macro_gate_closed is True


# --------------------------------------------------- liq proximity / HITL ---

def test_liq_proximity_4_3_percent_escalates_to_hitl():
    prox = liquidation_proximity_pct(mark_price=100.0, liq_price=95.7, side="long")
    assert prox.distance_pct == pytest.approx(0.043)
    assert prox.needs_hitl is True


def test_liq_proximity_12_percent_no_hitl():
    prox = liquidation_proximity_pct(mark_price=100.0, liq_price=88.0, side="long")
    assert prox.distance_pct == pytest.approx(0.12)
    assert prox.needs_hitl is False


def test_liq_proximity_short_side():
    prox = liquidation_proximity_pct(mark_price=100.0, liq_price=104.3, side="short")
    assert prox.distance_pct == pytest.approx(0.043)
    assert prox.needs_hitl is True


# ------------------------------------------------------------- cooldown ---

def test_cooldown_29_minutes_blocks_31_minutes_allows():
    last_exit = 1_700_000_000.0
    assert cooldown_active(last_exit, last_exit + 29 * 60) is True
    assert cooldown_active(last_exit, last_exit + 31 * 60) is False
    # Default 1800s-Grenze
    assert cooldown_active(last_exit, last_exit + 1800) is False


# ----------------------------------------------------- fee covered BE ---

def test_fee_covered_stop_values():
    assert fee_covered_stop(100.0, "long") == pytest.approx(100.05)
    assert fee_covered_stop(100.0, "short") == pytest.approx(99.95)
    # sicher: long ueber Entry, short unter Entry
    assert fee_covered_stop(100.0, "long") > 100.0
    assert fee_covered_stop(100.0, "short") < 100.0
    with pytest.raises(ValueError):
        fee_covered_stop(100.0, "sideways")


# ------------------------------------------------------- wick zone guard ---

def test_wick_buffer_pct_scales_with_beta():
    assert wick_buffer_pct(beta=3.5, expected_btc_wick_pct=0.01) == pytest.approx(
        0.035 + 0.01
    )
    assert wick_buffer_pct(beta=2.8, expected_btc_wick_pct=0.01, extra_pct=0.0) == \
        pytest.approx(0.028)


def test_leverage_10x_rejected_for_8pct_grid_beta35():
    # 8 % Tiefe + 3,5 % Wick + 1 % Puffer = 12,5 % noetig; 10x -> 10 % -> reject
    verdict = assert_leverage_for_depth(
        beta=3.5, grid_depth_pct=0.08, leverage=10.0, expected_btc_wick_pct=0.01
    )
    assert not verdict.ok
    assert verdict.reason == "liq_inside_wick_zone"


def test_leverage_low_enough_passes():
    # 5x -> Liq-Abstand 20 % >= 12,5 % -> ok
    verdict = assert_leverage_for_depth(
        beta=3.5, grid_depth_pct=0.08, leverage=5.0, expected_btc_wick_pct=0.01
    )
    assert verdict.ok
    assert verdict.reason == "leverage_ok"


def test_liq_outside_wick_zone_long_and_short():
    # long: Liq ueber dem erwarteten Docht-Tief -> NICHT ausserhalb -> False
    verdict = liq_outside_wick_zone(
        liquidation_price=97.0, wick_low_price=96.0, side="long"
    )
    assert not verdict.ok
    assert verdict.reason == "liq_inside_wick_zone"
    # long: Liq unter Docht-Tief -> ok
    assert liq_outside_wick_zone(95.0, 96.0, "long").ok
    # short: Liq unter Docht-Hoch -> nicht ok; ueber Docht-Hoch -> ok
    assert not liq_outside_wick_zone(103.0, 104.0, "short").ok
    assert liq_outside_wick_zone(105.0, 104.0, "short").ok


# --------------------------------------------------- receipt metadata ---

def test_receipt_carries_passive_requires_hitl():
    receipt = ExecutionReceipt(ok=True, accepted=True, requires_hitl=True)
    d = receipt.to_dict()
    assert d["requires_hitl"] is True
    assert receipt.requires_hitl is True  # nur Metadatum, keine Freigabe
