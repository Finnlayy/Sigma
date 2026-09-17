import re
with open("app/execution/KrakenCliBridge.py", "r") as f:
    content = f.read()

cancel_all_new = """    def cancel_all(self, reason: str = "kill_switch") -> OrderResult:
        if self.futures:
            if self.paper_mode:
                argv = [self.binary, "futures", "paper", "cancel-all"]
            else:
                argv = [self.binary, "futures", "cancel-all"]
        else:
            if self.paper_mode:
                argv = [self.binary, "paper", "cancel-all"]
            else:
                argv = [self.binary, "order", "cancel-all"]

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

with open("app/execution/KrakenCliBridge.py", "w") as f:
    f.write(content)
