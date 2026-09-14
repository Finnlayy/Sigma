"""
=========================================================
Datei:      sigma/strategies/pine_v6_generator.py
Zweck:      Pine v6 für HTF-Bias / LTF-Execution. Alerts nur auf
            confirmed bars. MP-09: gemeinsame Sigma-Standard-Header-
            und Bar-Close-Prüf-Helfer für den dynamischen
            Provisionierer (KEINE Upload-/Deploy-Logik).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Pine) / Noir (kein Repaint)
=========================================================
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from app.tv.alert_provisioner import build_alert_message
from app.tv.interval_map import to_tv_interval
from sigma.signals.timeframe_ladder import exec_tf, execution_ladder_tf


def standard_strategy_header(title: str) -> str:
    """Sigma-Standard strategy()-Header (MP-09, Pflicht fuer jede
    generierte/gehaertete Strategie): initial_capital=10000,
    currency.USD, strategy.cash 100, pyramiding=1, 0,04 % Commission,
    calc_on_every_tick=false, overlay=true. Alle Werte injiziert,
    keine freien Variablen."""
    return (
        f'strategy("{title}", overlay=true, initial_capital=10000, '
        f"currency=currency.USD, default_qty_type=strategy.cash, "
        f"default_qty_value=100, pyramiding=1, "
        f"commission_type=strategy.commission.percent, "
        f"commission_value=0.04, calc_on_every_tick=false)"
    )


def static_pine_checks(code: str) -> List[str]:
    """Statische String-Checks (MP-09): lookahead_on verboten,
    request.security mit lookahead_off, Bar-Close-Bedingung vorhanden,
    idempotency_keys eindeutig + Muster. Leere Liste = bestanden."""
    issues: List[str] = []
    if not code or not code.strip():
        return ["empty_code"]
    if "lookahead_on" in code:
        issues.append("contains_lookahead_on")
    for m in re.finditer(r"request\.security\s*\(", code):
        # bis zur schliessenden Klammer der Argumente
        seg = code[m.start(): code.find(")", m.start()) + 1]
        if "lookahead" not in seg:
            issues.append("request_security_without_lookahead_off")
    if "barstate.isconfirmed" not in code and not re.search(r"\[\d+\]", code):
        issues.append("missing_bar_close_guard")
    keys = re.findall(r'"idempotency_key"\s*:\s*"([^"]+)"', code)
    if len(keys) != len(set(keys)):
        issues.append("duplicate_idempotency_keys")
    if keys and not all(re.match(r"^[A-Za-z0-9_]+_[A-Z0-9]+_\d{2}_", k) for k in keys):
        issues.append("idempotency_key_pattern")
    return issues


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


__all__ = [
    "generate_htf_ltf_pine",
    "pine_spec",
    "standard_strategy_header",
    "static_pine_checks",
]
