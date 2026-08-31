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
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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
    # MP-06 Production Wire — Polymarket Gamma-Feed (nur Telemetrie)
    "POLYMARKET_GAMMA_URL": "Polymarket Gamma-API Basis-URL",
    "POLYMARKET_MIN_VOLUME_USD": "Gamma-Minimum 24h-Volumen (USD) fuer Strike-Leiter",
    "POLYMARKET_TTL_S": "Gamma-Snapshot-TTL (Sekunden)",
}

WRITABLE: Dict[str, str] = {**ENV_MAP, **SECRET_MAP, **RUNTIME_MAP}
SENSITIVE_PREFIXES = ("TELEGRAM_", "KRAKEN_", "ALPHA_WEBAUTHN_", "SIGMA_WEBHOOK_")

# kind: enum | float | int | url | flag | secret | text
SETTING_SPECS: Dict[str, Dict[str, Any]] = {
    "ALPHA_BASE_BUDGET_USD": {
        "kind": "float", "min": 1, "max": 1_000_000,
        "format": "positive Zahl, USD",
        "hint": "Basis-Budget als Dezimalzahl, z. B. 50 oder 50.0. Bereich: 1–1000000.",
    },
    "ALPHA_MAX_LEVERAGE": {
        "kind": "float", "min": 1, "max": 10,
        "format": "Zahl 1–10",
        "hint": "Hebel als Dezimalzahl, z. B. 5 oder 5.0. Erlaubt: 1 bis 10.",
    },
    "ALPHA_RISK_FRACTION": {
        "kind": "float", "min": 0, "max": 1,
        "format": "Zahl 0–1",
        "hint": "Risiko-Quote pro Trade, z. B. 0.15. Erlaubt: 0 bis 1.",
    },
    "ALPHA_MAKER_FEE": {
        "kind": "float", "min": 0, "max": 0.05,
        "format": "Dezimalbruch, z. B. 0.0002",
        "hint": "Maker-Fee als Bruch, z. B. 0.0002 (= 2 bps). Bereich: 0–0.05.",
    },
    "ALPHA_TAKER_FEE": {
        "kind": "float", "min": 0, "max": 0.05,
        "format": "Dezimalbruch, z. B. 0.0005",
        "hint": "Taker-Fee als Bruch, z. B. 0.0005. Bereich: 0–0.05.",
    },
    "ALPHA_CHURN_COOLDOWN_S": {
        "kind": "int", "min": 0, "max": 86400,
        "format": "ganze Sekunden",
        "hint": "Cooldown in ganzen Sekunden, z. B. 60. Bereich: 0–86400.",
    },
    "ALPHA_CHURN_MAX_DAILY": {
        "kind": "int", "min": 0, "max": 10000,
        "format": "ganze Zahl",
        "hint": "Max. Trades pro Tag als ganze Zahl, z. B. 40. Bereich: 0–10000.",
    },
    "ALPHA_FEE_HURDLE_MULT": {
        "kind": "float", "min": 0, "max": 50,
        "format": "Zahl, z. B. 2.5",
        "hint": "Fee-Hurdle-Multiplikator, z. B. 2.5. Bereich: 0–50.",
    },
    "ALPHA_MARKET_SOURCE": {
        "kind": "enum", "allowed": ["synthetic", "ccxt_ws"],
        "format": "synthetic | ccxt_ws",
        "hint": "Nur diese Werte: synthetic (Sandbox-Feed) oder ccxt_ws (Live-Stream).",
    },
    "ALPHA_AUTOPSY_ORDER": {
        "kind": "enum", "allowed": ["v1.2.0", "v1.6.4"],
        "format": "v1.2.0 | v1.6.4",
        "hint": "Nur diese Werte: v1.2.0 oder v1.6.4.",
    },
    "ALPHA_LOG_LEVEL": {
        "kind": "enum", "allowed": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "format": "DEBUG | INFO | WARNING | ERROR",
        "hint": "Log-Level exakt in Großbuchstaben: DEBUG, INFO, WARNING oder ERROR.",
    },
    "SIGMA_LIVE_TRADING": {
        "kind": "flag", "allowed": ["0", "1"],
        "format": "0 oder 1",
        "hint": "0 = Paper, 1 = Live. Keine anderen Werte (nicht true/yes).",
    },
    "SIGMA_MARKET_SOURCE": {
        "kind": "enum", "allowed": ["synthetic", "ccxt_ws"],
        "format": "synthetic | ccxt_ws",
        "hint": "Nur diese Werte: synthetic oder ccxt_ws.",
    },
    "SIGMA_TV_MCP_URL": {
        "kind": "url",
        "allowed": ["fake", "mock", "test://fake"],
        "format": "fake | https://…",
        "hint": "Sandbox: fake (oder mock, test://fake). Live: volle http(s)-URL zum TradingView-MCP.",
    },
    "SIGMA_OLLAMA_URL": {
        "kind": "url",
        "format": "http://host:port",
        "hint": "Ollama-Base-URL, z. B. http://127.0.0.1:11434 — muss mit http:// oder https:// beginnen.",
    },
    "SIGMA_PUBLIC_URL": {
        "kind": "url",
        "format": "http://host:port",
        "hint": "Öffentliche Webhook-Basis, z. B. http://127.0.0.1:8080 — muss mit http:// oder https:// beginnen.",
    },
    "KRAKEN_API_KEY": {
        "kind": "secret",
        "format": "nicht-leerer Key",
        "hint": "Kraken API Key einfügen (nicht leer). Bereits gesetzte Keys bleiben maskiert.",
    },
    "KRAKEN_API_SECRET": {
        "kind": "secret",
        "format": "nicht-leeres Secret",
        "hint": "Kraken API Secret einfügen (nicht leer).",
    },
    "SIGMA_WEBHOOK_SECRET": {
        "kind": "secret",
        "format": "nicht-leerer String",
        "hint": "Webhook-Secret als beliebiger nicht-leerer String.",
    },
    "TELEGRAM_BOT_TOKEN": {
        "kind": "text",
        "format": "123456:AA…",
        "hint": "Telegram-Bot-Token im Format <id>:<secret>, z. B. 123456789:AAH…",
    },
    "TELEGRAM_CHAT_ID": {
        "kind": "text",
        "format": "Zahl, optional negativ",
        "hint": "Chat-ID als ganze Zahl, z. B. 123456789 oder -1001234567890 für Gruppen.",
    },
}


class SettingValidationError(ValueError):
    def __init__(self, message: str, *, hint: str = "", format: str = "", allowed: Optional[List[str]] = None):
        super().__init__(message)
        self.hint = hint or message
        self.format = format
        self.allowed = list(allowed or [])


def spec_for(key: str) -> Dict[str, Any]:
    return dict(SETTING_SPECS.get(key) or {})


def _as_public_spec(key: str) -> Dict[str, Any]:
    spec = spec_for(key)
    if not spec:
        return {}
    return {
        "kind": spec.get("kind"),
        "format": spec.get("format") or "",
        "hint": spec.get("hint") or "",
        "allowed": list(spec.get("allowed") or []),
        "min": spec.get("min"),
        "max": spec.get("max"),
    }


def validate_setting(key: str, value: str) -> None:
    raw = "" if value is None else str(value).strip()
    spec = spec_for(key)
    hint = spec.get("hint") or f"Ungültiger Wert für {key}."
    fmt = spec.get("format") or ""
    allowed = list(spec.get("allowed") or [])
    kind = spec.get("kind") or "text"

    if not raw:
        raise SettingValidationError(
            f"{key}: Wert fehlt.", hint=hint, format=fmt, allowed=allowed,
        )

    if kind == "enum":
        if not any(a.lower() == raw.lower() for a in allowed):
            raise SettingValidationError(
                f"{key}: '{raw}' ist kein erlaubter Wert.",
                hint=hint, format=fmt, allowed=allowed,
            )
        return

    if kind == "flag":
        if raw not in ("0", "1"):
            raise SettingValidationError(
                f"{key}: nur 0 oder 1.", hint=hint, format=fmt, allowed=["0", "1"],
            )
        return

    if kind == "int":
        if not re.fullmatch(r"-?\d+", raw):
            raise SettingValidationError(
                f"{key}: erwartet ganze Zahl.", hint=hint, format=fmt, allowed=allowed,
            )
        n = int(raw)
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and n < lo or hi is not None and n > hi:
            raise SettingValidationError(
                f"{key}: {n} außerhalb {lo}–{hi}.", hint=hint, format=fmt, allowed=allowed,
            )
        return

    if kind == "float":
        try:
            n = float(raw)
        except ValueError as exc:
            raise SettingValidationError(
                f"{key}: erwartet Zahl.", hint=hint, format=fmt, allowed=allowed,
            ) from exc
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and n < lo or hi is not None and n > hi:
            raise SettingValidationError(
                f"{key}: {n} außerhalb {lo}–{hi}.", hint=hint, format=fmt, allowed=allowed,
            )
        return

    if kind == "url":
        if raw.lower() in {a.lower() for a in allowed}:
            return
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise SettingValidationError(
                f"{key}: keine gültige URL.", hint=hint, format=fmt, allowed=allowed,
            )
        return

    if kind == "secret":
        return

    if key == "TELEGRAM_BOT_TOKEN" and not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", raw):
        raise SettingValidationError(
            f"{key}: Token-Format ungültig.", hint=hint, format=fmt, allowed=allowed,
        )
    if key == "TELEGRAM_CHAT_ID" and not re.fullmatch(r"-?\d+", raw):
        raise SettingValidationError(
            f"{key}: Chat-ID muss eine Zahl sein.", hint=hint, format=fmt, allowed=allowed,
        )


def _canonicalize(key: str, value: str) -> str:
    raw = str(value).strip()
    spec = spec_for(key)
    allowed = list(spec.get("allowed") or [])
    kind = spec.get("kind")
    if kind in ("enum", "url") and allowed:
        for item in allowed:
            if item.lower() == raw.lower():
                return item
    return raw


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
                **_as_public_spec(key),
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
        validate_setting(key, value)
        value = _canonicalize(key, value)
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
