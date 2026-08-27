"""
TvMcpBacktest — single adapter facade for UI + GeneticOptimizer.

Evaluations go through TradingView MCP; interchange is parameter/result CSV.
Includes sync job queue, concurrency limit, and result cache.
No silent fallback to local BacktestEngine.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Dict, List, Optional

from app.backtest.tv_csv import (
    cache_key,
    params_to_csv,
    parse_parameter_csv,
    result_csv_to_backtest_result,
)
from app.mcp.TradingViewMCPClient import TradingViewMCPClient, TvMcpError

logger = logging.getLogger("app.backtest.tv_mcp")

_ADAPTER: Optional["TvMcpBacktest"] = None
_ADAPTER_LOCK = threading.Lock()


class TvMcpBacktest:
    def __init__(
        self,
        client: TradingViewMCPClient,
        *,
        concurrency: int = 4,
        cache_enabled: bool = True,
    ):
        self.client = client
        self.cache_enabled = cache_enabled
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="tv-mcp")
        self.stats = {"jobs": 0, "cache_hits": 0, "errors": 0}

    @classmethod
    def from_config(cls, config) -> "TvMcpBacktest":
        url = getattr(config, "tv_mcp_url", "") or ""
        timeout = float(getattr(config, "tv_mcp_timeout_s", 120) or 120)
        concurrency = int(getattr(config, "tv_mcp_concurrency", 4) or 4)
        if not url:
            raise TvMcpError(
                "SIGMA_TV_MCP_URL unset — cannot run backtests without TradingView MCP "
                "(local BacktestEngine disabled)."
            )
        client = TradingViewMCPClient.from_env(url, timeout_s=timeout)
        return cls(client, concurrency=concurrency)

    def status(self) -> Dict[str, Any]:
        return {
            "engine": "tradingview-mcp-csv",
            "jobs": self.stats["jobs"],
            "cacheHits": self.stats["cache_hits"],
            "errors": self.stats["errors"],
            "cacheSize": len(self._cache),
        }

    def params_csv_for(self, params: Dict[str, Any]) -> str:
        return params_to_csv(params)

    def run(self, config: Dict[str, Any], candles: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Run one backtest evaluation via TV MCP CSV seam.

        `candles` is accepted for GA IS/OOS window metadata only — evaluation
        itself is performed by TradingView and returned as result CSV.
        Local engine is never used.
        """
        params = dict(config.get("customParameters") or config.get("parameters") or {})
        # Allow direct CSV import (manual / autonomous hand-off)
        if config.get("resultCsv") or config.get("resultCsvPath"):
            src = config.get("resultCsv") or config.get("resultCsvPath")
            perf = config.get("performanceCsv") or config.get("performanceCsvPath")
            return result_csv_to_backtest_result(src, config=config, performance_csv=perf)

        if config.get("parametersCsv") or config.get("parametersCsvPath"):
            params = parse_parameter_csv(config.get("parametersCsv") or config.get("parametersCsvPath"))

        strategy_ref = str(config.get("pineStrategyId") or config.get("strategyId") or "")
        symbol = str(config.get("assetPair") or "BTC/USD")
        interval = config.get("interval") or 15
        window = {
            "split": config.get("split"),
            "from": config.get("windowFrom"),
            "to": config.get("windowTo"),
            "candleCount": len(candles) if candles else config.get("candleCount"),
        }
        if candles:
            window["from"] = window["from"] or (candles[0].get("ts") if candles else None)
            window["to"] = window["to"] or (candles[-1].get("ts") if candles else None)

        key = cache_key(strategy_ref, params, symbol, interval, window.get("from"), window.get("to"))
        if self.cache_enabled:
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit is not None:
                    self.stats["cache_hits"] += 1
                    return dict(hit)

        self.stats["jobs"] += 1
        try:
            pcsv = params_to_csv(params)
            csvs = self.client.run_backtest_csv(
                parameters_csv=pcsv,
                strategy_ref=strategy_ref,
                symbol=symbol,
                interval=int(interval),
                initial_balance=float(config.get("initialBalance") or 10_000.0),
                window=window,
                pine_code=config.get("customCode") or config.get("pineCode"),
            )
            result = result_csv_to_backtest_result(
                csvs["tradesCsv"],
                config=config,
                performance_csv=csvs.get("performanceCsv") or None,
            )
            result["mcpMeta"] = {
                "cacheKey": key,
                "parametersCsvSha": key[:16],
                "source": "tradingview-mcp-csv",
            }
        except Exception:
            self.stats["errors"] += 1
            raise

        if self.cache_enabled:
            with self._cache_lock:
                self._cache[key] = dict(result)
        return result

    def run_async(self, config: Dict[str, Any], candles: Optional[List[Dict[str, Any]]] = None) -> Future:
        return self._pool.submit(self.run, config, candles)

    def fetch_ohlc(self, pair: str, interval: int, count: int) -> Dict[str, Any]:
        csv_text = self.client.fetch_ohlc_csv(pair, interval, count)
        candles = []
        import csv as _csv
        import io
        reader = _csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            try:
                candles.append({
                    "ts": row.get("ts") or row.get("timestamp") or "",
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 0),
                })
            except (KeyError, ValueError):
                continue
        return {
            "pair": pair,
            "interval": int(interval),
            "total": len(candles),
            "candles": [{
                "time": i,
                "open": c["open"], "high": c["high"], "low": c["low"],
                "close": c["close"], "volume": c["volume"],
                "timestamp": str(c["ts"]),
            } for i, c in enumerate(candles[-count:])],
            "source": "tradingview-mcp-csv",
        }


def get_adapter(config=None) -> TvMcpBacktest:
    global _ADAPTER
    with _ADAPTER_LOCK:
        if _ADAPTER is None:
            if config is None:
                from app.core.config import load_config
                config = load_config()
            _ADAPTER = TvMcpBacktest.from_config(config)
        return _ADAPTER


def set_adapter(adapter: Optional[TvMcpBacktest]) -> None:
    """Test helper — inject Fake transport adapter."""
    global _ADAPTER
    with _ADAPTER_LOCK:
        _ADAPTER = adapter


def run_backtest(candles: Optional[List[Dict[str, Any]]], config: Dict[str, Any]) -> Dict[str, Any]:
    """Drop-in replacement signature for former BacktestEngine.run_backtest."""
    return get_adapter().run(config, candles)
