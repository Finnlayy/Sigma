"""
=========================================================
Datei:      app/server/routes_sigma.py
Zweck:      §7 API-Vertrag (Delta zu Alpha) — Webhook, TV-Jobs,
            Virtual Bots, Academy/Allocator, Ops-Panels.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / API
=========================================================
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import blueprint as bp
from app.core.config import load_config
from app.core.memory_watchdog import get_memory_watchdog
from app.execution.KrakenCliBridge import KrakenCliBridge
from app.execution.LoopAPipeline import LoopAPipeline, SignalRequest
from app.execution.SafetyGuard import get_safety_guard
from app.execution.VirtualBotEngine import get_virtual_bot_engine
from app.execution.deadman_switch_daemon import get_deadman
from app.optimizer.StrategyAllocator import get_allocator
from app.optimizer.reward_shaping import get_reward_engine
from app.quant.onnx_kelly import get_quant_engine
from app.quant.regime_detector import detect_regime
from app.quant.self_optimizing_onnx import get_self_optimizing_engine
from app.scout.ScoutDaemon import get_scout
from app.services.telegram_bot_operator import get_telegram_operator
from app.tv.alert_provisioner import get_alert_provisioner
from app.tv.scraper_client import ScraperUnavailable, get_scraper_client
from app.tv.selector_manager import get_selector_manager
from app.tv.worker import (JOB_KIND_BACKTEST, JOB_KIND_PULL_PARAMS, JOB_KIND_PUSH_CODE,
                           get_tv_queue)

logger = logging.getLogger("app.server.routes_sigma")
router = APIRouter()


# =============================================================================
# Pydantic-Verträge (§4.1)
# =============================================================================

class PineAlertPayload(BaseModel):
    symbol: str
    action: str
    price: float
    rsi: float = 50.0
    atr: float = 0.0
    cisd_score: float = 0.5
    timestamp: int = 0
    strategy_id: Optional[str] = None
    interval: Any = 15
    secret: str = ""


class BotCreate(BaseModel):
    strategy_id: str
    symbol: str
    budget_eur: float = Field(gt=0)
    timeframe: str = "15"
    max_loss_eur: float = 0.0
    style: str = "STYLE_INTRADAY_MOMENT"


class TradeResultIn(BaseModel):
    strategy_id: str
    symbol: str
    timeframe: Any = 15
    regime: str = bp.Regime.RANGING_CHOP.value
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    duration_bars: int = 0
    fee_usd: float = 0.0
    notional_usd: float = 0.0


class BacktestJobIn(BaseModel):
    strategy_id: str = ""
    symbol: str = "BTC/USD"
    interval: Any = 15
    params: Dict[str, Any] = Field(default_factory=dict)
    window: Dict[str, Any] = Field(default_factory=dict)
    code: str = ""


class TelegramIn(BaseModel):
    chat_id: str
    text: str


# =============================================================================
# Singletons (lazy, damit Tests injizieren können)
# =============================================================================

_pipeline: Optional[LoopAPipeline] = None


def pipeline() -> LoopAPipeline:
    global _pipeline
    if _pipeline is None:
        cfg = load_config()
        _pipeline = LoopAPipeline(
            cfg,
            safety=get_safety_guard(cfg),
            quant=get_quant_engine(cfg),
            kraken=KrakenCliBridge(cfg),
            virtual_bots=get_virtual_bot_engine(alert_provisioner=get_alert_provisioner()),
            allocator=get_allocator(alert_provisioner=get_alert_provisioner()),
            deadman=get_deadman(),
            telegram=get_telegram_operator(safety_guard=get_safety_guard(cfg)),
            reward=get_reward_engine(),
            self_opt=get_self_optimizing_engine(get_quant_engine(cfg)),
        )
    return _pipeline


def set_pipeline(p: Optional[LoopAPipeline]) -> None:
    """Test-Seam."""
    global _pipeline
    _pipeline = p


# =============================================================================
# Loop A — Webhook
# =============================================================================

@router.post(bp.WEBHOOK_ROUTE)
async def signal_webhook(payload: PineAlertPayload, request: Request,
                         x_sigma_webhook_secret: Optional[str] = Header(default=None)):
    """TradingView Pine Alert -> Safety -> Sizing -> Kraken CLI / Paper."""
    sig = SignalRequest(
        symbol=payload.symbol, action=payload.action, price=payload.price,
        rsi=payload.rsi, atr=payload.atr, cisd_score=payload.cisd_score,
        timestamp=payload.timestamp or int(time.time()),
        strategy_id=payload.strategy_id, interval=payload.interval, secret=payload.secret,
    )
    provided = payload.secret or x_sigma_webhook_secret
    response = pipeline().handle_signal(sig, provided_secret=provided)
    if not response.accepted and response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.to_dict())
    return response.to_dict()


@router.get("/api/v1/signal/pipeline")
async def signal_pipeline_state():
    return pipeline().snapshot()


# =============================================================================
# Safety / Ops
# =============================================================================

@router.get("/api/v1/safety")
async def safety_state():
    return get_safety_guard().snapshot()


@router.post("/api/v1/safety/kill")
async def safety_kill(reason: str = "operator"):
    guard = get_safety_guard()
    guard.engage_kill_switch(reason)
    disabled = get_alert_provisioner().disable_all("kill_switch")
    return {"kill_switch": True, "alerts_disabled": len(disabled), "halt_action": bp.HALT_ACTION}


@router.post("/api/v1/safety/release")
async def safety_release():
    guard = get_safety_guard()
    guard.release_kill_switch()
    guard.release_pause()
    return guard.snapshot()


@router.post("/api/v1/safety/pause")
async def safety_pause(reason: str = "operator"):
    get_safety_guard().engage_pause(reason)
    return get_safety_guard().snapshot()


@router.get("/api/v1/deadman")
async def deadman_state():
    return get_deadman().snapshot()


@router.post("/api/v1/deadman/beat")
async def deadman_beat(has_native_stop_loss: bool = True):
    dm = get_deadman()
    dm.beat(has_native_stop_loss=has_native_stop_loss)
    return dm.snapshot()


@router.get("/api/v1/memory")
async def memory_state():
    return get_memory_watchdog().snapshot()


@router.post("/api/v1/memory/check")
async def memory_check(force: bool = False):
    return get_memory_watchdog().check(force=force)


# =============================================================================
# Virtual Bots (§20)
# =============================================================================

@router.get("/api/v1/bots")
async def list_bots():
    engine = get_virtual_bot_engine()
    return {"bots": engine.list_cards(), **engine.snapshot()}


@router.post("/api/v1/bots")
async def create_bot(body: BotCreate):
    engine = get_virtual_bot_engine()
    bot = engine.create_bot(body.strategy_id, body.symbol, body.budget_eur,
                            timeframe=body.timeframe, max_loss_eur=body.max_loss_eur,
                            style=body.style)
    get_alert_provisioner().upsert(body.strategy_id, body.symbol, body.timeframe)
    return bot.to_card()


@router.post("/api/v1/bots/{bot_id}/start")
async def start_bot(bot_id: str):
    try:
        return get_virtual_bot_engine().start(bot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/v1/bots/{bot_id}/pause")
async def pause_bot(bot_id: str):
    try:
        return get_virtual_bot_engine().pause(bot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/v1/bots/{bot_id}/m8/{state}")
async def set_bot_m8_state(bot_id: str, state: str):
    try:
        return get_virtual_bot_engine().apply_m8_state(bot_id, state.upper())
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


# =============================================================================
# Alerts (§4.6 / §8.3)
# =============================================================================

@router.get("/api/strategies/{strategy_id}/alerts")
async def get_alert(strategy_id: str):
    rec = get_alert_provisioner().get(strategy_id)
    if rec is None:
        raise HTTPException(404, f"no alert for {strategy_id}")
    return rec.to_dict()


@router.post("/api/strategies/{strategy_id}/alerts/sync")
async def sync_alert(strategy_id: str, symbol: str = "BTC/USD", interval: Any = 15,
                     enable: bool = False):
    return get_alert_provisioner().upsert(strategy_id, symbol, interval, enable=enable)


@router.post("/api/strategies/{strategy_id}/alerts/{action}")
async def switch_alert(strategy_id: str, action: str):
    prov = get_alert_provisioner()
    try:
        if action == "enable":
            return prov.enable(strategy_id, reason="api")
        if action == "disable":
            return prov.disable(strategy_id, reason="api")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    raise HTTPException(400, "action must be enable|disable")


@router.get("/api/v1/alerts")
async def alerts_overview():
    return get_alert_provisioner().snapshot()


# =============================================================================
# TV Jobs (§7)
# =============================================================================

@router.post("/api/tv/jobs/backtest")
async def submit_backtest_job(body: BacktestJobIn):
    job = get_tv_queue().submit(JOB_KIND_BACKTEST, strategy_id=body.strategy_id,
                                symbol=body.symbol, interval=body.interval,
                                params=body.params, window=body.window)
    return job.to_dict()


@router.post("/api/strategies/{strategy_id}/tv/pull-parameters")
async def pull_parameters(strategy_id: str, symbol: str = "BTC/USD", interval: Any = 15):
    job = get_tv_queue().submit(JOB_KIND_PULL_PARAMS, strategy_id=strategy_id,
                                symbol=symbol, interval=interval)
    return job.to_dict()


@router.post("/api/strategies/{strategy_id}/tv/push")
async def push_code(strategy_id: str, body: BacktestJobIn):
    job = get_tv_queue().submit(JOB_KIND_PUSH_CODE, strategy_id=strategy_id,
                                symbol=body.symbol, interval=body.interval, code=body.code)
    return job.to_dict()


@router.get("/api/tv/jobs")
async def list_jobs(strategyId: str = "", limit: int = 50):
    return {"jobs": get_tv_queue().list(strategyId, limit), **get_tv_queue().snapshot()}


@router.get("/api/tv/jobs/{job_id}")
async def get_job(job_id: str):
    job = get_tv_queue().get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job {job_id}")
    return job.to_dict()


@router.post("/api/tv/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return get_tv_queue().cancel(job_id)


@router.get("/api/tv/session/status")
async def tv_session_status():
    import os

    cfg = load_config()
    return {
        "storage_state_path": cfg.tv_storage_state_path,
        "session_present": os.path.exists(cfg.tv_storage_state_path),
        "driver": "playwright" if os.path.exists(cfg.tv_storage_state_path) else "fake",
        "selectors": get_selector_manager().snapshot(),
        "worker": get_tv_queue().snapshot(),
    }


# =============================================================================
# Loop C — Scraper / Regime
# =============================================================================

@router.get("/api/v1/market/ohlc")
async def market_ohlc(symbol: str = "BTC/USD", interval: int = 15, count: int = 300):
    client = get_scraper_client()
    try:
        candles, meta = client.fetch_ohlc_with_meta(symbol, interval, count)
    except ScraperUnavailable as exc:
        raise HTTPException(503, f"scraper unavailable: {exc}") from exc
    return {"symbol": symbol, "interval": interval, "candles": candles,
            "count": len(candles), "feed": meta}


@router.get("/api/v1/market/indicators")
async def market_indicators(symbol: str = "BTC/USD", interval: int = 1440):
    client = get_scraper_client()
    try:
        data = client.fetch_indicators(symbol, interval)
    except ScraperUnavailable as exc:
        raise HTTPException(503, f"scraper unavailable: {exc}") from exc
    return {"symbol": symbol, "interval": interval, "indicators": data, "feed": client.last_meta}


@router.get("/api/v1/market/overview")
async def market_overview(symbol: str = "BTC/USD"):
    client = get_scraper_client()
    try:
        data = client.fetch_overview(symbol)
    except ScraperUnavailable as exc:
        raise HTTPException(503, f"scraper unavailable: {exc}") from exc
    return {"symbol": symbol, "overview": data, "feed": client.last_meta}


@router.get("/api/v1/market/movers")
async def market_movers(market: str = "crypto", category: str = "gainers", limit: int = 25):
    client = get_scraper_client()
    try:
        rows = client.movers(market, category, limit)
    except ScraperUnavailable as exc:
        raise HTTPException(503, f"scraper unavailable: {exc}") from exc
    return {"market": market, "category": category, "rows": rows,
            "count": len(rows), "feed": client.last_meta}


@router.get("/api/v1/market/screener")
async def market_screener(market: str = "crypto", sort_by: str = "volume",
                          sort_order: str = "desc", limit: int = 25):
    client = get_scraper_client()
    try:
        rows = client.screener(market=market, sort_by=sort_by, sort_order=sort_order, limit=limit)
    except ScraperUnavailable as exc:
        raise HTTPException(503, f"scraper unavailable: {exc}") from exc
    return {"market": market, "rows": rows, "count": len(rows), "feed": client.last_meta}


@router.get("/api/v1/scraper/health")
async def scraper_health():
    """Loop-C-Sidecar-Status inkl. Vendor-Import, Cache und Rate-Limit (§6)."""
    client = get_scraper_client()
    snapshot = client.health()
    return {
        "base_url": client.base_url,
        "endpoints": dict(bp.SCRAPER_ENDPOINTS),
        "market_sources": list(bp.MARKET_SOURCES),
        "market_source_prod": bp.MARKET_SOURCE_PROD,
        **snapshot,
    }


@router.get("/api/v1/regime")
async def regime(symbol: str = "BTC/USD", interval: int = 15, count: int = 300):
    client = get_scraper_client()
    try:
        candles, meta = client.fetch_ohlc_with_meta(symbol, interval, count)
    except ScraperUnavailable as exc:
        raise HTTPException(503, f"scraper unavailable: {exc}") from exc
    return {"symbol": symbol, **detect_regime(candles), "feed": meta}


# =============================================================================
# Loop D / E — Scout, Academy, Reward, ML
# =============================================================================

@router.get("/api/v1/scout")
async def scout_state():
    return get_scout(allocator=get_allocator()).snapshot()


@router.post("/api/v1/scout/plan")
async def scout_plan(strategy_ids: List[str], regime: str = bp.Regime.RANGING_CHOP.value):
    scout = get_scout(allocator=get_allocator())
    tasks = scout.plan(strategy_ids, regime)
    return {"created": [t.key for t in tasks], **scout.snapshot()}


@router.get("/api/v1/academy/badges")
async def academy_badges(strategyId: str = ""):
    alloc = get_allocator()
    return {"matrix": alloc.badge_matrix(strategyId), **alloc.snapshot()}


@router.post("/api/v1/academy/ingest")
async def academy_ingest(body: TradeResultIn):
    alloc = get_allocator(alert_provisioner=get_alert_provisioner())
    profile = alloc.ingest_trade_result(body.strategy_id, body.symbol, body.timeframe,
                                        body.regime, body.pnl_pct)
    reward = get_reward_engine().score_trade(
        body.strategy_id, pnl_pct=body.pnl_pct, mfe_pct=body.mfe_pct, mae_pct=body.mae_pct,
        duration_bars=body.duration_bars, fee_usd=body.fee_usd, notional_usd=body.notional_usd)
    return {"profile": profile, "reward": reward.to_dict()}


@router.get("/api/v1/academy/training-dataset")
async def academy_training_dataset():
    rows = get_allocator().export_training_dataset()
    return {"rows": rows, "count": len(rows), "min_sample": bp.BADGE_MIN_SAMPLE}


@router.get("/api/v1/reward/matrix")
async def reward_matrix():
    return {"matrix": get_reward_engine().matrix(), "weights": dict(bp.REWARD_WEIGHTS)}


@router.get("/api/v1/ml/self-optimizing")
async def self_optimizing_state():
    return get_self_optimizing_engine(get_quant_engine()).snapshot()


@router.post("/api/v1/ml/record")
async def self_optimizing_record(predicted: float, outcome: float, strategy_id: str = ""):
    return get_self_optimizing_engine(get_quant_engine()).record(predicted, outcome, strategy_id)


# =============================================================================
# Telegram Operator
# =============================================================================

@router.get("/api/v1/telegram")
async def telegram_state():
    return get_telegram_operator(safety_guard=get_safety_guard()).snapshot()


@router.post("/api/v1/telegram/message")
async def telegram_message(body: TelegramIn):
    op = get_telegram_operator(safety_guard=get_safety_guard(),
                               virtual_bots=get_virtual_bot_engine())
    return op.handle(body.chat_id, body.text)
