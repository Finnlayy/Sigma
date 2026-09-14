"""
=========================================================
Datei:      tests/test_dca_ladder.py
Zweck:      MP-02 Micro-DCA-Ladder — Karte §5: 8x0,15 % + 1,15x Vol
            mit DEFAULT_STEP_MULT=1.10 (kanonisch avg≈0.9904 / TP≈1.0053);
            Prompt-Referenz 0,9899/1,0047 bleibt innerhalb abs=0.001.
            3 %-Range/6 Stufen ~0,3 %-Step, enge Leiter reject,
            Avg sinkt, TP auf Avg, TTL 2h+1min expired, Guard aus MP-01.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template) / Jaune (Contract)
=========================================================
"""
from __future__ import annotations

import pytest

from sigma.execution.risk_guards import assert_grid_depth
from sigma.strategies.dca_ladder import (
    DEFAULT_N_SAFETY,
    DEFAULT_STEP_MULT,
    DEFAULT_STEP_PCT,
    DEFAULT_TP_PCT,
    DEFAULT_VOLUME_MULT,
    LADDER_TTL_SECONDS,
    MIN_MEME_DEPTH,
    RANGE_FACTOR,
    SPREAD_FEE_FLOOR_PCT,
    build_ladder,
    dynamic_step_from_range,
    take_profit_price,
    ttl_expired,
    validate_ladder,
)


MEME_SPEC = {"symbol": "SIRENPERP", "is_meme": True}

# Canonical §13.2 ladder under documented recurrence + DEFAULT_STEP_MULT=1.10
# gap_k = 0.0015 * 1.10**(k-1); volume_i = 1.15**i (entry included).
CANONICAL_AVG = 0.9904005349
CANONICAL_TP = CANONICAL_AVG * 1.015


def test_named_constants_match_mp02_contract():
    assert DEFAULT_STEP_PCT == 0.002
    assert DEFAULT_STEP_MULT == 1.10
    assert DEFAULT_VOLUME_MULT == 1.15
    assert DEFAULT_N_SAFETY == 6
    assert DEFAULT_TP_PCT == 0.015
    assert LADDER_TTL_SECONDS == 7200
    assert SPREAD_FEE_FLOOR_PCT == 0.001
    assert RANGE_FACTOR == 0.618
    assert MIN_MEME_DEPTH == 0.06


def test_example_8_rungs_015pct_vol115_reproduces_reference():
    # Entry 1.00, Step 0,15 %, 8 Stufen, Vol 1,15, step_mult DEFAULT 1.10.
    ladder = build_ladder(
        1.00, side="buy", n_safety=8, step_pct=0.0015,
        base_margin_pct=0.01, volume_mult=1.15,
    )
    assert ladder.step_mult == DEFAULT_STEP_MULT
    assert len(ladder.rungs) == 9
    avg = ladder.avg_price
    # Formula contract within 1e-4
    assert avg == pytest.approx(CANONICAL_AVG, abs=1e-4)
    assert ladder.tp_price == pytest.approx(CANONICAL_TP, abs=1e-4)
    # Prompt §13.2 reference numbers (spreadsheet rounding) within 0.001
    assert avg == pytest.approx(0.9899, abs=0.001)
    assert ladder.tp_price == pytest.approx(1.0047, abs=0.001)
    assert ladder.tp_price == pytest.approx(avg * (1.0 + DEFAULT_TP_PCT), rel=1e-9)
    assert ladder.rungs[1].volume_mult_applied == pytest.approx(1.15)
    assert ladder.rungs[1].volume == pytest.approx(1.15)
    assert ladder.rungs[8].volume_mult_applied == pytest.approx(1.15 ** 8)
    assert ladder.rungs[1].cumulative_depth_pct == pytest.approx(0.0015)
    assert ladder.rungs[1].margin_share == pytest.approx(0.01 * 1.15)
    assert ladder.accepted is True
    assert ladder.reject_reason == ""


def test_dynamic_step_from_range_3pct_6_rungs_approx_03pct():
    step = dynamic_step_from_range(
        high_2h=1.03, low_2h=1.00, current_price=1.00, n_safety=6
    )
    # (0.03 * 0.618) / 6 = 0.00309 ~ 0,3 %
    assert step == pytest.approx(0.00309, abs=1e-4)
    assert step > 0.003 and step < 0.0032


def test_tight_ladder_rejected_deep_ladder_accepted_via_mp01_guard():
    # 8 x 0,15 % (~1,2 % Tiefe) -> reject (Guard aus MP-01 importiert)
    tight = build_ladder(
        1.00, side="buy", n_safety=8, step_pct=0.0015, step_mult=1.0,
        base_margin_pct=0.01,
    )
    assert tight.total_depth_pct == pytest.approx(0.012)
    verdict = validate_ladder(tight, MEME_SPEC)
    assert isinstance(verdict, type(tight))
    assert verdict.accepted is False
    assert verdict.reject_reason == "depth_rejected"
    assert verdict.rungs == tight.rungs  # never invent a passing ladder
    assert not assert_grid_depth(tight.total_depth_pct, MEME_SPEC).ok

    # Range-basiertes Raster: 12 %-Range / 6 Stufen -> Tiefe ~7,4 % -> ok
    step = dynamic_step_from_range(
        high_2h=1.12, low_2h=1.00, current_price=1.00, n_safety=6
    )
    deep = build_ladder(
        1.00, side="buy", n_safety=6, step_pct=step, step_mult=1.0,
        base_margin_pct=0.01,
    )
    assert deep.total_depth_pct >= MIN_MEME_DEPTH
    assert validate_ladder(deep, MEME_SPEC).accepted is True
    assert deep.total_depth_pct == pytest.approx(0.12 * RANGE_FACTOR, rel=1e-6)


def test_average_fill_price_sinks_with_each_fill_and_tp_uses_avg():
    ladder = build_ladder(
        1.00, side="buy", n_safety=8, step_pct=0.0015, step_mult=1.0,
        base_margin_pct=0.01, volume_mult=1.15,
    )
    avgs = [ladder.average_fill_price(list(range(k + 1))) for k in range(len(ladder.rungs))]
    assert all(avgs[k + 1] < avgs[k] for k in range(len(avgs) - 1))
    tp = take_profit_price(avgs[-1], "buy", tp_pct=0.015)
    assert tp == pytest.approx(avgs[-1] * 1.015)
    assert tp < take_profit_price(1.00, "buy", tp_pct=0.015)
    short = build_ladder(
        1.00, side="sell", n_safety=4, step_pct=0.002, step_mult=1.0,
        base_margin_pct=0.01,
    )
    assert short.avg_price > 1.00
    assert short.tp_price < 1.00


def test_ttl_expired_after_2h1min():
    opened = 1_700_000_000.0
    assert LADDER_TTL_SECONDS == 7200
    assert ttl_expired(opened, opened + 7200 + 60) is True
    assert ttl_expired(opened, opened + 7199) is False
    ladder = build_ladder(1.00, side="buy", n_safety=4, step_pct=0.002,
                          base_margin_pct=0.01)
    assert ladder.ttl_expired(opened, opened + 7200 + 1) is True
    intent = ladder.flat_intent("SIRENPERP")
    assert intent.action == "FLAT"
    assert intent.execution_mode == "kraken_paper"
    assert intent.details["reason"] == "ttl_expired"


def test_first_step_spread_fee_floor_and_to_dict():
    ladder = build_ladder(
        1.00, side="buy", n_safety=4, step_pct=0.0008, step_mult=1.0,
        base_margin_pct=0.01,
    )
    verdict = validate_ladder(ladder, {"is_meme": False}, spread_pct=0.0)
    assert verdict.accepted is False
    assert verdict.reject_reason == "first_step_below_spread_fee_floor"
    assert verdict.first_step_pct == pytest.approx(0.0008)
    ok_ladder = build_ladder(
        1.00, side="buy", n_safety=6, step_pct=0.012, step_mult=1.0,
        base_margin_pct=0.01,
    )
    assert validate_ladder(ok_ladder, MEME_SPEC).accepted is True
    d = ladder.to_dict()
    assert len(d["rungs"]) == 5
    assert d["rungs"][0]["price"] == 1.00
    assert d["rungs"][1]["volume_mult_applied"] == pytest.approx(1.15)
    assert "avg_price" in d and "tp_price" in d and "accepted" in d
    assert "total_depth_pct" in d and "take_profit" in d
