"""
=========================================================
Datei:      app/server/routes_quant.py
Zweck:      Quant-Pillar-Endpunkte (M-00..M-17), Academy, Data Lake,
            M8-Admin (Phase 2/3/4 LAN events)
Knoten:     Noir (Diablo-Judge) / API
=========================================================
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.backtest.BacktestEngine import resample_candles
from app.core import blueprint as bp
from app.core.event_bus import EventBus
from app.quant.RegimeEngine import (ampel_status, dfa_hurst,
                                    lead_lag_matrix, sentiment_score)
from app.server.main import state

router = APIRouter()


# =====================================================================
# M-00 STATE MACHINE
# =====================================================================
class SetStateBody(BaseModel):
    state: str
    reason: Optional[str] = None


@router.post("/api/quant/state-machine/set-state")
async def set_state(body: SetStateBody):
    try:
        result = state.telemetry.set_state(body.state, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    state.bus.log("info", f"System-State → {body.state} ({body.reason or 'ui'})",
                  category="CIRCUIT_BREAKER" if body.state == "EMERGENCY_HALT" else "SYSTEM")
    if body.state == "EMERGENCY_HALT":
        state.paper.cancel_all(current_prices={
            sym: state.ingestor.last_price(sym) for sym in state.config.market_symbols
        })
    return {"ok": True, "state_machine": result}


# =====================================================================
# M-09 JUDGE + M-02 IMPACT + M-12 RECONCILIATION + M-16 RL FAST PATH
# =====================================================================
class JudgeBody(BaseModel):
    symbol: str = "BTC/USD"
    qty: float = 0.5
    side: str = "BUY"
    winRate: float = 0.60
    winLossRatio: float = 1.8
    targetVol: float = 0.15


@router.post("/api/quant/execution/m8-judge")
async def m8_judge(body: JudgeBody):
    closes = [c["close"] for c in state.store.ohlcv(body.symbol, 60, limit=200)]
    from app.server.main import _realized_vol

    verdict = state.judge.evaluate(
        body.symbol, body.qty, body.side, body.winRate, body.winLossRatio,
        body.targetVol, context={
            "realized_vol": _realized_vol(closes[-96:]) if len(closes) > 30 else 0.024,
            "spread_bps": 3.0,
            "hurst_regime": dfa_hurst(closes)["regime"] if len(closes) > 128 else "RANDOM_WALK",
            "system_state": state.telemetry.system.state,
        })
    return verdict


class ImpactBody(BaseModel):
    symbol: str = "BTC/USD"
    orderQty: float = 2.5
    side: str = "BUY"
    dailyVolume: float = 5000


@router.post("/api/quant/market-impact/simulate")
async def market_impact(body: ImpactBody):
    """Almgren-Chriss Square-Root Impact Model."""
    part = abs(body.orderQty) / max(1e-9, body.dailyVolume)
    k = 12.0  # Impact-Koeffizient (Krypto-Referenz)
    permanent_bps = k * math.sqrt(part) * 100.0
    temporary_bps = 8.0 * math.sqrt(part) * 100.0
    price = state.ingestor.last_price(body.symbol)
    notional = abs(body.orderQty) * price
    cost = notional * (permanent_bps + temporary_bps) / 10_000.0
    return {
        "symbol": body.symbol,
        "orderQty": body.orderQty,
        "participation": round(part, 8),
        "permanent_impact_bps": round(permanent_bps, 4),
        "temporary_impact_bps": round(temporary_bps, 4),
        "estimated_cost_usd": round(cost, 2),
        "model": "Almgren-Chriss sqrt(participation)",
    }


@router.post("/api/quant/reconciliation/run")
async def reconciliation_run():
    """M-12: Redis-M8-Budgets vs. DuckDB-Write-Through Abgleich + Auto-Heal."""
    redis_states = await state.m8.scan_states()
    db_rows = {b["instance_id"]: b for b in state.store.all_budgets()}
    discrepancies = []
    healed = 0
    for iid, rs in redis_states.items():
        db = db_rows.get(iid)
        if db is None:
            state.store.sync_budget(rs)
            healed += 1
            continue
        if (abs(float(db.get("current_budget_usd") or 0) - float(rs.get("current_budget_usd") or 0)) > 1e-6
                or db.get("status") != rs.get("status")):
            state.store.sync_budget(rs)
            discrepancies.append({"instance_id": iid,
                                  "redis": rs.get("status"),
                                  "duckdb": db.get("status")})
            healed += 1
    vault_redis = None
    if state.redis:
        try:
            vault_redis = float(await state.redis.hget("vault:balance", "total") or 0)
        except Exception:
            vault_redis = None
    vault_db = state.store.vault_balance()
    return {
        "reconciled": not discrepancies or healed == len(discrepancies),
        "instances_checked": len(redis_states),
        "discrepancies_found": len(discrepancies),
        "healed": healed,
        "vault": {"redis": vault_redis, "duckdb": round(vault_db, 4),
                  "in_sync": vault_redis is None or abs(vault_redis - vault_db) < 0.01},
        "autoHeal": "ARMED & ACTIVE",
        "timestamp": time.time(),
    }


@router.post("/api/quant/engine/rl-fast-path")
async def rl_fast_path():
    """M-16: [MOCK-SEAM] RL-Policy-Inferenz — deterministischer Platzhalter
    (echtes PPO-Modell im LAN-Produktionsbetrieb anbinden)."""
    t0 = time.perf_counter()
    # 'Inferenz' = state-geleitetes Determinierung (sub-ms)
    can_execute = state.telemetry.system.can_execute_orders
    action = "EXECUTE_IMMEDIATE_POST" if can_execute else "HOLD_AND_REQUEUE"
    q_value = 0.842 if can_execute else 0.121
    elapsed = (time.perf_counter() - t0) * 1000
    return {
        "inference_time_ms": round(max(elapsed, 0.4), 3),
        "action_name": action,
        "q_value": round(q_value, 4),
        "fast_path_share": 0.945 if can_execute else 0.0,
        "safe_path_share": 0.055 if can_execute else 1.0,
        "model": "deterministic-policy-proxy [MOCK]",
    }


# =====================================================================
# M-03 DFA / M-10 AMPEL / M-14 LEAD-LAG / M-15 SENTIMENT
# =====================================================================
@router.get("/api/quant/dfa/hurst")
async def dfa_hurst_endpoint(symbol: str = "BTC/USD"):
    closes = [c["close"] for c in state.store.ohlcv(symbol, 60, limit=1200)]
    return dfa_hurst(closes)


@router.get("/api/quant/regime/ampel")
async def ampel_endpoint(symbol: str = "BTC/USD"):
    closes = [c["close"] for c in state.store.ohlcv(symbol, 60, limit=300)]
    return ampel_status(symbol, closes)


@router.get("/api/quant/lead-lag/cross-impact")
async def lead_lag():
    series = {}
    for sym in state.config.market_symbols:
        series[sym] = [c["close"] for c in state.store.ohlcv(sym, 60, limit=600)]
    return lead_lag_matrix(list(state.config.market_symbols), series, max_lag=5)


class SentimentBody(BaseModel):
    text: str


@router.post("/api/quant/sentiment/score")
async def sentiment(body: SentimentBody):
    return sentiment_score(body.text)


# =====================================================================
# ACADEMY (WFO/DSR Registry, Drills, Bootstrap, Post-Mortem)
# =====================================================================
@router.get("/api/academy/strategies")
async def academy_strategies():
    return state.academy.list()


@router.get("/api/academy/strategies/{strategy_id}/career")
async def academy_career(strategy_id: str):
    return state.academy.career(strategy_id)


class DrillsBody(BaseModel):
    strategyId: str
    symbol: str = "BTC/USD"


@router.post("/api/academy/drills/run")
async def drills_run(body: DrillsBody):
    return await asyncio.to_thread(state.academy.run_drills, body.strategyId, body.symbol)


class BootstrapBody(BaseModel):
    trials: int = 500
    strategyId: Optional[str] = None


@router.post("/api/quant/validation/bootstrap")
async def validation_bootstrap(body: BootstrapBody):
    trades = state.store.trades(strategy_id=body.strategyId, status="closed", limit=1000)
    returns = [float(t.get("net_pnl_usd") or 0.0) for t in trades]
    return state.academy.bootstrap_validation(returns, trials=body.trials)


class PostmortemBody(BaseModel):
    failureQuery: str


@router.post("/api/quant/postmortem/analyze")
async def postmortem(body: PostmortemBody):
    return state.academy.postmortem_analyze(body.failureQuery)


class EvolutionBody(BaseModel):
    maxGenerations: int = bp.GA_MAX_GENERATIONS
    populationSize: int = bp.GA_MAX_POPULATION
    assetPair: str = "BTC/USD"
    interval: int = 15
    candleCount: int = 500


@router.post("/api/quant/evolution/run")
async def evolution_run(body: EvolutionBody):
    """Differential-Evolution-Variante (kleiner GA-Run) für die Academy."""
    candles = state.store.ohlcv(body.assetPair, 60, limit=max(
        body.candleCount * body.interval + 120, 300))
    candles = resample_candles(candles, max(1, body.interval))[-body.candleCount:]
    if len(candles) < 240:
        raise HTTPException(400, f"WFO benötigt ≥240 Candles — vorhanden: {len(candles)}.")
    result = await asyncio.to_thread(state.ga.run, {
        "populationSize": min(max(1, int(body.populationSize)), bp.GA_MAX_POPULATION),
        "maxGenerations": min(max(1, int(body.maxGenerations)), bp.GA_MAX_GENERATIONS),
        "survivorsCount": 3,
        "mutationRate": 0.25,
        "crossoverRate": 0.7,
        "walkForwardSplitPercent": 70,
        "assetPair": body.assetPair,
        "interval": body.interval,
        "candleCount": body.candleCount,
        "initialBalance": 10000,
        "feePercent": 0.26,
        "slippagePercent": 0.05,
    }, candles)
    return {
        "bestFitness": result["bestIndividual"]["fitness"],
        "bestReturn": result["bestIndividual"]["overallReturn"],
        "bestDsr": result["bestIndividual"]["dsr"],
        "generations": result["totalGenerationsCompleted"],
        "shadowGate": result["shadowGate"],
        "bestGenes": result["bestIndividual"]["genes"],
    }


# =====================================================================
# DATA LAKE (L2 DuckDB/Parquet)
# =====================================================================
@router.get("/api/lake/summary")
async def lake_summary():
    return state.store.lake_summary()


class LakeSeedBody(BaseModel):
    symbol: str = "BTC/USD"
    interval: str = "1m"
    candleCount: int = 720
    start: Optional[str] = None


@router.post("/api/lake/seed")
async def lake_seed(body: LakeSeedBody):
    """Seedet/synchronisiert OHLCV aus dem (synthetischen) Feed."""
    ing = state.ingestor
    candles = state.store.ohlcv(body.symbol, 60, limit=max(1, body.candleCount))
    if not candles:
        ing.seed_history(candles_per_symbol=body.candleCount)
        candles = state.store.ohlcv(body.symbol, 60, limit=body.candleCount)
    state.bus.log("info", f"Lake-Seed: {len(candles)} Candles {body.symbol}", category="LAKE")
    return {"ok": True, "candles_count": len(candles), "symbol": body.symbol}


@router.get("/api/lake/query")
async def lake_query(symbol: str = "BTC/USD", limit: int = 50):
    rows = state.store.lake_query(symbol, limit=limit)
    return {
        "symbol": symbol,
        "total": len(rows),
        "records": [{"timestamp": r["ts"], "open": r["open"], "high": r["high"],
                     "low": r["low"], "close": r["close"], "volume": r["volume"]}
                    for r in rows],
    }


class LakeResampleBody(BaseModel):
    symbol: str = "BTC/USD"
    interval: str = "15m"
    limit: int = 96


@router.post("/api/lake/resample")
async def lake_resample(body: LakeResampleBody):
    minutes = _interval_minutes(body.interval)
    candles = state.store.ohlcv(body.symbol, 60, limit=max(minutes * body.limit + 60, 300))
    out = resample_candles(candles, minutes)[-body.limit:]
    return {
        "symbol": body.symbol,
        "interval": body.interval,
        "records": [{"timestamp": c["ts"], "open": c["open"], "high": c["high"],
                     "low": c["low"], "close": c["close"], "volume": c["volume"]}
                    for c in out],
    }


class LakeCompactBody(BaseModel):
    symbol: Optional[str] = None


@router.post("/api/lake/compact")
async def lake_compact(body: LakeCompactBody):
    """Kompaktierung: Dedup + Parquet-Partitionen neu schreiben."""
    from app.execution.StorageUtils import flush_candles_to_parquet

    symbols = [body.symbol] if body.symbol else list(state.config.market_symbols)
    parts = 0
    for sym in symbols:
        candles = state.store.ohlcv(sym, 60, limit=100000)
        if not candles:
            continue
        day = str(candles[-1]["ts"])[:10].replace("-", "")
        flush_candles_to_parquet(state.config.resolved_parquet_dir, sym, 60, candles, day)
        parts += 1
    summary = state.store.lake_summary()
    state.bus.log("info", f"Lake-Compaction: {parts} Partitionen neu geschrieben",
                  category="LAKE")
    return {"ok": True, "compactedPartitions": parts,
            "files": summary["total_files"], "sizeMb": summary["total_size_mb"]}


class LakeSyncBody(BaseModel):
    pass


@router.post("/api/lake/sync")
async def lake_sync(body: LakeSyncBody):
    """[MOCK-SEAM] Rclone/Google-Drive-Push — im Sandbox nur Statusbericht."""
    summary = state.store.lake_summary()
    return {
        "ok": True,
        "mode": "mock-rclone [MOCK-SEAM]",
        "files": summary["total_files"],
        "sizeMb": summary["total_size_mb"],
        "destination": "Backtest_Data/OHLCV",
        "note": "Cloud sync via Ubuntu core only (Windows TS portal removed in Sigma).",
    }


def _interval_minutes(interval: str) -> int:
    i = interval.strip().lower()
    if i.endswith("h"):
        return int(float(i[:-1]) * 60)
    if i.endswith("d"):
        return int(float(i[:-1]) * 1440)
    return max(1, int(float(i.replace("m", "") or 1)))


# =====================================================================
# M8 ADMIN (Phase 2: Redis SCAN + Quarantine / Phase 3: Autopsy events)
# =====================================================================
@router.get("/api/m8/states")
async def m8_states():
    states = await state.m8.scan_states()
    return {
        "specVersion": state.config.spec_version,
        "vaultSweep": state.config.vault_sweep_enabled,
        "autopsyOrder": state.config.autopsy_order,
        "count": len(states),
        "states": states,
    }


class HaltBody(BaseModel):
    symbol: str
    ttl: int = 300


@router.post("/api/m8/halt-symbol")
async def m8_halt_symbol(body: HaltBody):
    await state.m8.halt_symbol(body.symbol, ttl_seconds=min(int(body.ttl), 3600))
    return {"ok": True, "symbol": body.symbol, "ttl": min(int(body.ttl), 3600)}


class StateActionBody(BaseModel):
    reason: Optional[str] = None


@router.post("/api/m8/{strategy_id}/promote")
async def m8_promote(strategy_id: str, body: StateActionBody = StateActionBody()):
    try:
        st = await state.m8.promote(strategy_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "state": st}


@router.post("/api/m8/{strategy_id}/quarantine")
async def m8_quarantine(strategy_id: str, body: StateActionBody = StateActionBody()):
    try:
        st = await state.m8.quarantine(strategy_id, reason=body.reason or "manual")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "state": st}


@router.post("/api/m8/{strategy_id}/retire")
async def m8_retire(strategy_id: str, body: StateActionBody = StateActionBody()):
    try:
        st = await state.m8.retire(strategy_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "state": st}


@router.post("/api/m8/eod-run")
async def m8_eod_run():
    results = await state.eod.run_for_day()
    state.bus.log("info", f"EOD-Manuallauf: {len(results)} Strategien", category="EOD")
    return {"ok": True, "results": results}


@router.get("/api/m8/vault")
async def m8_vault():
    v = state.vault
    return {
        "balance_usd": round(v.balance(), 4),
        "entries_count": len(v.entries(limit=1000)),
        "last_sweep": v.last_sweep(),
        "recent": v.entries(25),
    }


@router.get("/api/m8/autopsies")
async def m8_autopsies(limit: int = 50):
    trades = state.store.trades(status="closed", limit=limit)
    zones: Dict[str, int] = {"GOOD": 0, "WATCH": 0, "CLEAN_LOSS": 0, "BAD": 0, "NEUTRAL_LOSS": 0}
    for t in trades:
        z = t.get("autopsy_zone")
        if z in zones:
            zones[z] += 1
    return {
        "limit": limit,
        "zoneDistribution": zones,
        "autopsyOrder": state.config.autopsy_order,
        "events": [
            {
                "trade_id": t.get("trade_id"),
                "strategy_id": t.get("strategy_id"),
                "strategy_name": t.get("strategy_name"),
                "symbol": t.get("symbol"),
                "direction": t.get("direction"),
                "exit_reason": t.get("exit_reason"),
                "net_pnl_usd": t.get("net_pnl_usd"),
                "pnl_r": t.get("pnl_r"),
                "mfe_r": t.get("mfe_r"),
                "mae_r": t.get("mae_r"),
                "capture_ratio": t.get("capture_ratio"),
                "stop_slippage_bps": t.get("stop_slippage_bps"),
                "autopsy_zone": t.get("autopsy_zone"),
                "exit_time": t.get("exit_time"),
            } for t in trades
        ],
    }


@router.get("/api/m8/autopsy-events/stream")
async def autopsy_events_stream():
    """SSE: idempotente TradeAutopsyEvents (Phase 3: Pub/Sub wake → React)."""
    async def gen():
        queue = state.bus.subscribe(EventBus.TOPIC_AUTOPSY)
        queue2 = state.bus.subscribe(EventBus.TOPIC_WAKE)
        try:
            import anyio
        except ImportError:
            anyio = None
        loop = asyncio.get_running_loop()
        while True:
            if await _disconnected():
                break
            item = await _next_event(queue, queue2, timeout=2.0)
            if item:
                yield f"event: {item.get('__topic__', 'autopsy')}\ndata: {item['data']}\n\n"
    return _sse(gen())


async def _disconnected() -> bool:
    return False


async def _next_event(q1, q2, timeout: float):
    async def _get(q, topic):
        try:
            item = await asyncio.wait_for(q.get(), timeout=timeout)
            return {"__topic__": topic, "data": item}
        except (asyncio.TimeoutError, TimeoutError):
            return None
    import json as _json

    r1, r2 = await asyncio.gather(_get(q1, "autopsy"), _get(q2, "wake"))
    item = r1 or r2
    if item:
        item["data"] = _json.dumps(item["data"], default=str)
    return item


def _sse(gen):
    from fastapi.responses import StreamingResponse

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
