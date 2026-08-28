"""
=========================================================
Datei:      app/core/blueprint.py
Zweck:      HARD-CODED CANONICAL SPEC — Projekt:Sigma L4
            Maschinenlesbare Fassung von:
              * docs/BLUEPRINT-SIGMA.md  (Spec Freeze v3.0, 5-Loop A-E)
              * docs/MASTERPROMPT.md     (Ciel Core Matrix 3.0)
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================

Dieses Modul ist die **einzige** Stelle, an der Blueprint-Werte im Code
festverdrahtet sind. Jede Engine (Loop A-E), die Ports, Pfade, Limits,
Schwellen, Badges, Panels oder Routen braucht, importiert sie von hier —
niemals als Magic Number im Callsite-Code.

Invariante (getestet in tests/test_blueprint_spec.py):
    blueprint.py == docs/BLUEPRINT-SIGMA.md == config/autonomy-level-4.yaml

Alle Container sind eingefroren (tuple / MappingProxyType / frozen dataclass),
damit Runtime-Code die Spec nicht versehentlich mutieren kann.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

# =============================================================================
# 0. Identität / Freeze-Header
# =============================================================================

# Implementierter Stand (dieses Modul ist die Wahrheit fuer den Code).
BLUEPRINT_VERSION = "3.0"
BLUEPRINT_STATUS = "Canonical Spec Freeze v3.0 (5-Loop A-E, Virtual Bots, FlexLayout Terminal, Reward/ONNX/Telegram)"
MASTERPROMPT_VERSION = "3.0.0-SIGMA-RELEASE"

# Stand der eingefrorenen Dokumente in docs/. Liegt DOCS_* vor BLUEPRINT_VERSION,
# sind die in DOCS_PENDING_SECTIONS gelisteten Kapitel noch nicht implementiert.
DOCS_BLUEPRINT_VERSION = "3.6"
DOCS_MASTERPROMPT_VERSION = "3.6.0-SIGMA-RELEASE"
DOCS_SECTION_RANGE = (1, 38)
DOCS_PENDING_SECTIONS: Tuple[str, ...] = ()   # v3.6 vollstaendig hart verdrahtet
# §23-§30 sind als Konstanten + Laufzeitmodule implementiert:
#   §23 app/core/exchange_clock.py + app/core/scheduler_matrix.py
#   §24 app/quant/glint_orderbook_verifier.py
#   §25 app/execution/reliable_order_dispatcher.py
#   §26 app/core/rate_limiter.py
#   §27 app/quant/epidemic_contagion_engine.py
#   §28 app/execution/capital_flywheel_engine.py
#   §29 app/execution/fixed_leverage.py
#   §30 TERMINAL_PANELS_EXTENDED / TERMINAL_PRESETS_EXTENDED
#   §31 app/services/strategy_lifecycle_service.py
#   §32 app/execution/kraken_paper_engine.py + KrakenCliBridge (Dual-Mode)
#   §36 app/core/error_engine.py (Taxonomy, Handler, Diagnostics Desk)
#   §37 app/server/routes_logs.py + src/pages/ProcessLogView.tsx
#   §34 app/llm/schemas_llm.py + app/llm/tool_executor.py
#   §38 app/services/netron_server.py + deploy/systemd/sigma-netron.service
#   §33 app/server/schemas.py + POST /api/v1/signal/ingest
#   §35 app/optimizer/exact_csv_serializer.py
AUTONOMY_LEVEL = 4
AUTONOMY_LABEL = "L4 — High Operational Autonomy"
LINEAGE = "Fork von Alpha M8 Blueprint v1.2.0 / Skeleton v1.6.4"
CANONICAL_SPEC_PATH = "docs/BLUEPRINT-SIGMA.md"
MASTERPROMPT_PATH = "docs/MASTERPROMPT.md"
REPO_URL = "https://github.com/Finnlayy/Sigma"

HOST_OS = "ubuntu"
INSTALL_ROOT = "/opt/sigma"
SERVICE_USER = "sigma"
RUNTIME = "Python 3.12 + FastAPI + Playwright"
REGULATORY_REGION = "DE_BAFIN"

PRODUCT_TAGLINE = "Privates Pionex auf Steroiden"

# --- §0.1 Grundsatz: Strategy ≡ TradingView ---------------------------------
AXIOM_STRATEGY_IS_TRADINGVIEW = True
AXIOM_VIRTUAL_BOT_OVER_KRAKEN_CLI = True
AXIOM_SELF_HEALING_CLOSED_LOOP = True

AXIOMS: Tuple[str, ...] = (
    "Axiom 1: Strategy = TradingView (Pine v6 ist Single Source of Truth)",
    "Axiom 2: Pionex Virtual-Bot Prinzip, ausgefuehrt ueber Kraken CLI (BaFin/MiCA)",
    "Axiom 3: Autonomes Self-Healing & Closed-Loop (YAML-Resolver, Kausale Autopsie)",
)

# Was Sigma NICHT ist (§0) — als Guard-Liste für Audits/Tests
REJECTED_ARTIFACTS: Tuple[str, ...] = (
    "StrategyInterpreter-Archetypen als Live-Pfad",
    "BacktestEngine.run_backtest in Prod",
    "Yahoo-MCP als TV-Strategy",
    "Manuelles TV-Klicken als Betriebsvoraussetzung",
    "Windows-TS-Portal",
    "Pionex Live-Futures in DE",
    "atilaahmettaner MCP als TV Strategy Tester",
    "Manueller CSV-Primaerpfad",
    "Full shadcn-Migration in v1",
    "Stummer Fallback auf lokale BacktestEngine in Prod",
)

# Primordiale Subagenten (Masterprompt §0)
PRIMORDIALS: Mapping[str, str] = MappingProxyType({
    "Rouge": "Strategische Dekomposition, Architektur-Design, Portfolio-Allokation",
    "Noir": "Qualitaets-Gate, Risikomanagement, Blast-Radius-Audit, Validierung",
    "Blanche": "Datenabfrage, Schema-Standardisierung, Feature-Extraktion, RAG",
    "Jaune": "Code-Generierung, Mathematik, AST-Synthese, System-Integration",
})
NOIR_MIN_CREFFEKTIVITAET_SCORE = 6  # von 8

# =============================================================================
# 1. Prozesse, Ports, Verzeichnisse (§1)
# =============================================================================

PORT_CORE = 8000
PORT_SCRAPER = 8001
PORT_REDIS = 6379
PORT_UI_DEV = 3000
PORT_OLLAMA = 11434


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    entry: str
    port: int | None
    workdir: str


PROCESSES: Tuple[ProcessSpec, ...] = (
    ProcessSpec("sigma-core", "uvicorn app.server.main:app", PORT_CORE, INSTALL_ROOT),
    ProcessSpec("sigma-tv-scraper", "uvicorn api.main:app", PORT_SCRAPER, "vendor/tradingview-scraper"),
    ProcessSpec("sigma-tv-worker", "python -m app.tv.worker", None, INSTALL_ROOT),
    ProcessSpec("sigma-redis", "redis-server (AOF)", PORT_REDIS, "system"),
    ProcessSpec("sigma-ui", "vite", PORT_UI_DEV, INSTALL_ROOT),
)

# --- Kanonische Datenpfade (relativ zu SIGMA_DATA_DIR bzw. Repo-Root) -------
DATA_DIR = "./data"
PATH_TV_STORAGE_STATE = "./data/secrets/tv_storage_state.json"
PATH_TV_CHROME_PROFILE = "./data/secrets/tv_chrome_profile"
PATH_TV_EXPORTS = "./data/tv_exports"          # /{job_id}/
PATH_TV_JOBS = "./data/tv_jobs"                # /{job_id}.json
PATH_KILL_SWITCH = "./data/signals/KILL_SWITCH"
PATH_PAUSE = "./data/signals/PAUSE"
PATH_LOG_CORE = "./data/logs/sigma_core.log"
PATH_LOG_ORDERS = "./data/logs/orders.jsonl"
PATH_LOG_TV_WORKER = "./data/logs/tv_worker.log"
PATH_STRATEGIES = "./data/strategies"          # /{id}/parameters.csv, backtests/
PATH_DUCKDB = "./data/duckdb"
PATH_PARQUET = "./data/parquet"
PATH_SELECTORS_YAML = "./app/tv/selectors.yaml"
PATH_L4_CONFIG = "config/autonomy-level-4.yaml"
PATH_ONNX_REGIME = "./models/regime_classifier.onnx"
PATH_ONNX_ALLOCATOR = "models/strategy_allocator.onnx"

# §8.4 Persistenz-Konventionen je Strategie
STRATEGY_FILE_LAYOUT: Mapping[str, str] = MappingProxyType({
    "parameters": "./data/strategies/{id}/parameters.csv",
    "parameters_baseline": "./data/strategies/{id}/parameters_baseline.csv",
    "parameters_optimized": "./data/strategies/{id}/parameters_optimized.csv",
    "backtest_trades": "./data/strategies/{id}/backtests/{bid}_trades.csv",
    "backtest_performance": "./data/strategies/{id}/backtests/{bid}_performance.csv",
    "backtest_meta": "./data/strategies/{id}/backtests/{bid}_meta.json",
})

# =============================================================================
# 2. Fünf Loops A-E (§2)
# =============================================================================


class Loop(str, Enum):
    A_LIVE = "A"
    B_OPTIMIZATION = "B"
    C_FEED = "C"
    D_SCOUT = "D"
    E_ACADEMY = "E"


@dataclass(frozen=True)
class LoopSpec:
    loop: Loop
    title: str
    trigger: str
    output: str
    autonomy: str


LOOPS: Mapping[Loop, LoopSpec] = MappingProxyType({
    Loop.A_LIVE: LoopSpec(
        Loop.A_LIVE, "Live Execution & Risk Engine",
        "TV Alert HTTP POST", "Order / Paper Fill + Autopsy", "Vollautomatisch"),
    Loop.B_OPTIMIZATION: LoopSpec(
        Loop.B_OPTIMIZATION, "Backtest & Genetic Optimization",
        "UI Run / GA", "BacktestResult + ShadowGate", "Vollautomatisch (Playwright)"),
    Loop.C_FEED: LoopSpec(
        Loop.C_FEED, "Market Data Feed & Regime Radar",
        "Poll / On-demand", "OHLCV + Regime-Vektor", "Vollautomatisch"),
    Loop.D_SCOUT: LoopSpec(
        Loop.D_SCOUT, "Scout & Incubator (Paper)",
        "Cron / Queue", "Paper-Trades -> Academy", "Vollautomatisch"),
    Loop.E_ACADEMY: LoopSpec(
        Loop.E_ACADEMY, "Academy, Kausale Autopsie & Self-Heal",
        "Trade Close / Cron", "Badges, Reward, Allocator, Self-Heal, RAM", "Vollautomatisch"),
})

# =============================================================================
# 3. Modul-Inventar (§3) — Pfad-Kontrakt
# =============================================================================

MODULES_KEEP: Mapping[str, str] = MappingProxyType({
    "app/execution/M8StateEngine.py": "ACTIVE/THROTTLED/QUARANTINED/RETIRED; Redis m8:state:{id}",
    "app/execution/JudgeEngine.py": "8 Gates vor Order",
    "app/execution/PaperExecutionEngine.py": "Shadow bis LIVE_APPROVED",
    "app/execution/VaultEngine.py": "Profit Sweep",
    "app/execution/AutopsyProcessor.py": "5 Zonen",
    "app/execution/EodProfitFactorEngine.py": "3/7-Tage-Gates",
    "app/core/telemetry.py": "SHADOW_ACTIVE / LIVE_APPROVED / EMERGENCY_HALT",
    "app/core/duckdb_store.py": "Persistenz",
    "app/optimizer/GeneticOptimizer.py": "Orchestrierung behalten; Eval ersetzen",
    "app/optimizer/AcademyRegistry.py": "Loop E: Profiling/Badges",
    "src/types.ts": "BacktestResult, TradingStrategy, Gene",
    "bin/m8-ctl": "CLI / sigma-ctl Alias",
})

MODULES_NEW: Mapping[str, str] = MappingProxyType({
    "app/tv/scraper_client.py": "HTTP -> :8001",
    "app/tv/symbol_map.py": "BTC/USD -> KRAKEN:BTCUSD",
    "app/tv/interval_map.py": "15 -> 15m",
    "app/tv/strategy_tester_driver.py": "Playwright E2E (export/apply params, run backtest)",
    "app/tv/selectors.yaml": "DOM-Fallbacks",
    "app/tv/selector_manager.py": "Self-Healing: lokal -> remote -> builtin",
    "app/tv/alert_provisioner.py": "TV Alert upsert/enable/disable; M8-gekoppelt",
    "app/tv/worker.py": "Redis/File-Queue Consumer",
    "app/tv/yaml_resolver.py": "Self-Heal selectors/param_bounds",
    "app/backtest/tv_csv.py": "Params/Trades/Perf -> BacktestResult",
    "app/backtest/TvMcpBacktest.py": "TvBacktestService: Queue + Cache",
    "app/quant/onnx_kelly.py": "ONNX + Half-Kelly",
    "app/quant/regime_detector.py": "EMA-Delta, ATR-Perzentile, Hurst",
    "app/quant/self_optimizing_onnx.py": "Brier, Temperature, Hot-Reload",
    "app/optimizer/StrategyAllocator.py": "Badge + Regime -> Alert an/aus",
    "app/optimizer/strategy_scorecard.py": "Strategie-Ampel, Slots, Stage-1 Initialize",
    "app/optimizer/reward_shaping.py": "XP/Strike -> M8 Multiplier / Quarantaene",
    "app/optimizer/gene_schema.py": "Parameter-CSV -> GeneSchema",
    "app/execution/VirtualBotEngine.py": "Budget-Ringfence, Sizing, Max-Loss, Sweep",
    "app/execution/KrakenCliBridge.py": "kraken trade add-order Subprocess",
    "app/execution/SafetyGuard.py": "KILL_SWITCH / PAUSE / daily loss / errors",
    "app/execution/deadman_switch_daemon.py": "Heartbeat; Limit-Cancel",
    "app/core/memory_watchdog.py": "4-Stufen RAM Guard; RSS-Budget; GC immer",
    "app/services/telegram_bot_operator.py": "Bidirektional LLM + Fast-Path /kill",
    "app/scout/ScoutDaemon.py": "Loop D Paper Pairing",
    "src/components/SigmaTerminal.tsx": "FlexLayout 11-Panel Workspace",
    "bin/sigma-tv-login": "Speichert tv_storage_state.json",
})

MODULES_DROP: Mapping[str, str] = MappingProxyType({
    "stacks/windows/**": "Weg",
    "StrategyInterpreter live path": "Streichen — Strategien nur ueber TV/Pine",
    "Alpha Factory Archetyp-Seeds (sma_cross, rsi_reversion)": "Ersetzen durch Pine-Templates",
    "genes_to_params (lokale Archetypen)": "Ersetzen durch genes_to_pine_inputs",
    "BacktestEngine.run_backtest (Prod)": "Nur Tests/Fake",
    "KrakenMCPBridge (149 Mock-Tools als Exchange)": "Ersetzen durch KrakenCliBridge",
    "ALPHA_* Env": "-> SIGMA_*",
    "Yahoo MCP Primaer-Backtest": "Kein Primaerknoten",
    "Firebase als Core-Dependency": "Optional/ignorieren",
})

# =============================================================================
# 4. Loop A — Live Execution (§4, §17)
# =============================================================================

WEBHOOK_ROUTE = "/api/v1/signal/webhook"
WEBHOOK_SECRET_ENV = "SIGMA_WEBHOOK_SECRET"
WEBHOOK_SECRET_HEADER = "X-Sigma-Webhook-Secret"
WEBHOOK_UNAUTHORIZED_STATUS = 401
WEBHOOK_BLOCKED_STATUS = 503  # KILL_SWITCH / PAUSE

PINE_ALERT_ACTIONS: Tuple[str, ...] = ("BUY", "SELL", "CLOSE")
PINE_ALERT_FIELDS: Tuple[str, ...] = (
    "symbol", "action", "price", "rsi", "atr", "cisd_score", "timestamp", "strategy_id", "secret",
)

# §17.2 Timestamp-Normalisierung
TIMESTAMP_MS_THRESHOLD = 1e11
SIGNAL_STALE_MIN_SECONDS = 120  # max(2 * interval_seconds, 120)
SIGNAL_STALE_INTERVAL_FACTOR = 2

# Feste Pipeline-Reihenfolge (§4.2) — Reihenfolge ist normativ
LOOP_A_PIPELINE: Tuple[str, ...] = (
    "SafetyGuard.check",                 # 1 KILL_SWITCH / PAUSE -> 503
    "risk_guard.daily_pnl_and_errors",   # 2 DuckDB/Redis
    "QuantEngine.predict_confidence",    # 3 ONNX oder Heuristik
    "calculate_kelly",                   # 4 Half-Kelly + Cap
    "brackets_from_atr",                 # 5 sl=atr*1.5 tp=atr*3.0
    "symbol_map_to_kraken_pair",         # 6 Spot vs Futures
    "JudgeEngine.evaluate",              # 7 8 Gates
    "execute_kraken_cli_or_paper",       # 8 LIVE_APPROVED + SIGMA_LIVE_TRADING
    "append_orders_jsonl",               # 9
    "m8.update_post_trade_state",        # 10
)

# §4.2/4.4 Risk-Konstanten
KELLY_FRACTION = 0.5                      # Half-Kelly
KELLY_DEFAULT_RRR = 2.0
MAX_PORTFOLIO_RISK_PER_TRADE = 0.10
ATR_STOP_MULTIPLIER = 1.5
ATR_TAKE_PROFIT_MULTIPLIER = 3.0

RISK_GUARD: Mapping[str, float] = MappingProxyType({
    "max_open_positions": 4,
    "max_daily_loss_usd": 600.0,
    "max_consecutive_errors": 3,
    "max_spread_bps": 45.0,
    "min_spot_balance_usd": 250.0,
    "min_futures_balance_usd": 500.0,
    "kelly_fraction": KELLY_FRACTION,
    "max_portfolio_risk_per_trade": MAX_PORTFOLIO_RISK_PER_TRADE,
})

SYMBOL_HALT_KEY = "halt:symbol:{symbol}"
SYMBOL_HALT_TTL_SECONDS = 300

# §4.3 / §20 Kraken CLI
KRAKEN_CLI_BINARY = "kraken"
KRAKEN_ADD_ORDER_ARGV: Tuple[str, ...] = (
    "kraken", "trade", "add-order",
    "--pair={pair}", "--type={side}", "--ordertype={ordertype}", "--volume={volume}",
)
KRAKEN_BRACKET_ARGV: Tuple[str, ...] = (
    "--close-ordertype=stop-loss", "--close-price={stop_price}",
)
# §17.3 Fehler-Parsing: Text schlägt Exit-Code
KRAKEN_ERROR_MARKERS: Tuple[str, ...] = ("EOrder:", "EGeneral:", "EAPI:")

# §4.6 M8 -> Alert / Webhook-Annahme / Size-Matrix (normativ)


class M8State(str, Enum):
    ACTIVE = "ACTIVE"
    THROTTLED = "THROTTLED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class AlertAction(str, Enum):
    ENABLE = "enable"
    KEEP = "keep"
    DISABLE = "disable"


@dataclass(frozen=True)
class M8AlertPolicy:
    alert: AlertAction
    accept_webhook: bool
    budget_multiplier: float
    note: str


M8_ALERT_MATRIX: Mapping[M8State, M8AlertPolicy] = MappingProxyType({
    M8State.ACTIVE: M8AlertPolicy(AlertAction.ENABLE, True, 1.0, "normal (falls Runner running)"),
    M8State.THROTTLED: M8AlertPolicy(AlertAction.KEEP, True, 0.5, "Alert bleibt an, Size x0.5"),
    M8State.QUARANTINED: M8AlertPolicy(AlertAction.DISABLE, False, 0.0, "keine neuen Entries"),
    M8State.RETIRED: M8AlertPolicy(AlertAction.DISABLE, False, 0.0, "terminal"),
})

# Ereignisse, die den Alert NICHT verändern (§4.6): erfolgreicher Entry/Exit/Autopsy
ALERT_UNCHANGED_ON_EVENTS: Tuple[str, ...] = ("entry_filled", "exit_filled", "autopsy_done", "pause_file")
ALERT_DISABLE_ON_EVENTS: Tuple[str, ...] = ("ui_stop", "m8_quarantined", "m8_retired", "kill_switch")
ALERT_NAME_TEMPLATE = "sigma:{strategy_id}"

# =============================================================================
# 5. Loop B — Backtest / GA (§5, §17.4)
# =============================================================================

TV_MAX_CONCURRENCY = 1                  # Playwright serialisiert
TV_NAVIGATION_TIMEOUT_MS = 120_000
TV_TESTER_RUN_TIMEOUT_MS = 180_000
TV_JOB_TOTAL_TIMEOUT_MS = 600_000
TV_BASE_URL = "https://www.tradingview.com"
TV_LOGIN_URL = "https://www.tradingview.com/#signin"
TV_CHART_URL = "https://www.tradingview.com/chart/"

GA_MAX_POPULATION = 15
GA_MAX_GENERATIONS = 5
GA_EARLY_STOP_STALL_GENERATIONS = 3
GA_PARAM_CACHE_REQUIRED = True
GA_CONCURRENCY = TV_MAX_CONCURRENCY

DSR_SHADOW_GATE = 0.95
MIN_TRADES_FOR_GATE = 30

# =============================================================================
# 6. Loop C — Scraper Feed (§6)
# =============================================================================

SCRAPER_BASE_URL = "http://127.0.0.1:8001"
SCRAPER_TIMEOUT_S = 30
SCRAPER_ENDPOINTS: Mapping[str, str] = MappingProxyType({
    "ohlcv": "/api/ohlcv/{exchange}/{ticker}",
    "indicators": "/api/indicators/{exchange}/{ticker}",
    "overview": "/api/overview/{exchange}/{ticker}",
    "movers": "/api/movers",
    "screener": "/api/screener",
    "download": "/api/download",
})
# Sigma-Overlay um das Vendor-API (Cache, Rate-Limit, Retry, Offline-Fallback).
# Der Blueprint-Prozess `sigma-tv-scraper` startet weiterhin ein FastAPI auf :8001 —
# `bin/sigma-scraper` waehlt Overlay (Default) oder `--vendor` (pures api.main:app).
SCRAPER_SIGMA_ENTRY = "uvicorn app.scraper.main:app"
SCRAPER_VENDOR_ENTRY = "uvicorn api.main:app"
SCRAPER_VENDOR_PATH = "vendor/tradingview-scraper"
SCRAPER_LAUNCHER = "bin/sigma-scraper"
SCRAPER_HEALTH_ROUTE = "/health"
SCRAPER_OHLCV_TTL_S = 20.0          # Kerzen-Cache
SCRAPER_META_TTL_S = 300.0          # Overview/Indicators
SCRAPER_MARKET_TTL_S = 120.0        # Movers/Screener
SCRAPER_RATE_LIMIT_PER_MIN = 60.0   # Token-Bucket gegen TradingView
SCRAPER_RATE_LIMIT_BURST = 15.0
SCRAPER_MAX_RETRIES = 2
SCRAPER_SOURCES: Tuple[str, ...] = ("tv_scraper", "cache_stale", "synthetic")
SCRAPER_TIMEFRAMES: Tuple[str, ...] = (
    "1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "1d", "1w",
)

MARKET_SOURCES: Tuple[str, ...] = ("synthetic", "tv_scraper", "ccxt_ws")
MARKET_SOURCE_PROD = "tv_scraper"

# =============================================================================
# 7. API-Vertrag (§7, §8.3, §5)
# =============================================================================

API_ROUTES: Mapping[str, str] = MappingProxyType({
    "POST /api/v1/signal/webhook": "Loop A Signaleingang",
    "GET /api/v1/health": "status, kill_switch, scraper_ok, tv_worker_ok",
    "POST /api/backtest/run": "TV-Job (Playwright)",
    "GET /api/backtest/ohlc": "Scraper :8001",
    "GET /api/tv/jobs/{id}": "Job-Status",
    "POST /api/tv/jobs/{id}/cancel": "Abbruch wenn queued",
    "POST /api/genetic/run": "GA mit TV-Evals",
    "GET /api/tv/session/status": "storage_state vorhanden?",
    "POST /api/tv/session/login": "Chrome auf TradingView oeffnen (manueller Login)",
    "POST /api/strategies/{id}/tv/push": "Code+Params nach TV",
    "POST /api/strategies/{id}/tv/pull-parameters": "Parameter-CSV lesen",
    "POST /api/strategies/{id}/alerts/sync": "Alert upsert",
    "GET /api/strategies/{id}/alerts": "Alert-Status",
    "POST /api/strategies/{id}/backtest": "Backtest-Job der Strategy",
    "GET /api/tv/jobs": "Jobs (Filter strategyId)",
    "POST /api/strategies/from-template": "Neue Strategy aus Pine-Template",
    "GET /api/strategies/tv/scripts": "My Scripts / published scripts listen",
    "POST /api/strategies/tv/sync-library": "My Scripts importieren",
})

STRATEGY_PERSISTED_FIELDS: Tuple[str, ...] = (
    "code", "parameters", "parameters_csv_path", "current_backtest_id",
    "tv_alert_id", "tv_script_id", "alert_status", "pine_inputs_schema",
)

# =============================================================================
# 8. UI — Sigma Terminal (§8, Masterprompt §4.D)
# =============================================================================

TERMINAL_COMPONENT = "src/components/SigmaTerminal.tsx"
TERMINAL_PANELS: Tuple[str, ...] = (
    "VirtualBotDeck",
    "PineStudio",
    "MarketChart",
    "LLMConsole",
    "AcademyBadgeMatrix",
    "RiskGauges",
    "SelfOptimizingMLPanel",
    "TelegramOperatorPanel",
    "DeadmanSwitchPanel",
    "RewardXPMatrixPanel",
    "MemoryWatchdogPanel",
)
TERMINAL_PRESETS: Tuple[str, ...] = ("BOT_COCKPIT", "PINE_IDE", "RISK_RADAR", "SENTINEL_OPS")
STRATEGY_DETAIL_TABS: Tuple[str, ...] = (
    "Code", "Parameters", "Alerts", "Backtest", "Optimize", "Live / M8", "Audit",
    "Academy Badges & Profiling",
)
STRATEGY_CARD_REQUIRED_FIELDS: Tuple[str, ...] = (
    "runner_status", "capital_eur", "bot_pnl", "max_loss", "xp_strikes",
)
RUNNER_STATES: Tuple[str, ...] = ("RUNNING", "PAUSED", "QUARANTINED")

# =============================================================================
# 9. Regime-Detektor (§21, Masterprompt §3.A)
# =============================================================================


class Regime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    WEAK_BULL = "WEAK_BULL"
    STRONG_BEAR = "STRONG_BEAR"
    WEAK_BEAR = "WEAK_BEAR"
    RANGING_CHOP = "RANGING_CHOP"
    HIGH_VOL_CRISIS = "HIGH_VOL_CRISIS"


ATR_PERCENTILE_WINDOW_BARS = 100
ATR_PERIOD = 14
EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200

ATR_PCTL_COMPRESSION_MAX = 30.0     # < 30 Compression / Low Vol
ATR_PCTL_NORMAL_MAX = 70.0          # 30-70 Normal / Chop
ATR_PCTL_CRISIS_MIN = 95.0          # >= 95 Emergency Volatility Crisis -> Entry-Sperre

HURST_MEAN_REVERSION_MAX = 0.45
HURST_TREND_MIN = 0.55

# =============================================================================
# 10. Self-Optimizing ONNX (§21, Masterprompt §3.B)
# =============================================================================

BRIER_DRIFT_THRESHOLD = 0.28        # BS > 0.28 -> Temperatur erhoehen
ONNX_TEMPERATURE_DEFAULT = 1.0
ONNX_TEMPERATURE_STEP = 0.1
ONNX_TEMPERATURE_MAX = 3.0
ONNX_SHADOW_GATE_BEFORE_HOT_RELOAD = True

# =============================================================================
# 11. Reward Shaping (§21, Masterprompt §3.C)
# =============================================================================

REWARD_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "w1_pnl": 1.0,
    "w2_mfe_mae": 0.5,
    "w3_time_decay": 0.25,
    "w4_fee_churn": 0.25,
})
REWARD_EPSILON = 1e-9
REWARD_MULTIPLIER_S = 1.5
REWARD_MULTIPLIER_A = 1.25
REWARD_MULTIPLIER_B = 1.0
REWARD_MULTIPLIER_C = 0.5
REWARD_MULTIPLIER_F = 0.5
STRIKES_TO_QUARANTINE = 3

# =============================================================================
# 12. Academy Badges (§18)
# =============================================================================

BADGE_MIN_SAMPLE = 30               # N >= 30 sonst INSUFFICIENT_SAMPLE
BADGE_INSUFFICIENT = "INSUFFICIENT_SAMPLE"
BADGE_S_WINRATE_MIN = 0.60
BADGE_S_PROFIT_FACTOR_MIN = 1.8
BADGE_F_WINRATE_MAX = 0.40
BADGE_F_PROFIT_FACTOR_MAX = 0.9
BADGE_RATINGS: Tuple[str, ...] = ("S", "A", "B", "C", "F")

ACADEMY_TABLES: Tuple[str, ...] = ("academy_trade_history", "strategy_performance_profiles")

class StrategyLamp(str, Enum):
    GRAY = "gray"
    YELLOW = "yellow"
    GREEN_SOLID = "green_solid"
    GREEN_GLOW = "green_glow"
    RED_GLOW = "red_glow"


SLOT_ORIGIN_USER = "user"
SLOT_ORIGIN_ACADEMY = "academy"
INITIALIZE_RELEASE_PF = 1.5
INITIALIZE_LOOKBACK_DAYS = 30
STRATEGY_LAMP_RANK: Mapping[str, int] = MappingProxyType({
    StrategyLamp.GRAY.value: 0,
    StrategyLamp.YELLOW.value: 1,
    StrategyLamp.GREEN_SOLID.value: 2,
    StrategyLamp.GREEN_GLOW.value: 3,
    StrategyLamp.RED_GLOW.value: 4,
})

# Kausale Fehler-Dekomposition (Masterprompt §2 Loop E)
CAUSAL_FAULTS: Tuple[str, ...] = (
    "LEVERAGE_FAULT", "PARAMETER_FAULT", "ASSET_MISMATCH", "STRUCTURAL_DEFECT",
)

# =============================================================================
# 13. Style & Campaign Horizons (Masterprompt §3.D)
# =============================================================================


@dataclass(frozen=True)
class StyleSpec:
    style: str
    timeframes: str
    hold: str
    campaign: str
    restriction: str


STYLES: Tuple[StyleSpec, ...] = (
    StyleSpec("STYLE_MICRO_SCALP", "1m-3m", "1-10 Min", "max 6h Session-Burst", "RESTRICTION_NO_LONG_RUN"),
    StyleSpec("STYLE_INTRADAY_MOMENT", "5m-15m", "30 Min-4 Std", "1-3 Tage", ""),
    StyleSpec("STYLE_SWING_CAMPAIGN", "1h-4h", "1-7 Tage", "14-45 Tage", "SUITABLE_FOR_LONG_RUN_30D"),
    StyleSpec("STYLE_POSITION_INVEST", "1D", "Wochen-Monate", "90d+ Makro", ""),
)

# =============================================================================
# 14. Virtual Bot Engine (§20)
# =============================================================================

VIRTUAL_BOT_EXCHANGE_PRIMARY = "kraken_cli"
VIRTUAL_BOT_SIZING_BASIS = "bot.current_equity"     # niemals Gesamtkonto
VIRTUAL_BOT_VAULT_TABLE = "strategy_vaults"
VIRTUAL_BOT_ON_MAX_LOSS = (M8State.QUARANTINED, AlertAction.DISABLE)
PIONEX_ENABLED_DEFAULT = False
PIONEX_MODE_IF_ENABLED = "spot_only"
NATIVE_BRACKET_SL_REQUIRED = True

# Deadman Switch (§21, Masterprompt §4.C)
DEADMAN_HEARTBEAT_SECONDS_MIN = 15
DEADMAN_HEARTBEAT_SECONDS_MAX = 20
DEADMAN_TIMEOUT_SECONDS = 1800          # 30 min ohne Kraken-Time-Ping → Trigger
DEADMAN_CANCEL_ONLY_IF_NATIVE_STOP = True
DEADMAN_FALLBACK_ACTION = "close_all_market"

# =============================================================================
# 15. Memory Watchdog (§21, Masterprompt Loop E)
# =============================================================================

# Stufen relativ zum Prozess-/cgroup-Budget (4G), nicht zum Host-RAM.
# 60/72 liegen vor systemd MemoryHigh=3G; 92 vor MemoryMax=4G.
MEMORY_STAGES_PCT: Tuple[float, ...] = (60.0, 72.0, 85.0, 92.0)
MEMORY_STAGE_ACTIONS: Tuple[str, ...] = (
    "gc_collect", "duckdb_checkpoint", "chromium_zombie_reaper", "emergency_halt_and_restart_worker",
)
MEMORY_STAGE_COOLDOWN_S: Tuple[float, ...] = (45.0, 90.0, 60.0, 300.0)
MEMORY_CGROUP_MAX = "4G"
MEMORY_IDLE_ONLY = True
MEMORY_IDLE_MIN_STAGE = 3          # GC + DuckDB immer; Reaper nur ohne laufenden TV-Job
MEMORY_HOUSEKEEP_S = 90.0          # Stage-0 malloc_trim / gc, damit Arenen nicht anwachsen

# =============================================================================
# 16. Telegram Operator (§21, Masterprompt §4.B)
# =============================================================================

TELEGRAM_WHITELIST_ENV = "TELEGRAM_CHAT_ID"
TELEGRAM_FAST_PATH_COMMANDS: Tuple[str, ...] = ("/status", "/pause", "/resume", "/kill")
TELEGRAM_FAST_PATH_BUDGET_MS = 50
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODELS: Tuple[str, ...] = ("llama3.1:8b", "qwen2.5-coder")
LLM_TOOLS: Tuple[str, ...] = (
    "update_risk_settings", "control_strategy_runner", "edit_strategy_pine_code", "query_telemetry",
)

# =============================================================================
# 17. Self-Healing Selector Engine (§16)
# =============================================================================

SELECTORS_LOCAL_PATH_ENV = "SIGMA_SELECTORS_PATH"
SELECTORS_REMOTE_URL_ENV = "SIGMA_SELECTORS_REMOTE_URL"
SELECTORS_SHA256_ENV = "SIGMA_SELECTORS_SHA256"
SELECTOR_HEAL_STAGES: Tuple[str, ...] = ("local_yaml", "remote_fetch", "builtin_default")
SELECTOR_MAX_DOWNLOADS = 3
SELECTOR_DOWNLOAD_WINDOW_SECONDS = 300
SELECTOR_ERROR_CODE = "ELEMENT_NOT_FOUND"
SELECTOR_RETRY_AFTER_REMOTE_REFRESH = 1     # genau einmal, keine Endlosschleife

# =============================================================================
# 18. Redis-Keys (§11)
# =============================================================================

REDIS_KEYS: Mapping[str, str] = MappingProxyType({
    "m8_state": "m8:state:{instance_id}",
    "m8_processed_trades": "m8:processed_trades:{instance_id}",
    "halt_symbol": SYMBOL_HALT_KEY,
    "vault_balance": "vault:balance",
    "signals_proposed": "signals:proposed",
    "signals_verdict": "signals:verdict",
    "strategies_wake_up": "strategies:wake_up",
    "tv_jobs": "sigma:tv:jobs",
    "tv_job": "sigma:tv:job:{id}",
})

# =============================================================================
# 19. Systemd / Delivery / Env (§10, §13, §9)
# =============================================================================

SYSTEMD_UNITS: Tuple[str, ...] = (
    "sigma-redis.service", "sigma-scraper.service", "sigma-core.service",
    "sigma-tv-worker.service", "sigma-telegram.service",
)
SYSTEMD_RESTART = "always"
SYSTEMD_RESTART_SEC = 3

DELIVERY_PHASES: Mapping[str, str] = MappingProxyType({
    "P0": "Blaupause + YAML",
    "P1": "Scraper Sidecar + Client; /api/backtest/ohlc",
    "P2": "FakeDriver + CSV Seam + /api/backtest/run Job-API",
    "P3": "Echter Playwright Driver + login bootstrap",
    "P4": "GA auf Job-Queue (WFO, Cache, ShadowGate)",
    "P5": "Webhook + Safety + ONNX/Kelly + Kraken Bridge (default SIM)",
    "P6": "Monaco + Job-Status UI + systemd Dauerbetrieb",
})

ENV_MATRIX: Mapping[str, str] = MappingProxyType({
    "SIGMA_DATA_DIR": "Datenwurzel (default ./data)",
    "SIGMA_REDIS_URL": "Redis DSN",
    "SIGMA_TV_SCRAPER_URL": "Scraper Sidecar (default http://127.0.0.1:8001)",
    "SIGMA_TV_STORAGE_STATE": "Playwright storage state JSON",
    "SIGMA_TV_EXPORT_DIR": "CSV-Exportverzeichnis",
    "SIGMA_MARKET_SOURCE": "synthetic|tv_scraper|ccxt_ws",
    "SIGMA_LIVE_TRADING": "0|1 — Kraken CLI scharf schalten",
    "SIGMA_ONNX_MODEL_PATH": "Regime/Confidence Modell",
    "SIGMA_WEBHOOK_SECRET": "Shared Secret fuer Pine Alerts",
    "SIGMA_SELECTORS_PATH": "Lokale selectors.yaml",
    "SIGMA_SELECTORS_REMOTE_URL": "Remote Self-Heal Quelle",
    "SIGMA_SELECTORS_SHA256": "Optionale Integritaetspruefung",
    "TELEGRAM_BOT_TOKEN": "Telegram Operator",
    "TELEGRAM_CHAT_ID": "Whitelist Chat",
})

# Exchange-Defaults aus config/autonomy-level-4.yaml (§9) — hard-coded Spiegel
EXCHANGE_SPOT: Mapping[str, Any] = MappingProxyType({
    "enabled": True,
    "allowed_symbols": ("XBTUSD", "ETHUSD"),
    "allowed_order_types": ("limit", "market"),
    "max_order_notional_usd": 500,
    "max_daily_notional_usd": 2000,
    "symbol_mappings": MappingProxyType({"XBTUSD": "KRAKEN:XBTUSD", "ETHUSD": "KRAKEN:ETHUSD"}),
})
EXCHANGE_FUTURES: Mapping[str, Any] = MappingProxyType({
    "enabled": True,
    "allowed_symbols": ("PI_XBTUSD", "PI_ETHUSD"),
    "allowed_order_types": ("limit", "market", "stop", "take-profit"),
    "max_leverage": 5,
    "max_order_notional_usd": 1000,
    "max_daily_notional_usd": 5000,
    "symbol_mappings": MappingProxyType({"PI_XBTUSD": "KRAKEN:XBTUSD.P", "PI_ETHUSD": "KRAKEN:ETHUSD.P"}),
})
HALT_ACTION = "cancel_all"

# M8 Defaults (Alpha v1.2.0 lineage)
M8_BASE_BUDGET_USD = 50.0
M8_AUTOPSY_ORDER = "v1.2.0"


# =============================================================================
# 20. Normative Helfer (pure functions über den hard-coded Werten)
# =============================================================================

def alert_policy_for_state(state: "M8State | str") -> M8AlertPolicy:
    """§4.6 — welche Alert-Aktion / Size gilt für einen M8-State."""
    key = M8State(state) if not isinstance(state, M8State) else state
    return M8_ALERT_MATRIX[key]


def normalize_timestamp(ts: float) -> int:
    """§17.2 — ms -> s Normalisierung."""
    return int(ts // 1000) if ts > TIMESTAMP_MS_THRESHOLD else int(ts)


def is_stale_signal(ts: float, now: float, interval_seconds: int = 60) -> bool:
    """§17.2 — Signal ist stale wenn älter als max(2*interval, 120s)."""
    limit = max(SIGNAL_STALE_INTERVAL_FACTOR * interval_seconds, SIGNAL_STALE_MIN_SECONDS)
    return (now - normalize_timestamp(ts)) > limit


def kraken_output_is_error(stdout: str, stderr: str, exit_code: int) -> bool:
    """§17.3 — Text schlägt Exit-Code."""
    blob = f"{stdout or ''}\n{stderr or ''}"
    return exit_code != 0 or any(marker in blob for marker in KRAKEN_ERROR_MARKERS)


def calculate_kelly(equity: float, price: float, win_prob: float,
                    rrr: float = KELLY_DEFAULT_RRR) -> float:
    """§4.2 Schritt 4 — Half-Kelly mit Cap max_portfolio_risk_per_trade."""
    if price <= 0 or equity <= 0:
        return 0.0
    edge = win_prob - (1.0 - win_prob) / max(rrr, 1e-9)
    fraction = max(0.0, edge) * KELLY_FRACTION
    fraction = min(fraction, MAX_PORTFOLIO_RISK_PER_TRADE)
    return (equity * fraction) / price


def bracket_prices(entry: float, atr: float, action: str) -> Tuple[float, float]:
    """§4.2 Schritt 5 — sl = atr*1.5, tp = atr*3.0, richtungsabhängig."""
    sl_dist = atr * ATR_STOP_MULTIPLIER
    tp_dist = atr * ATR_TAKE_PROFIT_MULTIPLIER
    if action.upper() == "SELL":
        return entry + sl_dist, entry - tp_dist
    return entry - sl_dist, entry + tp_dist


def classify_atr_percentile(pctl: float) -> str:
    """Masterprompt §3.A — Volatilitäts-Bänder."""
    if pctl >= ATR_PCTL_CRISIS_MIN:
        return "CRISIS"
    if pctl > ATR_PCTL_NORMAL_MAX:
        return "EXPANSION"
    if pctl < ATR_PCTL_COMPRESSION_MAX:
        return "COMPRESSION"
    return "NORMAL"


def classify_hurst(h: float) -> str:
    if h < HURST_MEAN_REVERSION_MAX:
        return "MEAN_REVERSION"
    if h > HURST_TREND_MIN:
        return "PERSISTENT_TREND"
    return "RANDOM_WALK"


def badge_rating(trade_count: int, win_rate: float, profit_factor: float) -> Tuple[str, bool]:
    """§18.2 — (rating, is_allowed). Unter N=30 kein S/F-Urteil."""
    if trade_count < BADGE_MIN_SAMPLE:
        return BADGE_INSUFFICIENT, False
    if win_rate >= BADGE_S_WINRATE_MIN and profit_factor >= BADGE_S_PROFIT_FACTOR_MIN:
        return "S", True
    if win_rate <= BADGE_F_WINRATE_MAX or profit_factor < BADGE_F_PROFIT_FACTOR_MAX:
        return "F", False
    if win_rate >= 0.55 and profit_factor >= 1.4:
        return "A", True
    if win_rate >= 0.48 and profit_factor >= 1.1:
        return "B", True
    return "C", True


def reward_total(pnl: float, mfe: float, mae: float, time_decay: float, fee_churn: float) -> float:
    """Masterprompt §3.C — R_total = w1*PnL + w2*(MFE/(MAE+eps)) - w3*Time - w4*Fee."""
    w = REWARD_WEIGHTS
    return (w["w1_pnl"] * pnl
            + w["w2_mfe_mae"] * (mfe / (abs(mae) + REWARD_EPSILON))
            - w["w3_time_decay"] * time_decay
            - w["w4_fee_churn"] * fee_churn)


def budget_multiplier_for_grade(grade: str, strikes: int = 0) -> float:
    """Masterprompt §3.C — Notenschema -> Budget-Multiplier; 3 Strikes = Quarantäne."""
    if strikes >= STRIKES_TO_QUARANTINE:
        return 0.0
    return {
        "S": REWARD_MULTIPLIER_S,
        "A": REWARD_MULTIPLIER_A,
        "B": REWARD_MULTIPLIER_B,
        "C": REWARD_MULTIPLIER_C,
        "F": REWARD_MULTIPLIER_F,
    }.get(grade.upper(), REWARD_MULTIPLIER_B)


def brier_score(predictions: "list[float]", outcomes: "list[float]") -> float:
    """Masterprompt §3.B — BS = 1/N * sum((y_hat - y)^2)."""
    if not predictions:
        return 0.0
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)


def calibrate_confidence(p: float, temperature: float = ONNX_TEMPERATURE_DEFAULT,
                         bias: float = 0.0) -> float:
    """Masterprompt §3.B — y_cal = sigmoid(logit(y)/T + bias)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    logit = math.log(p / (1 - p))
    z = logit / max(temperature, 1e-6) + bias
    return 1.0 / (1.0 + math.exp(-z))


def next_temperature(current: float, brier: float) -> float:
    """BS > 0.28 -> Temperatur erhöhen (Konfidenz dämpfen), sonst langsam zurück."""
    if brier > BRIER_DRIFT_THRESHOLD:
        return min(current + ONNX_TEMPERATURE_STEP, ONNX_TEMPERATURE_MAX)
    return max(ONNX_TEMPERATURE_DEFAULT, current - ONNX_TEMPERATURE_STEP / 2)


def memory_stage(pct: float) -> int:
    """0 = ok, 1..4 = Eskalationsstufe laut MEMORY_STAGES_PCT."""
    stage = 0
    for i, threshold in enumerate(MEMORY_STAGES_PCT, start=1):
        if pct >= threshold:
            stage = i
    return stage


def strategy_path(kind: str, strategy_id: str, backtest_id: str = "") -> str:
    """§8.4 — kanonische CSV-Pfade je Strategie."""
    return STRATEGY_FILE_LAYOUT[kind].format(id=strategy_id, bid=backtest_id)


def spec_summary() -> Dict[str, Any]:
    """Kompakter Spec-Fingerprint für /api/v1/health und Audits."""
    return {
        "blueprint_version": BLUEPRINT_VERSION,
        "masterprompt_version": MASTERPROMPT_VERSION,
        "autonomy_level": AUTONOMY_LEVEL,
        "host": HOST_OS,
        "install_root": INSTALL_ROOT,
        "loops": [loop.value for loop in Loop],
        "ports": {
            "core": PORT_CORE, "scraper": PORT_SCRAPER, "redis": PORT_REDIS,
            "ui": PORT_UI_DEV, "ollama": PORT_OLLAMA,
        },
        "axioms": list(AXIOMS),
        "risk_guard": dict(RISK_GUARD),
        "ga": {
            "max_population": GA_MAX_POPULATION,
            "max_generations": GA_MAX_GENERATIONS,
            "early_stop_stall": GA_EARLY_STOP_STALL_GENERATIONS,
            "concurrency": GA_CONCURRENCY,
            "dsr_gate": DSR_SHADOW_GATE,
            "min_trades": MIN_TRADES_FOR_GATE,
        },
        "panels": list(TERMINAL_PANELS),
        "presets": list(TERMINAL_PRESETS),
    }


# =============================================================================
# 23. Zeit-Anker & Scheduler-Matrix (§23, Masterprompt Axiom 4/5)
# =============================================================================

KRAKEN_TIME_URL = "https://api.kraken.com/0/public/Time"
CLOCK_RESYNC_INTERVAL_S = 3600          # stündlicher Re-Sync
CLOCK_MAX_OFFSET_WARN_S = 2.0           # Drift-Warnung ab 2 s
CLOCK_SYNC_TIMEOUT_S = 5.0
STALE_SIGNAL_MAX_LATENCY_S = 60         # Webhook älter -> STALE_SIGNAL_REJECT
STALE_SIGNAL_REJECT_CODE = "STALE_SIGNAL_REJECT"


class SchedulerTier(int, Enum):
    T0_EVENT = 0
    T1_FAST_PULSE = 1
    T2_MID = 2
    T3_REGIME = 3
    T4_DAILY = 4
    T5_WEEKLY = 5


@dataclass(frozen=True)
class TierSpec:
    tier: SchedulerTier
    label: str
    cadence_s: float | None          # None = event-driven / cron
    cron: str | None
    tasks: Tuple[str, ...]


SCHEDULER_MATRIX: Tuple[TierSpec, ...] = (
    TierSpec(SchedulerTier.T0_EVENT, "Event-Driven (Just-in-Time)", None, None,
             ("glint_orderbook_verify", "webhook_execution", "kill_switch",
              "playwright_compile")),
    TierSpec(SchedulerTier.T1_FAST_PULSE, "Fast Pulse", 20.0, None,
             ("deadman_heartbeat", "memory_watchdog", "kraken_fill_reconcile")),
    TierSpec(SchedulerTier.T2_MID, "Mid", 300.0, None,
             ("macro_radar_scraper", "scorecard_stage1_idle")),
    TierSpec(SchedulerTier.T3_REGIME, "Regime", 14400.0, None,
             ("strategy_allocator", "regime_recheck", "brier_drift",
              "regime_strategy_dispatcher")),
    TierSpec(SchedulerTier.T4_DAILY, "Daily", None, "05 00 * * *",
             ("spot_rebalance", "eod_profit_factor", "flywheel_sweep")),
    TierSpec(SchedulerTier.T5_WEEKLY, "Weekly", None, "00 23 * * 0",
             ("academy_badge_recalibration", "kausal_audit")),
)
SCHEDULER_TIMEZONE = "UTC"
MAX_CACHED_DEPTH_AGE_S = 3              # kein Dauer-Orderbuch-Scan (§23.2)
SCOUT_INCUBATOR_CYCLE_MINUTES = 30      # §31.3

# =============================================================================
# 24. Glint x Orderbook Confluence (§24)
# =============================================================================

DEPTH_BAND_PCT = 0.02                   # 2 % Tiefe je Seite
DEPTH_IMBALANCE_CONFIRM = 0.30          # I_depth >= +0.30 -> bestätigt
DEPTH_IMBALANCE_VETO = -0.20            # I_depth <= -0.20 -> Liquidity Trap
CONFLUENCE_MAX_SPREAD_BPS = 15.0
CONFLUENCE_SIZE_MULTIPLIER = 1.25
GLINT_SCORE_AUTONOMOUS_ENTRY = 8.0      # §31.2 Score >= 8/10


class ConfluenceVerdict(str, Enum):
    CONFLUENCE_CONFIRMED = "CONFLUENCE_CONFIRMED"
    NEUTRAL = "NEUTRAL"
    LIQUIDITY_TRAP_VETO = "LIQUIDITY_TRAP_VETO"


ORDERBOOK_WALL_REJECT = "ORDERBOOK_WALL_REJECT"

# =============================================================================
# 25. Closed-Loop Order ACK & Retry (§25, Axiom 6)
# =============================================================================


class OrderAck(str, Enum):
    FILLED = "FILLED"
    RETRY_SUCCESS = "RETRY_SUCCESS"
    FAILED_REJECTED = "FAILED_REJECTED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    VETO_ORDERBOOK = "VETO_ORDERBOOK"


ORDER_MAX_RETRIES = 2
ORDER_GHOST_FILL_CHECK_TIMEOUT_MS = 200
ORDER_NON_RETRYABLE_PATTERNS: Tuple[str, ...] = (
    "insufficient funds", "invalid arguments", "invalid key", "permission denied",
    "unknown asset pair", "order minimum not met",
)
ORDER_RECEIPTS_ROUTE = "/api/orders/receipts"
ORDER_RECEIPT_LOG = "./data/logs/orders.jsonl"
IDEMPOTENCY_KEY_TEMPLATE = "sig_{strategy_id}_{ticker}_{bar_time}"

# =============================================================================
# 26. Multi-Provider Rate Limiter (§26)
# =============================================================================

TV_SUBSCRIPTION_TIERS: Mapping[str, int] = MappingProxyType({
    "free": 1, "essential": 5, "plus": 10, "premium": 20,
})
TV_SUBSCRIPTION_TIER_DEFAULT = "essential"
TV_ALERT_ROTATION_ENABLED = True
KRAKEN_MAX_CALL_COUNTER = 15.0
KRAKEN_COUNTER_DECAY_PER_S = 0.50
KRAKEN_EMERGENCY_RESERVE_TOKENS = 3.0
KRAKEN_SOFT_CAP_PCT = 0.80              # ab 80 % Hintergrund-Polls pausieren
TELEGRAM_MAX_MESSAGES_PER_S = 1.0
HTTP_429_BACKOFF_S: Tuple[float, ...] = (10.0, 30.0, 60.0)

# =============================================================================
# 27. Epidemic SIR Contagion (§27)
# =============================================================================

SIR_R0_HEDGE_THRESHOLD = 1.5            # >= 1.5 -> FLIGHT_TO_CASH_AND_HEDGE
SIR_R0_DERISK_THRESHOLD = 1.0           # >= 1.0 -> Futures-Sizing -50 %
SIR_DERISK_SIZE_MULTIPLIER = 0.5
SIR_MAX_STATE_AGE_S = 900
SIR_INPUTS: Tuple[str, ...] = (
    "oil_vol_zscore", "gold_dxy_ratio", "cross_asset_correlation",
    "orderbook_absorption",
)


class ContagionMode(str, Enum):
    NORMAL = "NORMAL"
    DERISK = "DERISK"
    FLIGHT_TO_CASH_AND_HEDGE = "FLIGHT_TO_CASH_AND_HEDGE"


CONTAGION_VETO_CODE = "ERR_CONTAGION_VETO_R0"

# =============================================================================
# 28. 50/50 Flywheel (§28, Axiom 7)
# =============================================================================

FLYWHEEL_DEPOSIT_TO_FUTURES_PCT = 1.0   # 100 % Einzahlung -> Futures
FLYWHEEL_PROFIT_REINVEST_PCT = 0.5      # 50 % Bot-Reinvest
FLYWHEEL_PROFIT_VAULT_PCT = 0.5         # 50 % Spot-Tresor
FLYWHEEL_MIN_SPLIT_TRIGGER_EUR = 10.0
FLYWHEEL_DEFAULT_VAULT_ASSET = "XBT"
FLYWHEEL_VAULT_QUOTE = "EUR"
FLYWHEEL_ONE_WAY = True                 # Spot -> Futures niemals automatisch
FLYWHEEL_LEDGER_TABLE = "flywheel_ledger"

# =============================================================================
# 29. Fester Hebel pro Strategie (§29, Axiom 8)
# =============================================================================

FIXED_LEVERAGE_MIN = 1
FIXED_LEVERAGE_MAX = 5
FIXED_LEVERAGE_DEFAULT = 1
DYNAMIC_LEVERAGE_REJECTED = True        # verworfen: per-Trade Neuberechnung
STYLE_DEFAULT_LEVERAGE: Mapping[str, int] = MappingProxyType({
    "STYLE_MICRO_SCALP": 5,
    "STYLE_INTRADAY_MOMENT": 3,
    "STYLE_SWING_CAMPAIGN": 2,
    "STYLE_POSITION_INVEST": 1,
})

# =============================================================================
# 30. Erweiterte UI-Panels (§30, §37.4, §38.4)
# =============================================================================

TERMINAL_PANELS_EXTENDED: Tuple[str, ...] = (
    "OrderbookConfluencePanel",
    "SchedulerTelemetryPanel",
    "OrderReceiptsPanel",
    "RateLimiterPanel",
    "ContagionRadarPanel",
    "FlywheelBudgetPanel",
    "PaperLabPanel",
    "DiagnosticsErrorPanel",
    "ProcessLogView",
    "NetronVisualizerPanel",
    "OverviewMetricsPanel",
    "StrategyLibraryPanel",
    "SystemHealthPanel",
    "RegimePanel",
    "ExecutionRiskPanel",
    "AcademyRegistryPanel",
    "BacktestPanel",
    "GeneticPanel",
    "QueueMatrixPanel",
    "LedgersPanel",
    "DataLakePanel",
    "SettingsPanel",
)
TERMINAL_PRESETS_EXTENDED: Tuple[str, ...] = (
    "CAPITAL_OPS", "PAPER_LAB", "OBSERVABILITY", "ML_INSPECTOR",
    "OVERVIEW", "LIBRARY", "QUANT", "CONFIG",
)
ALL_TERMINAL_PANELS: Tuple[str, ...] = TERMINAL_PANELS + TERMINAL_PANELS_EXTENDED
ALL_TERMINAL_PRESETS: Tuple[str, ...] = TERMINAL_PRESETS + TERMINAL_PRESETS_EXTENDED

# =============================================================================
# 31. Die 3 Trigger-Pfade (§31, Axiom 9)
# =============================================================================


class TriggerPath(str, Enum):
    MANUAL = "MANUAL"                    # UI / LLM-Chat / Telegram
    AUTONOMOUS_REGIME = "AUTONOMOUS_REGIME"   # RegimeStrategyDispatcher (Tier 3)
    SCOUT_INCUBATOR = "SCOUT_INCUBATOR"       # Loop D, paper-only


TRIGGER_PATH_MODES: Mapping[str, Tuple[str, ...]] = MappingProxyType({
    TriggerPath.MANUAL.value: ("live", "kraken_paper"),
    TriggerPath.AUTONOMOUS_REGIME.value: ("live",),
    TriggerPath.SCOUT_INCUBATOR.value: ("kraken_paper",),
})
LIFECYCLE_STEPS: Tuple[str, ...] = (
    "budget_reservation", "chart_navigation", "pine_injection_compile",
    "webhook_alert_provisioning", "arming_m8_active",
)
STRATEGY_START_ROUTE = "/api/strategies/{id}/start"

# =============================================================================
# 32. Kraken Paper Trading Lab (§32, Axiom 10)
# =============================================================================


class ExecutionMode(str, Enum):
    LIVE = "live"
    KRAKEN_PAPER = "kraken_paper"
    HYBRID_SCOUT = "hybrid_scout"
    DRY_RUN = "dry_run"


EXECUTION_MODE_DEFAULT = ExecutionMode.LIVE.value
KRAKEN_PAPER_ENABLED = True
KRAKEN_PAPER_INITIAL_BALANCE_USD = 10_000.0
KRAKEN_DEMO_FUTURES_URL = "https://demo-futures.kraken.com/api/v3"
PAPER_GRADUATION_MIN_TRADES = 20
PAPER_GRADUATION_MIN_PROFIT_FACTOR = 1.6
PAPER_GRADUATION_MIN_WIN_RATE_PCT = 55.0
KRAKEN_PAPER_COMMANDS: Mapping[str, str] = MappingProxyType({
    "spot_balance": "kraken paper balance",
    "spot_order": "kraken paper order {side} {pair} {volume} --type {ordertype} --price {price}",
    "futures_balance": "kraken futures paper balance",
    "futures_order": "kraken futures paper order {side} {pair} {volume} --type {ordertype} --price {price}",
})

# =============================================================================
# 33. Webhook-Alert-Schemata (§33, Axiom 11)
# =============================================================================

WEBHOOK_SCHEMAS: Tuple[str, ...] = ("SIGMA_L4_MASTER", "PIONEX_NATIVE", "ML_TELEMETRY")
SIGMA_L4_REQUIRED_FIELDS: Tuple[str, ...] = (
    "secret", "idempotency_key", "strategy_id", "bot_id", "symbol", "action",
    "price", "stop_loss", "fixed_leverage", "timestamp",
)
SIGMA_L4_ACTIONS: Tuple[str, ...] = ("BUY", "SELL", "CLOSE")
SIGMA_L4_ORDER_TYPES: Tuple[str, ...] = ("MARKET", "LIMIT")
SIGMA_SECRET_MIN_LENGTH = 16
IDEMPOTENCY_KEY_MIN_LENGTH = 8
ML_FEATURE_FIELDS: Tuple[str, ...] = ("rsi", "atr", "cisd_score", "bb_bandwidth")
PINE_EMITTER_TEMPLATE_PATH = "./prompts/pine_sigma_l4_emitter_v6.pine"
INGESTION_PIPELINE_STEPS: Tuple[str, ...] = (
    "secret_check", "stale_gate", "idempotency", "glint_orderbook_jit",
    "reliable_dispatch",
)

# =============================================================================
# 34. LLM Tool-Contracts (§34, Axiom 12)
# =============================================================================

LLM_TOOLS: Mapping[str, str] = MappingProxyType({
    "update_risk_settings": "max_daily_loss_usd, kelly_fraction, max_open_positions, global_max_leverage",
    "control_bot": "START | PAUSE | STOP | QUARANTINE (+ adjusted_budget_eur)",
    "edit_pine_strategy_code": "FULL_REPLACE | DIFF_PATCH | INJECT_TIME_STOP | ADJUST_PARAMETERS",
    "query_kausal_autopsy": "strategy_id, symbol, timeframe",
    "trigger_emergency_action": "KILL_SWITCH | CANCEL_ALL_ORDERS | FLIGHT_TO_CASH",
})
LLM_TOOLS_REQUIRING_CONFIRMATION: Tuple[str, ...] = ("trigger_emergency_action",)
PINE_EDIT_MODES: Tuple[str, ...] = (
    "FULL_REPLACE", "DIFF_PATCH", "INJECT_TIME_STOP", "ADJUST_PARAMETERS",
)
PINE_VERSION_HEADER = "//@version=6"
LLM_STREAM_ROUTE = "/api/v1/llm/stream"
LLM_STREAM_SENDERS: Tuple[str, ...] = ("USER", "ASSISTANT", "SYSTEM", "TOOL_EXECUTOR")
LLM_UI_TRIGGERS: Tuple[str, ...] = ("REFRESH_BOT_DECK", "RELOAD_CHART", "OPEN_INSPECTOR")
LLM_TOOL_STATUSES: Tuple[str, ...] = ("SUCCESS", "FAILED", "CONFIRMATION_REQUIRED")

# =============================================================================
# 35. Exact TradingView CSV Roundtrip (§35, Axiom 13)
# =============================================================================

CSV_KEEP_ORIGINAL_FILENAME = True
CSV_HEADER_MUST_MATCH_BYTEWISE = True
CSV_ALLOWED_DELIMITERS: Tuple[str, ...] = (",", ";")
CSV_HEADER_MISMATCH_CODE = "CSV_HEADER_MISMATCH"
CSV_BASELINE_DIR = "baseline"
CSV_OPTIMIZED_DIR = "optimized"
CSV_META_FILE = "meta.json"
CSV_META_FIELDS: Tuple[str, ...] = (
    "original_csv_filename", "exact_csv_header", "delimiter",
)
CSV_FORBIDDEN_FILENAMES: Tuple[str, ...] = ("parameters_optimized.csv",)

# =============================================================================
# 36. Unified Error Taxonomy (§36, Axiom 14)
# =============================================================================

ERROR_LOG_PATH = "./data/logs/errors.jsonl"
ERROR_CATEGORIES: Mapping[str, str] = MappingProxyType({
    "E1000": "AUTHENTICATION",
    "E2000": "TRADINGVIEW",
    "E3000": "KRAKEN",
    "E4000": "RISK_GUARD",
    "E5000": "SYSTEM",
})
ERROR_CATALOG: Mapping[str, Tuple[str, str, str]] = MappingProxyType({
    # code: (range, subsystem, remediation_hint)
    "ERR_AUTH_INVALID_SECRET": ("E1000", "sigma-core",
                                "SIGMA_SECRET in Pine-Alert und .env abgleichen"),
    "ERR_AUTH_TV_SESSION_EXPIRED": ("E1000", "playwright-worker",
                                    "bin/sigma-tv-login erneut ausfuehren (tv_storage_state.json)"),
    "ERR_AUTH_WHITELIST_BLOCKED": ("E1000", "sigma-core",
                                   "Telegram chat_id in die Whitelist aufnehmen"),
    "ERR_TV_SELECTOR_NOT_FOUND": ("E2000", "playwright-worker",
                                  "DynamicYamlResolver Selector-Update anstossen"),
    "ERR_TV_PINE_COMPILE_ERROR": ("E2000", "playwright-worker",
                                  "Pine v6 Syntax im Monaco Editor pruefen"),
    "ERR_TV_ALERT_QUOTA_EXCEEDED": ("E2000", "playwright-worker",
                                    "TV-Tier erhoehen oder Alert-Rotation aktivieren"),
    "ERR_TV_EXPORT_TIMEOUT": ("E2000", "playwright-worker",
                              "Job erneut einreihen; Netz/TV-Latenz pruefen"),
    "ERR_TV_CSV_HEADER_MISMATCH": ("E2000", "sigma-core",
                                   "Original-Header aus baseline/ uebernehmen"),
    "ERR_KRAKEN_INSUFFICIENT_FUNDS": ("E3000", "kraken-bridge",
                                      "Einzahlung oder Bot-Budget senken"),
    "ERR_KRAKEN_RATE_LIMIT_429": ("E3000", "kraken-bridge",
                                  "Backoff abwarten (10s/30s/60s)"),
    "ERR_KRAKEN_DEADMAN_TIMEOUT": ("E3000", "kraken-bridge",
                                   "Heartbeat-Quelle pruefen; offene Limits wurden gecancelt"),
    "ERR_KRAKEN_CLI_NOT_FOUND": ("E3000", "kraken-bridge",
                                 "kraken CLI installieren und SIGMA_KRAKEN_CLI setzen"),
    "ERR_RISK_MAX_DAILY_LOSS": ("E4000", "sigma-core",
                                "Handelstag beendet; Release erst nach Review"),
    "ERR_RISK_KILL_SWITCH_ACTIVE": ("E4000", "sigma-core",
                                    "KILL_SWITCH-Datei entfernen (bin/m8-ctl resume)"),
    "ERR_ORDERBOOK_LIQUIDITY_TRAP": ("E4000", "sigma-core",
                                     "Schutz-Veto - kein Eingriff noetig"),
    "ERR_CONTAGION_VETO_R0": ("E4000", "sigma-core",
                              "Makro-Kontagion aktiv; Sizing reduziert"),
    "ERR_STALE_SIGNAL_REJECT": ("E4000", "sigma-core",
                                "Signalzeit gegen Kraken-Serverzeit pruefen (NTP/Clock)"),
    "ERR_SYS_RAM_SOFT_CAP": ("E5000", "sigma-core",
                             "Memory Watchdog Stufe erreicht; Chromium-Zombies reapen"),
    "ERR_SYS_DUCKDB_LOCK": ("E5000", "sigma-core",
                            "Konkurrierende DuckDB-Verbindung schliessen"),
    "ERR_SYS_OLLAMA_OFFLINE": ("E5000", "sigma-core",
                               "ollama serve starten (:11434)"),
    "ERR_SYS_UNHANDLED_EXCEPTION": ("E5000", "sigma-core",
                                    "errors.jsonl + Stacktrace pruefen"),
})
ERROR_SEVERITIES: Tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ERROR_TELEGRAM_PUSH_SEVERITIES: Tuple[str, ...] = ("HIGH", "CRITICAL")

# =============================================================================
# 37. Live Process & AI Log Console (§37, Axiom 15)
# =============================================================================

LOG_STREAM_ROUTE = "/api/v1/logs/stream"
LOG_VIEW_ROUTE = "/logs"
LOG_SOURCES: Mapping[str, str] = MappingProxyType({
    "CORE": "./data/logs/sigma_core.log",
    "ORDERS": "./data/logs/orders.jsonl",
    "TV_WORKER": "./data/logs/tv_worker.log",
    "ERRORS": "./data/logs/errors.jsonl",
    "AI_LAYER": "./data/logs/ai_layer.log",
    "SCRAPER": "./data/logs/scraper.log",
})
LOG_POLL_INTERVAL_MS = 250
LOG_CLIENT_RING_BUFFER_LINES = 2000
LOG_MASK_KEYS: Tuple[str, ...] = ("secret", "token", "api_key", "private_key", "password")

# =============================================================================
# 38. Netron ONNX Visualization (§38, Axiom 16)
# =============================================================================

PORT_NETRON = 8082
NETRON_DEFAULT_MODEL = "./models/regime_classifier.onnx"
NETRON_MODELS_DIR = "./models"
NETRON_BROWSE = False
NETRON_INSPECT_ROUTE = "/api/v1/models/inspect/{version_tag}"
NETRON_STATUS_ROUTE = "/api/v1/models/netron/status"
NETRON_BIND_PROD = "127.0.0.1"
NETRON_BIND_DEV = "0.0.0.0"
NETRON_ALLOWED_SUFFIX = ".onnx"
