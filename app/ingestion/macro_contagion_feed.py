"""Live macro inputs for the SIR contagion engine."""
from __future__ import annotations

import math
import statistics
from typing import Any, Callable, Dict, Optional, Sequence

from app.ingestion.kraken_depth_adapter import KrakenDepthAdapter
from app.quant.epidemic_contagion_engine import ContagionInputs
from app.tv.scraper_client import TradingViewScraperClient, get_scraper_client

SeriesFetcher = Callable[[str, str, int], Sequence[float]]


class MacroContagionFeed:
    """Builds normalized SIR inputs from TV macro series and Kraken depth."""

    def __init__(
        self,
        *,
        scraper: Optional[TradingViewScraperClient] = None,
        depth: Optional[KrakenDepthAdapter] = None,
        series_fetcher: Optional[SeriesFetcher] = None,
        lookback: int = 90,
    ) -> None:
        self.scraper = scraper or get_scraper_client()
        self.depth = depth
        self.series_fetcher = series_fetcher or self._fetch_closes
        self.lookback = max(40, int(lookback))
        self.last_inputs: Optional[ContagionInputs] = None
        self.last_meta: Dict[str, Any] = {}

    def _fetch_closes(self, exchange: str, ticker: str, count: int) -> Sequence[float]:
        candles, meta = self.scraper.fetch_ticker_ohlc(
            exchange, ticker, interval_min=1440, count=count
        )
        self.last_meta[f"{exchange}:{ticker}"] = meta
        return [float(candle["c"]) for candle in candles if float(candle.get("c") or 0) > 0]

    def snapshot(self) -> ContagionInputs:
        oil = list(self.series_fetcher("TVC", "USOIL", self.lookback))
        gold = list(self.series_fetcher("TVC", "GOLD", self.lookback))
        dxy = list(self.series_fetcher("TVC", "DXY", self.lookback))
        btc = list(self.series_fetcher("KRAKEN", "XBTUSD", self.lookback))
        for name, values in (("oil", oil), ("gold", gold), ("dxy", dxy), ("btc", btc)):
            if len(values) < 30:
                raise ValueError(f"macro contagion feed: {name} has only {len(values)} closes")

        absorption = 1.0
        if self.depth is not None:
            absorption = self.depth.absorption(self.depth.fetch("XBTUSD"))

        inputs = ContagionInputs(
            oil_vol_zscore=_volatility_zscore(oil),
            gold_dxy_ratio_change=_ratio_change(gold, dxy),
            cross_asset_correlation=_absolute_correlation(btc, dxy),
            orderbook_absorption=absorption,
        )
        self.last_inputs = inputs
        return inputs

    def panel_state(self) -> Dict[str, Any]:
        return {
            "inputs": self.last_inputs.as_dict() if self.last_inputs else None,
            "sources": {
                "oil": "TVC:USOIL",
                "gold": "TVC:GOLD",
                "dxy": "TVC:DXY",
                "crypto": "KRAKEN:XBTUSD",
                "orderbook": "Kraken REST Depth",
            },
            "meta": dict(self.last_meta),
        }


def _returns(values: Sequence[float]) -> list[float]:
    return [
        math.log(float(values[index]) / float(values[index - 1]))
        for index in range(1, len(values))
        if float(values[index]) > 0 and float(values[index - 1]) > 0
    ]


def _volatility_zscore(values: Sequence[float], window: int = 10) -> float:
    returns = _returns(values)
    if len(returns) < window * 3:
        return 0.0
    rolling = [
        statistics.pstdev(returns[index - window:index])
        for index in range(window, len(returns) + 1)
    ]
    baseline = rolling[:-1]
    sigma = statistics.pstdev(baseline)
    if sigma <= 1e-12:
        return 0.0
    return max(-6.0, min(6.0, (rolling[-1] - statistics.mean(baseline)) / sigma))


def _ratio_change(gold: Sequence[float], dxy: Sequence[float]) -> float:
    length = min(len(gold), len(dxy))
    if length < 2:
        return 0.0
    previous = float(gold[-2]) / max(float(dxy[-2]), 1e-12)
    current = float(gold[-1]) / max(float(dxy[-1]), 1e-12)
    return current / max(previous, 1e-12) - 1.0


def _absolute_correlation(left: Sequence[float], right: Sequence[float]) -> float:
    left_returns = _returns(left)
    right_returns = _returns(right)
    length = min(len(left_returns), len(right_returns), 60)
    if length < 10:
        return 0.0
    x, y = left_returns[-length:], right_returns[-length:]
    mean_x, mean_y = statistics.mean(x), statistics.mean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denominator = math.sqrt(
        sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y)
    )
    if denominator <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, abs(numerator / denominator)))
