"""
=========================================================
Datei:      sigma/strategies/dynamic_pine_provisioner.py
Zweck:      Dynamischer Pine-v6-Provisionierer (MP-09).
            Erzeugt deterministische Skripte und haertet fremden Code.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Pine)
=========================================================
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.tv.alert_provisioner import build_alert_payload
from sigma.strategies.pine_v6_generator import standard_strategy_header, static_pine_checks


@dataclass
class ProvisionRequest:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    fixed_leverage: int
    strategy_id: str
    webhook_secret: str
    ttl_minutes: int
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    bar_close_only: bool = True


@dataclass
class PineHardeningRequest:
    raw_code: str
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    fixed_leverage: int
    strategy_id: str
    webhook_secret: str
    ttl_minutes: int
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None


@dataclass
class HardenedPineResult:
    code: str
    transformations: List[str]
    hardening_ok: bool
    reasons: List[str]


def _build_pine_alert(req: ProvisionRequest, action: str, seq: int, reason: str = "") -> str:
    """Builds a Schema A JSON string for a specific alert, replacing dynamic Pine vars."""
    base_payload = build_alert_payload(
        req.strategy_id, req.webhook_secret, execution_mode="kraken_paper", fixed_leverage=req.fixed_leverage
    )
    base_payload["action"] = action
    base_payload["symbol"] = req.symbol
    json_str = json.dumps(base_payload)
    
    # We replace "{{strategy.order.id}}" with:
    pattern = f"{req.strategy_id}_{action}_{seq:02d}_{{{{timenow}}}}"
    json_str = json_str.replace('"{{strategy.order.id}}"', pattern)
    
    # Escape single quotes in the payload for Pine String
    # Wait! json_str does not contain single quotes normally, except our pattern which has them.
    # We DO NOT want to escape the single quotes of our concatenation pattern.
    # But wait! If the entire string is enclosed in single quotes in Pine:
    # alert_message='{...}'
    # Then our pattern '"\' + ...' will close the single quote:
    # alert_message='{"id": "' + "..." + '"}'
    # This is correct and we don't need any other escaping of single quotes unless they are in the values.
    # Since JSON uses double quotes, we are safe.
    return json_str


def provision_pine_v6(req: ProvisionRequest) -> str:
    """Generates a standalone, deterministic Pine v6 script."""
    lines = [
        "//@version=6",
        f"// TTL: {req.ttl_minutes} minutes from provisioning",
        f"// strategy_id: {req.strategy_id}",
        standard_strategy_header(req.strategy_id),
        "",
        "// Injected constants",
        f"c_entry = {req.entry}",
        f"c_sl = {req.stop_loss}",
        f"c_tp = {req.take_profit}",
    ]

    fractal_mode = req.tp1 is not None
    if fractal_mode:
        lines.extend([
            f"c_tp1 = {req.tp1}",
            f"c_tp2 = {req.tp2 or req.take_profit}",
            f"c_tp3 = {req.tp3 or req.take_profit}",
        ])

    lines.extend([
        "",
        "confirmed = barstate.isconfirmed",
        f"condition = confirmed and close {'<' if req.side.lower() == 'buy' else '>'} c_entry // Example condition",
        "",
    ])

    alert_entry = _build_pine_alert(req, "BUY" if req.side.lower() == "buy" else "SELL", 1)
    alert_sl = _build_pine_alert(req, "CLOSE", 99, "SL")

    if fractal_mode:
        alert_tp1 = _build_pine_alert(req, "CLOSE", 2, "TP1")
        alert_tp2 = _build_pine_alert(req, "CLOSE", 3, "TP2")
        alert_tp3 = _build_pine_alert(req, "CLOSE", 4, "TP3")
        alert_update_sl = _build_pine_alert(req, "UPDATE_SL", 5, "TP1_HIT_FEE_COVERED_BREAKEVEN")
        
        sl_multiplier = 1.0005 if req.side.lower() == "buy" else 0.9995
        new_sl_val = req.entry * sl_multiplier

        lines.extend([
            "if condition",
            f"    strategy.entry('Entry', strategy.long if {str(req.side.lower() == 'buy').lower()} else strategy.short, alert_message='{alert_entry}')",
            f"    strategy.exit('TP1', 'Entry', qty_percent=40, limit=c_tp1, stop=c_sl, alert_message='{alert_tp1}')",
            f"    strategy.exit('TP2', 'Entry', qty_percent=30, limit=c_tp2, stop=c_sl, alert_message='{alert_tp2}')",
            f"    strategy.exit('TP3', 'Entry', qty_percent=20, limit=c_tp3, stop=c_sl, alert_message='{alert_tp3}')",
            f"    strategy.exit('Runner', 'Entry', qty_percent=10, stop=c_sl, alert_message='{alert_sl}')",
            "",
            "// UPDATE_SL alert after TP1",
            "var bool tp1_hit = false",
            "if not tp1_hit and strategy.position_size != 0",
            f"    if ({str(req.side.lower() == 'buy').lower()} and high >= c_tp1) or ({str(req.side.lower() == 'sell').lower()} and low <= c_tp1)",
            "        tp1_hit := true",
            f"        alert('{alert_update_sl}')",
        ])
    else:
        alert_tp = _build_pine_alert(req, "CLOSE", 2, "TP")
        lines.extend([
            "if condition",
            f"    strategy.entry('Entry', strategy.long if {str(req.side.lower() == 'buy').lower()} else strategy.short, alert_message='{alert_entry}')",
            f"    strategy.exit('Exit', 'Entry', limit=c_tp, stop=c_sl, alert_message='{alert_tp}')",
        ])

    return "\n".join(lines)


def harden_pine_code(req: PineHardeningRequest) -> HardenedPineResult:
    """Auto-hardens foreign Pine scripts to meet Sigma standards."""
    code = req.raw_code
    transforms = []
    reasons = []

    # 1. Version
    if "//@version=5" in code:
        code = code.replace("//@version=5", "//@version=6")
        transforms.append("Upgraded //@version=5 to //@version=6")
    elif "//@version=6" not in code:
        code = "//@version=6\n" + code
        transforms.append("Added //@version=6")

    # 2. Hard-fail conditions
    if "lookahead_on" in code:
        reasons.append("contains_lookahead_on")
    if "calc_on_every_tick=true" in code:
        reasons.append("calc_on_every_tick=true not rewritable")

    # 3. Fix request.security
    sec_pattern = re.compile(r"(request\.security\([^)]+)\)")
    def sec_repl(m):
        inner = m.group(1)
        if "lookahead" not in inner:
            transforms.append("Added lookahead_off to request.security")
            return inner + ", lookahead=barmerge.lookahead_off)"
        return m.group(0)
    code = sec_pattern.sub(sec_repl, code)

    # 4. Replace strategy header
    head_pattern = re.compile(r"strategy\s*\([^)]+\)")
    if head_pattern.search(code):
        code = head_pattern.sub(standard_strategy_header(req.strategy_id), code)
        transforms.append("Replaced strategy header with Sigma standard")
    else:
        code = code.replace("//@version=6", "//@version=6\n" + standard_strategy_header(req.strategy_id))
        transforms.append("Injected Sigma standard strategy header")

    # 5. Inject idempotency into existing alerts
    pr = ProvisionRequest(
        symbol=req.symbol, side=req.side, entry=req.entry, stop_loss=req.stop_loss,
        take_profit=req.take_profit, fixed_leverage=req.fixed_leverage,
        strategy_id=req.strategy_id, webhook_secret=req.webhook_secret,
        ttl_minutes=req.ttl_minutes, tp1=req.tp1, tp2=req.tp2, tp3=req.tp3
    )

    alert_idx = 1
    def alert_msg_repl(m):
        nonlocal alert_idx
        action = "BUY" if req.side.lower() == "buy" else "SELL"
        if "exit" in m.group(0) or "close" in m.group(0):
            action = "CLOSE"
        replacement = f"alert_message='{_build_pine_alert(pr, action, alert_idx)}'"
        alert_idx += 1
        return replacement

    old_code = code
    code = re.sub(r"alert_message\s*=\s*(['\"]).*?\1", alert_msg_repl, code)
    if code != old_code:
        transforms.append("Replaced foreign alert_message payloads with Schema A")

    if "barstate.isconfirmed" not in code and not re.search(r"\[\d+\]", code):
        reasons.append("missing_bar_close_guard")

    ok = len(reasons) == 0
    return HardenedPineResult(code=code, transformations=transforms, hardening_ok=ok, reasons=reasons)

