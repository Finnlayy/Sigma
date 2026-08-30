"""
=========================================================
Datei:      tests/test_exhaustion_unwind.py
Zweck:      MP-08 Exhaustion + Async-Unwind: BBW-Kollaps + OI-
            Divergenz + CVD-Umkehr -> exhausted; Trend ohne
            Kollaps -> False; fehlende Feeds -> Teil 0 / fail-
            closed; Sentiment nur Bias (kein Short); Unwind-
            Reihenfolge Gewinner->Verlierer, Net-PnL-Guard,
            Minute 55 -> flat. Synthetische 5m-Bars, kein Netz.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Test)
=========================================================
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sigma.signals.volatility_exhaustion import (
    WEIGHT_BBW,
    bbw_collapse,
    exhaustion_score,
    sentiment_saturation,
)
from sigma.strategies.async_unwind import (
    FORCED_LOSS_RATIO,
    MAX_PULLBACK_WAIT_SECONDS,
    TTL_MINUTE,
    WAIT_NONE,
    WAIT_PULLBACK,
    AsyncUnwind,
    ema20,
    plan_unwind,
    pullback_level,
    vwap,
)

M5 = 300


def _bar(ts, o, h, l, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _collapse_bars(n_wide=30, n_narrow=30, start=1_700_000_000.0):
    """5m: breite Range (Volatilitaet) -> enge Range (BBW-Kollaps)."""
    rows = []
    ts = start
    p = 100.0
    for _ in range(n_wide):
        rows.append(_bar(ts, p, p + 1.0, p - 1.0, p + 0.5))
        ts += M5
        p += 0.5
    for _ in range(n_narrow):
        rows.append(_bar(ts, p, p + 0.05, p - 0.05, p + 0.03))
        ts += M5
        p += 0.03
    return rows


def _trend_bars(n=60, start=1_700_000_000.0):
    """Reiner Trend: proportional skalierte Range (BBW bleibt ~konstant),
    monoton steigend — kein BBW-Kollaps, keine Volatilitaets-Aenderung."""
    rows = []
    ts = start
    p = 100.0
    for _ in range(n):
        rows.append(_bar(ts, p, p * 1.01, p * 0.99, p * 1.005))
        ts += M5
        p *= 1.005
    return rows


def _falling_oi(n=60):
    return [float(100 - i) for i in range(n)]


def _flat_cvd(n=60):
    return [50.0] * n


# ------------------------------------------------------------- exhaustion

def test_bbw_collapse_plus_oi_divergence_plus_cvd_reversal_exhausted():
    bars = _collapse_bars()
    ex = exhaustion_score(bars, _falling_oi(), _flat_cvd())
    assert ex.valid is True
    assert ex.exhausted is True
    assert ex.score == pytest.approx(1.0)  # 0.40 + 0.35 + 0.25
    assert ex.bbw["collapsed"] is True
    assert ex.oi["diverged"] is True
    assert ex.cvd["flattened"] is True
    assert ex.components_available == ["bbw", "oi", "cvd"]


def test_pure_trend_without_bbw_collapse_not_exhausted():
    bars = _trend_bars()
    bbw = bbw_collapse(bars)
    assert bbw.collapsed is False
    assert bbw.drop_ratio < 0.05  # keine 40 %-Reduktion
    ex = exhaustion_score(bars)
    assert ex.valid is True
    assert ex.exhausted is False


def test_missing_oi_cvd_score_from_bbw_only_and_no_bars_invalid():
    ex = exhaustion_score(_collapse_bars())
    assert ex.valid is True
    assert ex.score == pytest.approx(WEIGHT_BBW)  # 0.40, nicht renormiert
    assert ex.exhausted is False                  # fail-closed ohne OI/CVD
    assert ex.components_available == ["bbw"]
    # ohne Bars -> ungueltig; zu wenige Bars -> ungueltig
    assert exhaustion_score([]).valid is False
    assert exhaustion_score(_collapse_bars(n_wide=10, n_narrow=5)).valid is False


def test_oi_divergence_needs_new_high_and_falling_oi():
    bars = _collapse_bars()
    ex = exhaustion_score(bars, _falling_oi())
    assert ex.oi["diverged"] is True
    assert ex.oi["price_new_high"] is True
    assert ex.oi["oi_falling"] is True
    # OI steigend statt fallend -> keine Divergenz
    rising_oi = [float(100 + i) for i in range(60)]
    assert exhaustion_score(bars, rising_oi).oi["diverged"] is False
    # CVD fehlt -> Teil 0, aber OI-Teil zahlt
    assert exhaustion_score(bars, _falling_oi()).score == pytest.approx(WEIGHT_BBW + 0.35)


# ------------------------------------------------------------- sentiment

def test_sentiment_without_feed_zero_no_error():
    s = sentiment_saturation()
    assert s.saturated is False
    assert s.mean_reversion_bias is False
    assert s.signals_present == 0
    d = s.to_dict()
    assert "action" not in d and "entry" not in d  # reiner Kontext


def test_extreme_funding_and_long_ratio_sets_bias_but_no_entry():
    s = sentiment_saturation(funding=0.002, ls_ratio=5.0)
    assert s.funding_saturated is True
    assert s.ls_ratio_saturated is True
    assert s.saturated is True
    assert s.mean_reversion_bias is True
    # kein automatischer Short: Struktur enthaelt weder Entry noch Action
    d = s.to_dict()
    assert "action" not in d and "entry" not in d and "side" not in d


def test_single_sentiment_signal_does_not_saturate():
    assert sentiment_saturation(funding=0.002).saturated is False
    assert sentiment_saturation(social=0.95).saturated is False


def test_oi_high_with_flat_spot_volume_saturates():
    oi = [100.0 + i for i in range(20)]      # OI steigend
    vol = [1000.0] * 20                       # Spot-Volumen flach
    s = sentiment_saturation(oi_series=oi, spot_volume_series=vol)
    assert s.oi_volume_saturated is True
    # Fail-closed: nur EIN vorliegendes Signal saettigt -> keine Saettigung
    assert s.saturated is False
    assert s.signals_present == 1
    # OI hoch, aber Spot-Volumen ebenfalls steigend -> nicht saettigend
    vol2 = [1000.0 * (1.0 + 0.05 * i) for i in range(20)]
    s2 = sentiment_saturation(oi_series=oi, spot_volume_series=vol2)
    assert s2.oi_volume_saturated is False
    # zwei Signale (Funding + OI/Vol) -> Saettigung moeglich
    s3 = sentiment_saturation(funding=0.002, oi_series=oi, spot_volume_series=vol)
    assert s3.saturated is True
    assert s3.mean_reversion_bias is True


# --------------------------------------------------------------- unwind

def _unwind_ctx(**overrides):
    ctx = {
        "symbol": "SOL/USD",
        "winner_side": "buy",
        "loser_side": "sell",
        "winner_volume": 100.0,
        "loser_volume": 50.0,
        "winner_price": 105.0,
        "loser_price": 98.0,
        "winner_pnl": 100.0,
        "loser_loss": 30.0,
        "now": datetime(2026, 8, 28, 15, 30, tzinfo=timezone.utc).timestamp(),
        "pullback_level": 99.5,
    }
    ctx.update(overrides)
    return ctx


def test_unwind_sequence_winner_first_then_loser_with_wait():
    plan = plan_unwind(_unwind_ctx())
    assert plan.valid is True
    assert plan.forced is False
    assert plan.ttl_flat is False
    assert [s.index for s in plan.steps] == [1, 2]  # Reihenfolge im Record
    w, lo = plan.steps[0], plan.steps[1]
    assert w.side == "buy" and w.action == "CLOSE"
    assert w.volume == 100.0 and w.reason == "realize_winner"
    assert w.wait_condition == WAIT_NONE
    assert lo.side == "sell"
    assert lo.wait_condition == WAIT_PULLBACK
    assert lo.max_wait_seconds == MAX_PULLBACK_WAIT_SECONDS
    assert lo.price == pytest.approx(99.5)  # Pullback-Ziel
    assert lo.forced is False
    assert plan.net_estimate == pytest.approx(70.0)


def test_unwind_forced_when_loss_exceeds_half_of_gain():
    # Verlust 60 > 50 % von Gewinn 100 -> sofort schliessen, forced=True
    plan = plan_unwind(_unwind_ctx(loser_loss=60.0))
    assert plan.forced is True
    lo = plan.steps[1]
    assert lo.forced is True
    assert lo.wait_condition == WAIT_NONE
    assert lo.reason == "forced_net_pnl_guard"
    assert FORCED_LOSS_RATIO == 0.50
    # knapp unter 50 % -> normaler Pullback-Weg
    plan2 = plan_unwind(_unwind_ctx(loser_loss=49.9))
    assert plan2.steps[1].forced is False


def test_unwind_minute_55_forces_flat_both_sides():
    ts55 = datetime(2026, 8, 28, 15, 55, tzinfo=timezone.utc).timestamp()
    plan = plan_unwind(_unwind_ctx(now=ts55))
    assert plan.ttl_flat is True
    assert plan.forced is True
    assert len(plan.steps) == 2
    assert all(s.forced for s in plan.steps)
    assert all(s.wait_condition == WAIT_NONE for s in plan.steps)
    assert all(s.reason == "ttl_minute_55" for s in plan.steps)
    assert TTL_MINUTE == 55
    # Minute 54 -> noch kein TTL-Zwang
    ts54 = datetime(2026, 8, 28, 15, 54, tzinfo=timezone.utc).timestamp()
    assert plan_unwind(_unwind_ctx(now=ts54)).ttl_flat is False


def test_unwind_fail_closed_on_missing_data():
    assert plan_unwind(None).valid is False
    assert plan_unwind({}).valid is False
    assert plan_unwind(_unwind_ctx(symbol="")).valid is False
    assert plan_unwind(_unwind_ctx(winner_side="sell", loser_side="sell")).valid is False
    assert plan_unwind(_unwind_ctx(winner_volume=0.0)).valid is False
    assert plan_unwind(_unwind_ctx(winner_pnl=-5.0)).valid is False


def test_async_unwind_strategy_first_step_intent():
    tmpl = AsyncUnwind()
    intent = tmpl.plan(_unwind_ctx())
    assert intent.action == "CLOSE"
    assert intent.side == "buy"
    assert intent.volume == pytest.approx(100.0)
    assert intent.price == pytest.approx(105.0)
    assert intent.execution_mode == "kraken_paper"
    d = intent.details
    assert d["sequence_index"] == 1
    assert d["wait_condition"] == WAIT_NONE
    assert d["unwind_plan"]["valid"] is True
    assert len(d["unwind_plan"]["steps"]) == 2  # volle Sequenz im Record
    # fehlende Daten -> FLAT
    assert tmpl.plan(None).action == "FLAT"
    assert tmpl.plan({"symbol": "X"}).action == "FLAT"


def test_vwap_ema20_pullback_level_helpers():
    bars = _collapse_bars()
    v = vwap(bars)
    e = ema20(bars)
    assert v is not None and v > 0
    assert e is not None and e > 0
    level = pullback_level(bars)
    assert level in (v, e)  # naeheres der beiden Ziele
    assert pullback_level([]) is None
    assert vwap([]) is None
    assert ema20(_trend_bars(n=10)) is None  # < 20 Bars -> None
