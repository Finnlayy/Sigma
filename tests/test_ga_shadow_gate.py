"""
=========================================================
Datei:      tests/test_ga_shadow_gate.py
Zweck:      Phase-4-Acceptance: WFO + DSR > 95% vor Shadow-Deployment
Knoten:     Jaune (Carrera-Engine) / Test Suite
=========================================================
"""
import math
import random

import pytest

from app.core.config import AlphaConfig
from app.optimizer.GeneticOptimizer import (GeneticOptimizer, cadence_score,
                                            deflated_sharpe_ratio,
                                            multi_objective_fitness)


@pytest.fixture
def cfg():
    c = AlphaConfig()
    c.ga_min_trades_absolute = 30
    c.ga_min_trades_target = 80
    c.ga_max_allowed_rules = 6
    c.ga_fitness_threshold = 0.35
    c.ga_dsr_gate = 0.95
    c.ga_cadence_min = 3.0
    c.ga_cadence_max = 6.0
    return c


def _returns(n, drift=0.0008, vol=0.004, seed=42):
    rng = random.Random(seed)
    return [drift + rng.gauss(0, vol) for _ in range(n)]


def test_dsr_high_for_strong_trend(cfg):
    dsr = deflated_sharpe_ratio(_returns(400, drift=0.002, vol=0.003), trials=1500)
    assert dsr > 0.9


def test_dsr_low_for_noise(cfg):
    dsr = deflated_sharpe_ratio(_returns(200, drift=0.00005, vol=0.006), trials=1500)
    assert dsr < 0.95


def test_cadence_bandpass_3_6_per_day(cfg):
    # ideal 4.5 -> Score nahe 1
    assert cadence_score(4.5, cfg) > 0.99
    # 1.2/Tag (unter Band) und 9/Tag (über Band) deutlich bestraft
    assert cadence_score(1.2, cfg) < 0.5
    assert cadence_score(9.0, cfg) < 0.35


def test_fitness_trade_starvation_guard(cfg):
    result = multi_objective_fitness(
        {"totalTrades": 12, "finalBalance": 12000, "initialBalance": 10000},
        {"dsr": 1.0, "evaluationDays": 30, "activeRuleCount": 3, "cadenceScore": 1.0}, cfg)
    assert result["isValidCandidate"] is False
    assert "TRADE STARVATION" in result["rejectionReason"]


def test_fitness_fee_drag_death(cfg):
    result = multi_objective_fitness(
        {"totalTrades": 60, "finalBalance": 9800, "initialBalance": 10000},
        {"dsr": 1.0, "evaluationDays": 30, "activeRuleCount": 3, "cadenceScore": 1.0}, cfg)
    assert result["isValidCandidate"] is False
    assert "FEE DRAG" in result["rejectionReason"]


def test_shadow_gate_blocks_below_dsr_95(cfg):
    ga = GeneticOptimizer(cfg)
    weak = {"dsr": 0.88, "tradesPerDay": 4.2, "tradesCount": 120,
            "isValidCandidate": True}
    gate = ga.shadow_gate(weak)
    assert gate["passed"] is False
    assert gate["checks"]["dsr_above_gate"] is False
    assert "REJECTED" in gate["verdict"]


def test_shadow_gate_passes_with_dsr_95_plus(cfg):
    ga = GeneticOptimizer(cfg)
    strong = {"dsr": 0.97, "tradesPerDay": 4.2, "tradesCount": 140,
              "isValidCandidate": True}
    gate = ga.shadow_gate(strong)
    assert gate["passed"] is True
    assert gate["verdict"] == "SHADOW-APPROVED"


def test_shadow_gate_blocks_out_of_cadence_band(cfg):
    ga = GeneticOptimizer(cfg)
    offband = {"dsr": 0.99, "tradesPerDay": 9.5, "tradesCount": 300,
               "isValidCandidate": True}
    gate = ga.shadow_gate(offband)
    assert gate["passed"] is False
    assert gate["checks"]["cadence_in_band"] is False


def _candles(n=240):
    return [{"ts": i, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0} for i in range(n)]


def _flat_bt(*_a, **_k):
    return {
        "equityCurve": [{"equity": 10_000}, {"equity": 10_000}],
        "summary": {
            "totalTrades": 40, "totalReturnUSD": 0.0, "totalReturnPercent": 0.0,
            "sharpeRatio": 0.1, "winRate": 50.0, "maxDrawdownPercent": 1.0,
            "finalBalance": 10_000.0, "profitFactor": 1.0,
        },
    }


def test_ga_early_stops_after_stall(cfg, monkeypatch):
    monkeypatch.setattr("app.optimizer.GeneticOptimizer.run_backtest", _flat_bt)
    monkeypatch.setattr(
        "app.optimizer.GeneticOptimizer.multi_objective_fitness",
        lambda *_a, **_k: {"fitnessScore": 0.42, "isValidCandidate": True,
                           "rejectionReason": ""},
    )
    out = GeneticOptimizer(cfg).run({
        "populationSize": 2, "maxGenerations": 5, "earlyStopStall": 3, "seed": 1,
    }, _candles())
    assert out["earlyStopped"] is True
    assert out["totalGenerationsCompleted"] < 5


def test_counterfactual_replay_survives_store_crash(cfg, monkeypatch):
    monkeypatch.setattr("app.optimizer.GeneticOptimizer.run_backtest", _flat_bt)

    def boom(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr("app.core.duckdb_store.get_store", boom)
    out = GeneticOptimizer(cfg).counterfactual_replay(
        {"genes": {}, "strategyId": "s1", "assetPair": "BTC/USD"},
        _candles(), 0.26, 0.05, 10_000.0)
    assert out["liveTrades"] == 0
    assert out["liveNetPnlUsd"] == 0


def test_ga_quarantines_terminal_driver_error(cfg, monkeypatch):
    from app.tv.strategy_tester_driver import DriverError

    def boom(*_a, **_k):
        raise DriverError("no session", "TV_SESSION_MISSING")

    monkeypatch.setattr("app.optimizer.GeneticOptimizer.run_backtest", boom)
    out = GeneticOptimizer(cfg).run({
        "populationSize": 2, "maxGenerations": 1, "seed": 1,
    }, _candles())
    assert out["bestIndividual"] is None
    assert all(p.get("status") == "quarantined" for p in out["population"])
    assert out["shadowGate"]["passed"] is False


def test_ga_retries_once_on_target_closed(cfg, monkeypatch):
    from app.tv.strategy_tester_driver import DriverError

    calls = {"n": 0, "restarts": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DriverError("page gone", "TARGET_CLOSED")
        return _flat_bt()

    monkeypatch.setattr("app.optimizer.GeneticOptimizer.run_backtest", flaky)
    ga = GeneticOptimizer(cfg, driver_restart=lambda: calls.__setitem__("restarts", calls["restarts"] + 1))
    out = ga.run({"populationSize": 2, "maxGenerations": 1, "seed": 1}, _candles())
    assert calls["restarts"] == 1
    assert out["bestIndividual"] is not None
    assert out["bestIndividual"].get("status") != "quarantined"


def test_genetic_run_body_defaults_are_blueprint_caps():
    from app.core import blueprint as bp
    from app.server.main import GeneticRunBody

    body = GeneticRunBody()
    assert body.populationSize == bp.GA_MAX_POPULATION == 15
    assert body.maxGenerations == bp.GA_MAX_GENERATIONS == 5
