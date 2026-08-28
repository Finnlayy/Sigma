"""
=========================================================
Datei:      app/server/schemas.py
Zweck:      §33 / Axiom 11 — Standardisierte Webhook-Alert-Schemata
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / API
=========================================================

Drei Schema-Familien; der Ingestion-Router auf :8000 erkennt das Format
und validiert strikt (Pydantic V2, ``extra="forbid"``):

* **Schema A — Sigma L4 Master** (`SigmaL4AlertPayload`): Kraken Live & Paper.
  ``secret``, ``idempotency_key``, ``bot_id``, ``stop_loss``, ``fixed_leverage``,
  optional ``features`` (Schema C embedded).
* **Schema B — Pionex Native** (`PionexSignalPayload`): nur wenn der Connector
  aktiviert ist (DE-Default: aus).
* **Schema C — ML/Kausal-Telemetrie** (`MLFeaturePayload`): RSI, ATR,
  CISD-Score, BB-Bandwidth.

Kein raw-``dict``-Webhook mehr: die Routen konsumieren ausschliesslich diese
Modelle. Fehlerpfade liefern strukturierte Codes (in §36 an die Error-Engine
verdrahtet).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core import blueprint as bp

# --------------------------------------------------------------------------- #
# Fehlercodes, die die Schema-Schicht selbst erzeugen kann (§36-Vorbereitung)
# --------------------------------------------------------------------------- #

ERR_SCHEMA_UNKNOWN = "ERR_TV_WEBHOOK_SCHEMA_UNKNOWN"
ERR_SCHEMA_INVALID = "ERR_TV_WEBHOOK_SCHEMA_INVALID"
ERR_AUTH_INVALID_SECRET = "ERR_AUTH_INVALID_SECRET"
ERR_PIONEX_DISABLED = "ERR_TV_PIONEX_CONNECTOR_DISABLED"

_SYMBOL_PREFIX = re.compile(r"^[A-Z0-9_]+:")


def normalize_symbol(raw: str) -> str:
    """``KRAKEN:XRPUSD.P`` -> ``XRPUSD`` (Exchange-Prefix und Perp-Suffix weg)."""
    symbol = (raw or "").strip().upper()
    symbol = _SYMBOL_PREFIX.sub("", symbol)
    if symbol.endswith(".P"):
        symbol = symbol[:-2]
    return symbol


def normalize_epoch(value: Any) -> int:
    """TV liefert ``{{timenow}}`` teils in Millisekunden — auf Sekunden bringen."""
    ts = int(float(value))
    if ts > 100_000_000_000:      # Millisekunden
        ts //= 1000
    return ts


class StrictModel(BaseModel):
    """Basis: unbekannte Felder sind ein Vertragsbruch, kein Warnhinweis."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# =============================================================================
# Schema C — ML / Kausal-Telemetrie (§33.3)
# =============================================================================

class MLFeaturePayload(StrictModel):
    """Feature-Block fuer ONNX-Inferenz und Academy-Autopsie."""

    rsi: float = Field(..., ge=0.0, le=100.0)
    atr: float = Field(..., gt=0.0)
    cisd_score: Optional[float] = Field(default=0.5, ge=0.0, le=1.0)
    bb_bandwidth: Optional[float] = Field(default=0.0, ge=0.0)
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    regime: Optional[str] = None
    glint_score: Optional[float] = Field(default=None, ge=0.0, le=10.0)


# =============================================================================
# Schema A — Sigma L4 Master Signal (§33.1)
# =============================================================================

class SigmaL4AlertPayload(StrictModel):
    """Kanonischer Kraken-Alert (Live & Paper)."""

    secret: str = Field(..., min_length=bp.SIGMA_SECRET_MIN_LENGTH)
    idempotency_key: str = Field(..., min_length=bp.IDEMPOTENCY_KEY_MIN_LENGTH)
    strategy_id: str = Field(..., min_length=1)
    bot_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    action: Literal["BUY", "SELL", "CLOSE"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: float = Field(..., gt=0.0)
    stop_loss: float = Field(..., gt=0.0)
    take_profit: Optional[float] = Field(default=None, gt=0.0)
    fixed_leverage: int = Field(default=bp.FIXED_LEVERAGE_DEFAULT,
                                ge=bp.FIXED_LEVERAGE_MIN, le=bp.FIXED_LEVERAGE_MAX)
    timestamp: int
    interval: Optional[str] = None
    execution_mode: Literal["live", "kraken_paper", "hybrid_scout"] = \
        bp.EXECUTION_MODE_DEFAULT
    features: Optional[MLFeaturePayload] = None

    @field_validator("symbol")
    @classmethod
    def _strip_exchange_prefix(cls, value: str) -> str:
        symbol = normalize_symbol(value)
        if not symbol:
            raise ValueError("symbol darf nach Normalisierung nicht leer sein")
        return symbol

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ms_to_seconds(cls, value: Any) -> int:
        return normalize_epoch(value)

    @field_validator("action", "order_type", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _bracket_is_plausible(self) -> "SigmaL4AlertPayload":
        """§20 Bracket-SL: Stop muss auf der richtigen Seite des Entries liegen."""
        if self.action == "BUY":
            if self.stop_loss >= self.price:
                raise ValueError("stop_loss muss bei BUY unter dem Entry liegen")
            if self.take_profit is not None and self.take_profit <= self.price:
                raise ValueError("take_profit muss bei BUY ueber dem Entry liegen")
        elif self.action == "SELL":
            if self.stop_loss <= self.price:
                raise ValueError("stop_loss muss bei SELL ueber dem Entry liegen")
            if self.take_profit is not None and self.take_profit >= self.price:
                raise ValueError("take_profit muss bei SELL unter dem Entry liegen")
        return self

    # ---------------------------------------------------------------- helpers
    @property
    def side(self) -> str:
        return "buy" if self.action == "BUY" else "sell"

    def feature_dict(self) -> Dict[str, Any]:
        return self.features.model_dump(exclude_none=True) if self.features else {}


# =============================================================================
# Schema B — Pionex Signal Bot (§33.2)
# =============================================================================

class PionexOrderData(StrictModel):
    action: str
    contracts: str = "0"
    position_size: str = "0"


class PionexSignalPayload(StrictModel):
    """Native Pionex-Payload; nur aktiv wenn ``pionex_connector.enabled``."""

    data: PionexOrderData
    price: str
    signal_param: str = "{}"
    signal_type: str = Field(..., min_length=8)
    symbol: str
    time: str

    @field_validator("symbol")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_symbol(value)

    @property
    def numeric_price(self) -> float:
        try:
            return float(self.price)
        except ValueError as exc:  # pragma: no cover - Pydantic faengt Typen ab
            raise ValueError(f"price ist nicht numerisch: {self.price!r}") from exc


# =============================================================================
# Antwortvertrag (§33.1)
# =============================================================================

class SignalExecutionResponse(BaseModel):
    """Einheitliche Antwort des Ingestion-Routers."""

    model_config = ConfigDict(extra="allow")

    status: Literal["EXECUTED", "REJECTED", "DUPLICATE_IGNORED", "VETO_ORDERBOOK"]
    schema_family: Literal["SIGMA_L4_MASTER", "PIONEX_NATIVE", "ML_TELEMETRY"]
    strategy_id: str = ""
    bot_id: str = ""
    symbol: str = ""
    action: str = ""
    order_id: str = ""
    idempotency_key: str = ""
    execution_mode: str = bp.EXECUTION_MODE_DEFAULT
    fixed_leverage: int = bp.FIXED_LEVERAGE_DEFAULT
    code: str = ""
    reason: str = ""
    stage: str = ""
    quantity: float = 0.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: Optional[float] = None

    @property
    def accepted(self) -> bool:
        return self.status == "EXECUTED"


# =============================================================================
# Schema-Erkennung (§33, Ingestion-Router)
# =============================================================================

class SchemaDetectionError(ValueError):
    """Payload passt zu keiner der drei kanonischen Familien."""

    code = ERR_SCHEMA_UNKNOWN


def detect_schema(payload: Dict[str, Any]) -> str:
    """Erkennt die Schema-Familie eines rohen Webhook-Bodys."""
    if not isinstance(payload, dict) or not payload:
        raise SchemaDetectionError("leerer oder nicht-objekt Payload")
    keys = set(payload.keys())
    if {"signal_type", "data"} <= keys:
        return "PIONEX_NATIVE"
    if {"secret", "idempotency_key"} <= keys:
        return "SIGMA_L4_MASTER"
    if keys <= set(MLFeaturePayload.model_fields) and "rsi" in keys:
        return "ML_TELEMETRY"
    raise SchemaDetectionError(
        f"unbekanntes Alert-Schema (keys={sorted(keys)[:8]}); erwartet: "
        f"{', '.join(bp.WEBHOOK_SCHEMAS)}"
    )


def parse_payload(payload: Dict[str, Any]):
    """Erkennt und validiert in einem Schritt; wirft ``ValueError`` bei Bruch."""
    family = detect_schema(payload)
    if family == "SIGMA_L4_MASTER":
        return family, SigmaL4AlertPayload.model_validate(payload)
    if family == "PIONEX_NATIVE":
        return family, PionexSignalPayload.model_validate(payload)
    return family, MLFeaturePayload.model_validate(payload)
