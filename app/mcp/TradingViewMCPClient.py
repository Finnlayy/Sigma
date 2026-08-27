"""
TradingView MCP JSON-RPC client for Projekt:Sigma.

Canonical payload interchange is CSV (parameter + result), not opaque blobs.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Protocol

import httpx

logger = logging.getLogger("app.mcp.tradingview")


class TvMcpError(RuntimeError):
    """Raised when the TradingView MCP endpoint is unreachable or returns an error."""


class TvMcpTransport(Protocol):
    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        ...


class HttpJsonRpcTransport:
    """JSON-RPC 2.0 over HTTP POST to SIGMA_TV_MCP_URL."""

    def __init__(self, url: str, timeout_s: float = 120.0, headers: Optional[Dict[str, str]] = None):
        if not url:
            raise TvMcpError(
                "SIGMA_TV_MCP_URL is not set — TradingView MCP required; "
                "no local BacktestEngine fallback."
            )
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(self.url, json=payload, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise TvMcpError(f"TradingView MCP call failed ({method}): {exc}") from exc
        if "error" in data and data["error"]:
            raise TvMcpError(f"TradingView MCP error: {data['error']}")
        return data.get("result") or {}


class FakeTvMcpTransport:
    """
    Deterministic CSV-producing transport for unit tests.
    Does NOT invoke BacktestEngine — synthesizes TV-shaped CSVs only.
    """

    def __init__(self):
        from app.backtest.tv_csv import synthesize_result_csv, params_to_csv

        self._synth = synthesize_result_csv
        self._params_to_csv = params_to_csv
        self.calls: List[Dict[str, Any]] = []

    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"method": method, "params": params})
        if method in ("tv.ohlc", "tradingview/ohlc"):
            n = int(params.get("count") or 80)
            lines = ["ts,open,high,low,close,volume"]
            px = 100.0
            for i in range(n):
                px += 0.1
                lines.append(f"2024-01-01 {i % 24:02d}:00:00,{px},{px+1},{px-1},{px},10")
            return {"ohlcCsv": "\n".join(lines), "candles": []}
        if method in ("tv.backtest", "tradingview/backtest"):
            pcsv = params.get("parametersCsv") or self._params_to_csv(params.get("parameters") or {})
            from app.backtest.tv_csv import parse_parameter_csv

            try:
                parsed = parse_parameter_csv(pcsv) if pcsv else (params.get("parameters") or {})
            except Exception:
                parsed = params.get("parameters") or {}
            seed = str(params.get("window") or params.get("split") or "full")
            return {
                "tradesCsv": self._synth(parsed, initial_balance=float(params.get("initialBalance") or 10000), seed=seed),
                "performanceCsv": "Metric,Value\nTotal Trades,10\n",
            }
        raise TvMcpError(f"FakeTvMcpTransport: unknown method {method}")


class TradingViewMCPClient:
    """High-level TV MCP operations returning CSV strings / candle lists."""

    def __init__(self, transport: TvMcpTransport):
        self.transport = transport

    @classmethod
    def from_env(cls, url: str, timeout_s: float = 120.0) -> "TradingViewMCPClient":
        if url.strip().lower() in ("fake", "mock", "test://fake"):
            return cls(FakeTvMcpTransport())
        return cls(HttpJsonRpcTransport(url, timeout_s=timeout_s))

    def fetch_ohlc_csv(self, symbol: str, interval: int, count: int) -> str:
        result = self.transport.call("tv.ohlc", {
            "symbol": symbol,
            "interval": interval,
            "count": count,
        })
        csv_text = result.get("ohlcCsv") or result.get("csv") or ""
        if not csv_text and result.get("candles"):
            # Convert candle list to CSV
            lines = ["ts,open,high,low,close,volume"]
            for c in result["candles"]:
                lines.append(
                    f"{c.get('ts') or c.get('timestamp')},{c['open']},{c['high']},{c['low']},{c['close']},{c.get('volume', 0)}"
                )
            csv_text = "\n".join(lines)
        if not csv_text:
            raise TvMcpError("TradingView MCP returned empty OHLC CSV")
        return csv_text

    def run_backtest_csv(
        self,
        *,
        parameters_csv: str,
        strategy_ref: str = "",
        symbol: str = "BTC/USD",
        interval: int = 15,
        initial_balance: float = 10_000.0,
        window: Optional[Dict[str, Any]] = None,
        pine_code: Optional[str] = None,
    ) -> Dict[str, str]:
        """Submit parameter CSV; receive tradesCsv (+ optional performanceCsv)."""
        result = self.transport.call("tv.backtest", {
            "strategyRef": strategy_ref,
            "symbol": symbol,
            "interval": interval,
            "initialBalance": initial_balance,
            "parametersCsv": parameters_csv,
            "pineCode": pine_code,
            "window": window or {},
            "split": (window or {}).get("split"),
        })
        trades = result.get("tradesCsv") or result.get("resultCsv") or result.get("csv") or ""
        if not trades:
            # Allow file path hand-off from MCP
            path = result.get("tradesCsvPath") or result.get("resultCsvPath")
            if path:
                with open(path, "r", encoding="utf-8-sig") as f:
                    trades = f.read()
        if not trades:
            raise TvMcpError("TradingView MCP returned empty trades CSV")
        perf = result.get("performanceCsv") or ""
        if not perf and result.get("performanceCsvPath"):
            with open(result["performanceCsvPath"], "r", encoding="utf-8-sig") as f:
                perf = f.read()
        return {"tradesCsv": trades, "performanceCsv": perf}
