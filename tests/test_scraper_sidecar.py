"""
=========================================================
Datei:      tests/test_scraper_sidecar.py
Zweck:      §6 Loop C — Vertragstests für das Scraper-Sidecar
            (`app/scraper/*`): Endpunkte laut `bp.SCRAPER_ENDPOINTS`,
            Cache, Rate-Limit, Retry, Stale-Serve und der
            deterministische Offline-Fallback.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core import blueprint as bp
from app.scraper import synthetic
from app.scraper.cache import TTLCache, TokenBucket, cache_key
from app.scraper.settings import ScraperSettings
from app.scraper.vendor_bridge import (VendorBridge, VendorUnavailable, _unwrap,
                                       _unwrap_list, normalize_candles)


# =============================================================================
# Cache & Rate Limit
# =============================================================================

def test_ttl_cache_hit_and_expiry():
    cache = TTLCache(max_entries=4)
    cache.set("k", {"v": 1})
    assert cache.get("k", ttl_s=60) == {"v": 1}
    assert cache.get("k", ttl_s=-1) is None          # abgelaufen
    assert cache.get_stale("k") == {"v": 1}          # aber weiterhin vorhanden
    assert cache.stats.stale_serves == 1


def test_ttl_cache_evicts_lru():
    cache = TTLCache(max_entries=2)
    for key in ("a", "b", "c"):
        cache.set(key, key)
    assert len(cache) == 2
    assert cache.get("a", 60) is None
    assert cache.stats.evictions == 1


def test_cache_key_is_case_insensitive():
    assert cache_key("ohlcv", "kraken", "xbtusd", "15m", 100) == \
        cache_key("OHLCV", "KRAKEN", "XBTUSD", "15M", 100)


def test_token_bucket_limits_and_recovers():
    bucket = TokenBucket(rate_per_min=60.0, burst=3)
    assert [bucket.acquire() for _ in range(3)] == [True, True, True]
    assert bucket.acquire() is False
    assert bucket.retry_after_s() > 0
    snap = bucket.snapshot()
    assert snap["rejected"] == 1 and snap["granted"] == 3


# =============================================================================
# Vendor-Bridge
# =============================================================================

def test_normalize_candles_sorts_and_converts_ms():
    rows = normalize_candles([
        {"timestamp": 1_700_000_060_000, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 9},
        {"timestamp": 1_700_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 8},
    ])
    assert [r["timestamp"] for r in rows] == [1_700_000_000, 1_700_000_060]
    assert [r["index"] for r in rows] == [0, 1]
    assert rows[0]["close"] == 1.5


@pytest.mark.parametrize("status", ["error", "failed", "rate_limited"])
def test_unwrap_rejects_non_success_status(status):
    with pytest.raises(VendorUnavailable):
        _unwrap({"status": status, "data": {"a": 1}})
    with pytest.raises(VendorUnavailable):
        _unwrap_list({"status": status, "data": [{"a": 1}]})


def test_unwrap_rejects_error_and_empty_payloads():
    with pytest.raises(VendorUnavailable):
        _unwrap({"status": "error", "error": "boom"})
    with pytest.raises(VendorUnavailable):
        _unwrap_list({"status": "success", "data": []})
    assert _unwrap_list({"status": "success", "data": [{"a": 1}]}) == [{"a": 1}]


def test_bridge_retries_then_raises():
    bridge = VendorBridge(ScraperSettings(max_retries=2, retry_backoff_s=0.0))
    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise RuntimeError("network down")

    with pytest.raises(VendorUnavailable):
        bridge._call("probe", _boom)
    assert calls["n"] == 3                     # 1 Versuch + 2 Retries
    assert bridge.status()["failures"] == 3


def test_circuit_breaker_opens_and_short_circuits():
    bridge = VendorBridge(ScraperSettings(max_retries=0, retry_backoff_s=0.0,
                                          breaker_failures=2, breaker_cooldown_s=30.0))
    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise RuntimeError("network down")

    for _ in range(2):
        with pytest.raises(VendorUnavailable):
            bridge._call("probe", _boom)
    assert bridge.circuit_open is True
    assert calls["n"] == 2

    with pytest.raises(VendorUnavailable) as err:      # kein weiterer Upstream-Call
        bridge._call("probe", _boom)
    assert "circuit open" in str(err.value)
    assert calls["n"] == 2

    bridge.reset_circuit()
    assert bridge.circuit_open is False


def test_success_resets_consecutive_failures():
    bridge = VendorBridge(ScraperSettings(max_retries=0, retry_backoff_s=0.0,
                                          breaker_failures=3))
    with pytest.raises(VendorUnavailable):
        bridge._call("probe", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    bridge._call("probe", lambda: "ok")
    assert bridge.status()["consecutive_failures"] == 0
    assert bridge.circuit_open is False


def test_bridge_returns_on_first_success():
    bridge = VendorBridge(ScraperSettings(max_retries=2, retry_backoff_s=0.0))
    assert bridge._call("probe", lambda: "ok") == "ok"
    assert bridge.status()["failures"] == 0


def test_offline_bridge_is_never_available():
    bridge = VendorBridge(ScraperSettings(offline=True))
    assert bridge.available is False


# =============================================================================
# Synthetic Fallback
# =============================================================================

def test_synthetic_candles_are_deterministic_and_ordered():
    a = synthetic.candles("KRAKEN", "XBTUSD", 15, 50, now=1_800_000_000)
    b = synthetic.candles("KRAKEN", "XBTUSD", 15, 50, now=1_800_000_000)
    assert a == b
    assert len(a) == 50
    assert all(a[i]["timestamp"] < a[i + 1]["timestamp"] for i in range(len(a) - 1))
    assert all(c["low"] <= c["open"] <= c["high"] for c in a)
    assert all(c["low"] <= c["close"] <= c["high"] for c in a)


def test_synthetic_spacing_matches_timeframe():
    rows = synthetic.candles("KRAKEN", "ETHUSD", 5, 10, now=1_800_000_000)
    assert rows[1]["timestamp"] - rows[0]["timestamp"] == 300


def test_synthetic_movers_respect_category():
    gainers = synthetic.movers("crypto", "gainers", 5)
    losers = synthetic.movers("crypto", "losers", 5)
    assert len(gainers) == 5
    assert all(row["change"] > 0 for row in gainers)
    assert all(row["change"] < 0 for row in losers)


# =============================================================================
# HTTP-Vertrag
# =============================================================================

@pytest.fixture(scope="module")
def client():
    os.environ["SIGMA_SCRAPER_OFFLINE"] = "1"      # kein Netz in Tests
    os.environ["SIGMA_SCRAPER_MOUNT_VENDOR"] = "0"
    from app.scraper import main as sidecar

    sidecar.SETTINGS.offline = True
    sidecar.SETTINGS.mount_vendor_app = False
    sidecar.CACHE.clear()
    with TestClient(sidecar.app) as test_client:
        yield test_client
    os.environ.pop("SIGMA_SCRAPER_OFFLINE", None)
    os.environ.pop("SIGMA_SCRAPER_MOUNT_VENDOR", None)


def test_health_reports_vendor_and_endpoints(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "sigma-tv-scraper"
    assert body["endpoints"] == dict(bp.SCRAPER_ENDPOINTS)
    assert body["degraded"] is True                # offline mode
    assert "cache" in body and "rate_limit" in body


def test_root_advertises_loop_c(client):
    body = client.get("/").json()
    assert body["loop"] == "C"
    assert body["health"] == bp.SCRAPER_HEALTH_ROUTE


@pytest.mark.parametrize("path", [
    "/api/ohlcv/KRAKEN/XBTUSD",
    "/api/indicators/KRAKEN/XBTUSD",
    "/api/overview/KRAKEN/XBTUSD",
    "/api/movers",
    "/api/screener",
])
def test_every_blueprint_endpoint_answers(client, path):
    res = client.get(path)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["source"] in bp.SCRAPER_SOURCES


def test_ohlcv_shape_matches_client_contract(client):
    body = client.get("/api/ohlcv/KRAKEN/XBTUSD?timeframe=15m&candles=25").json()
    assert body["total"] == 25
    assert body["timeframe"] == "15m"
    assert body["interval_minutes"] == 15
    assert body["ohlc"] == body["data"]            # Vendor-Alias
    first = body["data"][0]
    assert set(first) == {"index", "timestamp", "open", "high", "low", "close", "volume"}


def test_ohlcv_is_cached_on_second_call(client):
    client.app  # noqa: B018 - readability
    a = client.get("/api/ohlcv/KRAKEN/ETHUSD?timeframe=1h&candles=10").json()
    b = client.get("/api/ohlcv/KRAKEN/ETHUSD?timeframe=1h&candles=10").json()
    assert a["cached"] is False and b["cached"] is True
    assert a["data"] == b["data"]


def test_offline_mode_marks_data_degraded(client):
    body = client.get("/api/ohlcv/KRAKEN/SOLUSD?timeframe=5m&candles=5").json()
    assert body["source"] == "synthetic"
    assert body["degraded"] is True


def test_candle_count_is_capped(client):
    body = client.get("/api/ohlcv/KRAKEN/XRPUSD?candles=99999").json()
    assert body["total"] <= 5000


def test_indicators_expose_vendor_alias(client):
    body = client.get("/api/indicators/KRAKEN/XBTUSD?timeframe=4h").json()
    assert body["indicator"] == body["data"]
    assert body["timeframe"] == "4h"


def test_download_csv_and_json(client):
    csv_res = client.get("/api/download/ohlcv/KRAKEN/XBTUSD?timeframe=1h&candles=5&fmt=csv")
    assert csv_res.status_code == 200
    assert csv_res.headers["content-type"].startswith("text/csv")
    assert "timestamp" in csv_res.text.splitlines()[0]

    json_res = client.get("/api/download/movers?fmt=json&limit=3")
    assert json_res.status_code == 200
    assert isinstance(json_res.json(), list)


def test_cache_clear_endpoint(client):
    client.get("/api/ohlcv/KRAKEN/XBTUSD?candles=5")
    assert client.post("/api/cache/clear").json()["cleared"] >= 1


def test_config_endpoint_exposes_settings(client):
    body = client.get("/api/config").json()
    assert body["settings"]["port"] == bp.PORT_SCRAPER
    assert body["vendor"]["offline_mode"] is True


# =============================================================================
# Vendor-Tree Vollständigkeit (§6 "Vendor")
# =============================================================================

VENDOR_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vendor", "tradingview-scraper", "tradingview_scraper", "data",
)


@pytest.mark.parametrize("filename", [
    "exchanges.txt", "indicators.txt", "timeframes.json",
    "areas.json", "languages.json", "news_providers.txt",
])
def test_vendor_data_pack_is_present(filename):
    """Ohne diese Dateien lehnt `Indicators` jede Exchange ab."""
    path = os.path.join(VENDOR_DATA, filename)
    assert os.path.exists(path), f"missing vendor data file: {filename}"
    assert os.path.getsize(path) > 0


def test_kraken_is_a_supported_exchange():
    with open(os.path.join(VENDOR_DATA, "exchanges.txt"), encoding="utf-8") as fh:
        exchanges = {line.strip() for line in fh}
    assert "KRAKEN" in exchanges          # Sigma handelt ausschliesslich Kraken


def test_indicator_fields_have_no_timeframe_suffix():
    """Der Vendor haengt `|<tf>` selbst an — vorgefertigte Suffixe wuerden doppeln."""
    with open(os.path.join(VENDOR_DATA, "indicators.txt"), encoding="utf-8") as fh:
        fields = [line.strip() for line in fh if line.strip()]
    assert fields, "indicator list must not be empty"
    assert all("|" not in field for field in fields)
    assert {"close", "RSI", "ATR", "EMA50", "EMA200", "Recommend.All"} <= set(fields)


def test_scraper_timeframes_are_supported_by_vendor():
    import json as _json

    with open(os.path.join(VENDOR_DATA, "timeframes.json"), encoding="utf-8") as fh:
        supported = set(_json.load(fh)["indicators"])
    for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d"):
        assert timeframe in supported
        assert timeframe in bp.SCRAPER_TIMEFRAMES


def test_blueprint_knows_both_scraper_entrypoints():
    assert bp.SCRAPER_VENDOR_ENTRY == "uvicorn api.main:app"
    assert bp.SCRAPER_SIGMA_ENTRY == "uvicorn app.scraper.main:app"
    assert any(proc.name == "sigma-tv-scraper" and proc.port == bp.PORT_SCRAPER
               for proc in bp.PROCESSES)
