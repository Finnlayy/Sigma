"""
=========================================================
Datei:      app/llm/tool_executor.py
Zweck:      §34 — Ausfuehrung der typisierten LLM-Tool-Calls
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / LLM
=========================================================

Der Executor ist die einzige Bruecke zwischen Ollama und der Laufzeit:

    ToolCallEnvelope -> Pydantic-Validierung -> Engine-Aufruf -> ToolResultEnvelope

Noir-Gate (§34.4): irreversible Tools liefern ohne
``confirmation_confirmed=true`` den Status ``CONFIRMATION_REQUIRED`` und
fassen die Laufzeit **nicht** an.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any, Dict, Optional

from app.core import blueprint as bp
from app.llm.schemas_llm import (ControlBotParams, PineCodePatchRequest,
                                 PineCodePatchResponse, QueryKausalAutopsyParams,
                                 ToolCallEnvelope, ToolResultEnvelope,
                                 TriggerEmergencyActionParams,
                                 UpdateRiskSettingsParams, validate_arguments)

logger = logging.getLogger("app.llm.tool_executor")


class LlmToolExecutor:
    """Fuehrt Tool-Calls gegen die realen Engines aus (§34.1)."""

    def __init__(self, *, lifecycle=None, safety=None, bridge=None, l4=None,
                 strategies_dir: str = "", tv_queue=None) -> None:
        self._lifecycle = lifecycle
        self._safety = safety
        self._bridge = bridge
        self._l4 = l4
        self._tv_queue = tv_queue
        self.strategies_dir = strategies_dir or bp.PATH_STRATEGIES
        self.risk_overrides: Dict[str, Any] = {}

    # ------------------------------------------------------------- seams --
    @property
    def lifecycle(self):
        if self._lifecycle is None:
            from app.services.strategy_lifecycle_service import get_lifecycle_service
            self._lifecycle = get_lifecycle_service()
        return self._lifecycle

    @property
    def safety(self):
        if self._safety is None:
            from app.execution.SafetyGuard import get_safety_guard
            self._safety = get_safety_guard()
        return self._safety

    @property
    def bridge(self):
        if self._bridge is None:
            from app.execution.KrakenCliBridge import KrakenCliBridge
            self._bridge = KrakenCliBridge()
        return self._bridge

    # ----------------------------------------------------------- dispatch --
    def execute(self, envelope: ToolCallEnvelope) -> ToolResultEnvelope:
        started = time.perf_counter()

        def done(status: str, data: Optional[Dict[str, Any]] = None,
                 error: Optional[str] = None) -> ToolResultEnvelope:
            return ToolResultEnvelope(
                call_id=envelope.call_id, tool_name=envelope.tool_name,
                status=status, result_data=data or {}, error_message=error,
                execution_time_ms=int((time.perf_counter() - started) * 1000))

        try:
            params = validate_arguments(envelope.tool_name, envelope.arguments)
        except Exception as exc:
            return done("FAILED", {"arguments": envelope.arguments}, str(exc))

        handler = {
            "update_risk_settings": self._update_risk_settings,
            "control_bot": self._control_bot,
            "edit_pine_strategy_code": self._edit_pine,
            "query_kausal_autopsy": self._query_autopsy,
            "trigger_emergency_action": self._emergency,
        }[envelope.tool_name]

        try:
            status, data = handler(params)
            return done(status, data)
        except Exception as exc:
            logger.warning("tool %s failed: %s", envelope.tool_name, exc)
            return done("FAILED", {}, f"{type(exc).__name__}: {exc}")

    def call(self, tool_name: str, **arguments: Any) -> ToolResultEnvelope:
        return self.execute(ToolCallEnvelope(tool_name=tool_name,
                                             arguments=arguments))

    # -------------------------------------------------------------- tools --
    def _update_risk_settings(self, p: UpdateRiskSettingsParams):
        changes = p.changes()
        if not changes:
            return "FAILED", {"reason": "keine Parameter uebergeben"}
        self.risk_overrides.update(changes)
        applied = {}
        for key, value in changes.items():
            applied[key] = value
            if self._l4 is not None and hasattr(self._l4, "set"):
                try:
                    self._l4.set(f"risk.{key}", value)
                except Exception as exc:  # pragma: no cover
                    logger.warning("l4 set %s: %s", key, exc)
        return "SUCCESS", {"applied": applied, "overrides": dict(self.risk_overrides),
                           "ui_component_trigger": "REFRESH_BOT_DECK"}

    def _control_bot(self, p: ControlBotParams):
        lifecycle = self.lifecycle
        if p.action == "START":
            result = lifecycle.start(p.strategy_id, trigger_path="MANUAL",
                                     budget_eur=p.adjusted_budget_eur)
        elif p.action == "PAUSE":
            result = lifecycle.pause(p.strategy_id, reason=p.reason)
        elif p.action == "STOP":
            result = lifecycle.pause(p.strategy_id, reason=f"stop:{p.reason}")
        else:
            result = lifecycle.quarantine(p.strategy_id, reason=p.reason)
        payload = result if isinstance(result, dict) else {"result": str(result)}
        return "SUCCESS", {"action": p.action, "strategy_id": p.strategy_id,
                           "lifecycle": payload,
                           "ui_component_trigger": "REFRESH_BOT_DECK"}

    def _edit_pine(self, p: PineCodePatchRequest):
        """§34.2 — Backup, Patch, optional Playwright-Compile, Rollback."""
        sdir = os.path.join(self.strategies_dir, p.strategy_id)
        os.makedirs(sdir, exist_ok=True)
        code_path = os.path.join(sdir, "code.pine")
        backup_path = code_path + ".bak"
        if os.path.exists(code_path):
            shutil.copyfile(code_path, backup_path)
        else:
            with open(backup_path, "w", encoding="utf-8") as fh:
                fh.write("")
        with open(code_path, "w", encoding="utf-8") as fh:
            fh.write(p.pine_source_code)

        if not p.push_to_tradingview:
            resp = PineCodePatchResponse(strategy_id=p.strategy_id,
                                         status="SAVED_LOCAL_ONLY",
                                         backup_file_path=backup_path)
            return "SUCCESS", resp.model_dump()

        error = self._compile_on_tv(p)
        if error:
            shutil.copyfile(backup_path, code_path)     # Rollback
            resp = PineCodePatchResponse(strategy_id=p.strategy_id,
                                         status="COMPILE_FAILED_ROLLBACK",
                                         backup_file_path=backup_path,
                                         tv_compilation_error=error)
            return "FAILED", resp.model_dump()

        resp = PineCodePatchResponse(strategy_id=p.strategy_id,
                                     status="SUCCESS_COMPILED",
                                     backup_file_path=backup_path)
        return "SUCCESS", {**resp.model_dump(), "edit_mode": p.edit_mode,
                           "commit_summary": p.commit_summary,
                           "ui_component_trigger": "RELOAD_CHART"}

    def _compile_on_tv(self, p: PineCodePatchRequest) -> str:
        """Playwright-Compile-Gate. Rueckgabe: Fehlertext oder "" bei Erfolg."""
        if self._tv_queue is None:
            return ""
        from app.tv.worker import JOB_KIND_PUSH_CODE

        try:
            job = self._tv_queue.submit(JOB_KIND_PUSH_CODE,
                                        strategy_id=p.strategy_id,
                                        pine_source=p.pine_source_code)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        if isinstance(job, dict) and job.get("status") == "failed":
            return str(job.get("error") or "TV compile failed")
        return ""

    def _query_autopsy(self, p: QueryKausalAutopsyParams):
        rows: Any = []
        try:
            from app.core.duckdb_store import get_store
            store = get_store()
            if hasattr(store, "kausal_autopsy"):
                rows = store.kausal_autopsy(p.strategy_id)
        except Exception as exc:  # pragma: no cover - Store optional
            logger.info("autopsy store unavailable: %s", exc)
        return "SUCCESS", {"strategy_id": p.strategy_id, "symbol": p.symbol,
                           "timeframe": p.timeframe, "rows": rows,
                           "ui_component_trigger": "OPEN_INSPECTOR"}

    def _emergency(self, p: TriggerEmergencyActionParams):
        if not p.confirmation_confirmed:
            return "CONFIRMATION_REQUIRED", {
                "action": p.action,
                "prompt": f"{p.action} ist irreversibel — bitte bestaetigen.",
                "confirm_field": "confirmation_confirmed"}
        data: Dict[str, Any] = {"action": p.action, "reason": p.reason}
        if p.action == "KILL_SWITCH":
            self.safety.engage_kill_switch(p.reason)
            data["kill_switch"] = True
            data["halt_action"] = bp.HALT_ACTION
        elif p.action == "CANCEL_ALL_ORDERS":
            result = self.bridge.cancel_all(reason=p.reason)
            data["cancelled"] = bool(getattr(result, "ok", False))
        else:  # FLIGHT_TO_CASH
            self.safety.engage_pause(p.reason)
            result = self.bridge.cancel_all(reason="flight_to_cash")
            data["paused"] = True
            data["cancelled"] = bool(getattr(result, "ok", False))
        data["ui_component_trigger"] = "REFRESH_BOT_DECK"
        return "SUCCESS", data


_EXECUTOR: Optional[LlmToolExecutor] = None


def get_tool_executor(**kwargs: Any) -> LlmToolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = LlmToolExecutor(**kwargs)
    return _EXECUTOR


def set_tool_executor(executor: Optional[LlmToolExecutor]) -> None:
    global _EXECUTOR
    _EXECUTOR = executor
