"""
=========================================================
Datei:      tests/test_dca_ladder.py
Zweck:      MP-02 Micro-DCA-Ladder — Karte §5: 8x0,15 % + 1,15x Vol
            reproduzieren (Referenz 0,9899/1,0047 aus Prompt §13.2),
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
    LADDER_TTL_SECONDS,
    MIN_MEME_DEPTH,
    average_fill_price,
    build_ladder,
    dynamic_step_from_range,
    take_profit_price,
    ttl_expired,
    validate_ladder,
)


MEME_SPEC = {"symbol": "SIRENPERP", "is_meme": True}


def test_example_8_rungs_015pct_vol115_reproduces_reference():
    # Prompt §13.2: Entry 1.00, Step 0,15 %, 8 Stufen, Vol-Faktor 1,15.
    # Kanonische Formel (KB-Defaults step_mult=1.10) liefert avg=0.9904,
    # TP=1.0053; Referenz 0,9899/1,0047 aus dem Prompt-Beispiel —
    # Toleranz 0,001 dokumentiert die Rundung des Quellbeispiels.
    ladder = build_ladder(
        1.00, side="buy", n_safety=8, step_pct=0.0015,
        base_margin_pct=0.01, volume_mult=1.15,
    )
    assert len(ladder.rungs) == 9
    avg = ladder.average_fill_price()
    assert avg == pytest.approx(0.9899, abs=0.001)
    assert ladder.take_profit == pytest.approx(1.0047, abs=0.001)
    assert ladder.take_profit == pytest.approx(avg * 1.015, rel=1e-9)
    # 1,15x-Volumen pro Sprosse
    assert ladder.rungs[1].volume == pytest.approx(1.15)
    assert ladder.rungs[8].volume == pytest.approx(1.15 ** 8)
    # 0,15 %-Schritte: erste Safety-Sprosse 0,15 % unter Entry
    assert ladder.rungs[1].cumulative_depth_pct == pytest.approx(0.0015)


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
    assert not verdict.ok
    assert verdict.reason == "depth_rejected"
    assert verdict.depth["ok"] is False
    # direkter MP-01-Guard identisch (kein lokaler Nachbau)
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
    assert validate_ladder(deep, MEME_SPEC).ok
    assert deep.total_depth_pct == pytest.approx(0.12 * 0.618, rel=1e-6)


def test_average_fill_price_sinks_with_each_fill_and_tp_uses_avg():
    ladder = build_ladder(
        1.00, side="buy", n_safety=8, step_pct=0.0015, step_mult=1.0,
        base_margin_pct=0.01, volume_mult=1.15,
    )
    avgs = [ladder.average_fill_price(list(range(k + 1))) for k in range(len(ladder.rungs))]
    # Avg sinkt mit jeder (billigeren) Fuellung
    assert all(avgs[k + 1] < avgs[k] for k in range(len(avgs) - 1))
    # TP relativ zu Avg, nicht Entry
    tp = take_profit_price(avgs[-1], "buy", tp_pct=0.015)
    assert tp == pytest.approx(avgs[-1] * 1.015)
    assert tp < take_profit_price(1.00, "buy", tp_pct=0.015)  # avg < entry
    # short spiegelbildlich
    short = build_ladder(
        1.00, side="sell", n_safety=4, step_pct=0.002, step_mult=1.0,
        base_margin_pct=0.01,
    )
    assert short.average_fill_price() > 1.00
    assert take_profit_price(short.average_fill_price(), "sell") < 1.00


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
    # erster Step 0,08 % < Spread+Fee-Floor 0,10 % -> reject
    # (non-meme spec, damit der Tiefen-Guard den Floor-Check nicht verdeckt)
    verdict = validate_ladder(ladder, {"is_meme": False}, spread_pct=0.0)
    assert not verdict.ok
    assert verdict.reason == "first_step_below_spread_fee_floor"
    assert verdict.first_step_pct == pytest.approx(0.0008)
    # mit ausreichendem Step ok
    ok_ladder = build_ladder(
        1.00, side="buy", n_safety=6, step_pct=0.012, step_mult=1.0,
        base_margin_pct=0.01,
    )
    assert validate_ladder(ok_ladder, MEME_SPEC).ok
    # to_dict rund (kein Stub)
    d = ladder.to_dict()
    assert len(d["rungs"]) == 5
    assert d["rungs"][0]["price"] == 1.00
    assert "total_depth_pct" in d and "take_profit" in d
