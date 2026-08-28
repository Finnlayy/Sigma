"""
=========================================================
Datei:      app/core/l4_config.py
Zweck:      Loader für config/autonomy-level-4.yaml (§9) mit
            hart verdrahtetem Fallback aus app/core/blueprint.py.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================

Reihenfolge (Self-Healing-Prinzip, §16 analog für Config):
  1. Datei `config/autonomy-level-4.yaml` (oder $SIGMA_L4_CONFIG)
  2. Bei Fehlen / Parse-Fehler / fehlendem PyYAML:
     eingebauter Default aus `app/core/blueprint.py` (BUILTIN_L4_CONFIG)

Damit läuft der Core auch ohne Config-Datei deterministisch mit
Blueprint-Werten weiter — niemals mit stillen Zufallswerten.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Mapping

from app.core import blueprint as bp

logger = logging.getLogger("app.core.l4_config")

L4_CONFIG_ENV = "SIGMA_L4_CONFIG"
DEFAULT_L4_CONFIG_PATH = bp.PATH_L4_CONFIG


def _builtin() -> Dict[str, Any]:
    """Vollständiger Config-Baum, rein aus den hard-coded Blueprint-Werten."""
    return {
        "version": bp.BLUEPRINT_VERSION,
        "environment": "production",
        "source": "builtin_blueprint",
        "exchange": {
            "name": "kraken",
            "primary": bp.VIRTUAL_BOT_EXCHANGE_PRIMARY,
            "regulatory_region": bp.REGULATORY_REGION,
            "spot": {
                "enabled": bp.EXCHANGE_SPOT["enabled"],
                "allowed_symbols": list(bp.EXCHANGE_SPOT["allowed_symbols"]),
                "allowed_order_types": list(bp.EXCHANGE_SPOT["allowed_order_types"]),
                "max_order_notional_usd": bp.EXCHANGE_SPOT["max_order_notional_usd"],
                "max_daily_notional_usd": bp.EXCHANGE_SPOT["max_daily_notional_usd"],
                "symbol_mappings": dict(bp.EXCHANGE_SPOT["symbol_mappings"]),
            },
            "futures": {
                "enabled": bp.EXCHANGE_FUTURES["enabled"],
                "allowed_symbols": list(bp.EXCHANGE_FUTURES["allowed_symbols"]),
                "allowed_order_types": list(bp.EXCHANGE_FUTURES["allowed_order_types"]),
                "max_leverage": bp.EXCHANGE_FUTURES["max_leverage"],
                "max_order_notional_usd": bp.EXCHANGE_FUTURES["max_order_notional_usd"],
                "max_daily_notional_usd": bp.EXCHANGE_FUTURES["max_daily_notional_usd"],
                "symbol_mappings": dict(bp.EXCHANGE_FUTURES["symbol_mappings"]),
            },
            "pionex": {"enabled": bp.PIONEX_ENABLED_DEFAULT, "mode": bp.PIONEX_MODE_IF_ENABLED},
        },
        "risk_guard": {
            **{k: v for k, v in bp.RISK_GUARD.items()},
            "atr_stop_multiplier": bp.ATR_STOP_MULTIPLIER,
            "atr_take_profit_multiplier": bp.ATR_TAKE_PROFIT_MULTIPLIER,
            "symbol_halt_ttl_seconds": bp.SYMBOL_HALT_TTL_SECONDS,
        },
        "tv_automation": {
            "enabled": True,
            "driver": "playwright",
            "base_url": bp.TV_BASE_URL,
            "storage_state_path": bp.PATH_TV_STORAGE_STATE,
            "export_dir": bp.PATH_TV_EXPORTS,
            "max_concurrency": bp.TV_MAX_CONCURRENCY,
            "navigation_timeout_ms": bp.TV_NAVIGATION_TIMEOUT_MS,
            "tester_run_timeout_ms": bp.TV_TESTER_RUN_TIMEOUT_MS,
            "job_total_timeout_ms": bp.TV_JOB_TOTAL_TIMEOUT_MS,
            "alert_name_template": bp.ALERT_NAME_TEMPLATE,
            "selectors_path": bp.PATH_SELECTORS_YAML,
        },
        "optimizer": {
            "max_population": bp.GA_MAX_POPULATION,
            "max_generations": bp.GA_MAX_GENERATIONS,
            "early_termination_stall_generations": bp.GA_EARLY_STOP_STALL_GENERATIONS,
            "param_cache_required": bp.GA_PARAM_CACHE_REQUIRED,
            "concurrency": bp.GA_CONCURRENCY,
            "dsr_shadow_gate": bp.DSR_SHADOW_GATE,
            "min_trades": bp.MIN_TRADES_FOR_GATE,
        },
        "data_feed": {
            "market_source": bp.MARKET_SOURCE_PROD,
            "tradingview_scraper": {
                "enabled": True,
                "base_url": bp.SCRAPER_BASE_URL,
                "timeout_s": bp.SCRAPER_TIMEOUT_S,
            },
        },
        "quant": {
            "regime": {
                "ema_fast": bp.EMA_FAST_PERIOD,
                "ema_slow": bp.EMA_SLOW_PERIOD,
                "atr_period": bp.ATR_PERIOD,
                "atr_percentile_window": bp.ATR_PERCENTILE_WINDOW_BARS,
                "atr_pctl_compression_max": bp.ATR_PCTL_COMPRESSION_MAX,
                "atr_pctl_normal_max": bp.ATR_PCTL_NORMAL_MAX,
                "atr_pctl_crisis_min": bp.ATR_PCTL_CRISIS_MIN,
                "hurst_mean_reversion_max": bp.HURST_MEAN_REVERSION_MAX,
                "hurst_trend_min": bp.HURST_TREND_MIN,
            },
            "onnx": {
                "model_path": bp.PATH_ONNX_REGIME,
                "brier_drift_threshold": bp.BRIER_DRIFT_THRESHOLD,
                "temperature_default": bp.ONNX_TEMPERATURE_DEFAULT,
                "temperature_step": bp.ONNX_TEMPERATURE_STEP,
                "temperature_max": bp.ONNX_TEMPERATURE_MAX,
                "shadow_gate_before_hot_reload": bp.ONNX_SHADOW_GATE_BEFORE_HOT_RELOAD,
            },
        },
        "academy": {
            "badge_min_sample": bp.BADGE_MIN_SAMPLE,
            "badge_s_winrate_min": bp.BADGE_S_WINRATE_MIN,
            "badge_s_profit_factor_min": bp.BADGE_S_PROFIT_FACTOR_MIN,
            "badge_f_winrate_max": bp.BADGE_F_WINRATE_MAX,
            "badge_f_profit_factor_max": bp.BADGE_F_PROFIT_FACTOR_MAX,
            "allocator_model_path": bp.PATH_ONNX_ALLOCATOR,
        },
        "reward_shaping": {
            **{k: v for k, v in bp.REWARD_WEIGHTS.items()},
            "multiplier_s": bp.REWARD_MULTIPLIER_S,
            "multiplier_a": bp.REWARD_MULTIPLIER_A,
            "multiplier_b": bp.REWARD_MULTIPLIER_B,
            "multiplier_c": bp.REWARD_MULTIPLIER_C,
            "multiplier_f": bp.REWARD_MULTIPLIER_F,
            "strikes_to_quarantine": bp.STRIKES_TO_QUARANTINE,
        },
        "virtual_bots": {
            "sizing_basis": "bot_equity",
            "native_bracket_sl_required": bp.NATIVE_BRACKET_SL_REQUIRED,
            "vault_table": bp.VIRTUAL_BOT_VAULT_TABLE,
            "on_max_loss": {"m8_state": bp.M8State.QUARANTINED.value, "alert": bp.AlertAction.DISABLE.value},
        },
        "deadman_switch": {
            "heartbeat_seconds": bp.DEADMAN_HEARTBEAT_SECONDS_MAX,
            "timeout_seconds": bp.DEADMAN_TIMEOUT_SECONDS,
            "cancel_only_if_native_stop": bp.DEADMAN_CANCEL_ONLY_IF_NATIVE_STOP,
            "fallback_action": bp.DEADMAN_FALLBACK_ACTION,
        },
        "memory_watchdog": {
            "stages_pct": list(bp.MEMORY_STAGES_PCT),
            "actions": list(bp.MEMORY_STAGE_ACTIONS),
            "cgroup_memory_max": bp.MEMORY_CGROUP_MAX,
            "idle_only": bp.MEMORY_IDLE_ONLY,
            "idle_min_stage": bp.MEMORY_IDLE_MIN_STAGE,
            "housekeep_s": bp.MEMORY_HOUSEKEEP_S,
        },
        "llm": {
            "ollama_url": bp.OLLAMA_URL,
            "models": list(bp.OLLAMA_MODELS),
            "tools": list(bp.LLM_TOOLS),
        },
        "telegram": {
            "enabled": False,
            "fast_path_commands": list(bp.TELEGRAM_FAST_PATH_COMMANDS),
            "fast_path_budget_ms": bp.TELEGRAM_FAST_PATH_BUDGET_MS,
        },
        "webhook": {
            "route": bp.WEBHOOK_ROUTE,
            "secret_env": bp.WEBHOOK_SECRET_ENV,
            "secret_header": bp.WEBHOOK_SECRET_HEADER,
            "stale_min_seconds": bp.SIGNAL_STALE_MIN_SECONDS,
            "stale_interval_factor": bp.SIGNAL_STALE_INTERVAL_FACTOR,
        },
        "safety": {
            "kill_switch_file": bp.PATH_KILL_SWITCH,
            "pause_signal_file": bp.PATH_PAUSE,
            "halt_action": bp.HALT_ACTION,
            "audit_log_dir": "./data/logs",
            "live_trading_env": "SIGMA_LIVE_TRADING",
        },
        "m8": {
            "base_budget_usd": bp.M8_BASE_BUDGET_USD,
            "autopsy_order": bp.M8_AUTOPSY_ORDER,
            "throttled_budget_multiplier": bp.M8_ALERT_MATRIX[bp.M8State.THROTTLED].budget_multiplier,
        },
    }


BUILTIN_L4_CONFIG: Mapping[str, Any] = _builtin()

_lock = threading.Lock()
_cache: Dict[str, Any] | None = None


def config_path() -> str:
    return os.environ.get(L4_CONFIG_ENV, DEFAULT_L4_CONFIG_PATH)


def load_l4_config(path: str | None = None, *, force: bool = False) -> Dict[str, Any]:
    """Lädt die L4-YAML; fällt hart auf die Blueprint-Defaults zurück."""
    global _cache
    with _lock:
        if _cache is not None and not force and path is None:
            return _cache
        target = path or config_path()
        data: Dict[str, Any]
        try:
            import yaml  # type: ignore

            with open(target, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError("root of autonomy-level-4.yaml must be a mapping")
            data = _deep_merge(_builtin(), loaded)
            data["source"] = target
        except FileNotFoundError:
            logger.warning("L4 config %s missing — using builtin blueprint defaults", target)
            data = _builtin()
        except Exception as exc:  # ImportError (kein PyYAML), Parse-Fehler, Schema
            logger.warning("L4 config %s unusable (%s) — using builtin blueprint defaults", target, exc)
            data = _builtin()
        if path is None:
            _cache = data
        return data


def _deep_merge(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def get(dotted: str, default: Any = None) -> Any:
    """`get("risk_guard.max_daily_loss_usd")` → 600."""
    node: Any = load_l4_config()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
