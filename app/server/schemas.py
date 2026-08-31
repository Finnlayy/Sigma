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
from typing import Any, Dict, List, Literal, Optional

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
_TV_PLACEHOLDER = re.compile(r"^\{\{[^}]+\}\}$")


def _is_tv_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(_TV_PLACEHOLDER.match(value.strip()))


def _maybe_float(value: Any) -> Optional[float]:
    if value is None or _is_tv_placeholder(value) or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_symbol(raw: str) -> str:
    """``KRAKEN:XRPUSD.P`` -> ``XRPUSD`` (Exchange-Prefix und Perp-Suffix weg)."""
    symbol = (raw or "").strip().upper()
    symbol = _SYMBOL_PREFIX.sub("", symbol)
    if symbol.endswith(".P"):
        symbol = symbol[:-2]
    if symbol.startswith(("PI_", "PF_")):
        symbol = symbol[3:]
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
    market_type: Literal["spot", "futures"] = "spot"
    features: Optional[MLFeaturePayload] = None

    @model_validator(mode="before")
    @classmethod
    def _preserve_market_type(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        features = data.get("features")
        if isinstance(features, dict):
            cleaned = dict(features)
            for key in ("rsi", "atr", "cisd_score", "bb_bandwidth"):
                if _is_tv_placeholder(cleaned.get(key)):
                    cleaned.pop(key, None)
            data["features"] = cleaned or None
        action = str(data.get("action") or "BUY").upper()
        price_f = _maybe_float(data.get("price"))
        atr_f = None
        if isinstance(data.get("features"), dict):
            atr_f = _maybe_float(data["features"].get("atr"))
        sl_f = _maybe_float(data.get("stop_loss"))
        tp_f = _maybe_float(data.get("take_profit"))
        if (sl_f is None and _is_tv_placeholder(data.get("stop_loss"))
                and price_f and atr_f and atr_f > 0 and action != "CLOSE"):
            sl_f = (price_f - bp.ATR_STOP_MULTIPLIER * atr_f if action == "BUY"
                    else price_f + bp.ATR_STOP_MULTIPLIER * atr_f)
            data["stop_loss"] = sl_f
        elif sl_f is not None:
            data["stop_loss"] = sl_f
        if (tp_f is None and _is_tv_placeholder(data.get("take_profit"))
                and price_f and atr_f and atr_f > 0 and action != "CLOSE"):
            tp_f = (price_f + bp.ATR_TAKE_PROFIT_MULTIPLIER * atr_f if action == "BUY"
                    else price_f - bp.ATR_TAKE_PROFIT_MULTIPLIER * atr_f)
            data["take_profit"] = tp_f
        elif tp_f is not None:
            data["take_profit"] = tp_f
        if action == "CLOSE" and sl_f is None and price_f:
            data["stop_loss"] = price_f
        raw = str(data.get("symbol") or "").strip().upper()
        inferred_futures = (
            raw.endswith(".P") or raw.startswith(("PI_", "PF_"))
            or ":PI_" in raw or ":PF_" in raw
        )
        if inferred_futures and data.get("market_type") == "spot":
            raise ValueError("futures symbol cannot be routed as spot")
        if inferred_futures:
            data["market_type"] = "futures"
        else:
            data.setdefault("market_type", "spot")
        return data

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
        if _is_tv_placeholder(value) or value in (None, ""):
            import time as _time
            return int(_time.time())
        return normalize_epoch(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _idempotency_placeholder(cls, value: Any) -> Any:
        if _is_tv_placeholder(value):
            import uuid as _uuid
            return f"tv_{_uuid.uuid4().hex[:16]}"
        return value

    @field_validator("action", "order_type", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _bracket_is_plausible(self) -> "SigmaL4AlertPayload":
        """§20 Bracket-SL: Stop muss auf der richtigen Seite des Entries liegen."""
        if self.action == "CLOSE":
            return self
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


# =============================================================================
# MP-17 — Sigma Research-/Panel-Read-Schemas (fail-closed)
# -----------------------------------------------------------------------------
# Kanonische Antworten unter /api/v1/sigma/* und /api/v1/research/*.
# Ohne Fachmodule liefern die Routen strukturierte Leerantworten
# (ok=False, available=False, leere Arrays) — niemals synthetische Werte.
# Zeiten: UNIX-Sekunden (UTC); Prozentangaben als Dezimalen (0.06 = 6 %).
# =============================================================================

class SigmaFeedMeta(BaseModel):
    """Herkunft eines Panel-Feeds; source='unknown' ohne Feed."""

    source: str = "unknown"          # tv_scraper | cache_stale | synthetic | unknown
    available: bool = False
    degraded: bool = False
    age_s: Optional[float] = None
    error: Optional[str] = None


class SigmaEmptyMixin(BaseModel):
    """Gemeinsame fail-closed Basis-Felder aller Panel-Antworten."""

    ok: bool = False
    available: bool = False
    feed: SigmaFeedMeta = Field(default_factory=SigmaFeedMeta)
    generated_at: Optional[str] = None   # ISO-8601 UTC


class SigmaRegimeState(SigmaEmptyMixin):
    """MP-07/05/06/11 Mission-Control-Zustand."""

    phase: Optional[str] = None          # SCAN_AND_DEPLOY | ACTIVE_EXECUTION | PRE_CLOSE_UNWIND | IDLE
    minute: Optional[int] = None         # Minute der laufenden 1h-Bar (UTC)
    last_scan_ts: Optional[float] = None
    wave_status: Optional[str] = None    # IDLE | COLLAPSED_INTO_ZONE | INVALIDATED | HTF_OPEN
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    eq: Optional[float] = None
    ce50: Optional[float] = None
    session_window: Optional[str] = None
    session_quarantine: Optional[bool] = None
    throttle_state: Optional[str] = None
    throttle_bots: Optional[int] = None
    hurst_htf: Optional[float] = None
    poly_bias: Optional[str] = None
    poly_p_cal: Optional[float] = None
    onnx_action: Optional[str] = None
    onnx_model_available: Optional[bool] = None
    shadow_plan: Optional[Dict[str, Any]] = None


class SigmaRiskState(SigmaEmptyMixin):
    """MP-01 Schutzschicht (nur Anzeige; Regeln nicht abschaltbar)."""

    positions: List[Dict[str, Any]] = Field(default_factory=list)
    rules: List[Dict[str, Any]] = Field(default_factory=list)  # {id,label,enabled:true}


class SigmaPowerState(SigmaEmptyMixin):
    """MP-04 Price-Action-Physics."""

    cos_phi: Optional[float] = None
    cluster: Optional[str] = None
    s_norm: Optional[float] = None
    p_norm: Optional[float] = None
    q_norm: Optional[float] = None
    q_upper: Optional[float] = None
    q_lower: Optional[float] = None
    q_bias: Optional[float] = None
    cos_path: List[Dict[str, Any]] = Field(default_factory=list)  # [{time,value}]
    resonance: Optional[float] = None
    resonance_badge: Optional[str] = None


class SigmaZonesState(SigmaEmptyMixin):
    """MP-03 Zonen & Tagesanker."""

    interval_min: Optional[int] = None
    zones: List[Dict[str, Any]] = Field(default_factory=list)
    envelope: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)  # Thrust/Marubozu


class SigmaScoutState(SigmaEmptyMixin):
    """MP-05 Stufe-2-Ranker."""

    last_scan_ts: Optional[float] = None
    phase_ok: Optional[bool] = None
    long_rank: List[Dict[str, Any]] = Field(default_factory=list)
    short_rank: List[Dict[str, Any]] = Field(default_factory=list)
    rejected: List[Dict[str, Any]] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    blinded: Optional[bool] = None


class SigmaPolymarketState(SigmaEmptyMixin):
    """MP-06 Layer 0; ohne Feed available=False und Gate inaktiv.
    Mit Gamma-Port: echte Strike-Leiter (density_bins), mu, bias_pct,
    Trajektorien und gate_060 (nur Telemetrie, nie Trade-Blocker)."""

    bins: List[Dict[str, Any]] = Field(default_factory=list)
    term_structure: List[Dict[str, Any]] = Field(default_factory=list)
    mu: Optional[float] = None
    bias: Optional[str] = None
    bias_pct: Optional[float] = None
    platt_a: Optional[float] = None
    platt_b: Optional[float] = None
    brier: Optional[float] = None
    p_cal: Optional[float] = None
    gate_open: Optional[bool] = None
    gate_060: Optional[bool] = None
    slug: Optional[str] = None
    title: Optional[str] = None
    volume24hr_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    spot_price: Optional[float] = None
    strikes: List[float] = Field(default_factory=list)
    yes_probs: List[float] = Field(default_factory=list)
    density_bins: List[Dict[str, Any]] = Field(default_factory=list)
    trajectories: Dict[str, float] = Field(default_factory=dict)
    source_ts: Optional[float] = None
    ttl_s: Optional[float] = None
    stale: Optional[bool] = None
    invalid_reason: Optional[str] = None


class SigmaExhaustionState(SigmaEmptyMixin):
    """MP-08 Exhaustion + geordnetes Glattstellen."""

    score: Optional[float] = None
    exhausted: Optional[bool] = None
    components: Dict[str, Any] = Field(default_factory=dict)  # bbw/oi/cvd + availability
    unwind: List[Dict[str, Any]] = Field(default_factory=list)
    forced: Optional[bool] = None
    ttl_flat: Optional[bool] = None


class SigmaProvisionState(SigmaEmptyMixin):
    """MP-09 Provisionierer."""

    provisions: List[Dict[str, Any]] = Field(default_factory=list)
    harden_supported: bool = True


class SigmaLadderPreview(SigmaEmptyMixin):
    """MP-02 DCA-Leiter-Preview + MP-01 Guards."""

    rungs: List[Dict[str, Any]] = Field(default_factory=list)
    guards: List[Dict[str, Any]] = Field(default_factory=list)  # {id,ok,reason}
    deploy_allowed: bool = False
    avg_fill_price: Optional[float] = None
    total_depth_pct: Optional[float] = None


class SigmaFractalPreview(SigmaEmptyMixin):
    """MP-15 Fraktaler Einzeltrade."""

    side: Optional[str] = None
    leverage: Optional[int] = None
    entry: Optional[float] = None
    tranches: List[Dict[str, Any]] = Field(default_factory=list)
    initial_sl: Optional[float] = None
    sl_basis: Optional[str] = None
    fee_covered_be: Optional[float] = None
    kill_switch: Dict[str, Any] = Field(default_factory=dict)


class SigmaOnnxState(SigmaEmptyMixin):
    """MP-11 Tensor-Inspektor."""

    tensor: List[Dict[str, Any]] = Field(default_factory=list)  # [{name,value}]
    action_probs: Dict[str, float] = Field(default_factory=dict)  # long/flat/short
    action: Optional[str] = None
    leverage: Optional[int] = None
    entropy: Optional[float] = None
    model_available: Optional[bool] = None
    bar_lock: Optional[str] = None       # EXECUTED | BLOCKED_BY_BAR_LOCK | None
    latency_ms: Optional[float] = None


class SigmaOrderflowState(SigmaEmptyMixin):
    """MP-10 (optional) — ohne L2-Feed immer leer. Mit Kraken-JIT:
    echter Audit-Status (i_depth, spread_bps, size_multiplier,
    audit_status) aus dem GlintOrderbookVerifier."""

    reason: Optional[str] = "orderflow_port_not_available"
    i_depth: Optional[float] = None
    spread_bps: Optional[float] = None
    size_multiplier: Optional[float] = None
    audit_status: Optional[str] = None
    symbol: Optional[str] = None
    snapshot_age_s: Optional[float] = None
    bid_volume_2pct: Optional[float] = None
    ask_volume_2pct: Optional[float] = None
    audits: List[Dict[str, Any]] = Field(default_factory=list)


class SigmaWriteResult(BaseModel):
    """Operator-POST-Antworten (Scan/Provision/De-Provision/Harden/Research)."""

    ok: bool = False
    available: bool = False
    reason: str = "backend_module_not_available"
    job_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


class ResearchRunRequest(BaseModel):
    """POST /api/v1/research/run — Hypothese H1-H7."""

    hypothesis: str = Field(..., min_length=1, max_length=16)
    params: Optional[Dict[str, Any]] = None


class ResearchJob(BaseModel):
    """Asynchroner Research-Job (MP-12/16)."""

    job_id: str
    hypothesis: str
    status: str = "unavailable"   # queued | running | done | failed | unavailable
    progress: float = 0.0
    error: Optional[str] = None
    created_at: Optional[float] = None


class ResearchJobResult(ResearchJob):
    """Job-Detail inkl. Ergebnis-Report (MP-16-Exportpfad)."""

    result: Optional[Dict[str, Any]] = None


class ResearchDashboard(SigmaEmptyMixin):
    """H1-H7-Status + Sweep-Tabelle + Export-Link."""

    hypotheses: List[Dict[str, Any]] = Field(default_factory=list)
    sweeps: List[Dict[str, Any]] = Field(default_factory=list)
    export_html_path: Optional[str] = None
