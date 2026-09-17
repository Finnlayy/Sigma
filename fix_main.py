with open("app/server/main.py", "r") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if line.startswith("async def kraken_positions_pro()"):
        start_idx = i
        break

if start_idx != -1:
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith("@app.get(\"/api/kraken/symbols\")"):
            end_idx = i
            break

if start_idx != -1 and end_idx != -1:
    new_func = """async def kraken_positions_pro():
    from app.execution.kraken_futures_positions_sot import fetch_futures_positions
    from app.execution.kraken_paper_sot import fetch_paper_capital

    if state.config.live_trading and getattr(state, "has_credentials", False):
        res = fetch_futures_positions(state.kraken_cli, paper=False)
        if not res.get("ok"):
            return {
                "ok": False,
                "source": "unavailable",
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
        is_offline = getattr(cap, "cli_offline", False)
        if not is_offline and hasattr(cap, "available"):
            is_offline = not cap.available

        if is_offline:
            return {
                "ok": False,
                "source": "unavailable",
                "live": False,
                "reason": "cli_offline",
                "positions": [],
                "totalCollateralUSD": None,
                "freeMarginUSD": None,
                "totalUnrealizedPnL": None,
            }
        res = fetch_futures_positions(state.kraken_cli, paper=True)
        pos = res.get("positions", []) if isinstance(res, dict) else getattr(res, "positions", [])
        return {
            "ok": True,
            "source": "futures/paper/positions",
            "live": False,
            "reason": None,
            "positions": pos,
            "totalCollateralUSD": cap.current_value,
            "freeMarginUSD": cap.balances.get("USD", cap.current_value),
            "totalUnrealizedPnL": cap.unrealized_pnl,
        }

"""
    lines[start_idx:end_idx] = [new_func]
    with open("app/server/main.py", "w") as f:
        f.writelines(lines)
