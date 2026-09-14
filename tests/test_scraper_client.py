"""
=========================================================
Datei:      tests/test_scraper_client.py
Zweck:      §6 / §Tests — `TradingViewScraperClient` gegen ein
            gemocktes Sidecar :8001 (kein Netz), inkl. Envelope-
            Auswertung, Symbol/Interval-Mapping und Fallback.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.core import blueprint as bp
from app.tv.scraper_client import (ScraperUnavailable, TradingViewScraperClient,
                                   get_scraper_client, normalize_ohlc, set_scraper_client)


class FakeSidecar:
    """Nimmt Requests entgegen und antwortet im Sigma-Envelope."""

    def __init__(self, source: str = "tv_scraper", fail: bool = False):
        self.source = source
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"url": url, "params": params})
        if self.fail:
            raise ScraperUnavailable(f"{url}: connection refused")
        if "/health" in url:
            return {"status": "ok", "degraded": self.source != "tv_scraper",
                    "vendor": {"importable": True}, "cache": {"entries": 3},
                    "rate_limit": {"tokens": 12}}
        if "/api/ohlcv/" in url:
            rows = [
                {"index": 0, "timestamp": 1_700_000_000, "open": 100.0, "high": 101.0,
                 "low": 99.0, "close": 100.5, "volume": 12.0},
                {"index": 1, "timestamp": 1_700_000_900, "open": 100.5, "high": 102.0,
                 "low": 100.0, "close": 101.5, "volume": 15.0},
            ]
            return {"status": "success", "source": self.source, "cached": False,
                    "data": rows, "ohlc": rows, "total": 2}
        if "/api/indicators/" in url:
            return {"status": "success", "source": self.source,
                    "data": {"RSI": 55.0}, "indicator": {"RSI": 55.0}}
        if "/api/overview/" in url:
            return {"status": "success", "source": self.source,
                    "data": {"symbol": "KRAKEN:XBTUSD"}}
        if url.endswith(bp.SCRAPER_ENDPOINTS["movers"]):
            return {"status": "success", "source": self.source,
                    "data": [{"name": "XBTUSD", "change": 4.2}]}
        if url.endswith(bp.SCRAPER_ENDPOINTS["screener"]):
            return {"status": "success", "source": self.source,
                    "data": [{"name": "ETHUSD", "volume": 1e6}]}
        raise ScraperUnavailable(f"unmapped route {url}")


def make_client(sidecar: FakeSidecar) -> TradingViewScraperClient:
    return TradingViewScraperClient(base_url="http://127.0.0.1:8001", transport=sidecar)


# =============================================================================
# Mapping & Routen
# =============================================================================

def test_ohlc_request_uses_blueprint_route_and_mapping():
    sidecar = FakeSidecar()
    client = make_client(sidecar)
    client.fetch_ohlc("BTC/USD", 15, 300)
    call = sidecar.calls[-1]
    assert call["url"] == "http://127.0.0.1:8001/api/ohlcv/KRAKEN/XBTUSD"
    assert call["params"] == {"timeframe": "15m", "candles": 300}


@pytest.mark.parametrize("minutes,timeframe", [(1, "1m"), (15, "15m"), (60, "1h"),
                                               (240, "4h"), (1440, "1d")])
def test_interval_mapping_matches_scraper_timeframes(minutes, timeframe):
    sidecar = FakeSidecar()
    make_client(sidecar).fetch_ohlc("ETH/USD", minutes, 10)
    assert sidecar.calls[-1]["params"]["timeframe"] == timeframe
    assert timeframe in bp.SCRAPER_TIMEFRAMES


def test_candles_are_normalized_to_alpha_schema():
    candles = make_client(FakeSidecar()).fetch_ohlc("BTC/USD", 15, 2)
    assert len(candles) == 2
    assert set(candles[0]) == {"ts", "o", "h", "l", "c", "v"}
    assert candles[0]["ts"] < candles[1]["ts"]
    assert candles[1]["c"] == 101.5


def test_normalize_ohlc_accepts_vendor_and_sigma_payloads():
    vendor = {"ohlc": [{"timestamp": 1_700_000_000, "open": 1, "high": 2,
                        "low": 0.5, "close": 1.5, "volume": 3}]}
    sigma = {"data": [{"timestamp": 1_700_000_000_000, "open": 1, "high": 2,
                       "low": 0.5, "close": 1.5, "volume": 3}]}
    assert normalize_ohlc(vendor)[0]["ts"] == 1_700_000_000
    assert normalize_ohlc(sigma)[0]["ts"] == 1_700_000_000     # ms -> s
    assert normalize_ohlc(None) == []


# =============================================================================
# Envelope / Herkunft
# =============================================================================

def test_meta_flags_vendor_data_as_trustworthy():
    client = make_client(FakeSidecar(source="tv_scraper"))
    _, meta = client.fetch_ohlc_with_meta("BTC/USD", 15, 2)
    assert meta["source"] == "tv_scraper"
    assert meta["degraded"] is False


def test_meta_flags_synthetic_data():
    sidecar = FakeSidecar(source="synthetic")
    client = make_client(sidecar)
    _, meta = client.fetch_ohlc_with_meta("BTC/USD", 15, 2)
    assert meta["source"] == "synthetic"
    assert client.last_meta["source"] == "synthetic"


def test_indicators_and_overview_use_blueprint_routes():
    sidecar = FakeSidecar()
    client = make_client(sidecar)
    assert client.fetch_indicators("BTC/USD", 1440) == {"RSI": 55.0}
    assert sidecar.calls[-1]["url"].endswith("/api/indicators/KRAKEN/XBTUSD")
    assert client.fetch_overview("BTC/USD")["symbol"] == "KRAKEN:XBTUSD"
    assert sidecar.calls[-1]["url"].endswith("/api/overview/KRAKEN/XBTUSD")


def test_movers_and_screener_return_rows():
    client = make_client(FakeSidecar())
    assert client.movers("crypto", "gainers", 5)[0]["name"] == "XBTUSD"
    assert client.screener(market="crypto", limit=5)[0]["name"] == "ETHUSD"


def test_download_url_points_at_sidecar():
    url = make_client(FakeSidecar()).download_url(
        "ohlcv", exchange="KRAKEN", ticker="XBTUSD", fmt="csv", timeframe="15m", candles=100)
    assert url.startswith("http://127.0.0.1:8001/api/download/ohlcv/KRAKEN/XBTUSD?")
    assert "fmt=csv" in url and "timeframe=15m" in url


# =============================================================================
# Ausfallverhalten
# =============================================================================

def test_unavailable_sidecar_raises_not_silently_fakes():
    client = make_client(FakeSidecar(fail=True))
    with pytest.raises(ScraperUnavailable):
        client.fetch_ohlc("BTC/USD")


def test_health_reports_down_without_raising():
    client = make_client(FakeSidecar(fail=True))
    health = client.health(max_age_s=0.0)
    assert health["ok"] is False
    assert health["degraded"] is True
    assert client.ping() in (False, True)


def test_health_is_cached_briefly():
    sidecar = FakeSidecar()
    client = make_client(sidecar)
    client.health()
    calls = len(sidecar.calls)
    client.health()
    assert len(sidecar.calls) == calls          # aus dem Cache


def test_health_surfaces_sidecar_details():
    health = make_client(FakeSidecar()).health(max_age_s=0.0)
    assert health["ok"] is True
    assert health["vendor"]["importable"] is True
    assert health["cache"]["entries"] == 3


def test_singleton_seam_is_replaceable():
    sentinel = make_client(FakeSidecar())
    set_scraper_client(sentinel)
    assert get_scraper_client() is sentinel
    set_scraper_client(None)
