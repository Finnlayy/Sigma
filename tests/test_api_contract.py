"""
API-Vertrag (§7) — Webhook, Safety, Bots, Alerts, TV-Jobs, Academy,
Telegram, Health/Blueprint. Läuft gegen die echte FastAPI-App.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.core import blueprint as bp


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os

    tmp = tmp_path_factory.mktemp("sigma_api")
    os.environ["SIGMA_WEBHOOK_SECRET"] = "api-secret"
    os.environ["SIGMA_DATA_DIR"] = str(tmp)
    os.environ["TELEGRAM_CHAT_ID"] = "4242"

    import app.server.main as main
    import app.server.routes_sigma as routes
    from app.ingestion.macro_contagion_feed import MacroContagionFeed
    from app.quant.epidemic_contagion_engine import ContagionInputs

    routes.set_pipeline(None)          # frisch bauen, damit das Secret greift
    original_snapshot = MacroContagionFeed.snapshot
    MacroContagionFeed.snapshot = lambda self: ContagionInputs()
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        MacroContagionFeed.snapshot = original_snapshot


def _alert(**kw):
    body = {"symbol": "XBTUSD", "action": "BUY", "price": 50_000.0, "rsi": 28.0,
            "atr": 500.0, "cisd_score": 0.7, "timestamp": int(time.time()),
            "strategy_id": "api_strat", "interval": 15, "secret": "api-secret"}
    body.update(kw)
    return body


# ------------------------------------------------------------- health/spec ---

def test_health_and_blueprint(client):
    h = client.get("/api/v1/health").json()
    assert h["blueprint"]["blueprint_version"] == bp.BLUEPRINT_VERSION
    assert h["blueprint"]["ports"]["core"] == bp.PORT_CORE
    b = client.get("/api/v1/blueprint").json()
    assert set(b["loops"]) == {"A", "B", "C", "D", "E"}
    assert b["m8_alert_matrix"]["THROTTLED"]["budget_multiplier"] == 0.5
    assert f"POST {bp.WEBHOOK_ROUTE}" in b["api_contract"]


# ------------------------------------------------------------------ webhook ---

def test_webhook_rejects_missing_secret(client):
    r = client.post(bp.WEBHOOK_ROUTE, json=_alert(secret=""))
    assert r.status_code == bp.WEBHOOK_UNAUTHORIZED_STATUS
    assert r.json()["detail"]["code"] == "UNAUTHORIZED"


def test_webhook_accepts_header_secret(client):
    r = client.post(bp.WEBHOOK_ROUTE, json=_alert(secret=""),
                    headers={bp.WEBHOOK_SECRET_HEADER: "api-secret"})
    assert r.status_code == 200 and r.json()["accepted"] is True


def test_webhook_happy_path_returns_sizing(client):
    body = client.post(bp.WEBHOOK_ROUTE, json=_alert()).json()
    assert body["accepted"] and body["quantity"] > 0
    assert body["pair"] == "XBTUSD"
    assert body["stop_loss"] < body["price"] < body["take_profit"]
    assert body["mode"] in ("sim", "paper", "dry_run")


def test_legacy_webhook_is_disabled_when_live_trading(client):
    import app.server.routes_sigma as routes

    pipe = routes.pipeline()
    previous = pipe.config.live_trading
    pipe.config.live_trading = True
    try:
        response = client.post(bp.WEBHOOK_ROUTE, json=_alert())
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "LEGACY_WEBHOOK_LIVE_DISABLED"
    finally:
        pipe.config.live_trading = previous


def test_webhook_rejects_stale_and_unknown_symbol(client):
    stale = client.post(bp.WEBHOOK_ROUTE, json=_alert(timestamp=int(time.time()) - 9999))
    assert stale.status_code == 400
    bad_symbol = client.post(bp.WEBHOOK_ROUTE, json=_alert(symbol="DOGE/USD"))
    assert bad_symbol.status_code == 403


def test_kill_switch_endpoint_blocks_webhook(client):
    assert client.post("/api/v1/safety/kill").json()["kill_switch"] is True
    blocked = client.post(bp.WEBHOOK_ROUTE, json=_alert())
    assert blocked.status_code == bp.WEBHOOK_BLOCKED_STATUS
    client.post("/api/v1/safety/release")
    assert client.post(bp.WEBHOOK_ROUTE, json=_alert()).status_code == 200


def test_pipeline_snapshot_exposes_order(client):
    snap = client.get("/api/v1/signal/pipeline").json()
    assert snap["pipeline"] == list(bp.LOOP_A_PIPELINE)
    assert snap["processed"] >= 1


# ---------------------------------------------------------------- bots/alerts ---

def test_bot_lifecycle_via_api(client):
    created = client.post("/api/v1/bots", json={
        "strategy_id": "api_strat", "symbol": "BTC/USD", "budget_eur": 500}).json()
    bot_id = created["bot_id"]
    assert created["capital_eur"] == 500
    assert client.post(f"/api/v1/bots/{bot_id}/start").json()["bot"]["runner_status"] == "RUNNING"

    throttled = client.post(f"/api/v1/bots/{bot_id}/m8/THROTTLED").json()
    assert throttled["budget_multiplier"] == 0.5 and throttled["runner_status"] == "RUNNING"

    quarantined = client.post(f"/api/v1/bots/{bot_id}/m8/QUARANTINED").json()
    assert quarantined["runner_status"] == "QUARANTINED"

    alert = client.get("/api/strategies/api_strat/alerts").json()
    assert alert["name"] == "sigma:api_strat" and alert["status"] == "DISABLED"


def test_alert_sync_and_switch(client):
    client.post("/api/strategies/alerts_strat/alerts/sync",
                params={"symbol": "ETH/USD", "interval": 15})
    enabled = client.post("/api/strategies/alerts_strat/alerts/enable").json()
    assert enabled["status"] == "ENABLED"
    assert enabled["webhook_url"].endswith(bp.WEBHOOK_ROUTE)
    disabled = client.post("/api/strategies/alerts_strat/alerts/disable").json()
    assert disabled["status"] == "DISABLED"
    assert client.post("/api/strategies/alerts_strat/alerts/bogus").status_code == 400


# ------------------------------------------------------------------ tv jobs ---

def test_tv_job_submit_and_status(client):
    job = client.post("/api/tv/jobs/backtest", json={
        "strategy_id": "api_strat", "symbol": "BTC/USD", "interval": 15}).json()
    assert job["status"] == "queued"
    fetched = client.get(f"/api/tv/jobs/{job['job_id']}").json()
    assert fetched["job_id"] == job["job_id"]
    listed = client.get("/api/tv/jobs", params={"strategyId": "api_strat"}).json()
    assert listed["concurrency"] == 1
    assert client.post(f"/api/tv/jobs/{job['job_id']}/cancel").json()["ok"] is True
    assert client.get("/api/tv/jobs/unknown").status_code == 404


def test_tv_session_status_reports_fake_driver(client):
    s = client.get("/api/tv/session/status").json()
    assert s["driver"] in ("fake", "playwright")
    assert "categories" in s["selectors"]


# ------------------------------------------------------------- academy/scout ---

def test_academy_ingest_and_badges(client):
    for _ in range(bp.BADGE_MIN_SAMPLE + 2):
        client.post("/api/v1/academy/ingest", json={
            "strategy_id": "api_strat", "symbol": "XRP/USD", "timeframe": 5,
            "regime": bp.Regime.STRONG_BULL.value, "pnl_pct": 2.0,
            "mfe_pct": 3.0, "mae_pct": 1.0, "duration_bars": 12,
            "fee_usd": 0.2, "notional_usd": 500})
    matrix = client.get("/api/v1/academy/badges", params={"strategyId": "api_strat"}).json()
    row = next(r for r in matrix["matrix"] if r["symbol"] == "XRP/USD")
    assert row["trade_count"] >= bp.BADGE_MIN_SAMPLE and row["rating"] == "S"
    dataset = client.get("/api/v1/academy/training-dataset").json()
    assert dataset["count"] >= 1


def test_reward_matrix_and_ml_state(client):
    rm = client.get("/api/v1/reward/matrix").json()
    assert rm["weights"]["w1_pnl"] == bp.REWARD_WEIGHTS["w1_pnl"]
    ml = client.get("/api/v1/ml/self-optimizing").json()
    assert ml["brier_threshold"] == bp.BRIER_DRIFT_THRESHOLD
    rec = client.post("/api/v1/ml/record", params={"predicted": 0.8, "outcome": 1.0}).json()
    assert rec["samples"] >= 1


def test_scout_plan_endpoint(client):
    out = client.post("/api/v1/scout/plan", json=["api_strat"]).json()
    assert out["mode"] == "paper_only" and out["tasks"] >= 1


# ---------------------------------------------------------- ops / telegram ---

def test_deadman_and_memory_endpoints(client):
    import app.server.routes_sigma as routes

    snap = client.get("/api/v1/deadman").json()
    assert snap["timeout_s"] == bp.DEADMAN_TIMEOUT_SECONDS
    assert snap["expired"] is False
    assert snap["auto_pulse"] is True
    routes.set_operator_auth_override(lambda request: True)
    try:
        dm = client.post("/api/v1/deadman/beat",
                         params={"has_native_stop_loss": True}).json()
        assert dm["timeout_s"] == bp.DEADMAN_TIMEOUT_SECONDS and dm["expired"] is False
        mem = client.post("/api/v1/memory/check", params={"force": True}).json()
        assert "stage" in mem
    finally:
        routes.set_operator_auth_override(None)
    assert client.get("/api/v1/memory").json()["stages_pct"] == list(bp.MEMORY_STAGES_PCT)
    tele = client.get("/api/v1/scheduler").json()
    names = [t["name"] for tier in tele["tiers"] for t in tier.get("registered", [])]
    assert "deadman_heartbeat" in names


def test_telegram_whitelist_and_fastpath(client):
    denied = client.post("/api/v1/telegram/message",
                         json={"chat_id": "1", "text": "/kill"}).json()
    assert denied["authorized"] is False
    ok = client.post("/api/v1/telegram/message",
                     json={"chat_id": "4242", "text": "/status"}).json()
    assert ok["fast_path"] is True and "Sigma L4" in ok["text"]
    client.post("/api/v1/safety/release")


def test_safety_snapshot_shape(client):
    s = client.get("/api/v1/safety").json()
    assert s["max_daily_loss_usd"] == bp.RISK_GUARD["max_daily_loss_usd"]
    assert s["live_trading"] is False


def test_strategy_from_template_creates_pine_v6(client):
    out = client.post("/api/strategies/from-template", json={"template": "cisd"}).json()
    assert out["id"] and out["code"].startswith("//@version=6")
    assert out["parameters"]["template"] == "cisd"
    bad = client.post("/api/strategies/from-template", json={"template": "nope"})
    assert bad.status_code == 400
