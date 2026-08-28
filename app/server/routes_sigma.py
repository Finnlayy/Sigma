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
import os
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import blueprint as bp
from app.core.config import load_config
from app.core.memory_watchdog import get_memory_watchdog
from app.core.telemetry import get_telemetry_center
from app.server.schemas import SignalExecutionResponse
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
_KRAKEN_BRIDGE: Optional[KrakenCliBridge] = None
_DEPTH_ADAPTER: Optional[Any] = None
_OPERATOR_AUTH_OVERRIDE: Optional[Callable[[Request], bool]] = None


def get_kraken_bridge() -> KrakenCliBridge:
    global _KRAKEN_BRIDGE
    if _KRAKEN_BRIDGE is None:
        _KRAKEN_BRIDGE = KrakenCliBridge(
            load_config(), telemetry=get_telemetry_center()
        )
    return _KRAKEN_BRIDGE


def set_kraken_bridge(bridge: Optional[KrakenCliBridge]) -> None:
    global _KRAKEN_BRIDGE
    _KRAKEN_BRIDGE = bridge


def get_depth_adapter():
    global _DEPTH_ADAPTER
    if _DEPTH_ADAPTER is None:
        from app.ingestion.kraken_depth_adapter import get_kraken_depth_adapter

        _DEPTH_ADAPTER = get_kraken_depth_adapter()
    return _DEPTH_ADAPTER


def set_depth_adapter(adapter: Optional[Any]) -> None:
    global _DEPTH_ADAPTER
    _DEPTH_ADAPTER = adapter


def set_operator_auth_override(
    override: Optional[Callable[[Request], bool]]
) -> None:
    """Test seam; production must always leave this unset."""
    global _OPERATOR_AUTH_OVERRIDE
    _OPERATOR_AUTH_OVERRIDE = override


def pipeline() -> LoopAPipeline:
    global _pipeline
    if _pipeline is None:
        cfg = load_config()
        from app.quant.epidemic_contagion_engine import get_contagion_engine

        _pipeline = LoopAPipeline(
            cfg,
            safety=get_safety_guard(cfg),
            quant=get_quant_engine(cfg),
            kraken=get_kraken_bridge(),
            virtual_bots=get_virtual_bot_engine(alert_provisioner=get_alert_provisioner()),
            allocator=get_allocator(alert_provisioner=get_alert_provisioner()),
            deadman=get_deadman(),
            telegram=get_telegram_operator(safety_guard=get_safety_guard(cfg)),
            reward=get_reward_engine(),
            self_opt=get_self_optimizing_engine(get_quant_engine(cfg)),
            contagion=get_contagion_engine(),
            dispatcher=get_order_dispatcher(),
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
    if pipeline().config.live_trading:
        raise HTTPException(status_code=503, detail={
            "code": "LEGACY_WEBHOOK_LIVE_DISABLED",
            "reason": "Use the schema-A ingest route with verified execution accounting",
        })
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


# --- §33 Ingestion-Router: strikte Schema-Familien A/B/C ---------------------

@router.post("/api/v1/signal/ingest", response_model=SignalExecutionResponse)
async def signal_ingest(request: Request,
                        x_sigma_webhook_secret: Optional[str] = Header(default=None)):
    """§33.5 — Schema erkennen, strikt validieren, dann Loop A ausfuehren.

    Reihenfolge (kanonisch): secret -> stale gate (Kraken-Zeit) -> Idempotenz
    -> Glint x Orderbook JIT -> reliable_order_dispatcher.
    """
    from app.core.exchange_clock import get_exchange_clock
    from app.server.schemas import (ERR_PIONEX_DISABLED, ERR_SCHEMA_INVALID,
                                    SchemaDetectionError, detect_schema,
                                    PionexSignalPayload, SigmaL4AlertPayload)

    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={
            "code": ERR_SCHEMA_INVALID, "reason": "Body ist kein gueltiges JSON"})

    try:
        family = detect_schema(raw)
    except SchemaDetectionError as exc:
        raise HTTPException(status_code=400, detail={
            "code": exc.code, "reason": str(exc),
            "supported": list(bp.WEBHOOK_SCHEMAS)})

    if family == "PIONEX_NATIVE":
        from app.core import l4_config

        enabled = bool(l4_config.get("execution.pionex.enabled",
                                     bp.PIONEX_ENABLED_DEFAULT))
        try:
            PionexSignalPayload.model_validate(raw)
        except Exception as exc:
            raise HTTPException(status_code=422, detail={
                "code": ERR_SCHEMA_INVALID, "reason": str(exc)})
        if not enabled:
            raise HTTPException(status_code=403, detail={
                "code": ERR_PIONEX_DISABLED,
                "reason": "pionex_connector.enabled=false (DE_BAFIN default)"})
        return SignalExecutionResponse(
            status="REJECTED", schema_family=family, code=ERR_PIONEX_DISABLED,
            reason="Pionex-Lab-Routing ist nicht Teil des Kraken-Execution-Pfads",
            stage="pionex_lab")

    if family == "ML_TELEMETRY":
        raise HTTPException(status_code=400, detail={
            "code": ERR_SCHEMA_INVALID,
            "reason": "ML-Features sind kein eigenstaendiges Signal — "
                      "in Schema A unter 'features' einbetten"})

    try:
        alert = SigmaL4AlertPayload.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={
            "code": ERR_SCHEMA_INVALID, "reason": str(exc)})

    provided = alert.secret or x_sigma_webhook_secret
    auth = pipeline().safety.verify_webhook_secret(provided)
    if not auth.allowed:
        raise HTTPException(
            status_code=auth.status_code,
            detail={"code": auth.code, "reason": auth.reason},
        )
    clock = get_exchange_clock()
    if clock.synced and clock.is_signal_stale(alert.timestamp):
        raise HTTPException(status_code=400, detail={
            "code": bp.STALE_SIGNAL_REJECT_CODE,
            "reason": f"Signal {clock.signal_age_s(alert.timestamp):.0f}s alt "
                      f"(max {bp.STALE_SIGNAL_MAX_LATENCY_S}s gegen Kraken-Zeit)"})

    dispatcher = get_order_dispatcher()
    if dispatcher.seen(alert.idempotency_key):
        return SignalExecutionResponse(
            status="DUPLICATE_IGNORED", schema_family=family,
            strategy_id=alert.strategy_id, bot_id=alert.bot_id, symbol=alert.symbol,
            action=alert.action, idempotency_key=alert.idempotency_key,
            execution_mode=alert.execution_mode, fixed_leverage=alert.fixed_leverage,
            code=bp.OrderAck.DUPLICATE_IGNORED.value,
            reason="idempotency_key bereits verarbeitet", stage="idempotency")

    from app.tv.symbol_map import is_allowed

    is_futures = alert.market_type == "futures"
    if is_futures and alert.execution_mode == bp.ExecutionMode.LIVE.value:
        raise HTTPException(status_code=503, detail={
            "code": "FUTURES_LIVE_BRACKET_UNAVAILABLE",
            "reason": "Live futures stay disabled until atomic entry + reduce-only stop is supported",
        })
    if not is_futures and alert.execution_mode == bp.ExecutionMode.LIVE.value:
        raise HTTPException(status_code=503, detail={
            "code": "SPOT_LIVE_PNL_RECONCILIATION_UNAVAILABLE",
            "reason": (
                "Kraken trades-history returns fill price/volume/fee, not "
                "cost-basis realized PnL; live spot stays disabled"
            ),
        })
    if not is_allowed(alert.symbol, futures=is_futures):
        raise HTTPException(status_code=403, detail={
            "code": "SYMBOL_NOT_ALLOWED",
            "reason": f"{alert.symbol} not in Kraken allowlist",
        })

    confluence_multiplier = 1.0
    if alert.action != "CLOSE":
        from app.execution.reliable_order_dispatcher import OrderRequest
        from app.tv.symbol_map import to_kraken_pair

        try:
            snapshot = get_depth_adapter().fetch(alert.symbol)
            from app.core.scheduler_matrix import get_scheduler

            scheduler = get_scheduler()
            if scheduler.get("glint_orderbook_verify") is not None:
                scheduler.fire_event("glint_orderbook_verify")
            from app.quant.glint_orderbook_verifier import get_verifier

            confluence = get_verifier().verify(
                snapshot, alert.action, now=clock.now()
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail={
                "code": "ORDERBOOK_DEPTH_UNAVAILABLE",
                "reason": str(exc),
                "stage": "glint_orderbook_jit",
            }) from exc
        if not confluence.approved:
            request_model = OrderRequest(
                idempotency_key=alert.idempotency_key,
                strategy_id=alert.strategy_id,
                bot_id=alert.bot_id,
                pair=(f"PF_{to_kraken_pair(alert.symbol)}"
                      if is_futures else to_kraken_pair(alert.symbol)),
                side=alert.side,
                volume=0.0,
                stop_loss=alert.stop_loss,
                take_profit=alert.take_profit,
                fixed_leverage=alert.fixed_leverage,
                execution_mode=alert.execution_mode,
                market_type=alert.market_type,
            )
            receipt = dispatcher.veto(
                request_model,
                confluence.reason,
                confluence.reject_code or bp.ORDERBOOK_WALL_REJECT,
            )
            return SignalExecutionResponse(
                status="VETO_ORDERBOOK", schema_family=family,
                strategy_id=alert.strategy_id, bot_id=alert.bot_id,
                symbol=alert.symbol, action=alert.action,
                idempotency_key=alert.idempotency_key,
                execution_mode=alert.execution_mode,
                fixed_leverage=alert.fixed_leverage,
                code=receipt.error_code, reason=receipt.detail,
                stage="glint_orderbook_jit", price=alert.price,
                stop_loss=alert.stop_loss, take_profit=alert.take_profit,
            )
        confluence_multiplier = confluence.size_multiplier

    features = alert.features
    sig = SignalRequest(
        symbol=alert.symbol, action=alert.action, price=alert.price,
        rsi=features.rsi if features else 50.0,
        atr=features.atr if features else 0.0,
        cisd_score=(features.cisd_score if features and features.cisd_score is not None
                    else 0.5),
        timestamp=alert.timestamp, strategy_id=alert.strategy_id,
        interval=alert.interval or 15,
        secret=alert.secret,
    )
    result = pipeline().handle_signal(
        sig,
        provided_secret=provided,
        quantity_multiplier=confluence_multiplier,
        idempotency_key=alert.idempotency_key,
        bot_id=alert.bot_id,
        execution_mode=alert.execution_mode,
        fixed_leverage=alert.fixed_leverage,
        execution_market=alert.market_type,
    )
    payload = result.to_dict()

    if not result.accepted:
        response = SignalExecutionResponse(
            status="REJECTED", schema_family=family, strategy_id=alert.strategy_id,
            bot_id=alert.bot_id, symbol=alert.symbol, action=alert.action,
            idempotency_key=alert.idempotency_key, execution_mode=alert.execution_mode,
            fixed_leverage=alert.fixed_leverage, code=result.code,
            reason=result.reason, stage=result.stage, price=alert.price,
            stop_loss=alert.stop_loss, take_profit=alert.take_profit,
        )
        if result.status_code >= 400:
            raise HTTPException(status_code=result.status_code,
                                detail=response.model_dump())
        return response

    order_id = str(payload.get("txid") or payload.get("order_id") or "")
    if not dispatcher.seen(alert.idempotency_key):
        dispatcher.remember(
            alert.idempotency_key, order_id=order_id, strategy_id=alert.strategy_id,
            bot_id=alert.bot_id, pair=str(payload.get("pair") or alert.symbol),
            side=alert.side, volume=float(payload.get("quantity") or 0.0),
            execution_mode=alert.execution_mode, fixed_leverage=alert.fixed_leverage,
            detail=f"Loop A stage={result.stage}",
        )
    return SignalExecutionResponse(
        status="EXECUTED", schema_family=family, strategy_id=alert.strategy_id,
        bot_id=alert.bot_id, symbol=alert.symbol, action=alert.action,
        order_id=order_id, idempotency_key=alert.idempotency_key,
        execution_mode=str(payload.get("mode") or alert.execution_mode),
        fixed_leverage=alert.fixed_leverage, code=result.code, reason=result.reason,
        stage=result.stage, quantity=float(payload.get("quantity") or 0.0),
        price=alert.price, stop_loss=alert.stop_loss, take_profit=alert.take_profit,
    )


@router.get("/api/v1/signal/schemas")
async def signal_schema_catalog():
    """§33 — maschinenlesbarer Katalog der drei Schema-Familien."""
    from app.server.schemas import (MLFeaturePayload, PionexSignalPayload,
                                    SigmaL4AlertPayload)

    return {
        "families": list(bp.WEBHOOK_SCHEMAS),
        "required_fields": list(bp.SIGMA_L4_REQUIRED_FIELDS),
        "actions": list(bp.SIGMA_L4_ACTIONS),
        "order_types": list(bp.SIGMA_L4_ORDER_TYPES),
        "ingestion_steps": list(bp.INGESTION_PIPELINE_STEPS),
        "pine_emitter_template": bp.PINE_EMITTER_TEMPLATE_PATH,
        "json_schema": {
            "SIGMA_L4_MASTER": SigmaL4AlertPayload.model_json_schema(),
            "PIONEX_NATIVE": PionexSignalPayload.model_json_schema(),
            "ML_TELEMETRY": MLFeaturePayload.model_json_schema(),
        },
    }


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
async def deadman_beat(request: Request, has_native_stop_loss: bool = True,
                       x_sigma_settings_token: Optional[str] = Header(default=None)):
    _require_operator(request, x_sigma_settings_token)
    dm = get_deadman()
    dm.beat(has_native_stop_loss=has_native_stop_loss)
    return dm.snapshot()


@router.get("/api/v1/memory")
async def memory_state():
    return get_memory_watchdog().snapshot()


@router.post("/api/v1/memory/check")
async def memory_check(request: Request, force: bool = False,
                       x_sigma_settings_token: Optional[str] = Header(default=None)):
    _require_operator(request, x_sigma_settings_token)
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


# =============================================================================
# §38 Netron ONNX Visualization & Inspection Stack
# =============================================================================

@router.get("/api/v1/models/netron/status")
async def netron_status():
    """§38.3 — aktives Modell + Port-Health."""
    from app.services.netron_server import get_netron_service

    return get_netron_service().status()


@router.post("/api/v1/models/netron/start")
async def netron_start(model: str = ""):
    from app.services.netron_server import get_netron_service

    return get_netron_service().start_server(model or bp.NETRON_DEFAULT_MODEL)


@router.post("/api/v1/models/inspect/{version_tag}")
async def netron_inspect(version_tag: str):
    """§38.5 — 'In Netron betrachten' aus der Model-Registry."""
    from app.services.netron_server import get_netron_service

    service = get_netron_service()
    if not service.load_model(version_tag):
        raise HTTPException(status_code=404, detail={
            "code": "NETRON_MODEL_NOT_LOADED",
            "reason": service.last_error or version_tag})
    return {"loaded": True, "version_tag": service.version_tag,
            "status": service.status()}


# =============================================================================
# §34 LLM-, Tool-Calling- & Streaming-Schemata
# =============================================================================

@router.get("/api/v1/llm/tools")
async def llm_tools():
    """§34.1 — Ollama/OpenAI-kompatible Function-Definitionen."""
    from app.llm.schemas_llm import tool_registry

    return {"version": bp.DOCS_BLUEPRINT_VERSION,
            "requires_confirmation": list(bp.LLM_TOOLS_REQUIRING_CONFIRMATION),
            "stream_route": bp.LLM_STREAM_ROUTE,
            "ui_triggers": list(bp.LLM_UI_TRIGGERS),
            "tools": tool_registry()}


@router.post("/api/v1/llm/tool-call")
async def llm_tool_call(body: Dict[str, Any]):
    """Typisierter Tool-Call — Freitext wird nie ausgefuehrt (§34)."""
    from app.llm.schemas_llm import ToolCallEnvelope
    from app.llm.tool_executor import get_tool_executor

    try:
        envelope = ToolCallEnvelope(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={
            "code": "LLM_TOOL_ENVELOPE_INVALID", "reason": str(exc)})
    result = get_tool_executor().execute(envelope)
    return result.model_dump()


@router.post("/api/v1/llm/pine-patch")
async def llm_pine_patch(body: Dict[str, Any]):
    """§34.2 — Pine-Patch mit Backup, Compile-Gate und Rollback."""
    from app.llm.schemas_llm import PineCodePatchRequest, ToolCallEnvelope
    from app.llm.tool_executor import get_tool_executor

    try:
        request = PineCodePatchRequest(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={
            "code": "ERR_TV_PINE_COMPILE_ERROR", "reason": str(exc)})
    result = get_tool_executor().execute(ToolCallEnvelope(
        tool_name="edit_pine_strategy_code", arguments=request.model_dump()))
    return result.model_dump()


@router.websocket(bp.LLM_STREAM_ROUTE)
async def llm_stream(websocket: WebSocket):
    """§34.3 — ChatStreamMessage-Stream fuer die LLMConsole."""
    from app.llm.schemas_llm import ChatStreamMessage, ToolCallEnvelope
    from app.llm.tool_executor import get_tool_executor

    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            session = str(payload.get("session_id") or "default")
            call = payload.get("tool_call")
            if call:
                envelope = ToolCallEnvelope(**call)
                await websocket.send_json(ChatStreamMessage(
                    session_id=session, sender="TOOL_EXECUTOR",
                    active_tool_call=envelope).model_dump())
                result = get_tool_executor().execute(envelope)
                trigger = result.result_data.get("ui_component_trigger")
                await websocket.send_json(ChatStreamMessage(
                    session_id=session, sender="TOOL_EXECUTOR",
                    tool_result=result, is_complete=True,
                    ui_component_trigger=trigger
                    if trigger in bp.LLM_UI_TRIGGERS else None).model_dump())
                continue
            prompt = str(payload.get("prompt") or "")
            for chunk in (prompt.split(" ") or [""]):
                await websocket.send_json(ChatStreamMessage(
                    session_id=session, sender="ASSISTANT",
                    content_chunk=chunk + " ").model_dump())
            await websocket.send_json(ChatStreamMessage(
                session_id=session, sender="ASSISTANT",
                is_complete=True).model_dump())
    except WebSocketDisconnect:
        return


# =============================================================================
# §36 Unified Error Taxonomy & Diagnostics Desk
# =============================================================================

@router.get("/api/v1/diagnostics/errors")
async def diagnostics_errors(limit: int = 50, severity: str = "", category: str = ""):
    """§36.4 — DiagnosticsErrorPanel: Code, Subsystem, Hint, Severity, Zeit."""
    from app.core.error_engine import get_error_engine

    engine = get_error_engine()
    state = engine.panel_state(limit)
    if severity or category:
        state["errors"] = engine.recent(limit, severity=severity, category=category)
    return state


@router.get("/api/v1/diagnostics/catalog")
async def diagnostics_catalog():
    from app.core.error_engine import get_error_engine

    return {"catalog": get_error_engine().catalog(),
            "ranges": dict(bp.ERROR_CATEGORIES)}


@router.get("/api/v1/diagnostics/export")
async def diagnostics_export():
    """Error-Logs als .jsonl exportieren (§36.4)."""
    from fastapi.responses import PlainTextResponse

    from app.core.error_engine import get_error_engine

    return PlainTextResponse(
        get_error_engine().export_jsonl(), media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=sigma_errors.jsonl"})


@router.post("/api/v1/diagnostics/self-test")
async def diagnostics_self_test():
    from app.core.error_engine import get_error_engine

    return get_error_engine().self_test()


@router.post("/api/v1/diagnostics/clear")
async def diagnostics_clear():
    from app.core.error_engine import get_error_engine

    get_error_engine().clear()
    return {"cleared": True}


# =============================================================================
# §32 Kraken Paper Trading Lab & Graduation
# =============================================================================

class PaperOrderIn(BaseModel):
    strategy_id: str
    symbol: str
    side: str
    volume: float
    ordertype: str = "market"
    price: Optional[float] = None
    stop_price: Optional[float] = None


class PaperFillIn(BaseModel):
    strategy_id: str
    symbol: str
    side: str = "buy"
    quantity: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_eur: float = 0.0
    fee_eur: float = 0.0
    slippage_bps: float = 0.0
    latency_ms: float = 0.0
    order_id: str = ""


class PaperPromoteIn(BaseModel):
    reason: str = "operator"
    force: bool = False


@router.get("/api/v1/paper-lab")
async def paper_lab_state(limit: int = 50):
    """§32 — PaperLabPanel: Trades, Sim-PnL, Winrate, Graduation-Status."""
    from app.execution.kraken_paper_engine import get_paper_engine

    return get_paper_engine().panel_state(limit)


@router.post("/api/v1/paper-lab/order")
async def paper_lab_order(body: PaperOrderIn):
    from app.execution.kraken_paper_engine import (PaperOnlyViolation,
                                                   get_paper_engine)

    try:
        return get_paper_engine().submit_order(
            body.strategy_id, body.symbol, body.side, body.volume,
            ordertype=body.ordertype, price=body.price, stop_price=body.stop_price)
    except PaperOnlyViolation as exc:
        raise HTTPException(status_code=409, detail={
            "code": "PAPER_ONLY_VIOLATION", "reason": str(exc)})


@router.post("/api/v1/paper-lab/fill")
async def paper_lab_fill(body: PaperFillIn):
    from app.execution.kraken_paper_engine import PaperTrade, get_paper_engine

    return get_paper_engine().record_fill(PaperTrade(**body.model_dump()))


@router.get("/api/v1/paper-lab/{strategy_id}")
async def paper_lab_strategy(strategy_id: str):
    from app.execution.kraken_paper_engine import get_paper_engine

    engine = get_paper_engine()
    return {"stats": engine.stats(strategy_id),
            "graduation": engine.graduation_status(strategy_id),
            "execution_mode": engine.execution_mode_for(strategy_id),
            "trades": engine.trades(strategy_id)}


@router.post("/api/v1/paper-lab/{strategy_id}/promote")
async def paper_lab_promote(strategy_id: str, body: PaperPromoteIn | None = None):
    """§32.1 — Stufe 2 -> Stufe 3, nur mit erfuellten Gates (oder force)."""
    from app.execution.kraken_paper_engine import get_paper_engine

    payload = body or PaperPromoteIn()
    outcome = get_paper_engine().graduate(strategy_id, reason=payload.reason,
                                          force=payload.force)
    if not outcome["promoted"]:
        raise HTTPException(status_code=409, detail=outcome)
    return outcome


@router.post("/api/v1/paper-lab/{strategy_id}/demote")
async def paper_lab_demote(strategy_id: str, body: PaperPromoteIn | None = None):
    from app.execution.kraken_paper_engine import get_paper_engine

    return get_paper_engine().demote(strategy_id,
                                     (body.reason if body else "risk"))


# =============================================================================
# §35 Exact TradingView CSV Roundtrip
# =============================================================================

class CsvOptimizeIn(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)


def _strategy_dir(strategy_id: str) -> str:
    return os.path.join(load_config().strategies_dir, strategy_id)


@router.get("/api/strategies/{strategy_id}/csv/meta")
async def csv_meta(strategy_id: str):
    """§35.2 — eingefrorener Dateiname, Header und Delimiter."""
    from app.optimizer.exact_csv_serializer import CsvHeaderMismatch, load_handler

    try:
        handler = load_handler(_strategy_dir(strategy_id))
    except (CsvHeaderMismatch, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail={
            "code": bp.CSV_HEADER_MISMATCH_CODE, "reason": str(exc)})
    return {**handler.meta().as_dict(), "parameters": handler.parameters(),
            "baseline_dir": bp.CSV_BASELINE_DIR, "optimized_dir": bp.CSV_OPTIMIZED_DIR}


@router.get("/api/strategies/{strategy_id}/csv/diff")
async def csv_diff(strategy_id: str):
    """§35.6 — Baseline vs Optimized (gleicher Dateiname, andere Ordner)."""
    from app.optimizer.exact_csv_serializer import (CsvHeaderMismatch,
                                                    ExactTradingViewCSVHandler,
                                                    load_handler, optimized_path)

    strategy_dir = _strategy_dir(strategy_id)
    try:
        baseline = load_handler(strategy_dir)
    except (CsvHeaderMismatch, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail={
            "code": bp.CSV_HEADER_MISMATCH_CODE, "reason": str(exc)})
    target = optimized_path(strategy_dir, baseline.original_filename)
    base_params = baseline.parameters()
    if not os.path.exists(target):
        return {"filename": baseline.original_filename, "has_optimized": False,
                "baseline": base_params, "optimized": {}, "changed": {}}
    optimized = ExactTradingViewCSVHandler(target).parameters()
    changed = {k: {"baseline": base_params.get(k), "optimized": v}
               for k, v in optimized.items() if base_params.get(k) != v}
    return {"filename": baseline.original_filename, "has_optimized": True,
            "baseline": base_params, "optimized": optimized, "changed": changed}


@router.post("/api/strategies/{strategy_id}/csv/optimized")
async def csv_write_optimized(strategy_id: str, body: CsvOptimizeIn):
    """GA-Ergebnis schreiben — Header-Assertion inklusive (§35.4)."""
    from app.optimizer.exact_csv_serializer import CsvHeaderMismatch, emit_optimized

    try:
        return emit_optimized(_strategy_dir(strategy_id), body.params)
    except (CsvHeaderMismatch, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail={
            "code": bp.CSV_HEADER_MISMATCH_CODE, "reason": str(exc)})


# =============================================================================
# §31 Strategy Lifecycle — die 3 Trigger-Pfade
# =============================================================================

class LifecycleDepth(BaseModel):
    price: float
    volume: float


class StrategyStartIn(BaseModel):
    symbol: str
    budget_eur: float = 250.0
    trigger_path: str = bp.TriggerPath.MANUAL.value
    execution_mode: Optional[str] = None
    fixed_leverage: Optional[int] = None
    timeframe: str = "15"
    style: Optional[str] = None
    glint_score: Optional[float] = None
    initiator: str = "operator"
    bids: List[LifecycleDepth] = Field(default_factory=list)
    asks: List[LifecycleDepth] = Field(default_factory=list)


class LifecycleReasonIn(BaseModel):
    reason: str = "operator"


def get_lifecycle():
    """Teilt Flywheel-Singleton und Engines mit dem restlichen Core."""
    from app.services.strategy_lifecycle_service import (get_lifecycle_service,
                                                         set_lifecycle_service,
                                                         StrategyLifecycleService)

    service = get_lifecycle_service()
    if service._flywheel is None:                     # gemeinsamer Kapitaltopf
        service._flywheel = get_flywheel()
    if service._depth_adapter is None:
        service._depth_adapter = get_depth_adapter()
    if service.config is None:
        service.config = load_config()
    assert isinstance(service, StrategyLifecycleService)
    set_lifecycle_service(service)
    return service


@router.post("/api/strategies/{strategy_id}/start")
async def strategy_start(strategy_id: str, body: StrategyStartIn):
    """§31 — Pfad 1/2/3 muenden in dieselbe Dispatcher-Pipeline."""
    from app.quant.glint_orderbook_verifier import OrderbookSnapshot
    from app.services.strategy_lifecycle_service import LifecycleError

    orderbook = None
    if body.bids and body.asks:
        from app.core.exchange_clock import get_exchange_clock

        orderbook = OrderbookSnapshot(
            symbol=body.symbol,
            bids=[(lvl.price, lvl.volume) for lvl in body.bids],
            asks=[(lvl.price, lvl.volume) for lvl in body.asks],
            timestamp=get_exchange_clock().now(),
        )
    try:
        record = get_lifecycle().start(
            strategy_id, body.symbol, trigger_path=body.trigger_path,
            budget_eur=body.budget_eur, execution_mode=body.execution_mode,
            fixed_leverage=body.fixed_leverage, timeframe=body.timeframe,
            style=body.style, glint_score=body.glint_score, orderbook=orderbook,
            initiator=body.initiator)
    except LifecycleError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "reason": exc.reason})
    payload = record.as_dict()
    if not record.ok:
        raise HTTPException(status_code=409, detail=payload)
    return payload


@router.post("/api/strategies/{strategy_id}/pause")
async def strategy_pause(strategy_id: str, body: LifecycleReasonIn | None = None):
    return _lifecycle_transition("pause", strategy_id, body)


@router.post("/api/strategies/{strategy_id}/resume")
async def strategy_resume(strategy_id: str, body: LifecycleReasonIn | None = None):
    return _lifecycle_transition("resume", strategy_id, body)


@router.post("/api/strategies/{strategy_id}/quarantine")
async def strategy_quarantine(strategy_id: str, body: LifecycleReasonIn | None = None):
    return _lifecycle_transition("quarantine", strategy_id, body)


def _lifecycle_transition(action: str, strategy_id: str,
                          body: Optional[LifecycleReasonIn]):
    from app.services.strategy_lifecycle_service import LifecycleError

    reason = (body.reason if body else None) or ("risk" if action == "quarantine"
                                                 else "operator")
    try:
        return getattr(get_lifecycle(), action)(strategy_id, reason)
    except LifecycleError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"code": exc.code, "reason": exc.reason})


@router.get("/api/v1/lifecycle")
async def lifecycle_snapshot(limit: int = 25):
    return get_lifecycle().snapshot(limit)


@router.get("/api/strategies/{strategy_id}/lifecycle")
async def lifecycle_for_strategy(strategy_id: str):
    state = get_lifecycle().status(strategy_id)
    if state is None:
        raise HTTPException(status_code=404, detail={
            "code": "UNKNOWN_STRATEGY", "reason": f"{strategy_id} wurde nie platziert"})
    return state


# =============================================================================
# Execution Plane §23-§29 — Telemetrie fuer die erweiterten Panels (§30)
# =============================================================================

_ORDER_DISPATCHER: Optional[Any] = None
_FLYWHEEL: Optional[Any] = None


def get_order_dispatcher():
    """Lazy Singleton — teilt sich die KrakenCliBridge mit Loop A."""
    global _ORDER_DISPATCHER
    if _ORDER_DISPATCHER is None:
        from app.execution.reliable_order_dispatcher import ReliableOrderDispatcher
        _ORDER_DISPATCHER = ReliableOrderDispatcher(
            get_kraken_bridge(),
            paper_bridge=KrakenCliBridge(
                load_config(),
                telemetry=get_telemetry_center(),
                execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
            ),
            futures_bridge=KrakenCliBridge(
                load_config(),
                telemetry=get_telemetry_center(),
                futures=True,
            ),
            paper_futures_bridge=KrakenCliBridge(
                load_config(),
                telemetry=get_telemetry_center(),
                execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
                futures=True,
            ),
        )
    return _ORDER_DISPATCHER


def set_order_dispatcher(dispatcher: Optional[Any]) -> None:
    global _ORDER_DISPATCHER
    _ORDER_DISPATCHER = dispatcher


def get_flywheel():
    global _FLYWHEEL
    if _FLYWHEEL is None:
        from app.execution.capital_flywheel_engine import CapitalFlywheelEngine
        from app.quant.epidemic_contagion_engine import get_contagion_engine

        contagion = get_contagion_engine()
        _FLYWHEEL = CapitalFlywheelEngine(
            treasury_guard=contagion.treasury_allowed,
        )
    return _FLYWHEEL


def set_flywheel(flywheel: Optional[Any]) -> None:
    global _FLYWHEEL
    _FLYWHEEL = flywheel


class DepthLevel(BaseModel):
    price: float
    volume: float


class ConfluenceIn(BaseModel):
    symbol: str
    direction: str = "BUY"
    bids: List[DepthLevel] = Field(default_factory=list)
    asks: List[DepthLevel] = Field(default_factory=list)
    timestamp: float = 0.0


class ContagionIn(BaseModel):
    oil_vol_zscore: float = 0.0
    gold_dxy_ratio_change: float = 0.0
    cross_asset_correlation: float = 0.0
    orderbook_absorption: float = 1.0


class FlywheelDepositIn(BaseModel):
    amount_eur: float
    note: str = "operator deposit"


class FlywheelProfitIn(BaseModel):
    amount_eur: float
    strategy_id: str = ""


class FlywheelReconcileIn(BaseModel):
    executed: bool
    order_id: str = ""


def _require_operator(request: Request, token: Optional[str]) -> None:
    if _OPERATOR_AUTH_OVERRIDE is not None and _OPERATOR_AUTH_OVERRIDE(request):
        return
    from app.server.main import state

    if not token or state.passkey is None \
            or state.passkey.validate_settings_token(token) is None:
        raise HTTPException(403, detail={
            "code": "OPERATOR_AUTH_REQUIRED",
            "reason": "Passkey settings token required for flywheel mutation",
        })


@router.get("/api/v1/clock")
async def exchange_clock_state():
    """§23.1 — Kraken-Serverzeit als Single Source of Truth."""
    from app.core.exchange_clock import get_exchange_clock

    clock = get_exchange_clock()
    status = clock.maybe_resync().as_dict()
    status["now"] = clock.now()
    status["host_now"] = time.time()
    status["stale_signal_max_latency_s"] = bp.STALE_SIGNAL_MAX_LATENCY_S
    return status


@router.get("/api/v1/scheduler")
async def scheduler_telemetry():
    """§23.2 — Tier 0-5 Cadence-Matrix (SchedulerTelemetryPanel)."""
    from app.core.scheduler_matrix import get_scheduler

    return get_scheduler().telemetry()


@router.post("/api/v1/orderbook/confluence")
async def orderbook_confluence(body: ConfluenceIn):
    """§24 — JIT Glint x Orderbook Audit (nie als Poll-Loop aufrufen)."""
    from app.quant.glint_orderbook_verifier import OrderbookSnapshot, get_verifier

    from app.core.exchange_clock import get_exchange_clock

    clock = get_exchange_clock()
    if body.bids and body.asks:
        snapshot = OrderbookSnapshot(
            symbol=body.symbol,
            bids=[(lvl.price, lvl.volume) for lvl in body.bids],
            asks=[(lvl.price, lvl.volume) for lvl in body.asks],
            timestamp=body.timestamp or clock.now(),
        )
    else:
        try:
            snapshot = get_depth_adapter().fetch(body.symbol)
        except Exception as exc:
            raise HTTPException(503, detail={
                "code": "ORDERBOOK_DEPTH_UNAVAILABLE", "reason": str(exc)
            }) from exc
    return get_verifier().verify(
        snapshot, body.direction, now=clock.now()
    ).as_dict()


@router.get("/api/v1/orderbook/confluence")
async def orderbook_confluence_state():
    from app.quant.glint_orderbook_verifier import get_verifier

    return get_verifier().panel_state()


@router.get("/api/orders/receipts")
async def order_receipts(limit: int = 50):
    """§25 — Closed-Loop Receipts (OrderReceiptsPanel)."""
    state = get_order_dispatcher().panel_state()
    state["receipts"] = get_order_dispatcher().receipts(limit)
    return state


@router.get("/api/v1/rate-limiter")
async def rate_limiter_state():
    """§26 — Token-Bucket-Status je Provider (RateLimiterPanel)."""
    from app.core.rate_limiter import get_rate_limiter

    return get_rate_limiter().status()


@router.get("/api/v1/contagion")
async def contagion_state():
    """§27 — SIR-Fruehwarnung (ContagionRadarPanel)."""
    from app.quant.epidemic_contagion_engine import get_contagion_engine

    return get_contagion_engine().panel_state()


@router.post("/api/v1/contagion")
async def contagion_update(body: ContagionIn):
    from app.quant.epidemic_contagion_engine import (ContagionInputs,
                                                     get_contagion_engine)

    state = get_contagion_engine().evaluate(ContagionInputs(**body.model_dump()))
    return state.as_dict()


@router.get("/api/v1/flywheel")
async def flywheel_state():
    """§28 — 50/50 Kapitalarchitektur (FlywheelBudgetPanel)."""
    return get_flywheel().panel_state()


@router.post("/api/v1/flywheel/deposit")
async def flywheel_deposit(body: FlywheelDepositIn, request: Request,
                           x_sigma_settings_token: Optional[str] = Header(default=None)):
    _require_operator(request, x_sigma_settings_token)
    entry = get_flywheel().deposit(body.amount_eur, body.note)
    return {"entry": entry.as_dict(), "state": get_flywheel().panel_state()}


@router.post("/api/v1/flywheel/profit")
async def flywheel_profit(body: FlywheelProfitIn, request: Request,
                          x_sigma_settings_token: Optional[str] = Header(default=None)):
    _require_operator(request, x_sigma_settings_token)
    outcome = get_flywheel().register_realized_profit(
        body.amount_eur, strategy_id=body.strategy_id)
    return {"result": outcome, "state": get_flywheel().panel_state()}


@router.post("/api/v1/flywheel/sweep")
async def flywheel_sweep(request: Request,
                         x_sigma_settings_token: Optional[str] = Header(default=None)):
    _require_operator(request, x_sigma_settings_token)
    return {"result": get_flywheel().sweep(), "state": get_flywheel().panel_state()}


@router.post("/api/v1/flywheel/reconcile")
async def flywheel_reconcile(body: FlywheelReconcileIn, request: Request,
                             x_sigma_settings_token: Optional[str] = Header(default=None)):
    _require_operator(request, x_sigma_settings_token)
    return {
        "result": get_flywheel().reconcile_vault_purchase(
            executed=body.executed, order_id=body.order_id
        ),
        "state": get_flywheel().panel_state(),
    }


@router.get("/api/v1/leverage/{strategy_id}")
async def leverage_profile(strategy_id: str, style: Optional[str] = None):
    """§29 — fester Hebel pro Strategie inkl. Strategy-Card-Badge."""
    from app.execution.fixed_leverage import load_profile

    cfg = load_config()
    root = getattr(cfg, "strategies_dir", "./data/strategies")
    return load_profile(strategy_id, strategies_root=root, style=style).as_dict()
