import re
with open("app/execution/KrakenCliBridge.py", "r") as f:
    content = f.read()

# ADD MISSING METHODS AFTER balance
json_methods = """
    def _json_flag(self) -> List[str]:
        return ["-o", "json"]

    def _run_paper_read(self, argv: List[str]) -> OrderResult:
        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        return OrderResult(
            not failed, "paper", stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
            error_code=_extract_error(stdout, stderr) if failed else ""
        )

    def paper_status(self) -> OrderResult:
        base = [self.binary, "futures", "paper"] if self.futures else [self.binary, "paper"]
        return self._run_paper_read(base + ["status"] + self._json_flag())

    def paper_balance(self) -> OrderResult:
        base = [self.binary, "futures", "paper"] if self.futures else [self.binary, "paper"]
        return self._run_paper_read(base + ["balance"] + self._json_flag())

    def paper_history(self) -> OrderResult:
        base = [self.binary, "futures", "paper"] if self.futures else [self.binary, "paper"]
        return self._run_paper_read(base + ["history"] + self._json_flag())

    def run_leaf(self, name: str, *extra: str, confirmed: bool = False, json_output: bool = True) -> OrderResult:
        from app.execution.kraken_cli_registry import argv_for, get_command
        try:
            cmd = get_command(name)
            if not cmd:
                return OrderResult(False, "sim", error_code="ERR_UNKNOWN_LEAF")
            if "wallet/transfer" in name or "withdraw" in name:
                return OrderResult(False, "sim", error_code="L5_FORBIDDEN")
            is_dangerous = cmd.get("dangerous", False)
            if is_dangerous and not confirmed:
                return OrderResult(False, "sim", error_code="DANGEROUS_REQUIRES_CONFIRM")
            if is_dangerous and not self.paper_mode and not self.live_enabled:
                return OrderResult(False, "sim", error_code="ERR_LIVE_NOT_APPROVED")
            
            argv = argv_for(name, *extra, binary=self.binary)
            if json_output and "-o" not in argv and "--output=json" not in argv:
                argv.extend(self._json_flag())
            
            stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
            failed = bp.kraken_output_is_error(stdout, stderr, code)
            return OrderResult(
                not failed, "paper" if self.paper_mode else "live",
                stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
                error_code=_extract_error(stdout, stderr) if failed else ""
            )
        except Exception as e:
            return OrderResult(False, "sim", error_code=f"ERR_RUN_LEAF_FAIL: {e}")

    def cancel_after(self, timeout_s: int, confirmed: bool = False) -> OrderResult:
        if self.paper_mode:
            return OrderResult(True, "paper", argv=[self.binary, "futures", "cancel-after", str(timeout_s)])
        if self.futures and self.live_enabled and confirmed:
            argv = [self.binary, "futures", "cancel-after", str(timeout_s)]
            stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
            failed = bp.kraken_output_is_error(stdout, stderr, code)
            return OrderResult(
                not failed, "live", stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
                error_code=_extract_error(stdout, stderr) if failed else ""
            )
        return OrderResult(False, "sim", error_code="ERR_CANCEL_AFTER_NOT_ALLOWED")
"""
content = re.sub(
    r'(    def balance\(self\) -> OrderResult:.*?return OrderResult\(not failed.*?error_code=_extract_error\(stdout, stderr\) if failed else ""\))',
    r'\1\n' + json_methods,
    content,
    flags=re.DOTALL
)

with open("app/execution/KrakenCliBridge.py", "w") as f:
    f.write(content)
