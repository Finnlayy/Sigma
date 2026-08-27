"""
=========================================================
Datei:      app/core/config.py
Zweck:      Zentraler Konfigurations-Hub (Hot-Reload fähig)
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.core import blueprint as bp


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_first(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v is not None and str(v).strip() != "":
            return str(v)
    return default


@dataclass
class SigmaConfig:
    """Runtime configuration. Mutated in-place by SettingsEnvManager on hot-reload."""

    # --- Identity / environment -------------------------------------------------
    system_name: str = "Manas: Ciel Core Matrix (M8 Execution & Risk Architecture)"
    project_name: str = "Projekt:Sigma"
    spec_version: str = "sigma-hybrid-1.0"
    blueprint_version: str = bp.BLUEPRINT_VERSION
    masterprompt_version: str = bp.MASTERPROMPT_VERSION
    autonomy_level: int = bp.AUTONOMY_LEVEL
    skeleton_version: str = "1.6.4"
    host_label: str = "core"  # Ubuntu only

    # --- Redis ------------------------------------------------------------------
    redis_url: str = "redis://127.0.0.1:6379/0"
    allow_fakeredis: bool = True

    # --- Data lake ----------------------------------------------------------------
    data_dir: str = "data"
    duckdb_path: str = ""
    parquet_dir: str = ""
    duckdb_memory_limit: str = "2GB"
    duckdb_threads: int = 4

    # --- M8 state machine (inherited from Alpha v1.2.0 frozen) ----------------------
    base_budget_usd: float = 50.0
    throttle_budget_pct: float = 0.5
    activate_budget_pct: float = 0.5
    use_v164_promotion: bool = False
    v164_promotion_pct: float = 0.8
    low_pf_throttle_days: int = 3
    low_pf_quarantine_days: int = 7
    retired_shadow_weeks: int = 4
    vault_sweep_enabled: bool = True
    autopsy_order: str = "v1.2.0"

    # --- Risk / sizing -------------------------------------------------------------
    max_allowed_leverage: float = 10.0
    spot_max_leverage: float = 1.0
    maintenance_margin_rate: float = 0.005
    clearance_fee_rate: float = 0.0075
    risk_fraction_per_trade: float = 0.20
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0005

    # --- TradeChurnGuard ----------------------------------------------------------
    churn_min_holding_seconds: int = 180
    churn_cooldown_seconds: int = 300
    churn_max_daily_trades: int = 12
    churn_fee_hurdle_multiple: float = 2.5

    # --- Ingestion / market ---------------------------------------------------------
    market_symbols: tuple = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")
    market_source: str = "synthetic"
    tick_interval_seconds: float = 1.5
    candle_interval_sec: int = 60
    seed_candle_count: int = 7560

    # --- Paper portfolio ----------------------------------------------------------
    paper_baseline_usd: float = 190412.50
    paper_seeds: tuple = (
        "USD:50000", "BTC:1.5", "ETH:10", "SOL:100", "XRP:5000"
    )
    paper_symbol_prices: tuple = (
        "BTC/USD:97000", "ETH/USD:3400", "SOL/USD:180", "XRP/USD:2.20"
    )

    # --- Security ---------------------------------------------------------------------
    rp_id: str = "localhost"
    rp_origin: str = "http://localhost:3000"
    settings_token_ttl_seconds: int = 3600
    webauthn_degraded_fallback: bool = True

    # --- Integrations -------------------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    mcp_tool_count: int = 149

    # --- TradingView MCP (CSV seam) ---------------------------------------------------
    tv_mcp_url: str = "fake"  # set SIGMA_TV_MCP_URL for live; 'fake' for sandbox/tests
    tv_mcp_timeout_s: float = 120.0
    tv_mcp_concurrency: int = 4

    # --- GA / Academy ---------------------------------------------------------------------
    ga_min_trades_absolute: int = 30
    ga_min_trades_target: int = 80
    ga_max_allowed_rules: int = 6
    ga_fitness_threshold: float = 0.35
    ga_dsr_gate: float = 0.95
    ga_cadence_min: float = 3.0
    ga_cadence_max: float = 6.0

    # --- Runtime ---------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = bp.PORT_CORE
    sse_interval_seconds: float = 2.0
    log_level: str = "INFO"

    # =====================================================================
    # Blueprint L4 hard-coded surface (docs/BLUEPRINT-SIGMA.md v3.0)
    # Alle Defaults stammen aus app/core/blueprint.py — keine Magic Numbers.
    # =====================================================================

    # --- §1 Prozesse / Pfade ------------------------------------------------
    install_root: str = bp.INSTALL_ROOT
    l4_config_path: str = bp.PATH_L4_CONFIG
    kill_switch_file: str = bp.PATH_KILL_SWITCH
    pause_signal_file: str = bp.PATH_PAUSE
    orders_log_path: str = bp.PATH_LOG_ORDERS
    strategies_dir: str = bp.PATH_STRATEGIES

    # --- §4 Loop A: Webhook / Safety / Sizing ---------------------------------
    webhook_route: str = bp.WEBHOOK_ROUTE
    webhook_secret: str = ""
    live_trading: bool = False
    kelly_fraction: float = bp.KELLY_FRACTION
    max_portfolio_risk_per_trade: float = bp.MAX_PORTFOLIO_RISK_PER_TRADE
    atr_stop_multiplier: float = bp.ATR_STOP_MULTIPLIER
    atr_take_profit_multiplier: float = bp.ATR_TAKE_PROFIT_MULTIPLIER
    max_open_positions: int = int(bp.RISK_GUARD["max_open_positions"])
    max_daily_loss_usd: float = float(bp.RISK_GUARD["max_daily_loss_usd"])
    max_consecutive_errors: int = int(bp.RISK_GUARD["max_consecutive_errors"])
    max_spread_bps: float = float(bp.RISK_GUARD["max_spread_bps"])
    symbol_halt_ttl_seconds: int = bp.SYMBOL_HALT_TTL_SECONDS
    halt_action: str = bp.HALT_ACTION

    # --- §5/§17.4 Loop B: TV Automation + GA-Härtung ---------------------------
    tv_base_url: str = bp.TV_BASE_URL
    tv_storage_state_path: str = bp.PATH_TV_STORAGE_STATE
    tv_export_dir: str = bp.PATH_TV_EXPORTS
    tv_jobs_dir: str = bp.PATH_TV_JOBS
    tv_max_concurrency: int = bp.TV_MAX_CONCURRENCY
    tv_navigation_timeout_ms: int = bp.TV_NAVIGATION_TIMEOUT_MS
    tv_tester_run_timeout_ms: int = bp.TV_TESTER_RUN_TIMEOUT_MS
    tv_job_total_timeout_ms: int = bp.TV_JOB_TOTAL_TIMEOUT_MS
    ga_max_population: int = bp.GA_MAX_POPULATION
    ga_max_generations: int = bp.GA_MAX_GENERATIONS
    ga_early_stop_stall_generations: int = bp.GA_EARLY_STOP_STALL_GENERATIONS
    ga_param_cache_required: bool = bp.GA_PARAM_CACHE_REQUIRED

    # --- §6 Loop C: Scraper Sidecar -------------------------------------------
    tv_scraper_url: str = bp.SCRAPER_BASE_URL
    tv_scraper_timeout_s: float = float(bp.SCRAPER_TIMEOUT_S)

    # --- §21 Quant / ONNX ------------------------------------------------------
    onnx_model_path: str = bp.PATH_ONNX_REGIME
    brier_drift_threshold: float = bp.BRIER_DRIFT_THRESHOLD
    onnx_temperature: float = bp.ONNX_TEMPERATURE_DEFAULT

    # --- §18 Academy / Badges ---------------------------------------------------
    badge_min_sample: int = bp.BADGE_MIN_SAMPLE

    # --- §20 Virtual Bots / Deadman ---------------------------------------------
    exchange_primary: str = bp.VIRTUAL_BOT_EXCHANGE_PRIMARY
    regulatory_region: str = bp.REGULATORY_REGION
    pionex_enabled: bool = bp.PIONEX_ENABLED_DEFAULT
    native_bracket_sl_required: bool = bp.NATIVE_BRACKET_SL_REQUIRED
    deadman_timeout_seconds: int = bp.DEADMAN_TIMEOUT_SECONDS

    # --- §16 Self-Healing Selectors ------------------------------------------------
    selectors_path: str = bp.PATH_SELECTORS_YAML
    selectors_remote_url: str = ""
    selectors_sha256: str = ""

    # --- §21 Memory Watchdog / LLM -----------------------------------------------
    memory_cgroup_max: str = bp.MEMORY_CGROUP_MAX
    ollama_url: str = bp.OLLAMA_URL

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def resolved_duckdb_path(self) -> str:
        return self.duckdb_path or os.path.join(self.data_dir, "sigma.duckdb")

    @property
    def resolved_parquet_dir(self) -> str:
        return self.parquet_dir or os.path.join(self.data_dir, "lake")

    def snapshot(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def reload_from_env(self) -> None:
        with self._lock:
            self.data_dir = _env_first("SIGMA_DATA_DIR", "ALPHA_DATA_DIR", default=self.data_dir)
            self.redis_url = _env_first("SIGMA_REDIS_URL", "ALPHA_REDIS_URL", default=self.redis_url)
            self.allow_fakeredis = _env_bool(
                "SIGMA_ALLOW_FAKE_REDIS",
                _env_bool("ALPHA_ALLOW_FAKE_REDIS", self.allow_fakeredis),
            )
            self.base_budget_usd = _env_float(
                "SIGMA_BASE_BUDGET_USD",
                _env_float("ALPHA_BASE_BUDGET_USD", self.base_budget_usd),
            )
            self.use_v164_promotion = _env_bool(
                "SIGMA_USE_V164_PROMOTION",
                _env_bool("ALPHA_USE_V164_PROMOTION", self.use_v164_promotion),
            )
            self.vault_sweep_enabled = _env_bool(
                "SIGMA_VAULT_SWEEP",
                _env_bool("ALPHA_VAULT_SWEEP", self.vault_sweep_enabled),
            )
            self.autopsy_order = _env_first("SIGMA_AUTOPSY_ORDER", "ALPHA_AUTOPSY_ORDER", default=self.autopsy_order)
            self.max_allowed_leverage = _env_float(
                "SIGMA_MAX_LEVERAGE",
                _env_float("ALPHA_MAX_LEVERAGE", self.max_allowed_leverage),
            )
            self.risk_fraction_per_trade = _env_float(
                "SIGMA_RISK_FRACTION",
                _env_float("ALPHA_RISK_FRACTION", self.risk_fraction_per_trade),
            )
            self.maker_fee_rate = _env_float("SIGMA_MAKER_FEE", _env_float("ALPHA_MAKER_FEE", self.maker_fee_rate))
            self.taker_fee_rate = _env_float("SIGMA_TAKER_FEE", _env_float("ALPHA_TAKER_FEE", self.taker_fee_rate))
            self.churn_cooldown_seconds = _env_int(
                "SIGMA_CHURN_COOLDOWN_S",
                _env_int("ALPHA_CHURN_COOLDOWN_S", self.churn_cooldown_seconds),
            )
            self.churn_max_daily_trades = _env_int(
                "SIGMA_CHURN_MAX_DAILY",
                _env_int("ALPHA_CHURN_MAX_DAILY", self.churn_max_daily_trades),
            )
            self.churn_fee_hurdle_multiple = _env_float(
                "SIGMA_FEE_HURDLE_MULT",
                _env_float("ALPHA_FEE_HURDLE_MULT", self.churn_fee_hurdle_multiple),
            )
            self.market_source = _env_first("SIGMA_MARKET_SOURCE", "ALPHA_MARKET_SOURCE", default=self.market_source)
            self.log_level = _env_first("SIGMA_LOG_LEVEL", "ALPHA_LOG_LEVEL", default=self.log_level)
            self.telegram_bot_token = _env("TELEGRAM_BOT_TOKEN", self.telegram_bot_token)
            self.telegram_chat_id = _env("TELEGRAM_CHAT_ID", self.telegram_chat_id)
            self.tv_mcp_url = _env("SIGMA_TV_MCP_URL", self.tv_mcp_url)
            self.tv_mcp_timeout_s = _env_float("SIGMA_TV_MCP_TIMEOUT_S", self.tv_mcp_timeout_s)
            self.tv_mcp_concurrency = _env_int("SIGMA_TV_MCP_CONCURRENCY", self.tv_mcp_concurrency)

            # --- Blueprint L4 env matrix (§9 Env-Matrix) --------------------
            self.webhook_secret = _env(bp.WEBHOOK_SECRET_ENV, self.webhook_secret)
            self.live_trading = _env_bool("SIGMA_LIVE_TRADING", self.live_trading)
            self.l4_config_path = _env("SIGMA_L4_CONFIG", self.l4_config_path)
            self.tv_scraper_url = _env("SIGMA_TV_SCRAPER_URL", self.tv_scraper_url)
            self.tv_scraper_timeout_s = _env_float("SIGMA_TV_SCRAPER_TIMEOUT_S", self.tv_scraper_timeout_s)
            self.tv_storage_state_path = _env("SIGMA_TV_STORAGE_STATE", self.tv_storage_state_path)
            self.tv_export_dir = _env("SIGMA_TV_EXPORT_DIR", self.tv_export_dir)
            self.tv_max_concurrency = _env_int("SIGMA_TV_CONCURRENCY", self.tv_max_concurrency)
            self.onnx_model_path = _env("SIGMA_ONNX_MODEL_PATH", self.onnx_model_path)
            self.selectors_path = _env(bp.SELECTORS_LOCAL_PATH_ENV, self.selectors_path)
            self.selectors_remote_url = _env(bp.SELECTORS_REMOTE_URL_ENV, self.selectors_remote_url)
            self.selectors_sha256 = _env(bp.SELECTORS_SHA256_ENV, self.selectors_sha256)
            self.kelly_fraction = _env_float("SIGMA_KELLY_FRACTION", self.kelly_fraction)
            self.max_daily_loss_usd = _env_float("SIGMA_MAX_DAILY_LOSS_USD", self.max_daily_loss_usd)
            self.max_open_positions = _env_int("SIGMA_MAX_OPEN_POSITIONS", self.max_open_positions)
            self.ga_max_population = min(
                _env_int("SIGMA_GA_MAX_POPULATION", self.ga_max_population), bp.GA_MAX_POPULATION
            )
            self.ga_max_generations = min(
                _env_int("SIGMA_GA_MAX_GENERATIONS", self.ga_max_generations), bp.GA_MAX_GENERATIONS
            )
            self.ollama_url = _env("SIGMA_OLLAMA_URL", self.ollama_url)
            self.pionex_enabled = _env_bool("SIGMA_PIONEX_ENABLED", self.pionex_enabled)
            # §17.4: Playwright bleibt serialisiert — Env darf das nicht aufweichen
            self.tv_max_concurrency = max(1, min(self.tv_max_concurrency, bp.TV_MAX_CONCURRENCY))


# Back-compat alias for imports still naming AlphaConfig
AlphaConfig = SigmaConfig


def load_config() -> SigmaConfig:
    cfg = SigmaConfig()
    cfg.reload_from_env()
    return cfg
