"""§34 — LLM-, Tool-Calling- & Streaming-Schemata."""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import blueprint as bp
from app.llm.schemas_llm import (ChatStreamMessage, ControlBotParams,
                                 PineCodePatchRequest, ToolCallEnvelope,
                                 ToolResultEnvelope, TOOL_PARAM_MODELS,
                                 TriggerEmergencyActionParams,
                                 UpdateRiskSettingsParams, registry_path,
                                 tool_registry, validate_arguments, write_registry)
from app.llm.tool_executor import LlmToolExecutor, set_tool_executor
from app.server.main import app

PINE_OK = "//@version=6\nstrategy('Sigma', overlay=true)\nplot(close)"


class FakeLifecycle:
    def __init__(self):
        self.calls = []

    def start(self, sid, trigger_path="MANUAL", budget_eur=None, **kw):
        self.calls.append(("start", sid, trigger_path, budget_eur))
        return {"strategy_id": sid, "state": "ACTIVE"}

    def pause(self, sid, reason="operator"):
        self.calls.append(("pause", sid, reason))
        return {"strategy_id": sid, "state": "PAUSED", "reason": reason}

    def quarantine(self, sid, reason="risk"):
        self.calls.append(("quarantine", sid, reason))
        return {"strategy_id": sid, "state": "QUARANTINED"}


class FakeSafety:
    def __init__(self):
        self.killed = None
        self.paused = None

    def engage_kill_switch(self, reason="operator"):
        self.killed = reason

    def engage_pause(self, reason="operator"):
        self.paused = reason


class FakeBridge:
    def __init__(self, ok=True):
        self.ok = ok
        self.cancels = []

    def cancel_all(self, reason="kill_switch"):
        self.cancels.append(reason)
        return type("R", (), {"ok": self.ok})()


@pytest.fixture()
def executor(tmp_path):
    ex = LlmToolExecutor(lifecycle=FakeLifecycle(), safety=FakeSafety(),
                         bridge=FakeBridge(), strategies_dir=str(tmp_path / "s"))
    set_tool_executor(ex)
    yield ex
    set_tool_executor(None)


# --------------------------------------------------------------- blueprint --
def test_blueprint_llm_constants():
    assert set(bp.LLM_TOOLS) == set(TOOL_PARAM_MODELS)
    assert bp.LLM_TOOLS_REQUIRING_CONFIRMATION == ("trigger_emergency_action",)
    assert bp.LLM_STREAM_ROUTE == "/api/v1/llm/stream"
    assert bp.PINE_VERSION_HEADER == "//@version=6"
    assert set(bp.LLM_TOOL_STATUSES) == {"SUCCESS", "FAILED", "CONFIRMATION_REQUIRED"}


def test_section_34_not_pending():
    assert not any(s.startswith("34 ") for s in bp.DOCS_PENDING_SECTIONS)


# ----------------------------------------------------------------- schemas --
def test_risk_param_ranges():
    ok = UpdateRiskSettingsParams(kelly_fraction=0.5, global_max_leverage=5)
    assert ok.changes() == {"kelly_fraction": 0.5, "global_max_leverage": 5}
    with pytest.raises(ValidationError):
        UpdateRiskSettingsParams(global_max_leverage=6)     # > FIXED_LEVERAGE_MAX
    with pytest.raises(ValidationError):
        UpdateRiskSettingsParams(kelly_fraction=1.5)
    with pytest.raises(ValidationError):
        UpdateRiskSettingsParams(max_open_positions=-1)


def test_control_bot_action_enum():
    assert ControlBotParams(strategy_id="s", action="PAUSE").action == "PAUSE"
    with pytest.raises(ValidationError):
        ControlBotParams(strategy_id="s", action="YOLO")


def test_pine_patch_requires_v6_header():
    req = PineCodePatchRequest(strategy_id="s", edit_mode="FULL_REPLACE",
                               pine_source_code=PINE_OK, commit_summary="x")
    assert req.push_to_tradingview is True
    with pytest.raises(ValidationError):
        PineCodePatchRequest(strategy_id="s", edit_mode="FULL_REPLACE",
                             pine_source_code="//@version=5\nstrategy('a')\nplot(1)")
    with pytest.raises(ValidationError):       # min_length 20
        PineCodePatchRequest(strategy_id="s", edit_mode="FULL_REPLACE",
                             pine_source_code="//@version=6")


def test_pine_edit_modes_match_blueprint():
    for mode in bp.PINE_EDIT_MODES:
        assert PineCodePatchRequest(strategy_id="s", edit_mode=mode,
                                    pine_source_code=PINE_OK).edit_mode == mode


def test_chat_stream_message_defaults():
    msg = ChatStreamMessage(session_id="s1", sender="ASSISTANT",
                            content_chunk="hallo")
    assert msg.message_id.startswith("msg_") and msg.is_complete is False
    assert msg.timestamp > 0
    with pytest.raises(ValidationError):
        ChatStreamMessage(sender="ROBOT")
    with pytest.raises(ValidationError):
        ChatStreamMessage(ui_component_trigger="EXPLODE")


def test_validate_arguments_unknown_tool():
    with pytest.raises(ValueError):
        validate_arguments("rm_rf", {})


# ---------------------------------------------------------------- registry --
def test_tool_registry_is_openai_compatible():
    tools = tool_registry()
    assert {t["function"]["name"] for t in tools} == set(bp.LLM_TOOLS)
    for tool in tools:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["type"] == "object"
        assert tool["function"]["description"]
    emergency = next(t for t in tools
                     if t["function"]["name"] == "trigger_emergency_action")
    assert emergency["function"]["x-sigma"]["requires_confirmation"] is True


def test_write_registry(tmp_path):
    target = write_registry(str(tmp_path / "tools_registry.json"))
    payload = json.load(open(target, encoding="utf-8"))
    assert payload["version"] == bp.DOCS_BLUEPRINT_VERSION
    assert len(payload["tools"]) == 5


def test_registry_file_shipped():
    assert os.path.exists(registry_path())


# ---------------------------------------------------------------- executor --
def test_update_risk_settings(executor):
    result = executor.call("update_risk_settings", kelly_fraction=0.4,
                           max_daily_loss_usd=500)
    assert result.status == "SUCCESS"
    assert result.result_data["applied"]["kelly_fraction"] == 0.4
    assert executor.risk_overrides["max_daily_loss_usd"] == 500


def test_update_risk_settings_rejects_out_of_range(executor):
    result = executor.call("update_risk_settings", global_max_leverage=10)
    assert result.status == "FAILED" and "less_than_equal" in result.error_message


def test_control_bot_actions(executor):
    assert executor.call("control_bot", strategy_id="s1",
                         action="START", adjusted_budget_eur=250).status == "SUCCESS"
    executor.call("control_bot", strategy_id="s1", action="PAUSE")
    executor.call("control_bot", strategy_id="s1", action="STOP")
    executor.call("control_bot", strategy_id="s1", action="QUARANTINE")
    kinds = [c[0] for c in executor._lifecycle.calls]
    assert kinds == ["start", "pause", "pause", "quarantine"]
    assert executor._lifecycle.calls[0][3] == 250


def test_emergency_requires_confirmation(executor):
    result = executor.call("trigger_emergency_action", action="KILL_SWITCH")
    assert result.status == "CONFIRMATION_REQUIRED"
    assert result.result_data["confirm_field"] == "confirmation_confirmed"
    assert executor._safety.killed is None        # Laufzeit unangetastet


def test_emergency_kill_switch_confirmed(executor):
    result = executor.call("trigger_emergency_action", action="KILL_SWITCH",
                           confirmation_confirmed=True, reason="llm")
    assert result.status == "SUCCESS" and executor._safety.killed == "llm"
    assert result.result_data["halt_action"] == bp.HALT_ACTION


def test_emergency_cancel_all_and_flight_to_cash(executor):
    executor.call("trigger_emergency_action", action="CANCEL_ALL_ORDERS",
                  confirmation_confirmed=True)
    assert executor._bridge.cancels == ["llm"]
    executor.call("trigger_emergency_action", action="FLIGHT_TO_CASH",
                  confirmation_confirmed=True)
    assert executor._safety.paused == "llm"
    assert "flight_to_cash" in executor._bridge.cancels


def test_pine_patch_local_only_creates_backup(executor):
    result = executor.call("edit_pine_strategy_code", strategy_id="s1",
                           edit_mode="FULL_REPLACE", pine_source_code=PINE_OK,
                           push_to_tradingview=False)
    assert result.status == "SUCCESS"
    assert result.result_data["status"] == "SAVED_LOCAL_ONLY"
    assert os.path.exists(result.result_data["backup_file_path"])
    code = os.path.join(executor.strategies_dir, "s1", "code.pine")
    assert open(code, encoding="utf-8").read() == PINE_OK


def test_pine_patch_rollback_on_compile_error(executor):
    executor.call("edit_pine_strategy_code", strategy_id="s2",
                  edit_mode="FULL_REPLACE", pine_source_code=PINE_OK,
                  push_to_tradingview=False)

    class FailingQueue:
        def submit(self, kind, **payload):
            return {"status": "failed", "error": "line 3: syntax error"}

    executor._tv_queue = FailingQueue()
    broken = PINE_OK.replace("plot(close)", "plot(closeeee)")
    result = executor.call("edit_pine_strategy_code", strategy_id="s2",
                           edit_mode="DIFF_PATCH", pine_source_code=broken)
    assert result.status == "FAILED"
    assert result.result_data["status"] == "COMPILE_FAILED_ROLLBACK"
    assert result.result_data["tv_compilation_error"] == "line 3: syntax error"
    code = os.path.join(executor.strategies_dir, "s2", "code.pine")
    assert open(code, encoding="utf-8").read() == PINE_OK     # zurueckgerollt


def test_pine_patch_success_compiled(executor):
    class OkQueue:
        def submit(self, kind, **payload):
            return {"status": "queued", "job_id": "j1"}

    executor._tv_queue = OkQueue()
    result = executor.call("edit_pine_strategy_code", strategy_id="s3",
                           edit_mode="INJECT_TIME_STOP", pine_source_code=PINE_OK)
    assert result.result_data["status"] == "SUCCESS_COMPILED"
    assert result.result_data["ui_component_trigger"] == "RELOAD_CHART"


def test_query_autopsy(executor):
    result = executor.call("query_kausal_autopsy", strategy_id="s1", symbol="XBTUSD")
    assert result.status == "SUCCESS"
    assert result.result_data["ui_component_trigger"] == "OPEN_INSPECTOR"


def test_unknown_tool_returns_failed(executor):
    result = executor.execute(ToolCallEnvelope(tool_name="drop_database"))
    assert result.status == "FAILED" and "drop_database" in result.error_message


def test_result_envelope_has_timing(executor):
    result = executor.call("query_kausal_autopsy", strategy_id="s1")
    assert isinstance(result, ToolResultEnvelope)
    assert result.execution_time_ms >= 0 and result.call_id.startswith("call_")


# -------------------------------------------------------------------- API --
@pytest.fixture()
def client(executor):
    return TestClient(app)


def test_api_tools(client):
    body = client.get("/api/v1/llm/tools").json()
    assert len(body["tools"]) == 5
    assert body["requires_confirmation"] == ["trigger_emergency_action"]
    assert body["stream_route"] == bp.LLM_STREAM_ROUTE


def test_api_tool_call_confirmation(client):
    body = client.post("/api/v1/llm/tool-call", json={
        "tool_name": "trigger_emergency_action",
        "arguments": {"action": "FLIGHT_TO_CASH"}}).json()
    assert body["status"] == "CONFIRMATION_REQUIRED"


def test_api_tool_call_invalid_envelope(client):
    r = client.post("/api/v1/llm/tool-call", json={"arguments": {}})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "LLM_TOOL_ENVELOPE_INVALID"


def test_api_pine_patch_rejects_v5(client):
    r = client.post("/api/v1/llm/pine-patch", json={
        "strategy_id": "s9", "edit_mode": "FULL_REPLACE",
        "pine_source_code": "//@version=5\nstrategy('x')\nplot(close)"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "ERR_TV_PINE_COMPILE_ERROR"


def test_api_pine_patch_ok(client):
    body = client.post("/api/v1/llm/pine-patch", json={
        "strategy_id": "s9", "edit_mode": "FULL_REPLACE",
        "pine_source_code": PINE_OK, "push_to_tradingview": False}).json()
    assert body["result_data"]["status"] == "SAVED_LOCAL_ONLY"


def test_websocket_tool_call_flow(client):
    with client.websocket_connect(bp.LLM_STREAM_ROUTE) as ws:
        ws.send_json({"session_id": "s1", "tool_call": {
            "tool_name": "control_bot",
            "arguments": {"strategy_id": "s1", "action": "PAUSE"}}})
        first = ws.receive_json()
        assert first["sender"] == "TOOL_EXECUTOR"
        assert first["active_tool_call"]["tool_name"] == "control_bot"
        second = ws.receive_json()
        assert second["tool_result"]["status"] == "SUCCESS"
        assert second["ui_component_trigger"] == "REFRESH_BOT_DECK"
        assert second["is_complete"] is True


def test_websocket_text_chunks(client):
    with client.websocket_connect(bp.LLM_STREAM_ROUTE) as ws:
        ws.send_json({"session_id": "s1", "prompt": "status bitte"})
        assert ws.receive_json()["content_chunk"] == "status "
        assert ws.receive_json()["content_chunk"] == "bitte "
        assert ws.receive_json()["is_complete"] is True
