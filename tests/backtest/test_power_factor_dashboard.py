"""
=========================================================
Datei:      tests/backtest/test_power_factor_dashboard.py
Zweck:      MP-16 Tests: cos-phi-Backtester (Trend/Chop/Bär,
            Hysterese, 1-Bar-Lag) und HTML/JSON-Export
            (aufsteigende lückenlose Zeiten, Marker nur bei
            Wechseln, drei Panes, Schwellen-Linien,
            Determinismus). Kein Netz, keine Orders, keine
            Artefakte in Git (results/ gitignored).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Test)
=========================================================
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping

import pytest

from app.dashboard.tv_lightweight_export import (
    build_payload,
    export_backtest_html,
    payload_to_json,
    render_html,
)
from sigma.backtest.power_factor_backtest import (
    run_power_factor_backtest,
)

H1 = 3600
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
START = datetime(2026, 3, 2, tzinfo=timezone.utc)


def _bar(ts: float, o: float, h: float, l: float, c: float) -> Dict[str, Any]:
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": 100.0}


def _uptrend(n: int = 300, step: float = 0.05) -> List[Dict[str, Any]]:
    out = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + step
        out.append(_bar(START.timestamp() + i * H1, o, c + 0.02, price - 0.02, c))
        price = c
    return out


def _downtrend(n: int = 300, step: float = 0.05) -> List[Dict[str, Any]]:
    out = []
    price = 100.0
    for i in range(n):
        o = price
        c = price - step
        out.append(_bar(START.timestamp() + i * H1, o, price + 0.02, c - 0.02, c))
        price = c
    return out


def _chop(n: int = 300, amp: float = 0.02) -> List[Dict[str, Any]]:
    """Deterministischer Random Walk: Richtung wechselt zufällig je Bar
    -> cos_phi_path bleibt nahe 0 (kein Trend, keine glatte Kurve)."""
    out = []
    price = 100.0
    rng = 987654321
    for i in range(n):
        rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
        direction = 1.0 if (rng % 100) < 50 else -1.0
        o = price
        c = price + direction * amp
        out.append(_bar(START.timestamp() + i * H1, o, max(o, c) + 0.01, min(o, c) - 0.01, c))
        price = c
    return out


def _uptrend_noisy(n: int = 300, step: float = 0.05, noise: float = 0.01) -> List[Dict[str, Any]]:
    out = []
    price = 100.0
    for i in range(n):
        o = price
        wobble = noise * math.sin(i / 3.0)
        c = price + step + wobble
        out.append(_bar(START.timestamp() + i * H1, o, max(o, c) + 0.02, min(o, c) - 0.02, c))
        price = c
    return out


# ------------------------------------------------------------- Backtester

def test_uptrend_long_profit():
    bars = _uptrend()
    r = run_power_factor_backtest(bars)
    assert r.trade_count >= 1
    assert all(t.side == "long" for t in r.trades)
    assert r.total_return > 0
    assert r.equity[-1] > 1.0
    assert r.labels.count("entry_long") >= 1


def test_downtrend_short_profit():
    bars = _downtrend()
    r = run_power_factor_backtest(bars)
    assert r.trade_count >= 1
    assert all(t.side == "short" for t in r.trades)
    assert r.total_return > 0


def test_chop_mostly_flat():
    bars = _chop()
    r = run_power_factor_backtest(bars)
    # Chop: kaum signifikante ER-Signale -> überwiegend flat
    active = sum(1 for p in r.positions if p != 0)
    assert active / len(r.positions) < 0.5
    assert r.labels.count("flat") > len(r.labels) // 2


def test_hysteresis_no_flapping():
    bars = _uptrend_noisy(noise=0.01)
    r = run_power_factor_backtest(bars)
    # einmal Long: keine kurzen Flats mitten im Long (bis Exit-Schwelle)
    flips = 0
    for i in range(1, len(r.positions)):
        if r.positions[i] != r.positions[i - 1]:
            flips += 1
    assert flips <= 4  # Entry + Exit, kein Flattern


def test_one_bar_lag():
    bars = _uptrend_noisy(noise=0.0)
    r = run_power_factor_backtest(bars)
    # Finde die Signal-Bar des ersten Long und prüfe: Position an
    # dieser Bar ist noch 0 (1-Bar-Lag gegen Look-ahead)
    idx = next(i for i, lab in enumerate(r.labels) if lab == "entry_long")
    # Labels sind pro wirksamer Bar; Signal entstand an Bar idx-1.
    # cos_phi an Bar idx-1 muss >= Schwelle sein, Position dort 0.
    assert r.positions[idx - 1] == 0
    assert r.cos_phi[idx - 1] >= r.params.long_threshold


def test_metrics_sanity():
    bars = _uptrend()
    r = run_power_factor_backtest(bars)
    assert r.max_drawdown >= 0.0
    assert r.win_rate <= 1.0
    assert r.profit_factor > 0.0
    assert len(r.equity) == len(bars)
    assert len(r.returns) == len(bars)
    assert len(r.labels) == len(bars)
    d = r.to_dict()
    assert d["trade_count"] == len(d["trades"])


# ------------------------------------------------------------- Export

def test_payload_ascending_gapfree_and_markers():
    bars = _uptrend(60)
    r = run_power_factor_backtest(bars)
    payload = build_payload(bars, r)
    times = [c["time"] for c in payload["candles"]]
    assert times == sorted(times)
    for i in range(1, len(times)):
        assert times[i] - times[i - 1] == H1  # lückenlos
    assert len(payload["cos_phi"]) == len(bars)
    assert len(payload["equity"]) == len(bars)
    # Marker nur an Positionswechseln
    pos = r.positions
    expected = [i for i in range(len(pos))
                if i > 0 and pos[i] != pos[i - 1]]
    assert len(payload["markers"]) == len(expected)
    # erster Marker = Long-Eintritt (arrowUp, unten)
    assert payload["markers"][0]["shape"] == "arrowUp"
    assert payload["markers"][0]["position"] == "belowBar"
    assert "benchmark" in payload
    assert payload["thresholds"] == {"long": 0.40, "short": -0.40, "exit": 0.15}


def test_payload_rejects_gaps():
    bars = _uptrend(30)
    bars = list(bars)
    bars[10] = dict(bars[10], ts=bars[10]["ts"] + 2 * H1)  # Zeitlücke
    r = run_power_factor_backtest(bars)
    with pytest.raises(ValueError, match="lückenlos"):
        build_payload(bars, r)


def test_html_contract():
    bars = _uptrend(40)
    r = run_power_factor_backtest(bars)
    html = render_html(build_payload(bars, r))
    for container in ("pane-candles", "pane-cos", "pane-equity"):
        assert f'id="{container}"' in html
    # Schwellen-Linien: createPriceLine mit 0.4 / -0.4 / 0.15 / -0.15
    assert '"price":0.4' in html or "0.4" in html
    assert "createPriceLine" in html
    assert "subscribeVisibleLogicalRangeChange" in html
    assert "lightweight-charts" in html
    assert "arrowUp" in html


def test_export_writes_file_and_deterministic():
    bars = _uptrend(40)
    r = run_power_factor_backtest(bars)
    out = os.path.join(RESULTS_DIR, "mp16_demo.html")
    html1 = export_backtest_html(bars, r, out)
    assert os.path.isfile(out)
    with open(out) as f:
        assert f.read() == html1
    # Determinismus: gleiche Eingabe -> identische Payload-JSON
    p1 = payload_to_json(build_payload(bars, r))
    p2 = payload_to_json(build_payload(bars, r))
    assert p1 == p2
    # </script> im JSON muss escaped sein (HTML-Embedding sicher)
    esc = render_html({"x": "</script>"})
    assert '{"x":"<\\/script>"}' in esc
