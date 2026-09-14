"""
Wave-Regime: Collider-Klassifikation + Orchestrator-Anwendung.
Synthetische Closed-Bar-OHLCV, now= injiziert. Keine Live-Orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sigma.core.fractal_scaling import SigmaFractalCore
from sigma.orchestration import MasterOrchestrator
from sigma.signals.quantum_wave_collider import (
    STATUS_COLLAPSED,
    STATUS_HTF_OPEN,
    STATUS_IDLE,
    STATUS_INVALIDATED,
    QuantumWaveCollider,
)

# Friday 2026-08-28 15:00 UTC — NY expansion, no 21:00 gap
NY_FRIDAY = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc).timestamp()
M15 = 900
H1 = 3600


def _bar(ts: float, o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _bars(n: int = 20, start: float = 1_700_000_000.0, step: int = M15, price: float = 100.0) -> list:
    out = []
    for i in range(n):
        p = price * (1.0 + 0.001 * i)
        out.append(_bar(start + i * step, p, p * 1.01, p * 0.99, p, 100.0 + i))
    return out


def _closed_now(bars: list, interval_sec: int) -> float:
    return float(bars[-1]["ts"]) + float(interval_sec)


def _expansion_then_fvg(start: float = 1_700_000_000.0, step: int = M15) -> list:
    """Uptrend structure + 3-bar bullish FVG. Last structure high 120, early low ~99."""
    rows = []
    prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]
    for i, p in enumerate(prices):
        rows.append(_bar(start + i * step, p, p + 1.0, p - 1.0, p))
    # A / B / C — bullish FVG: A.high < C.low
    i0 = len(rows)
    rows.append(_bar(start + i0 * step, 112.0, 113.0, 111.0, 112.5))
    rows.append(_bar(start + (i0 + 1) * step, 113.0, 118.0, 113.0, 117.0))
    rows.append(_bar(start + (i0 + 2) * step, 117.0, 120.0, 116.0, 119.0))
    return rows


def _collapsed_series(step: int = M15) -> list:
    """Prior FVG left in structure; last closed bar dips below CE50 into the gap."""
    rows = _expansion_then_fvg(step=step)
    ts = rows[-1]["ts"] + step
    # range ≈ 99–120 → CE50 ≈ 109.5; FVG 113–116; close 109 in discount, wick into gap
    rows.append(_bar(ts, 115.0, 115.0, 108.0, 109.0))
    return rows


def _invalidated_series(step: int = M15) -> list:
    rows = _expansion_then_fvg(step=step)
    ts = rows[-1]["ts"] + step
    rows.append(_bar(ts, 100.0, 101.0, 90.0, 92.0))
    return rows


def test_collider_collapsed_into_ce50_discount():
    bars = _collapsed_series()
    now = _closed_now(bars, M15)
    state = QuantumWaveCollider().evaluate(bars, interval_min=15, now=now)
    assert state.status == STATUS_COLLAPSED
    assert state.valid is True
    assert state.live_gate is False
    assert state.ce50 is not None
    assert state.range_high is not None and state.range_low is not None
    assert state.ce50 == SigmaFractalCore.ce50(state.range_high, state.range_low)
    assert state.discount is True
    assert state.bullish_fvg is True
    assert state.fvg_touch is True
    assert bars[-1]["c"] < state.ce50
    assert bars[-1]["c"] >= state.range_low


def test_zero_lookahead_integrity():
    closed = _collapsed_series()
    now_closed = _closed_now(closed, M15)
    base = QuantumWaveCollider().evaluate(closed, interval_min=15, now=now_closed)
    open_bar = _bar(closed[-1]["ts"] + M15, 200.0, 999.0, 1.0, 500.0)
    leaking = closed + [open_bar]
    now_open = open_bar["ts"] + 10.0
    leaked = QuantumWaveCollider().evaluate(leaking, interval_min=15, now=now_open)
    assert leaked.status == STATUS_HTF_OPEN
    assert leaked.range_high == base.range_high
    assert leaked.range_low == base.range_low
    assert leaked.ce50 == base.ce50
    assert leaked.valid is False


def test_orchestrator_unwinds_on_range_low_breach():
    htf = _invalidated_series(step=H1)
    ltf = _bars(20, start=htf[0]["ts"], step=M15, price=100.0)
    snap = SimpleNamespace(
        series={"BTC/USD": ltf},
        htf_series={"BTC/USD": htf},
        degraded=False,
    )
    orch = MasterOrchestrator(ports={"polymarket": None})
    out = orch.tick(snap, now=NY_FRIDAY)
    assert out["status"] == "unwind_only"
    assert out["deployed"] == 0
    assert out["wave"]["status"] == STATUS_INVALIDATED
    assert out["wave"]["reason"] == "range_low_breach"


def test_orchestrator_publishes_wave_on_tick():
    htf = _bars(20, start=1_700_000_000.0, step=H1, price=100.0)
    ltf = _bars(20, start=htf[0]["ts"], step=M15, price=100.0)
    now = max(NY_FRIDAY, _closed_now(htf, H1))
    snap = SimpleNamespace(
        series={"BTC/USD": ltf},
        htf_series={"BTC/USD": htf},
        degraded=False,
    )
    orch = MasterOrchestrator(ports={"polymarket": None})
    out = orch.tick(snap, now=now)
    assert "wave" in out
    wave = out["wave"]
    assert wave["status"] in (STATUS_IDLE, STATUS_COLLAPSED, STATUS_INVALIDATED, STATUS_HTF_OPEN)
    assert "range_high" in wave and "ce50" in wave
    assert wave["live_gate"] is False
    assert out["status"] in ("tick", "unwind_only", "htf_not_ready")
    if out["status"] == "tick":
        for route in out.get("routes") or []:
            assert route.get("path") in ("e_then_a", "flat", "e_blocked", "loop_d_paper")


def test_orchestrator_wave_and_gap_fail_closed():
    htf = _collapsed_series(step=H1)
    ltf = _bars(20, start=htf[0]["ts"], step=M15, price=110.0)
    gap_ts = datetime(2026, 8, 28, 21, 15, tzinfo=timezone.utc).timestamp()
    now = max(gap_ts, _closed_now(htf, H1))
    snap = SimpleNamespace(
        series={"BTC/USD": ltf},
        htf_series={"BTC/USD": htf},
        degraded=False,
    )
    out = MasterOrchestrator(ports={"polymarket": None}).tick(snap, now=now)
    assert out["deployed"] == 0
    assert out["status"] == "unwind_only"
    assert out["session"]["liquidity_gap"] is True
    assert "wave" in out
