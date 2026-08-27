"""
=========================================================
Datei:      tests/test_m8_eod_vault.py
Zweck:      Phase-2-Acceptance: Redis SCAN + Quarantäne bei $0 oder
            7-Tage-PF<1, Vault-Sweep & strategy_budgets DuckDB-Sync
Knoten:     Jaune (Carrera-Engine) / Test Suite
=========================================================
"""
import asyncio
import os
import tempfile

import pytest

from app.core.config import AlphaConfig
from app.core.duckdb_store import DuckDBStore
from app.core.event_bus import EventBus, get_event_bus
from app.execution.M8StateEngine import M8StateEngine, StrategyState


@pytest.fixture
def cfg(tmp_path):
    c = AlphaConfig()
    c.data_dir = str(tmp_path)
    c.duckdb_path = str(tmp_path / "test.duckdb")
    c.base_budget_usd = 50.0
    c.vault_sweep_enabled = True
    c.autopsy_order = "v1.2.0"
    c.allow_fakeredis = True
    return c


@pytest.fixture
def store(cfg):
    s = DuckDBStore(cfg.resolved_duckdb_path)
    yield s
    s.close()


# ------------------------------------------------------------------ Phase 2
@pytest.mark.asyncio
async def test_redis_scan_over_m8_state_keys():
    """Phase 2: Redis SCAN über m8:state:* liefert alle registrierten Instanzen."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    redis = fakeredis.FakeRedis(decode_responses=True)
    engine = M8StateEngine(redis)
    await engine.initialize_scripts()
    await engine.register_strategy("STRAT_A__BTC-USDT__15m__PAPER", base_budget_usd=50.0)
    await engine.register_strategy("STRAT_B__ETH-USDT__15m__PAPER", base_budget_usd=50.0)

    states = await engine.scan_states()
    assert "STRAT_A__BTC-USDT__15m__PAPER" in states
    assert "STRAT_B__ETH-USDT__15m__PAPER" in states
    assert states["STRAT_A__BTC-USDT__15m__PAPER"]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_quarantine_at_zero_budget_redis_lua():
    """Phase 2: Budget $0 -> QUARANTINED (Lua, Redis) + Wake-Publish."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    redis = fakeredis.FakeRedis(decode_responses=True)
    engine = M8StateEngine(redis)
    await engine.initialize_scripts()
    iid = "LOSER__BTC-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0)

    result = await engine.update_post_trade_state(iid, pnl_usd=-50.0, trade_id="trd_lose_1")
    assert result["status"] == "QUARANTINED"
    assert float(result["current_budget_usd"]) == 0.0
    assert float(result["budget_multiplier"]) == 0.0


@pytest.mark.asyncio
async def test_quarantine_after_7_low_pf_days():
    """Phase 2: 7 aufeinanderfolgende EOD-Tage mit PF<1 -> QUARANTINED."""
    engine = M8StateEngine(None, AlphaConfig())
    iid = "SLOW_BURN__SOL-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0)

    for day in range(1, 8):
        st = await engine.update_eod_profit_factor(iid, daily_pf=0.82,
                                                   daily_trades_count=4,
                                                   day_label=f"2026-08-{day:02d}")
        if day < 3:
            assert st.status == "ACTIVE"
        elif day < 7:
            assert st.status == "THROTTLED", f"Tag {day} sollte THROTTLED sein"
    assert st.status == "QUARANTINED"
    assert st.consecutive_low_pf_days == 7


@pytest.mark.asyncio
async def test_zero_trade_days_do_not_increment_counter():
    engine = M8StateEngine(None, AlphaConfig())
    iid = "IDLE__XRP-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0)
    st = await engine.update_eod_profit_factor(iid, None, 0, day_label="2026-08-01")
    assert st.consecutive_low_pf_days == 0
    st = await engine.update_eod_profit_factor(iid, 0.4, 3, day_label="2026-08-02")
    assert st.consecutive_low_pf_days == 1
    st = await engine.update_eod_profit_factor(iid, None, 0, day_label="2026-08-03")
    assert st.consecutive_low_pf_days == 1  # kein Trade -> kein Inkrement
    st = await engine.update_eod_profit_factor(iid, 1.4, 5, day_label="2026-08-04")
    assert st.consecutive_low_pf_days == 0  # PF >= 1 -> Reset
    assert st.status == "ACTIVE"


# ------------------------------------------------------------------ Vault
@pytest.mark.asyncio
async def test_vault_sweep_100_percent_above_base(cfg, store):
    """v1.2.0: 100% des Gewinns oberhalb Base wandert in den Vault."""
    engine = M8StateEngine(None, cfg)
    engine.store = store
    iid = "WINNER__BTC-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0)
    # Manuell auf 50 setzen (startet bei Base)
    result = await engine.update_post_trade_state(iid, pnl_usd=15.0, trade_id="trd_win_1")
    assert result["current_budget_usd"] == 50.0  # Budget bleibt auf Base
    vault = store.vault_balance()
    assert abs(vault - 15.0) < 1e-6
    entries = store.vault_entries(10)
    assert len(entries) == 1
    assert entries[0]["amount_usd"] == 15.0
    assert entries[0]["balance_snapshot"] == 15.0


@pytest.mark.asyncio
async def test_vault_sweep_partial_recovery(cfg, store):
    """Budget 40 + 20 Gewinn -> 10 wird zurückgeführt, 10 gesweept."""
    engine = M8StateEngine(None, cfg)
    engine.store = store
    iid = "RECOVER__ETH-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0)
    await engine.update_post_trade_state(iid, pnl_usd=-10.0, trade_id="trd_l1")
    assert engine.get_strategy_state(iid).current_budget_usd == 40.0
    result = await engine.update_post_trade_state(iid, pnl_usd=20.0, trade_id="trd_w1")
    assert result["current_budget_usd"] == 50.0
    assert abs(store.vault_balance() - 10.0) < 1e-6


@pytest.mark.asyncio
async def test_strategy_budgets_write_through(cfg, store):
    """Write-Through: jeder State-Update landet in DuckDB strategy_budgets."""
    engine = M8StateEngine(None, cfg)
    engine.store = store
    iid = "SYNC__SOL-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0)
    await engine.update_post_trade_state(iid, pnl_usd=-8.0, trade_id="trd_s1")
    rows = store.all_budgets()
    row = next(r for r in rows if r["instance_id"] == iid)
    assert abs(row["current_budget_usd"] - 42.0) < 1e-6
    assert row["status"] in ("ACTIVE", "THROTTLED")
    assert row["consecutive_losses"] == 1


# ------------------------------------------------------------------ RETIRED
@pytest.mark.asyncio
async def test_retired_after_four_weeks_shadow():
    import time

    engine = M8StateEngine(None, AlphaConfig())
    iid = "STALE__XRP-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0,
                             last_ga_recalibration_ts=time.time() - 30 * 24 * 3600)
    st = engine.check_retirement(iid)
    assert st.status == "RETIRED"
    # promotion von RETIRED ist terminal blockiert
    with pytest.raises(ValueError):
        await engine.promote(iid)


@pytest.mark.asyncio
async def test_not_retired_after_recent_ga():
    import time

    engine = M8StateEngine(None, AlphaConfig())
    iid = "FRESH__BTC-USDT__15m__PAPER"
    await engine.register_strategy(iid, base_budget_usd=50.0,
                             last_ga_recalibration_ts=time.time() - 5 * 24 * 3600)
    st = engine.check_retirement(iid)
    assert st.status == "ACTIVE"
