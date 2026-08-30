"""
=========================================================
Datei:      tests/test_dynamic_pine.py
Zweck:      MP-09 dynamischer Pine-Provisionierer: deterministische
            v6-Scripts, Sigma-Standard-Header, eindeutige
            idempotency_keys, Bar-Close/lookahead_off, Fraktal-TPs,
            Haertung fremder Pine-Skripte (v5->v6, Webhook-Ersatz,
            fail-closed). Kein Netz, kein Upload.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Test)
=========================================================
"""
from __future__ import annotations

import json
import re

import pytest

from sigma.strategies.dynamic_pine_provisioner import (
    FEE_COVERED_BE_OFFSET,
    RUNNER_QTY_PCT,
    TP1_QTY_PCT,
    TP2_QTY_PCT,
    TP3_QTY_PCT,
    HardenedPineResult,
    PineHardeningRequest,
    ProvisionRequest,
    de_provision_hint,
    generate_dynamic_pine,
    harden_pine_code,
    idempotency_key,
)
from sigma.strategies.pine_v6_generator import (
    standard_strategy_header,
    static_pine_checks,
)

V6 = "//@version=6"


def _req(**over):
    base = dict(
        symbol="BTC/USD",
        strategy_id="sniper_demo",
        side="buy",
        entry=100.0,
        stop_loss=95.0,
        take_profit=108.0,
        fixed_leverage=5,
        webhook_secret="",
        ttl_minutes=120,
        generated_ts=12345,
    )
    base.update(over)
    return ProvisionRequest(**base)


def _keys(code: str) -> list:
    return re.findall(r'"idempotency_key"\s*:\s*"([^"]+)"', code)


def _payloads(code: str) -> list:
    return re.findall(r"alert_message\s*=\s*'(\{.*?\})'", code, re.DOTALL) + \
        re.findall(r"alert\('(\{.*?\})',", code)


# ------------------------------------------------------------- generator

def test_standard_header_constants_present():
    code = generate_dynamic_pine(_req())
    assert "initial_capital=10000" in code
    assert "default_qty_type=strategy.cash" in code
    assert "default_qty_value=100" in code
    assert "pyramiding=1" in code
    assert "commission_type=strategy.commission.percent" in code
    assert "commission_value=0.04" in code
    assert "calc_on_every_tick=false" in code
    assert "currency=currency.USD" in code
    assert code.startswith(V6)
    # Standard-Header-Helfer identisch
    assert standard_strategy_header("x").startswith('strategy("x"')


def test_schema_a_fields_and_alert_messages():
    code = generate_dynamic_pine(_req())
    for field in ("action", "ticker", "price", "stop_loss", "take_profit",
                  "fixed_leverage", "strategy_id", "secret"):
        assert f'"{field}"' in code
    assert "alert_message" in code
    # CLOSE-Payload hat action CLOSE
    close_payload = [p for p in _payloads(code) if '"CLOSE"' in p]
    assert close_payload, "CLOSE payload fehlt"
    assert json.loads(close_payload[0].replace("\\'", "'"))["action"] == "CLOSE"


def test_unique_idempotency_keys_pattern():
    code = generate_dynamic_pine(_req())
    keys = _keys(code)
    assert len(keys) >= 2
    assert len(keys) == len(set(keys))
    for k in keys:
        assert re.match(r"^sniper_demo_(BUY|CLOSE)_\d{2}_", k), k
    # Helper
    assert idempotency_key("s", "BUY", 1) == "s_BUY_01_{{timenow}}"
    assert idempotency_key("s", "CLOSE", 6) == "s_CLOSE_06_{{timenow}}"


def test_fractal_mode_tps_and_update_sl():
    code = generate_dynamic_pine(_req(tp1=102.0, tp2=105.0, tp3=108.0))
    entry_payload = [p for p in _payloads(code) if '"BUY"' in p][0]
    data = json.loads(entry_payload.replace("\\'", "'"))
    assert data["tp1"] == {"price": 102.0, "qty_pct": TP1_QTY_PCT}
    assert data["tp2"] == {"price": 105.0, "qty_pct": TP2_QTY_PCT}
    assert data["tp3"] == {"price": 108.0, "qty_pct": TP3_QTY_PCT}
    assert data["runner_qty_pct"] == RUNNER_QTY_PCT
    assert data["fee_covered_be_offset"] == FEE_COVERED_BE_OFFSET
    update = [p for p in _payloads(code) if '"UPDATE_SL"' in p][0]
    upd = json.loads(update.replace("\\'", "'"))
    assert upd["new_sl"] == pytest.approx(100.0 * 1.0005)  # long: entry x 1,0005
    assert upd["reason"] == "TP1_HIT_FEE_COVERED_BREAKEVEN"
    # short: entry x 0,9995
    short_code = generate_dynamic_pine(_req(side="sell", tp1=98.0, tp2=95.0, tp3=92.0))
    upd_s = [p for p in _payloads(short_code) if '"UPDATE_SL"' in p][0]
    assert json.loads(upd_s.replace("\\'", "'"))["new_sl"] == pytest.approx(100.0 * 0.9995)
    # alle 6 Alert-Keys paarweise verschieden
    keys = _keys(code)
    assert len(keys) == len(set(keys)) == 6
    seqs = sorted(int(re.search(r"_(\d{2})_", k).group(1)) for k in keys)
    assert seqs == [1, 2, 3, 4, 5, 6]


def test_no_lookahead_and_bar_close_guard():
    code = generate_dynamic_pine(_req())
    assert "lookahead_on" not in code
    assert "lookahead=barmerge.lookahead_off" in code
    assert "barstate.isconfirmed" in code
    assert static_pine_checks(code) == []


def test_different_requests_different_scripts_and_deterministic():
    a = generate_dynamic_pine(_req(strategy_id="sniper_a", symbol="SOL/USD", entry=50.0))
    b = generate_dynamic_pine(_req(strategy_id="sniper_b", symbol="ETH/USD", entry=3000.0))
    assert a != b
    c = generate_dynamic_pine(_req())
    d = generate_dynamic_pine(_req())
    assert c == d  # deterministisch


def test_de_provision_hint():
    assert de_provision_hint(_req()) == "sigma:sniper_demo:BTC/USD"


# ------------------------------------------------------------- hardening

V5_RAW = """//@version=5
strategy("Gemini Draft", overlay=true, initial_capital=100000, default_qty_type=strategy.percent, default_qty_value=10, pyramiding=10, commission_type=strategy.commission.percent, commission_value=0.1, calc_on_every_tick=true)

emaFast = ta.ema(close, 9)
emaSlow = ta.ema(close, 21)
longCond = close > emaFast and emaFast > emaSlow
shortCond = close < emaFast and emaFast < emaSlow

if longCond
    strategy.entry("L", strategy.long)
if shortCond
    strategy.entry("S", strategy.short)
"""


def _hreq(raw=V5_RAW, **over):
    base = dict(
        raw_code=raw, symbol="BTC/USD", strategy_id="gem", side="buy",
        entry=100.0, stop_loss=95.0, take_profit=108.0, fixed_leverage=5,
        webhook_secret="", ttl_minutes=60, generated_ts=999,
    )
    base.update(over)
    return PineHardeningRequest(**base)


def test_harden_v5_to_v6_with_schema_a_header_and_guards():
    res = harden_pine_code(_hreq())
    assert res.hardening_ok is True
    code = res.code
    assert code.startswith("//@version=6") or "//@version=6" in code.split("\n")[2]
    assert "//@version=5" not in code
    for token in ("initial_capital=10000", "default_qty_type=strategy.cash",
                  "default_qty_value=100", "pyramiding=1",
                  "commission_type=strategy.commission.percent",
                  "commission_value=0.04", "calc_on_every_tick=false"):
        assert token in code
    assert "gem" in code and "<SIGMA_WEBHOOK_SECRET>" in code
    assert "barstate.isconfirmed" in code
    assert "lookahead_on" not in code
    # Transformationen dokumentieren die Ueberschreibungen
    joined = "|".join(res.transformations)
    assert "version_upgrade:v5->v6" in joined
    assert "header_overwrite:initial_capital->10000" in joined
    assert "header_overwrite:pyramiding->1" in joined
    assert "header_overwrite:calc_on_every_tick->false" in joined
    assert "alert_message_injected:entry_long" in joined
    assert "alert_message_injected:entry_short" in joined
    # eindeutige Keys je Alert
    keys = _keys(code)
    assert len(keys) == len(set(keys)) == 2
    assert any(k.startswith("gem_BUY_01_") for k in keys)
    assert any(k.startswith("gem_SELL_01_") for k in keys)
    assert static_pine_checks(code) == []


def test_harden_foreign_webhook_replaced_no_foreign_url():
    raw = """//@version=6
strategy("Fremd", overlay=true)
if close > ta.ema(close, 9)
    strategy.entry("L", strategy.long, alert_message = '{\"url\":\"https://evil.example.com/hook\"}')
    alert('{\"url\":\"https://evil.example.com/hook\"}', alert.freq_once_per_bar_close)
"""
    res = harden_pine_code(_hreq(raw=raw))
    assert res.hardening_ok is True
    assert "evil.example.com" not in res.code
    joined = "|".join(res.transformations)
    assert "foreign_alert_message_removed" in joined
    assert "foreign_alert_call_removed" in joined
    assert "alert_message_injected:entry_long" in joined


def test_harden_request_security_lookahead_added():
    raw = """//@version=6
strategy("Sec", overlay=true)
htf = request.security(syminfo.tickerid, '60', close)
if close > htf
    strategy.entry("L", strategy.long)
"""
    res = harden_pine_code(_hreq(raw=raw))
    assert res.hardening_ok is True
    assert "lookahead=barmerge.lookahead_off" in res.code
    assert "lookahead_on" not in res.code
    assert any("lookahead_off_added" in t for t in res.transformations)


def test_harden_lookahead_on_replaced():
    raw = """//@version=6
strategy("Sec", overlay=true)
htf = request.security(syminfo.tickerid, '60', close, lookahead=barmerge.lookahead_on)
if close > htf
    strategy.entry("L", strategy.long)
"""
    res = harden_pine_code(_hreq(raw=raw))
    assert res.hardening_ok is True
    assert "lookahead_on" not in res.code
    assert any("lookahead_on_replaced" in t for t in res.transformations)


def test_harden_intrabar_fail_closed():
    raw = """//@version=6
strategy("Intrabar", overlay=true, calc_on_every_tick=true)
if barstate.isrealtime and close > ta.ema(close, 9)
    strategy.entry("L", strategy.long)
"""
    res = harden_pine_code(_hreq(raw=raw))
    assert res.hardening_ok is False
    assert "intrabar_not_wrappable" in res.reasons
    assert res.code == ""  # kein Einsatzcode


def test_harden_python_source_fail_closed():
    res = harden_pine_code(_hreq(raw="import pandas as pd\ndef f():\n    pass"))
    assert res.hardening_ok is False
    assert "python_source_not_pine" in res.reasons


def test_harden_empty_fail_closed():
    res = harden_pine_code(_hreq(raw="   "))
    assert res.hardening_ok is False
    assert res.reasons


def test_harden_deterministic():
    a = harden_pine_code(_hreq())
    b = harden_pine_code(_hreq())
    assert a.code == b.code
    assert a.transformations == b.transformations


def test_harden_missing_version_added():
    raw = """strategy("NoVersion", overlay=true)
if close > ta.ema(close, 9)
    strategy.entry("L", strategy.long)
"""
    res = harden_pine_code(_hreq(raw=raw))
    assert res.hardening_ok is True
    assert res.code.startswith("//@version=6")
    assert any("version_added:v6" in t for t in res.transformations)


def test_harden_no_barstate_wraps_if_condition():
    raw = """//@version=6
strategy("NoGuard", overlay=true)
if close > ta.ema(close, 9)
    strategy.entry("L", strategy.long)
"""
    res = harden_pine_code(_hreq(raw=raw))
    assert res.hardening_ok is True
    assert "barstate.isconfirmed" in res.code
    assert any("barstate_isconfirmed_added" in t for t in res.transformations)
    assert "if (barstate.isconfirmed and (close > ta.ema(close, 9)))" in res.code
