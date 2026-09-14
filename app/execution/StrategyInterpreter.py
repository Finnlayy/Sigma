"""
=========================================================
Datei:      app/execution/StrategyInterpreter.py
Zweck:      Sichere Archetyp-Signal-Engine (kein eval von User-Code!)
            Erzeugt M8-Proposals aus OHLCV-Candles & Strategie-Parametern.
Knoten:     Rouge (Planung) / Jaune (Code)
=========================================================
Unterstützte Archetypen: sma_cross, ema_trend, rsi_reversion
Der Strategie-Code (aus dem Runner) wird nur zum Archetyp-Erkennen geparst.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.execution.strategy_interpreter")


def detect_archetype(code: str, parameters: Dict[str, Any]) -> str:
    if parameters.get("archetype"):
        return str(parameters["archetype"]).lower()
    c = (code or "").lower()
    if "rsi" in c or "rsi" in str(parameters):
        return "rsi_reversion"
    if "smafast" in c or "sma_fast" in c or "sma" in c:
        return "sma_cross"
    return "ema_trend"


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def _ema_series(values: List[float], period: int) -> List[float]:
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(candles: List[Dict[str, Any]], period: int) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def generate_signal(strategy: Dict[str, Any], candles: List[Dict[str, Any]],
                    price: float) -> Optional[Dict[str, Any]]:
    """Candle-Close -> Signal-Proposal (None = kein Setup)."""
    params = strategy.get("parameters") or {}
    archetype = detect_archetype(strategy.get("code", ""), params)
    if len(candles) < 60:
        return None
    closes = [c["close"] for c in candles]

    entry = float(candles[-1]["close"]) or float(price)
    stop_pct = float(params.get("hardStopPercent", 5.0)) / 100.0

    if archetype == "sma_cross":
        fast_p = int(params.get("smaFast", 12))
        slow_p = int(params.get("smaSlow", 48))
        fast = _sma(closes, fast_p)
        slow = _sma(closes, slow_p)
        fast_prev = _sma(closes[:-1], fast_p)
        slow_prev = _sma(closes[:-1], slow_p)
        if fast is None or slow is None or fast_prev is None or slow_prev is None:
            return None
        golden = fast_prev <= slow_prev and fast > slow
        death = fast_prev >= slow_prev and fast < slow
        if golden:
            stop = entry * (1 - stop_pct)
            tp = entry * (1 + stop_pct * 2.2)
            return _proposal(strategy, "LONG", entry, stop, tp,
                             f"SMA{fast_p}×SMA{slow_p} Golden Cross", archetype)
        if death:
            stop = entry * (1 + stop_pct)
            tp = entry * (1 - stop_pct * 2.2)
            return _proposal(strategy, "SHORT", entry, stop, tp,
                             f"SMA{fast_p}×SMA{slow_p} Death Cross", archetype)
        return None

    if archetype == "rsi_reversion":
        period = int(params.get("rsiPeriod", 14))
        lower = float(params.get("rsiLower", 32))
        upper = float(params.get("rsiUpper", 68))
        rsi_now = _rsi(closes, period)
        if rsi_now is None:
            return None
        if rsi_now < lower:
            stop = entry * (1 - stop_pct)
            tp = entry * (1 + stop_pct * 1.6)
            return _proposal(strategy, "LONG", entry, stop, tp,
                             f"RSI{period} Reversion {rsi_now:.1f} < {lower}", archetype)
        if rsi_now > upper:
            stop = entry * (1 + stop_pct)
            tp = entry * (1 - stop_pct * 1.6)
            return _proposal(strategy, "SHORT", entry, stop, tp,
                             f"RSI{period} Reversion {rsi_now:.1f} > {upper}", archetype)
        return None

    # ema_trend (Default)
    fast_p = int(params.get("trendFastEma", 12))
    slow_p = int(params.get("trendSlowEma", 60))
    fast = _ema_series(closes, fast_p)
    slow = _ema_series(closes, slow_p)
    if len(fast) < 2 or len(slow) < 2 or len(fast) < len(slow):
        return None
    bull = fast[-1] > slow[-1] and fast[-2] <= slow[-2]
    bear = fast[-1] < slow[-1] and fast[-2] >= slow[-2]
    atr = _atr(candles, 14) or entry * 0.01
    if bull:
        stop = entry - 1.5 * atr
        tp = entry + 2.5 * atr
        return _proposal(strategy, "LONG", entry, stop, tp,
                         f"EMA{fast_p}×EMA{slow_p} Bullish Break + ATR1.5", archetype)
    if bear:
        stop = entry + 1.5 * atr
        tp = entry - 2.5 * atr
        return _proposal(strategy, "SHORT", entry, stop, tp,
                         f"EMA{fast_p}×EMA{slow_p} Bearish Break + ATR1.5", archetype)
    return None


def _proposal(strategy: Dict[str, Any], direction: str, entry: float,
              stop: float, tp: float, reason: str, archetype: str) -> Dict[str, Any]:
    return {
        # Instanz-ID = Strategie-ID (M8-Registry-Schlüssel, Blueprint §2)
        "instance_id": strategy["id"],
        "strategy_id": strategy["id"],
        "symbol": strategy.get("assetPair"),
        "timeframe": f"{strategy.get('interval', 15)}m",
        "execution_queue": (strategy.get("executionMode") or "paper").upper(),
        "market_type": "PERP",
        "direction": direction,
        "entry_price": round(entry, 6),
        "stop_loss_price": round(stop, 6),
        "take_profit_price": round(tp, 6),
        "reason": reason,
        "archetype": archetype,
        "proposed_at": _now_iso(),
    }


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
