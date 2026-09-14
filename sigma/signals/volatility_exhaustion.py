"""
=========================================================
Datei:      sigma/signals/volatility_exhaustion.py
Zweck:      MP-08 Exhaustion: BBW-Kollaps (5m, skaleninvariant),
            OI-Divergenz, CVD-Flachlinie/Umkehr -> Score 0..1
            + exhausted (KB §8 Regel 7). Sentiment-Saettigung
            nur als mean_reversion_bias-Kontext (KB §4.6) —
            nie ein automatischer Short. Fail-closed ohne Bars;
            fehlende OI/CVD-Feeds -> Teil 0 (kein Renormieren).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Math) / Noir (Fail-Closed)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# --- Konstanten ----------------------------------------------------------
BBW_PERIOD = 20               # Bollinger-Periode (5m)
BBW_STD = 2.0                 # Standardabweichungen
BBW_DAILY_WINDOW = 288        # 288 x 5m = 24h Tagesfenster
BBW_DROP_RATIO = 0.40         # BBW faellt > 40 % vom Tageshoch
OI_DIVERGENCE_BARS = 6        # Preis neues Hoch vs OI fallend (letzte N)
CVD_FLAT_RATIO = 0.001        # CVD-Aenderung <= 0,1 % gilt als flach/umkehr
SCORE_EXHAUSTED = 0.60        # exhausted-Schwelle (gewichtet 0..1)

WEIGHT_BBW = 0.40
WEIGHT_OI = 0.35
WEIGHT_CVD = 0.25

FUNDING_SATURATION = 0.001    # Funding extrem positiv (0,1 % / 8h)
LS_RATIO_SATURATION = 4.0     # Retail-Long > 4:1
SOCIAL_SATURATION = 0.85      # Social bullisch > 85 %
OI_VOLUME_FLAT_RATIO = 1.20   # Spot-Volumen-Ratio <= 1,2 = flach
MIN_SENTIMENT_SIGNALS = 2     # mind. 2 vorliegende Signale muessen saettigen


# ------------------------------------------------------------------ helpers

def _closed_bars(candles: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    rows = list(candles or [])
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _o(c: Mapping[str, Any]) -> float:
    return float(c.get("o", c.get("open", 0.0)) or 0.0)


def _h(c: Mapping[str, Any]) -> float:
    return float(c.get("h", c.get("high", 0.0)) or 0.0)


def _l(c: Mapping[str, Any]) -> float:
    return float(c.get("l", c.get("low", 0.0)) or 0.0)


def _c(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


def _v(c: Mapping[str, Any]) -> float:
    return float(c.get("v", c.get("volume", 0.0)) or 0.0)


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(var)


# ------------------------------------------------------------------ BBW

def bollinger_band_width(
    candles: Sequence[Mapping[str, Any]], *, period: int = BBW_PERIOD, std_mult: float = BBW_STD
) -> List[float]:
    """Rollierende BBW = (upper - lower) / mid (skaleninvariant).
    Leer, wenn weniger als `period` geschlossene Bars vorliegen."""
    closed = _closed_bars(candles)
    closes = [_c(c) for c in closed]
    widths: List[float] = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        mean = sum(window) / period
        sd = _std(window)
        upper = mean + std_mult * sd
        lower = mean - std_mult * sd
        if mean > 0:
            widths.append((upper - lower) / mean)
    return widths


@dataclass(frozen=True)
class BbwVerdict:
    collapsed: bool
    current_width: float
    daily_high_width: float
    drop_ratio: float
    window_bars: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def bbw_collapse(
    candles: Sequence[Mapping[str, Any]],
    *,
    drop_ratio: float = BBW_DROP_RATIO,
    window: int = BBW_DAILY_WINDOW,
) -> BbwVerdict:
    """BBW-Kollaps: aktuelle Bandbreite < Tageshoch x (1 - drop_ratio)."""
    widths = bollinger_band_width(candles)
    if not widths:
        return BbwVerdict(False, 0.0, 0.0, 0.0, 0, "insufficient_bars")
    current = float(widths[-1])
    daily = widths[-min(window, len(widths)):]
    high = max(daily)
    if high <= 0:
        return BbwVerdict(False, current, 0.0, 0.0, len(daily), "zero_width_high")
    ratio = 1.0 - (current / high)
    collapsed = ratio > drop_ratio
    return BbwVerdict(
        collapsed=collapsed,
        current_width=round(current, 10),
        daily_high_width=round(high, 10),
        drop_ratio=round(ratio, 6),
        window_bars=len(daily),
        reason="bbw_collapsed" if collapsed else "bbw_steady",
    )


# ------------------------------------------------------------------ OI

@dataclass(frozen=True)
class OiVerdict:
    diverged: bool
    price_new_high: bool
    oi_falling: bool
    window_bars: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def oi_divergence(
    closes: Sequence[float],
    open_interest: Sequence[float],
    *,
    bars: int = OI_DIVERGENCE_BARS,
) -> OiVerdict:
    """OI-Divergenz: Preis macht neues Hoch (letzte N), OI faellt in
    demselben Fenster (letzter < erster Wert). Fehlende Reihen -> False."""
    if len(closes) < bars + 1 or len(open_interest) < bars + 1:
        return OiVerdict(False, False, False, 0, "insufficient_series")
    c_win = [float(x) for x in closes[-(bars + 1):]]
    oi_win = [float(x) for x in open_interest[-(bars + 1):]]
    if any(x <= 0 for x in c_win) or any(x < 0 for x in oi_win):
        return OiVerdict(False, False, False, len(c_win), "non_positive_values")
    price_new_high = c_win[-1] > max(c_win[:-1])
    oi_falling = oi_win[-1] < oi_win[0]
    diverged = price_new_high and oi_falling
    return OiVerdict(
        diverged=diverged,
        price_new_high=price_new_high,
        oi_falling=oi_falling,
        window_bars=len(c_win),
        reason="oi_divergence" if diverged else "oi_aligned",
    )


# ------------------------------------------------------------------ CVD

@dataclass(frozen=True)
class CvdVerdict:
    flattened: bool
    price_continuing: bool
    cvd_change_ratio: float
    window_bars: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def cvd_reversal(
    closes: Sequence[float],
    cvd_series: Sequence[float],
    *,
    bars: int = OI_DIVERGENCE_BARS,
    flat_ratio: float = CVD_FLAT_RATIO,
) -> CvdVerdict:
    """CVD-Flachlinie/Umkehr trotz Preisfortsetzung: Close steigt im
    Fenster, CVD steigt nicht (Aenderung <= flat_ratio bzw. negativ)."""
    if len(closes) < bars + 1 or len(cvd_series) < bars + 1:
        return CvdVerdict(False, False, 0.0, 0, "insufficient_series")
    c_win = [float(x) for x in closes[-(bars + 1):]]
    cvd_win = [float(x) for x in cvd_series[-(bars + 1):]]
    price_continuing = c_win[-1] > c_win[0]
    base = abs(cvd_win[0])
    if base <= 0:
        change = 0.0 if cvd_win[-1] == cvd_win[0] else 1.0
    else:
        change = (cvd_win[-1] - cvd_win[0]) / base
    flattened = price_continuing and change <= flat_ratio
    return CvdVerdict(
        flattened=flattened,
        price_continuing=price_continuing,
        cvd_change_ratio=round(change, 8),
        window_bars=len(c_win),
        reason="cvd_flattened" if flattened else "cvd_confirms",
    )


# ------------------------------------------------------------------ Score

@dataclass(frozen=True)
class Exhaustion:
    valid: bool
    reason: str
    score: float = 0.0
    exhausted: bool = False
    bbw: Dict[str, Any] = field(default_factory=dict)
    oi: Dict[str, Any] = field(default_factory=dict)
    cvd: Dict[str, Any] = field(default_factory=dict)
    components_available: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def exhaustion_score(
    bars_5m: Optional[Sequence[Mapping[str, Any]]],
    open_interest_series: Optional[Sequence[float]] = None,
    cvd_series: Optional[Sequence[float]] = None,
) -> Exhaustion:
    """Gewichteter Exhaustion-Score 0..1 aus BBW-Kollaps (Pflicht),
    OI-Divergenz und CVD-Flachlinie (optional). Fehlende Reihen tragen
    0 bei (kein Renormieren = fail-closed); ohne geschlossene Bars ist
    der Score ungueltig. exhausted=True nur, wenn der BBW-Kollaps
    vorliegt (Trend ohne BBW-Einbruch bleibt unerschoepft)."""
    closed = _closed_bars(bars_5m or [])
    if not closed:
        return Exhaustion(False, "missing_bars")
    widths = bollinger_band_width(closed)
    if not widths:
        return Exhaustion(False, "insufficient_bars")

    bbw = bbw_collapse(closed)
    score = WEIGHT_BBW * (1.0 if bbw.collapsed else 0.0)
    components = ["bbw"]
    oi_v: Optional[OiVerdict] = None
    cvd_v: Optional[CvdVerdict] = None
    if open_interest_series is not None:
        oi_v = oi_divergence([_c(c) for c in closed], list(open_interest_series))
        score += WEIGHT_OI * (1.0 if oi_v.diverged else 0.0)
        components.append("oi")
    if cvd_series is not None:
        cvd_v = cvd_reversal([_c(c) for c in closed], list(cvd_series))
        score += WEIGHT_CVD * (1.0 if cvd_v.flattened else 0.0)
        components.append("cvd")
    exhausted = bbw.collapsed and score >= SCORE_EXHAUSTED
    return Exhaustion(
        valid=True,
        reason="exhausted" if exhausted else "not_exhausted",
        score=round(score, 6),
        exhausted=exhausted,
        bbw=bbw.to_dict(),
        oi=oi_v.to_dict() if oi_v is not None else {},
        cvd=cvd_v.to_dict() if cvd_v is not None else {},
        components_available=components,
    )


# ------------------------------------------------------- Sentiment (4.6)

@dataclass(frozen=True)
class SentimentSaturation:
    """Reiner Kontext — kein Entry, kein Short. mean_reversion_bias=True
    signalisiert nur Saettigung; ein Mean-Reversion-Trade braucht zusaetz-
    lich die Bar-Close-Bestaetigung (Structure-Shift/Rejection, MP-03)."""

    saturated: bool
    mean_reversion_bias: bool
    funding_saturated: bool
    ls_ratio_saturated: bool
    oi_volume_saturated: bool
    social_saturated: bool
    signals_present: int
    signals_saturated: int

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def sentiment_saturation(
    funding: Optional[float] = None,
    ls_ratio: Optional[float] = None,
    oi_series: Optional[Sequence[float]] = None,
    spot_volume_series: Optional[Sequence[float]] = None,
    social: Optional[float] = None,
) -> SentimentSaturation:
    """Optionaler Saettigungs-Eingang (KB §4.6): Funding extrem positiv,
    Retail-Long > 4:1, OI hoch bei flachem Spot-Volumen, Social > 0,85.
    Ohne Feed -> saturated=False (fail-closed, Teil 0). Mindestens zwei
    vorliegende Signale muessen saettigen, sonst keine Saettigung."""
    funding_sat = funding is not None and float(funding) >= FUNDING_SATURATION
    ls_sat = ls_ratio is not None and float(ls_ratio) >= LS_RATIO_SATURATION
    oi_volume_sat = False
    if oi_series is not None and spot_volume_series is not None:
        oi = [float(x) for x in oi_series]
        vol = [float(x) for x in spot_volume_series]
        if len(oi) >= 3 and len(vol) >= 3 and all(x >= 0 for x in oi) and all(x > 0 for x in vol):
            oi_mean = sum(oi) / len(oi)
            vol_mean = sum(vol) / len(vol)
            oi_volume_sat = oi[-1] > oi_mean and (vol[-1] / vol_mean) <= OI_VOLUME_FLAT_RATIO
    social_sat = social is not None and float(social) >= SOCIAL_SATURATION

    present = sum(
        1 for x in (funding is not None, ls_ratio is not None,
                    (oi_series is not None and spot_volume_series is not None),
                    social is not None) if x
    )
    saturated_count = sum(
        1 for x in (funding_sat, ls_sat, oi_volume_sat, social_sat) if x
    )
    saturated = present >= MIN_SENTIMENT_SIGNALS and saturated_count >= MIN_SENTIMENT_SIGNALS
    return SentimentSaturation(
        saturated=saturated,
        mean_reversion_bias=saturated,
        funding_saturated=funding_sat,
        ls_ratio_saturated=ls_sat,
        oi_volume_saturated=oi_volume_sat,
        social_saturated=social_sat,
        signals_present=present,
        signals_saturated=saturated_count,
    )


__all__ = [
    "BBW_DROP_RATIO",
    "BBW_PERIOD",
    "CVD_FLAT_RATIO",
    "Exhaustion",
    "OI_DIVERGENCE_BARS",
    "SCORE_EXHAUSTED",
    "SentimentSaturation",
    "WEIGHT_BBW",
    "WEIGHT_CVD",
    "WEIGHT_OI",
    "BbwVerdict",
    "bollinger_band_width",
    "bbw_collapse",
    "cvd_reversal",
    "exhaustion_score",
    "oi_divergence",
    "sentiment_saturation",
]
