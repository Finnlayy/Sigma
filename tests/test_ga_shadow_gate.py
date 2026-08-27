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
