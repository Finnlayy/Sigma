"""
API-Vertrag (§7) — Webhook, Safety, Bots, Alerts, TV-Jobs, Academy,
Telegram, Health/Blueprint. Läuft gegen die echte FastAPI-App.
"""
from __future__ import annotations

import os

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core import blueprint as bp

from sigma.ports.polymarket_gamma_feeder import (
    TRAJECTORY_WEIGHTS,
    GammaFeederPort,
    parse_gamma_payload,
    set_gamma_port,
)
from app.ingestion.kraken_depth_adapter import KrakenDepthAdapter
from app.quant.glint_orderbook_verifier import GlintOrderbookVerifier, get_verifier, set_verifier


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
    assert f"POST {bp.WEBHOOK_INGEST_ROUTE}" in b["api_contract"]


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


def test_legacy_webhook_forwards_schema_a_when_live_trading(client):
    import app.server.routes_sigma as routes
    from app.quant.glint_orderbook_verifier import OrderbookSnapshot

    pipe = routes.pipeline()
    previous = pipe.config.live_trading
    previous_secret = pipe.config.webhook_secret
    previous_open = pipe.open_positions
    long_secret = "api-secret-token16"
    pipe.config.live_trading = True
    pipe.config.webhook_secret = long_secret
    pipe.safety.config.webhook_secret = long_secret
    routes.set_depth_adapter(type("_Depth", (), {
        "fetch": staticmethod(lambda symbol: OrderbookSnapshot(
            symbol, [(49_999.0, 80.0)], [(50_001.0, 20.0)], time.time()
        ))
    })())
    try:
        response = client.post(bp.WEBHOOK_ROUTE, json=_alert(secret=long_secret),
                               headers={bp.WEBHOOK_SECRET_HEADER: long_secret})
        assert response.status_code == 200
        assert response.json()["accepted"] is True
        schema_a = {
            "secret": long_secret,
            "idempotency_key": f"sig_live_fwd_{int(time.time())}_ok",
            "strategy_id": "api_strat",
            "bot_id": "bot_api",
            "symbol": "KRAKEN:XBTUSD",
            "action": "BUY",
            "order_type": "MARKET",
            "price": 50_000.0,
            "stop_loss": 49_000.0,
            "take_profit": 52_000.0,
            "fixed_leverage": 1,
            "execution_mode": "live",
            "timestamp": int(time.time()),
            "features": {"rsi": 28.0, "atr": 500.0, "cisd_score": 0.7},
        }
        forwarded = client.post(bp.WEBHOOK_ROUTE, json=schema_a)
        assert forwarded.status_code == 200, forwarded.json()
        assert forwarded.json()["status"] == "EXECUTED"
        assert forwarded.json()["schema_family"] == "SIGMA_L4_MASTER"
    finally:
        pipe.config.live_trading = previous
        pipe.config.webhook_secret = previous_secret
        pipe.safety.config.webhook_secret = previous_secret
        pipe.open_positions = previous_open
        routes.set_depth_adapter(None)


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
    assert enabled["webhook_url"].endswith(bp.WEBHOOK_INGEST_ROUTE)
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
    assert s["login_url"].startswith("https://www.tradingview.com")
    assert s["live_trading"] is False


def test_tv_session_login_opens_tradingview_without_live(client):
    from app.tv import chrome_login

    calls = []

    def fake_open(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "open": True,
            "reused": False,
            "launched": True,
            "url": bp.TV_LOGIN_URL,
            "mode": "test",
            "chrome_binary": "/usr/bin/google-chrome",
        }

    chrome_login.set_tv_chrome_opener(fake_open)
    try:
        out = client.post("/api/tv/session/login").json()
        assert out["ok"] is True
        assert out["live_trading"] is False
        assert out["url"] == bp.TV_LOGIN_URL
        assert "tradingview.com" in out["url"]
        assert calls
        again = client.post("/api/tv/session/login").json()
        assert again["live_trading"] is False
        assert len(calls) == 2
        contract = client.get("/api/v1/blueprint").json()["api_contract"]
        assert "POST /api/tv/session/login" in contract
    finally:
        chrome_login.set_tv_chrome_opener(None)


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
    tele = client.get("/api/v1/scheduler")
    assert tele.status_code == 200
    payload = tele.json()
    json.dumps(payload)
    names = [t["name"] for tier in payload["tiers"] for t in tier.get("registered", [])]
    assert "deadman_heartbeat" in names
    assert "scorecard_stage1_idle" in names
    t0 = next(tier for tier in payload["tiers"] if tier["tier"] == 0)
    for task in t0.get("registered", []):
        assert task["next_run"] is None


def test_library_snapshot_omits_code_and_scorecard_writes(client):
    import app.server.routes_sigma as routes

    snap = client.get("/api/v1/strategies/library-snapshot").json()
    assert "strategies" in snap
    for row in snap["strategies"]:
        assert "code" not in row
        assert "lamp" in row
    routes.set_operator_auth_override(lambda request: True)
    try:
        denied = client.put("/api/v1/strategies/missing/slots", json={"slots": []})
        # override is on — unknown strategy still upserts empty list
        assert denied.status_code in (200, 404)
        if snap["strategies"]:
            sid = snap["strategies"][0]["id"]
            put = client.put(f"/api/v1/strategies/{sid}/slots", json={
                "slots": [{"symbol": "ETH/USD", "timeframe": "15", "favorite": True, "locked": False}],
            })
            assert put.status_code == 200
            body = put.json()
            assert body["slots"][0]["origin"] == "user"
            assert body["slots"][0]["lamp"] == "green_solid"
            card = client.get(f"/api/v1/strategies/{sid}/scorecard").json()
            assert "code" not in card["strategy"]
            init = client.post(f"/api/v1/strategies/{sid}/initialize")
            assert init.status_code == 200
            assert init.json()["ok"] is True
    finally:
        routes.set_operator_auth_override(None)


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


def test_tv_library_list_and_import_is_idempotent_paper(client):
    from app.services import tv_library_service as tvlib
    from app.tv.strategy_tester_driver import FakeStrategyTesterDriver

    tvlib.set_tv_library_driver_factory(FakeStrategyTesterDriver)
    try:
        catalog = client.get("/api/strategies/tv/scripts").json()
        assert catalog["source"] == "driver"
        ids = {row["tv_script_id"] for row in catalog["scripts"]}
        assert "PUB;fake1" in ids
        first = client.post("/api/strategies/tv/sync-library",
                            json={"script_ids": ["PUB;fake1"]}).json()
        assert first["ok"] is True
        assert first["imported_count"] == 1
        assert first["skipped_count"] == 0
        assert first["execution_mode"] == "paper"
        assert first["live_trading"] is False
        row = first["strategies"][0]
        assert row["executionMode"] == "paper"
        assert row["status"] == "inactive"
        assert row["tv_script_id"] == "PUB;fake1"
        assert row["code"].startswith("//@version=6")
        lib = client.get("/api/strategies").json()
        assert sum(1 for s in lib if s.get("tv_script_id") == "PUB;fake1") == 1
        again = client.post("/api/strategies/tv/sync-library",
                            json={"script_ids": ["PUB;fake1"]}).json()
        assert again["imported_count"] == 0
        assert again["skipped_count"] == 1
        lib2 = client.get("/api/strategies").json()
        assert sum(1 for s in lib2 if s.get("tv_script_id") == "PUB;fake1") == 1
        live_ignored = client.post("/api/strategies/tv/sync-library",
                                   json={"script_ids": ["USER;fake2"], "execution_mode": "live"}).json()
        assert live_ignored["imported_count"] == 1
        assert live_ignored["strategies"][0]["executionMode"] == "paper"
        contract = client.get("/api/v1/blueprint").json()["api_contract"]
        assert "GET /api/strategies/tv/scripts" in contract
        assert "POST /api/strategies/tv/sync-library" in contract
    finally:
        tvlib.set_tv_library_driver_factory(None)


def test_sync_balance_without_keys_is_empty(client, monkeypatch):
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    import app.server.main as main

    main.state.has_credentials = True
    main.state.live_kraken_balances = {"USD": 99.0}
    main.state.live_kraken_sync_ts = 1.0
    try:
        body = client.post("/api/kraken/sync-balance").json()
        assert body["hasCredentials"] is False
        assert body["liveKrakenBalances"] == {}
        assert body["lastSyncTimestamp"] is None
        assert body["balances"] == {}
        assert body.get("portfolioUSD") == 0.0
        metrics = client.get("/api/logs").json()["metrics"]
        assert metrics["liveKrakenBalances"] == {}
        assert metrics["hasCredentials"] is False
    finally:
        main.state.has_credentials = False
        main.state.live_kraken_balances = {}
        main.state.live_kraken_sync_ts = None


def test_sync_balance_with_keys_normalizes_and_logs_use_cache(client, monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    import app.server.main as main
    from app.execution.KrakenCliBridge import OrderResult

    calls = []

    class FakeBridge:
        def balance(self):
            calls.append(1)
            return OrderResult(True, "live", stdout=json.dumps({"XXBT": "0.5", "ZUSD": "1000"}))

    original = main.state.kraken_cli
    main.state.kraken_cli = FakeBridge()
    try:
        body = client.post("/api/kraken/sync-balance").json()
        assert body["hasCredentials"] is True
        assert body["liveKrakenBalances"] == {"BTC": 0.5, "USD": 1000.0}
        assert body["lastSyncTimestamp"] is not None
        assert calls == [1]
        metrics = client.get("/api/logs").json()["metrics"]
        assert metrics["liveKrakenBalances"] == {"BTC": 0.5, "USD": 1000.0}
        assert metrics["hasCredentials"] is True
        assert calls == [1]
    finally:
        main.state.kraken_cli = original
        main.state.has_credentials = False
        main.state.live_kraken_balances = {}
        main.state.live_kraken_sync_ts = None


def test_sync_balance_cli_error_clears_without_paper_seed(client, monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    import app.server.main as main
    from app.execution.KrakenCliBridge import OrderResult

    class FakeBridge:
        def balance(self):
            return OrderResult(False, "live", error_code="EAPI:Invalid key")

    original = main.state.kraken_cli
    main.state.kraken_cli = FakeBridge()
    main.state.live_kraken_balances = {"BTC": 9.0}
    try:
        body = client.post("/api/kraken/sync-balance").json()
        assert body["hasCredentials"] is True
        assert body["liveKrakenBalances"] == {}
        assert body["lastSyncTimestamp"] is None
        assert body["balances"] == {}
        paper = client.get("/api/logs").json()["balances"]
        assert paper != {}
        assert body["balances"] != paper
        snap = main.state.store.get_live_kraken_snapshot()
        assert snap is not None
        assert snap["balances"] == {}
        assert snap["error"]
    finally:
        main.state.kraken_cli = original
        main.state.has_credentials = False
        main.state.live_kraken_balances = {}
        main.state.live_kraken_sync_ts = None


def test_live_balance_snapshot_hydrates_cache(client, monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "k")
    monkeypatch.setenv("KRAKEN_API_SECRET", "s")
    import app.server.main as main
    from app.execution.KrakenCliBridge import OrderResult

    class FakeBridge:
        def balance(self):
            return OrderResult(True, "live", stdout=json.dumps({"XXBT": "0.25"}))

    original = main.state.kraken_cli
    main.state.kraken_cli = FakeBridge()
    try:
        client.post("/api/kraken/sync-balance")
        snap = main.state.store.get_live_kraken_snapshot()
        assert snap["balances"]["BTC"] == 0.25
        main.state.live_kraken_balances = {}
        main.state.live_kraken_sync_ts = None
        main.state.has_credentials = True
        main.state._hydrate_live_kraken_cache()
        assert main.state.live_kraken_balances["BTC"] == 0.25
        main.state.has_credentials = False
        main.state._hydrate_live_kraken_cache()
        assert main.state.live_kraken_balances == {}
    finally:
        main.state.kraken_cli = original
        main.state.has_credentials = False
        main.state.live_kraken_balances = {}
        main.state.live_kraken_sync_ts = None


def test_zero_mock_seams_are_honest_empty(client):
    sent = client.post("/api/quant/sentiment/score", json={"text": "SEC approves ETF"}).json()
    assert sent.get("available") is False
    assert sent.get("score") is None
    assert sent.get("model") == "unavailable"
    ai = client.post("/api/ai/suggest", json={"prompt": "rsi mean reversion btc 15m"}).json()
    assert ai.get("ok") is False and ai.get("code") == ""
    assert "rsi_reversion" not in str(ai.get("parameters"))
    pro = client.get("/api/kraken/positions/pro").json()
    assert pro.get("live") is False and pro.get("positions") == []
    assert pro.get("totalCollateralUSD") is None
    rl = client.post("/api/quant/engine/rl-fast-path").json()
    assert rl.get("q_value") is None and rl.get("available") is False
    lake = client.post("/api/lake/sync", json={}).json()
    assert lake.get("ok") is False and lake.get("mode") == "disabled"
    poly = client.get("/api/quant/polymarket/layer0").json()
    assert poly.get("valid") is False and poly.get("reason") == "missing_data"


# =============================================================================
# MP-17 — Sigma Panel-/Research-Routen (fail-closed, ohne Fachmodule)
# =============================================================================

MP17_SIGMA_GET_ENDPOINTS = [
    "/api/v1/sigma/regime",
    "/api/v1/sigma/risk",
    "/api/v1/sigma/power",
    "/api/v1/sigma/zones",
    "/api/v1/sigma/scout",
    "/api/v1/sigma/polymarket",
    "/api/v1/sigma/exhaustion",
    "/api/v1/sigma/provisions",
    "/api/v1/sigma/ladder/preview",
    "/api/v1/sigma/fractal/preview",
    "/api/v1/sigma/onnx",
    "/api/v1/sigma/orderflow",
]


@pytest.mark.parametrize("endpoint", MP17_SIGMA_GET_ENDPOINTS)
def test_mp17_sigma_get_endpoints_fail_closed(client, endpoint: str):
    """MP-17 — ohne Fachmodule: ok=False, available=False, feed unknown;
    niemals synthetische Werte; stabile strukturierte Antwort.
    /orderflow ist fail-closed, solange keine JIT-Audits vorliegen
    (Verifier-Historie isolieren)."""
    if endpoint == "/api/v1/sigma/orderflow":
        from app.quant.glint_orderbook_verifier import get_verifier
        get_verifier()._history.clear()
    resp = client.get(endpoint)
    assert resp.status_code == 200, endpoint
    body = resp.json()
    assert body["ok"] is False
    assert body["available"] is False
    assert body["feed"]["source"] == "unknown"
    assert body["generated_at"]  # ISO-8601 UTC


def test_mp17_risk_rules_never_toggleable(client):
    """MP-01-Sicherheitsregeln sind im UI nur sichtbar, nie abschaltbar."""
    rules = client.get("/api/v1/sigma/risk").json()["rules"]
    assert len(rules) >= 3
    for rule in rules:
        assert rule["enabled"] is True
    labels = " ".join(r["label"] for r in rules)
    assert "Hard-Stop" in labels
    assert "6 %" in labels or "6%" in labels
    assert "Fee-Covered" in labels or "fee-covered" in labels


def test_mp17_orderflow_and_polymarket_fail_closed_gate(client):
    # Orderflow: fail-closed, solange keine JIT-Audits existieren
    # (Isolation: Verifier-Singleton-Historie leeren)
    from app.quant.glint_orderbook_verifier import get_verifier
    get_verifier()._history.clear()
    of = client.get("/api/v1/sigma/orderflow").json()
    assert of["ok"] is False
    assert of["reason"] == "orderflow_port_not_available"
    poly = client.get("/api/v1/sigma/polymarket").json()
    assert poly["gate_open"] is False  # ohne Feed Gate inaktiv


def test_mp17_write_actions_rejected_without_token(client):
    """Schreibzugriffe ohne Operator-Token -> 403."""
    assert client.post("/api/v1/sigma/scan").status_code == 403
    assert client.post("/api/v1/sigma/provisions",
                       json={"strategy_id": "x"}).status_code == 403
    assert client.post("/api/v1/sigma/provisions/de-provision",
                       json={}).status_code == 403
    assert client.post("/api/v1/sigma/provisions/harden", json={}).status_code == 403
    assert client.post("/api/v1/research/run",
                       json={"hypothesis": "H1"}).status_code == 403


def test_mp17_write_actions_with_token_fail_closed(client):
    """Mit Token: strukturierte fail-closed Antworten (kein Backend)."""
    import app.server.routes_sigma as routes

    routes.set_operator_auth_override(lambda request: True)
    try:
        h = {"X-Sigma-Settings-Token": "test"}
        r = client.post("/api/v1/sigma/scan", headers=h)
        assert r.status_code == 200
        assert r.json()["reason"] == "scan_backend_not_available"
        r = client.post("/api/v1/sigma/provisions/harden", json={}, headers=h)
        assert r.json()["detail"]["hardening_ok"] is False  # kein Code nach außen
        r = client.post("/api/v1/research/run", json={"hypothesis": "H3"}, headers=h)
        assert r.json()["reason"] == "research_backend_not_available"
        # unbekannte Hypothese -> 422 (Auth geht vor Validierung)
        assert client.post("/api/v1/research/run", json={"hypothesis": "H9"},
                           headers=h).status_code == 422
    finally:
        routes.set_operator_auth_override(None)


def test_mp17_research_jobs_and_dashboard_fail_closed(client):
    job = client.get("/api/v1/research/jobs/xyz").json()
    assert job["status"] == "unavailable"
    assert job["job_id"] == "xyz"
    dash = client.get("/api/v1/research/dashboard").json()
    assert dash["ok"] is False
    assert dash["hypotheses"] == []
    assert dash["sweeps"] == []


def test_queue_matrices_groups_trades_by_strategy_and_mode(client):
    """HTTP contract for O(T) queue grouping. Numeric checks on our rows only
    so leftover factory/webhook trades in this module-scoped store cannot flake.
    """
    import app.server.main as main

    paper = client.post("/api/strategies", json={
        "id": "qm_paper", "name": "QM Paper", "assetPair": "BTC/USD",
        "executionMode": "paper", "status": "active", "interval": 15,
    }).json()
    live = client.post("/api/strategies", json={
        "id": "qm_live", "name": "QM Live", "assetPair": "ETH/USD",
        "executionMode": "live", "status": "inactive", "interval": 30,
    }).json()
    assert paper["id"] == "qm_paper" and live["id"] == "qm_live"

    def trade(tid, sid, mode, pnl, notional, exit_time, status="closed",
              symbol="BTC/USD", name=None):
        return {
            "trade_id": tid, "strategy_id": sid,
            "strategy_name": name or sid, "status": status,
            "execution_mode": mode, "symbol": symbol,
            "direction": "LONG", "side": "buy",
            "net_pnl_usd": pnl, "notional_usd": notional,
            "entry_time": exit_time, "exit_time": exit_time,
            "entry_price": 100.0, "quantity": 1.0,
        }

    store = main.state.store
    store.upsert_trade(trade("qm1", "qm_paper", "paper", 10.0, 200.0,
                             "2026-08-01 10:00:00", name="QM Paper"))
    store.upsert_trade(trade("qm2", "qm_paper", "", 5.0, 50.0,
                             "2026-08-01 11:00:00", name="QM Paper"))
    store.upsert_trade(trade("qm3", "qm_paper", "paper", -4.0, 80.0,
                             "2026-08-01 12:00:00", name="QM Paper"))
    store.upsert_trade(trade("qm0", "qm_paper", "paper", 0.0, 30.0,
                             "2026-08-01 09:00:00", name="QM Paper"))
    store.upsert_trade(trade("qm_open", "qm_paper", "paper", 1.0, 10.0,
                             "2026-08-01 13:00:00", status="open", name="QM Paper"))
    store.upsert_trade(trade("qm5", "qm_live", "live", 20.0, 100.0,
                             "2026-08-01 14:00:00", symbol="ETH/USD", name="QM Live"))

    body = client.get("/api/queue-matrices").json()
    prow = next(s for s in body["paper"]["strategies"] if s["strategyId"] == "qm_paper")
    lrow = next(s for s in body["live"]["strategies"] if s["strategyId"] == "qm_live")

    assert prow["totalTrades"] == 4
    assert prow["winningTrades"] == 2
    assert prow["losingTrades"] == 2
    assert prow["realizedPnL"] == 11.0
    assert prow["totalPnL"] == 11.0
    assert prow["volumeTradedUSD"] == 360.0
    assert prow["profitFactor"] == 3.75
    assert prow["winRate"] == 50.0
    assert prow["bestTrade"] == 10.0
    assert prow["worstTrade"] == -4.0
    assert prow["avgTradeReturn"] == 2.75
    assert prow["maxDrawdown"] == 26.6667
    assert prow["executionMode"] == "paper"
    assert prow["status"] == "active"
    assert {t["id"] for t in prow["trades"]} == {"qm1", "qm2", "qm3", "qm0"}

    assert lrow["totalTrades"] == 1
    assert lrow["winningTrades"] == 1
    assert lrow["losingTrades"] == 0
    assert lrow["realizedPnL"] == 20.0
    assert lrow["volumeTradedUSD"] == 100.0
    assert lrow["profitFactor"] == 999.0
    assert lrow["executionMode"] == "live"
    assert {t["id"] for t in lrow["trades"]} == {"qm5"}

    ours = [p for p in body["paper"]["pnlTrajectory"] if p["strategyName"] == "QM Paper"]
    assert [p["time"] for p in ours] == [
        "2026-08-01T09:00:00",
        "2026-08-01T10:00:00",
        "2026-08-01T11:00:00",
        "2026-08-01T12:00:00",
    ]
    assert [p["tradePnL"] for p in ours] == [0.0, 10.0, 5.0, -4.0]
    assert "qm_live" not in {s["strategyId"] for s in body["paper"]["strategies"]}
    assert "qm_paper" not in {s["strategyId"] for s in body["live"]["strategies"]}
    assert "qm_open" not in {t["id"] for t in prow["trades"]}
    assert body["paper"]["activeWorkers"] >= 1


# =============================================================================
# GLINT-POLYMARKET-WIRING — echte gemappte Payloads (offline, ein App-Start)
# =============================================================================

GAMMA_PAYLOAD = {
    "slug": "btc-macro-42",
    "title": "Will Bitcoin close above $110,000 by end of August?",
    "volume24hr": 2_500_000.0,
    "liquidity": 1_100_000.0,
    "markets": [
        {"groupItemTitle": "100000", "outcomePrices": [0.98, 0.02],
         "synthetic": False},
        {"groupItemTitle": "105000", "outcomePrices": [0.90, 0.10],
         "synthetic": False},
        {"groupItemTitle": "110000", "outcomePrices": [0.60, 0.40],
         "synthetic": False},
        {"groupItemTitle": "115000", "outcomePrices": [0.20, 0.80],
         "synthetic": False},
    ],
}

KRAKEN_DEPTH_PAYLOAD = {
    "error": [],
    "result": {
        "XBTUSD": {
            "bids": [
                ["67000.0", "1.5", 1700000000],
                ["66950.0", "2.0", 1700000000],
                ["66800.0", "5.0", 1700000000],
            ],
            "asks": [
                ["67050.0", "1.0", 1700000000],
                ["67100.0", "1.5", 1700000000],
                ["67200.0", "4.0", 1700000000],
            ],
        }
    },
}


def _wiring_gamma_port() -> GammaFeederPort:
    import time
    odds = parse_gamma_payload(GAMMA_PAYLOAD, spot_price=107_000.0,
                               now=time.time())
    return GammaFeederPort(odds)


def test_wiring_polymarket_real_payload_with_port(client):
    """GET /api/v1/sigma/polymarket liefert echte Gamma-Dichten,
    mu und Trajektorien; gate_open bleibt False (Telemetrie)."""
    set_gamma_port(_wiring_gamma_port())
    try:
        body = client.get("/api/v1/sigma/polymarket").json()
        assert body["ok"] is True
        assert body["available"] is True
        assert body["feed"]["source"] == "polymarket_gamma"
        assert body["mu"] is not None and body["mu"] > 0
        assert len(body["density_bins"]) == 5
        assert body["gate_open"] is False      # nie Trade-Blocker
        assert body["gate_060"] is True
        assert set(body["trajectories"]) == set(TRAJECTORY_WEIGHTS)
        assert body["spot_price"] == 107_000.0
        assert body["stale"] is False
    finally:
        set_gamma_port(None)


def test_wiring_polymarket_fail_closed_without_port(client):
    set_gamma_port(None)
    body = client.get("/api/v1/sigma/polymarket").json()
    assert body["ok"] is False
    assert body["available"] is False
    assert body["gate_open"] is False
    assert body["invalid_reason"] == "no_port"


def test_wiring_polymarket_fail_closed_stale(client):
    import time
    odds = parse_gamma_payload(GAMMA_PAYLOAD, spot_price=107_000.0,
                               now=time.time() - 600.0, ttl_s=300.0)
    set_gamma_port(GammaFeederPort(odds))
    try:
        body = client.get("/api/v1/sigma/polymarket").json()
        assert body["ok"] is False
        assert body["invalid_reason"] == "stale_snapshot"
    finally:
        set_gamma_port(None)


def test_wiring_orderflow_real_audit_with_history(client):
    """GET /api/v1/sigma/orderflow liefert echten Kraken-L2-JIT-Audit
    (i_depth, spread, size_multiplier, audit_status)."""
    verifier = GlintOrderbookVerifier()
    set_verifier(verifier)
    try:
        verifier.verify(
            KrakenDepthAdapter().snapshot_from_payload(
                KRAKEN_DEPTH_PAYLOAD, "XBTUSD", 1700000000.0),
            "BULLISH", now=1700000000.0,
        )
        body = client.get("/api/v1/sigma/orderflow").json()
        assert body["ok"] is True
        assert body["available"] is True
        assert body["feed"]["source"] == "kraken_l2_jit"
        assert body["i_depth"] is not None
        assert body["size_multiplier"] == 1.0
        assert body["audit_status"] is not None
        assert body["audits"]
    finally:
        set_verifier(None)


def test_wiring_orderflow_fail_closed_without_audits(client):
    set_verifier(GlintOrderbookVerifier())  # leere History
    try:
        body = client.get("/api/v1/sigma/orderflow").json()
        assert body["ok"] is False
        assert body["reason"] == "orderflow_port_not_available"
    finally:
        set_verifier(None)


def test_wiring_preflight_jit_audit_in_lifecycle():
    """StrategyLifecycleService._preflight nutzt den bestehenden
    JIT-Orderbuch-Audit (kein Blind-Entry ohne Konfluenz)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "app", "services", "strategy_lifecycle_service.py")
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "self.verifier.verify" in src
    assert "ORDERBOOK_AUDIT_MISSING" in src
    assert "ORDERBOOK_DEPTH_UNAVAILABLE" in src
