"""
=========================================================
Datei:      app/optimizer/GeneticOptimizer.py
Zweck:      Walk-Forward GA (70/30 Split) mit Multi-Objective-Fitness
            DSR × ln(1+Return) × P_sample × P_complexity × P_cadence
            + Deflated Sharpe Ratio (DSR ≥ 95% Gate vor Shadow)
            + Counterfactual Replay (v1.2.0 'Still Missing' → umgesetzt)
Knoten:     Jaune (Carrera-Engine) / Optimizer
=========================================================
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.backtest.TvMcpBacktest import run_backtest
from app.backtest.tv_csv import params_to_csv, genes_from_parameter_csv

logger = logging.getLogger("app.optimizer.ga")

GENE_RANGES: Dict[str, tuple] = {
    "atrPeriod": (7, 30),
    "atrStopMultiplier": (1.0, 4.5),
    "atrTakeProfitMultiplier": (1.5, 8.0),
    "useTrailingAtr": (0, 1),
    "trailingAtrStep": (0.2, 2.0),
    "useVolumeFilter": (0, 1),
    "rvolThreshold": (1.0, 3.5),
    "useObvTrend": (0, 1),
    "useTrendFilter": (0, 1),
    "trendFastEma": (5, 35),
    "trendSlowEma": (35, 200),
    "adxFilterEnabled": (0, 1),
    "adxThreshold": (15, 35),
    "useFvgFilter": (0, 1),
    "fvgMinGapPercent": (0.05, 1.0),
    "fvgMitigationStrict": (0, 1),
    "useCisdFilter": (0, 1),
    "cisdLookback": (5, 25),
    "cisdDisplacementMult": (1.1, 2.5),
    "useMtfFilter": (0, 1),
    "mtfMultiplier": (3, 12),
    "mtfTrendEma": (20, 100),
    "riskPerTradePercent": (0.5, 5.0),
}
BOOL_GENES = {"useTrailingAtr", "useVolumeFilter", "useObvTrend", "useTrendFilter",
              "adxFilterEnabled", "useFvgFilter", "fvgMitigationStrict",
              "useCisdFilter", "useMtfFilter"}
INT_GENES = {"atrPeriod", "trendFastEma", "trendSlowEma", "adxThreshold",
             "cisdLookback", "mtfMultiplier", "mtfTrendEma"}


def random_genes(rng: random.Random) -> Dict[str, Any]:
    genes = {}
    for name, (lo, hi) in GENE_RANGES.items():
        if name in BOOL_GENES:
            genes[name] = 1 if rng.random() < 0.4 else 0
        elif name in INT_GENES:
            genes[name] = int(round(rng.uniform(lo, hi)))
        else:
            genes[name] = round(rng.uniform(lo, hi), 4)
    return genes


def mutate(genes: Dict[str, Any], rate: float, rng: random.Random) -> Dict[str, Any]:
    out = dict(genes)
    for name, (lo, hi) in GENE_RANGES.items():
        if rng.random() < rate:
            if name in BOOL_GENES:
                out[name] = 1 - out[name]
            elif name in INT_GENES:
                out[name] = int(min(hi, max(lo, out[name] + rng.randint(-3, 3))))
            else:
                span = hi - lo
                out[name] = round(min(hi, max(lo, out[name] + rng.gauss(0, span * 0.15))), 4)
    # Slow EMA muss immer > Fast EMA bleiben
    if out.get("trendSlowEma", 60) <= out.get("trendFastEma", 12):
        out["trendSlowEma"] = int(out.get("trendFastEma", 12) * 4 + 8)
    return out


def crossover(a: Dict[str, Any], b: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    return {k: (a[k] if rng.random() < 0.5 else b[k]) for k in a}


def genes_to_params(genes: Dict[str, Any]) -> Dict[str, Any]:
    """Genome → Pine strategy inputs (round-trippable via parameter CSV)."""
    params: Dict[str, Any] = {}
    for k, v in genes.items():
        if k in GENE_RANGES:
            params[k] = v
    # Compatibility aliases commonly used in Pine input names / TV CSV exports
    fast = int(params.get("trendFastEma", genes.get("trendFastEma", 12)))
    slow = int(params.get("trendSlowEma", genes.get("trendSlowEma", 48)))
    if slow <= fast:
        slow = int(fast * 4 + 8)
        params["trendSlowEma"] = slow
    params.setdefault("trendFastEma", fast)
    params.setdefault("trendSlowEma", slow)
    params["smaFast"] = min(35, max(5, fast))
    params["smaSlow"] = min(200, max(params["smaFast"] + 4, slow))
    params["hardStopPercent"] = round(
        min(8.0, max(2.0, float(params.get("atrStopMultiplier", 1.5)) * 1.8)), 2
    )
    return params


def params_to_parameter_csv(genes_or_params: Dict[str, Any]) -> str:
    """Serialize gene/params dict to TradingView parameter CSV."""
    return params_to_csv(genes_to_params(genes_or_params) if any(k in GENE_RANGES for k in genes_or_params) else genes_or_params)


def genes_from_tv_parameter_csv(src: str) -> Dict[str, Any]:
    """Import GA genes from a TradingView parameter CSV export."""
    return genes_from_parameter_csv(src, GENE_RANGES)


# ------------------------------------------------------------- DSR (Bailey/López)
def deflated_sharpe_ratio(returns: List[float], trials: int = 50) -> float:
    """Deflated Sharpe Ratio (Bailey / López de Prado / Zhu 2014).

    DSR = Φ( (SR̄ - SR0) · √(T-1) / √(1 - γ3·SR̄ + (γ4-1)/4·SR̄²) )

    SR̄:  periodweise Sample-Sharpe (ohne Annualisierung)
    SR0:  E[max] von 'trials' Null-Verteilungs-SRs in per-Period-Einheiten
          = [ (1-λ)Φ⁻¹(1-1/N) + λΦ⁻¹(1-1/(N·e)) ] / √T,  λ = 0.5772
    """
    if len(returns) < 30:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    if sd == 0:
        return 0.0
    sr = mean / sd
    g3 = (sum((r - mean) ** 3 for r in returns) / n) / sd ** 3
    g4 = (sum((r - mean) ** 4 for r in returns) / n) / sd ** 4
    lam = 0.5772156649
    emax_std = ((1 - lam) * _ndtri(1 - 1 / max(trials, 2))
                + lam * _ndtri(1 - 1 / (max(trials, 2) * math.e)))
    sr0 = emax_std / math.sqrt(n)
    denom = math.sqrt(max(1e-9, 1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    z = (sr - sr0) * math.sqrt(n - 1) / denom
    return _normal_cdf(z)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _ndtri(p: float) -> float:
    """Acklam's inverse normal CDF."""
    p = min(max(p, 1e-12), 1 - 1e-12)
    if p < 0.5:
        return -_ndtri(1 - p)
    t = math.sqrt(-2 * math.log(1 - p))
    c0, c1, c2 = 2.51551716e-1, 2.43753479e-1, 5.63989648e-1
    d1, d2, d3 = 1.63645629e-1, 3.87763674e-1, 2.88490754e-1
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)


# ------------------------------------------------------------- Fitness (Frontend-Port)
def multi_objective_fitness(summary: Dict[str, Any], metrics_extra: Dict[str, Any],
                            cfg) -> Dict[str, Any]:
    """Port von src/optimizer/MultiObjectiveFitnessEngine.ts (Frontend-Vertrag)."""
    total_trades = int(summary.get("totalTrades") or 0)
    net_pnl = float(summary.get("finalBalance") or 0.0) - float(summary.get("initialBalance") or 0.0)
    dsr = float(metrics_extra.get("dsr") or 0.0)
    days = max(1, float(metrics_extra.get("evaluationDays") or 30))
    annual_net_return_pct = max(0.0, (net_pnl / max(1.0, float(summary.get("initialBalance") or 1.0))) * (365.0 / days) * 100.0)

    if total_trades < cfg.ga_min_trades_absolute:
        return {"fitnessScore": 0.0, "isValidCandidate": False,
                "rejectionReason": f"🚨 [TRADE STARVATION] Nur {total_trades} Trades (min {cfg.ga_min_trades_absolute}).",
                "samplePenalty": 0.0, "complexityPenalty": 1.0}
    if net_pnl <= 0:
        return {"fitnessScore": 0.0, "isValidCandidate": False,
                "rejectionReason": f"💸 [FEE DRAG DEATH] Netto-P&L negativ (${net_pnl:.2f}).",
                "samplePenalty": 1.0, "complexityPenalty": 1.0}

    sample_penalty = 1.0
    if total_trades < cfg.ga_min_trades_target:
        num = total_trades - cfg.ga_min_trades_absolute
        den = cfg.ga_min_trades_target - cfg.ga_min_trades_absolute
        sample_penalty = (num / den) ** 2 if den > 0 else 0.0

    active_rules = int(metrics_extra.get("activeRuleCount") or 3)
    complexity_penalty = 1.0
    if active_rules > cfg.ga_max_allowed_rules:
        complexity_penalty = math.exp(-0.15 * (active_rules - cfg.ga_max_allowed_rules))

    net_return_factor = math.log(1 + annual_net_return_pct)
    cadence_score = metrics_extra.get("cadenceScore")
    cadence_mult = cadence_score if isinstance(cadence_score, (int, float)) else 1.0

    raw = dsr * net_return_factor * sample_penalty * complexity_penalty * cadence_mult
    final = max(0.0, raw)
    is_valid = final > cfg.ga_fitness_threshold and dsr >= cfg.ga_dsr_gate
    return {
        "fitnessScore": round(final, 4),
        "isValidCandidate": bool(is_valid),
        "samplePenalty": round(sample_penalty, 4),
        "complexityPenalty": round(complexity_penalty, 4),
        "cadenceScore": round(float(cadence_mult), 4),
        "rejectionReason": None if is_valid else
        f"⚠️ Fitness {final:.4f} / DSR {dsr:.2f} unter Schwellenwert "
        f"({cfg.ga_fitness_threshold} / {cfg.ga_dsr_gate}).",
    }


def cadence_score(trades_per_day: float, cfg) -> float:
    """Gauß'scher Bandpass 3–6 Trades/Tag (ideal 4.5), σ=1.25 — Port des TS-Moduls."""
    ideal = (cfg.ga_cadence_min + cfg.ga_cadence_max) / 2.0
    sigma = 1.25
    diff = trades_per_day - ideal
    return math.exp(-(diff * diff) / (2 * sigma * sigma))


# ------------------------------------------------------------------- GA Runner
class GeneticOptimizer:
    def __init__(self, config=None):
        from app.core.config import load_config

        self.config = config or load_config()

    def run(self, cfg: Dict[str, Any], candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Vollständiger WFO-Run. candles = vollständiger 1m-Historie (ts, o,h,l,c,v)."""
        rng = random.Random(int(cfg.get("seed") or 1337))
        pop_size = int(cfg.get("populationSize") or 30)
        max_gens = int(cfg.get("maxGenerations") or 50)
        survivors = int(cfg.get("survivorsCount") or 3)
        mutation_rate = float(cfg.get("mutationRate") or 0.18)
        crossover_rate = float(cfg.get("crossoverRate") or 0.80)
        wfo_split = float(cfg.get("walkForwardSplitPercent") or 70.0) / 100.0
        initial_balance = float(cfg.get("initialBalance") or 10_000.0)
        fee_pct = float(cfg.get("feePercent") or 0.26)
        slippage_pct = float(cfg.get("slippagePercent") or 0.05)

        n = len(candles)
        if n < 240:
            raise ValueError("WFO benötigt mindestens 240 Candles (bisher %d)." % n)
        split = int(n * wfo_split)
        is_candles = candles[:split]
        oos_candles = candles[split:]
        is_days = max(1, split / 96.0)
        oos_days = max(1, len(oos_candles) / 96.0)

        baseline_genes: Optional[Dict[str, Any]] = None
        if cfg.get("baselineStrategyId"):
            baseline_genes = self._genes_from_baseline(cfg)

        population: List[Dict[str, Any]] = []
        if baseline_genes:
            population.append({"id": "baseline", "generation": 0,
                               "genes": baseline_genes, "isBaselineSeed": True})
        while len(population) < pop_size:
            population.append({"id": f"ind_{uuid.uuid4().hex[:8]}", "generation": 0,
                               "genes": random_genes(rng)})

        history: List[Dict[str, Any]] = []
        best_individual: Optional[Dict[str, Any]] = None

        for gen in range(1, max_gens + 1):
            gen_fitness: List[float] = []
            for ind in population:
                params = genes_to_params(ind["genes"])
                bt_cfg_is = {
                    "strategyId": cfg.get("baselineStrategyId"),
                    "pineStrategyId": cfg.get("pineStrategyId") or cfg.get("baselineStrategyId"),
                    "assetPair": cfg.get("assetPair"),
                    "interval": cfg.get("interval"),
                    "initialBalance": initial_balance,
                    "feePercent": fee_pct,
                    "slippagePercent": slippage_pct,
                    "hardStopEnabled": True,
                    "hardStopPercent": params.get("hardStopPercent", 4.0),
                    "customParameters": params,
                    "parametersCsv": params_to_csv(params),
                    "split": "is",
                    "windowFrom": is_candles[0]["ts"] if is_candles else None,
                    "windowTo": is_candles[-1]["ts"] if is_candles else None,
                }
                bt_cfg_oos = {**bt_cfg_is, "split": "oos",
                              "windowFrom": oos_candles[0]["ts"] if oos_candles else None,
                              "windowTo": oos_candles[-1]["ts"] if oos_candles else None}
                is_bt = run_backtest(is_candles, bt_cfg_is)
                oos_bt = run_backtest(oos_candles, bt_cfg_oos)
                is_returns = self._equity_returns(is_bt["equityCurve"])
                oos_returns = self._equity_returns(oos_bt["equityCurve"])
                dsr_is = deflated_sharpe_ratio(is_returns, trials=pop_size * max_gens)
                dsr_oos = deflated_sharpe_ratio(oos_returns, trials=max(pop_size, 10))
                dsr = 0.5 * dsr_is + 0.5 * dsr_oos
                overall_trades = int(is_bt["summary"]["totalTrades"]) + int(oos_bt["summary"]["totalTrades"])
                overall_days = is_days + oos_days
                tpd = overall_trades / overall_days if overall_days else 0.0
                active_rules = 2 + sum(1 for k in BOOL_GENES
                                       if ind["genes"].get(k) == 1) + \
                    (1 if ind["genes"].get("adxFilterEnabled") else 0)
                extra = {
                    "dsr": dsr,
                    "evaluationDays": overall_days,
                    "activeRuleCount": active_rules,
                    "cadenceScore": cadence_score(tpd, self.config),
                }
                fitness = multi_objective_fitness(
                    {**is_bt["summary"], "finalBalance":
                     is_bt["summary"]["finalBalance"] +
                     oos_bt["summary"]["finalBalance"] - initial_balance,
                     "initialBalance": initial_balance},
                    extra, self.config)
                robustness = (oos_bt["summary"]["sharpeRatio"] /
                              is_bt["summary"]["sharpeRatio"] * 100.0) \
                    if is_bt["summary"]["sharpeRatio"] > 0.01 else 0.0
                ind.update({
                    "generation": gen,
                    "fitness": fitness["fitnessScore"],
                    "isValidCandidate": fitness["isValidCandidate"],
                    "rejectionReason": fitness["rejectionReason"],
                    "inSampleSummary": is_bt["summary"],
                    "outOfSampleSummary": oos_bt["summary"],
                    "overallReturn": round(
                        is_bt["summary"]["totalReturnPercent"] +
                        oos_bt["summary"]["totalReturnPercent"], 4),
                    "overallDrawdown": round(
                        max(is_bt["summary"]["maxDrawdownPercent"],
                            oos_bt["summary"]["maxDrawdownPercent"]), 4),
                    "sharpeRatio": round(
                        0.5 * is_bt["summary"]["sharpeRatio"] +
                        0.5 * oos_bt["summary"]["sharpeRatio"], 4),
                    "winRate": round(
                        0.5 * is_bt["summary"]["winRate"] +
                        0.5 * oos_bt["summary"]["winRate"], 2),
                    "tradesCount": overall_trades,
                    "tradesPerDay": round(tpd, 3),
                    "dsr": round(dsr, 4),
                    "dsrInSample": round(dsr_is, 4),
                    "dsrOutOfSample": round(dsr_oos, 4),
                    "robustnessIndex": round(robustness, 2),
                })
                gen_fitness.append(fitness["fitnessScore"])

            population.sort(key=lambda x: x["fitness"], reverse=True)
            for i, ind in enumerate(population):
                ind["rank"] = i + 1
                ind["isSurvivor"] = i < survivors
            best = population[0]
            if best_individual is None or best["fitness"] > best_individual["fitness"]:
                best_individual = dict(best)
            history.append({
                "generation": gen,
                "bestFitness": round(best["fitness"], 4),
                "avgFitness": round(sum(gen_fitness) / len(gen_fitness), 4),
                "bestReturn": best["overallReturn"],
                "bestSharpe": best["sharpeRatio"],
                "bestDrawdown": best["overallDrawdown"],
                "bestIndividualId": best["id"],
            })

            # Eliten + Crossover + Mutation
            new_pop = [dict(ind) for ind in population[:survivors]]
            while len(new_pop) < pop_size:
                pa, pb = rng.sample(population[: max(survivors, 2)], 2)
                if rng.random() < crossover_rate:
                    genes = crossover(pa["genes"], pb["genes"], rng)
                else:
                    genes = dict(pa["genes"])
                genes = mutate(genes, mutation_rate, rng)
                new_pop.append({"id": f"ind_{uuid.uuid4().hex[:8]}",
                                "generation": gen + 1, "genes": genes})
            population = new_pop

        # ------------------------------------------------------------------ Gate
        gate = self.shadow_gate(best_individual)
        # Counterfactual Replay: Best-Genom auf den letzten 200 Candles (Shadow-Überlappung)
        replay = self.counterfactual_replay(best_individual, candles,
                                            fee_pct=fee_pct, slippage=slippage_pct,
                                            initial_balance=initial_balance)

        baseline_individual = None
        baseline_comparison = None
        if baseline_genes is not None:
            baseline_individual = next((p for p in population if p.get("isBaselineSeed")), None)
            if baseline_individual is None:
                baseline_individual = self._evaluate_single(
                    "baseline", 0, baseline_genes, is_candles, oos_candles,
                    is_days, oos_days, fee_pct, slippage_pct, initial_balance,
                    pop_size, max_gens)
            b, e = baseline_individual["overallReturn"], best_individual["overallReturn"]
            baseline_comparison = {
                "returnDelta": round(e - b, 4),
                "sharpeDelta": round(best_individual["sharpeRatio"] - baseline_individual["sharpeRatio"], 4),
                "winRateDelta": round(best_individual["winRate"] - baseline_individual["winRate"], 2),
                "drawdownDelta": round(best_individual["overallDrawdown"] - baseline_individual["overallDrawdown"], 4),
                "baselineFitness": baseline_individual["fitness"],
                "evolvedFitness": best_individual["fitness"],
                "improvementPercent": round((e - b) / abs(b) * 100.0, 2) if b else 0.0,
                "isBetter": e > b,
            }

        return {
            "id": f"ga_{uuid.uuid4().hex[:10]}",
            "assetPair": cfg.get("assetPair"),
            "interval": cfg.get("interval"),
            "totalGenerationsCompleted": len(history),
            "populationSize": pop_size,
            "survivorCount": survivors,
            "bestIndividual": best_individual,
            "topSurvivors": population[: survivors],
            "population": population,
            "history": history,
            "inSampleCandles": split,
            "outOfSampleCandles": len(oos_candles),
            "generatedCode": _generated_code(best_individual["genes"]),
            "baselineStrategyId": cfg.get("baselineStrategyId"),
            "baselineStrategyName": cfg.get("baselineStrategyName"),
            "baselineIndividual": baseline_individual,
            "baselineComparison": baseline_comparison,
            "shadowGate": gate,
            "counterfactualReplay": replay,
        }

    # ------------------------------------------------------------------ helpers
    def shadow_gate(self, individual: Dict[str, Any]) -> Dict[str, Any]:
        """Phase-4-Acceptance: WFO + DSR > 95% VOR Shadow."""
        dsr = float(individual.get("dsr") or 0.0)
        tpd = float(individual.get("tradesPerDay") or 0.0)
        n_trades = int(individual.get("tradesCount") or 0)
        checks = {
            "dsr_above_gate": dsr >= self.config.ga_dsr_gate,
            "cadence_in_band": self.config.ga_cadence_min <= tpd <= self.config.ga_cadence_max,
            "sample_size_ok": n_trades >= self.config.ga_min_trades_absolute,
            "valid_candidate": bool(individual.get("isValidCandidate")),
        }
        passed = all(checks.values())
        return {
            "passed": passed,
            "checks": checks,
            "dsr": round(dsr, 4),
            "requiredDsr": self.config.ga_dsr_gate,
            "verdict": "SHADOW-APPROVED" if passed else "REJECTED — kein Shadow-Deployment",
        }

    def counterfactual_replay(self, individual: Dict[str, Any],
                              candles: List[Dict[str, Any]],
                              fee_pct: float, slippage: float,
                              initial_balance: float,
                              tail: int = 200) -> Dict[str, Any]:
        """'Still Missing' v1.2.0: replayt das Beste Genom auf dem Shadow-Fenster
        und vergleicht mit dem Live-Track-Record der Strategie."""
        from app.core.duckdb_store import get_store

        tail_candles = candles[-tail:]
        params = genes_to_params(individual["genes"])
        bt = run_backtest(tail_candles, {
            "assetPair": individual.get("assetPair") or "BTC/USD",
            "initialBalance": initial_balance,
            "feePercent": fee_pct,
            "slippagePercent": slippage,
            "hardStopEnabled": True,
            "hardStopPercent": params.get("hardStopPercent", 4.0),
            "customParameters": params,
            "parametersCsv": params_to_csv(params),
            "split": "replay",
        })
        summary = bt["summary"]
        try:
            store = get_store()
            live_trades = store.trades(strategy_id=individual.get("strategyId"),
                                       status="closed", limit=500)
            live_net = sum(float(t.get("net_pnl_usd") or 0.0) for t in live_trades)
            live_n = len(live_trades)
        except Exception:
            live_net, live_n = 0.0, 0
        return {
            "windowCandles": len(tail_candles),
            "replayTrades": summary["totalTrades"],
            "replayNetPnlUsd": round(summary["totalReturnUSD"], 2),
            "replayReturnPct": round(summary["totalReturnPercent"], 4),
            "replaySharpe": summary["sharpeRatio"],
            "liveTrades": live_n,
            "liveNetPnlUsd": round(live_net, 2),
            "divergencePct": round(
                (summary["totalReturnUSD"] - live_net) / max(1.0, abs(live_net)), 4
            ) if live_n else None,
            "note": "Counterfactual: Genom vs. Live-Track-Record im Überlappungsfenster",
        }

    def _equity_returns(self, equity_curve: List[Dict[str, Any]]) -> List[float]:
        out = []
        prev = None
        for p in equity_curve:
            e = float(p.get("equity") or 0.0)
            if prev and prev > 0:
                out.append(e / prev - 1.0)
            prev = e
        return out

    def _genes_from_baseline(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if cfg.get("seedGenes"):
            base = random_genes(random.Random(7))
            base.update({k: v for k, v in cfg["seedGenes"].items() if k in GENE_RANGES})
            return base
        rng = random.Random(hash(str(cfg.get("baselineStrategyId"))) % (2 ** 31))
        genes = random_genes(rng)
        genes["trendFastEma"] = 12
        genes["trendSlowEma"] = 60
        return genes

    def _evaluate_single(self, ind_id, generation, genes, is_candles, oos_candles,
                         is_days, oos_days, fee_pct, slippage_pct, initial_balance,
                         pop_size, max_gens) -> Dict[str, Any]:
        params = genes_to_params(genes)
        bt_cfg = {
            "assetPair": "BTC/USD", "interval": 15,
            "initialBalance": initial_balance, "feePercent": fee_pct,
            "slippagePercent": slippage_pct, "hardStopEnabled": True,
            "hardStopPercent": params.get("hardStopPercent", 4.0),
            "customParameters": params,
            "parametersCsv": params_to_csv(params),
        }
        is_bt = run_backtest(is_candles, {**bt_cfg, "split": "is"})
        oos_bt = run_backtest(oos_candles, {**bt_cfg, "split": "oos"})
        dsr = 0.5 * deflated_sharpe_ratio(self._equity_returns(is_bt["equityCurve"]),
                                          trials=pop_size * max_gens) + \
            0.5 * deflated_sharpe_ratio(self._equity_returns(oos_bt["equityCurve"]),
                                         trials=max(pop_size, 10))
        return {
            "id": ind_id, "generation": generation, "genes": genes,
            "isBaselineSeed": True,
            "fitness": 0.0,
            "inSampleSummary": is_bt["summary"],
            "outOfSampleSummary": oos_bt["summary"],
            "overallReturn": round(is_bt["summary"]["totalReturnPercent"] +
                                   oos_bt["summary"]["totalReturnPercent"], 4),
            "overallDrawdown": round(max(is_bt["summary"]["maxDrawdownPercent"],
                                         oos_bt["summary"]["maxDrawdownPercent"]), 4),
            "sharpeRatio": round(0.5 * is_bt["summary"]["sharpeRatio"] +
                                 0.5 * oos_bt["summary"]["sharpeRatio"], 4),
            "winRate": round(0.5 * is_bt["summary"]["winRate"] +
                             0.5 * oos_bt["summary"]["winRate"], 2),
            "tradesCount": int(is_bt["summary"]["totalTrades"]) + int(oos_bt["summary"]["totalTrades"]),
            "dsr": round(dsr, 4),
            "robustnessIndex": 0.0,
            "rank": -1, "isSurvivor": False,
        }


def _generated_code(genes: Dict[str, Any]) -> str:
    """Evolved Pine strategy stub with input.* names matching parameter CSV columns."""
    return f"""// Evolved Genome — Projekt:Sigma GA (Pine inputs via TV parameter CSV)
//@version=6
// ATR Stop x{genes.get('atrStopMultiplier', 2.0):.2f} | TP x{genes.get('atrTakeProfitMultiplier', 3.0):.2f}
// EMA {genes.get('trendFastEma', 12)}/{genes.get('trendSlowEma', 60)} | Risk {genes.get('riskPerTradePercent', 1.0):.1f}%
strategy("Sigma Evolved", overlay=true)
fastLen = input.int({int(genes.get('trendFastEma', 12))}, "trendFastEma")
slowLen = input.int({int(genes.get('trendSlowEma', 60))}, "trendSlowEma")
atrMult = input.float({float(genes.get('atrStopMultiplier', 2.0)):.2f}, "atrStopMultiplier")
"""

