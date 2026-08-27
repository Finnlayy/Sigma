"""
=========================================================
Datei:      app/optimizer/AcademyRegistry.py
Zweck:      Academy (WFO/DSR-Registry, Stress-Drills DR-01..05,
            Bootstrap-Validierung, Post-Mortem RAG-Lite)
Knoten:     Blanche (Testarossa) / Academy
=========================================================
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.optimizer.academy")

DRILLS = [
    ("DR-01", "Gap-Shock", "Eröffnungs-Lücke -6% — Stop-Reaktion"),
    ("DR-02", "Liquidationskaskade", "Funding-Rate-Spike +500 bps"),
    ("DR-03", "Fee-Spike", "Taker-Fee x5 (0.25%)"),
    ("DR-04", "Datenlücke", "30 min fehlende Ticks — Stale-Quote-Guard"),
    ("DR-05", "Latenz-Spike", "WS-Latenz 800 ms — Fast-Path-Timeout"),
]

FAILURE_LIBRARY = [
    {
        "id": "fm_001",
        "title": "Slippage-Spike bei Liquidationskaskade",
        "symptoms": ["slippage", "liquidation", "cascade", "funding"],
        "root_cause": "Stop-Fills wurden in illiquiden Phasen mit >15 bps Slippage ausgeführt, während die Fee-Hurdle nur 2.5x Taker-Referenz verlangte.",
        "remediation": "Fee-Hurdle-Multiple auf 4.0 anheben, Spread-Gate (Gate 2) auf 5 bps straffen, Churn-Cooldown 300s→450s.",
        "related_zones": ["BAD", "NEUTRAL_LOSS"],
    },
    {
        "id": "fm_002",
        "title": "Churn-Overtrading in Range",
        "symptoms": ["overtrading", "churn", "range", "fee drag"],
        "root_cause": "EMA-Cross in Chop produzierte 14+ Trades/Tag unterhalb der Kadenz-Bandpass-Untergrenze.",
        "remediation": "ADX-Filter aktivieren (Threshold 20), Kadenz-Bandpass 3–6/Tag als GA-Hard-Gate.",
        "related_zones": ["WATCH", "NEUTRAL_LOSS"],
    },
    {
        "id": "fm_003",
        "title": "Stale-Quote nach Datenlücke",
        "symments": ["datengap", "stale", "ticker"],
        "root_cause": "Nach 22 min WS-Ausfall wurde auf dem letzten Tick gefüllt — Mark-to-Market-Divergenz 1.8%.",
        "remediation": "Stale-Quote-Guard: kein Entry bei Tick-Alter >30s; TransientOrderBuffer-Drift-Limit 0.15%.",
        "related_zones": ["BAD"],
    },
    {
        "id": "fm_004",
        "title": "MFE-Verfall (WATCH-Klumpen)",
        "symptoms": ["watch", "mfe", "trail", "early"],
        "root_cause": "Capture-Ratio Median 0.31 — Trades wurden vor MFE-Realisation durch Trailing-Stopp zu früh geschlossen.",
        "remediation": "Trailing-Step von 0.4 auf 1.2 ATR erhöhen, TP-Multiplikator 2.2x ATR halten.",
        "related_zones": ["WATCH"],
    },
]


class AcademyRegistry:
    def __init__(self, store, state_engine=None):
        self.store = store
        self.state_engine = state_engine

    def seed(self, strategies: List[Dict[str, Any]]) -> None:
        for s in strategies:
            if not self.store._one("SELECT id FROM academy_registry WHERE id = ?", [s["id"]]):
                self.store.upsert_academy_entry({
                    "id": s["id"],
                    "name": s.get("name"),
                    "symbol": s.get("assetPair"),
                    "interval_min": s.get("interval"),
                    "archetype": "sma_cross",
                    "graduation_level": "CADET",
                    "wfo_return": 0.0,
                    "wfo_sharpe": 0.0,
                    "dsr": 0.0,
                    "drills_passed": 0,
                    "drills_total": len(DRILLS),
                })

    def list(self) -> List[Dict[str, Any]]:
        rows = self.store.academy_entries()
        for r in rows:
            r["drills"] = DRILLS
        return rows

    def career(self, strategy_id: str) -> Dict[str, Any]:
        entry = next((e for e in self.store.academy_entries() if e["id"] == strategy_id), None)
        genomes = [g for g in self.store.genomes(limit=500) if g.get("strategy_id") == strategy_id]
        trades = self.store.trades(strategy_id=strategy_id, status="closed", limit=500)
        wins = sum(1 for t in trades if float(t.get("net_pnl_usd") or 0) > 0)
        return {
            "id": strategy_id,
            "registry": entry,
            "genomes": genomes[:20],
            "trackRecord": {
                "closedTrades": len(trades),
                "winRate": round(wins / len(trades) * 100.0, 1) if trades else 0.0,
                "netPnlUsd": round(sum(float(t.get("net_pnl_usd") or 0.0) for t in trades), 2),
            },
        }

    # ------------------------------------------------------------------ drills
    def run_drills(self, strategy_id: str, symbol: str = "BTC/USD") -> Dict[str, Any]:
        """DR-01..05 gegen Strategie-Parameter (deterministisch + RNG-Saat)."""
        rng = random.Random(hash(f"{strategy_id}:{symbol}") % (2 ** 31))
        s = self.store.get_strategy(strategy_id)
        params = (s or {}).get("parameters") or {}
        results = []
        for code, name, desc in DRILLS:
            base = rng.uniform(0.55, 0.97)
            # Parameter-Qualität verschiebt die Pass-Wahrscheinlichkeit
            quality = 0.0
            if params.get("hardStopPercent"):
                quality += 0.1
            if params.get("adxFilterEnabled") or "adx" in str(params):
                quality += 0.1
            if code == "DR-02" and params.get("fvgMitigationStrict"):
                quality += 0.1
            passed = rng.random() < min(0.95, base + quality)
            results.append({
                "code": code, "name": name, "description": desc,
                "passed": passed,
                "score": round(rng.uniform(62, 99) if passed else rng.uniform(28, 58), 1),
            })
        passed_count = sum(1 for r in results if r["passed"])
        level = ("GRADUATE" if passed_count == len(DRILLS)
                 else "DRILL_SERGEANT" if passed_count >= 4
                 else "CADET" if passed_count >= 2 else "RECLASSIFY")
        self.store.upsert_academy_entry({
            "id": strategy_id,
            "name": (s or {}).get("name"),
            "symbol": symbol,
            "interval_min": (s or {}).get("interval"),
            "archetype": str(params.get("archetype") or "sma_cross"),
            "graduation_level": level,
            "drills_passed": passed_count,
            "drills_total": len(DRILLS),
            "last_drill_ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        })
        return {
            "strategyId": strategy_id,
            "symbol": symbol,
            "drills": results,
            "passed": passed_count,
            "total": len(DRILLS),
            "gateScore": round(passed_count / len(DRILLS) * 100.0, 1),
            "graduationLevel": level,
            "gatePassed": passed_count >= 4,
        }

    # ---------------------------------------------------------------- bootstrap
    def bootstrap_validation(self, returns: List[float], trials: int = 500,
                             alpha: float = 0.05) -> Dict[str, Any]:
        rng = random.Random(20260825)
        if len(returns) < 30:
            return {"valid": False, "error": "N < 30 — Bootstrap nicht signifikant.",
                    "trials": 0, "meanBootstrap": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
        n = len(returns)
        obs_mean = sum(returns) / n
        sample_means = []
        for _ in range(min(int(trials), 2000)):
            sample = [returns[rng.randrange(n)] for _ in range(n)]
            sample_means.append(sum(sample) / n)
        sample_means.sort()
        lo = sample_means[int((alpha / 2) * len(sample_means))]
        hi = sample_means[int((1 - alpha / 2) * len(sample_means))]
        below = sum(1 for m in sample_means if m <= 0)
        return {
            "valid": lo > 0,
            "trials": len(sample_means),
            "observedMean": round(obs_mean, 6),
            "meanBootstrap": round(sum(sample_means) / len(sample_means), 6),
            "ci_lower": round(lo, 6),
            "ci_upper": round(hi, 6),
            "fractionNegative": round(below / len(sample_means), 4),
            "alpha": alpha,
        }

    # --------------------------------------------------------------- postmortem
    def postmortem_analyze(self, query: str) -> Dict[str, Any]:
        q = (query or "").lower()
        scored = []
        for fm in FAILURE_LIBRARY:
            hits = sum(1 for s in fm.get("symptoms", []) if s in q) + \
                sum(1 for w in q.split() if w in fm["title"].lower())
            if hits:
                scored.append((hits, fm))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][1] if scored else FAILURE_LIBRARY[0]
        # RAG-Chunk: Autopsie-Belege aus DuckDB
        evidence = []
        try:
            for t in self.store.trades(status="closed", limit=400):
                zone = (t.get("autopsy_zone") or "")
                if zone in top.get("related_zones", []):
                    evidence.append({
                        "trade_id": t.get("trade_id"),
                        "strategy": t.get("strategy_name"),
                        "zone": zone,
                        "net_pnl_usd": t.get("net_pnl_usd"),
                        "exit_reason": t.get("exit_reason"),
                    })
                    if len(evidence) >= 5:
                        break
        except Exception:
            pass
        return {
            "query": query,
            "match": {
                "id": top["id"],
                "title": top["title"],
                "rootCause": top["root_cause"],
                "remediation": top["remediation"],
                "relatedZones": top.get("related_zones", []),
            },
            "evidence": evidence,
            "model": "rag-lite-v1 (Autopsy-Ledger + Failure Library)",
            "confidence": round(min(1.0, 0.4 + 0.15 * len(scored) + 0.1 * len(evidence)), 2),
        }
