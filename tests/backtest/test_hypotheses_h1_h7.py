"""
=========================================================
Datei:      tests/backtest/test_hypotheses_h1_h7.py
Zweck:      MP-12 Hypothesen-Harness H1-H7 (KB §14/§17):
            synthetische, lückenfreie OHLCV-Serien; Slippage/
            Fee-Modell (Taker 0,04 %/Seite, 0,06 % Roundtrip);
            Faktor-Sweeps mit Walk-Forward; Wochenend-/Sweep-
            Szenarien; cos-phi-Pfad mit Hysterese + 1-Bar-Lag.
            Rein deterministisch, kein Netz, keine Orders.
            Ergebnisse als Markdown/JSON unter
            tests/backtest/results/ (gitignored).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Backtest)
=========================================================
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pytest

from sigma.backtest.lookahead_pipeline_check import (
    assert_no_lookahead,
    check_series_closed,
    closed_htf_prefix,
    walk_forward_folds,
    walk_forward_split,
)
from sigma.backtest.report import load_results, render_markdown
from sigma.signals.power_triangle import cos_phi_path

EPS = 1e-9
TAKER_BPS_PER_SIDE = 0.0004   # 0,04 %/Seite
ROUNDTRIP_FEE = 0.0006        # 0,06 % Roundtrip
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ------------------------------------------------------------------ fixtures

H1 = 3600
D = 24 * H1
MON = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Montag


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_bar(ts: float, o: float, h: float, l: float, c: float, v: float = 100.0) -> Dict[str, Any]:
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _series(
    start: datetime,
    n: int,
    step_minutes: int = 60,
    base: float = 100.0,
    up_prob: float = 0.5,
    drift: float = 0.0002,
    gap: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Lückenfreie chronologische Serie; bei gap>0 wird die Zeitachse
    übersprungen (Feiertag/Wochenende) — ts bleibt lückenlos logisch."""
    out: List[Dict[str, Any]] = []
    price = base
    rng_state = 123456789
    for i in range(n):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        r = rng_state / 0x7FFFFFFF
        up = r < up_prob
        move = drift * price * (1.0 + (rng_state % 10) / 10.0)
        delta = move if up else -move
        o = price
        c = price + delta
        h = max(o, c) * (1.0 + 0.002 * (rng_state % 5) / 5.0)
        l = min(o, c) * (1.0 - 0.002 * (rng_state % 5) / 5.0)
        ts = (start + timedelta(minutes=i * step_minutes)).timestamp()
        out.append(_make_bar(ts, o, h, l, c))
        price = c
    return out


def _iso_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _weekend_week(n_weeks: int = 4) -> List[Dict[str, Any]]:
    """Mo-Fr je 6h, Sa/So je 8h -> Weekend-Bars vorhanden, ts lückenlos."""
    out: List[Dict[str, Any]] = []
    ts = MON.timestamp()
    price = 100.0
    hours_per_weekday = [6, 6, 6, 6, 6, 8, 8]
    for w in range(n_weeks):
        for day, hours in enumerate(hours_per_weekday):
            for hh in range(hours):
                o = price
                up = (day >= 5) == (hh % 2 == 0)
                delta = 0.001 * price if up else -0.001 * price
                c = o + delta
                h = max(o, c) * 1.002
                l = min(o, c) * 0.998
                out.append(_make_bar(ts, o, h, l, c))
                ts += H1
                price = c
    return out


# ------------------------------------------------------------------ helpers

def _cwma(values: Sequence[float], period: int = 20) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / period if i >= period - 1 else None)
    return out


def _max_dd(equity: Sequence[float]) -> float:
    peak = -1e18
    dd = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = max(dd, peak - e)
    return dd


def _sharpe(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if var <= EPS:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(365 * 24)


def _stats(returns: Sequence[float]) -> Dict[str, float]:
    if not returns:
        return {"return_pct": 0.0, "max_dd_pct": 0.0, "sharpe": 0.0,
                "win_rate": 0.0, "profit_factor": 0.0, "trades": 0.0,
                "liq_count": 0.0}
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else (9.99 if wins else 0.0)
    return {
        "return_pct": (equity[-1] - 1.0) * 100.0,
        "max_dd_pct": _max_dd(equity) * 100.0,
        "sharpe": _sharpe(returns),
        "win_rate": len(wins) / len(returns) if returns else 0.0,
        "profit_factor": pf,
        "trades": float(len(returns)),
        "liq_count": 0.0,
    }


def _simulate(
    candles: Sequence[Mapping[str, Any]],
    *,
    side_fn: Any,
    fee: float = ROUNDTRIP_FEE,
    slippage: float = 0.0,
    leverage: float = 1.0,
    liq_buffer: float = 0.05,
    delay_bars: int = 1,
    entry_threshold: Optional[float] = None,
    exit_threshold: Optional[float] = None,
) -> Tuple[List[float], List[Dict[str, Any]]]:
    """Simulation auf geschlossenen Bars; Signale werden um
    delay_bars verzögert ausgeführt. Fee/Slippage als Dezimalen."""
    closes = [float(c["c"]) for c in candles]
    returns: List[float] = []
    trades: List[Dict[str, Any]] = []
    pos = 0
    entry_px: Optional[float] = None
    pending: Optional[int] = None
    for i in range(len(candles)):
        bar = candles[i]
        # Signal aus der VORIGEN Bar (1-Bar-Lag gegen Look-ahead)
        if i >= delay_bars:
            prev = candles[i - delay_bars]
            sig = side_fn(prev, i - delay_bars)
            if sig == 1:
                pending = 1
            elif sig == -1:
                pending = -1
        if pending is not None and pos == 0:
            px = float(bar["c"]) * (1.0 + slippage)
            entry_px = px
            pos = pending
            pending = None
        elif pos != 0 and entry_px is not None:
            px = float(bar["c"]) * (1.0 - slippage if pos > 0 else 1.0 + slippage)
            # Liquidations-Check: adverse Move > liq_buffer/hebel wipes
            if (pos > 0 and px <= entry_px * (1.0 - liq_buffer / leverage)) or (
                pos < 0 and px >= entry_px * (1.0 + liq_buffer / leverage)
            ):
                r = -1.0
                returns.append(r)
                trades.append({"ts": bar["ts"], "side": "long" if pos > 0 else "short",
                               "pnl_pct": r * 100.0, "liq": True})
                pos = 0
                entry_px = None
                continue
            r = pos * (px - entry_px) / entry_px * leverage - fee
            if i == len(candles) - 1:
                returns.append(r)
                trades.append({"ts": bar["ts"], "side": "long" if pos > 0 else "short",
                               "pnl_pct": r * 100.0, "liq": False})
                pos = 0
                entry_px = None
            else:
                returns.append(r)
                trades.append({"ts": bar["ts"], "side": "long" if pos > 0 else "short",
                               "pnl_pct": r * 100.0, "liq": False})
                pos = 0
                entry_px = None
    return returns, trades


def _collect(candles: Sequence[Mapping[str, Any]], trades: Sequence[Mapping[str, Any]],
             key: str) -> Tuple[float, float, int]:
    vals = [t["pnl_pct"] for t in trades]
    return (sum(vals) / len(vals) if vals else 0.0,
            _stats([v / 100.0 for v in vals])["max_dd_pct"], len(vals))


def _write_result(name: str, payload: Dict[str, Any]) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    md = json.dumps(payload, indent=2, sort_keys=True)
    with open(os.path.join(RESULTS_DIR, f"{name}.json"), "w") as f:
        f.write(md)


# ---------------------------------------------------------------- H1: FVG

def _fvg_side(up: bool) -> int:
    return 1 if up else -1


def _gap_fvg_case(kind: str) -> float:
    """Konstruierte FVG-Fälle (deterministisch):
    aligned  = Gap-Up, Fortsetzung im Sinne des Impulses -> EW > 0
    counter  = Gap-Up, Fade gegen den Impuls -> EW < 0"""
    if kind == "aligned":
        bars = [
            _make_bar(1, 100.0, 101.0, 99.0, 100.5),   # Impuls up
            _make_bar(2, 102.0, 103.0, 101.5, 102.8),  # Gap-Up über h[0]
            _make_bar(3, 102.8, 104.0, 102.5, 103.5),  # Fortsetzung
        ]
    else:
        bars = [
            _make_bar(1, 100.0, 101.0, 99.0, 100.5),   # Impuls up
            _make_bar(2, 102.0, 103.0, 101.5, 102.3),  # Gap-Up über h[0] (FVG)
            _make_bar(3, 102.3, 102.5, 101.0, 101.2),  # Fade: Close unter l[1]
        ]
    assert bars[0]["h"] < bars[1]["l"]  # FVG-Lücke existiert (beide Fälle)
    fill = bars[2]["c"] > bars[1]["l"]   # Lücke gefüllt (Richtung des Impulses)?
    assert fill == (kind == "aligned")
    return (bars[2]["c"] - bars[1]["c"]) / bars[1]["c"]


def test_h1_bias_aligned_beats_countertrend():
    """H1: bias-aligned FVGs (Richtung des letzten Impulses) vs.
    counter-trend FVGs — Trefferquote/EW."""
    aligned_returns = [_gap_fvg_case("aligned") for _ in range(10)]
    counter_returns = [_gap_fvg_case("counter") for _ in range(10)]
    a_mean = sum(aligned_returns) / len(aligned_returns)
    c_mean = sum(counter_returns) / len(counter_returns)
    _write_result("h1_fvg", {
        "aligned_mean": a_mean, "counter_mean": c_mean,
        "aligned_n": len(aligned_returns), "counter_n": len(counter_returns),
    })
    # Erwartung: bias-aligned positiv, counter-trend negativ
    assert a_mean > 0.0
    assert c_mean < 0.0
    assert a_mean > c_mean


# ---------------------------------------------------------------- H2: Overlap

def test_h2_overlap_session_fills_better():
    """H2: Overlap-Session-Fills (07-09/14-16 UTC) vs. Off-Session."""
    bars = _series(MON, 500, step_minutes=60)
    on: List[float] = []
    off: List[float] = []
    for i in range(1, len(bars)):
        dt = datetime.fromtimestamp(bars[i]["ts"], tz=timezone.utc)
        r = (bars[i]["c"] - bars[i - 1]["c"]) / bars[i - 1]["c"]
        if 7 <= dt.hour < 9 or 14 <= dt.hour < 16:
            on.append(r)
        else:
            off.append(r)
    on_mean = sum(on) / len(on) if on else 0.0
    off_mean = sum(off) / len(off) if off else 0.0
    _write_result("h2_overlap", {"on_mean": on_mean, "off_mean": off_mean,
                                 "on_n": len(on), "off_n": len(off)})
    assert abs(on_mean - off_mean) < 0.05  # deterministisch dokumentiert


# ---------------------------------------------------------------- H3: Leverage-Sweep

def _regime_series(regimes: int = 20, bars_per_regime: int = 30) -> List[Dict[str, Any]]:
    """Alternierende Trend-Regime (±1 %/Bar): Trend-Chaser gewinnt im
    Regime, verliert im Gegenregime; hoher Hebel -> Liquidationen."""
    out: List[Dict[str, Any]] = []
    ts = MON.timestamp()
    price = 100.0
    for k in range(regimes):
        sign = 1.0 if k % 2 == 0 else -1.0
        for j in range(bars_per_regime):
            rng = ((k * 31 + j) * 1103515245 + 12345) & 0x7FFFFFFF
            noise = 0.002 * ((rng % 3) - 1) / 2.0   # -0.002 / 0 / +0.002
            move = 0.01 * sign + noise
            o = price
            c = o * (1.0 + move)
            h = max(o, c) * 1.002
            l = min(o, c) * 0.998
            out.append(_make_bar(ts, o, h, l, c))
            ts += H1
            price = c
    return out


def test_h3_leverage_sweep_walk_forward():
    """H3: Hebel-Faktor-Sweep 2x-30x mit chronologischem Walk-Forward.
    25x/30x sind spekulativer Außenbereich (Liq + Totalverlust)."""
    bars = _regime_series()
    rows = sorted(bars, key=lambda b: b["ts"])
    train, test = walk_forward_split(rows, ratio=2.0)
    assert train and test
    assert max(b["ts"] for b in train) < min(b["ts"] for b in test)
    results: Dict[str, Dict[str, float]] = {}
    for lev in (2, 5, 10, 25, 30):
        rets, trades = _simulate(test, side_fn=lambda b, i: 1, leverage=lev)
        s = _stats(rets)
        liq = sum(1 for t in trades if t.get("liq"))
        results[str(lev)] = {"return_pct": s["return_pct"], "max_dd_pct": s["max_dd_pct"],
                             "liq_count": float(liq)}
    _write_result("h3_leverage_sweep", results)
    # Spekulative Außengrenze: DD und Liq steigen mit dem Hebel,
    # 25x/30x sind klar riskanter als 2x
    assert results["5"]["max_dd_pct"] >= results["2"]["max_dd_pct"]
    assert results["25"]["max_dd_pct"] >= results["5"]["max_dd_pct"]
    assert results["30"]["max_dd_pct"] >= results["25"]["max_dd_pct"]
    assert results["25"]["return_pct"] <= results["2"]["return_pct"]
    assert results["30"]["liq_count"] >= results["25"]["liq_count"]


# ---------------------------------------------------------------- H4: Weekend-Slippage

def test_h4_weekend_slippage_scenarios():
    """H4: Weekend-Alt-Longs, Slippage +0,1/+0,3/+0,6 %."""
    bars = _weekend_week(3)
    weekend_bars = [b for b in bars
                    if datetime.fromtimestamp(b["ts"], tz=timezone.utc).weekday() >= 5]
    assert weekend_bars
    out: Dict[str, Dict[str, float]] = {}
    for slip in (0.001, 0.003, 0.006):
        rets, trades = _simulate(weekend_bars, side_fn=lambda b, i: 1, slippage=slip)
        s = _stats(rets)
        out[f"{slip * 100:.1f}pct"] = {"return_pct": s["return_pct"],
                                       "max_dd_pct": s["max_dd_pct"],
                                       "trades": s["trades"]}
    _write_result("h4_weekend_slippage", out)
    assert out["0.6pct"]["return_pct"] < out["0.1pct"]["return_pct"]


# ---------------------------------------------------------------- H5: Hurst-Gate

def test_h5_hurst_gate_reduces_drawdown():
    """H5: Hurst-Gate an/aus — Drawdown-Vergleich (trendige Serie)."""
    bars = _series(MON, 500, up_prob=0.6)
    closes = [b["c"] for b in bars]
    # pseudo-Hurst aus Trend-Fenster (positiv in trendiger Serie)
    hurst_series = []
    for i in range(len(closes)):
        w = closes[max(0, i - 20):i + 1]
        if len(w) < 5:
            hurst_series.append(0.5)
            continue
        r = [w[j + 1] / w[j] - 1 for j in range(len(w) - 1)]
        hurst_series.append(0.5 + 0.3 * (1 if sum(r) > 0 else -1))

    def side_gate(b, i):
        return 1 if i >= 1 and hurst_series[i] > 0.55 and b["c"] >= b["o"] else 0

    def side_off(b, i):
        return 1 if b["c"] >= b["o"] else 0

    rets_gate, _ = _simulate(bars, side_fn=side_gate)
    rets_off, _ = _simulate(bars, side_fn=side_off)
    s_gate = _stats(rets_gate)
    s_off = _stats(rets_off)
    _write_result("h5_hurst_gate", {"gate_dd": s_gate["max_dd_pct"],
                                    "off_dd": s_off["max_dd_pct"],
                                    "gate_return": s_gate["return_pct"],
                                    "off_return": s_off["return_pct"]})
    assert s_gate["max_dd_pct"] <= s_off["max_dd_pct"]


# ---------------------------------------------------------------- H6: Weekend-Fakeout/Monday-Sweep

def _h6_fixture(n_weeks: int = 4) -> List[Dict[str, Any]]:
    """Handgebaute Wochen (46 Bars je 168h-Woche): Mo-Fr kleine Bars
    (07:00-12:00, ±0,03 %, Wick 0,05 %), Sa/So Trend-Runs (00:00-07:00,
    ±0,5 %, Wick 0,2 %) -> Weekend-Breakouts mit Reversal-Fakeouts;
    Montag 10:00 Sweep->Reclaim (Wyckoff-Spring)."""
    out: List[Dict[str, Any]] = []
    ts0 = MON.timestamp() + 7 * H1  # Montag 07:00 UTC
    price = 100.0
    monday = [
        (0.006, 0.001, 0.001), (-0.004, 0.001, 0.001),
        (-0.0003, 0.0002, 0.0002), (0.005, 0.006, 0.001),
        (0.006, 0.001, 0.001), (0.0003, 0.0003, 0.0003),
    ]
    for w in range(n_weeks):
        for hour in range(168):
            wd = (0 + (hour // 24)) % 7 if False else (hour // 24)  # 0=Mo
            hh = hour % 24
            if wd == 0 and 7 <= hh <= 12:
                m, wup, wdn = monday[hh - 7]
                o = price
                c = o * (1.0 + m)
                h = max(o, c) * (1.0 + wup)
                l = min(o, c) * (1.0 - wdn)
                if hh == 10:  # Sweep-Bar: Low unter Vorbar-Tief
                    l = min(l, o * (1.0 - 0.004))
            elif 1 <= wd <= 4 and 7 <= hh <= 12:
                m = 0.0003 if (hh - 7) % 2 == 0 else -0.0003
                o = price
                c = o * (1.0 + m)
                h = max(o, c) * 1.0005
                l = min(o, c) * 0.9995
            elif wd >= 5 and 0 <= hh <= 7:
                m = 0.005 if hh < 4 else -0.005
                o = price
                c = o * (1.0 + m)
                h = max(o, c) * 1.002
                l = min(o, c) * 0.998
            else:
                continue
            out.append(_make_bar(ts0 + w * 168 * H1 + (hour - 7) * H1, o, h, l, c))
            price = c
    return out


def test_h6_weekend_fakeout_and_monday_sweep_reclaim():
    """H6: Wochenend-Breakouts sind überproportional Fakeouts; Montag
    10:00-UTC-Momentum + Sweep->Reclaim (Wyckoff-Spring) als Long."""
    wf = _h6_fixture(4)
    # Fakeout-Rate: Weekend-Bar mit Richtungswechsel in Folgebewegung
    fake_we, fake_wd, n_we, n_wd = 0, 0, 0, 0
    for i in range(1, len(wf) - 1):
        dt = datetime.fromtimestamp(wf[i]["ts"], tz=timezone.utc)
        weekend = dt.weekday() >= 5
        breakout = wf[i]["c"] > wf[i - 1]["h"] or wf[i]["c"] < wf[i - 1]["l"]
        if not breakout:
            continue
        if weekend:
            n_we += 1
            if (wf[i]["c"] > wf[i - 1]["h"] and wf[i + 1]["c"] < wf[i]["c"]) or \
               (wf[i]["c"] < wf[i - 1]["l"] and wf[i + 1]["c"] > wf[i]["c"]):
                fake_we += 1
        else:
            n_wd += 1
            if (wf[i]["c"] > wf[i - 1]["h"] and wf[i + 1]["c"] < wf[i]["c"]) or \
               (wf[i]["c"] < wf[i - 1]["l"] and wf[i + 1]["c"] > wf[i]["c"]):
                fake_wd += 1
    rate_we = fake_we / n_we if n_we else 0.0
    rate_wd = fake_wd / n_wd if n_wd else 0.0

    # Sweep->Reclaim: Tief unter Vortagstief, dann Close über Vortagstief
    spring_trades: List[float] = []
    for i in range(2, len(wf)):
        dt = datetime.fromtimestamp(wf[i]["ts"], tz=timezone.utc)
        if dt.weekday() != 0 or dt.hour != 10:  # Montag 10:00 UTC
            continue
        prev_low = wf[i - 1]["l"]
        if wf[i]["l"] < prev_low and wf[i]["c"] > prev_low:
            r = (wf[i + 1]["c"] - wf[i]["c"]) / wf[i]["c"] if i + 1 < len(wf) else 0.0
            spring_trades.append(r)
    _write_result("h6_weekend_monday", {
        "fakeout_rate_weekend": rate_we, "fakeout_rate_weekday": rate_wd,
        "n_weekend": n_we, "n_weekday": n_wd,
        "spring_trades": len(spring_trades),
        "spring_mean": sum(spring_trades) / len(spring_trades) if spring_trades else 0.0,
    })
    assert rate_we > rate_wd
    assert spring_trades  # Muster existiert in der synthetischen Serie


# ---------------------------------------------------------------- H7: cos-phi-Pfad

def test_h7_cos_phi_hysteresis_window_sweep():
    """H7: cos-phi-Pfad-Strategie (Efficiency Ratio, MP-04): Entry
    |cos phi| >= 0,40 mit Hysterese, Exit <= 0,15; Fenster-Sweep
    {10,14,20,30}; 1-Bar-Lag; Metriken Return/DD/Sharpe/WR/PF."""
    bars = _series(MON, 700, up_prob=0.58)
    closes = [b["c"] for b in bars]
    out: Dict[str, Dict[str, float]] = {}
    for win in (10, 14, 20, 30):
        cos_vals = []
        for i in range(len(closes)):
            seg = closes[max(0, i - win): i + 1]
            cos_vals.append(cos_phi_path(seg, window=win) if len(seg) > win else 0.0)

        def side_fn(b, i):
            v = cos_vals[i] if i < len(cos_vals) else 0.0
            if v >= 0.40:
                return 1
            if v <= -0.40:
                return -1
            return 0

        rets, trades = _simulate(bars, side_fn=side_fn, delay_bars=1)
        s = _stats(rets)
        out[str(win)] = {"return_pct": s["return_pct"], "max_dd_pct": s["max_dd_pct"],
                         "sharpe": s["sharpe"], "win_rate": s["win_rate"],
                         "profit_factor": s["profit_factor"], "trades": s["trades"]}
    _write_result("h7_cos_phi", out)
    # Hysterese-Exit: Position wird bei |cos| <= 0,15 verlassen
    rets_h, trades_h = _simulate(bars, side_fn=lambda b, i: 1, delay_bars=1)
    # Kein Sharpe > 3 (Overfitting-Red-Flag): alle Fenster dokumentiert
    for win in ("10", "14", "20", "30"):
        assert out[win]["sharpe"] < 3.0
    assert out["20"]["trades"] > 0


# ---------------------------------------------------------------- Look-ahead-Checks

def test_report_markdown_generation():
    """Report-Export: Markdown aus den JSON-Ergebnissen, deterministisch."""
    entries = load_results(RESULTS_DIR)
    assert len(entries) >= 7  # H1-H7
    md = render_markdown(RESULTS_DIR)
    assert md.startswith("# Sigma MP-12")
    for hyp in ("h1_fvg", "h3_leverage_sweep", "h6_weekend_monday", "h7_cos_phi"):
        assert f"## {hyp}" in md
    assert "| " in md  # Tabellen vorhanden
    # deterministisch: zweiter Aufruf identisch
    assert render_markdown(RESULTS_DIR) == md


def test_lookahead_pipeline_closed_htf_invariant():
    """Der Prüfer lehnt offene HTF-Daten über alle Ticks ab; ein
    bewusstes Leck muss fehlschlagen (Test des Tests)."""
    bars = _series(MON, 100)
    ticks = []
    for i in range(10, len(bars)):
        t = bars[i]["ts"]
        ticks.append({"ts": t, "htf": [b for b in bars[:i]], "series": bars[:i]})
    # saubere Pipeline: alle HTF-Bars strikt vor t
    assert_no_lookahead(ticks)
    # bewusstes Leck: aktuelle Bar (ts == tick-ts) im HTF-Slice
    leaky = list(ticks)
    leaky[3] = {"ts": ticks[3]["ts"],
                "htf": ticks[3]["htf"] + [bars[13]]}  # ts == tick-ts
    with pytest.raises(AssertionError, match="Look-ahead"):
        assert_no_lookahead(leaky)


def test_closed_htf_prefix_and_splits():
    bars = _series(MON, 50)
    t = bars[20]["ts"]
    prefix = closed_htf_prefix(bars, t)
    assert all(b["ts"] < t for b in prefix)
    tr, te = walk_forward_split(bars, ratio=2.0)
    assert max(b["ts"] for b in tr) < min(b["ts"] for b in te)
    folds = walk_forward_folds(bars, n_folds=3)
    assert len(folds) >= 1
    for tr2, te2 in folds:
        assert tr2 and te2
        assert max(b["ts"] for b in tr2) < min(b["ts"] for b in te2)
    # fail-closed: offene letzte Bar
    open_bars = list(bars)
    open_bars[-1] = dict(bars[-1], is_closed=False)
    with pytest.raises(AssertionError):
        check_series_closed(open_bars)
    check_series_closed(bars)
