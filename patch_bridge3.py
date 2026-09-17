import re
with open("app/execution/KrakenCliBridge.py", "r") as f:
    content = f.read()

dispatch_paper_new = """    def _dispatch_paper(self, argv: List[str], *, pair: str, side: str, volume: float,
                        ordertype: str, stop_price: Optional[float],
                        strategy_id: str) -> OrderResult:
        \"\"\"§32 — Paper-Order: identische Struktur, 0 EUR Risiko, kein Live-Gate.\"\"\"
        has_stop = stop_price is not None
        if not self._cli_available():
            return OrderResult(False, "paper", error_code="ERR_KRAKEN_CLI_NOT_FOUND")
            
        if self.futures:
            # Check capital SoT before acting
            from app.execution.kraken_paper_sot import fetch_paper_capital
            st = fetch_paper_capital(self)
            if not st.ok:
                return OrderResult(False, "paper", error_code="ERR_PAPER_CAPITAL_UNAVAILABLE")

        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        result = OrderResult(
            not failed, "paper", txid=_extract_txid(stdout),
            pair=pair, side=side, volume=volume, ordertype=ordertype,
            has_native_stop_loss=has_stop and not failed,
            stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
            error_code=_extract_error(stdout, stderr) if failed else "",
        )
        self._audit(result, strategy_id)
        
        # In paper mode, if stop_price is set, we need to issue a separate reduce-only stop order (like live)
        if not failed and has_stop and self.futures:
            stop_side = "sell" if side == "buy" else "buy"
            stop_argv = self._prefix() + [stop_side, pair, f"{volume:g}", "--type=stop", f"--stop-price={stop_price}", "--reduce-only"]
            s_out, s_err, s_code = self._runner(stop_argv, self.config.tv_scraper_timeout_s)
            s_failed = bp.kraken_output_is_error(s_out, s_err, s_code)
            if s_failed:
                logger.error(f"Paper stop order failed: {s_err}")
                
        return result"""

content = re.sub(
    r'    def _dispatch_paper\(self.*?return result',
    dispatch_paper_new,
    content,
    flags=re.DOTALL
)

add_order_new = """    def add_order(self, *, pair: str, side: str, volume: float,
                  ordertype: str = "market", price: Optional[float] = None,
                  stop_price: Optional[float] = None, leverage: Optional[float] = None,
                  strategy_id: str = "", validate: bool = False) -> OrderResult:
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"invalid side {side!r}")
        if volume <= 0:
            return OrderResult(False, "sim", error_code="ZERO_VOLUME", pair=pair, side=side)
            
        if self.futures and ordertype not in ("market", "limit", "stop", "take-profit"):
            return OrderResult(False, "sim", error_code="ERR_INVALID_ORDERTYPE", stdout=f"Ordertype {ordertype} restricted")

        argv = self._prefix() + (
            [side, pair, f"{volume:g}"] if self.paper_mode or self.futures
            else ["add-order"]
        )
        if self.paper_mode:
            argv += [f"--type={ordertype}"]
            if price is not None:
                argv.append(f"--price={price}")
            # Do NOT append --stop-price here directly because we handle it via a second call in _dispatch_paper for futures
            return self._dispatch_paper(argv, pair=pair, side=side, volume=volume,
                                        ordertype=ordertype, stop_price=stop_price,
                                        strategy_id=strategy_id)
        if self.futures:
            argv += [f"--type={ordertype}"]
            if price is not None:
                argv.append(f"--price={price}")
            if leverage:
                argv.append(f"--leverage={leverage:g}")
            if strategy_id:
                argv.append(f"--client-order-id={strategy_id[:32]}")
            if not self.live_enabled:
                result = OrderResult(
                    ok=True, mode="sim", txid=f"SIM-{uuid.uuid4().hex[:10].upper()}",
                    pair=pair, side=side, volume=volume, ordertype=ordertype,
                    has_native_stop_loss=stop_price is not None, argv=argv,
                    stdout="[SIM] futures live trading disabled",
                )
                self._audit(result, strategy_id)
                return result
            stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
            failed = bp.kraken_output_is_error(stdout, stderr, code)
            if failed:
                result = OrderResult(
                    ok=False, mode="live", txid=_extract_txid(stdout),
                    pair=pair, side=side, volume=volume, ordertype=ordertype,
                    stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
                    error_code=_extract_error(stdout, stderr),
                )
                self._audit(result, strategy_id)
                return result
            
            # live stop logic
            has_stop = False
            if stop_price is not None:
                stop_side = "sell" if side == "buy" else "buy"
                stop_argv = self._prefix() + [stop_side, pair, f"{volume:g}", "--type=stop", f"--stop-price={stop_price}", "--reduce-only"]
                if strategy_id:
                    stop_argv.append(f"--client-order-id={strategy_id[:32]}-SL")
                s_out, s_err, s_code = self._runner(stop_argv, self.config.tv_scraper_timeout_s)
                if bp.kraken_output_is_error(s_out, s_err, s_code):
                    result = OrderResult(
                        ok=False, mode="live", txid=_extract_txid(stdout),
                        pair=pair, side=side, volume=volume, ordertype=ordertype,
                        stdout=s_out, stderr=s_err, exit_code=s_code, argv=stop_argv,
                        error_code=_extract_error(s_out, s_err),
                    )
                    self._audit(result, strategy_id)
                    return result
                has_stop = True
                argv = argv + stop_argv
            result = OrderResult(
                ok=True, mode="live", txid=_extract_txid(stdout),
                pair=pair, side=side, volume=volume, ordertype=ordertype,
                has_native_stop_loss=has_stop or stop_price is None,
                stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
            )
            self._audit(result, strategy_id)
            return result
            
        # Spot path
        argv += [f"--pair={pair}", f"--type={side}",
                f"--ordertype={ordertype}", f"--volume={volume:.8f}".rstrip("0").rstrip(".")]
        if price is not None and ordertype in ("limit", "stop", "take-profit"):
            argv.append(f"--price={price}")
        if leverage:
            argv.append(f"--leverage={leverage:g}")
        if stop_price is not None:
            # §20 Bracket-SL Pflicht — börsenseitiger Stop
            argv.append("--close-ordertype=stop-loss")
            argv.append(f"--close-price={stop_price}")
        if validate:
            argv.append("--validate")

        has_stop = stop_price is not None
        if not self.live_enabled:
            result = OrderResult(
                ok=True, mode="sim", txid=f"SIM-{uuid.uuid4().hex[:10].upper()}",
                pair=pair, side=side, volume=volume, ordertype=ordertype,
                has_native_stop_loss=has_stop, argv=argv,
                stdout="[SIM] live trading disabled (SIGMA_LIVE_TRADING/LIVE_APPROVED)",
            )
            self._audit(result, strategy_id)
            return result

        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        result = OrderResult(
            not failed, "live", txid=_extract_txid(stdout),
            pair=pair, side=side, volume=volume, ordertype=ordertype,
            has_native_stop_loss=has_stop and not failed,
            stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
            error_code=_extract_error(stdout, stderr) if failed else "",
        )
        if failed:
            logger.error("Kraken CLI order failed: %s | %s", result.error_code, stderr.strip() or stdout.strip())
        self._audit(result, strategy_id)
        return result"""

content = re.sub(
    r'    def add_order\(self.*?return result',
    add_order_new,
    content,
    flags=re.DOTALL
)

with open("app/execution/KrakenCliBridge.py", "w") as f:
    f.write(content)
