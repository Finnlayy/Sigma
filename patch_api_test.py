import re
with open("tests/test_kraken_single_book.py", "r") as f:
    content = f.read()

new_test = """
def test_api_positions_pro(monkeypatch):
    from fastapi.testclient import TestClient
    from app.server.main import app, state
    from app.execution.kraken_paper_sot import PaperCapitalSnapshot
    
    # Mock live trading False, which implies paper mode
    state.config = type("C", (), {"live_trading": False})()
    
    def fake_fetch_paper_capital(bridge, futures=False, force=False):
        return PaperCapitalSnapshot(
            ok=True,
            available=True,
            balances={"USD": 50000.0},
            current_value=50000.0,
            starting_balance=50000.0,
            starting_currency="USD",
            unrealized_pnl=0.0,
            total_trades=0,
            fee_rate=0.0,
            slippage_rate=0.0,
            workspace="",
            source="mock",
            book="futures",
            argv=[]
        )
    
    def fake_fetch_futures_positions(bridge, paper=False):
        return {
            "ok": True,
            "positions": [
                {"symbol": "PF_XBTUSD", "size": 1.0, "side": "long"}
            ],
            "total_collateral_usd": 50000.0,
            "free_margin_usd": 50000.0,
            "unrealized_pnl_usd": 0.0
        }
    
    monkeypatch.setattr("app.server.main.fetch_paper_capital", fake_fetch_paper_capital)
    monkeypatch.setattr("app.server.main.fetch_futures_positions", fake_fetch_futures_positions)
    
    with TestClient(app) as client:
        res = client.get("/api/kraken/positions/pro")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["live"] is False
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["symbol"] == "PF_XBTUSD"
        assert "fundingRate" not in pos or pos["fundingRate"] != 0.01
"""

content = re.sub(
    r'def test_api_positions_pro\(monkeypatch\):[\s\S]*?assert "fundingRate" not in pos or pos\["fundingRate"\] != 0\.01',
    new_test.strip(),
    content
)

with open("tests/test_kraken_single_book.py", "w") as f:
    f.write(content)
