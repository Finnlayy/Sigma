"""
=========================================================
Datei:      sigma/strategies/pine_v6_generator.py
Zweck:      Pine v6 für HTF-Bias / LTF-Execution. Alerts nur auf confirmed bars.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Pine) / Noir (kein Repaint)
=========================================================
"""
from __future__ import annotations

from typing import Any, Dict

from app.tv.alert_provisioner import build_alert_message
from app.tv.interval_map import to_tv_interval
from sigma.signals.timeframe_ladder import exec_tf, execution_ladder_tf


def generate_htf_ltf_pine(
    strategy_id: str = "htf_trend_ltf_reversion",
    *,
    bias_minutes: int = 60,
    use_ict_ladder: bool = False,
    enable_fvg_locator: bool = False,
    secret: str = "",
) -> str:
    """Pine v6: HTF request.security bias, LTF entries, barstate.isconfirmed."""
    ltf = (
        execution_ladder_tf(bias_minutes)
        if use_ict_ladder
        else exec_tf(bias_minutes)
    )
    if ltf is None:
        ltf = 15
    htf_code = to_tv_interval(bias_minutes)
    alert = build_alert_message(
        strategy_id, secret, execution_mode="kraken_paper",
    ).replace("'", "\\'")
    fvg_block = ""
    if enable_fvg_locator:
        fvg_block = """
// Research-only FVG locator (H4 default off — not a live gate)
bullFvg = barstate.isconfirmed and high[2] < low
bearFvg = barstate.isconfirmed and low[2] > high
"""
    return f'''//@version=6
strategy("Sigma HTF Trend LTF Reversion", overlay=true, initial_capital=1000, pyramiding=0, calc_on_every_tick=false)

htfTf     = input.timeframe("{htf_code}", "HTF Bias")
atrLen    = input.int(14, "ATR Period")
atrMult   = input.float(1.5, "ATR Stop Multiplier")
useIct    = input.bool({str(use_ict_ladder).lower()}, "Use 12-16x ICT ladder (H3)")
useFvg    = input.bool({str(enable_fvg_locator).lower()}, "FVG locator (H4 research)")

htfClose = request.security(syminfo.tickerid, htfTf, close, lookahead=barmerge.lookahead_off)
htfHigh  = request.security(syminfo.tickerid, htfTf, high, lookahead=barmerge.lookahead_off)
htfLow   = request.security(syminfo.tickerid, htfTf, low, lookahead=barmerge.lookahead_off)
htfClosed = request.security(syminfo.tickerid, htfTf, barstate.isconfirmed, lookahead=barmerge.lookahead_off)

atr = ta.atr(atrLen)
htfBiasUp = htfClose > htfClose[1]
htfBiasDn = htfClose < htfClose[1]
eqPos = htfHigh != htfLow ? (close - htfLow) / (htfHigh - htfLow) : 0.5

// Sweep / reclaim on confirmed LTF bar only
priorHigh = ta.highest(high[1], 5)
priorLow  = ta.lowest(low[1], 5)
sweepLow  = low < priorLow and close > priorLow
sweepHigh = high > priorHigh and close < priorHigh
{fvg_block}
confirmed = barstate.isconfirmed
htfReady  = htfClosed

longCond  = confirmed and htfReady and htfBiasUp and sweepLow
shortCond = confirmed and htfReady and htfBiasDn and sweepHigh
if useFvg
    longCond  := longCond  // FVG does not size or block until H4 passes
    shortCond := shortCond

plot(ta.rsi(close, 14), "rsi", display=display.none)
plot(atr, "atr", display=display.none)
plot(eqPos, "cisd", display=display.none)
plot(close - atr * atrMult, "sl", display=display.none)
plot(close + atr * atrMult * 2, "tp", display=display.none)

if longCond
    strategy.entry("L", strategy.long, alert_message = '{alert}')
    strategy.exit("XL", "L", stop = close - atr * atrMult, limit = close + atr * atrMult * 2)

if shortCond
    strategy.entry("S", strategy.short, alert_message = '{alert}')
    strategy.exit("XS", "S", stop = close + atr * atrMult, limit = close - atr * atrMult * 2)
'''


def pine_spec(strategy_id: str, **kwargs: Any) -> Dict[str, Any]:
    code = generate_htf_ltf_pine(strategy_id, **kwargs)
    return {
        "name": "HTF Trend LTF Reversion",
        "description": "HTF bias + LTF sweep/reclaim. Confirmed bars only. Paper until E graduates.",
        "code": code,
        "assetPair": "BTC/USD",
        "parameters": {
            "template": "htf_trend_ltf_reversion",
            "biasMinutes": int(kwargs.get("bias_minutes") or 60),
            "useIctLadder": bool(kwargs.get("use_ict_ladder") or False),
            "enableFvgLocator": bool(kwargs.get("enable_fvg_locator") or False),
        },
    }
