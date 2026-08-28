"""
=========================================================
Datei:      app/quant/regime_detector.py
Zweck:      §21 / Masterprompt §3.A — 4-Regime & Volatilitaets-Klassifikation.
            EMA50/200-Delta, ATR(14)-Perzentil (100-Bar Rolling), Hurst.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature-Extraktion) / Jaune
=========================================================

Reine Python-Mathematik (kein numpy-Zwang), damit der Detector überall
läuft — Core, Worker, Scout, Tests.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.core import blueprint as bp

Candle = Dict[str, float]      # {ts,o,h,l,c,v}


@dataclass
class RegimeVector:
    regime: str
    ema_delta_pct: float
    atr_percentile: float
    volatility_band: str
    hurst: float
    hurst_class: str
    crisis: bool
    entry_blocked: bool
    sample_size: int
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "ema_delta_pct": round(self.ema_delta_pct, 4),
            "atr_percentile": round(self.atr_percentile, 2),
            "volatility_band": self.volatility_band,
            "hurst": round(self.hurst, 4),
            "hurst_class": self.hurst_class,
            "crisis": self.crisis,
            "entry_blocked": self.entry_blocked,
            "sample_size": self.sample_size,
            **self.details,
        }


# ------------------------------------------------------------------ indicators

def ema(values: Sequence[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(float(v) * k + out[-1] * (1.0 - k))
    return out


def true_ranges(candles: Sequence[Candle]) -> List[float]:
    trs: List[float] = []
    prev_close: Optional[float] = None
    for c in candles:
        high, low, close = float(c["h"]), float(c["l"]), float(c["c"])
        if prev_close is None:
            trs.append(high - low)
        else:
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    return trs


def _rma(values: Sequence[float], period: int) -> List[float]:
    if len(values) < period or period < 1:
        return []
    out: List[float] = []
    window = sum(values[:period]) / period
    out.append(window)
    for v in values[period:]:
        window = (window * (period - 1) + v) / period
        out.append(window)
    return out


def atr_series(candles: Sequence[Candle], period: int = bp.ATR_PERIOD) -> List[float]:
    return _rma(true_ranges(candles), period)


def normalized_atr_series(candles: Sequence[Candle], period: int = bp.ATR_PERIOD) -> List[float]:
    """ATR relativ zum Close DERSELBEN Bar.

    Wichtig: erst je Bar normieren, dann glätten. Andersherum (absolutes ATR
    durch den letzten Close) würde jeder saubere Trend als Vol-Crisis gelten,
    weil das geglättete ATR aus höheren/tieferen Preisen stammt.
    """
    trs = true_ranges(candles)
    ntr = [tr / float(c["c"]) for tr, c in zip(trs, candles) if float(c["c"])]
    return _rma(ntr, period)


def percentile_rank(series: Sequence[float], value: float) -> float:
    """Midrank-Perzentil: konstante Serien landen bei 50, nicht bei 100."""
    if not series:
        return 50.0
    below = sum(1 for s in series if s < value)
    equal = sum(1 for s in series if s == value)
    return 100.0 * (below + 0.5 * equal) / len(series)


def hurst_exponent(closes: Sequence[float], min_lag: int = 2, max_lag: int = 20) -> float:
    """Rescaled-Range-Näherung über Lag-Varianzen (robust, stdlib-only)."""
    n = len(closes)
    if n < max_lag * 2:
        max_lag = max(min_lag + 1, n // 2)
    if n < 8:
        return 0.5
    lags = list(range(min_lag, max(min_lag + 1, max_lag)))
    xs, ys = [], []
    for lag in lags:
        diffs = [closes[i + lag] - closes[i] for i in range(n - lag)]
        if len(diffs) < 2:
            continue
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
        std = math.sqrt(var)
        if std <= 0:
            continue
        xs.append(math.log(lag))
        ys.append(math.log(std))
    if len(xs) < 2:
        return 0.5
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.5
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return max(0.0, min(1.0, slope))


# --------------------------------------------------------------------- detector

class RegimeDetector:
    """Liefert den Regime-Vektor für Judge, Allocator und Academy."""

    def __init__(self, ema_fast: int = bp.EMA_FAST_PERIOD, ema_slow: int = bp.EMA_SLOW_PERIOD,
                 atr_period: int = bp.ATR_PERIOD,
                 percentile_window: int = bp.ATR_PERCENTILE_WINDOW_BARS):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.percentile_window = percentile_window

    def detect(self, candles: Sequence[Candle]) -> RegimeVector:
        closes = [float(c["c"]) for c in candles]
        n = len(closes)
        if n < 5:
            return RegimeVector(bp.Regime.RANGING_CHOP.value, 0.0, 50.0, "NORMAL",
                                0.5, "RANDOM_WALK", False, False, n,
                                {"reason": "insufficient_data"})

        fast = ema(closes, min(self.ema_fast, max(2, n // 2)))
        slow = ema(closes, min(self.ema_slow, max(3, n - 1)))
        ema_delta = ((fast[-1] - slow[-1]) / slow[-1] * 100.0) if slow[-1] else 0.0

        period = min(self.atr_period, max(2, n // 2))
        atrs = atr_series(candles, period)
        normalized = normalized_atr_series(candles, period)[-self.percentile_window:]
        atr_pctl = percentile_rank(normalized, normalized[-1]) if normalized else 50.0
        band = bp.classify_atr_percentile(atr_pctl)

        h = hurst_exponent(closes)
        h_class = bp.classify_hurst(h)

        crisis = atr_pctl >= bp.ATR_PCTL_CRISIS_MIN
        regime = self._classify(ema_delta, band, h_class, crisis)
        return RegimeVector(
            regime=regime, ema_delta_pct=ema_delta, atr_percentile=atr_pctl,
            volatility_band=band, hurst=h, hurst_class=h_class,
            crisis=crisis, entry_blocked=crisis, sample_size=n,
            details={"ema_fast": round(fast[-1], 6), "ema_slow": round(slow[-1], 6),
                     "atr": round(atrs[-1], 8) if atrs else 0.0},
        )

    @staticmethod
    def _classify(ema_delta: float, band: str, hurst_class: str, crisis: bool) -> str:
        if crisis:
            return bp.Regime.HIGH_VOL_CRISIS.value
        trending = hurst_class == "PERSISTENT_TREND"
        strong = abs(ema_delta) >= 1.5
        # Ein klares EMA50/200-Delta schlaegt den Hurst-Hinweis: ein fallender
        # Markt mit zackigem Mikro-Rauschen bleibt ein Baerentrend.
        if not strong and (abs(ema_delta) < 0.25 or hurst_class == "MEAN_REVERSION"):
            return bp.Regime.RANGING_CHOP.value
        if ema_delta > 0:
            return (bp.Regime.STRONG_BULL.value if (strong or trending)
                    else bp.Regime.WEAK_BULL.value)
        return (bp.Regime.STRONG_BEAR.value if (strong or trending)
                else bp.Regime.WEAK_BEAR.value)


_detector: Optional[RegimeDetector] = None


def get_regime_detector() -> RegimeDetector:
    global _detector
    if _detector is None:
        _detector = RegimeDetector()
    return _detector


def detect_regime(candles: Sequence[Candle]) -> Dict[str, Any]:
    return get_regime_detector().detect(candles).to_dict()
