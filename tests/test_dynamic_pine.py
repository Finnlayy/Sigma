from sigma.strategies.dynamic_pine_provisioner import (
    ProvisionRequest, PineHardeningRequest,
    provision_pine_v6, harden_pine_code
)
from sigma.strategies.pine_v6_generator import static_pine_checks

def test_provision_pine_v6_deterministic():
    req1 = ProvisionRequest(
        symbol="BTC/USD", side="buy", entry=60000, stop_loss=59000, take_profit=62000,
        fixed_leverage=5, strategy_id="test_strat_1", webhook_secret="sec1", ttl_minutes=60
    )
    req2 = ProvisionRequest(
        symbol="BTC/USD", side="buy", entry=60000, stop_loss=59000, take_profit=62000,
        fixed_leverage=5, strategy_id="test_strat_1", webhook_secret="sec1", ttl_minutes=60
    )
    code1 = provision_pine_v6(req1)
    code2 = provision_pine_v6(req2)
    assert code1 == code2
    assert "//@version=6" in code1
    assert "initial_capital=10000" in code1
    assert "pyramiding=1" in code1
    assert "barstate.isconfirmed" in code1
    assert "test_strat_1_BUY_01_{{timenow}}" in code1
    assert not static_pine_checks(code1)

def test_provision_pine_v6_fractal():
    req = ProvisionRequest(
        symbol="BTC/USD", side="buy", entry=60000, stop_loss=59000, take_profit=62000,
        fixed_leverage=5, strategy_id="fractal_strat", webhook_secret="sec2", ttl_minutes=60,
        tp1=60500, tp2=61000, tp3=61500
    )
    code = provision_pine_v6(req)
    assert "c_tp1 = 60500" in code
    assert "qty_percent=40" in code
    assert "UPDATE_SL" in code
    assert "fractal_strat_UPDATE_SL_05_" in code
    assert not static_pine_checks(code)

def test_harden_pine_code_v5_upgrade():
    raw_v5 = '''//@version=5
strategy("MyStrat")
if close > open
    strategy.entry("Long", strategy.long, alert_message="some webhook")
'''
    req = PineHardeningRequest(
        raw_code=raw_v5, symbol="BTC/USD", side="buy", entry=60000, stop_loss=59000,
        take_profit=62000, fixed_leverage=5, strategy_id="harden_test", webhook_secret="sec",
        ttl_minutes=120
    )
    res = harden_pine_code(req)
    assert not res.hardening_ok
    assert "missing_bar_close_guard" in res.reasons

def test_harden_pine_code_valid():
    raw_v5 = '''//@version=5
strategy("MyStrat")
if barstate.isconfirmed
    strategy.entry("Long", strategy.long, alert_message="some webhook")
'''
    req = PineHardeningRequest(
        raw_code=raw_v5, symbol="BTC/USD", side="buy", entry=60000, stop_loss=59000,
        take_profit=62000, fixed_leverage=5, strategy_id="harden_test2", webhook_secret="sec",
        ttl_minutes=120
    )
    res = harden_pine_code(req)
    assert res.hardening_ok
    assert "//@version=6" in res.code
    assert "initial_capital=10000" in res.code
    assert "some webhook" not in res.code
    assert "harden_test2_BUY_01_" in res.code

def test_harden_rejects_lookahead_on():
    raw = '''//@version=6
request.security(syminfo.tickerid, "D", close, lookahead=barmerge.lookahead_on)
'''
    req = PineHardeningRequest(
        raw_code=raw, symbol="ETH/USD", side="buy", entry=3000, stop_loss=2900,
        take_profit=3200, fixed_leverage=2, strategy_id="fail_test", webhook_secret="sec",
        ttl_minutes=60
    )
    res = harden_pine_code(req)
    assert not res.hardening_ok
    assert "contains_lookahead_on" in res.reasons

