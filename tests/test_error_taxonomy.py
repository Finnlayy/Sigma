"""§36 — Unified Error Taxonomy & Diagnostics Desk."""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import blueprint as bp
from app.core.error_engine import (EXCEPTION_BY_CODE, ErrorDetail, ErrorEngine,
                                   KillSwitchActiveException,
                                   KrakenDeadmanTimeoutException,
                                   InvalidWebhookSecretException,
                                   LiquidityTrapOrderbookException,
                                   PineCompilationException, SigmaBaseException,
                                   category_for, get_error_engine, http_status_for,
                                   install_error_handlers, raise_for_code,
                                   set_error_engine, severity_for)
from app.server.main import app as sigma_app


@pytest.fixture()
def engine(tmp_path):
    eng = ErrorEngine(log_path=str(tmp_path / "errors.jsonl"))
    set_error_engine(eng)
    yield eng
    set_error_engine(None)


# --------------------------------------------------------------- blueprint --
def test_catalog_ranges_and_categories():
    assert set(bp.ERROR_CATEGORIES) == {"E1000", "E2000", "E3000", "E4000", "E5000"}
    for code, (rng, subsystem, hint) in bp.ERROR_CATALOG.items():
        assert code.startswith("ERR_") and rng in bp.ERROR_CATEGORIES
        assert subsystem and hint


def test_every_catalog_code_has_exception():
    assert set(bp.ERROR_CATALOG) == set(EXCEPTION_BY_CODE)


def test_section_36_not_pending():
    assert not any(s.startswith("36 ") for s in bp.DOCS_PENDING_SECTIONS)


def test_panel_registered():
    assert "DiagnosticsErrorPanel" in bp.ALL_TERMINAL_PANELS
    assert bp.ERROR_LOG_PATH == "./data/logs/errors.jsonl"


# -------------------------------------------------------------- taxonomy ----
def test_category_and_status_mapping():
    assert category_for("ERR_AUTH_INVALID_SECRET") == "AUTHENTICATION"
    assert category_for("ERR_TV_PINE_COMPILE_ERROR") == "TRADINGVIEW"
    assert category_for("ERR_KRAKEN_RATE_LIMIT_429") == "KRAKEN"
    assert category_for("ERR_RISK_MAX_DAILY_LOSS") == "RISK_GUARD"
    assert category_for("ERR_SYS_DUCKDB_LOCK") == "SYSTEM"
    assert http_status_for("ERR_AUTH_INVALID_SECRET") == 401
    assert http_status_for("ERR_RISK_KILL_SWITCH_ACTIVE") == 409
    assert http_status_for("ERR_SYS_UNHANDLED_EXCEPTION") == 500


def test_severity_defaults_and_overrides():
    assert severity_for("ERR_RISK_KILL_SWITCH_ACTIVE") == "CRITICAL"
    assert severity_for("ERR_ORDERBOOK_LIQUIDITY_TRAP") == "LOW"
    assert severity_for("ERR_TV_EXPORT_TIMEOUT") == "MEDIUM"
    assert set(bp.ERROR_TELEGRAM_PUSH_SEVERITIES) == {"HIGH", "CRITICAL"}


def test_exception_to_detail_schema():
    exc = PineCompilationException("line 12: undeclared identifier",
                                   context={"strategy_id": "s1"})
    detail = exc.to_detail()
    assert isinstance(detail, ErrorDetail)
    assert detail.error_code == "ERR_TV_PINE_COMPILE_ERROR"
    assert detail.category == "TRADINGVIEW"
    assert detail.subsystem == "playwright-worker"
    assert "Pine v6" in detail.remediation_hint
    assert detail.technical_context["strategy_id"] == "s1"
    assert detail.trace_id.startswith("trc_") and detail.timestamp > 0


def test_raise_for_code():
    with pytest.raises(SigmaBaseException) as ei:
        raise_for_code("ERR_KRAKEN_INSUFFICIENT_FUNDS", "balance 12 EUR", need=100)
    assert ei.value.error_code == "ERR_KRAKEN_INSUFFICIENT_FUNDS"
    assert ei.value.context["need"] == 100


# ---------------------------------------------------------------- engine ----
def test_record_persists_jsonl(engine):
    engine.record(InvalidWebhookSecretException("bad secret"))
    rows = [json.loads(l) for l in open(engine.log_path, encoding="utf-8")]
    assert rows[0]["error_code"] == "ERR_AUTH_INVALID_SECRET"
    assert rows[0]["severity"] == "HIGH"


def test_unknown_exception_becomes_unhandled_code(engine):
    detail = engine.record(ValueError("boom"), subsystem="scraper-8001")
    assert detail.error_code == "ERR_SYS_UNHANDLED_EXCEPTION"
    assert detail.severity == "CRITICAL"
    assert detail.subsystem == "scraper-8001"
    assert "ValueError" in detail.technical_context["exception"]
    assert "stacktrace" in detail.technical_context


def test_recent_filtering_and_counts(engine):
    engine.record(LiquidityTrapOrderbookException("veto"))
    engine.record(KillSwitchActiveException("halted"))
    engine.record(KillSwitchActiveException("halted again"))
    assert engine.counts()["ERR_RISK_KILL_SWITCH_ACTIVE"] == 2
    assert len(engine.recent(severity="CRITICAL")) == 2
    assert len(engine.recent(category="RISK_GUARD")) == 3
    assert engine.recent()[0]["message"] == "halted again"  # neueste zuerst


def test_telegram_push_only_for_high_and_critical(engine):
    sent = []

    class Notifier:
        def send_alert(self, text, category="ALERT"):
            sent.append((text, category))
            return True

    engine.notifier = Notifier()
    engine.record(LiquidityTrapOrderbookException("veto"))       # LOW
    assert sent == []
    engine.record(KrakenDeadmanTimeoutException("no heartbeat"))  # CRITICAL
    assert len(sent) == 1
    assert "ERR_KRAKEN_DEADMAN_TIMEOUT" in sent[0][0]
    assert "Lösung" in sent[0][0]


def test_notifier_failure_is_swallowed(engine):
    class Boom:
        def send_alert(self, *a, **k):
            raise RuntimeError("telegram down")

    engine.notifier = Boom()
    assert engine.record(KillSwitchActiveException("x")).severity == "CRITICAL"


def test_export_and_clear(engine):
    engine.record(KillSwitchActiveException("x"))
    assert "ERR_RISK_KILL_SWITCH_ACTIVE" in engine.export_jsonl()
    engine.clear()
    assert engine.export_jsonl() == "" and engine.counts() == {}


def test_self_test(engine):
    result = engine.self_test()
    assert result["ok"] is True
    assert os.path.exists(engine.log_path)
    assert {c["check"] for c in result["checks"]} == {
        "errors_jsonl_writable", "catalog_complete", "telegram_notifier"}


def test_catalog_export(engine):
    rows = engine.catalog()
    assert len(rows) == len(bp.ERROR_CATALOG)
    row = next(r for r in rows if r["error_code"] == "ERR_KRAKEN_CLI_NOT_FOUND")
    assert row["category"] == "KRAKEN" and row["severity"] == "CRITICAL"


def test_singleton():
    set_error_engine(None)
    assert get_error_engine() is get_error_engine()
    set_error_engine(None)


# ------------------------------------------------------- FastAPI handler ----
def test_global_handler_returns_structured_detail(tmp_path):
    eng = ErrorEngine(log_path=str(tmp_path / "e.jsonl"))
    api = FastAPI()
    install_error_handlers(api, engine=eng)

    @api.get("/boom")
    async def boom():
        raise KillSwitchActiveException("kill switch aktiv", context={"src": "test"})

    @api.get("/oops")
    async def oops():
        raise ZeroDivisionError("nope")

    client = TestClient(api, raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["error_code"] == "ERR_RISK_KILL_SWITCH_ACTIVE"
    assert body["category"] == "RISK_GUARD" and body["severity"] == "CRITICAL"
    assert body["remediation_hint"]

    r2 = client.get("/oops")
    assert r2.status_code == 500
    assert r2.json()["error"]["error_code"] == "ERR_SYS_UNHANDLED_EXCEPTION"
    assert len(eng.recent()) == 2


# ------------------------------------------------------------------- API ----
@pytest.fixture()
def client(tmp_path):
    eng = ErrorEngine(log_path=str(tmp_path / "errors.jsonl"))
    set_error_engine(eng)
    yield TestClient(sigma_app), eng
    set_error_engine(None)


def test_api_errors_and_filter(client):
    c, eng = client
    eng.record(KillSwitchActiveException("halt"))
    eng.record(LiquidityTrapOrderbookException("veto"))
    body = c.get("/api/v1/diagnostics/errors").json()
    assert len(body["errors"]) == 2
    assert body["push_severities"] == ["HIGH", "CRITICAL"]
    assert body["categories"]["E4000"] == "RISK_GUARD"
    filtered = c.get("/api/v1/diagnostics/errors?severity=LOW").json()["errors"]
    assert [e["error_code"] for e in filtered] == ["ERR_ORDERBOOK_LIQUIDITY_TRAP"]


def test_api_catalog(client):
    c, _ = client
    body = c.get("/api/v1/diagnostics/catalog").json()
    assert len(body["catalog"]) == len(bp.ERROR_CATALOG)
    assert body["ranges"]["E2000"] == "TRADINGVIEW"


def test_api_export_and_clear(client):
    c, eng = client
    eng.record(KillSwitchActiveException("halt"))
    r = c.get("/api/v1/diagnostics/export")
    assert r.status_code == 200
    assert "ERR_RISK_KILL_SWITCH_ACTIVE" in r.text
    assert c.post("/api/v1/diagnostics/clear").json()["cleared"] is True
    assert c.get("/api/v1/diagnostics/errors").json()["errors"] == []


def test_api_self_test(client):
    c, _ = client
    assert c.post("/api/v1/diagnostics/self-test").json()["ok"] is True
