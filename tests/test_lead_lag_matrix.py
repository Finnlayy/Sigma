"""Lead-lag matrix must emit the QUANT UI contract (assets / correlations)."""
from __future__ import annotations

from app.quant.RegimeEngine import lead_lag_matrix


def test_lead_lag_matrix_includes_ui_contract_on_short_series():
    symbols = ["BTC/USD", "ETH/USD"]
    out = lead_lag_matrix(symbols, {"BTC/USD": [100.0, 101.0], "ETH/USD": [10.0, 10.1]}, max_lag=2)
    assert out["assets"] == symbols
    assert out["lead_asset"] == "BTC/USD"
    assert out["lead_lag_bars"] == 0
    assert len(out["matrix"]) == 2
    btc = out["matrix"][0]
    assert btc["asset"] == "BTC/USD"
    assert btc["symbol_a"] == "BTC/USD"
    assert btc["correlations"]["BTC/USD"] == 1.0
    assert "ETH/USD" in btc["correlations"]
    assert "ETH/USD" in btc["lags"]
    assert "spillover" in btc


def test_lead_lag_matrix_correlations_cover_every_pair():
    n = 200
    btc = [100.0 + i * 0.2 for i in range(n)]
    eth = [10.0] + [10.0 + (btc[i] - btc[i - 1]) for i in range(1, n)]
    out = lead_lag_matrix(["BTC/USD", "ETH/USD"], {"BTC/USD": btc, "ETH/USD": eth}, max_lag=3)
    assert set(out["assets"]) == {"BTC/USD", "ETH/USD"}
    by_asset = {row["asset"]: row for row in out["matrix"]}
    assert by_asset["BTC/USD"]["correlations"]["BTC/USD"] == 1.0
    assert isinstance(by_asset["BTC/USD"]["correlations"]["ETH/USD"], float)
    assert by_asset["ETH/USD"]["correlations"]["ETH/USD"] == 1.0
