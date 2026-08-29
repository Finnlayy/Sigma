"""
=========================================================
Datei:      app/server/main.py
Zweck:      FastAPI-Execution-API (Ubuntu Core 192.168.178.50)
            — kompletter M8-Pipeline-Lauf: Ingestion → Signal → Judge →
              Fast-Path → Paper-Fill → MFE/MAE → Close → Vault → Autopsy
Knoten:     Jaune (Carrera-Engine) / API
=========================================================
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import math
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.tv.alert_provisioner import build_alert_message
from app.core import blueprint as bp
from app.core.config import AlphaConfig, load_config
from app.core.l4_config import load_l4_config
from app.core.duckdb_store import get_store
from app.core.event_bus import EventBus, get_event_bus
from app.core.redis_client import close_redis, get_redis
from app.core.telemetry import get_telemetry_center
from app.execution.EodProfitFactorEngine import EodProfitFactorEngine
from app.execution.FeeEngine import FeeEngine
from app.execution.JudgeEngine import JudgeEngine
from app.execution.LeverageEngine import LeverageEngine
from app.execution.M8StateEngine import M8StateEngine
from app.execution.PaperExecutionEngine import PaperExecutionEngine
from app.execution.StrategyInterpreter import generate_signal
from app.execution.TradeChurnGuard import ChurnGuardConfig, TradeChurnGuard
from app.execution.VaultEngine import VaultEngine
from app.backtest.BacktestEngine import resample_candles
from app.backtest.TvMcpBacktest import get_adapter, set_adapter, TvMcpBacktest
from app.mcp.TradingViewMCPClient import TradingViewMCPClient, TvMcpError, FakeTvMcpTransport
from app.ingestion.OmniStreamIngestor import OmniStreamIngestor
from app.mcp.KrakenMCPBridge import KrakenMCPBridge
from app.optimizer.AcademyRegistry import AcademyRegistry
from app.optimizer.GeneticOptimizer import GeneticOptimizer
from app.quant.RegimeEngine import ampel_status, dfa_hurst, lead_lag_matrix, sentiment_score
from app.security.PasskeyAuthEngine import PasskeyAuthEngine
from app.security.SettingsEnvManager import SettingsEnvManager
from app.telegram.TelegramBotEngine import TelegramBotEngine

logger = logging.getLogger("app.server.main")


def _tv_schema_a(strategy_id: str) -> str:
    return build_alert_message(
        strategy_id, "<SIGMA_WEBHOOK_SECRET>", execution_mode="kraken_paper")


_PINE_FEATURE_PLOTS = """
plot(ta.rsi(close, 14), "rsi", display=display.none)
plot(atr, "atr", display=display.none)
plot(0.5, "cisd", display=display.none)
plot(close - atr * atrMultSL, "sl", display=display.none)
plot(close + atr * atrMultTP, "tp", display=display.none)
"""

FACTORY_STRATEGIES: List[Dict[str, Any]] = [
    {
        "id": "MEAN_REV_V3__BTC-USDT__15m__PAPER",
        "name": "BTC Mean-Reversion V3",
        "description": "RSI-Reversion Pine v6 auf BTC/USD mit ATR-Stop 1.5x und TP 2.2x. Seed-Manifest — Strategy≡TV.",
        "code": ("""//@version=6
strategy("BTC Mean-Reversion V3", overlay=true, initial_capital=10000, commission_type=strategy.commission.percent, commission_value=0.1, pyramiding=0)
// Sigma L4 — RSI Reversion + ATR Bracket (BaFin/MiCA Kraken CLI)
rsiLength = input.int(14, "RSI Length", minval=2)
rsiLower = input.int(32, "RSI Oversold", minval=5, maxval=50)
rsiUpper = input.int(68, "RSI Overbought", minval=50, maxval=95)
atrLength = input.int(14, "ATR Length", minval=2)
atrMultSL = input.float(1.5, "ATR SL Mult", step=0.1)
atrMultTP = input.float(2.2, "ATR TP Mult", step=0.1)
hardStopPct = input.float(4.0, "Hard Stop %", step=0.1)
rsi = ta.rsi(close, rsiLength)
atr = ta.atr(atrLength)
longCond = ta.crossover(rsi, rsiLower) or (rsi < rsiLower and ta.crossover(ta.sma(close, 12), ta.sma(close, 48)))
shortCond = ta.crossunder(rsi, rsiUpper) or (rsi > rsiUpper and ta.crossunder(ta.sma(close, 12), ta.sma(close, 48)))
""" + _PINE_FEATURE_PLOTS + """
if longCond
    strategy.entry("LONG", strategy.long, alert_message='%s')
    strategy.exit("LONG_EXIT", from_entry="LONG", stop=close - atr*atrMultSL, limit=close + atr*atrMultTP)
if shortCond
    strategy.entry("SHORT", strategy.short, alert_message='%s')
    strategy.exit("SHORT_EXIT", from_entry="SHORT", stop=close + atr*atrMultSL, limit=close - atr*atrMultTP)
""" % ((_tv_schema_a("MEAN_REV_V3__BTC-USDT__15m__PAPER"),) * 2)),
        "status": "active",
        "assetPair": "BTC/USD",
        "interval": 15,
        "executionMode": "paper",
        "parameters": {"rsiLength": 14, "rsiLower": 32, "rsiUpper": 68, "atrLength": 14, "atrMultSL": 1.5, "atrMultTP": 2.2, "hardStopPct": 4.0},
        "pine_inputs_schema": {"rsiLength": "int", "rsiLower": "int", "rsiUpper": "int", "atrLength": "int", "atrMultSL": "float", "atrMultTP": "float", "hardStopPct": "float"},
        "hardStopEnabled": True,
        "hardStopPercent": 4.0,
        "createdAt": _dt.datetime(2026, 8, 1, 8, 0, 0).isoformat() + "Z",
        "version": 3,
    },
    {
        "id": "SMA_CROSS_V2__ETH-USDT__15m__PAPER",
        "name": "ETH Momentum SMA-Cross",
        "description": "SMA 12/48 Golden- & Death-Cross Pine v6 auf ETH/USD, ATR-gestoppt. Strategy≡TV.",
        "code": ("""//@version=6
strategy("ETH Momentum SMA-Cross", overlay=true, initial_capital=10000, commission_type=strategy.commission.percent, commission_value=0.1, pyramiding=0)
// Sigma L4 — SMA Cross Momentum
fastLen = input.int(12, "Fast SMA", minval=2)
slowLen = input.int(48, "Slow SMA", minval=5)
atrLen = input.int(14, "ATR Length")
atrMultSL = input.float(1.5, "ATR SL")
atrMultTP = input.float(2.5, "ATR TP")
hardStopPct = input.float(4.5, "Hard Stop %", step=0.1)
fast = ta.sma(close, fastLen)
slow = ta.sma(close, slowLen)
atr = ta.atr(atrLen)
longCond = ta.crossover(fast, slow)
shortCond = ta.crossunder(fast, slow)
""" + _PINE_FEATURE_PLOTS + """
if longCond
    strategy.entry("LONG", strategy.long, alert_message='%s')
    strategy.exit("LONG_EXIT", from_entry="LONG", stop=close - atr*atrMultSL, limit=close + atr*atrMultTP)
if shortCond
    strategy.entry("SHORT", strategy.short, alert_message='%s')
    strategy.exit("SHORT_EXIT", from_entry="SHORT", stop=close + atr*atrMultSL, limit=close - atr*atrMultTP)
""" % ((_tv_schema_a("SMA_CROSS_V2__ETH-USDT__15m__PAPER"),) * 2)),
        "status": "active",
        "assetPair": "ETH/USD",
        "interval": 15,
        "executionMode": "paper",
        "parameters": {"fastLen": 12, "slowLen": 48, "atrLen": 14, "atrMultSL": 1.5, "atrMultTP": 2.5, "hardStopPct": 4.5},
        "pine_inputs_schema": {"fastLen": "int", "slowLen": "int", "atrLen": "int", "atrMultSL": "float", "atrMultTP": "float", "hardStopPct": "float"},
        "hardStopEnabled": True,
        "hardStopPercent": 4.5,
        "createdAt": _dt.datetime(2026, 8, 3, 10, 30, 0).isoformat() + "Z",
        "version": 2,
    },
    {
        "id": "EMA_TREND_V1__SOL-USDT__15m__PAPER",
        "name": "SOL EMA Trend Rider",
        "description": "EMA 12/60 Trend-Following Pine v6 auf SOL/USD mit 1.5x-ATR-Stop. Strategy≡TV.",
        "code": ("""//@version=6
strategy("SOL EMA Trend Rider", overlay=true, initial_capital=10000, commission_type=strategy.commission.percent, commission_value=0.1, pyramiding=0)
// Sigma L4 — EMA Trend
fastLen = input.int(12, "Fast EMA", minval=2)
slowLen = input.int(60, "Slow EMA", minval=5)
atrLen = input.int(14, "ATR Length")
atrMultSL = input.float(1.5, "ATR SL")
atrMultTP = input.float(3.0, "ATR TP")
hardStopPct = input.float(5.0, "Hard Stop %")
fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
atr = ta.atr(atrLen)
longCond = ta.crossover(fast, slow)
shortCond = ta.crossunder(fast, slow)
""" + _PINE_FEATURE_PLOTS + """
if longCond
    strategy.entry("LONG", strategy.long, alert_message='%s')
    strategy.exit("LONG_EXIT", from_entry="LONG", stop=close - atr*atrMultSL, limit=close + atr*atrMultTP)
if shortCond
    strategy.entry("SHORT", strategy.short, alert_message='%s')
    strategy.exit("SHORT_EXIT", from_entry="SHORT", stop=close + atr*atrMultSL, limit=close - atr*atrMultTP)
""" % ((_tv_schema_a("EMA_TREND_V1__SOL-USDT__15m__PAPER"),) * 2)),
        "status": "active",
        "assetPair": "SOL/USD",
        "interval": 15,
        "executionMode": "paper",
        "parameters": {"fastLen": 12, "slowLen": 60, "atrLen": 14, "atrMultSL": 1.5, "atrMultTP": 3.0, "hardStopPct": 5.0},
        "pine_inputs_schema": {"fastLen": "int", "slowLen": "int", "atrLen": "int", "atrMultSL": "float", "atrMultTP": "float", "hardStopPct": "float"},
        "hardStopEnabled": True,
        "hardStopPercent": 5.0,
        "createdAt": _dt.datetime(2026, 8, 9, 14, 0, 0).isoformat() + "Z",
        "version": 1,
    },
    {
        "id": "XRP_RANGE_V1__XRP-USDT__15m__PAPER",
        "name": "XRP Range Fader (Archiv)",
        "description": "Archivierter Range-Scalper Pine v6 — durch GA-Deployment ersetzt. Strategy≡TV.",
        "code": """//@version=6
strategy("XRP Range Fader (Archiv)", overlay=true, initial_capital=10000, pyramiding=0)
// Archived — range fade
rsiLen = input.int(10, "RSI Length")
rsiLower = input.int(28, "RSI Lower")
rsiUpper = input.int(72, "RSI Upper")
atrLen = input.int(14, "ATR")
atrMultSL = input.float(1.2, "ATR SL")
atrMultTP = input.float(1.8, "ATR TP")
rsi = ta.rsi(close, rsiLen)
atr = ta.atr(atrLen)
longCond = rsi < rsiLower
shortCond = rsi > rsiUpper
if longCond
    strategy.entry("LONG", strategy.long)
    strategy.exit("LE", from_entry="LONG", stop=close - atr*atrMultSL, limit=close + atr*atrMultTP)
if shortCond
    strategy.entry("SHORT", strategy.short)
    strategy.exit("SE", from_entry="SHORT", stop=close + atr*atrMultSL, limit=close - atr*atrMultTP)
""",
        "status": "archived",
        "assetPair": "XRP/USD",
        "interval": 15,
        "executionMode": "paper",
        "parameters": {"rsiLen": 10, "rsiLower": 28, "rsiUpper": 72, "atrLen": 14, "atrMultSL": 1.2, "atrMultTP": 1.8, "hardStopPct": 3.0},
        "pine_inputs_schema": {"rsiLen": "int", "rsiLower": "int", "rsiUpper": "int", "atrLen": "int", "atrMultSL": "float", "atrMultTP": "float", "hardStopPct": "float"},
        "hardStopEnabled": True,
        "hardStopPercent": 3.0,
        "createdAt": _dt.datetime(2026, 7, 12, 9, 0, 0).isoformat() + "Z",
        "archivedAt": _dt.datetime(2026, 8, 20, 18, 0, 0).isoformat() + "Z",
        "version": 1,
    },
]


class AppState:
    """Alle Engines + ihre Verkabelung (Dependency Container)."""

    def __init__(self):
        self.config: Optional[AlphaConfig] = None
        self.redis = None
        self.store = None
        self.bus: EventBus = get_event_bus()
        self.telemetry = get_telemetry_center()
        self.m8: Optional[M8StateEngine] = None
        self.vault: Optional[VaultEngine] = None
        self.leverage: Optional[LeverageEngine] = None
        self.fee: Optional[FeeEngine] = None
        self.churn: Optional[TradeChurnGuard] = None
        self.judge: Optional[JudgeEngine] = None
        self.paper: Optional[PaperExecutionEngine] = None
        self.ingestor: Optional[OmniStreamIngestor] = None
        self.academy: Optional[AcademyRegistry] = None
        self.ga: Optional[GeneticOptimizer] = None
        self.passkey: Optional[PasskeyAuthEngine] = None
        self.settings: Optional[SettingsEnvManager] = None
        self.mcp: Optional[KrakenMCPBridge] = None
        self.telegram: Optional[TelegramBotEngine] = None
        self.eod: Optional[EodProfitFactorEngine] = None
        self.tv_backtest: Optional[TvMcpBacktest] = None
        self.safety = None
        self.kraken_cli = None
        self.deadman = None
        self.memory_watchdog = None
        self.scorecard = None
        self.flywheel = None
        self.contagion = None
        self.contagion_feed = None
        self.depth_adapter = None
        self.order_dispatcher = None
        self.fill_reconciler = None
        self.l4_pipeline = None
        self.started_at = time.time()
        self.is_paper_trading = True
        self.has_credentials = False
        self._tasks: List[asyncio.Task] = []
        self._scheduler_work: Optional[asyncio.Task] = None
        self._last_eod_day: Optional[str] = None

    # ------------------------------------------------------------- lifecycle
    async def startup(self) -> None:
        cfg = load_config()
        self.config = cfg
        logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                            format="%(asctime)s %(name)s %(levelname)s %(message)s")
        self.redis = await get_redis(cfg)
        self.store = get_store(cfg)
        self.m8 = M8StateEngine(self.redis, cfg)
        await self.m8.initialize_scripts()
        self.vault = VaultEngine(self.store, self.redis)
        self.leverage = LeverageEngine(
            max_allowed_leverage=cfg.max_allowed_leverage,
            maintenance_margin_rate=cfg.maintenance_margin_rate,
            clearance_fee_rate=cfg.clearance_fee_rate,
        )
        self.fee = FeeEngine(cfg.maker_fee_rate, cfg.taker_fee_rate)
        self.churn = TradeChurnGuard(ChurnGuardConfig(
            min_holding_seconds=cfg.churn_min_holding_seconds,
            cooldown_seconds=cfg.churn_cooldown_seconds,
            max_daily_trades=cfg.churn_max_daily_trades,
            min_fee_hurdle_multiple=cfg.churn_fee_hurdle_multiple,
        ))
        self.judge = JudgeEngine(cfg)
        self.paper = PaperExecutionEngine(self.fee, cfg)
        self.academy = AcademyRegistry(self.store, self.m8)
        self.ga = GeneticOptimizer(cfg)
        self.passkey = PasskeyAuthEngine(self.redis, cfg)
        self.settings = SettingsEnvManager(cfg)
        self.has_credentials = _kraken_credentials_present()
        self.mcp = KrakenMCPBridge(cfg, self.passkey, None, self.store)
        self.telegram = TelegramBotEngine(cfg)
        # §36 — HIGH/CRITICAL Fehler pushen ueber denselben Bot
        from app.core.error_engine import get_error_engine
        get_error_engine().notifier = self.telegram
        self.eod = EodProfitFactorEngine(self.store, self.m8)
        self._compose_l4_runtime()
        try:
            inputs = await asyncio.to_thread(self.contagion_feed.snapshot)
            self.contagion.evaluate(inputs)
        except Exception as exc:
            logger.error("initial contagion refresh failed; entries stay blocked: %s", exc)

        self._seed_strategies()
        await self._register_m8_instances()
        self.m8.store = self.store

        self.ingestor = OmniStreamIngestor(cfg, self.redis, self.store)
        self.ingestor.candle_close_subscribers.append(self._on_candle_close)
        self.mcp.ingestor = self.ingestor
        await self.ingestor.start()

        # Event-Bridges
        self.bus.subscribe(EventBus.TOPIC_WAKE)
        self.bus.subscribe(EventBus.TOPIC_AUTOPSY)
        self._tasks.append(asyncio.create_task(self._sse_heartbeat()))
        self._tasks.append(asyncio.create_task(self._eod_scheduler()))
        self._tasks.append(asyncio.create_task(self._tick_guard()))
        self._tasks.append(asyncio.create_task(self._scheduler_loop()))
        logger.info("Projekt:Sigma Core online (spec %s / skeleton v%s)",
                    cfg.spec_version, cfg.skeleton_version)
        try:
            set_adapter(TvMcpBacktest.from_config(cfg))
            self.tv_backtest = get_adapter()
            logger.info("TV MCP adapter ready (%s)", cfg.tv_mcp_url)
        except TvMcpError as exc:
            logger.error("TV MCP adapter unavailable: %s", exc)
            self.tv_backtest = None

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._scheduler_work is not None and not self._scheduler_work.done():
            await self._scheduler_work
        if self.deadman:
            await self.deadman.stop()
        if self.ingestor:
            await self.ingestor.stop()
        await close_redis()
        if self.store:
            from app.core.duckdb_store import close_store

            close_store()
            self.store = None

    def _compose_l4_runtime(self) -> None:
        """Single composition root shared by API routes, scheduler and Loop A."""
        import app.server.routes_sigma as routes
        from app.core.memory_watchdog import MemoryWatchdog, set_memory_watchdog
        from app.execution.KrakenCliBridge import KrakenCliBridge
        from app.execution.LoopAPipeline import LoopAPipeline
        from app.execution.SafetyGuard import SafetyGuard, set_safety_guard
        from app.execution.VirtualBotEngine import get_virtual_bot_engine
        from app.execution.capital_flywheel_engine import CapitalFlywheelEngine
        from app.execution.capital_vault_executor import CapitalVaultExecutor
        from app.execution.deadman_switch_daemon import (DeadmanSwitchDaemon,
                                                         set_deadman)
        from app.execution.reliable_order_dispatcher import ReliableOrderDispatcher
        from app.execution.kraken_fill_reconciler import KrakenFillReconciler
        from app.ingestion.kraken_depth_adapter import (KrakenDepthAdapter,
                                                        set_kraken_depth_adapter)
        from app.ingestion.macro_contagion_feed import MacroContagionFeed
        from app.optimizer.StrategyAllocator import get_allocator
        from app.optimizer.reward_shaping import get_reward_engine
        from app.quant.epidemic_contagion_engine import (EpidemicContagionEngine,
                                                        set_contagion_engine)
        from app.quant.glint_orderbook_verifier import get_verifier
        from app.quant.onnx_kelly import get_quant_engine
        from app.quant.self_optimizing_onnx import get_self_optimizing_engine
        from app.services.strategy_lifecycle_service import (StrategyLifecycleService,
                                                             _SERVICE,
                                                             set_lifecycle_service)
        from app.services.telegram_bot_operator import (TelegramBotOperator,
                                                         set_telegram_operator)
        from app.optimizer.strategy_scorecard import (
            StrategyScorecard, set_strategy_scorecard,
        )
        from app.tv.alert_provisioner import get_alert_provisioner
        from app.tv.worker import get_tv_queue

        cfg = self.config
        self.safety = SafetyGuard(cfg, redis_client=self.redis, store=self.store)
        set_safety_guard(self.safety)
        self.kraken_cli = KrakenCliBridge(cfg, telemetry=self.telemetry)
        deadman_bridge = self.kraken_cli
        if os.environ.get("PYTEST_CURRENT_TEST"):
            from app.execution.deadman_switch_daemon import TestDeadmanBridge

            # TestClient must never cancel/flatten a real Kraken account.
            deadman_bridge = TestDeadmanBridge()
        self.deadman = DeadmanSwitchDaemon(
            kraken_bridge=deadman_bridge,
            safety_guard=self.safety,
            timeout_seconds=cfg.deadman_timeout_seconds,
        )
        set_deadman(self.deadman)
        self.memory_watchdog = MemoryWatchdog(
            store=self.store,
            telemetry=self.telemetry,
            safety_guard=self.safety,
            idle_provider=self._runtime_idle,
            worker_restart=self._restart_tv_worker,
            pressure_hook=self._memory_pressure,
        )
        set_memory_watchdog(self.memory_watchdog)
        alloc = get_allocator(alert_provisioner=get_alert_provisioner())
        self.scorecard = StrategyScorecard(
            store=self.store,
            queue=get_tv_queue(cfg),
            allocator=alloc,
            ga_runner=self._scorecard_ga,
            live_trading_provider=lambda: bool(self.config.live_trading),
            idle_provider=self._runtime_idle,
        )
        alloc.lock_provider = self.scorecard.is_locked
        set_strategy_scorecard(self.scorecard)
        self.contagion = EpidemicContagionEngine(store=self.store)
        set_contagion_engine(self.contagion)
        self.depth_adapter = routes._DEPTH_ADAPTER or KrakenDepthAdapter()
        set_kraken_depth_adapter(self.depth_adapter)
        self.flywheel = CapitalFlywheelEngine(
            store=self.store,
            treasury_guard=self.contagion.treasury_allowed,
            vault_executor=CapitalVaultExecutor(
                self.kraken_cli,
                self.depth_adapter,
                enabled=cfg.flywheel_spot_execution_enabled,
            ),
        )
        self.contagion_feed = MacroContagionFeed(depth=self.depth_adapter)
        paper_bridge = KrakenCliBridge(
            cfg,
            telemetry=self.telemetry,
            execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
        )
        futures_bridge = KrakenCliBridge(
            cfg,
            telemetry=self.telemetry,
            futures=True,
        )
        paper_futures_bridge = KrakenCliBridge(
            cfg,
            telemetry=self.telemetry,
            execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value,
            futures=True,
        )
        self.order_dispatcher = ReliableOrderDispatcher(
            self.kraken_cli,
            paper_bridge=paper_bridge,
            futures_bridge=futures_bridge,
            paper_futures_bridge=paper_futures_bridge,
            receipts_log=cfg.orders_log_path,
        )
        self.fill_reconciler = KrakenFillReconciler(
            futures_bridge,
            self.store,
            self._on_realized_trade,
        )
        self.paper.realized_pnl_handler = self._on_realized_trade
        quant = get_quant_engine(cfg)
        virtual_bots = get_virtual_bot_engine(
            alert_provisioner=get_alert_provisioner()
        )
        telegram_operator = TelegramBotOperator(
            cfg,
            safety_guard=self.safety,
            telemetry=self.telemetry,
            virtual_bots=virtual_bots,
        )
        set_telegram_operator(telegram_operator)
        self.l4_pipeline = LoopAPipeline(
            cfg,
            safety=self.safety,
            quant=quant,
            judge=self.judge,
            m8=self.m8,
            kraken=self.kraken_cli,
            paper=self.paper,
            virtual_bots=virtual_bots,
            allocator=get_allocator(alert_provisioner=get_alert_provisioner()),
            telemetry=self.telemetry,
            deadman=self.deadman,
            telegram=telegram_operator,
            reward=get_reward_engine(),
            self_opt=get_self_optimizing_engine(quant),
            contagion=self.contagion,
            dispatcher=self.order_dispatcher,
        )

        routes.set_kraken_bridge(self.kraken_cli)
        if routes._DEPTH_ADAPTER is None:
            routes.set_depth_adapter(self.depth_adapter)
        routes.set_order_dispatcher(self.order_dispatcher)
        routes.set_flywheel(self.flywheel)
        if routes._pipeline is None:
            routes.set_pipeline(self.l4_pipeline)
        if _SERVICE is None:
            set_lifecycle_service(StrategyLifecycleService(
                virtual_bots=get_virtual_bot_engine(),
                alert_provisioner=get_alert_provisioner(),
                tv_queue=get_tv_queue(cfg),
                flywheel=self.flywheel,
                safety=self.safety,
                verifier=get_verifier(),
                depth_adapter=self.depth_adapter,
                allocator=get_allocator(),
                config=cfg,
            ))

    def _runtime_idle(self) -> bool:
        """Idle = kein laufender TV/Playwright-Job. Offene Paper-Positionen
        sind der Normalzustand und dürfen GC/Checkpoint nicht blockieren."""
        from app.tv.worker import get_tv_queue

        snapshot = get_tv_queue(self.config).snapshot()
        running = (snapshot.get("counts") or {}).get("running", 0) or 0
        return int(running) == 0

    def _memory_pressure(self, stage: int) -> str:
        if stage < 2:
            return "skip"
        try:
            from app.tv.worker import get_tv_queue

            dropped = get_tv_queue(self.config).trim_cache(keep=8)
            return f"tv cache trimmed {dropped}"
        except Exception as exc:
            return f"tv cache trim failed: {exc}"

    def _scorecard_ga(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        pair = str(cfg.get("assetPair") or "BTC/USD")
        interval = int(cfg.get("interval") or 15)
        count = int(cfg.get("candleCount") or 500)
        candles = self.store.ohlcv(pair, 60, limit=max(count * interval + 120, 300))
        candles = resample_candles(candles, max(1, interval))[-count:]
        if len(candles) < 240:
            return {"shadowGate": {"passed": False}, "error": "WFO needs 240 candles"}
        if self.ga is None:
            return {"shadowGate": {"passed": False}, "error": "ga unavailable"}
        return self.ga.run(cfg, candles)

    def _restart_tv_worker(self) -> str:
        from app.tv.worker import get_tv_queue

        queue = get_tv_queue(self.config)
        if not queue.snapshot().get("running"):
            return "tv worker external or inactive"
        if not queue.stop(timeout=1.0):
            return "tv worker restart deferred; worker still stopping"
        queue.start()
        return "tv worker restarted"

    def _on_realized_trade(self, trade: Dict[str, Any]) -> None:
        if (trade.get("execution_mode") != "live"
                or trade.get("accounting_source") != "verified_live_fill"):
            return
        fill_id = str(trade.get("fill_id") or "")
        if not fill_id:
            return
        pnl_usd = float(trade.get("net_pnl_usd") or 0.0)
        if self.safety:
            self.safety.record_pnl(pnl_usd, reference_id=fill_id)
        fx = float(self.config.flywheel_usd_to_eur_rate or 0.0)
        if (self.flywheel and trade.get("execution_mode") == "live" and fx > 0):
            self.flywheel.register_realized_profit(
                pnl_usd * fx,
                strategy_id=str(trade.get("strategy_id") or ""),
                external_ref=fill_id,
            )

    # ------------------------------------------------------------------ seeds
    def _seed_strategies(self) -> None:
        if not self.store.list_strategies():
            for s in FACTORY_STRATEGIES:
                self.store.upsert_strategy(s)
            self.bus.log("info", "Factory-Seed-Manifest geladen (4 Strategien)",
                         category="SYSTEM")
        self.academy.seed(self.store.list_strategies())

    async def _register_m8_instances(self) -> None:
        for s in self.store.list_strategies():
            if self.m8.get_strategy_state(s["id"]) is None:
                await self.m8.register_strategy(
                    s["id"],
                    base_budget_usd=self.config.base_budget_usd,
                    status="ACTIVE",
                    last_ga_recalibration_ts=time.time() - 7 * 24 * 3600,
                )

    # ------------------------------------------------------- live trade loop
    def _on_candle_close(self, symbol: str, candle: Dict[str, Any]) -> None:
        """Sync-Callback vom Ingestor → async Pipeline im Event-Loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._evaluate_symbol(symbol, candle))

    async def _evaluate_symbol(self, symbol: str, candle: Dict[str, Any]) -> None:
        cfg = self.config
        bus = self.bus
        try:
            strategies = [s for s in self.store.list_strategies()
                          if s["assetPair"] == symbol and s["status"] == "active"]
            for s in strategies:
                await self._evaluate_strategy(s, symbol)
        except Exception as exc:
            logger.exception("evaluate_symbol failed: %s", exc)
            bus.log("error", f"Signal-Pipeline-Fehler: {exc}", category="ERROR")

    async def _evaluate_strategy(self, s: Dict[str, Any], symbol: str) -> None:
        cfg = self.config
        bus = self.bus
        inst = s["id"]
        paper = self.paper

        if paper.positions_for(inst):
            return  # keine Pyramiding — 1 Position pro Instanz

        candles = self.store.ohlcv(symbol, 60, limit=1500)
        # Laufende (partielle) Kerze mit einbeziehen → Evaluation pro 1m-Close
        partial = self.ingestor._candles.get(symbol)
        if partial:
            import datetime as _dt2

            candles.append({
                "ts": _dt2.datetime.fromtimestamp(
                    int(partial["ts_bucket"]), _dt2.timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "open": partial["open"], "high": partial["high"],
                "low": partial["low"], "close": partial["close"],
                "volume": partial["volume"],
            })
        if len(candles) < 80:
            return
        # 1m-Lake auf Strategie-Intervall resamplen
        factor = max(1, int(s.get("interval") or 15))
        candles_n = resample_candles(candles, factor)
        if len(candles_n) < 80:
            return

        signal = generate_signal(s, candles_n, float(candles[-1]["close"]))
        if signal is None:
            return
        if signal["direction"] == "SHORT" and s.get("parameters", {}).get("spotOnly"):
            bus.log("info", f"{inst}: Short blockiert (Spot-Only 1.0x long-only)",
                    category="RISK", strategy_id=inst)
            return

        entry = float(signal["entry_price"])
        tp = float(signal["take_profit_price"])
        ok, reason = self.churn.validate_entry_signal(
            inst, entry, tp, taker_fee_rate=cfg.taker_fee_rate)
        if not ok:
            bus.log("warn", f"{inst}: {reason}", category="RISK", strategy_id=inst)
            return

        if await self.m8.is_symbol_halted(symbol):
            bus.log("warn", f"{inst}: HALT aktiv auf {symbol} — Signal verworfen",
                    category="CIRCUIT_BREAKER", strategy_id=inst)
            return

        state = self.m8.get_strategy_state(inst)
        if state is None:
            await self.m8.register_strategy(inst, last_ga_recalibration_ts=time.time())
            state = self.m8.get_strategy_state(inst)
        if state.status in ("QUARANTINED", "RETIRED"):
            bus.log("info", f"{inst}: SHADOW-Modus ({state.status}) — nur Counting",
                    category="SHADOW", strategy_id=inst)
            return
        if not self.telemetry.system.can_execute_orders:
            return

        sizing = self.leverage.calculate_sizing(
            "PERP", "PAPER", signal["direction"],
            state.current_budget_usd, state.budget_multiplier,
            entry, float(signal["stop_loss_price"]),
            base_leverage=1.0,
            risk_fraction_per_trade=cfg.risk_fraction_per_trade,
        )
        if not sizing.is_safe:
            bus.log("warn", f"{inst}: {sizing.rejection_reason}",
                    category="RISK", strategy_id=inst)
            return

        closes = [c["close"] for c in candles_n]
        realized_vol = _realized_vol(closes[-96:]) if len(closes) >= 30 else 0.024
        verdict = self.judge.evaluate(
            symbol, sizing.quantity_contracts,
            "BUY" if signal["direction"] == "LONG" else "SELL",
            win_rate=_win_rate(self.store, s["id"]),
            win_loss_ratio=1.8,
            target_vol=0.15,
            context={
                "realized_vol": realized_vol,
                "spread_bps": 3.0,
                "hurst_regime": (dfa_hurst(closes).get("regime") if len(closes) > 128
                                 else "RANDOM_WALK"),
                "system_state": self.telemetry.system.state,
            },
        )
        if not verdict["m8_verdict"]["passed"]:
            bus.log("warn",
                    f"{inst}: M8 JUDGE REJECT — {', '.join(verdict['m8_verdict']['rejected_by'])}",
                    category="JUDGE", strategy_id=inst)
            return

        # Fast-Path: signals:proposed / signals:verdict (~300 B JSON)
        if self.redis:
            try:
                await self.redis.publish("signals:proposed", json.dumps(
                    {**signal, "instance_id": inst, "qty": sizing.quantity_contracts},
                    default=str))
                await self.redis.publish("signals:verdict", json.dumps(
                    {"instance_id": inst, "verdict": "APPROVED",
                     "gates": verdict["m8_verdict"]["passed"]}))
            except Exception:
                pass

        signal["fee_hurdle_multiple"] = cfg.churn_fee_hurdle_multiple
        paper.open_position(s, signal, sizing.to_dict() if hasattr(sizing, "to_dict")
                            else sizing.__dict__)
        self.churn.record_entry(inst)
        bus.log("info",
                f"{inst}: SIGNAL {signal['direction']} {symbol} @ {entry:.2f} — "
                f"{signal['reason']} (M8 {state.status}, mult {state.budget_multiplier})"
                if state else f"{inst}: SIGNAL {signal['direction']} {symbol}",
                category="SIGNAL", strategy_id=inst,
                payload={"entry": entry, "stop": signal["stop_loss_price"]})

    # ------------------------------------------------------------- background
    async def _sse_heartbeat(self) -> None:
        cfg = self.config
        while True:
            try:
                self.telemetry.beat()
            except Exception:
                pass
            await asyncio.sleep(cfg.sse_interval_seconds)

    async def _eod_scheduler(self) -> None:
        """00:05 UTC täglich + idempotent (einmal pro UTC-Tag)."""
        while True:
            try:
                now = _dt.datetime.now(_dt.timezone.utc)
                if (now.hour == 0 and now.minute >= 5
                        and self._last_eod_day != now.strftime("%Y-%m-%d")):
                    self._last_eod_day = now.strftime("%Y-%m-%d")
                    results = await self.eod.run_for_day()
                    self.bus.log("info",
                                 f"EOD-Abrechnung {self._last_eod_day}: {len(results)} Strategien",
                                 category="EOD",
                                 payload={"results": len(results)})
                    await self.telegram.on_event("EOD", {"day": self._last_eod_day,
                                                         "results": len(results)})
            except Exception as exc:
                logger.warning("eod scheduler: %s", exc)
            await asyncio.sleep(30)

    async def _tick_guard(self) -> None:
        """Prüft offene Positionen bei jedem Tick (MFE/MAE/Stop/TP/Liq)."""
        while True:
            try:
                for pos in list(self.paper.all_positions()):
                    price = self.ingestor.last_price(pos["symbol"])
                    closed = await self.paper._evaluate_exit(self.m8, pos, price)
                    if closed:
                        if pos.get("exit_reason") == "LIQUIDATION":
                            await self.telegram.on_event("LIQUIDATION", pos)
            except Exception as exc:
                logger.warning("tick guard: %s", exc)
            await asyncio.sleep(1.0)

    async def _scheduler_loop(self) -> None:
        """§23.2 Tier-1 Fast Pulse — Deadman erneuert sich selbst, Memory-Watchdog tickt."""
        from app.core.scheduler_matrix import install_canonical_tasks
        sched = install_canonical_tasks(
            deadman=self.deadman,
            memory=self.memory_watchdog,
            contagion=self.contagion,
            contagion_feed=self.contagion_feed,
            flywheel=self.flywheel,
            fill_reconciler=self.fill_reconciler,
            scorecard=self.scorecard,
        )
        self.deadman.start()
        logger.info("Scheduler matrix online (%d tasks)", len(sched.tasks))
        while True:
            try:
                self._scheduler_work = asyncio.create_task(
                    asyncio.to_thread(sched.run_due)
                )
                await asyncio.shield(self._scheduler_work)
            except Exception as exc:
                logger.warning("scheduler: %s", exc)
            finally:
                if self._scheduler_work is not None and self._scheduler_work.done():
                    self._scheduler_work = None
            await asyncio.sleep(1.0)


# -------------------------------------------------------------------- helpers
def _realized_vol(closes: List[float], window: int = 96) -> float:
    """Realisierte Volatilität PRO BAR (Standardabweichung der Bar-Renditen).
    Skala der M8-Gates: Gate-1-Max 4.5% pro Bar (krypto-15m Referenz)."""
    if len(closes) < 3:
        return 0.024
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    rets = rets[-window:]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return max(1e-6, math.sqrt(var))


def _win_rate(store, strategy_id: str) -> float:
    trades = store.trades(strategy_id=strategy_id, status="closed", limit=200)
    if not trades:
        return 0.55
    wins = sum(1 for t in trades if float(t.get("net_pnl_usd") or 0) > 0)
    return max(0.35, min(0.85, wins / len(trades)))


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


def _pair_prices(state: AppState) -> Dict[str, float]:
    return {sym: state.ingestor.last_price(sym) for sym in state.config.market_symbols}


def _paper_balances(state: AppState) -> Dict[str, float]:
    """Paper-Basket (50k USD + 1.5 BTC + 10 ETH + 100 SOL + 5000 XRP) + Realised PnL."""
    balances: Dict[str, float] = {}
    for seed in state.config.paper_seeds:
        asset, amt = seed.split(":")
        balances[asset] = float(amt)
    # Realisierter PnL fließt in den USD-Bestand
    for t in state.store.trades(status="closed", limit=10000):
        if (t.get("execution_mode") or "paper") == "paper":
            balances["USD"] = balances.get("USD", 0.0) + float(t.get("net_pnl_usd") or 0.0)
    return balances


def _portfolio_value(state: AppState, balances: Dict[str, float]) -> float:
    prices = _pair_prices(state)
    value = balances.get("USD", 0.0)
    for asset, amt in balances.items():
        if asset in ("BTC", "ETH", "SOL", "XRP"):
            value += amt * prices.get(f"{asset}/USD", 0.0)
    return value


# ---------------------------------------------------------------------- app
async def _lifespan(_app: FastAPI):
    await state.startup()
    _app.state.sigma = state
    yield
    await state.shutdown()
    _app.state.sigma = None


app = FastAPI(title="Projekt:Sigma — M8 Execution API",
              description="Ubuntu-native M8 core · TradingView MCP CSV backtesting",
              version="sigma-hybrid-1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
state = AppState()


# =====================================================================
# BLUEPRINT / HEALTH (§7 API-Vertrag)
# =====================================================================
@app.get("/api/v1/health")
async def sigma_health():
    """§7 — status, kill_switch, scraper_ok, tv_worker_ok (+ Spec-Fingerprint)."""
    import os as _os

    kill = _os.path.exists(state.config.kill_switch_file)
    paused = _os.path.exists(state.config.pause_signal_file)
    tv_worker_ok = _os.path.exists(state.config.tv_jobs_dir)

    # Loop C: echter Ping gegen das Sidecar (:8001), Ergebnis 5 s gecacht.
    scraper: dict = {"ok": False, "degraded": True}
    try:
        from app.tv.scraper_client import get_scraper_client

        scraper = await asyncio.to_thread(get_scraper_client().health)
    except Exception as exc:  # pragma: no cover - defensive
        scraper = {"ok": False, "degraded": True, "error": str(exc)}

    return {
        "status": "halted" if kill else ("paused" if paused else "ok"),
        "kill_switch": kill,
        "pause": paused,
        "scraper_ok": bool(scraper.get("ok")),
        "scraper": scraper,
        "tv_worker_ok": tv_worker_ok,
        "live_trading": state.config.live_trading,
        "uptime": round(time.time() - state.started_at, 1),
        "blueprint": bp.spec_summary(),
    }


@app.get("/api/v1/blueprint")
async def sigma_blueprint():
    """Hart verdrahtete Spec (app/core/blueprint.py) + geladene L4-Config."""
    return {
        "spec": bp.spec_summary(),
        "loops": {
            loop.value: {
                "title": s.title, "trigger": s.trigger,
                "output": s.output, "autonomy": s.autonomy,
            }
            for loop, s in bp.LOOPS.items()
        },
        "m8_alert_matrix": {
            st.value: {
                "alert": pol.alert.value,
                "accept_webhook": pol.accept_webhook,
                "budget_multiplier": pol.budget_multiplier,
                "note": pol.note,
            }
            for st, pol in bp.M8_ALERT_MATRIX.items()
        },
        "api_contract": dict(bp.API_ROUTES),
        "delivery_phases": dict(bp.DELIVERY_PHASES),
        "config": load_l4_config(),
    }


# =====================================================================
# DASHBOARD / MARKET / KRAKEN
# =====================================================================
@app.get("/api/dashboard/init")
async def dashboard_init():
    st = state
    strategies = st.store.list_strategies()
    return {
        "status": "ok",
        "uptime": round(time.time() - st.started_at, 1),
        "timestamp": _iso(time.time()),
        "isPaperTrading": st.is_paper_trading,
        "hasCredentials": st.has_credentials,
        "default_timeframe": "15",
        "symbols": list(st.config.market_symbols),
        "activeStrategiesCount": sum(1 for s in strategies if s["status"] == "active"),
        "totalStrategiesCount": len(strategies),
        "lake_status": f"{st.store.lake_summary()['total_rows']} rows",
    }


@app.get("/api/kraken/status")
async def kraken_status():
    from app.core.exchange_clock import get_exchange_clock

    clock = get_exchange_clock()
    st = clock.status()
    return {
        "connected": bool(clock.synced) and st.last_error is None,
        "hasCredentials": state.has_credentials,
        "paperTrading": state.is_paper_trading,
        "mode": "paper" if state.is_paper_trading else "live",
        "latencyMs": None if clock.last_rtt_ms is None else round(clock.last_rtt_ms, 1),
        "stream": "kraken_time",
        "lastError": st.last_error,
    }


class ToggleModeBody(BaseModel):
    paperTrading: bool


@app.post("/api/kraken/toggle-mode")
async def kraken_toggle_mode(body: ToggleModeBody):
    state.is_paper_trading = bool(body.paperTrading)
    state.bus.log("info",
                  f"Modus → {'PAPER (L2)' if state.is_paper_trading else 'LIVE (L4)'}",
                  category="SYSTEM")
    return {"paperTrading": state.is_paper_trading}


@app.get("/api/market-data")
async def market_data():
    return state.ingestor.ticker_rows()


@app.get("/api/logs")
async def logs():
    st = state
    strategies = st.store.list_strategies()
    closed = st.store.trades(status="closed", limit=1000)
    open_positions = st.paper.all_positions()
    metrics = _build_metrics(st, strategies)
    orders = [_order_row(t) for t in closed[:80] if t.get("exit_time")] + \
             [_order_row(p) for p in open_positions]
    return {
        "logs": st.bus.to_log_rows(120),
        "metrics": metrics,
        "orders": orders,
        "balances": _paper_balances(st),
        "strategyPnL": _strategy_pnl(st),
    }


def _build_metrics(st: AppState, strategies: List[Dict[str, Any]]) -> Dict[str, Any]:
    from app.core.exchange_clock import get_exchange_clock
    from app.core.telemetry import _cpu_percent, _mem_usage_percent

    balances = _paper_balances(st)
    portfolio = _portfolio_value(st, balances)
    baseline = st.config.paper_baseline_usd
    closed = st.store.trades(status="closed", limit=10000)
    paper_closed = [t for t in closed if (t.get("execution_mode") or "paper") == "paper"]
    total_pnl = sum(float(t.get("net_pnl_usd") or 0.0) for t in paper_closed)
    active = [s for s in strategies if s["status"] == "active"]
    paper_active = [s for s in active if s["executionMode"] == "paper"]
    live_active = [s for s in active if s["executionMode"] == "live"]
    btc_price = st.ingestor.last_price("BTC/USD")
    return {
        "cpuUsage": round(_cpu_percent(), 1),
        "memoryUsage": round(_mem_usage_percent(), 1),
        "latencyMs": round(get_exchange_clock().last_rtt_ms, 1)
        if get_exchange_clock().last_rtt_ms is not None else 0.0,
        "activeWorkers": len(active),
        "paperWorkers": len(paper_active),
        "liveWorkers": len(live_active),
        "totalTrades": len(paper_closed),
        "profitLossPercentage": round(total_pnl / baseline * 100.0, 4),
        "balanceUSD": round(balances.get("USD", 0.0), 2),
        "balanceBTC": round(balances.get("BTC", 0.0), 4),
        "portfolioUSD": round(portfolio, 2),
        "baselineUSD": baseline,
        "initialPaperBalanceUSD": baseline,
        "automationLevel": 2 if st.is_paper_trading else 4,
        "automationLevelLabel": "Level 2 Paper Automation" if st.is_paper_trading
                                else "Level 4 Live Capital Execution",
        "activeLedgerMode": "paper" if st.is_paper_trading else "live",
        "paperBalances": {k: round(v, 2) for k, v in balances.items()},
        "liveKrakenBalances": {},
        "hasCredentials": st.has_credentials,
    }


def _order_row(t: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": t.get("trade_id"),
        "strategyId": t.get("strategy_id"),
        "strategyName": t.get("strategy_name"),
        "timestamp": str(t.get("exit_time") or t.get("entry_time") or "")[:19].replace(" ", "T"),
        "type": t.get("side") or ("buy" if (t.get("direction") or "LONG") == "LONG" else "sell"),
        "price": float(t.get("entry_price") or 0.0),
        "amount": float(t.get("quantity") or 0.0),
        "total": round(float(t.get("entry_price") or 0.0) * float(t.get("quantity") or 0.0), 2),
        "pair": t.get("symbol"),
        "status": "pending" if t.get("status") == "open" else "filled",
        "executionMode": t.get("execution_mode"),
        "pnl": float(t.get("net_pnl_usd") or 0.0) if t.get("status") == "closed" else None,
    }


def _strategy_pnl(st: AppState) -> List[Dict[str, Any]]:
    strategies = st.store.list_strategies()
    closed = st.store.trades(status="closed", limit=5000)
    open_positions = st.paper.all_positions()
    out = []
    for s in strategies:
        mine = [t for t in closed if t.get("strategy_id") == s["id"]]
        realized = sum(float(t.get("net_pnl_usd") or 0.0) for t in mine)
        wins = sum(1 for t in mine if float(t.get("net_pnl_usd") or 0) > 0)
        unrealized = 0.0
        for p in open_positions:
            if p.get("strategy_id") == s["id"]:
                price = st.ingestor.last_price(p["symbol"])
                qty = float(p.get("quantity") or 0.0)
                if p["direction"] == "LONG":
                    unrealized += (price - float(p.get("entry_price") or price)) * qty
                else:
                    unrealized += (float(p.get("entry_price") or price) - price) * qty
        volume = sum(float(t.get("notional_usd") or 0.0) for t in mine)
        out.append({
            "strategyId": s["id"],
            "strategyName": s["name"],
            "realizedPnL": round(realized, 4),
            "unrealizedPnL": round(unrealized, 4),
            "totalPnL": round(realized + unrealized, 4),
            "totalTrades": len(mine),
            "winningTrades": wins,
            "losingTrades": len(mine) - wins,
            "winRate": round(wins / len(mine) * 100.0, 1) if mine else 0.0,
            "volumeTradedUSD": round(volume, 2),
            "executionMode": s["executionMode"],
        })
    return out


# =====================================================================
# STRATEGIES CRUD + RUN + MANIFEST + CLI
# =====================================================================
@app.get("/api/strategies")
async def list_strategies():
    return state.store.list_strategies()


class StrategyBody(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = ""
    code: Optional[str] = ""
    status: Optional[str] = "inactive"
    assetPair: Optional[str] = None
    interval: Optional[int] = 15
    executionMode: Optional[str] = "paper"
    parameters: Optional[Dict[str, Any]] = None
    hardStopEnabled: Optional[bool] = True
    hardStopPercent: Optional[float] = 5.0
    seededFromId: Optional[str] = None
    seededFromName: Optional[str] = None
    version: Optional[int] = 1


@app.post("/api/strategies")
async def create_strategy(body: StrategyBody):
    sid = body.id or f"{uuid.uuid4().hex[:12]}"
    s = body.model_dump(exclude_none=True)
    s["id"] = sid
    s.setdefault("parameters", {})
    s["status"] = s.get("status", "inactive")
    s["createdAt"] = s.get("createdAt") or _iso(time.time())
    state.store.upsert_strategy(s)
    await state.m8.register_strategy(sid, last_ga_recalibration_ts=time.time())
    state.academy.seed([s])
    state.bus.log("info", f"Strategy created: {s.get('name')} ({s.get('assetPair')})",
                  category="SYSTEM", strategy_id=sid)
    return state.store.get_strategy(sid)


_SCHEMA_A_ALERT = _tv_schema_a("REPLACE_ME")

_PINE_CISD = f'''//@version=6
strategy("Sigma CISD Momentum", overlay=true, initial_capital=1000, pyramiding=0)

fastLen      = input.int(12, "Fast EMA")
slowLen      = input.int(60, "Slow EMA")
atrLen       = input.int(14, "ATR Period")
atrMult      = input.float(1.5, "ATR Stop Multiplier")
cisdLookback = input.int(12, "CISD Lookback")
cisdDisp     = input.float(1.5, "CISD Displacement Mult")

fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
atr  = ta.atr(atrLen)
avgBody = ta.sma(math.abs(close - open), cisdLookback)
bullDisp = (close - open) > avgBody * cisdDisp and close > ta.highest(high, cisdLookback)[1]
bearDisp = (open - close) > avgBody * cisdDisp and close < ta.lowest(low, cisdLookback)[1]
cisdBull = bullDisp and fast > slow
cisdBear = bearDisp and fast < slow
plot(ta.rsi(close, 14), "rsi", display=display.none)
plot(atr, "atr", display=display.none)
plot(0.5, "cisd", display=display.none)
plot(close - atr * atrMult, "sl", display=display.none)
plot(close + atr * atrMult * 2, "tp", display=display.none)

if ta.crossover(fast, slow) or cisdBull
    strategy.entry("L", strategy.long, alert_message = '{_SCHEMA_A_ALERT}')
    strategy.exit("XL", "L", stop = close - atr * atrMult, limit = close + atr * atrMult * 2)

if ta.crossunder(fast, slow) or cisdBear
    strategy.entry("S", strategy.short, alert_message = '{_SCHEMA_A_ALERT}')
    strategy.exit("XS", "S", stop = close + atr * atrMult, limit = close - atr * atrMult * 2)
'''

_PINE_RSI = f'''//@version=6
strategy("Sigma RSI Reversion", overlay=true, initial_capital=1000, pyramiding=0)

rsiLen   = input.int(14, "RSI Period")
rsiLower = input.int(32, "RSI Oversold")
rsiUpper = input.int(68, "RSI Overbought")
atrLen   = input.int(14, "ATR Period")
atrMult  = input.float(1.5, "ATR Stop Multiplier")

rsi = ta.rsi(close, rsiLen)
atr = ta.atr(atrLen)
plot(rsi, "rsi", display=display.none)
plot(atr, "atr", display=display.none)
plot(0.5, "cisd", display=display.none)
plot(close - atr * atrMult, "sl", display=display.none)
plot(close + atr * atrMult * 2, "tp", display=display.none)

if ta.crossunder(rsi, rsiLower)
    strategy.entry("L", strategy.long, alert_message = '{_SCHEMA_A_ALERT}')
    strategy.exit("XL", "L", stop = close - atr * atrMult, limit = close + atr * atrMult * 2)

if ta.crossover(rsi, rsiUpper)
    strategy.entry("S", strategy.short, alert_message = '{_SCHEMA_A_ALERT}')
    strategy.exit("XS", "S", stop = close + atr * atrMult, limit = close - atr * atrMult * 2)
'''

_PINE_EMPTY = '''//@version=6
strategy("Sigma Empty")
'''

PINE_STRATEGY_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "cisd": {
        "name": "CISD Momentum",
        "description": "CISD/EMA momentum Pine v6 — displacement through prior swing plus EMA trend.",
        "code": _PINE_CISD,
        "assetPair": "BTC/USD",
        "parameters": {
            "template": "cisd",
            "trendFastEma": 12,
            "trendSlowEma": 60,
            "cisdLookback": 12,
            "cisdDisplacementMult": 1.5,
            "atrStopMultiplier": 1.5,
        },
    },
    "rsi": {
        "name": "RSI Reversion",
        "description": "RSI mean-reversion Pine v6 — oversold/overbought crosses with ATR stop/TP.",
        "code": _PINE_RSI,
        "assetPair": "BTC/USD",
        "parameters": {
            "template": "rsi",
            "rsiPeriod": 14,
            "rsiLower": 32,
            "rsiUpper": 68,
            "atrStopMultiplier": 1.5,
        },
    },
    "empty": {
        "name": "Sigma Empty",
        "description": "Minimal Pine v6 strategy stub.",
        "code": _PINE_EMPTY,
        "assetPair": "BTC/USD",
        "parameters": {"template": "empty"},
    },
}


class FromTemplateBody(BaseModel):
    template: str
    name: Optional[str] = None
    assetPair: Optional[str] = None
    interval: Optional[int] = 15


@app.post("/api/strategies/from-template")
async def create_strategy_from_template(body: FromTemplateBody):
    spec = PINE_STRATEGY_TEMPLATES.get((body.template or "").strip().lower())
    if spec is None:
        raise HTTPException(400, f"unknown template: {body.template!r}")
    sid = uuid.uuid4().hex[:12]
    s = {
        "id": sid,
        "name": body.name or spec["name"],
        "description": spec["description"],
        "code": spec["code"],
        "status": "inactive",
        "assetPair": body.assetPair or spec.get("assetPair") or "BTC/USD",
        "interval": int(body.interval if body.interval is not None else 15),
        "executionMode": "paper",
        "parameters": dict(spec.get("parameters") or {}),
        "hardStopEnabled": True,
        "hardStopPercent": 5.0,
        "createdAt": _iso(time.time()),
        "version": 1,
    }
    state.store.upsert_strategy(s)
    await state.m8.register_strategy(sid, last_ga_recalibration_ts=time.time())
    state.academy.seed([s])
    state.bus.log("info",
                  f"Strategy created from template {body.template}: {s.get('name')} ({s.get('assetPair')})",
                  category="SYSTEM", strategy_id=sid)
    return state.store.get_strategy(sid)


@app.put("/api/strategies/{strategy_id}")
async def update_strategy(strategy_id: str, body: StrategyBody):
    existing = state.store.get_strategy(strategy_id)
    if not existing:
        raise HTTPException(404, "Strategy not found")
    merged = {**existing, **body.model_dump(exclude_none=True)}
    merged["id"] = strategy_id
    merged.setdefault("parameters", existing.get("parameters") or {})
    state.store.upsert_strategy(merged)
    state.bus.log("info", f"Strategy updated: {merged.get('name')}",
                  category="SYSTEM", strategy_id=strategy_id)
    return state.store.get_strategy(strategy_id)


@app.delete("/api/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str):
    state.store._exec("DELETE FROM strategies WHERE id = ?", [strategy_id])
    state.bus.log("info", f"Strategy deleted: {strategy_id}", category="SYSTEM")
    return {"ok": True}


@app.post("/api/strategies/{strategy_id}/archive")
async def archive_strategy(strategy_id: str):
    s = state.store.get_strategy(strategy_id)
    if not s:
        raise HTTPException(404, "Strategy not found")
    s["status"] = "archived"
    s["archivedAt"] = _iso(time.time())
    state.store.upsert_strategy(s)
    return {"strategy": state.store.get_strategy(strategy_id)}


@app.post("/api/strategies/{strategy_id}/restore")
async def restore_strategy(strategy_id: str):
    s = state.store.get_strategy(strategy_id)
    if not s:
        raise HTTPException(404, "Strategy not found")
    s["status"] = "inactive"
    s["archivedAt"] = None
    state.store.upsert_strategy(s)
    return {"strategy": state.store.get_strategy(strategy_id)}


class RunBody(BaseModel):
    id: str
    action: str
    mode: Optional[str] = None


@app.post("/api/run")
async def run_toggle(body: RunBody):
    s = state.store.get_strategy(body.id)
    if not s:
        raise HTTPException(404, "Strategy not found")
    if body.mode:
        s["executionMode"] = body.mode
    if body.action == "start":
        s["status"] = "active"
    else:
        s["status"] = "inactive"
        state.paper.cancel_all(strategy_id=body.id, current_prices=_pair_prices(state))
    state.store.upsert_strategy(s)
    state.m8.mark_ga_recalibration(body.id) if body.action == "start" else None
    state.bus.log("info",
                  f"{'▶ START' if body.action == 'start' else '■ STOP'} {s['name']} "
                  f"({s['executionMode'].upper()})", category="SYSTEM", strategy_id=body.id)
    return state.store.get_strategy(body.id)


class CliBody(BaseModel):
    command: str


@app.post("/api/cli-command")
async def cli_command(body: CliBody):
    cmd = body.command.strip()
    bus = state.bus
    if cmd in ("status", "status ", ""):
        return {"reply": f"M8 Core online — {len(state.store.list_strategies())} Strategien, "
                         f"Vault {state.vault.balance():.2f} USD, "
                         f"{len(state.paper.all_positions())} offene Paper-Positionen."}
    if cmd == "strategies":
        lines = [f"{s['status']:>8}  {s['executionMode']:>5}  {s['name']} ({s['assetPair']})"
                 for s in state.store.list_strategies()]
        return {"reply": "\n".join(lines) or "(keine Strategien)"}
    if cmd == "vault":
        return {"reply": f"Vault-Balance: {state.vault.balance():.2f} USD "
                         f"({len(state.vault.entries())} Einträge)"}
    if cmd == "m8" or cmd.startswith("m8 "):
        states = await state.m8.scan_states()
        lines = [f"{v['status']:>12}  mult={v['budget_multiplier']:<4}  "
                 f"budget={v['current_budget_usd']:.2f}/{v['base_budget_usd']:.2f}  {k}"
                 for k, v in list(states.items())[:12]]
        return {"reply": "\n".join(lines)}
    if cmd.startswith("halt "):
        sym = cmd.split()[1]
        await state.m8.halt_symbol(sym)
        return {"reply": f"⛔ {sym} für 300s gehalted (halt:symbol TTL)"}
    if cmd == "eod":
        results = await state.eod.run_for_day()
        lines = [f"{r['instance_id']}: PF={r['profit_factor']:.2f} trades={r['trades_count']} → {r['new_status']}"
                 for r in results]
        return {"reply": "\n".join(lines) or "EOD: keine registrierten Instanzen."}
    if cmd == "PENDING_AI_GENERATION" or cmd.startswith("generate "):
        prompt = cmd.replace("generate ", "", 1) or "Erzeuge eine Trend-Strategie für BTC/USD"
        bus.log("info", "AI-Generierung angefordert (Copilot)", category="AI")
        return {"reply": f"PENDING_AI_GENERATION:{prompt}"}
    if cmd == "reset":
        _reset_history()
        return {"reply": "Historie zurückgesetzt (Paper-Trades & PnL)."}
    bus.log("warn", f"CLI: unbekannter Befehl '{cmd}'", category="SYSTEM")
    return {"reply": f"Unbekannter CLI-Befehl: {cmd}\nVerfügbar: status | strategies | m8 | vault | halt <SYMBOL> | eod | reset"}


def _reset_history() -> None:
    state.store._exec("DELETE FROM trades")
    state.paper.open_positions.clear()
    state.bus.log("info", "History reset durchgeführt", category="SYSTEM")


@app.post("/api/history/reset")
async def history_reset():
    _reset_history()
    return {"ok": True}


# ------------------------------------------------------------------ manifest
@app.get("/api/manifest")
async def manifest():
    strategies = state.store.list_strategies()
    return {
        "schemaVersion": "1.6.4",
        "manifestId": f"alpha-{uuid.uuid4().hex[:8]}",
        "updatedAt": _iso(time.time()),
        "totalStrategies": len(strategies),
        "activeCount": sum(1 for s in strategies if s["status"] == "active"),
        "environment": "paper" if state.is_paper_trading else "live",
        "strategies": strategies,
        "persistedPath": f"{state.config.resolved_duckdb_path}::strategies",
    }


@app.post("/api/manifest/import")
async def manifest_import(request: Request):
    data = await request.json()
    payload = data if isinstance(data, dict) else {}
    rows = payload.get("strategies", [])
    imported = 0
    for s in rows:
        if not s.get("id"):
            s["id"] = f"{uuid.uuid4().hex[:12]}"
        state.store.upsert_strategy(s)
        await state.m8.register_strategy(s["id"], last_ga_recalibration_ts=time.time())
        imported += 1
    state.bus.log("info", f"Manifest import: {imported} Strategien", category="SYSTEM")
    return {"ok": True, "imported": imported}


@app.post("/api/manifest/reset")
async def manifest_reset():
    state.store._exec("DELETE FROM strategies")
    for s in FACTORY_STRATEGIES:
        state.store.upsert_strategy(s)
    state.academy.seed(state.store.list_strategies())
    state.bus.log("info", "Factory-Seed-Manifest wiederhergestellt", category="SYSTEM")
    return {"ok": True}


class EmergencyBody(BaseModel):
    strategyId: Optional[str] = None
    reason: Optional[str] = None


@app.post("/api/emergency/cancel-all")
async def emergency_cancel_all(body: EmergencyBody):
    state.telemetry.set_state("EMERGENCY_HALT", reason=body.reason or "manual")
    count = state.paper.cancel_all(strategy_id=body.strategyId,
                                   current_prices=_pair_prices(state))
    state.bus.log("error",
                  f"🚨 EMERGENCY CANCEL-ALL: {count} Positionen geschlossen "
                  f"({body.reason or 'manual'})", category="CIRCUIT_BREAKER")
    await state.telegram.on_event("EMERGENCY", {"closed": count})
    return {"ok": True, "closedPositions": count,
            "message": f"{count} offene Positionen notfallmäßig geschlossen, "
                       f"Workers suspendiert."}


# =====================================================================
# MARKET DATA / OHLC / PNL
# =====================================================================
@app.get("/api/backtest/ohlc")
async def backtest_ohlc(pair: str, interval: int = 15, count: int = 80):
    """OHLC via TradingView MCP (CSV). Falls back to store only for candle timestamps
    when MCP returns empty — never runs local backtest engine."""
    adapter = state.tv_backtest
    if adapter is None:
        raise HTTPException(503, "TradingView MCP adapter unavailable (set SIGMA_TV_MCP_URL)")
    try:
        payload = await asyncio.to_thread(adapter.fetch_ohlc, pair, int(interval), int(count))
        if payload.get("candles"):
            return payload
    except TvMcpError as exc:
        raise HTTPException(502, str(exc))
    # Display helpers may still use store candles for charting when MCP OHLC empty
    candles = state.store.ohlcv(pair, 60, limit=max(count * max(1, interval) + 60, 300))
    factor = max(1, int(interval))
    out = resample_candles(candles, factor)[-int(count):]
    return {
        "pair": pair,
        "interval": int(interval),
        "total": len(out),
        "source": "duckdb-store-display-only",
        "candles": [{
            "time": int(_ts_epoch(c["ts"])),
            "open": c["open"], "high": c["high"], "low": c["low"],
            "close": c["close"], "volume": c["volume"],
            "timestamp": str(c["ts"]),
        } for c in out],
    }


def _ts_epoch(ts: str) -> float:
    try:
        dt = _dt.datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=_dt.timezone.utc).timestamp()
    except Exception:
        return 0.0


@app.get("/api/pnl/history/{strategy_id}")
async def pnl_history(strategy_id: str):
    trades = state.store.trades(strategy_id=strategy_id, status="closed", limit=1000)
    trades.sort(key=lambda t: str(t.get("exit_time") or t.get("entry_time") or ""))
    points = []
    cum = 0.0
    now = time.time()
    for t in trades:
        cum += float(t.get("net_pnl_usd") or 0.0)
        ts = _ts_epoch(str(t.get("exit_time") or t.get("entry_time") or ""))
        points.append({
            "time": _dt.datetime.fromtimestamp(ts or now, _dt.timezone.utc).strftime("%H:%M"),
            "value": round(cum, 4),
        })
    # aktuelle open-Position unrealisiert ergänzen
    for p in state.paper.positions_for(strategy_id):
        price = state.ingestor.last_price(p["symbol"])
        qty = float(p.get("quantity") or 0.0)
        upnl = ((price - float(p.get("entry_price") or price)) * qty
                if p["direction"] == "LONG"
                else (float(p.get("entry_price") or price) - price) * qty)
        cum += upnl
        points.append({"time": _dt.datetime.now(_dt.timezone.utc).strftime("%H:%M"),
                       "value": round(cum, 4)})
    values = [p["value"] for p in points] or [0.0]
    return {
        "data": points,
        "high": max(values),
        "low": min(values),
        "currentPnL": round(values[-1], 4),
    }


@app.get("/api/pnl/daily/{endpoint_id}")
async def pnl_daily(endpoint_id: str, days: int = 90, strategies: str = ""):
    """DailyPnLHeatmapData — pro Strategie oder combined_all/paper/live/custom_multi."""
    st = state
    strategies_all = st.store.list_strategies()
    if endpoint_id in ("combined_all", "combined_paper", "combined_live"):
        pool = strategies_all
        if endpoint_id == "combined_paper":
            pool = [s for s in pool if s["executionMode"] == "paper"]
        elif endpoint_id == "combined_live":
            pool = [s for s in pool if s["executionMode"] == "live"]
        name, pair = f"All ({endpoint_id.replace('combined_', '')})", "Multi"
    elif endpoint_id == "custom_multi":
        ids = {x for x in strategies.split(",") if x}
        pool = [s for s in strategies_all if s["id"] in ids]
        name, pair = f"Custom ({len(pool)})", "Multi"
    else:
        s = next((x for x in strategies_all if x["id"] == endpoint_id), None)
        if not s:
            raise HTTPException(404, "Strategy not found")
        pool, name, pair = [s], s["name"], s["assetPair"]

    closed = []
    for s in pool:
        closed += st.store.trades(strategy_id=s["id"], status="closed", limit=5000)
    by_day: Dict[str, Dict[str, float]] = {}
    for t in closed:
        day = str(t.get("exit_time") or t.get("entry_time") or "")[:10]
        if not day:
            continue
        d = by_day.setdefault(day, {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0,
                                    "vol": 0.0, "realized": 0.0})
        pnl = float(t.get("net_pnl_usd") or 0.0)
        d["pnl"] += pnl
        d["realized"] += pnl
        d["trades"] += 1
        d["wins"] += 1 if pnl > 0 else 0
        d["losses"] += 1 if pnl <= 0 else 0
        d["vol"] += float(t.get("notional_usd") or 0.0)

    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    days_out = []
    for i in range(int(days) - 1, -1, -1):
        d = (today and _dt.datetime.strptime(today, "%Y-%m-%d")
             - _dt.timedelta(days=int(days) - 1 - i)).strftime("%Y-%m-%d")
        rec = by_day.get(d, {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0,
                             "vol": 0.0, "realized": 0.0})
        dt = _dt.datetime.strptime(d, "%Y-%m-%d")
        days_out.append({
            "date": d,
            "formattedDate": dt.strftime("%b %d, %Y"),
            "dayOfWeek": dt.weekday(),
            "dayLabel": dt.strftime("%a"),
            "dayOfMonth": dt.day,
            "monthLabel": dt.strftime("%b"),
            "pnl": round(rec["pnl"], 4),
            "realizedPnL": round(rec["realized"], 4),
            "unrealizedPnL": 0.0,
            "tradesCount": int(rec["trades"]),
            "wins": int(rec["wins"]),
            "losses": int(rec["losses"]),
            "winRate": round(rec["wins"] / rec["trades"] * 100.0, 1) if rec["trades"] else 0.0,
            "volumeUSD": round(rec["vol"], 2),
            "isToday": d == today,
            "machineState": {
                "automationLevel": 2 if st.is_paper_trading else 4,
                "executionMode": "paper" if st.is_paper_trading else "live",
                "engineStatus": "active" if st.telemetry.system.can_execute_orders else "halted",
                "activeWorkersCount": sum(1 for s in strategies_all if s["status"] == "active"),
                "daemonHealth": "HEALTHY",
            },
        })
    active_days = [d for d in days_out if d["tradesCount"] > 0 or d["pnl"] != 0]
    green = sum(1 for d in active_days if d["pnl"] > 0)
    red = sum(1 for d in active_days if d["pnl"] < 0)
    total30 = sum(d["pnl"] for d in days_out[-30:])
    wins_pnl = sum(d["pnl"] for d in days_out if d["pnl"] > 0)
    loss_pnl = abs(sum(d["pnl"] for d in days_out if d["pnl"] < 0))
    return {
        "strategyId": endpoint_id,
        "strategyName": name,
        "assetPair": pair,
        "days": days_out,
        "total30DPnL": round(total30, 2),
        "greenDays": green,
        "redDays": red,
        "flatDays": max(0, len(active_days) - green - red),
        "bestDay": max(days_out, key=lambda d: d["pnl"], default=days_out[-1]) and {
            "date": max(days_out, key=lambda d: d["pnl"])["date"],
            "formattedDate": max(days_out, key=lambda d: d["pnl"])["formattedDate"],
            "pnl": max(days_out, key=lambda d: d["pnl"])["pnl"],
        },
        "worstDay": {
            "date": min(days_out, key=lambda d: d["pnl"])["date"],
            "formattedDate": min(days_out, key=lambda d: d["pnl"])["formattedDate"],
            "pnl": min(days_out, key=lambda d: d["pnl"])["pnl"],
        },
        "winRatePercent": round(green / len(active_days) * 100.0, 1) if active_days else 0.0,
        "avgDailyPnL": round(sum(d["pnl"] for d in days_out) / max(1, len(days_out)), 4),
        "profitFactor": round(wins_pnl / loss_pnl, 4) if loss_pnl > 0 else (999.0 if wins_pnl > 0 else 0.0),
    }


# =====================================================================
# KRAKEN LEDGERS / POSITIONS / SYMBOLS
# =====================================================================
@app.get("/api/kraken/ledgers")
async def kraken_ledgers():
    st = state
    balances = _paper_balances(st)
    prices = _pair_prices(st)
    assets = []
    for asset, amt in sorted(balances.items()):
        price = prices.get(f"{asset}/USD", 1.0) if asset != "USD" else 1.0
        value = amt * price
        assets.append({
            "asset": asset,
            "name": {"USD": "US Dollar", "BTC": "Bitcoin", "ETH": "Ethereum",
                     "SOL": "Solana", "XRP": "XRP"}.get(asset, asset),
            "amount": round(amt, 6),
            "available": round(amt, 6),
            "inOrders": 0.0,
            "unitPriceUSD": round(price, 4),
            "totalValueUSD": round(value, 2),
            "portfolioPercentage": 0.0,  # unten normalisiert
            "change24h": round(st.ingestor.change24h.get(f"{asset}/USD", 0.0), 2),
            "type": "fiat" if asset == "USD" else ("stablecoin" if asset == "USDT" else "crypto"),
        })
    total = sum(a["totalValueUSD"] for a in assets) or 1.0
    for a in assets:
        a["portfolioPercentage"] = round(a["totalValueUSD"] / total * 100.0, 2)
    free_cash = balances.get("USD", 0.0)
    crypto_value = total - free_cash
    change_usd = sum(a["change24h"] / 100.0 * a["totalValueUSD"] for a in assets)
    positions = [_pro_position(p) for p in st.paper.all_positions()]
    collateral = sum(p["collateralUSD"] for p in positions)
    upnl = sum(p["unrealizedPnLUSD"] for p in positions)
    return {
        "mode": "paper" if st.is_paper_trading else "live",
        "hasCredentials": st.has_credentials,
        "lastSync": _iso(time.time()),
        "spot": {
            "totalValueUSD": round(total, 2),
            "freeCashUSD": round(free_cash, 2),
            "cryptoValueUSD": round(crypto_value, 2),
            "change24hUSD": round(change_usd, 2),
            "change24hPercent": round(change_usd / total * 100.0, 2) if total else 0.0,
            "assets": assets,
        },
        "pro": {
            "totalCollateralUSD": round(collateral, 2),
            "freeMarginUSD": round(free_cash, 2),
            "usedMarginUSD": round(collateral, 2),
            "marginLevelPercent": round(100.0 / max(0.01, (collateral / (collateral + upnl) if collateral else 0)), 1) if collateral else 0.0,
            "totalUnrealizedPnL": round(upnl, 2),
            "unrealizedPnLPercent": round(upnl / collateral * 100.0, 2) if collateral else 0.0,
            "effectiveLeverage": round(sum(p["notionalValueUSD"] for p in positions) / max(1.0, collateral), 2) if collateral else 0.0,
            "positions": positions,
        },
    }


def _pro_position(p: Dict[str, Any]) -> Dict[str, Any]:
    price = state.ingestor.last_price(p["symbol"])
    qty = float(p.get("quantity") or 0.0)
    entry = float(p.get("entry_price") or price)
    upnl = (price - entry) * qty if p["direction"] == "LONG" else (entry - price) * qty
    return {
        "id": p.get("trade_id"),
        "pair": p.get("symbol"),
        "type": "long" if p["direction"] == "LONG" else "short",
        "contractType": "perpetual",
        "size": round(qty, 6),
        "notionalValueUSD": round(qty * price, 2),
        "leverage": float(p.get("leverage") or 1.0),
        "entryPrice": round(entry, 4),
        "markPrice": round(price, 4),
        "liquidationPrice": p.get("estimated_liquidation_price"),
        "collateralUSD": round(float(p.get("margin_usd") or 0.0), 2),
        "marginRequirementUSD": round(float(p.get("margin_usd") or 0.0), 2),
        "unrealizedPnLUSD": round(upnl, 4),
        "unrealizedPnLPercent": round(upnl / max(1e-9, float(p.get("margin_usd") or 1)) * 100.0, 2),
        "fundingRate": 0.01,
        "status": "open",
    }


@app.post("/api/kraken/ledgers/sync")
async def kraken_ledgers_sync():
    state.bus.log("info", "Ledger-Sync (Paper) ausgeführt", category="SYSTEM")
    return await kraken_ledgers()


@app.post("/api/kraken/sync-balance")
async def kraken_sync_balance():
    return {"ok": True, "balances": _paper_balances(state),
            "portfolioUSD": round(_portfolio_value(state, _paper_balances(state)), 2)}


@app.get("/api/kraken/positions/pro")
async def kraken_positions_pro():
    return [ _pro_position(p) for p in state.paper.all_positions()]


@app.get("/api/kraken/symbols")
async def kraken_symbols():
    base_meta = {
        "BTC/USD": {"base": "BTC", "lotDecimals": 6, "pairDecimals": 1, "ordermin": "0.00005"},
        "ETH/USD": {"base": "ETH", "lotDecimals": 5, "pairDecimals": 2, "ordermin": "0.001"},
        "SOL/USD": {"base": "SOL", "lotDecimals": 3, "pairDecimals": 2, "ordermin": "0.1"},
        "XRP/USD": {"base": "XRP", "lotDecimals": 1, "pairDecimals": 3, "ordermin": "1"},
    }
    symbols = []
    for sym in state.config.market_symbols:
        m = base_meta.get(sym, {"base": sym.split("/")[0], "lotDecimals": 4,
                                "pairDecimals": 2, "ordermin": "0.1"})
        symbols.append({
            "symbol": sym.replace("/", ""),
            "wsname": f"XBT/USD" if sym.startswith("BTC") else sym.replace("/", "/"),
            "altname": sym,
            "base": m["base"],
            "quote": "USD",
            "status": "online",
            "lotDecimals": m["lotDecimals"],
            "pairDecimals": m["pairDecimals"],
            "costDecimals": 2,
            "ordermin": m["ordermin"],
            "costmin": "10",
            "hasLeverage": True,
            "leverageBuy": [1, 2, 3, 5],
            "leverageSell": [1, 2],
        })
    return {
        "total": len(symbols),
        "symbols": symbols,
        "quotes": ["USD", "USDT"],
        "popularSymbols": ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"],
    }


# =====================================================================
# QUEUE MATRICES
# =====================================================================
@app.get("/api/queue-matrices")
async def queue_matrices():
    st = state
    closed = st.store.trades(status="closed", limit=5000)
    out = {}
    for queue in ("paper", "live"):
        strats = [s for s in st.store.list_strategies() if s["executionMode"] == queue]
        rows = []
        q_trades = [t for t in closed if (t.get("execution_mode") or "paper") == queue]
        for s in strats:
            mine = [t for t in q_trades if t.get("strategy_id") == s["id"]]
            realized = sum(float(t.get("net_pnl_usd") or 0.0) for t in mine)
            wins = [t for t in mine if float(t.get("net_pnl_usd") or 0) > 0]
            losses = [t for t in mine if float(t.get("net_pnl_usd") or 0) <= 0]
            gw = sum(float(t["net_pnl_usd"]) for t in wins)
            gl = abs(sum(float(t["net_pnl_usd"]) for t in losses))
            equity = [0.0]
            for t in sorted(mine, key=lambda x: str(x.get("exit_time") or "")):
                equity.append(equity[-1] + float(t.get("net_pnl_usd") or 0.0))
            peak, mdd = equity[0], 0.0
            for v in equity:
                peak = max(peak, v)
                mdd = max(mdd, (peak - v) / max(1e-9, peak) if peak > 0 else 0.0)
            returns = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
            rows.append({
                "strategyId": s["id"],
                "strategyName": s["name"],
                "assetPair": s["assetPair"],
                "status": s["status"],
                "interval": s["interval"],
                "executionMode": queue,
                "realizedPnL": round(realized, 4),
                "unrealizedPnL": 0.0,
                "totalPnL": round(realized, 4),
                "totalTrades": len(mine),
                "winningTrades": len(wins),
                "losingTrades": len(losses),
                "winRate": round(len(wins) / len(mine) * 100.0, 1) if mine else 0.0,
                "volumeTradedUSD": round(sum(float(t.get("notional_usd") or 0) for t in mine), 2),
                "profitFactor": round(gw / gl, 4) if gl > 0 else (999.0 if gw > 0 else 0.0),
                "maxDrawdown": round(mdd * 100.0, 4),
                "avgTradeReturn": round(sum(float(t.get("net_pnl_usd") or 0) for t in mine) / max(1, len(mine)), 4),
                "bestTrade": round(max((float(t.get("net_pnl_usd") or 0) for t in mine), default=0.0), 2),
                "worstTrade": round(min((float(t.get("net_pnl_usd") or 0) for t in mine), default=0.0), 2),
                "trades": [_order_row(t) for t in mine[:50]],
            })
        total_realized = sum(float(t.get("net_pnl_usd") or 0) for t in q_trades)
        q_wins = [t for t in q_trades if float(t.get("net_pnl_usd") or 0) > 0]
        q_losses = [t for t in q_trades if float(t.get("net_pnl_usd") or 0) <= 0]
        gw = sum(float(t["net_pnl_usd"]) for t in q_wins)
        gl = abs(sum(float(t["net_pnl_usd"]) for t in q_losses))
        baseline = st.config.paper_baseline_usd
        equity = [baseline]
        for t in sorted(q_trades, key=lambda x: str(x.get("exit_time") or "")):
            equity.append(equity[-1] + float(t.get("net_pnl_usd") or 0.0))
        returns = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
        from app.backtest.BacktestEngine import _sharpe, _sortino

        def _cl(v):
            return round(max(-99.0, min(99.0, v)), 4)

        trajectory = []
        cum = 0.0
        for idx, t in enumerate(sorted(q_trades, key=lambda x: str(x.get("exit_time") or ""))):
            cum += float(t.get("net_pnl_usd") or 0.0)
            trajectory.append({
                "tradeIndex": idx + 1,
                "time": str(t.get("exit_time") or "")[:19].replace(" ", "T"),
                "tradePnL": round(float(t.get("net_pnl_usd") or 0.0), 4),
                "cumPnL": round(cum, 4),
                "pair": t.get("symbol"),
                "type": t.get("side") or "buy",
                "strategyName": t.get("strategy_name"),
            })
        asset_map: Dict[str, Dict[str, float]] = {}
        for t in q_trades:
            a = asset_map.setdefault(t.get("symbol") or "?",
                                     {"volume": 0.0, "n": 0, "pnl": 0.0, "w": 0})
            a["volume"] += float(t.get("notional_usd") or 0)
            a["n"] += 1
            a["pnl"] += float(t.get("net_pnl_usd") or 0)
            a["w"] += 1 if float(t.get("net_pnl_usd") or 0) > 0 else 0
        out[queue] = {
            "queue": queue,
            "queueLabel": "Level 2 — Paper Automation" if queue == "paper" else "Level 4 — Live Capital",
            "automationLevel": 2 if queue == "paper" else 4,
            "totalRealizedPnL": round(total_realized, 4),
            "totalUnrealizedPnL": 0.0,
            "totalPnL": round(total_realized, 4),
            "cumulativeReturnPercent": round(total_realized / baseline * 100.0, 4),
            "totalClosedTrades": len(q_trades),
            "totalAllTrades": len(q_trades),
            "winningTrades": len(q_wins),
            "losingTrades": len(q_losses),
            "winRate": round(len(q_wins) / len(q_trades) * 100.0, 1) if q_trades else 0.0,
            "volumeTradedUSD": round(sum(float(t.get("notional_usd") or 0) for t in q_trades), 2),
            "profitFactor": round(gw / gl, 4) if gl > 0 else (999.0 if gw > 0 else 0.0),
            "sharpeRatio": _cl(_sharpe(returns, 365 * 24)),
            "sortinoRatio": _cl(_sortino(returns, 365 * 24)),
            "maxDrawdownPercent": 0.0,
            "averageTradeReturn": round(total_realized / max(1, len(q_trades)), 4),
            "bestTradeUSD": round(max((float(t.get("net_pnl_usd") or 0) for t in q_trades), default=0.0), 2),
            "worstTradeUSD": round(min((float(t.get("net_pnl_usd") or 0) for t in q_trades), default=0.0), 2),
            "activeWorkers": sum(1 for s in strats if s["status"] == "active"),
            "strategies": rows,
            "allTimeTrades": [_order_row(t) for t in q_trades[:100]],
            "pnlTrajectory": trajectory,
            "assetBreakdown": [
                {"pair": k, "volumeUSD": round(v["volume"], 2), "tradesCount": int(v["n"]),
                 "netPnL": round(v["pnl"], 2), "winRate": round(v["w"] / v["n"] * 100.0, 1) if v["n"] else 0.0}
                for k, v in asset_map.items()
            ],
        }
    return out


# =====================================================================
# BACKTESTING
# =====================================================================
class BacktestRunBody(BaseModel):
    strategyId: Optional[str] = None
    assetPair: Optional[str] = "BTC/USD"
    interval: Optional[int] = 15
    candleCount: Optional[int] = 500
    initialBalance: Optional[float] = 10000
    feePercent: Optional[float] = 0.26
    slippagePercent: Optional[float] = 0.05
    hardStopEnabled: Optional[bool] = True
    hardStopPercent: Optional[float] = 5.0
    customParameters: Optional[Dict[str, Any]] = None
    customCode: Optional[str] = None
    pineStrategyId: Optional[str] = None
    parametersCsv: Optional[str] = None
    resultCsv: Optional[str] = None
    resultCsvPath: Optional[str] = None
    performanceCsv: Optional[str] = None


@app.post("/api/backtest/run")
async def backtest_run(body: BacktestRunBody):
    """Run backtest via TradingView MCP CSV seam (or import result CSV directly)."""
    adapter = state.tv_backtest
    if adapter is None:
        raise HTTPException(503, "TradingView MCP adapter unavailable (set SIGMA_TV_MCP_URL)")
    pair = body.assetPair or "BTC/USD"
    s = state.store.get_strategy(body.strategyId or "") if body.strategyId else None
    params = {**(s.get("parameters") or {} if s else {}), **(body.customParameters or {})}
    cfg = {
        "strategyId": body.strategyId,
        "pineStrategyId": body.pineStrategyId or body.strategyId,
        "strategyName": s.get("name") if s else "Custom",
        "assetPair": pair,
        "interval": body.interval,
        "candleCount": body.candleCount,
        "initialBalance": body.initialBalance,
        "feePercent": body.feePercent,
        "slippagePercent": body.slippagePercent,
        "hardStopEnabled": body.hardStopEnabled,
        "hardStopPercent": body.hardStopPercent,
        "customParameters": params,
        "customCode": body.customCode,
        "parametersCsv": body.parametersCsv,
        "resultCsv": body.resultCsv,
        "resultCsvPath": body.resultCsvPath,
        "performanceCsv": body.performanceCsv,
    }
    # Window candles only for metadata / GA compatibility (not local evaluation)
    count = int(body.candleCount or 500)
    candles = state.store.ohlcv(pair, 60, limit=max(count * (body.interval or 15) + 120, 300))
    candles = resample_candles(candles, max(1, body.interval or 15))[-count:]
    try:
        result = await asyncio.to_thread(adapter.run, cfg, candles)
    except TvMcpError as exc:
        raise HTTPException(502, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


@app.get("/api/backtest/mcp-status")
async def backtest_mcp_status():
    adapter = state.tv_backtest
    if adapter is None:
        return {"ok": False, "engine": None, "error": "adapter unavailable"}
    return {"ok": True, **adapter.status()}


class AiAnalyzeBody(BaseModel):
    summary: Dict[str, Any]
    equityCurve: Optional[List[Dict[str, Any]]] = None
    trades: Optional[List[Dict[str, Any]]] = None


@app.post("/api/backtest/ai-analyze")
async def backtest_ai_analyze(body: AiAnalyzeBody):
    from app.backtest.BacktestEngine import _ai_analysis

    return _ai_analysis(body.summary, body.equityCurve or [], body.trades or [])


# =====================================================================
# GENETIC OPTIMIZER
# =====================================================================
class GeneticRunBody(BaseModel):
    populationSize: Optional[int] = 30
    maxGenerations: Optional[int] = 50
    survivorsCount: Optional[int] = 3
    mutationRate: Optional[float] = 0.18
    crossoverRate: Optional[float] = 0.80
    walkForwardSplitPercent: Optional[float] = 70
    assetPair: Optional[str] = "BTC/USD"
    interval: Optional[int] = 15
    candleCount: Optional[int] = 500
    initialBalance: Optional[float] = 10000
    feePercent: Optional[float] = 0.26
    slippagePercent: Optional[float] = 0.05
    baselineStrategyId: Optional[str] = None
    baselineStrategyName: Optional[str] = None
    seedGenes: Optional[Dict[str, Any]] = None


@app.post("/api/genetic/run")
async def genetic_run(body: GeneticRunBody):
    pair = body.assetPair or "BTC/USD"
    count = int(body.candleCount or 500)
    candles = state.store.ohlcv(pair, 60, limit=max(count * (body.interval or 15) + 120, 300))
    candles = resample_candles(candles, max(1, body.interval or 15))[-count:]
    if len(candles) < 240:
        raise HTTPException(400, f"WFO benötigt ≥240 Candles — vorhanden: {len(candles)}.")
    state.bus.log("info",
                  f"GA-Run gestartet: {pair} {body.interval}m, Pop={body.populationSize}, "
                  f"Gens={body.maxGenerations}, WFO={body.walkForwardSplitPercent}%",
                  category="GA")
    try:
        result = await asyncio.to_thread(state.ga.run, body.model_dump(exclude_none=True), candles)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # Beste Genome persistieren
    best = result["bestIndividual"]
    state.store.upsert_genome({
        "genome_id": best["id"],
        "strategy_id": body.baselineStrategyId,
        "asset_pair": pair,
        "interval_min": body.interval,
        "genes": best["genes"],
        "generation": best.get("generation"),
        "fitness": best.get("fitness"),
        "dsr": best.get("dsr"),
        "cadence_per_day": best.get("tradesPerDay"),
        "in_sample_summary": best.get("inSampleSummary"),
        "oos_sample_summary": best.get("outOfSampleSummary"),
    })
    state.bus.log("info",
                  f"GA-Run fertig: best fitness={best.get('fitness')} DSR={best.get('dsr')} "
                  f"Gate={'PASS' if result['shadowGate']['passed'] else 'FAIL'}",
                  category="GA")
    return result


class DeployBody(BaseModel):
    individual: Dict[str, Any]
    assetPair: Optional[str] = "BTC/USD"
    interval: Optional[int] = 15
    strategyName: Optional[str] = None
    autoActivate: Optional[bool] = True
    baselineStrategyId: Optional[str] = None


@app.post("/api/genetic/deploy-to-orchestrator")
async def genetic_deploy(body: DeployBody):
    ind = body.individual or {}
    genes = ind.get("genes") or {}
    from app.optimizer.GeneticOptimizer import genes_to_params

    params = genes_to_params(genes)
    baseline = state.store.get_strategy(body.baselineStrategyId or "") if body.baselineStrategyId else None
    next_version = (baseline.get("version") or 1) + 1 if baseline else 1
    import re as _re

    base_name = _re.sub(r" \(v\d+\)$", "", (baseline or {}).get("name") or "Evolved Genome")
    name = body.strategyName or f"{base_name} (v{next_version})"
    sid = f"EVOLVED_{uuid.uuid4().hex[:10]}"
    strategy = {
        "id": sid,
        "name": name,
        "description": f"GA-Evolved (Gen {ind.get('generation')}, fitness {ind.get('fitness')}), DSR {ind.get('dsr')}",
        "code": ind.get("generatedCode") or "// evolved genome (auto-deploy)",
        "status": "active" if body.autoActivate else "inactive",
        "assetPair": body.assetPair,
        "interval": body.interval,
        "executionMode": "paper",
        "parameters": params,
        "hardStopEnabled": True,
        "hardStopPercent": params.get("hardStopPercent", 4.0),
        "createdAt": _iso(time.time()),
        "version": next_version,
        "seededFromId": baseline["id"] if baseline else None,
        "seededFromName": baseline["name"] if baseline else None,
        "evolutionGeneration": ind.get("generation"),
        "evolutionFitness": ind.get("fitness"),
        "lastGaRecalibrationTs": _iso(time.time()),
    }
    state.store.upsert_strategy(strategy)
    await state.m8.register_strategy(sid,
                               last_ga_recalibration_ts=time.time())
    if baseline:
        archived = {**baseline, "status": "archived", "archivedAt": _iso(time.time())}
        state.store.upsert_strategy(archived)
    state.bus.log("info",
                  f"🧬 GA-DEPLOY: {name} (v{next_version}) aus Gen {ind.get('generation')}",
                  category="GA", strategy_id=sid)
    return {
        "strategy": state.store.get_strategy(sid),
        "archivedStrategy": baseline if baseline else None,
    }


# =====================================================================
# AUTH (Passkey) & SETTINGS
# =====================================================================
@app.get("/api/v1/auth/passkey/challenge")
async def passkey_challenge(email: str = "master@alpha.local"):
    return await state.passkey.create_challenge(email)


class PasskeyVerifyBody(BaseModel):
    email: str
    credential: Dict[str, Any]


@app.post("/api/v1/auth/passkey/verify")
async def passkey_verify(body: PasskeyVerifyBody):
    return await state.passkey.verify_assertion(body.email, body.credential)


def _kraken_credentials_present() -> bool:
    key = os.environ.get("KRAKEN_API_KEY", "").strip()
    secret = os.environ.get("KRAKEN_API_SECRET", "").strip()
    return bool(key and secret)


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _settings_unlocked(request: Request, token: Optional[str]) -> bool:
    if _is_loopback(request):
        return True
    return state.passkey.validate_settings_token(token) is not None


@app.get("/api/settings")
async def settings_get():
    return {
        "settings": state.settings.get_all(),
        "hasCredentials": _kraken_credentials_present(),
        "loopbackUnlocked": True,
    }


class SettingsUpdateBody(BaseModel):
    settingsToken: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None


@app.put("/api/settings")
async def settings_update(request: Request, body: SettingsUpdateBody):
    if not _settings_unlocked(request, body.settingsToken):
        raise HTTPException(403, "PASSKEY GATE: gültiges settingsToken erforderlich.")
    if not body.key or body.value is None:
        raise HTTPException(400, "key & value erforderlich.")
    try:
        out = state.settings.update(body.key, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    state.has_credentials = _kraken_credentials_present()
    out["hasCredentials"] = state.has_credentials
    return out


@app.delete("/api/settings/{key}")
async def settings_delete(request: Request, key: str, token: Optional[str] = None):
    if not _settings_unlocked(request, token):
        raise HTTPException(403, "PASSKEY GATE: gültiges settingsToken erforderlich.")
    try:
        out = state.settings.delete(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    state.has_credentials = _kraken_credentials_present()
    out["hasCredentials"] = state.has_credentials
    return out


# =====================================================================
# MCP BRIDGE
# =====================================================================
@app.get("/api/mcp/tools")
async def mcp_tools():
    return state.mcp.list_tools()


class McpInvokeBody(BaseModel):
    tool: str
    args: Optional[Dict[str, Any]] = None
    settingsToken: Optional[str] = None


@app.post("/api/mcp/invoke")
async def mcp_invoke(body: McpInvokeBody):
    return state.mcp.execute(body.tool, body.args or {}, body.settingsToken)


# =====================================================================
# AI COPILOT (regelbasiert — [MOCK-SEAM] für echten LLM)
# =====================================================================
class AiSuggestBody(BaseModel):
    prompt: str


@app.post("/api/ai/suggest")
async def ai_suggest(body: AiSuggestBody):
    prompt = body.prompt.lower()
    # [MOCK-SEAM] Regex-Archetyp-Resolver — echte LLM-Anbindung hier anbinden.
    if "rsi" in prompt or "reversion" in prompt or "mean" in prompt:
        archetype, pair, interval = "rsi_reversion", "BTC/USD", 15
        params = {"archetype": "rsi_reversion", "rsiPeriod": 14, "rsiLower": 33,
                  "rsiUpper": 67, "hardStopPercent": 4.0}
        desc = "RSI-Mean-Reversion mit 33/67-Gates und 4% Hard-Stop."
    elif "momentum" in prompt or "trend" in prompt or "ema" in prompt:
        archetype, pair, interval = "ema_trend", "ETH/USD", 15
        params = {"archetype": "ema_trend", "trendFastEma": 12, "trendSlowEma": 60,
                  "hardStopPercent": 5.0}
        desc = "EMA 12/60 Trend-Following mit 1.5x-ATR-Stop."
    else:
        archetype, pair, interval = "sma_cross", "BTC/USD", 15
        params = {"archetype": "sma_cross", "smaFast": 12, "smaSlow": 48,
                  "hardStopPercent": 4.5}
        desc = "SMA 12/48 Golden- & Death-Cross mit ATR-gestopptem TP."
    if "eth" in prompt:
        pair = "ETH/USD"
    elif "sol" in prompt:
        pair = "SOL/USD"
    elif "xrp" in prompt:
        pair = "XRP/USD"
    for tf, name in ((5, "5m"), (15, "15m"), (30, "30m"), (60, "1h"), (240, "4h")):
        if name in prompt:
            interval = tf
    code = f"""// AI-generated strategy ({archetype})
function onCandle(ctx) {{
  // {desc}
  const entry = ctx.close;
  const stop = entry * (1 - {params['hardStopPercent']}/100);
  const tp = entry * (1 + {params['hardStopPercent']*2.2/100});
  return {{ direction: 'LONG', stop, tp }};
}}"""
    return {
        "name": f"AI {archetype.replace('_', ' ').title()} {pair} {interval}m",
        "description": desc,
        "assetPair": pair,
        "interval": interval,
        "parameters": params,
        "code": code,
        "tweaksApplied": ["Archetyp-Erkennung", "Hard-Stop kalibriert"],
        "reasoning": f"Prompt '{body.prompt[:80]}' → {archetype}-Archetyp mit konservativer Risikokalibrierung.",
        "expectedImprovement": "+0.2 PF im Backtest-Referenzfenster (Schätzung)",
    }


class AiDebugBody(BaseModel):
    code: str
    name: Optional[str] = None


@app.post("/api/ai/debug")
async def ai_debug(body: AiDebugBody):
    code = body.code or ""
    issues = []
    smells = []
    risk = 10.0
    if "eval(" in code:
        issues.append("⛔ eval() — Code-Injection-Risiko (Noir Blast-Radius Gate L4).")
        risk += 30
    if "await" in code and "catch" not in code:
        issues.append("⚠️ await ohne try/catch — unhandelte Rejects möglich.")
        risk += 10
    if "stop" not in code.lower() and "stoploss" not in code.lower():
        issues.append("⚠️ Kein Stop-Loss definiert — M8-Gate 4 wird hart abgewiesen.")
        risk += 20
    if "volume" not in code.lower() and "rsi" not in code.lower() and "sma" not in code.lower():
        smells.append("Kein Indikator erkennbar — möglicher Noise-Trader.")
    status = "error" if risk > 50 else ("warning" if issues else "clean")
    return {
        "status": status,
        "summary": f"Creffektivitäts-Score {max(0, 8 - len(issues))}/8 — "
                   f"{'Blast-Radius-Gate bestanden' if not issues else 'Blast-Radius-Gate aktiv'}.",
        "issues": issues,
        "recommendations": "; ".join(issues) or "Keine Kritika — Deployment-fähig.",
        "riskScore": round(risk, 1),
        "codeSmells": smells,
    }


class AiTweakBody(BaseModel):
    strategy: Dict[str, Any]
    auditReport: Optional[Dict[str, Any]] = None
    customInstruction: Optional[str] = None


@app.post("/api/ai/tweak")
async def ai_tweak(body: AiTweakBody):
    s = body.strategy or {}
    params = dict(s.get("parameters") or {})
    tweaks = []
    report = body.auditReport or {}
    risk = float(report.get("riskScore") or 0.0)
    if risk > 30:
        params["hardStopPercent"] = max(2.0, float(params.get("hardStopPercent", 5.0)) - 1.0)
        tweaks.append("Hard-Stop um 1.0pp verschärft (Risiko-Score >30).")
    if "stop" in str(report.get("issues", "")).lower():
        params.setdefault("hardStopEnabled", True)
        tweaks.append("Hard-Stop-Pflicht aktiviert.")
    if params.get("archetype") == "sma_cross" and risk > 15:
        params["smaSlow"] = int(params.get("smaSlow", 48)) + 6
        tweaks.append("Slow-SMA +6 Bars — weniger Churn in Range.")
    if not tweaks:
        tweaks.append("Parameter unverändert — Audit im grünen Bereich.")
    if body.customInstruction:
        tweaks.append(f"Anweisung berücksichtigt: {body.customInstruction[:120]}")
    return {
        "name": s.get("name") or "Tweaked",
        "description": (s.get("description") or "") + " [AI-tweaked]",
        "assetPair": s.get("assetPair"),
        "interval": s.get("interval", 15),
        "parameters": params,
        "code": s.get("code") or "",
        "tweaksApplied": tweaks,
        "reasoning": "Regelbasierte Tweaks gemäß Noir-Audit + M8-Risikobudget.",
        "expectedImprovement": "Reduzierter Churn & engerer Drawdown-Korridor",
    }


@app.get("/api/ai/manifest-learn")
async def ai_manifest_learn():
    strategies = state.store.list_strategies()
    closed = state.store.trades(status="closed", limit=2000)
    by_arch: Dict[str, List[float]] = {}
    for s in strategies:
        arch = str((s.get("parameters") or {}).get("archetype") or "unknown")
        by_arch.setdefault(arch, [])
    for t in closed:
        s = next((x for x in strategies if x["id"] == t.get("strategy_id")), None)
        if s:
            by_arch[str((s.get("parameters") or {}).get("archetype") or "unknown")].append(
                float(t.get("net_pnl_usd") or 0.0))
    insights = [
        f"{arch}: {len(pnls)} Trades, Σ {sum(pnls):+.2f} USD, "
        f"WinRate {sum(1 for p in pnls if p > 0) / max(1, len(pnls)) * 100:.0f}%"
        for arch, pnls in by_arch.items()
    ]
    return {
        "manifestId": f"learn-{uuid.uuid4().hex[:8]}",
        "insights": insights or ["Noch keine geschlossenen Trades zur Analyse."],
        "topPerformer": max(strategies, key=lambda s: next(
            (float(t.get("net_pnl_usd") or 0) for t in closed
             if t.get("strategy_id") == s["id"]), -1))["name"] if strategies else None,
        "model": "statistical-manifest-learner (no-LLM)",
    }


from app.server.routes_quant import router as quant_router  # noqa: E402
from app.server.routes_sigma import router as sigma_router  # noqa: E402

from app.core.error_engine import install_error_handlers  # §36
install_error_handlers(app)        # Unified Error Taxonomy — kein nacktes 500

app.include_router(quant_router)
app.include_router(sigma_router)   # Blueprint L4: Loop A-E Routen (§7)

from app.server.routes_logs import router as logs_router  # §37
app.include_router(logs_router)    # Live Process & AI Log Console


# =====================================================================
# SSE TELEMETRY STREAM (M-17)
# =====================================================================
@app.get("/api/quant/telemetry/stream")
async def telemetry_stream(request: Request):
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            frame = state.telemetry.build_frame(store=state.store, log_bus=state.bus)
            autopsies = state.bus.recent_logs_list(50)
            yield f"event: telemetry\ndata: {json.dumps(frame, default=str)}\n\n"
            await asyncio.sleep(state.config.sse_interval_seconds)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
