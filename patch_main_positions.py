import re
with open("app/server/main.py", "r") as f:
    content = f.read()

kraken_positions_pro_new = """async def kraken_positions_pro():
    from app.execution.kraken_futures_positions_sot import fetch_futures_positions
    from app.execution.kraken_paper_sot import fetch_paper_capital

    if state.config.live_trading and getattr(state, "has_credentials", False):
        res = fetch_futures_positions(state.kraken_cli, paper=False)
        if not res.get("ok"):
            return {
                "ok": False,
                "source": "futures/live/positions",
                "live": True,
                "reason": "cli_offline" if "CLI_NOT_FOUND" in res.get("error", "") else res.get("error"),
                "positions": [],
                "totalCollateralUSD": None,
                "freeMarginUSD": None,
                "totalUnrealizedPnL": None,
            }
        return {
            "ok": True,
            "source": "futures/live/positions",
            "live": True,
            "reason": None,
            "positions": res.get("positions", []),
            "totalCollateralUSD": res.get("total_collateral_usd"),
            "freeMarginUSD": res.get("free_margin_usd"),
            "totalUnrealizedPnL": res.get("unrealized_pnl_usd"),
        }
    else:
        # Paper
        cap = fetch_paper_capital(state.kraken_cli, futures=True)
        if cap.cli_offline:
            return {
                "ok": False,
                "source": "futures/paper/positions",
                "live": False,
                "reason": "cli_offline",
                "positions": [],
                "totalCollateralUSD": None,
                "freeMarginUSD": None,
                "totalUnrealizedPnL": None,
            }
        res = fetch_futures_positions(state.kraken_cli, paper=True)
        return {
            "ok": True,
            "source": "futures/paper/positions",
            "live": False,
            "reason": None,
            "positions": res.get("positions", []),
            "totalCollateralUSD": cap.total_collateral_usd if hasattr(cap, 'total_collateral_usd') else cap.margin_used_usd,
            "freeMarginUSD": cap.free_margin_usd,
            "totalUnrealizedPnL": cap.unrealized_pnl_usd,
        }"""

content = re.sub(
    r'async def kraken_positions_pro\(\):.*?    }',
    kraken_positions_pro_new,
    content,
    flags=re.DOTALL
)

with open("app/server/main.py", "w") as f:
    f.write(content)
