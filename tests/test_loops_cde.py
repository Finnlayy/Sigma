"""
Loops C/D/E — Regime-Detektor, QuantEngine/Kelly, Self-Optimizing ONNX,
Reward-Shaping, Strategy-Allocator/Badges, Scout, Memory-Watchdog, Telegram,
GeneSchema.
"""
from __future__ import annotations

import math
import time

import pytest

from app.core import blueprint as bp
from app.core.config import load_config
from app.core.memory_watchdog import MemoryWatchdog
from app.optimizer.StrategyAllocator import StrategyAllocator
from app.optimizer.gene_schema import GeneSchema
from app.optimizer.reward_shaping import RewardShapingEngine
from app.quant.onnx_kelly import QuantEngine
from app.quant.regime_detector import RegimeDetector, hurst_exponent
from app.quant.self_optimizing_onnx import SelfOptimizingOnnxEngine
from app.scout.ScoutDaemon import ScoutDaemon
from app.services.telegram_bot_operator import TelegramBotOperator


def candles(prices, atr_scale=0.01):
    out = []
    for i, p in enumerate(prices):
        span = p * atr_scale
        out.append({"ts": 1_700_000_000 + i * 900, "o": p, "h": p + span,
                    "l": p - span, "c": p, "v": 100.0})
    return out


# ------------------------------------------------------------------ regime ---

def test_regime_detects_bull_trend():
    prices = [100 * (1.01 ** i) for i in range(250)]
    vec = RegimeDetector().detect(candles(prices))
    assert vec.regime in (bp.Regime.STRONG_BULL.value, bp.Regime.WEAK_BULL.value)
    assert vec.ema_delta_pct > 0
    assert not vec.entry_blocked


def test_regime_detects_bear_and_chop():
    # milder Abwaertstrend mit Rauschen (monotone Extremreihen sind per
    # Definition Vol-Crisis, siehe test_regime_crisis_blocks_entries)
    bear_prices = [100 * (0.998 ** i) * (1.003 if i % 3 else 0.997) for i in range(250)]
    bear = RegimeDetector().detect(candles(bear_prices))
    assert bear.regime in (bp.Regime.STRONG_BEAR.value, bp.Regime.WEAK_BEAR.value)
    chop = RegimeDetector().detect(candles([100 + (1 if i % 2 else -1) for i in range(250)]))
    assert chop.regime == bp.Regime.RANGING_CHOP.value


def test_regime_crisis_blocks_entries():
    prices = [100.0] * 240
    data = candles(prices, atr_scale=0.001)
    for c in data[-3:]:                       # Vol-Spike am Ende
        c["h"] = c["c"] * 1.25
        c["l"] = c["c"] * 0.75
    vec = RegimeDetector().detect(data)
    assert vec.atr_percentile >= bp.ATR_PCTL_CRISIS_MIN
    assert vec.regime == bp.Regime.HIGH_VOL_CRISIS.value
    assert vec.entry_blocked and vec.crisis


def test_regime_short_series_is_safe():
    vec = RegimeDetector().detect(candles([100, 101]))
    assert vec.regime == bp.Regime.RANGING_CHOP.value


def test_hurst_classes():
    trend = hurst_exponent([100 * (1.005 ** i) for i in range(200)])
    assert bp.classify_hurst(trend) in ("PERSISTENT_TREND", "RANDOM_WALK")
    assert 0.0 <= trend <= 1.0


# ------------------------------------------------------------ quant / kelly ---

def test_quant_confidence_is_bounded_and_monotone():
    q = QuantEngine(load_config())
    low = q.predict_confidence(rsi=50, atr=100, cisd_score=0.5, price=10_000)
    high = q.predict_confidence(rsi=15, atr=100, cisd_score=0.9, price=10_000)
    assert 0 < low["win_prob"] < 1 and 0 < high["win_prob"] < 1
    assert high["win_prob"] > low["win_prob"]
    assert high["source"] == "heuristic"      # kein ONNX-Modell im Repo


def test_quant_sizing_respects_cap_and_brackets():
    q = QuantEngine(load_config())
    d = q.size_position(equity=10_000, price=100, win_prob=0.9, atr=2.0, action="BUY")
    assert d.capped is True
    assert d.kelly_fraction_used == bp.MAX_PORTFOLIO_RISK_PER_TRADE
    assert d.stop_loss == pytest.approx(100 - 2 * bp.ATR_STOP_MULTIPLIER)
    assert d.take_profit == pytest.approx(100 + 2 * bp.ATR_TAKE_PROFIT_MULTIPLIER)
    short = q.size_position(equity=10_000, price=100, win_prob=0.6, atr=2.0, action="SELL")
    assert short.stop_loss > 100 > short.take_profit


def test_quant_no_edge_no_size():
    q = QuantEngine(load_config())
    assert q.size_position(equity=10_000, price=100, win_prob=0.2, atr=1.0, action="BUY").quantity == 0


# ------------------------------------------------------------- self-opt onnx ---

def test_self_opt_raises_temperature_on_drift():
    engine = SelfOptimizingOnnxEngine(min_samples=5)
    for _ in range(10):
        engine.record(0.9, 0.0)                  # dauernd zu selbstsicher
    snap = engine.snapshot()
    assert snap["brier"] > bp.BRIER_DRIFT_THRESHOLD
    assert snap["drift"] is True
    assert engine.temperature > bp.ONNX_TEMPERATURE_DEFAULT
    assert engine.calibrate(0.9) < 0.9           # Konfidenz gedämpft


def test_self_opt_stays_calm_when_calibrated():
    engine = SelfOptimizingOnnxEngine(min_samples=5)
    for i in range(20):
        engine.record(0.9 if i % 10 else 0.1, 1.0 if i % 10 else 0.0)
    assert engine.snapshot()["drift"] is False


def test_shadow_gate_blocks_worse_model():
    engine = SelfOptimizingOnnxEngine(min_samples=5)
    for _ in range(10):
        engine.record(0.6, 1.0)
    good = engine.shadow_gate([0.95] * 10, [1.0] * 10)
    bad = engine.shadow_gate([0.05] * 10, [1.0] * 10)
    assert good["passed"] is True and bad["passed"] is False
    assert engine.shadow_gate([0.9], [1.0])["passed"] is False   # zu wenige Samples


def test_hot_reload_resets_state():
    engine = SelfOptimizingOnnxEngine(min_samples=5)
    for _ in range(10):
        engine.record(0.95, 0.0)
    out = engine.hot_reload("models/candidate.onnx")
    assert out["temperature"] == bp.ONNX_TEMPERATURE_DEFAULT
    assert engine.sample_size == 0 and engine.retrain_count == 1


# ----------------------------------------------------------- reward shaping ---

def test_reward_grades_and_multipliers():
    engine = RewardShapingEngine()
    great = engine.score_trade("s1", pnl_pct=6.0, mfe_pct=8.0, mae_pct=1.0,
                               duration_bars=10, fee_usd=0.5, notional_usd=1000)
    assert great.grade in ("S", "A") and great.budget_multiplier >= bp.REWARD_MULTIPLIER_A
    assert great.xp_delta > 0


def test_three_strikes_quarantine():
    engine = RewardShapingEngine()
    for _ in range(bp.STRIKES_TO_QUARANTINE):
        out = engine.score_trade("s2", pnl_pct=-3.0, mfe_pct=0.2, mae_pct=4.0,
                                 duration_bars=80, fee_usd=5.0, notional_usd=500)
    assert out.grade == "F"
    assert out.strikes >= bp.STRIKES_TO_QUARANTINE
    assert out.quarantine is True and out.budget_multiplier == 0.0
    assert engine.score("s2")["quarantined"] is True


def test_fee_churn_and_time_decay_hurt():
    engine = RewardShapingEngine()
    clean = engine.score_trade("a", pnl_pct=1.0, mfe_pct=2.0, mae_pct=1.0,
                               duration_bars=10, fee_usd=0.1, notional_usd=1000)
    churny = engine.score_trade("b", pnl_pct=1.0, mfe_pct=2.0, mae_pct=1.0,
                                duration_bars=200, fee_usd=50.0, notional_usd=1000)
    assert churny.reward < clean.reward


# --------------------------------------------------------------- allocator ---

def _feed(alloc, sid, symbol, tf, regime, wins, losses):
    for _ in range(wins):
        alloc.ingest_trade_result(sid, symbol, tf, regime, 2.0)
    for _ in range(losses):
        alloc.ingest_trade_result(sid, symbol, tf, regime, -1.0)


def test_badges_need_thirty_trades():
    alloc = StrategyAllocator()
    _feed(alloc, "cisd", "XRP/USD", 5, bp.Regime.STRONG_BULL.value, 10, 2)
    verdict = alloc.evaluate("cisd", "XRP/USD", 5, bp.Regime.STRONG_BULL.value)
    assert verdict["rating"] == bp.BADGE_INSUFFICIENT and verdict["incubating"]


def test_super_badge_and_allocator_enable():
    class Alerts:
        def __init__(self):
            self.events = []

        def enable(self, sid, reason=""):
            self.events.append(("enable", sid))

        def disable(self, sid, reason=""):
            self.events.append(("disable", sid))

    alerts = Alerts()
    alloc = StrategyAllocator(alert_provisioner=alerts)
    _feed(alloc, "cisd", "XRP/USD", 5, bp.Regime.STRONG_BULL.value, 24, 8)
    prof = alloc.get_profile("cisd", "XRP/USD", 5, bp.Regime.STRONG_BULL.value)
    assert prof.trade_count >= bp.BADGE_MIN_SAMPLE
    assert prof.rating == "S" and prof.badge_name() == "SUPER_ON_XRPUSD_5"
    out = alloc.apply("cisd", "XRP/USD", 5, bp.Regime.STRONG_BULL.value)
    assert out["allow"] and out["action"] == "enable"
    assert ("enable", "cisd") in alerts.events


def test_poor_badge_disables_alert():
    class Alerts:
        def __init__(self):
            self.events = []

        def enable(self, sid, reason=""):
            self.events.append(("enable", sid))

        def disable(self, sid, reason=""):
            self.events.append(("disable", sid))

    alerts = Alerts()
    alloc = StrategyAllocator(alert_provisioner=alerts)
    _feed(alloc, "cisd", "XRP/USD", 10, bp.Regime.RANGING_CHOP.value, 10, 25)
    out = alloc.apply("cisd", "XRP/USD", 10, bp.Regime.RANGING_CHOP.value)
    assert not out["allow"] and out["rating"] == "F"
    assert out["badge"].startswith("POOR_ON_")
    assert ("disable", "cisd") in alerts.events


def test_chop_master_badge_naming():
    alloc = StrategyAllocator()
    _feed(alloc, "fvg", "BTC/USD", 15, bp.Regime.RANGING_CHOP.value, 25, 8)
    prof = alloc.get_profile("fvg", "BTC/USD", 15, bp.Regime.RANGING_CHOP.value)
    assert prof.badge_name().startswith("CHOP_MASTER_ON_BTCUSD")


def test_training_dataset_export_needs_sample():
    alloc = StrategyAllocator()
    _feed(alloc, "s", "BTC/USD", 15, bp.Regime.WEAK_BULL.value, 5, 5)
    assert alloc.export_training_dataset() == []
    _feed(alloc, "s", "BTC/USD", 15, bp.Regime.WEAK_BULL.value, 20, 5)
    rows = alloc.export_training_dataset()
    assert rows and rows[0]["trade_count"] >= bp.BADGE_MIN_SAMPLE


# ------------------------------------------------------------------- scout ---

def test_math_engine_sharpe_and_nan_penalty():
    from sigma.core.math_engine import NAN_PENALTY, clamp, nan_penalty, sharpe

    assert nan_penalty(float("nan")) == NAN_PENALTY == -1e9
    assert clamp(12, 0, 5) == 5
    assert sharpe([0.01, 0.02, -0.005, 0.015]) != 0.0


def test_sigma_loop_ports_import():
    from sigma.loops import LoopAPort, LoopBPort, LoopCPort, LoopDPort, LoopEPort
    from sigma.orchestration import MasterOrchestrator

    assert LoopAPort and LoopBPort and LoopCPort and LoopDPort and LoopEPort
    status = MasterOrchestrator().tick()["status"]
    assert status in ("htf_not_ready", "unwind_only", "tick")


def test_loop_c_port_degraded_empty_when_sidecar_down():
    from sigma.loops import LoopCPort

    class _Down:
        def health(self):
            return {"ok": False, "degraded": True}

        def fetch_ohlc_with_meta(self, *args, **kwargs):
            raise AssertionError("Loop C must not fetch when sidecar is down")

    snap = LoopCPort(scraper=_Down(), store=None).poll()
    assert snap.degraded is True
    assert snap.series == {}
    assert snap.symbols == []
    assert snap.regime is None


def test_loop_c_port_rejects_synthetic_source():
    from sigma.loops import LoopCPort

    class _Synth:
        def health(self):
            return {"ok": True, "degraded": False}

        def fetch_ohlc_with_meta(self, *args, **kwargs):
            candles = [{"ts": 1.0, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
            return candles, {"source": "synthetic", "degraded": False}

    snap = LoopCPort(scraper=_Synth(), store=None, symbols=["BTC/USD"]).poll()
    assert snap.degraded is True
    assert snap.series == {}


def test_loop_d_port_is_paper_only():
    from sigma.loops import LoopDPort

    alloc = StrategyAllocator()

    def runner(sid, symbol, tf):
        return {"trades": [{"pnlPercent": 1.5} for _ in range(bp.BADGE_MIN_SAMPLE + 2)]}

    scout = ScoutDaemon(
        allocator=alloc, backtest_runner=runner,
        symbols=["ETH/USD"], timeframes=[15],
    )
    scout.plan(["s2"], bp.Regime.WEAK_BULL.value)
    grads = LoopDPort(daemon=scout).tick(regime=bp.Regime.WEAK_BULL.value)
    assert grads
    assert all(g.paper_only and not g.live_capital for g in grads)


def test_scout_plans_only_unprofiled_pairs():
    alloc = StrategyAllocator()
    _feed(alloc, "s1", "BTC/USD", 15, bp.Regime.RANGING_CHOP.value, 25, 10)
    scout = ScoutDaemon(allocator=alloc, symbols=["BTC/USD", "ETH/USD"], timeframes=[15])
    tasks = scout.plan(["s1"], bp.Regime.RANGING_CHOP.value)
    keys = [t.key for t in tasks]
    assert ("s1", "ETH/USD", "15") in keys
    assert ("s1", "BTC/USD", "15") not in keys     # bereits profiliert


def test_scout_runs_paper_only_and_feeds_academy():
    alloc = StrategyAllocator()

    def runner(sid, symbol, tf):
        return {"trades": [{"pnlPercent": 1.5} for _ in range(bp.BADGE_MIN_SAMPLE + 2)]}

    scout = ScoutDaemon(allocator=alloc, backtest_runner=runner,
                        symbols=["ETH/USD"], timeframes=[15])
    scout.plan(["s2"], bp.Regime.WEAK_BULL.value)
    results = scout.cycle(bp.Regime.WEAK_BULL.value)
    assert results[0]["paper_only"] is True and results[0]["live_capital"] is False
    prof = alloc.get_profile("s2", "ETH/USD", 15, bp.Regime.WEAK_BULL.value)
    assert prof.trade_count >= bp.BADGE_MIN_SAMPLE
    assert scout.snapshot()["by_status"].get("graduated", 0) == 1


def test_scout_run_task_survives_runner_exception():
    alloc = StrategyAllocator()

    def boom(sid, symbol, tf):
        raise RuntimeError("tv tester crashed")

    scout = ScoutDaemon(allocator=alloc, backtest_runner=boom,
                        symbols=["ETH/USD"], timeframes=[15])
    tasks = scout.plan(["s_err"], bp.Regime.WEAK_BULL.value)
    out = scout.run_task(tasks[0], bp.Regime.WEAK_BULL.value)
    assert out["ok"] is False
    assert tasks[0].status == "pending"
    assert alloc.get_profile("s_err", "ETH/USD", 15, bp.Regime.WEAK_BULL.value) is None


# --------------------------------------------------------- memory watchdog ---

def test_memory_stages_escalate():
    wd = MemoryWatchdog(idle_provider=lambda: True)
    assert wd.check(50.0)["stage"] == 0
    assert wd.check(65.0)["action"] == "gc_collect"
    assert wd.check(88.0)["stage"] == 3
    assert wd.check(93.0)["action"] == "emergency_halt_and_restart_worker"


def test_memory_watchdog_gc_runs_when_busy():
    wd = MemoryWatchdog(idle_provider=lambda: False)
    out = wd.check(65.0)
    assert out["executed"] is True and out["action"] == "gc_collect"


def test_memory_watchdog_reaper_is_idle_only():
    wd = MemoryWatchdog(idle_provider=lambda: False)
    out = wd.check(88.0)
    assert out["reason"] == "busy_partial" and out["executed"] is True
    assert out["action"] == "duckdb_checkpoint"
    forced = wd.check(88.0, force=True)
    assert forced["executed"] is True
    assert forced["action"] == "chromium_zombie_reaper"


def test_memory_stage_4_halts_even_when_busy():
    class Telemetry:
        def __init__(self):
            self.state_set = None

        def set_state(self, s, reason=""):
            self.state_set = s

    tele = Telemetry()
    wd = MemoryWatchdog(idle_provider=lambda: False, telemetry=tele)
    out = wd.check(99.0)
    assert out["executed"] is True
    assert tele.state_set == "EMERGENCY_HALT"


def test_parse_memory_bytes_and_rss_budget(monkeypatch):
    from app.core.memory_watchdog import parse_memory_bytes, read_memory_snapshot

    assert parse_memory_bytes("4G") == 4 * 1024 ** 3
    assert parse_memory_bytes("2GB") == 2 * 1024 ** 3
    budget = parse_memory_bytes(bp.MEMORY_CGROUP_MAX)
    monkeypatch.setattr("app.core.memory_watchdog.read_cgroup_memory", lambda: None)
    monkeypatch.setattr(
        "app.core.memory_watchdog.read_process_tree_rss_bytes",
        lambda: int(0.8 * budget),
    )
    monkeypatch.setattr("app.core.memory_watchdog.read_host_memory_percent", lambda: 12.0)
    snap = read_memory_snapshot()
    assert snap["source"] == "rss"
    assert snap["percent"] == pytest.approx(80.0, abs=0.15)


def test_pressure_hook_and_release_memory_run_at_stage_two():
    calls: list[int] = []

    class Store:
        def release_memory(self):
            return "released"

    wd = MemoryWatchdog(
        store=Store(),
        idle_provider=lambda: True,
        pressure_hook=lambda stage: calls.append(stage) or f"ok-{stage}",
    )
    out = wd.check(80.0)
    assert out["executed"] is True
    assert calls == [2]
    assert "released" in out["detail"]
    assert "ok-2" in out["detail"]


def test_headless_browser_cmd_detects_playwright_not_login():
    from app.core.memory_watchdog import is_headless_browser_cmd

    assert is_headless_browser_cmd(
        "/home/sigma/.cache/ms-playwright/chromium/chrome --headless=new"
    )
    assert is_headless_browser_cmd("chrome-headless-shell --no-sandbox")
    assert not is_headless_browser_cmd(
        "/usr/bin/google-chrome --user-data-dir=/opt/sigma/data/tv-profile"
    )


# ---------------------------------------------------------------- telegram ---

@pytest.fixture()
def operator(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    cfg = load_config()
    cfg.kill_switch_file = str(tmp_path / "KILL_SWITCH")
    cfg.pause_signal_file = str(tmp_path / "PAUSE")
    from app.execution.SafetyGuard import SafetyGuard

    return TelegramBotOperator(cfg, safety_guard=SafetyGuard(cfg), sender=lambda c, t: None)


def test_telegram_whitelist(operator):
    assert operator.is_authorized("4242")
    out = operator.handle("9999", "/kill")
    assert out["authorized"] is False
    assert not operator.safety.kill_switch_active     # nichts passiert


def test_telegram_fast_path_commands(operator):
    for cmd in bp.TELEGRAM_FAST_PATH_COMMANDS:
        out = operator.handle("4242", cmd)
        assert out["fast_path"] is True
        assert out["latency_ms"] < 200                 # großzügig für CI
    assert operator.safety.kill_switch_active          # /kill hat gewirkt


def test_telegram_pause_resume(operator):
    operator.handle("4242", "/pause")
    assert operator.safety.pause_active
    operator.handle("4242", "/resume")
    assert not operator.safety.pause_active


def test_telegram_freetext_uses_llm(operator):
    operator.llm = lambda prompt: f"llm:{prompt}"
    out = operator.handle("4242", "wie laeuft der BTC bot?")
    assert out["fast_path"] is False and out["text"].startswith("llm:")


def test_telegram_push_notifications(operator):
    out = operator.notify_quarantine("s1", "max_loss")
    assert out["sent_to"] == ["4242"] and "QUARANTINE" in out["text"]


# -------------------------------------------------------------- gene schema ---

PARAM_CSV = """Parameter,Value
trendFastEma,12
atrStopMultiplier,2.0
useTrailing,true
"""


def test_gene_schema_from_tv_parameter_csv():
    schema = GeneSchema.from_parameter_csv(PARAM_CSV)
    assert set(schema.names()) == {"trendFastEma", "atrStopMultiplier", "useTrailing"}
    assert schema.genes["trendFastEma"].kind == "int"
    assert schema.genes["atrStopMultiplier"].kind == "float"
    assert schema.genes["useTrailing"].kind == "bool"
    assert schema.search_space_size() > 1


def test_genes_map_to_pine_inputs_with_clamping():
    schema = GeneSchema.from_parameter_csv(PARAM_CSV)
    out = schema.genes_to_pine_inputs({"trendFastEma": 9999, "atrStopMultiplier": -5})
    assert out["trendFastEma"] <= schema.genes["trendFastEma"].high
    assert out["atrStopMultiplier"] >= schema.genes["atrStopMultiplier"].low
    assert "Parameter,Value" in schema.to_parameter_csv(out)


def test_explicit_bounds_override_heuristics():
    schema = GeneSchema.from_parameter_csv(PARAM_CSV, bounds={"trendFastEma": (5, 20)})
    assert schema.genes["trendFastEma"].low == 5 and schema.genes["trendFastEma"].high == 20
    assert schema.clamp_all({"trendFastEma": 100})["trendFastEma"] == 20


# --------------------------------------------------------- HTF / LTF harmonics ---

def _bars(n=20, start=1_700_000_000, step=900, price=100.0):
    out = []
    for i in range(n):
        p = price * (1.0 + 0.001 * i)
        out.append({
            "ts": start + i * step, "o": p, "h": p * 1.01, "l": p * 0.99,
            "c": p, "v": 100.0 + i,
        })
    return out


def test_timeframe_ladder_rejects_unmatched_and_m15_m5():
    from sigma.signals.timeframe_ladder import (
        bias_tf, classify_pair, exec_tf, reject_unpaired, session_exec_pair,
    )
    assert bias_tf(15) == 60 and exec_tf(60) == 15
    assert classify_pair(60, 15).kind == "bias"
    assert classify_pair(60, 5).kind == "execution"
    assert reject_unpaired(15, 5) is True
    assert classify_pair(15, 5) is None
    ny = session_exec_pair("NEW_YORK_EXPANSION")
    assert ny.bias_minutes == 60 and ny.exec_minutes == 15


def test_session_clock_21utc_gap_and_weekend_alts():
    from datetime import datetime, timezone
    from sigma.signals.session_clock import SessionClock
    clock = SessionClock()
    gap = clock.evaluate(datetime(2026, 8, 28, 21, 10, tzinfo=timezone.utc).timestamp())
    assert gap.liquidity_gap is True
    assert gap.session == "US_CLOSE_TRANSITION"
    sat = clock.evaluate(datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc).timestamp())
    assert sat.weekend_alts_paper_only is True
    london = clock.evaluate(datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc).timestamp())
    assert london.session == "LONDON_MANIPULATION"


def test_dual_hurst_fail_closed_on_open_htf():
    from sigma.signals.dual_hurst import evaluate_dual_hurst, htf_ready
    bars = _bars(10, start=1_000_000, step=3600)
    now = 1_000_000 + 9 * 3600 + 60  # last bar still open (close at +3600)
    assert htf_ready(bars, 60, now=now) is False
    dual = evaluate_dual_hurst(bars, bars, htf_interval_min=60, now=now)
    assert dual.htf_ready is False and dual.complementary is False
    closed = 1_000_000 + 10 * 3600
    assert htf_ready(bars, 60, now=closed) is True


def test_htf_flags_never_live_gates():
    from sigma.signals.htf_features import extract_htf_flags
    flags = extract_htf_flags(_bars(8), interval_min=60, now=1_700_000_000 + 8 * 900 + 3600)
    assert flags.get("live_gate") is False
    assert flags.get("valid") is True
    assert "bullish_fvg" in flags


def test_scale_features_reject_open_bar():
    from sigma.signals.scale_features import scale_invariant_features
    bars = _bars(20)
    open_now = bars[-1]["ts"] + 10
    assert scale_invariant_features(bars, interval_min=15, now=open_now)["valid"] is False
    closed = bars[-1]["ts"] + 900
    feat = scale_invariant_features(bars, interval_min=15, now=closed)
    assert feat["valid"] is True
    assert "log_return_z" in feat and "atr_over_price" in feat


def test_templates_paper_only_and_gap_flat():
    from datetime import datetime, timezone
    from sigma.strategies import DualHedgeGrid, DynamicChannelDCA, HtfTrendLtfReversion
    gap_ts = datetime(2026, 8, 28, 21, 5, tzinfo=timezone.utc).timestamp()
    ctx = {
        "symbol": "ETH/USD",
        "htf_candles": _bars(12, step=3600),
        "ltf_candles": _bars(12),
        "now": gap_ts,
        "htf_interval_min": 60,
    }
    for tmpl in (HtfTrendLtfReversion(), DynamicChannelDCA(), DualHedgeGrid()):
        intent = tmpl.plan(ctx)
        assert intent.execution_mode == "kraken_paper"
        assert intent.action == "FLAT"
        assert intent.details.get("reason") in ("utc_21_gap", "missing_data", "htf_open", "not_london")


def test_loop_b_h_tests_h3_h4_default_off():
    from sigma.loops.loop_b import LoopBPort
    closed = _bars(12)
    now = closed[-1]["ts"] + 900
    out = LoopBPort().paper_hypotheses(
        closed, closed, htf_interval_min=60, ltf_interval_min=15, now=now,
    )
    assert out["live"] is False and out["mode"] == "paper"
    by_id = {r["hypothesis"]: r for r in out["results"]}
    assert by_id["H3"]["enabled"] is False and by_id["H3"]["reason"] == "default_off"
    assert by_id["H4"]["enabled"] is False and by_id["H4"]["reason"] == "default_off"
    assert by_id["H2"]["passed"] is True
    assert by_id["H5"]["passed"] is True


def test_loop_c_poll_pair_fetches_both_legs():
    from sigma.loops.loop_c import LoopCPort
    seen = []

    class _Ok:
        def health(self):
            return {"ok": True, "degraded": False}

        def fetch_ohlc_with_meta(self, symbol, interval, count):
            seen.append(interval)
            return _bars(4, step=interval * 60), {"source": "tv_scraper"}

    snap = LoopCPort(scraper=_Ok(), store=None, symbols=["BTC/USD"]).poll_pair(15, 60)
    assert snap.degraded is False
    assert 15 in seen and 60 in seen
    assert "BTC/USD" in snap.series
    assert snap.htf_interval_min == 60


def test_orchestrator_fail_closed_when_htf_open():
    from types import SimpleNamespace
    from sigma.orchestration import MasterOrchestrator
    bars = _bars(8)
    snap = SimpleNamespace(
        series={"BTC/USD": bars},
        htf_series={"BTC/USD": bars},
        degraded=False,
    )
    orch = MasterOrchestrator(ports={"polymarket": None})
    # open HTF: now is inside last hour bar
    out = orch.tick(snap)
    assert out["deployed"] == 0
    assert out["status"] in ("htf_not_ready", "unwind_only", "tick")


def test_multi_asset_router_weekend_alts_paper_only():
    from datetime import datetime, timezone
    from sigma.orchestration.multi_asset_router import MultiAssetRouter
    from sigma.signals.session_clock import SessionClock
    series = {"BTC/USD": _bars(50), "ETH/USD": _bars(50)}
    sat = SessionClock().evaluate(datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc).timestamp())
    rows = MultiAssetRouter().route(series, session=sat)
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["ETH/USD"].paper_only is True
    assert by_sym["ETH/USD"].live_a is False
    assert by_sym["BTC/USD"].paper_only is False


def test_polymarket_layer0_fail_closed():
    from sigma.signals.polymarket_layer0 import layer0_pre_regime
    empty = layer0_pre_regime(None)
    assert empty.valid is False and empty.reason == "missing_data"
    ok = layer0_pre_regime({"event_id": "e1", "implied_prob": 0.72, "title": "risk"})
    assert ok.valid is True and ok.regime_hint == "RISK_ON"
    synth = layer0_pre_regime({"implied_prob": 0.8, "source": "synthetic"})
    assert synth.valid is False


def test_pine_generator_requires_confirmed_bars():
    from sigma.strategies.pine_v6_generator import generate_htf_ltf_pine
    code = generate_htf_ltf_pine("htf_trend_ltf_reversion")
    assert code.startswith("//@version=6")
    assert "barstate.isconfirmed" in code
    assert "lookahead=barmerge.lookahead_off" in code
