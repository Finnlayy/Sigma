"""
Spec-Freeze Tests — app/core/blueprint.py muss deckungsgleich sein mit
docs/BLUEPRINT-SIGMA.md, docs/MASTERPROMPT.md und config/autonomy-level-4.yaml.

Diese Suite ist das Noir-Gate: sie verhindert Drift zwischen Dokument,
YAML und hart verdrahtetem Code.
"""
from __future__ import annotations

import os
import re

import pytest

from app.core import blueprint as bp
from app.core import l4_config
from app.core.config import load_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUEPRINT_MD = os.path.join(ROOT, "docs", "BLUEPRINT-SIGMA.md")
MASTERPROMPT_MD = os.path.join(ROOT, "docs", "MASTERPROMPT.md")
L4_YAML = os.path.join(ROOT, "config", "autonomy-level-4.yaml")


@pytest.fixture(scope="module")
def blueprint_text() -> str:
    with open(BLUEPRINT_MD, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def masterprompt_text() -> str:
    with open(MASTERPROMPT_MD, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def yaml_cfg():
    return l4_config.load_l4_config(L4_YAML)


# ---------------------------------------------------------------- identity ---

def test_freeze_header_matches_docs(blueprint_text, masterprompt_text):
    # docs/ traegt den eingefrorenen Spec-Stand, blueprint.py den implementierten.
    assert f"Canonical Spec Freeze v{bp.DOCS_BLUEPRINT_VERSION}" in blueprint_text
    assert bp.DOCS_MASTERPROMPT_VERSION in masterprompt_text
    assert bp.BLUEPRINT_VERSION == "3.0"
    assert bp.MASTERPROMPT_VERSION.startswith("3.")
    assert bp.AUTONOMY_LEVEL == 4
    assert bp.INSTALL_ROOT in masterprompt_text
    assert bp.HOST_OS == "ubuntu"


def test_pending_doc_sections_are_declared(blueprint_text):
    """Was in docs/ steht, aber noch nicht implementiert ist, muss benannt sein."""
    assert bp.DOCS_PENDING_SECTIONS, "pending sections must be listed explicitly"
    for section in bp.DOCS_PENDING_SECTIONS:
        number = section.split(" ", 1)[0]
        assert f"## {number}." in blueprint_text


def test_five_loops_present():
    assert [loop.value for loop in bp.Loop] == ["A", "B", "C", "D", "E"]
    assert bp.LOOPS[bp.Loop.D_SCOUT].title.startswith("Scout")
    assert "Academy" in bp.LOOPS[bp.Loop.E_ACADEMY].title


def test_axioms_and_rejections():
    assert bp.AXIOM_STRATEGY_IS_TRADINGVIEW is True
    assert len(bp.AXIOMS) == 3
    assert any("Windows" in item for item in bp.REJECTED_ARTIFACTS)
    assert any("BacktestEngine" in item for item in bp.REJECTED_ARTIFACTS)


# ------------------------------------------------------------------- ports ---

def test_ports_match_blueprint(blueprint_text):
    assert (bp.PORT_CORE, bp.PORT_SCRAPER, bp.PORT_REDIS, bp.PORT_UI_DEV) == (8000, 8001, 6379, 3000)
    assert bp.PORT_OLLAMA == 11434
    for port in (8000, 8001, 6379, 3000):
        assert str(port) in blueprint_text
    names = {proc.name for proc in bp.PROCESSES}
    assert {"sigma-core", "sigma-tv-scraper", "sigma-tv-worker"} <= names


def test_canonical_paths(blueprint_text):
    for path in (bp.PATH_KILL_SWITCH, bp.PATH_PAUSE, bp.PATH_TV_STORAGE_STATE, bp.PATH_LOG_ORDERS):
        assert path.lstrip("./") in blueprint_text


# -------------------------------------------------------------- risk / loop A ---

def test_risk_guard_matches_yaml(yaml_cfg):
    rg = yaml_cfg["risk_guard"]
    assert rg["max_open_positions"] == bp.RISK_GUARD["max_open_positions"] == 4
    assert rg["max_daily_loss_usd"] == bp.RISK_GUARD["max_daily_loss_usd"] == 600
    assert rg["max_consecutive_errors"] == 3
    assert rg["max_spread_bps"] == 45
    assert rg["kelly_fraction"] == bp.KELLY_FRACTION == 0.5
    assert rg["max_portfolio_risk_per_trade"] == bp.MAX_PORTFOLIO_RISK_PER_TRADE == 0.10


def test_pipeline_order_is_normative():
    assert bp.LOOP_A_PIPELINE[0] == "SafetyGuard.check"
    assert bp.LOOP_A_PIPELINE.index("JudgeEngine.evaluate") < bp.LOOP_A_PIPELINE.index(
        "execute_kraken_cli_or_paper"
    )
    assert bp.LOOP_A_PIPELINE[-1] == "m8.update_post_trade_state"
    assert len(bp.LOOP_A_PIPELINE) == 10


def test_webhook_contract(blueprint_text):
    assert bp.WEBHOOK_ROUTE == "/api/v1/signal/webhook"
    assert bp.WEBHOOK_ROUTE in blueprint_text
    assert bp.WEBHOOK_SECRET_ENV in blueprint_text
    assert bp.WEBHOOK_UNAUTHORIZED_STATUS == 401
    assert "secret" in bp.PINE_ALERT_FIELDS


def test_timestamp_normalisation_and_staleness():
    assert bp.normalize_timestamp(1_700_000_000_000) == 1_700_000_000
    assert bp.normalize_timestamp(1_700_000_000) == 1_700_000_000
    now = 1_700_000_000
    assert bp.is_stale_signal(now - 300, now, interval_seconds=60) is True
    assert bp.is_stale_signal(now - 10, now, interval_seconds=60) is False
    # max(2*interval, 120): bei 15m Interval sind 300s noch frisch
    assert bp.is_stale_signal(now - 300, now, interval_seconds=900) is False


def test_kelly_is_half_and_capped():
    size = bp.calculate_kelly(equity=10_000, price=100, win_prob=0.60, rrr=2.0)
    # edge = 0.6 - 0.4/2 = 0.4 -> half kelly 0.2 -> capped at 0.10
    assert size == pytest.approx(10_000 * 0.10 / 100)
    assert bp.calculate_kelly(10_000, 100, 0.30) == 0.0


def test_bracket_direction():
    sl, tp = bp.bracket_prices(entry=100.0, atr=2.0, action="BUY")
    assert sl == pytest.approx(97.0) and tp == pytest.approx(106.0)
    sl_s, tp_s = bp.bracket_prices(entry=100.0, atr=2.0, action="SELL")
    assert sl_s == pytest.approx(103.0) and tp_s == pytest.approx(94.0)


def test_kraken_error_parsing_beats_exit_code():
    assert bp.kraken_output_is_error("EOrder:Insufficient funds", "", 0) is True
    assert bp.kraken_output_is_error("txid=OABC", "", 0) is False
    assert bp.kraken_output_is_error("", "", 1) is True


def test_m8_alert_matrix_semantics():
    active = bp.alert_policy_for_state(bp.M8State.ACTIVE)
    throttled = bp.alert_policy_for_state("THROTTLED")
    quarantined = bp.alert_policy_for_state("QUARANTINED")
    retired = bp.alert_policy_for_state("RETIRED")

    assert active.budget_multiplier == 1.0 and active.accept_webhook
    # THROTTLED laesst den Alert AN und halbiert nur die Size (§4.6)
    assert throttled.alert is bp.AlertAction.KEEP
    assert throttled.budget_multiplier == 0.5 and throttled.accept_webhook
    assert quarantined.alert is bp.AlertAction.DISABLE and not quarantined.accept_webhook
    assert retired.alert is bp.AlertAction.DISABLE

    # Erfolgreicher Trade veraendert den Alert NICHT
    assert "entry_filled" in bp.ALERT_UNCHANGED_ON_EVENTS
    assert "ui_stop" in bp.ALERT_DISABLE_ON_EVENTS


# ----------------------------------------------------------- loop B hardening ---

def test_ga_hardening_matches_section_17_4(yaml_cfg):
    opt = yaml_cfg["optimizer"]
    assert bp.GA_MAX_POPULATION == opt["max_population"] == 15
    assert bp.GA_MAX_GENERATIONS == opt["max_generations"] == 5
    assert bp.GA_EARLY_STOP_STALL_GENERATIONS == opt["early_termination_stall_generations"] == 3
    assert bp.GA_PARAM_CACHE_REQUIRED is True
    assert bp.GA_CONCURRENCY == bp.TV_MAX_CONCURRENCY == 1
    assert bp.DSR_SHADOW_GATE == 0.95 and bp.MIN_TRADES_FOR_GATE == 30


def test_env_cannot_exceed_ga_or_concurrency_caps(monkeypatch):
    monkeypatch.setenv("SIGMA_GA_MAX_POPULATION", "500")
    monkeypatch.setenv("SIGMA_GA_MAX_GENERATIONS", "99")
    monkeypatch.setenv("SIGMA_TV_CONCURRENCY", "8")
    cfg = load_config()
    assert cfg.ga_max_population == bp.GA_MAX_POPULATION
    assert cfg.ga_max_generations == bp.GA_MAX_GENERATIONS
    assert cfg.tv_max_concurrency == 1


# ------------------------------------------------------------------- quant ---

def test_regime_thresholds(masterprompt_text):
    assert bp.classify_atr_percentile(10) == "COMPRESSION"
    assert bp.classify_atr_percentile(50) == "NORMAL"
    assert bp.classify_atr_percentile(80) == "EXPANSION"
    assert bp.classify_atr_percentile(96) == "CRISIS"
    assert bp.classify_hurst(0.30) == "MEAN_REVERSION"
    assert bp.classify_hurst(0.50) == "RANDOM_WALK"
    assert bp.classify_hurst(0.70) == "PERSISTENT_TREND"
    for enum_member in bp.Regime:
        assert enum_member.value in masterprompt_text


def test_onnx_temperature_and_brier():
    assert bp.BRIER_DRIFT_THRESHOLD == 0.28
    assert bp.brier_score([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert bp.brier_score([0.5], [1.0]) == pytest.approx(0.25)
    hot = bp.next_temperature(1.0, brier=0.4)
    assert hot > 1.0
    assert bp.calibrate_confidence(0.9, temperature=hot) < 0.9
    assert bp.next_temperature(1.5, brier=0.1) < 1.5


def test_reward_and_multipliers():
    r = bp.reward_total(pnl=10.0, mfe=6.0, mae=2.0, time_decay=1.0, fee_churn=2.0)
    assert r == pytest.approx(1.0 * 10 + 0.5 * 3 - 0.25 * 1 - 0.25 * 2)
    assert bp.budget_multiplier_for_grade("S") == 1.5
    assert bp.budget_multiplier_for_grade("A") == 1.25
    assert bp.budget_multiplier_for_grade("C") == 0.5
    assert bp.budget_multiplier_for_grade("S", strikes=3) == 0.0


# ----------------------------------------------------------------- academy ---

def test_badge_rules_require_thirty_trades():
    assert bp.badge_rating(10, 0.9, 3.0) == (bp.BADGE_INSUFFICIENT, False)
    assert bp.badge_rating(30, 0.68, 2.4) == ("S", True)
    assert bp.badge_rating(40, 0.32, 0.7) == ("F", False)
    rating, allowed = bp.badge_rating(50, 0.52, 1.2)
    assert rating in ("A", "B", "C") and allowed


def test_academy_tables_and_faults(blueprint_text, masterprompt_text):
    for table in bp.ACADEMY_TABLES:
        assert table in blueprint_text
    for fault in bp.CAUSAL_FAULTS:
        assert fault in masterprompt_text


# ------------------------------------------------------------- ui / systemd ---

def test_terminal_panels_and_presets(masterprompt_text):
    assert len(bp.TERMINAL_PANELS) == 11
    for panel in bp.TERMINAL_PANELS:
        assert panel in masterprompt_text
    for preset in bp.TERMINAL_PRESETS:
        assert preset in masterprompt_text
    assert len(bp.STRATEGY_DETAIL_TABS) == 8


def test_systemd_units_and_phases(blueprint_text):
    for unit in ("sigma-core", "sigma-tv-worker", "sigma-scraper"):
        assert unit in blueprint_text
    assert set(bp.DELIVERY_PHASES) == {"P0", "P1", "P2", "P3", "P4", "P5", "P6"}


def test_redis_keys_are_alpha_compatible(blueprint_text):
    assert bp.REDIS_KEYS["m8_state"] == "m8:state:{instance_id}"
    assert bp.REDIS_KEYS["tv_job"].startswith("sigma:tv:job")
    assert "vault:balance" in blueprint_text


# ------------------------------------------------------- selectors / memory ---

def test_selector_self_healing_policy():
    assert bp.SELECTOR_HEAL_STAGES == ("local_yaml", "remote_fetch", "builtin_default")
    assert bp.SELECTOR_MAX_DOWNLOADS == 3
    assert bp.SELECTOR_DOWNLOAD_WINDOW_SECONDS == 300
    assert bp.SELECTOR_RETRY_AFTER_REMOTE_REFRESH == 1  # keine Endlosschleife


def test_memory_watchdog_stages():
    assert bp.MEMORY_STAGES_PCT == (75.0, 85.0, 92.0, 96.0)
    assert bp.memory_stage(50) == 0
    assert bp.memory_stage(80) == 1
    assert bp.memory_stage(93) == 3
    assert bp.memory_stage(99) == 4
    assert bp.MEMORY_CGROUP_MAX == "4G"


def test_virtual_bot_and_deadman_rules():
    assert bp.VIRTUAL_BOT_SIZING_BASIS == "bot.current_equity"
    assert bp.PIONEX_ENABLED_DEFAULT is False
    assert bp.NATIVE_BRACKET_SL_REQUIRED is True
    assert bp.DEADMAN_TIMEOUT_SECONDS == 60
    assert bp.DEADMAN_CANCEL_ONLY_IF_NATIVE_STOP is True
    state, action = bp.VIRTUAL_BOT_ON_MAX_LOSS
    assert state is bp.M8State.QUARANTINED and action is bp.AlertAction.DISABLE


# ------------------------------------------------------------------ config ---

def test_sigma_config_defaults_come_from_blueprint():
    cfg = load_config()
    assert cfg.api_port == bp.PORT_CORE
    assert cfg.kelly_fraction == bp.KELLY_FRACTION
    assert cfg.max_daily_loss_usd == bp.RISK_GUARD["max_daily_loss_usd"]
    assert cfg.kill_switch_file == bp.PATH_KILL_SWITCH
    assert cfg.tv_scraper_url == bp.SCRAPER_BASE_URL
    assert cfg.live_trading is False
    assert cfg.pionex_enabled is False
    assert cfg.blueprint_version == bp.BLUEPRINT_VERSION


def test_l4_yaml_and_builtin_agree_on_critical_keys(yaml_cfg):
    builtin = l4_config.BUILTIN_L4_CONFIG
    for dotted in (
        "risk_guard.max_daily_loss_usd",
        "risk_guard.kelly_fraction",
        "optimizer.max_population",
        "optimizer.dsr_shadow_gate",
        "tv_automation.max_concurrency",
        "academy.badge_min_sample",
        "safety.kill_switch_file",
        "m8.base_budget_usd",
    ):
        node_yaml, node_builtin = yaml_cfg, builtin
        for part in dotted.split("."):
            node_yaml, node_builtin = node_yaml[part], node_builtin[part]
        assert node_yaml == node_builtin, dotted


def test_l4_loader_falls_back_when_file_missing(tmp_path):
    cfg = l4_config.load_l4_config(str(tmp_path / "nope.yaml"))
    assert cfg["source"] == "builtin_blueprint"
    assert cfg["risk_guard"]["max_daily_loss_usd"] == 600.0
    assert cfg["exchange"]["pionex"]["enabled"] is False


def test_strategy_csv_paths(blueprint_text):
    assert bp.strategy_path("parameters", "abc") == "./data/strategies/abc/parameters.csv"
    assert bp.strategy_path("backtest_trades", "abc", "b1").endswith("backtests/b1_trades.csv")
    assert "parameters_baseline.csv" in blueprint_text


def test_spec_summary_shape():
    summary = bp.spec_summary()
    assert summary["blueprint_version"] == "3.0"
    assert summary["ports"]["core"] == 8000
    assert len(summary["panels"]) == 11
    assert summary["ga"]["concurrency"] == 1


def test_docs_and_yaml_version_strings_align(blueprint_text):
    with open(L4_YAML, encoding="utf-8") as fh:
        raw = fh.read()
    assert re.search(r'^version:\s*"3\.0"', raw, re.M)
    assert 'version: "3.0"' in blueprint_text or "Freeze v3.0" in blueprint_text
