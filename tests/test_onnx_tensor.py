"""
=========================================================
Datei:      tests/test_onnx_tensor.py
Zweck:      MP-11 ONNX-Observation-Tensor: Shape (1,16) float32,
            Skaleninvarianz (78.000 vs 0,014), Einzel-Features,
            Fallback-Policy (21:00/TTL/ohne P_cal), Bar-Lock,
            Determinismus, Latenz (tolerant). Kein Netz, kein
            Modell-Training; ohne onnxruntime -> Fallback.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Test)
=========================================================
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import pytest

from sigma.core.onnx_quantum_tensor import (
    ACTION_FLAT,
    ACTION_LONG,
    ACTION_SHORT,
    COS_PHI_STRONG,
    LEVERAGE_MAX,
    LEVERAGE_MIN,
    FEATURE_NAMES,
    OnnxQuantumTensor,
    TensorContext,
    build_observation_tensor,
    cos_phi_feature,
    d_ce_feature,
    fallback_action,
    m_tangent_feature,
    p_cal_feature,
    platt_scale,
    pos_00_feature,
    pos_eq_feature,
    q_norm_feature,
    ttl_norm_feature,
    utc_safe_feature,
)

M15 = 900
H1 = 3600


def _bar(ts, o, h, l, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _bars(price=100.0, n=30, start=1_700_000_000.0, drift=0.0):
    out = []
    for i in range(n):
        p = price * (1.0 + drift * i)
        out.append(_bar(start + i * M15, p, p * 1.01, p * 0.99, p))
    return out


def _ctx(**over):
    base = dict(
        candles=_bars(),
        open_00=100.0,
        minutes_since_00=600.0,
        atr=1.0,
        poly_raw=0.70,
        range_low=95.0,
        range_high=115.0,
        ce50=105.0,
        ttl_minutes_remaining=30.0,
        utc_hour=15,
        rvol=2.0,
        cvd_absorption=0.1,
        hurst=0.6,
        liq_distance_pct=0.05,
        thrust=True,
        fvg_touch=True,
        leverage=15,
    )
    base.update(over)
    return TensorContext(**base)


# ------------------------------------------------------------- tensor

def test_tensor_shape_dtype_and_range():
    t = build_observation_tensor(_ctx())
    assert t.shape == (1, 16)
    assert t.dtype == np.float32
    assert len(FEATURE_NAMES) == 16
    assert np.isfinite(t).all()
    # definierte Bereiche je Feature
    for i, name in enumerate(FEATURE_NAMES):
        v = float(t[0, i])
        if name in ("cos_phi", "cvd", "m_tangent", "d_ce"):
            assert -1.0 <= v <= 1.0
        else:
            assert 0.0 <= v <= 1.0


def test_scale_invariance_78000_vs_0014():
    hi = _ctx(candles=_bars(price=78000.0), open_00=78000.0, atr=500.0,
              range_low=75000.0, range_high=81000.0, ce50=78000.0)
    lo = _ctx(candles=_bars(price=0.014), open_00=0.014, atr=0.0001,
              range_low=0.013, range_high=0.015, ce50=0.014)
    t_hi = build_observation_tensor(hi)
    t_lo = build_observation_tensor(lo)
    # Ratio-Features identisch trotz 5-6 Groessenordnungen Unterschied
    for i in (0, 1, 2, 3, 4, 6, 7, 8):
        assert float(t_hi[0, i]) == pytest.approx(float(t_lo[0, i]), abs=1e-4), FEATURE_NAMES[i]


def test_single_features_constructed_bars():
    # Marubozu: Close > Open, kaum Dochte
    maru = _bar(0, 100.0, 101.0, 99.99, 101.0)
    assert cos_phi_feature(maru) == pytest.approx((101.0 - 100.0) / (101.0 - 99.99 + 1e-9))
    assert cos_phi_feature(maru) > 0.9
    # Doji: ~0
    doji = _bar(0, 100.0, 101.0, 99.0, 100.01)
    assert abs(cos_phi_feature(doji)) < 0.01
    # q_norm: Dochte/ATR (auf [0,1] geclippt)
    assert q_norm_feature(doji, 2.0) == pytest.approx(((101.0 - 100.01) + (100.0 - 99.0)) / 2.0)
    assert q_norm_feature(doji, 1.0) == 1.0  # geclippt
    assert q_norm_feature(doji, None) == 0.0
    # pos_00 Vorzeichen
    assert pos_00_feature(102.0, 100.0, 2.0) > 0
    assert pos_00_feature(98.0, 100.0, 2.0) < 0
    assert pos_00_feature(100.0, None, 2.0) == 0.0
    # m_tangent
    assert m_tangent_feature(101.0, 100.0, 60.0) > 0
    assert m_tangent_feature(100.0, 101.0, 60.0) < 0
    # pos_EQ unter 0,5 im Discount
    assert pos_eq_feature(100.0, 95.0, 115.0) == pytest.approx(0.25)
    assert pos_eq_feature(110.0, 95.0, 115.0) == pytest.approx(0.75)
    # d_CE
    assert d_ce_feature(105.0, 105.0, 1.0) == pytest.approx(0.0)
    assert d_ce_feature(106.0, 105.0, 1.0) > 0
    # TTL
    assert ttl_norm_feature(30.0) == pytest.approx(0.5)
    assert ttl_norm_feature(120.0) == pytest.approx(1.0)
    assert ttl_norm_feature(None) == 0.0
    # UTC
    assert utc_safe_feature(15) == 1.0
    assert utc_safe_feature(21) == 0.0
    assert utc_safe_feature(21.5) == 0.0
    assert utc_safe_feature(22) == 1.0
    assert utc_safe_feature(None) == 0.0
    # P_cal (Platt, Default Identitaet)
    assert p_cal_feature(0.7) == pytest.approx(0.7)
    assert p_cal_feature(None) == 0.0
    assert platt_scale(0.7) == pytest.approx(0.7)


def test_missing_sources_stay_neutral():
    t = build_observation_tensor(TensorContext(candles=[_bar(0, 100.0, 101.0, 99.0, 100.0)]))
    assert t.shape == (1, 16)
    assert float(t[0, 5]) == 0.0   # P_cal ohne Feed
    assert float(t[0, 6]) == 0.5   # pos_EQ neutral
    assert float(t[0, 9]) == 0.0   # UTC unbekannt -> unsicher
    assert float(t[0, 12]) == 0.5  # Hurst neutral
    assert float(t[0, 13]) == 0.5  # Liq-Distanz neutral


# ------------------------------------------------------------- fallback

def test_fallback_utc_quarantine_flat():
    out = fallback_action(_ctx(utc_hour=21, ttl_minutes_remaining=30.0))
    assert out["action"] == ACTION_FLAT
    assert out["reason"] == "utc_quarantine"


def test_fallback_ttl_short_flat():
    out = fallback_action(_ctx(ttl_minutes_remaining=8.0))  # 8/60 = 0,133 < 0,15
    assert out["action"] == ACTION_FLAT
    assert out["reason"].startswith("ttl_too_short")


def test_fallback_long_all_conditions():
    # Marubozu-Candle (cos_phi ~1), P_cal 0,7 -> LONG
    maru = _bar(0, 100.0, 101.0, 99.5, 101.0)
    out = fallback_action(_ctx(candles=[maru], poly_raw=0.7, atr=1.0))
    assert out["action"] == ACTION_LONG
    assert LEVERAGE_MIN <= out["leverage"] <= LEVERAGE_MAX
    # Discount + Kauf-Tail ohne starken cos_phi
    maru2 = _bar(0, 100.0, 100.6, 99.0, 100.3)  # langer unterer Docht
    out2 = fallback_action(_ctx(candles=[maru2], poly_raw=0.7, atr=1.0,
                                range_low=99.5, range_high=115.0))
    assert out2["action"] == ACTION_LONG
    # ohne P_cal -> FLAT (fail-closed)
    out3 = fallback_action(_ctx(candles=[maru], poly_raw=None))
    assert out3["action"] == ACTION_FLAT


def test_fallback_short_mirror():
    bear = _bar(0, 100.0, 100.2, 99.0, 99.0)  # cos_phi <= -0,75
    out = fallback_action(_ctx(candles=[bear], poly_raw=0.2, atr=1.0))
    assert out["action"] == ACTION_SHORT
    out2 = fallback_action(_ctx(candles=[bear], poly_raw=0.5, atr=1.0))
    assert out2["action"] == ACTION_FLAT  # P_cal neutral


# ------------------------------------------------------------- wrapper

def test_wrapper_fallback_without_model_and_deterministic():
    w = OnnxQuantumTensor()  # kein Modellpfad
    assert w.model_available is False
    out = w.evaluate(_ctx())
    assert out["action"] in (ACTION_LONG, ACTION_FLAT, ACTION_SHORT)
    assert out["model_available"] is False
    results = [w.evaluate(_ctx())["action"] for _ in range(100)]
    assert len(set(results)) == 1  # deterministisch


def test_wrapper_bar_lock():
    w = OnnxQuantumTensor()
    bar_ts = 1_700_000_000.0
    first = w.evaluate(_ctx(), bar_ts=bar_ts)
    if first["action"] == ACTION_FLAT:
        # FLAT sperrt nicht; nutze einen sicheren LONG-Kontext
        maru = _bar(0, 100.0, 101.0, 99.5, 101.0)
        ctx = _ctx(candles=[maru], poly_raw=0.7, atr=1.0)
        first = w.evaluate(ctx, bar_ts=bar_ts)
    assert first["action"] == ACTION_LONG
    second = w.evaluate(_ctx(), bar_ts=bar_ts)
    assert second["action"] == ACTION_FLAT
    assert second["reason"] == "BLOCKED_BY_BAR_LOCK"
    # andere Bar -> erneut moeglich
    third = w.evaluate(_ctx(), bar_ts=bar_ts + 900.0)
    assert third["reason"] != "BLOCKED_BY_BAR_LOCK"


def test_wrapper_latency_tolerant():
    w = OnnxQuantumTensor()
    ctx = _ctx()
    t0 = time.perf_counter()
    for _ in range(100):
        w.evaluate(ctx)
    elapsed = time.perf_counter() - t0
    # tolerante CI-Schwelle: 100 Aufrufe deutlich unter 2 s
    assert elapsed < 2.0


def test_wrapper_model_path_unavailable_falls_back():
    w = OnnxQuantumTensor(model_path="/nonexistent/model.onnx")
    assert w.model_available is False
    out = w.evaluate(_ctx())
    assert out["model_available"] is False
    assert out["action"] in (ACTION_LONG, ACTION_FLAT, ACTION_SHORT)


# ------------------------------------------------------------- orchestrator

def test_orchestrator_onnx_key_only_with_port():
    from types import SimpleNamespace
    from sigma.orchestration import MasterOrchestrator

    class FakeOnnxPort:
        def evaluate(self, material):
            return {"action": ACTION_LONG, "leverage": 15, "reason": "fake"}

    snap = SimpleNamespace(
        series={"BTC/USD": _bars()},
        htf_series={"BTC/USD": _bars()},
        degraded=False,
    )
    plain = MasterOrchestrator()
    out = plain.tick(snap, now=datetime(2026, 8, 28, 15, 30, tzinfo=timezone.utc).timestamp())
    assert "onnx" not in out  # ohne Port kein Key
    orch = MasterOrchestrator(ports={"onnx": FakeOnnxPort()})
    out2 = orch.tick(snap, now=datetime(2026, 8, 28, 15, 30, tzinfo=timezone.utc).timestamp())
    assert out2["onnx"]["action"] == ACTION_LONG


def test_orchestrator_onnx_port_error_fail_closed():
    from types import SimpleNamespace
    from sigma.orchestration import MasterOrchestrator

    class BrokenPort:
        def evaluate(self, material):
            raise RuntimeError("boom")

    snap = SimpleNamespace(
        series={"BTC/USD": _bars()},
        htf_series={"BTC/USD": _bars()},
        degraded=False,
    )
    orch = MasterOrchestrator(ports={"onnx": BrokenPort()})
    out = orch.tick(snap, now=datetime(2026, 8, 28, 15, 30, tzinfo=timezone.utc).timestamp())
    assert out["onnx"]["action"] == ACTION_FLAT
    assert out["onnx"]["reason"].startswith("onnx_error:")
