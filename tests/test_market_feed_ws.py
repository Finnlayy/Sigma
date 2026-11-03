"""Market-feed WebSocket route — LWC guide visualization plane."""
from __future__ import annotations

from app.core import blueprint as bp


def test_market_feed_route_constant():
    assert bp.MARKET_FEED_WS_ROUTE == "/ws/market-feed/{symbol}"


def test_market_feed_handler_registered():
    from app.server import routes_sigma

    src = open(routes_sigma.__file__, encoding="utf-8").read()
    assert "async def market_feed_ws" in src
    assert "market:candles:" in src
    assert "alpha:executions:live" in src
    assert "fetch_ohlc_with_meta" in src


def test_frontend_client_exposes_market_feed_url():
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    api = open(os.path.join(root, "src", "lib", "sigmaApi.ts"), encoding="utf-8").read()
    assert "marketFeedUrl" in api
    assert "/ws/market-feed/" in api


def test_encoded_pair_reaches_market_websocket_and_uses_configured_redis(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.server import routes_sigma
    from app.core.config import SigmaConfig

    class Scraper:
        def fetch_ohlc_with_meta(self, symbol, interval, count):
            assert symbol == 'BTC/USD'
            return [{'ts': 1700000000, 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 10}], {'source': 'tv_scraper'}

    async def no_redis(config):
        assert isinstance(config, SigmaConfig)
        return None

    monkeypatch.setattr(routes_sigma, 'get_scraper_client', lambda: Scraper())
    monkeypatch.setattr('app.core.redis_client.get_redis', no_redis)
    app = FastAPI()
    app.include_router(routes_sigma.router)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/market-feed/BTC%2FUSD?interval=15') as ws:
            payload = ws.receive_json()
            assert payload['type'] == 'init'
            assert payload['symbol'] == 'BTC/USD'
            assert payload['feed']['source'] == 'tv_scraper'
