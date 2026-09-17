import re
with open("app/execution/KrakenCliBridge.py", "r") as f:
    content = f.read()

close_all_new = """    def close_all_market(self, reason: str = "deadman_no_native_stop") -> OrderResult:
        if not self.futures:
            return OrderResult(False, "sim", error_code="CLI_UNSUPPORTED", stdout="Spot close_all_market not supported")
        
        # Flatten futures book
        # 1. Cancel all
        c_res = self.cancel_all(reason)
        if not c_res.ok:
            return c_res
            
        # 2. Get open positions and market close them
        from app.execution.kraken_futures_positions_sot import fetch_futures_positions
        pos_res = fetch_futures_positions(self, paper=self.paper_mode)
        
        # fetch_futures_positions returns a dict like {"ok": bool, "positions": list, ...}
        if isinstance(pos_res, dict):
            positions = pos_res.get("positions", []) if pos_res.get("ok") else []
        else:
            positions = getattr(pos_res, "positions", []) if getattr(pos_res, "ok", False) else []
            
        if not positions:
            return OrderResult(True, "paper" if self.paper_mode else "live", stdout="Book empty after cancel-all")
            
        errors = []
        for p in positions:
            side = "sell" if p.get("side") == "long" else "buy"
            pair = p.get("symbol", p.get("pair", ""))
            vol = float(p.get("size", 0.0))
            if pair and vol > 0:
                # Issue reduce-only market order
                res = self.add_order(pair=pair, side=side, volume=vol, ordertype="market", validate=False, strategy_id="FLATTEN")
                if not res.ok:
                    errors.append(f"{pair}: {res.error_code}")
                    
        if errors:
            return OrderResult(False, "paper" if self.paper_mode else "live", error_code="FLATTEN_ERRORS", stdout=", ".join(errors))
            
        return OrderResult(True, "paper" if self.paper_mode else "live", stdout="Flattened all positions")"""

content = re.sub(
    r'    def close_all_market\(self, reason: str = "deadman_no_native_stop"\) -> OrderResult:.*?return OrderResult\(True, "paper" if self\.paper_mode else "live", stdout="Flattened all positions"\)',
    close_all_new,
    content,
    flags=re.DOTALL
)

with open("app/execution/KrakenCliBridge.py", "w") as f:
    f.write(content)
