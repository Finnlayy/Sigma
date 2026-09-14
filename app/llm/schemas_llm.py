"""
=========================================================
Datei:      app/llm/schemas_llm.py
Zweck:      §34 / Axiom 12 — LLM-, Tool-Calling- & Streaming-Schemata
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / LLM
=========================================================

Das offline laufende Ollama (:11434) steuert Sigma **ausschliesslich** ueber
typisierte Tool-Contracts — kein Freitext-Execution. Irreversible Tools
(``trigger_emergency_action``) verlangen ``confirmation_confirmed: true``.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core import blueprint as bp

ToolStatus = Literal["SUCCESS", "FAILED", "CONFIRMATION_REQUIRED"]
PineEditMode = Literal["FULL_REPLACE", "DIFF_PATCH", "INJECT_TIME_STOP",
                       "ADJUST_PARAMETERS"]
BotAction = Literal["START", "PAUSE", "STOP", "QUARANTINE"]
EmergencyAction = Literal["KILL_SWITCH", "CANCEL_ALL_ORDERS", "FLIGHT_TO_CASH"]
Sender = Literal["USER", "ASSISTANT", "SYSTEM", "TOOL_EXECUTOR"]
UiTrigger = Literal["REFRESH_BOT_DECK", "RELOAD_CHART", "OPEN_INSPECTOR"]


def _now_ms() -> int:
    return int(time.time() * 1000)


# =============================================================================
# 34.1 Tool-Parameter
# =============================================================================

class UpdateRiskSettingsParams(BaseModel):
    max_daily_loss_usd: Optional[float] = Field(default=None, ge=0, le=100_000)
    kelly_fraction: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_open_positions: Optional[int] = Field(default=None, ge=0, le=20)
    global_max_leverage: Optional[int] = Field(
        default=None, ge=bp.FIXED_LEVERAGE_MIN, le=bp.FIXED_LEVERAGE_MAX)

    @field_validator("max_daily_loss_usd", "kelly_fraction", mode="before")
    @classmethod
    def _reject_nan(cls, value):
        if value is not None and value != value:  # NaN
            raise ValueError("NaN ist kein gueltiger Risikowert")
        return value

    def changes(self) -> Dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ControlBotParams(BaseModel):
    strategy_id: str = Field(min_length=1)
    action: BotAction
    adjusted_budget_eur: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    reason: str = "llm"


class PineCodePatchRequest(BaseModel):
    strategy_id: str = Field(min_length=1)
    edit_mode: PineEditMode
    pine_source_code: str = Field(min_length=20)
    commit_summary: str = "llm patch"
    push_to_tradingview: bool = True

    @field_validator("pine_source_code")
    @classmethod
    def _enforce_pine_v6(cls, value: str) -> str:
        head = "\n".join(value.splitlines()[:3])
        if bp.PINE_VERSION_HEADER not in head:
            raise ValueError(
                f"Pine-Header {bp.PINE_VERSION_HEADER} fehlt in den ersten 3 Zeilen")
        return value


class PineCodePatchResponse(BaseModel):
    strategy_id: str
    status: Literal["SUCCESS_COMPILED", "COMPILE_FAILED_ROLLBACK", "SAVED_LOCAL_ONLY"]
    backup_file_path: str
    tv_compilation_error: Optional[str] = None


class QueryKausalAutopsyParams(BaseModel):
    strategy_id: str = Field(min_length=1)
    symbol: str = ""
    timeframe: str = ""


class TriggerEmergencyActionParams(BaseModel):
    action: EmergencyAction
    confirmation_confirmed: bool = False
    reason: str = "llm"


TOOL_PARAM_MODELS: Dict[str, type] = {
    "update_risk_settings": UpdateRiskSettingsParams,
    "control_bot": ControlBotParams,
    "edit_pine_strategy_code": PineCodePatchRequest,
    "query_kausal_autopsy": QueryKausalAutopsyParams,
    "trigger_emergency_action": TriggerEmergencyActionParams,
}


# =============================================================================
# Envelopes
# =============================================================================

class ToolCallEnvelope(BaseModel):
    call_id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    timestamp: int = Field(default_factory=_now_ms)


class ToolResultEnvelope(BaseModel):
    call_id: str
    tool_name: str
    status: ToolStatus
    result_data: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    execution_time_ms: int = 0


class ChatStreamMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    session_id: str = "default"
    sender: Sender = "ASSISTANT"
    content_chunk: Optional[str] = None
    is_complete: bool = False
    active_tool_call: Optional[ToolCallEnvelope] = None
    tool_result: Optional[ToolResultEnvelope] = None
    ui_component_trigger: Optional[UiTrigger] = None
    timestamp: int = Field(default_factory=_now_ms)


class ChatRequest(BaseModel):
    session_id: str = "default"
    prompt: str = ""
    tool_call: Optional[ToolCallEnvelope] = None


# =============================================================================
# 34.1 Tool-Registry (Ollama / OpenAI function-calling kompatibel)
# =============================================================================

_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "update_risk_settings": "Passt globale Risikoparameter an (Kelly, Tagesverlust, "
                            "offene Positionen, maximaler Hebel 1-5).",
    "control_bot": "Startet, pausiert, stoppt oder quarantaeniert eine Strategie.",
    "edit_pine_strategy_code": "Patcht den Pine-v6-Code einer Strategie und "
                               "kompiliert ihn optional auf TradingView.",
    "query_kausal_autopsy": "Liefert die kausale Autopsie (Verlustursachen) einer "
                            "Strategie.",
    "trigger_emergency_action": "Irreversible Notfallaktion — erfordert "
                                "confirmation_confirmed=true.",
}


def _json_schema(model: type) -> Dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def tool_registry() -> List[Dict[str, Any]]:
    """OpenAI-/Ollama-kompatible Function-Definitionen."""
    return [{
        "type": "function",
        "function": {
            "name": name,
            "description": _TOOL_DESCRIPTIONS[name],
            "parameters": _json_schema(model),
            "x-sigma": {
                "summary": bp.LLM_TOOLS[name],
                "requires_confirmation":
                    name in bp.LLM_TOOLS_REQUIRING_CONFIRMATION,
            },
        },
    } for name, model in TOOL_PARAM_MODELS.items()]


def registry_path() -> str:
    import os

    return os.path.join(os.path.dirname(__file__), "tools_registry.json")


def write_registry(path: str = "") -> str:
    """Schreibt ``app/llm/tools_registry.json`` (aus den Pydantic-Modellen)."""
    import json
    import os

    target = path or registry_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump({"version": bp.DOCS_BLUEPRINT_VERSION, "tools": tool_registry()},
                  fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return target


def validate_arguments(tool_name: str, arguments: Dict[str, Any]) -> BaseModel:
    """Argumente gegen das Parameter-Modell validieren (Range-Checks inklusive)."""
    model = TOOL_PARAM_MODELS.get(tool_name)
    if model is None:
        raise ValueError(f"unbekanntes Tool {tool_name!r}")
    return model(**(arguments or {}))
