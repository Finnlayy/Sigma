import re
with open("app/execution/KrakenCliBridge.py", "r") as f:
    content = f.read()

# Fix cancel_all
cancel_all_new = """    def cancel_all(self, reason: str = "kill_switch") -> OrderResult:
        if self.futures:
            if self.paper_mode:
                argv = [self.binary, "futures", "paper", "cancel-all"]
            else:
                argv = [self.binary, "futures", "cancel-all"]
        else:
            argv = [self.binary, "trade", "cancel-all"]

        if not self.live_enabled and not self.paper_mode:
            res = OrderResult(True, "sim", txid="SIM-CANCEL-ALL", argv=argv,
                              stdout=f"[SIM] cancel_all ({reason})")
            self._audit(res, "")
            return res
        
        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        mode = "paper" if self.paper_mode else "live"
        res = OrderResult(not failed, mode, stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
                          error_code=_extract_error(stdout, stderr) if failed else "")
        self._audit(res, "")
        return res"""

content = re.sub(
    r'    def cancel_all\(self, reason: str = "kill_switch"\) -> OrderResult:.*?return res',
    cancel_all_new,
    content,
    flags=re.DOTALL
)

# Fix close_all_market
close_all_new = """    def close_all_market(self, reason: str = "deadman_no_native_stop") -> OrderResult:
        if not self.futures:
            return OrderResult(False, "sim", error_code="CLI_UNSUPPORTED", stdout="Spot close_all_market not supported")
        
        # Flatten futures book
        # 1. Cancel all
        c_res = self.cancel_all(reason)
        if not c_res.ok:
            return c_res
            
        # 2. Get open positions and market close them
        if self.paper_mode:
            from app.execution.kraken_paper_sot import fetch_paper_capital
            st = fetch_paper_capital(self)
            positions = st.positions if st.ok else []
        else:
            from app.execution.kraken_futures_positions_sot import fetch_futures_positions
            pos_res = fetch_futures_positions(self, paper=False)
            positions = pos_res.get("positions", []) if pos_res.get("ok") else []
            
        if not positions:
            return OrderResult(True, "paper" if self.paper_mode else "live", stdout="Book empty after cancel-all")
            
        errors = []
        for p in positions:
            side = "sell" if p.get("side") == "long" else "buy"
            pair = p.get("symbol", p.get("pair", ""))
            vol = p.get("size", 0.0)
            if pair and vol > 0:
                # Issue reduce-only market order
                res = self.add_order(pair=pair, side=side, volume=vol, ordertype="market", validate=False, strategy_id="FLATTEN")
                if not res.ok:
                    errors.append(f"{pair}: {res.error_code}")
                    
        if errors:
            return OrderResult(False, "paper" if self.paper_mode else "live", error_code="FLATTEN_ERRORS", stdout=", ".join(errors))
            
        return OrderResult(True, "paper" if self.paper_mode else "live", stdout="Flattened all positions")"""

content = re.sub(
    r'    def close_all_market\(self, reason: str = "deadman_no_native_stop"\) -> OrderResult:.*?return res',
    close_all_new,
    content,
    flags=re.DOTALL
)

with open("app/execution/KrakenCliBridge.py", "w") as f:
    f.write(content)
