"""
Loop B/C — TV-Seam: Symbol/Interval-Maps, Scraper-Client, Self-Healing
Selectors, FakeDriver, Job-Queue (Concurrency 1 + Param-Cache), Alerts.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from app.core import blueprint as bp
from app.core.config import load_config
from app.tv.alert_provisioner import AlertProvisioner, build_alert_message
from app.tv.interval_map import (stale_limit_seconds, style_for_interval, to_minutes,
                                 to_scraper_timeframe, to_seconds, to_tv_interval)
from app.tv.scraper_client import (ScraperUnavailable, TradingViewScraperClient, normalize_ohlc)
from app.tv.selector_manager import (BUILTIN_DEFAULT_SELECTORS, SelectorManager, SelectorNotFound)
from app.tv.strategy_tester_driver import FakeStrategyTesterDriver, get_driver
from app.tv.symbol_map import (is_allowed, market_type, notional_limits, split_pair,
                               to_kraken_pair, to_scraper_ticker, to_tradingview)
from app.tv.worker import JOB_KIND_BACKTEST, JOB_KIND_PULL_PARAMS, TvJobQueue


@pytest.fixture()
def cfg(tmp_path):
    c = load_config()
    c.tv_jobs_dir = str(tmp_path / "tv_jobs")
    c.tv_export_dir = str(tmp_path / "tv_exports")
    c.tv_storage_state_path = str(tmp_path / "missing_state.json")
    return c


# --------------------------------------------------------------- symbol map ---

def test_symbol_mapping_roundtrip():
    assert split_pair("BTC/USD") == ("XBT", "USD")
    assert split_pair("KRAKEN:XBTUSD.P") == ("XBT", "USD")
    assert to_kraken_pair("BTC/USD") == "XBTUSD"
    assert to_kraken_pair("ETHUSDT") == "ETHUSD"
    assert to_tradingview("BTC/USD") == "KRAKEN:XBTUSD"
    assert to_tradingview("BTC/USD", futures=True).endswith(".P")
    assert to_scraper_ticker("ETH/USD") == ("KRAKEN", "ETHUSD")


def test_allowlist_and_notional_limits():
    assert is_allowed("BTC/USD") and is_allowed("ETH/USD")
    assert not is_allowed("DOGE/USD")
    assert market_type("KRAKEN:XBTUSD.P") == "FUTURES"
    spot = notional_limits("BTC/USD")
    fut = notional_limits("PI_XBTUSD")
    assert spot["max_order_notional_usd"] == bp.EXCHANGE_SPOT["max_order_notional_usd"]
    assert fut["max_leverage"] == bp.EXCHANGE_FUTURES["max_leverage"] == 5


# ------------------------------------------------------------- interval map ---

def test_interval_conversions():
    assert to_minutes("15m") == to_minutes(15) == to_minutes("15") == 15
    assert to_minutes("4h") == 240 and to_minutes("D") == 1440
    assert to_tv_interval(240) == "240" and to_tv_interval(1440) == "D"
    assert to_scraper_timeframe(60) == "1h" and to_scraper_timeframe(15) == "15m"
    assert to_seconds(15) == 900


def test_style_and_stale_limits():
    assert style_for_interval(1) == "STYLE_MICRO_SCALP"
    assert style_for_interval(15) == "STYLE_INTRADAY_MOMENT"
    assert style_for_interval(240) == "STYLE_SWING_CAMPAIGN"
    assert style_for_interval(1440) == "STYLE_POSITION_INVEST"
    assert stale_limit_seconds(1) == bp.SIGNAL_STALE_MIN_SECONDS      # Floor 120s
    assert stale_limit_seconds(15) == 1800                            # 2 * 900


# ----------------------------------------------------------- scraper client ---

def test_scraper_normalizes_candles(cfg):
    payload = {"ohlc": [
        {"timestamp": 1_700_000_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"timestamp": 1_699_999_000, "open": 1, "high": 2, "low": 0.5, "close": 1.4, "volume": 9},
    ]}
    client = TradingViewScraperClient(cfg, transport=lambda url, params: payload)
    candles = client.fetch_ohlc("BTC/USD", 15, 2)
    assert len(candles) == 2
    assert candles[0]["ts"] < candles[1]["ts"]        # sortiert
    assert candles[1]["ts"] == 1_700_000_000          # ms -> s
    assert set(candles[0]) == {"ts", "o", "h", "l", "c", "v"}


def test_scraper_failure_is_explicit(cfg):
    def boom(url, params):
        raise ScraperUnavailable("connection refused")

    client = TradingViewScraperClient(cfg, transport=boom)
    with pytest.raises(ScraperUnavailable):
        client.fetch_ohlc("BTC/USD")
    assert client.health()["ok"] is False


def test_normalize_handles_bare_list():
    assert normalize_ohlc([{"ts": 1, "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}])[0]["c"] == 2.0


# --------------------------------------------------------- selector manager ---

REMOTE_YAML = """
version: "3.0"
strategy_tester:
  tab:
    - 'button[data-name="remote-healed"]'
"""


def test_selectors_load_local(tmp_path):
    path = tmp_path / "selectors.yaml"
    path.write_text('version: "3.0"\nchart:\n  interval_button:\n    - "#x"\n', encoding="utf-8")
    mgr = SelectorManager(local_path=str(path))
    assert mgr.source == "local_yaml"
    assert mgr.get("chart", "interval_button") == ["#x"]


def test_selectors_self_heal_from_remote(tmp_path):
    path = tmp_path / "missing.yaml"
    mgr = SelectorManager(local_path=str(path), remote_url="https://example/selectors.yaml",
                          fetcher=lambda url: REMOTE_YAML)
    assert mgr.source == "remote_fetch"
    assert "remote-healed" in mgr.get("strategy_tester", "tab")[0]
    assert path.exists()          # atomar persistiert


def test_selectors_fall_back_to_builtin(tmp_path):
    mgr = SelectorManager(local_path=str(tmp_path / "none.yaml"), remote_url="")
    assert mgr.source == "builtin_default"
    assert mgr.get("strategy_tester", "export_button")
    assert set(mgr.snapshot()["categories"]) <= set(BUILTIN_DEFAULT_SELECTORS)


def test_selector_circuit_breaker(tmp_path):
    attempts = {"n": 0}

    def failing(url):
        attempts["n"] += 1
        raise RuntimeError("404")

    mgr = SelectorManager(local_path=str(tmp_path / "none.yaml"),
                          remote_url="https://example/x.yaml", fetcher=failing)
    for _ in range(6):
        mgr.download_remote_selectors()
    assert attempts["n"] <= bp.SELECTOR_MAX_DOWNLOADS + 1     # Breaker greift


def test_selector_format_and_missing(tmp_path):
    mgr = SelectorManager(local_path=str(tmp_path / "none.yaml"), remote_url="")
    assert mgr.get("properties", "input_field", param="atrPeriod")[0].count("atrPeriod") == 1
    with pytest.raises(SelectorNotFound):
        mgr.get("chart", "does_not_exist")


def test_real_selectors_yaml_is_valid():
    mgr = SelectorManager(local_path=bp.PATH_SELECTORS_YAML.lstrip("./"), remote_url="")
    assert mgr.source == "local_yaml"
    for category in ("chart", "strategy_tester", "properties", "pine_editor", "alerts"):
        assert category in mgr.snapshot()["categories"]


# ---------------------------------------------------------------- fake driver ---

def test_fake_driver_contract():
    drv = FakeStrategyTesterDriver()
    drv.open_chart("BTC/USD", 15)
    assert drv.push_pine_code("//@version=6\nstrategy('x')")["compiled"]
    csv_text = drv.export_parameters()
    assert "atrStopMultiplier" in csv_text
    drv.apply_parameters({"atrStopMultiplier": 2.5})
    out = drv.run_backtest({"from": "2024-01-01"})
    assert "Trade #" in out["trades_csv"] and out["source"] == "fake"


def test_get_driver_prefers_fake_without_session(cfg):
    assert isinstance(get_driver(cfg), FakeStrategyTesterDriver)


# ---------------------------------------------------------------- job queue ---

def test_job_queue_runs_backtest_and_caches(cfg):
    q = TvJobQueue(cfg, driver_factory=FakeStrategyTesterDriver)
    job = q.submit(JOB_KIND_BACKTEST, strategy_id="s1", symbol="BTC/USD", interval=15,
                   params={"atrStopMultiplier": 2.0})
    done = q.run_job(job)
    assert done.status == "done"
    assert done.result["backtest"]["trades"]
    assert os.path.exists(done.result["trades_csv"])
    assert done.result["cached"] is False

    again = q.run_job(q.submit(JOB_KIND_BACKTEST, strategy_id="s1", symbol="BTC/USD",
                               interval=15, params={"atrStopMultiplier": 2.0}))
    assert again.result["cached"] is True        # §17.4 Param-Cache Pflicht


def test_job_queue_pull_parameters_and_persistence(cfg):
    q = TvJobQueue(cfg, driver_factory=FakeStrategyTesterDriver)
    job = q.run_job(q.submit(JOB_KIND_PULL_PARAMS, strategy_id="s2", symbol="ETH/USD"))
    assert job.status == "done"
    assert job.result["parameters"]["atrPeriod"] == bp.ATR_PERIOD
    with open(os.path.join(cfg.tv_jobs_dir, f"{job.job_id}.json"), encoding="utf-8") as fh:
        assert json.load(fh)["status"] == "done"


def test_job_queue_cancel_and_snapshot(cfg):
    q = TvJobQueue(cfg, driver_factory=FakeStrategyTesterDriver)
    job = q.submit(JOB_KIND_BACKTEST, strategy_id="s3")
    assert q.cancel(job.job_id)["ok"] is True
    assert q.cancel(job.job_id)["ok"] is False       # nicht mehr queued
    snap = q.snapshot()
    assert snap["concurrency"] == bp.TV_MAX_CONCURRENCY == 1


def test_job_queue_trims_oldest_cache_entries(cfg):
    q = TvJobQueue(cfg, driver_factory=FakeStrategyTesterDriver)
    q._cache = {f"k{i}": {"n": i} for i in range(12)}
    dropped = q.trim_cache(keep=8)
    assert dropped == 4
    assert list(q._cache) == [f"k{i}" for i in range(4, 12)]


def test_job_failure_carries_error_code(cfg):
    class BrokenDriver(FakeStrategyTesterDriver):
        def run_backtest(self, window=None):
            from app.tv.strategy_tester_driver import DriverError

            raise DriverError("selector gone", bp.SELECTOR_ERROR_CODE)

    q = TvJobQueue(cfg, driver_factory=BrokenDriver)
    job = q.run_job(q.submit(JOB_KIND_BACKTEST, strategy_id="s4"))
    assert job.status == "failed" and job.error_code == bp.SELECTOR_ERROR_CODE


# ------------------------------------------------------------------- alerts ---

@pytest.fixture()
def provisioner(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "abc123")
    c = load_config()
    c.tv_jobs_dir = cfg.tv_jobs_dir
    return AlertProvisioner(c, store_path=str(tmp_path / "alerts.json"))


def test_alert_message_carries_secret_and_fields(provisioner):
    msg = json.loads(build_alert_message("s1", "sigma_prod_secure_token_8849"))
    assert msg["secret"] == "sigma_prod_secure_token_8849"
    for key in bp.SIGMA_L4_REQUIRED_FIELDS:
        assert key in msg
    assert msg["features"]["rsi"] == "{{plot_0}}"
    assert msg["stop_loss"] == "{{plot_3}}"
    assert "rsi" not in msg
    assert set(msg).issuperset({"idempotency_key", "bot_id", "fixed_leverage", "execution_mode"})


def test_alert_template_validates_as_schema_a_after_tv_fill():
    from app.server.schemas import SigmaL4AlertPayload

    raw = json.loads(build_alert_message("s1", "sigma_prod_secure_token_8849"))
    filled = {
        **raw,
        "symbol": "XBTUSD",
        "action": "buy",
        "price": 50_000.0,
        "stop_loss": 49_000.0,
        "take_profit": 52_000.0,
        "timestamp": int(time.time()),
        "idempotency_key": "tv_order_abcdef12",
        "interval": "15",
        "features": {"rsi": 28.0, "atr": 500.0, "cisd_score": 0.7},
    }
    alert = SigmaL4AlertPayload.model_validate(filled)
    assert alert.action == "BUY" and alert.stop_loss < alert.price


def test_alert_upsert_is_idempotent(provisioner):
    a = provisioner.upsert("s1", "BTC/USD", 15)
    b = provisioner.upsert("s1", "BTC/USD", 15)
    assert a["name"] == b["name"] == "sigma:s1"
    assert len(provisioner.list()) == 1
    assert b["webhook_url"].endswith(bp.WEBHOOK_INGEST_ROUTE)


def test_alert_m8_matrix_behaviour(provisioner):
    provisioner.upsert("s1", "BTC/USD", 15, enable=True)
    throttled = provisioner.sync_with_m8("s1", "THROTTLED")
    assert throttled["action"] == "keep" and throttled["status"] == "ENABLED"
    assert throttled["budget_multiplier"] == 0.5

    quarantined = provisioner.sync_with_m8("s1", "QUARANTINED")
    assert quarantined["action"] == "disable" and quarantined["status"] == "DISABLED"

    provisioner.sync_with_m8("s1", "ACTIVE")
    assert provisioner.get("s1").enabled is True


def test_alert_reconcile_removes_orphans(provisioner):
    provisioner.upsert("s1", "BTC/USD")
    provisioner.upsert("s2", "ETH/USD")
    out = provisioner.reconcile_alerts(["s1"])
    assert out["orphans_removed"] == ["s2"] and out["active"] == 1


def test_alert_disable_all_on_kill(provisioner):
    provisioner.upsert("s1", "BTC/USD", enable=True)
    provisioner.upsert("s2", "ETH/USD", enable=True)
    provisioner.disable_all("kill_switch")
    assert all(a["status"] == "DISABLED" for a in provisioner.list())
