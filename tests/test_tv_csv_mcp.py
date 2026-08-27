"""Unit tests: TradingView CSV seam + TvMcpBacktest adapter (no local BacktestEngine)."""
from __future__ import annotations

import pytest

from app.backtest.tv_csv import (
    genes_from_parameter_csv,
    params_to_csv,
    parse_parameter_csv,
    parse_trades_csv,
    result_csv_to_backtest_result,
    synthesize_result_csv,
)
from app.backtest.TvMcpBacktest import TvMcpBacktest, set_adapter, run_backtest
from app.mcp.TradingViewMCPClient import FakeTvMcpTransport, TradingViewMCPClient, TvMcpError
from app.backtest.BacktestEngine import run_backtest as legacy_run_backtest


SAMPLE_PARAMS_CSV = """Parameter,Value
trendFastEma,12
trendSlowEma,60
atrStopMultiplier,2.0
atrPeriod,14
"""

SAMPLE_TRADES_CSV = """Trade #,Type,Date/Time,Signal,Price USD,Position size (qty),Position size (value),Net P&L USD,Net P&L %,Cumulative P&L USD,Cumulative P&L %,Fee
1,Entry long,2024-06-01 10:00,long,100.00,1,100.00,0,0,0,0,0.1
1,Exit long,2024-06-01 14:00,long,110.00,1,110.00,9.8,9.8,9.8,0.098,0.1
2,Entry short,2024-06-02 10:00,short,110.00,1,110.00,0,0,9.8,0.098,0.1
2,Exit short,2024-06-02 14:00,short,100.00,1,100.00,9.7,8.818,19.5,0.195,0.1
"""


def test_parse_parameter_csv_roundtrip():
    params = parse_parameter_csv(SAMPLE_PARAMS_CSV)
    assert params["trendFastEma"] == 12
    assert params["atrStopMultiplier"] == 2.0
    again = parse_parameter_csv(params_to_csv(params))
    assert again["trendSlowEma"] == 60


def test_genes_from_parameter_csv_filters():
    from app.optimizer.GeneticOptimizer import GENE_RANGES

    genes = genes_from_parameter_csv(SAMPLE_PARAMS_CSV, GENE_RANGES)
    assert "trendFastEma" in genes
    assert "smaFast" not in genes  # not a gene key


def test_parse_trades_and_result_mapping():
    events = parse_trades_csv(SAMPLE_TRADES_CSV)
    assert len(events) == 4
    result = result_csv_to_backtest_result(
        SAMPLE_TRADES_CSV,
        config={"initialBalance": 10000, "assetPair": "BTC/USD", "interval": 15},
    )
    assert result["source"] == "tradingview-csv"
    assert result["summary"]["totalTrades"] == 2
    assert result["summary"]["totalReturnUSD"] == pytest.approx(19.5, abs=0.2)
    assert len(result["trades"]) == 2
    assert len(result["equityCurve"]) == 2


def test_synthesize_and_map():
    csv_text = synthesize_result_csv({"atrStopMultiplier": 2.0, "trendFastEma": 10}, seed="is")
    result = result_csv_to_backtest_result(csv_text, config={"initialBalance": 10000})
    assert result["summary"]["totalTrades"] >= 1
    assert "summary" in result


def test_fake_mcp_adapter_run():
    client = TradingViewMCPClient(FakeTvMcpTransport())
    adapter = TvMcpBacktest(client, concurrency=2)
    set_adapter(adapter)
    result = run_backtest([], {
        "assetPair": "BTC/USD",
        "interval": 15,
        "initialBalance": 10000,
        "customParameters": {"trendFastEma": 12, "trendSlowEma": 48, "atrStopMultiplier": 2.0},
        "split": "is",
    })
    assert result["summary"]["totalTrades"] >= 1
    assert result.get("mcpMeta", {}).get("source") == "tradingview-mcp-csv"
    # cache hit
    result2 = adapter.run({
        "assetPair": "BTC/USD",
        "interval": 15,
        "initialBalance": 10000,
        "customParameters": {"trendFastEma": 12, "trendSlowEma": 48, "atrStopMultiplier": 2.0},
        "split": "is",
    })
    assert adapter.stats["cache_hits"] >= 1
    assert result2["summary"]["totalTrades"] == result["summary"]["totalTrades"]


def test_import_result_csv_direct():
    client = TradingViewMCPClient(FakeTvMcpTransport())
    adapter = TvMcpBacktest(client)
    result = adapter.run({
        "resultCsv": SAMPLE_TRADES_CSV,
        "initialBalance": 10000,
        "assetPair": "ETH/USD",
    })
    assert result["assetPair"] == "ETH/USD"
    assert result["summary"]["totalTrades"] == 2


def test_legacy_engine_disabled():
    with pytest.raises(RuntimeError, match="disabled"):
        legacy_run_backtest([], {"initialBalance": 10000})


def test_http_transport_requires_url():
    from app.mcp.TradingViewMCPClient import HttpJsonRpcTransport

    with pytest.raises(TvMcpError, match="SIGMA_TV_MCP_URL"):
        HttpJsonRpcTransport("")
