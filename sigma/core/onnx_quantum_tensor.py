"""
=========================================================
Datei:      sigma/core/onnx_quantum_tensor.py
Zweck:      MP-11 kanonischer 16D-Observation-Tensor (KB §11):
            reine Feature-Formeln (skaleninvariant, geschlossene
            Bars), optionaler onnxruntime-Wrapper (nur wenn
            konfiguriert + importierbar, sonst Fallback),
            deterministische Fallback-Policy, Bar-Level-Lock.
            KEINE Symbole im Tensor (Ranker = Stufe 2), KEINE
            Orders, KEIN Training.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Tensor) / Noir (Fallback fail-closed)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

EPS = 1e-9

# --- Feature-Indizes (KB §11: Kern-9 + Features 10-16) ------------------
FEATURE_NAMES = [
    "cos_phi",     # 1  (C-O)/(H-L+eps)
    "p_norm",      # 2  |C-O|/ATR14
    "q_norm",      # 3  (obere+untere Dochte)/ATR14
    "pos_00",      # 4  tanh((C-open_00)/(2*ATR))
    "m_tangent",   # 5  arctan((C-open_00)/min_since_00)*2/pi
    "p_cal",       # 6  Platt-kalibrierte Polymarket-Wahrscheinlichkeit
    "pos_eq",      # 7  (C-range_low)/(range_high-range_low+eps)
    "d_ce",        # 8  tanh((C-ce50)/ATR)
    "ttl_norm",    # 9  Restminuten der 1h-Bar / 60
    "utc_safe",    # 10 Flag 21:00-22:00-Quarantaene
    "rvol",        # 11 Volumen-Ratio
    "cvd",         # 12 CVD-Absorption (0 ohne L2-Feed)
    "hurst",       # 13 Hurst-Exponent
    "liq_dist",    # 14 Liquidationsdistanz (neutral ohne Daten)
    "thrust",      # 15 Two-Bar-Thrust
    "fvg_touch",   # 16 FVG-Touch
]

UTC_QUARANTINE_START = 21
UTC_QUARANTINE_END = 22
TTL_FLAT_NORM = 0.15       # TTL_norm < 0,15 -> FLAT
P_CAL_LONG = 0.65
P_CAL_SHORT = 0.35
COS_PHI_STRONG = 0.75
ENTROPY_FLAT = 0.65        # unsichere Verteilung -> Zwangs-Flat
LEVERAGE_MIN = 10
LEVERAGE_MAX = 25

ACTION_LONG = "LONG"
ACTION_FLAT = "FLAT"
ACTION_SHORT = "SHORT"


@dataclass(frozen=True)
class TensorContext:
    """Alle Feature-Quellen. Fehlende Quelle -> sicherer Default
    (fail-closed), niemals Exception schlucken/synthetisieren."""

    candles: Sequence[Mapping[str, Any]] = ()          # geschlossene HTF-Bars
    open_00: Optional[float] = None                    # 00:00-UTC-Open
    minutes_since_00: Optional[float] = None
    atr: Optional[float] = None                        # Wilder ATR14
    poly_raw: Optional[float] = None                   # Rohquote (nicht kalibriert)
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    ce50: Optional[float] = None
    ttl_minutes_remaining: Optional[float] = None      # bis 1h-Bar-Close
    utc_hour: Optional[int] = None
    rvol: Optional[float] = None
    cvd_absorption: Optional[float] = None
    hurst: Optional[float] = None
    liq_distance_pct: Optional[float] = None
    thrust: Optional[bool] = None
    fvg_touch: Optional[bool] = None
    leverage: Optional[int] = None  # begrenzter Hebel (keine freie Wahl)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ------------------------------------------------------------------ helpers

def _closed(candles: Sequence[Mapping[str, Any]]) -> list:
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


def platt_scale(p: float, a: float = 1.0, b: float = 0.0) -> float:
    """Platt-Skalierung einer Wahrscheinlichkeit (KB §11 P_cal).
    Default a=1, b=0 -> Identitaet; in [0,1] geklemmt."""
    x = max(1e-12, min(1.0 - 1e-12, float(p)))
    if a == 1.0 and b == 0.0:
        return max(0.0, min(1.0, x))
    logit = math.log(x / (1.0 - x))
    return max(0.0, min(1.0, 1.0 / (1.0 + math.exp(-(a * logit + b)))))


# ---------------------------------------------------------------- features

def cos_phi_feature(candle: Mapping[str, Any]) -> float:
    """(C-O)/(H-L+eps), geclippt [-1,1] (KB §11 Kern 1)."""
    span = _h(candle) - _l(candle) + EPS
    return max(-1.0, min(1.0, (_c(candle) - _o(candle)) / span))


def p_norm_feature(candle: Mapping[str, Any], atr: Optional[float]) -> float:
    """|C-O|/ATR14 (Kern 2), geclippt [0,1] (Tensor-Vertrag); ATR <= 0
    -> 0 (kein NaN)."""
    if atr is None or atr <= 0:
        return 0.0
    return max(0.0, min(1.0, abs(_c(candle) - _o(candle)) / float(atr)))


def q_norm_feature(candle: Mapping[str, Any], atr: Optional[float]) -> float:
    """(obere+untere Dochte)/ATR14 (Kern 3), geclippt [0,1]."""
    if atr is None or atr <= 0:
        return 0.0
    upper = max(0.0, _h(candle) - max(_o(candle), _c(candle)))
    lower = max(0.0, min(_o(candle), _c(candle)) - _l(candle))
    return max(0.0, min(1.0, (upper + lower) / float(atr)))


def pos_00_feature(close: float, open_00: Optional[float], atr: Optional[float]) -> float:
    """tanh((C-open_00)/(2*ATR)) (Kern 4); fehlt 00:00-Anker -> 0."""
    if open_00 is None or atr is None or atr <= 0:
        return 0.0
    return math.tanh((close - float(open_00)) / (2.0 * float(atr)))


def m_tangent_feature(close: float, open_00: Optional[float], minutes_since_00: Optional[float]) -> float:
    """arctan((C-open_00)/min_since_00)*2/pi (Kern 5); fehlt -> 0."""
    if open_00 is None or minutes_since_00 is None or minutes_since_00 <= 0:
        return 0.0
    return math.atan((close - float(open_00)) / float(minutes_since_00)) * 2.0 / math.pi


def p_cal_feature(poly_raw: Optional[float]) -> float:
    """Platt-kalibrierte Poly-Wahrscheinlichkeit in [0,1]; ohne Feed 0
    (neutral, fail-closed, kein Gate)."""
    if poly_raw is None:
        return 0.0
    return platt_scale(float(poly_raw))


def pos_eq_feature(close: float, range_low: Optional[float], range_high: Optional[float]) -> float:
    """(C-range_low)/(range_high-range_low+eps) in [0,1]; fehlt -> 0.5."""
    if range_low is None or range_high is None:
        return 0.5
    denom = float(range_high) - float(range_low) + EPS
    return max(0.0, min(1.0, (close - float(range_low)) / denom))


def d_ce_feature(close: float, ce50: Optional[float], atr: Optional[float]) -> float:
    """tanh((C-ce50)/ATR) (Kern 8); fehlt -> 0."""
    if ce50 is None or atr is None or atr <= 0:
        return 0.0
    return math.tanh((close - float(ce50)) / float(atr))


def ttl_norm_feature(minutes_remaining: Optional[float]) -> float:
    """Restminuten der 1h-Bar / 60, geklemmt [0,1]; fehlt -> 0
    (fail-closed: Policy flattet)."""
    if minutes_remaining is None:
        return 0.0
    return max(0.0, min(1.0, float(minutes_remaining) / 60.0))


def utc_safe_feature(utc_hour: Optional[int]) -> float:
    """1 = sicher; 0 = 21:00-22:00-Quarantaene (oder unbekannt)."""
    if utc_hour is None:
        return 0.0
    return 0.0 if UTC_QUARANTINE_START <= int(utc_hour) < UTC_QUARANTINE_END else 1.0


def rvol_feature(rvol: Optional[float]) -> float:
    """RVOL auf [0,1] (>= 3 -> 1); fehlt -> 0."""
    if rvol is None or rvol <= 0:
        return 0.0
    return max(0.0, min(1.0, float(rvol) / 3.0))


def cvd_feature(cvd_absorption: Optional[float]) -> float:
    """CVD-Absorption in [-1,1]; ohne L2-Feed 0 (fail-closed)."""
    if cvd_absorption is None:
        return 0.0
    return max(-1.0, min(1.0, float(cvd_absorption)))


def hurst_feature(hurst: Optional[float]) -> float:
    """Hurst auf [0,1]: 0.35 -> 0, 0.65 -> 1; fehlt -> 0.5 neutral."""
    if hurst is None:
        return 0.5
    return max(0.0, min(1.0, (float(hurst) - 0.35) / 0.3))


def liq_dist_feature(liq_distance_pct: Optional[float]) -> float:
    """Liq-Distanz auf [0,1] (10 % -> 1); fehlt -> 0.5 neutral."""
    if liq_distance_pct is None:
        return 0.5
    return max(0.0, min(1.0, float(liq_distance_pct) / 0.10))


def bool_feature(value: Optional[bool]) -> float:
    return 1.0 if value else 0.0


# ------------------------------------------------------------------ tensor

def build_observation_tensor(ctx: Optional[TensorContext]) -> "Any":
    """Tensor [1,16] float32; nur geschlossene Bars (letzte geschlossene
    Kerze), alle Werte in definierten Bereichen. Fehlender Kontext ->
    Null-Tensor (fail-closed)."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ist Projekt-Dependency
        raise RuntimeError("numpy wird fuer den Observation-Tensor benoetigt")
    if ctx is None:
        return np.zeros((1, 16), dtype=np.float32)
    closed = _closed(ctx.candles)
    if not closed:
        return np.zeros((1, 16), dtype=np.float32)
    bar = closed[-1]
    close = _c(bar)
    features = [
        cos_phi_feature(bar),
        p_norm_feature(bar, ctx.atr),
        q_norm_feature(bar, ctx.atr),
        pos_00_feature(close, ctx.open_00, ctx.atr),
        m_tangent_feature(close, ctx.open_00, ctx.minutes_since_00),
        p_cal_feature(ctx.poly_raw),
        pos_eq_feature(close, ctx.range_low, ctx.range_high),
        d_ce_feature(close, ctx.ce50, ctx.atr),
        ttl_norm_feature(ctx.ttl_minutes_remaining),
        utc_safe_feature(ctx.utc_hour),
        rvol_feature(ctx.rvol),
        cvd_feature(ctx.cvd_absorption),
        hurst_feature(ctx.hurst),
        liq_dist_feature(ctx.liq_distance_pct),
        bool_feature(ctx.thrust),
        bool_feature(ctx.fvg_touch),
    ]
    return np.asarray([features], dtype=np.float32)


def q_bias_feature(candle: Mapping[str, Any], atr: Optional[float]) -> float:
    """Kauf-Tail-Bias (MP-04): (unterer Docht - oberer Docht)/ATR in
    [-1,1]; nur fuer die Fallback-Policy (Discount/Kauf-Tail)."""
    if atr is None or atr <= 0:
        return 0.0
    upper = max(0.0, _h(candle) - max(_o(candle), _c(candle)))
    lower = max(0.0, min(_o(candle), _c(candle)) - _l(candle))
    return max(-1.0, min(1.0, (lower - upper) / float(atr)))


# ------------------------------------------------------------------ policy

def fallback_action(ctx: Optional[TensorContext]) -> Dict[str, Any]:
    """Deterministische Fallback-Policy (produktiv ohne Modell):
    TTL_norm < 0,15 oder UTC-Quarantaene -> FLAT; P_cal >= 0,65 und
    (cos_phi >= 0,75 oder Discount mit Kauf-Tail) -> LONG; spiegel-
    bildlich SHORT; sonst FLAT. Fail-closed ohne Quellen."""
    if ctx is None:
        return {"action": ACTION_FLAT, "leverage": 0, "reason": "missing_context"}
    closed = _closed(ctx.candles)
    if not closed:
        return {"action": ACTION_FLAT, "leverage": 0, "reason": "missing_bars"}
    bar = closed[-1]
    ttl = ttl_norm_feature(ctx.ttl_minutes_remaining)
    if ttl < TTL_FLAT_NORM:
        return {"action": ACTION_FLAT, "leverage": 0,
                "reason": f"ttl_too_short:{round(ttl, 4)}"}
    if utc_safe_feature(ctx.utc_hour) == 0.0:
        return {"action": ACTION_FLAT, "leverage": 0, "reason": "utc_quarantine"}
    p_cal = p_cal_feature(ctx.poly_raw)
    cos_phi = cos_phi_feature(bar)
    pos_eq = pos_eq_feature(_c(bar), ctx.range_low, ctx.range_high)
    q_bias = q_bias_feature(bar, ctx.atr)
    discount_buy_tail = pos_eq < 0.5 and q_bias > 0.0
    premium_sell_tail = pos_eq > 0.5 and q_bias < 0.0
    lev = LEVERAGE_MIN if ctx.leverage is None else max(LEVERAGE_MIN, min(LEVERAGE_MAX, int(ctx.leverage)))
    if p_cal >= P_CAL_LONG and (cos_phi >= COS_PHI_STRONG or discount_buy_tail):
        return {"action": ACTION_LONG, "leverage": lev, "reason": "fallback_long"}
    if p_cal <= P_CAL_SHORT and (cos_phi <= -COS_PHI_STRONG or premium_sell_tail):
        return {"action": ACTION_SHORT, "leverage": lev, "reason": "fallback_short"}
    return {"action": ACTION_FLAT, "leverage": 0, "reason": "no_conviction"}


# ------------------------------------------------------------------ wrapper

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


class OnnxQuantumTensor:
    """Inferenz-Wrapper: onnxruntime NUR wenn Pfad konfiguriert UND
    importierbar; sonst model_available=False und deterministische
    Fallback-Policy. Bar-Level-Lock: hoechstens eine Aktion je
    Bar-Zeitstempel. Keine Symbole, keine Orders."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        self.model_path = model_path
        self._session = None
        self._bar_lock_ts: Optional[float] = None
        if model_path:
            try:
                import onnxruntime as ort  # type: ignore
                self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            except Exception:
                self._session = None  # fail-closed: Fallback bleibt aktiv

    @property
    def model_available(self) -> bool:
        return self._session is not None

    def evaluate(self, ctx: Optional[TensorContext], *, bar_ts: Optional[float] = None) -> Dict[str, Any]:
        tensor = build_observation_tensor(ctx)
        locked = bar_ts is not None and self._bar_lock_ts == float(bar_ts)
        if self.model_available and self._session is not None:
            out = self._session.run(None, {"tensor_x": tensor})
            action_probs = list(out[0][0])
            leverage_factor = float(out[1][0][0])
            action = self._probs_to_action(action_probs)
            entropy = self._entropy(action_probs)
            if entropy > ENTROPY_FLAT:
                return {"action": ACTION_FLAT, "leverage": 0,
                        "reason": "high_entropy", "model_available": True}
            result: Dict[str, Any] = {
                "action": action,
                "leverage": int(round(LEVERAGE_MIN + (LEVERAGE_MAX - LEVERAGE_MIN) * _sigmoid(leverage_factor))),
                "reason": "model",
                "model_available": True,
                "action_probs": [round(float(x), 6) for x in action_probs],
            }
        else:
            fb = fallback_action(ctx)
            result = {
                "action": fb["action"],
                "leverage": int(fb["leverage"]),
                "reason": fb["reason"],
                "model_available": False,
            }
        if locked:
            return {"action": ACTION_FLAT, "leverage": 0,
                    "reason": "BLOCKED_BY_BAR_LOCK", "model_available": result.get("model_available", False)}
        if result["action"] != ACTION_FLAT and bar_ts is not None:
            self._bar_lock_ts = float(bar_ts)
        return result

    def reset_bar_lock(self) -> None:
        self._bar_lock_ts = None

    @staticmethod
    def _probs_to_action(probs: Sequence[float]) -> str:
        p = [float(x) for x in probs]
        if len(p) != 3:
            return ACTION_FLAT
        idx = max(range(3), key=lambda i: p[i])
        return (ACTION_LONG, ACTION_FLAT, ACTION_SHORT)[idx]

    @staticmethod
    def _entropy(probs: Sequence[float]) -> float:
        h = 0.0
        for p in probs:
            x = max(1e-12, min(1.0, float(p)))
            h -= x * math.log(x)
        return h


__all__ = [
    "ACTION_FLAT",
    "ACTION_LONG",
    "ACTION_SHORT",
    "COS_PHI_STRONG",
    "ENTROPY_FLAT",
    "FEATURE_NAMES",
    "LEVERAGE_MAX",
    "LEVERAGE_MIN",
    "OnnxQuantumTensor",
    "P_CAL_LONG",
    "P_CAL_SHORT",
    "TTL_FLAT_NORM",
    "TensorContext",
    "bool_feature",
    "build_observation_tensor",
    "cos_phi_feature",
    "cvd_feature",
    "d_ce_feature",
    "fallback_action",
    "hurst_feature",
    "liq_dist_feature",
    "m_tangent_feature",
    "p_cal_feature",
    "platt_scale",
    "pos_00_feature",
    "pos_eq_feature",
    "p_norm_feature",
    "q_bias_feature",
    "q_norm_feature",
    "rvol_feature",
    "ttl_norm_feature",
    "utc_safe_feature",
]
