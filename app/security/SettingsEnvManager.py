"""
=========================================================
Datei:      app/security/SettingsEnvManager.py
Zweck:      Full CRUD & Hot-Reload für `.env`-Variablen in der App UI
Knoten:     Jaune (Carrera-Engine)
=========================================================
Mutative Schreibzugriffe erfordern ein gültiges settingsToken
(Passkey-Gate, Blueprint: 'Passkey-gated settings and mutative commands').
Loopback (127.0.0.1) darf ohne Token schreiben — lokaler Operator.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.core.config import AlphaConfig

logger = logging.getLogger("app.security.settings_manager")

ENV_MAP: Dict[str, str] = {
    "ALPHA_BASE_BUDGET_USD": "M8 Basis-Budget (USD)",
    "ALPHA_MAX_LEVERAGE": "Max. Perpetual-Hebel (1–10)",
    "ALPHA_RISK_FRACTION": "Risiko-Quote pro Trade (0–1)",
    "ALPHA_MAKER_FEE": "Maker-Fee (z.B. 0.0002)",
    "ALPHA_TAKER_FEE": "Taker-Fee (z.B. 0.0005)",
    "ALPHA_CHURN_COOLDOWN_S": "Churn-Cooldown (Sekunden)",
    "ALPHA_CHURN_MAX_DAILY": "Max. Trades pro Tag",
    "ALPHA_FEE_HURDLE_MULT": "Fee-Hurdle-Multiplikator (2.5x)",
    "ALPHA_MARKET_SOURCE": "Feed-Quelle (synthetic | ccxt_ws)",
    "ALPHA_AUTOPSY_ORDER": "Autopsy-Order (v1.2.0 | v1.6.4)",
    "ALPHA_LOG_LEVEL": "Log-Level (DEBUG|INFO|WARNING)",
}

SECRET_MAP: Dict[str, str] = {
    "KRAKEN_API_KEY": "Kraken API Key",
    "KRAKEN_API_SECRET": "Kraken API Secret",
    "SIGMA_WEBHOOK_SECRET": "TradingView Webhook Secret",
    "TELEGRAM_BOT_TOKEN": "Telegram Bot Token",
    "TELEGRAM_CHAT_ID": "Telegram Chat ID",
}

RUNTIME_MAP: Dict[str, str] = {
    "SIGMA_LIVE_TRADING": "Live Trading (0 = paper, 1 = live)",
    "SIGMA_MARKET_SOURCE": "Market source (synthetic | ccxt_ws)",
    "SIGMA_TV_MCP_URL": "TradingView MCP URL (fake = sandbox)",
    "SIGMA_OLLAMA_URL": "Ollama URL",
    "SIGMA_PUBLIC_URL": "Public webhook base URL",
}

WRITABLE: Dict[str, str] = {**ENV_MAP, **SECRET_MAP, **RUNTIME_MAP}
SENSITIVE_PREFIXES = ("TELEGRAM_", "KRAKEN_", "ALPHA_WEBAUTHN_", "SIGMA_WEBHOOK_")


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return value[:2] + "…" + value[-2:]


def _is_sensitive(key: str) -> bool:
    return key in SECRET_MAP or any(key.startswith(p) for p in SENSITIVE_PREFIXES)


def _group(key: str) -> str:
    if key in SECRET_MAP:
        return "secrets"
    if key in RUNTIME_MAP:
        return "runtime"
    return "risk"


class SettingsEnvManager:
    def __init__(self, config: AlphaConfig, env_file: Optional[str] = None):
        self.config = config
        self.env_file = env_file or os.path.join(os.getcwd(), ".env")

    # --------------------------------------------------------------------- read
    def get_all(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        seen = set()
        for key, label in WRITABLE.items():
            value = os.environ.get(key, "")
            sensitive = _is_sensitive(key)
            rows.append({
                "key": key,
                "label": label,
                "group": _group(key),
                "value": _mask(value) if sensitive and value else (value or self._default_for(key)),
                "isMasked": bool(sensitive and value),
                "setInEnv": bool(value),
            })
            seen.add(key)
        for k in os.environ:
            if k in seen:
                continue
            if any(k.startswith(p) for p in SENSITIVE_PREFIXES):
                rows.append({
                    "key": k,
                    "label": "Secret (masked)",
                    "group": "secrets",
                    "value": "****" if os.environ.get(k) else "",
                    "isMasked": True,
                    "setInEnv": bool(os.environ.get(k)),
                })
        return rows

    def _default_for(self, key: str) -> str:
        defaults = {
            "ALPHA_BASE_BUDGET_USD": str(self.config.base_budget_usd),
            "ALPHA_MAX_LEVERAGE": str(self.config.max_allowed_leverage),
            "ALPHA_RISK_FRACTION": str(self.config.risk_fraction_per_trade),
            "ALPHA_MAKER_FEE": str(self.config.maker_fee_rate),
            "ALPHA_TAKER_FEE": str(self.config.taker_fee_rate),
            "ALPHA_CHURN_COOLDOWN_S": str(self.config.churn_cooldown_seconds),
            "ALPHA_CHURN_MAX_DAILY": str(self.config.churn_max_daily_trades),
            "ALPHA_FEE_HURDLE_MULT": str(self.config.churn_fee_hurdle_multiple),
            "ALPHA_MARKET_SOURCE": self.config.market_source,
            "ALPHA_AUTOPSY_ORDER": self.config.autopsy_order,
            "ALPHA_LOG_LEVEL": self.config.log_level,
            "SIGMA_LIVE_TRADING": "1" if self.config.live_trading else "0",
            "SIGMA_MARKET_SOURCE": self.config.market_source,
            "SIGMA_TV_MCP_URL": self.config.tv_mcp_url,
            "SIGMA_OLLAMA_URL": self.config.ollama_url,
        }
        return defaults.get(key, "")

    # -------------------------------------------------------------------- write
    def update(self, key: str, value: str) -> Dict[str, Any]:
        if key not in WRITABLE:
            raise ValueError(f"Key '{key}' ist nicht für die UI freigegeben.")
        os.environ[key] = str(value)
        self._write_env_file(key, str(value))
        self.config.reload_from_env()
        if _is_sensitive(key):
            logger.info("Hot-Reload: %s → [redacted]", key)
            return {"key": key, "value": "****", "applied": True, "hotReloaded": True, "masked": True}
        logger.info("Hot-Reload: %s → %s", key, value)
        return {"key": key, "value": str(value), "applied": True, "hotReloaded": True}

    def delete(self, key: str) -> Dict[str, Any]:
        if key not in WRITABLE:
            raise ValueError(f"Key '{key}' ist nicht für die UI freigegeben.")
        os.environ.pop(key, None)
        self._write_env_file(key, None)
        self.config.reload_from_env()
        return {"key": key, "applied": True, "hotReloaded": True}

    def _write_env_file(self, key: str, value: Optional[str]) -> None:
        try:
            lines: List[str] = []
            if os.path.exists(self.env_file):
                with open(self.env_file, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            found = False
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{key}="):
                    lines[i] = f"{key}={value}" if value is not None else f"# {key}=<deleted>"
                    found = True
                    break
            if not found:
                lines.append(f"{key}={value}" if value is not None else f"# {key}=<deleted>")
            with open(self.env_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:
            logger.warning("env file write failed: %s", exc)
