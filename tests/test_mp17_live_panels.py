"""
MP-17 Live-Panels + TradingView-Alert-Treiber (Produktions-Scharfschaltung).

Deckt ab:
  * echte Panel-Berechnungen (Market Geometry, Power Physics)
  * Redis-Cache-Pfad der Panel-Engine
  * fail-closed 503 statt Mock/available=False
  * tv_driver Session-Gate (kein Fake-Transport)
  * nativer M8 -> Alert-Rückkanal
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.panels import panel_engine as pe
from app.quant.glint_orderbook_verifier import OrderbookSnapshot


class FakeAsyncRedis:
    """Minimaler async Redis-Ersatz (get/set mit TTL-Ignoranz)."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = []

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        self.writes.append((key, ex))
        return True


def _snapshot(ts=None):
    bids = [(100.0 - i * 0.5, 9.0 if i < 5 else 3.0) for i in range(20)]
    asks = [(100.5 + i * 0.5, 1.0) for i in range(20)]
    return OrderbookSnapshot(symbol="XXBTZUSD", bids=bids, asks=asks,
                             timestamp=ts if ts is not None else time.time())


# ------------------------------------------------------- market geometry ---

def test_market_geometry_computes_real_book_metrics():
    out = pe.compute_market_geometry(_snapshot())
    assert out["depth_levels"] == 20
    assert len(out["bids"]) == 20 and len(out["asks"]) == 20
    assert out["imbalance"]["bid_volume"] > out["imbalance"]["ask_volume"]
    assert out["imbalance"]["ratio"] > 0 and out["imbalance"]["pressure"] == "BID"
    assert out["vpoc"] is not None
    assert out["liquidity_clusters"]
    assert all(p < out["mid"] for p in out["support_levels"])
    assert all(p > out["mid"] for p in out["resistance_levels"])
    assert out["spread_bps"] > 0


def test_market_geometry_uses_redis_depth_cache_without_network():
    snap = _snapshot()
    redis = FakeAsyncRedis({pe.KEY_DEPTH_SNAPSHOT: json.dumps({
        "symbol": snap.symbol, "timestamp": snap.timestamp,
        "bids": [[p, v] for p, v in snap.bids],
        "asks": [[p, v] for p, v in snap.asks],
    })})
    out = asyncio.run(pe.market_geometry(redis, symbol="BTC/USD"))
    assert out["symbol"] == "XXBTZUSD"
    assert out["imbalance"]["top_n"] == 20
    # Panel wurde in den Cache zurückgeschrieben
    assert pe.KEY_PANEL_MARKET_GEOMETRY in redis.data


def test_market_geometry_rejects_empty_book():
    empty = OrderbookSnapshot(symbol="X", bids=[], asks=[], timestamp=time.time())
    with pytest.raises(pe.PanelUnavailable):
        pe.compute_market_geometry(empty)


# ---------------------------------------------------------- power physics ---

def _bars(n=20):
    bars = []
    price = 100.0
    for i in range(n):
        price *= 1.001
        bars.append({"open": price, "high": price * 1.001, "low": price * 0.999,
                     "close": price, "volume": 10.0 + i})
    return bars


def test_power_physics_derives_momentum_and_exhaustion():
    out = pe.compute_power_physics(_bars())
    assert out["bars"] == 20
    assert out["direction"] == "UP"
    assert out["kinetic_momentum"] > 0
    assert out["volatility_energy"] >= 0
    assert 0.0 <= out["exhaustion_index"] <= 1.0
    assert out["realized_volatility"] is not None
    assert out["acceleration_series"]


def test_power_physics_fails_closed_on_empty_buffer():
    with pytest.raises(pe.PanelUnavailable):
        pe.compute_power_physics(_bars(2))


# ------------------------------------------------------------------- rsi ---

def test_rsi_matches_wilder_bounds():
    up = [100.0 + i for i in range(30)]
    down = [100.0 - i for i in range(30)]
    assert pe.rsi(up) == pytest.approx(100.0)
    assert pe.rsi(down) == pytest.approx(0.0, abs=1e-6)
    assert pe.rsi([1.0, 2.0]) is None


# ----------------------------------------------------------- fractal dim ---

def test_fractal_dimension_is_two_minus_hurst():
    assert pe._fractal_dimension(0.5) == 1.5
    assert pe._fractal_dimension(None) is None


# ---------------------------------------------------------------- routes ---

@pytest.fixture(scope="module")
def client():
    """Routen-Client ohne zweiten Lifespan — die Panel-/Alert-Routen sind
    zustandslos gegenüber dem Startup-Container (Redis/DuckDB lazy)."""
    import app.server.main as main

    return TestClient(main.app)


PANEL_ROUTES = [
    "/api/v1/sigma/panels/market-geometry",
    "/api/v1/sigma/panels/quantum-regime",
    "/api/v1/sigma/panels/power-physics",
    "/api/v1/sigma/panels/glint-polymarket",
]


@pytest.fixture()
def no_network(monkeypatch):
    """Panels dürfen im Test nie wirklich ins Netz gehen."""
    import httpx

    def _blocked(*args, **kwargs):
        raise httpx.ConnectError("network disabled in tests")

    monkeypatch.setattr(httpx, "get", _blocked)
    return _blocked


@pytest.mark.parametrize("route", PANEL_ROUTES)
def test_panel_routes_never_return_stub_payloads(client, no_network, route):
    """Scharf: entweder echte Daten (ok/available true) oder 503 —
    niemals available=False mit leerem Mock-Body."""
    resp = client.get(route)
    assert resp.status_code in (200, 503), route
    body = resp.json()
    if resp.status_code == 200:
        assert body["ok"] is True and body["available"] is True
        assert body["source"] != "unknown"
        assert body["payload"]
    else:
        detail = body["detail"]
        assert detail["code"] in ("PANEL_FEED_UNAVAILABLE", "PANEL_FEED_ERROR")
        assert detail["reason"]
        assert detail["source"] != "unknown"


def test_panel_routes_are_registered_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    for route in PANEL_ROUTES:
        assert route in paths


def test_market_geometry_route_serves_live_cache(client, monkeypatch):
    snap = _snapshot()
    payload = pe.compute_market_geometry(snap)
    payload["symbol"] = snap.symbol

    async def _fake_panel(redis=None, **kwargs):
        return payload

    monkeypatch.setattr(pe, "market_geometry", _fake_panel)
    body = client.get("/api/v1/sigma/panels/market-geometry").json()
    assert body["ok"] is True
    assert body["source"] == "kraken_live_l2"
    assert body["feed"]["available"] is True
    assert body["payload"]["imbalance"]["top_n"] == 20


def test_panel_route_maps_unavailable_to_503(client, monkeypatch):
    async def _boom(redis=None, **kwargs):
        raise pe.PanelUnavailable("tick buffer empty", "duckdb_orderflow")

    monkeypatch.setattr(pe, "power_physics", _boom)
    resp = client.get("/api/v1/sigma/panels/power-physics")
    assert resp.status_code == 503
    assert resp.json()["detail"]["source"] == "duckdb_orderflow"


# ------------------------------------------------------------- tv driver ---

def test_tv_driver_is_fail_closed_without_session(tmp_path):
    from app.core.config import load_config
    from app.tv.tv_driver import (TvDriverUnavailable, get_tv_alert_driver,
                                  session_status)

    cfg = load_config()
    cfg.tv_storage_state_path = str(tmp_path / "missing.json")
    status = session_status(cfg)
    assert status["valid"] is False and status["driver"] == "unavailable"
    assert get_tv_alert_driver(cfg, required=False) is None
    with pytest.raises(TvDriverUnavailable):
        get_tv_alert_driver(cfg, required=True)


def test_tv_driver_detects_missing_auth_cookie(tmp_path):
    from app.core.config import load_config
    from app.tv.tv_driver import session_status

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cookies": [{"name": "irrelevant"}]}))
    cfg = load_config()
    cfg.tv_storage_state_path = str(path)
    status = session_status(cfg)
    assert status["present"] is True and status["valid"] is False
    assert status["error"] == "no_auth_cookie"


def test_tv_driver_accepts_valid_session(tmp_path):
    from app.core.config import load_config
    from app.tv.tv_driver import session_status

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cookies": [{"name": "sessionid"},
                                            {"name": "sessionid_sign"}]}))
    cfg = load_config()
    cfg.tv_storage_state_path = str(path)
    status = session_status(cfg)
    assert status["valid"] is True and status["driver"] == "playwright"
    assert status["auth_cookies"] == ["sessionid", "sessionid_sign"]


# -------------------------------------------------- provisioner <-> driver ---

class RecordingDriver:
    def __init__(self):
        self.calls = []

    def upsert_alert(self, **kwargs):
        self.calls.append(("upsert", kwargs["name"]))
        return {"tv_alert_id": f"tv::{kwargs['name']}"}

    def enable_alert(self, ref):
        self.calls.append(("enable", ref))
        return {"ok": True}

    def disable_alert(self, ref):
        self.calls.append(("disable", ref))
        return {"ok": True}


@pytest.fixture()
def wired_provisioner(tmp_path, monkeypatch):
    from app.core.config import load_config
    from app.tv.alert_provisioner import AlertProvisioner

    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "prov-secret")
    cfg = load_config()
    cfg.tv_jobs_dir = str(tmp_path / "jobs")
    driver = RecordingDriver()
    prov = AlertProvisioner(cfg, driver=driver,
                            store_path=str(tmp_path / "alerts.json"))
    return prov, driver


def test_provisioner_drives_real_driver_and_tracks_remote_sync(wired_provisioner):
    prov, driver = wired_provisioner
    rec = prov.upsert("s1", "BTC/USD", 15, enable=True)
    assert rec["tv_alert_id"] == "tv::sigma:s1"
    assert rec["remote_synced"] is True
    assert ("upsert", "sigma:s1") in driver.calls
    assert ("enable", "tv::sigma:s1") in driver.calls

    prov.disable("s1", reason="test")
    assert ("disable", "tv::sigma:s1") in driver.calls
    snap = prov.snapshot()
    assert snap["driver"]["attached"] is True


def test_provisioner_marks_last_error_when_driver_fails(wired_provisioner):
    prov, driver = wired_provisioner
    prov.upsert("s2", "ETH/USD", 15)

    def _boom(ref):
        raise RuntimeError("tv down")

    driver.enable_alert = _boom
    out = prov.enable("s2")
    assert out["remote_synced"] is False
    assert "enable_alert_failed" in out["last_error"]


def test_provisioner_stays_local_without_session(tmp_path, monkeypatch):
    from app.core.config import load_config
    from app.tv.alert_provisioner import AlertProvisioner

    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "prov-secret")
    cfg = load_config()
    cfg.tv_jobs_dir = str(tmp_path / "jobs")
    cfg.tv_storage_state_path = str(tmp_path / "no_session.json")
    prov = AlertProvisioner(cfg, store_path=str(tmp_path / "alerts.json"))
    assert prov.driver is None
    rec = prov.upsert("s3", "BTC/USD", 15, enable=True)
    assert rec["enabled"] is True           # lokaler Zustand bleibt konsistent
    assert rec["remote_synced"] is False
    assert rec["last_error"] in ("no_tv_session", prov.driver_error)


# ------------------------------------------------------ M8 lifecycle wire ---

class FakeM8:
    def __init__(self, states):
        self._states = states

    async def scan_states(self):
        return self._states


def test_sync_all_with_m8_applies_alert_matrix(wired_provisioner):
    prov, driver = wired_provisioner
    prov.upsert("act", "BTC/USD", 15, enable=True)
    prov.upsert("quar", "ETH/USD", 15, enable=True)
    prov.upsert("thr", "SOL/USD", 15, enable=True)
    prov.upsert("ghost", "XRP/USD", 15, enable=True)
    driver.calls.clear()

    m8 = FakeM8({
        "act": {"status": "ACTIVE"},
        "quar": {"status": "QUARANTINED"},
        "thr": {"status": "THROTTLED"},
    })
    out = asyncio.run(prov.sync_all_with_m8(m8))
    assert out["ok"] is True and out["synced"] == 3
    actions = {r["strategy_id"]: r["action"] for r in out["results"]}
    assert actions == {"act": "enable", "quar": "disable", "thr": "keep"}
    assert prov.get("quar").enabled is False
    assert prov.get("thr").enabled is True          # THROTTLED lässt Alert an
    assert out["orphans_disabled"] == ["ghost"]
    assert prov.get("ghost").enabled is False


def test_sync_all_with_m8_reports_scan_failure(wired_provisioner):
    prov, _ = wired_provisioner

    class Broken:
        async def scan_states(self):
            raise RuntimeError("redis gone")

    out = asyncio.run(prov.sync_all_with_m8(Broken()))
    assert out["ok"] is False and "m8_scan_failed" in out["reason"]


def test_m8_engine_pushes_status_changes_into_alerts(wired_provisioner):
    from app.core.config import SigmaConfig
    from app.execution.M8StateEngine import M8StateEngine

    prov, driver = wired_provisioner
    prov.upsert("s9", "BTC/USD", 15, enable=True)
    driver.calls.clear()

    engine = M8StateEngine(None, SigmaConfig())
    engine.alert_provisioner = prov
    asyncio.run(engine.register_strategy("s9", base_budget_usd=100.0))
    asyncio.run(engine.quarantine("s9", reason="test"))
    assert prov.get("s9").enabled is False
    assert any(call[0] == "disable" for call in driver.calls)

    asyncio.run(engine.promote("s9", force=True))
    assert prov.get("s9").enabled is True
    assert any(call[0] == "enable" for call in driver.calls)


def test_alert_driver_status_route_exposes_session(client):
    body = client.get("/api/v1/alerts/driver").json()
    assert "driver" in body and "attached" in body
    session = client.get("/api/tv/session/status").json()
    assert "alert_driver" in session
