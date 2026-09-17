import re
with open("app/execution/LoopAPipeline.py", "r") as f:
    content = f.read()

daily_cap_init = """    def __init__(self, config: Optional[SigmaConfig] = None,
                 safety: Optional[SafetyGuard] = None,
                 quant: Optional[QuantEngine] = None,
                 judge=None,
                 dispatcher=None,
                 equity_provider=None):
        self.config = config or load_config()
        self.safety = safety or get_safety_guard(self.config)
        self.quant = quant or get_quant_engine()
        self.judge = judge
        self.dispatcher = dispatcher
        self.equity_provider = equity_provider
        self._daily_notional_day = ""
        self._daily_notional = {"spot": 0.0, "futures": 0.0}"""

content = re.sub(
    r'    def __init__.*?self\.equity_provider = equity_provider',
    daily_cap_init,
    content,
    flags=re.DOTALL
)

notional_check = """        limits = notional_limits(sig.symbol)
        notional = quantity * sig.price
        
        # Enforce max daily notional
        import time
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._daily_notional_day != today:
            self._daily_notional_day = today
            self._daily_notional = {"spot": 0.0, "futures": 0.0}
            
        mkt = "futures" if futures else "spot"
        used = self._daily_notional[mkt]
        max_daily = limits["max_daily_notional_usd"]
        
        if used + notional > max_daily:
            return self._reject("symbol", "DAILY_NOTIONAL_CAP",
                                f"Daily notional limit exceeded ({used} + {notional} > {max_daily})", 403, sig, trace)
            
        self._daily_notional[mkt] += notional

        if notional > limits["max_order_notional_usd"]:
            quantity = limits["max_order_notional_usd"] / sig.price
            notional = limits["max_order_notional_usd"]
            self._daily_notional[mkt] -= (quantity * sig.price) # revert
            self._daily_notional[mkt] += notional # add correct
            trace.append("notional_capped")"""

content = re.sub(
    r'        limits = notional_limits\(sig\.symbol\).*?trace\.append\("notional_capped"\)',
    notional_check,
    content,
    flags=re.DOTALL
)

with open("app/execution/LoopAPipeline.py", "w") as f:
    f.write(content)
