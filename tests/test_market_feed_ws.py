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
