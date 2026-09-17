import re
with open("app/server/main.py", "r") as f:
    content = f.read()

paper_balances_new = """def _paper_balances(
    state: "AppState",
    closed_trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, float]:
    from app.execution.kraken_paper_sot import fetch_paper_capital
    cap = fetch_paper_capital(state.kraken_cli, futures=False)
    if cap.ok and getattr(cap, "available", True):
        return cap.balances

    balances: Dict[str, float] = {}
    for seed in getattr(getattr(state, "config", None), "paper_seeds", []):
        asset, amt = seed.split(":")
        balances[asset] = float(amt)
    if closed_trades is not None:
        for t in closed_trades:
            if (t.get("execution_mode") or "paper") == "paper":
                balances["USD"] = balances.get("USD", 0.0) + float(t.get("net_pnl_usd") or 0.0)
    else:
        if hasattr(state, "store") and state.store:
            balances["USD"] = balances.get("USD", 0.0) + state.store.sum_closed_pnl("paper")
    return balances"""

content = re.sub(
    r'def _paper_balances\([\s\S]*?\) -> Dict\[str, float\]:[\s\S]*?return balances',
    paper_balances_new,
    content
)

with open("app/server/main.py", "w") as f:
    f.write(content)
