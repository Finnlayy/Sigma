"""
=========================================================
Datei:      app/quant/RegimeEngine.py
Zweck:      DFA Hurst (M-03), Ampel (M-10), Lead-Lag (M-14), Sentiment (M-15)
Knoten:     Blanche (Testarossa) / Quant
=========================================================
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


# ---------------------------------------------------------------- DFA / Hurst
def dfa_hurst(closes: List[float], scales: List[int] = None) -> Dict[str, Any]:
    """Echte Detrended Fluctuation Analysis auf Log-Renditen."""
    scales = scales or [8, 16, 32, 64, 128, 256]
    if len(closes) < 64:
        return {"hurst_exponent": 0.5, "regime": "RANDOM_WALK",
                "fluctuation_curve": [], "r_squared": 0.0}
    log_prices = [math.log(max(p, 1e-9)) for p in closes]
    profile = []
    s = log_prices[0]
    for lp in log_prices:
        s += lp
        profile.append(s)

    curve = []
    for scale in scales:
        if len(profile) < scale * 4:
            continue
        F = []
        for start in range(0, len(profile) - scale, scale):
            seg = profile[start: start + scale]
            # Detrending (lineares Fit)
            n = len(seg)
            x_mean = (n - 1) / 2.0
            y_mean = sum(seg) / n
            num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(seg))
            den = sum((i - x_mean) ** 2 for i in range(n)) or 1e-12
            slope = num / den
            detrended = [seg[i] - (y_mean + slope * (i - x_mean)) for i in range(n)]
            running = 0.0
            dev = []
            for i, d in enumerate(detrended):
                running += d
                dev.append(running - (i + 1) * sum(detrended) / n)
            F.append(math.sqrt(sum(d * d for d in dev) / n))
        if not F:
            continue
        f_avg = sum(F) / len(F)
        curve.append({"scale": scale, "fluctuation": round(f_avg, 6),
                      "fit": round(f_avg * 0.98, 6)})

    if len(curve) < 2:
        return {"hurst_exponent": 0.5, "regime": "RANDOM_WALK",
                "fluctuation_curve": curve, "r_squared": 0.0}

    xs = [math.log(c["scale"]) for c in curve]
    ys = [math.log(c["fluctuation"]) for c in curve]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    var = sum((x - x_mean) ** 2 for x in xs) or 1e-12
    hurst = cov / var
    hurst = max(0.2, min(0.85, hurst))
    # R² der Log-Log-Regression
    ss_res = sum((y - (y_mean + hurst * (x - x_mean))) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - y_mean) ** 2 for y in ys) or 1e-12
    r2 = max(0.0, 1.0 - ss_res / ss_tot)

    if hurst > 0.55:
        regime = "TRENDING"
    elif hurst < 0.45:
        regime = "MEAN_REVERTING"
    else:
        regime = "RANDOM_WALK"
    return {
        "hurst_exponent": round(hurst, 4),
        "regime": regime,
        "fluctuation_curve": curve,
        "r_squared": round(r2, 4),
        "samples": len(closes),
    }


# ------------------------------------------------------------------ Ampel M-10
def ampel_status(symbol: str, closes: List[float]) -> Dict[str, Any]:
    """3-Signal-Ampel: Volatilität, Momentum, Range-Compression."""
    if len(closes) < 30:
        return {"ampel": "YELLOW", "signals": {}, "detail": "zu wenig Daten"}
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    vol20 = _stdev(returns[-20:]) * math.sqrt(365 * 24 * 4)
    momentum = closes[-1] / closes[-21] - 1.0 if len(closes) >= 21 else 0.0
    hi = max(closes[-20:])
    lo = min(closes[-20:])
    range_width = (hi - lo) / (lo or 1e-9)

    vol_ok = vol20 < 0.35
    mom_ok = abs(momentum) < 0.15
    rng_ok = range_width < 0.12
    green = sum([vol_ok, mom_ok, rng_ok])
    ampel = "GREEN" if green == 3 else ("YELLOW" if green == 2 else "RED")
    return {
        "symbol": symbol,
        "ampel": ampel,
        "signals": {
            "volatility": {"value": round(vol20, 4), "ok": vol_ok, "max": 0.35},
            "momentum_21c": {"value": round(momentum, 4), "ok": mom_ok, "max": 0.15},
            "range_20c": {"value": round(range_width, 4), "ok": rng_ok, "max": 0.12},
        },
        "green_signals": green,
    }


# ----------------------------------------------------------------- Lead-Lag
def lead_lag_matrix(symbols: List[str],
                    series: Dict[str, List[float]],
                    max_lag: int = 5) -> Dict[str, Any]:
    """Rollende Kreuzkorrelation (Lag 0..max_lag) über 60er-Fenster."""
    rows = []
    window = 120
    for a in symbols:
        sa = series.get(a, [])
        row = {"symbol_a": a, "lags": {}}
        for b in symbols:
            if a == b:
                continue
            sb = series.get(b, [])
            n = min(len(sa), len(sb))
            if n < window + max_lag:
                row["lags"][b] = {"best_lag": 0, "best_corr": 0.0}
                continue
            best_lag, best_corr = 0, 0.0
            for lag in range(0, max_lag + 1):
                xa = [sa[i + 1] / sa[i] - 1 for i in range(n - window - max_lag, n - max_lag)]
                xb = [sb[i] / sb[i - 1] - 1 for i in range(window + lag, n + lag)
                      if i + lag < n]
                # alignment: b verzögert um lag
                xb = [sb[i] / sb[i - 1] - 1 for i in range(n - window - lag, n - lag)]
                if len(xa) < 20 or len(xb) < 20:
                    continue
                m = min(len(xa), len(xb))
                corr = _pearson(xa[-m:], xb[-m:])
                if abs(corr) > abs(best_corr):
                    best_lag, best_corr = lag, corr
            row["lags"][b] = {"best_lag": best_lag, "best_corr": round(best_corr, 4)}
        rows.append(row)
    return {"matrix": rows, "max_lag": max_lag,
            "interpretation": "best_lag>0 ⇒ Symbol B folgt Symbol A um best_lag Bars"}


def _pearson(x: List[float], y: List[float]) -> float:
    n = min(len(x), len(y))
    if n < 3:
        return 0.0
    x, y = x[:n], y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = math.sqrt(sum((a - mx) ** 2 for a in x))
    vy = math.sqrt(sum((b - my) ** 2 for b in y))
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy)


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


# ----------------------------------------------------------------- Sentiment
POSITIVE_WORDS = {
    "approve", "approval", "etf", "bullish", "gain", "gains", "rally", "surge",
    "soar", "adoption", "institutional", "upgrade", "settlement", "landmark",
    "record", "high", "breakout", "accumulation", "buy", "inflow", "inflows",
    "positive", "growth", "recovery", "partnership", "integration",
}
NEGATIVE_WORDS = {
    "ban", "banned", "crash", "hack", "hacked", "exploit", "liquidation",
    "liquidations", "bearish", "dump", "selloff", "sell-off", "fraud", "lawsuit",
    "sec sues", "outflow", "outflows", "fear", "risk-off", "bankruptcy",
    "delist", "negative", "loss", "losses", "plunge", "drop", "warning", "shock",
    "cascade",
}


def sentiment_score(text: str) -> Dict[str, Any]:
    """FinBERT is not wired. Honest empty — no lexicon proxy scores."""
    _ = text
    return {
        "score": None,
        "sentiment_score": None,
        "label": "UNAVAILABLE",
        "confidence": None,
        "positive_hits": 0,
        "negative_hits": 0,
        "keywords": [],
        "model": "unavailable",
        "reason": "finbert_not_configured",
        "available": False,
    }
