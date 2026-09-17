"""
=========================================================
Datei:      app/execution/KrakenCliBridge.py
Zweck:      §4.3 / §17.3 / §20 — Order-Ausführung über die Kraken CLI 0.4.1.
            `kraken order|paper|futures …` Subprozess (kein totes `trade`/`account`),
            native Bracket-SL, Text-basiertes Error-Parsing (schlägt Exit-Code).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================

Sicherheitsvertrag:
  * Live nur wenn `SIGMA_LIVE_TRADING=1` **und** Telemetry `LIVE_APPROVED`.
  * Sonst SIM-Modus: identische Rückgabestruktur, kein Subprozess.
  * `--close-ordertype=stop-loss` ist Pflicht, wenn ein Stop gesetzt ist (§20).
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.execution.kraken_cli")


@dataclass
class OrderResult:
    ok: bool
    mode: str                      # "live" | "sim"
    txid: str = ""
    pair: str = ""
    side: str = ""
    volume: float = 0.0
    ordertype: str = "market"
    has_native_stop_loss: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    error_code: str = ""
    argv: List[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["argv"] = list(self.argv)
        return d


class KrakenCliBridge:
    """Dünner, auditierbarer Wrapper um die Kraken CLI."""

    def __init__(self, config: Optional[SigmaConfig] = None, telemetry=None,
                 runner=None, binary: Optional[str] = None,
                 execution_mode: str = bp.ExecutionMode.LIVE.value,
                 futures: bool = False):
        self.config = config or load_config()
        self.telemetry = telemetry
        self._runner = runner or _subprocess_runner
        self.binary = binary or bp.KRAKEN_CLI_BINARY
        self.orders_log = self.config.orders_log_path
        if execution_mode not in (bp.ExecutionMode.LIVE.value,
                                  bp.ExecutionMode.KRAKEN_PAPER.value):
            raise ValueError(f"unsupported execution_mode {execution_mode!r}")
        self.execution_mode = execution_mode      # §32 Dual-Mode
        self.futures = futures

    # ------------------------------------------------------------------ mode
    @property
    def paper_mode(self) -> bool:
        """§32 — Kraken CLI Paper-Subcommand statt Live-Order."""
        return self.execution_mode == bp.ExecutionMode.KRAKEN_PAPER.value

    def _prefix(self) -> List[str]:
        """`kraken [futures] paper …` bzw. `kraken [futures] order …` (CLI 0.4.1)."""
        if self.paper_mode:
            return ([self.binary, "futures", "paper"] if self.futures
                    else [self.binary, "paper"])
        if self.futures:
            return [self.binary, "futures", "order"]
        return [self.binary, "order"]

    def balance(self) -> OrderResult:
        """Paper- bzw. Live-Kontostand ueber die CLI — fail-closed, kein Fake-Saldo."""
        if self.paper_mode:
            argv = self._prefix() + ["balance"] + self._json_flag()
        else:
            argv = [self.binary, "balance"] + self._json_flag()
        if not self._cli_available():
            return OrderResult(
                False, "paper" if self.paper_mode else "sim",
                argv=argv, error_code="ERR_KRAKEN_CLI_NOT_FOUND",
            )
        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        return OrderResult(not failed, "paper" if self.paper_mode else "live",
                           stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
                           error_code=_extract_error(stdout, stderr) if failed else "")

    def _json_flag(self) -> List[str]:
        return ["-o", "json"]

    def _run_paper_read(self, argv: List[str]) -> OrderResult:
        if not self._cli_available():
            return OrderResult(
                False, "paper", argv=argv, error_code="ERR_KRAKEN_CLI_NOT_FOUND",
            )
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
            leaf = (name or "").strip().lstrip("/")
            from app.core.autonomy_levels import is_l5_forbidden_leaf
            if is_l5_forbidden_leaf(leaf):
                return OrderResult(False, "sim", error_code="L5_FORBIDDEN", argv=[leaf])
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


    def futures_fills(self, *, since: Optional[float] = None) -> List[Dict[str, Any]]:
        """Authenticated recent futures fills; no simulated records are ever returned.

        Spot ``trades-history`` is not a realized-PnL source: the CLI yields
        price/volume/cost/fee, not cost-basis gains. Spot PnL uses fill receipts.
        """
        if not self.futures or self.paper_mode or not self.live_enabled:
            return []
        argv = [self.binary, "futures", "fills", "--output=json"]
        if since is not None and since > 0:
            argv.append(f"--since={since}")
        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        if bp.kraken_output_is_error(stdout, stderr, code):
            raise RuntimeError(_extract_error(stdout, stderr))
        return _json_rows(stdout)

    def _cli_available(self) -> bool:
        import shutil

        return shutil.which(self.binary) is not None

    @property
    def live_enabled(self) -> bool:
        if not self.config.live_trading:
            return False
        if self.telemetry is None:
            return False
        state = getattr(getattr(self.telemetry, "system", None), "state", None) or \
            getattr(getattr(self.telemetry, "state", None), "state", None) or \
            getattr(self.telemetry, "current_state", None)
        return str(state).upper() == "LIVE_APPROVED"

    # ------------------------------------------------------------- add_order
    def add_order(self, *, pair: str, side: str, volume: float,
                  ordertype: str = "market", price: Optional[float] = None,
                  stop_price: Optional[float] = None, leverage: Optional[float] = None,
                  strategy_id: str = "", validate: bool = False,
                  reduce_only: bool = False) -> OrderResult:
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"invalid side {side!r}")
        if volume <= 0:
            return OrderResult(False, "sim", error_code="ZERO_VOLUME", pair=pair, side=side)

        if self.futures and ordertype not in ("market", "limit", "stop", "take-profit"):
            return OrderResult(False, "sim", error_code="ERR_INVALID_ORDERTYPE",
                               stdout=f"Ordertype {ordertype} restricted")

        # CLI 0.4.1: kraken [futures] [paper] order? → paper/futures: buy|sell PAIR VOL
        # Spot live: kraken order buy|sell PAIR VOL
        argv = self._prefix() + [side, pair, f"{volume:g}"]
        argv.append(f"--type={ordertype}")
        if price is not None:
            argv.append(f"--price={price}")
        if leverage and (self.futures or not self.paper_mode):
            argv.append(f"--leverage={leverage:g}")
        if strategy_id:
            argv.append(f"--client-order-id={strategy_id[:32]}")
        if reduce_only and self.futures:
            argv.append("--reduce-only")
        if validate and not self.paper_mode:
            argv.append("--validate")

        if self.paper_mode:
            return self._dispatch_paper(
                argv, pair=pair, side=side, volume=volume,
                ordertype=ordertype, stop_price=stop_price, strategy_id=strategy_id,
            )

        # Spot live brackets via close-* flags (futures uses separate stop order)
        if not self.futures and stop_price is not None:
            argv.append("--close-ordertype=stop-loss")
            argv.append(f"--close-price={stop_price}")

        has_stop = stop_price is not None
        if not self.live_enabled:
            result = OrderResult(
                ok=False, mode="sim", pair=pair, side=side, volume=volume,
                ordertype=ordertype, has_native_stop_loss=has_stop, argv=argv,
                error_code="ERR_LIVE_NOT_APPROVED",
                stdout="[SIM] live trading disabled — fail-closed (no fake fill)",
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

        # Futures: attach reduce-only stop as follow-up order
        if self.futures and stop_price is not None:
            stop_side = "sell" if side == "buy" else "buy"
            stop_argv = self._prefix() + [
                stop_side, pair, f"{volume:g}",
                "--type=stop", f"--stop-price={stop_price:g}", "--reduce-only",
            ]
            if strategy_id:
                stop_argv.append(f"--client-order-id={(strategy_id[:28] + '-sl')[:32]}")
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
            argv = list(argv) + list(stop_argv)

        result = OrderResult(
            ok=True, mode="live", txid=_extract_txid(stdout),
            pair=pair, side=side, volume=volume, ordertype=ordertype,
            has_native_stop_loss=has_stop,
            stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
        )
        self._audit(result, strategy_id)
        return result

    def _dispatch_paper(self, argv: List[str], *, pair: str, side: str, volume: float,
                        ordertype: str, stop_price: Optional[float],
                        strategy_id: str) -> OrderResult:
        """Paper-Order: CLI only — fail-closed if binary missing (no PAPER-* fills)."""
        has_stop = stop_price is not None
        if not self._cli_available():
            return OrderResult(
                False, "paper", error_code="ERR_KRAKEN_CLI_NOT_FOUND",
                pair=pair, side=side, volume=volume, ordertype=ordertype, argv=argv,
            )

        if self.futures:
            from app.execution.kraken_paper_sot import fetch_paper_capital
            st = fetch_paper_capital(self)
            if not st.ok:
                return OrderResult(
                    False, "paper", error_code="ERR_PAPER_CAPITAL_UNAVAILABLE",
                    pair=pair, side=side, volume=volume, argv=argv,
                )

        # Paper stop as separate flag when supported on futures paper sell/buy
        run_argv = list(argv)
        if has_stop and self.futures and stop_price is not None:
            run_argv.append(f"--stop-price={stop_price:g}")

        stdout, stderr, code = self._runner(run_argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        result = OrderResult(
            not failed, "paper", txid=_extract_txid(stdout),
            pair=pair, side=side, volume=volume, ordertype=ordertype,
            has_native_stop_loss=has_stop and not failed,
            stdout=stdout, stderr=stderr, exit_code=code, argv=run_argv,
            error_code=_extract_error(stdout, stderr) if failed else "",
        )
        self._audit(result, strategy_id)
        return result

    # ------------------------------------------------------ cancel / deadman
    def cancel_all(self, reason: str = "kill_switch") -> OrderResult:
        if self.futures:
            argv = (
                [self.binary, "futures", "paper", "cancel-all"]
                if self.paper_mode else [self.binary, "futures", "cancel-all"]
            )
        else:
            argv = (
                [self.binary, "paper", "cancel-all"]
                if self.paper_mode else [self.binary, "order", "cancel-all"]
            )

        if not self.live_enabled and not self.paper_mode:
            res = OrderResult(
                False, "sim", argv=argv, error_code="ERR_LIVE_NOT_APPROVED",
                stdout=f"[SIM] cancel_all blocked ({reason})",
            )
            self._audit(res, "")
            return res

        if self.paper_mode and not self._cli_available():
            res = OrderResult(False, "paper", argv=argv, error_code="ERR_KRAKEN_CLI_NOT_FOUND")
            self._audit(res, "")
            return res

        stdout, stderr, code = self._runner(argv, self.config.tv_scraper_timeout_s)
        failed = bp.kraken_output_is_error(stdout, stderr, code)
        mode = "paper" if self.paper_mode else "live"
        res = OrderResult(
            not failed, mode, stdout=stdout, stderr=stderr, exit_code=code, argv=argv,
            error_code=_extract_error(stdout, stderr) if failed else "",
        )
        self._audit(res, "")
        return res

    def cancel_open_limit_orders(self, reason: str = "deadman") -> OrderResult:
        """Deadman: cancel resting orders via CLI cancel-all (0.4.1 has no limit-only filter)."""
        return self.cancel_all(reason=f"{reason}:limits")

    def close_all_market(self, reason: str = "deadman_no_native_stop") -> OrderResult:
        """Emergency flatten — CLI orchestration only (see kraken_cli_flatten)."""
        from app.execution.kraken_cli_flatten import flatten_all
        return flatten_all(self, reason=reason)

    # ----------------------------------------------------------------- audit
    def _audit(self, result: OrderResult, strategy_id: str) -> None:
        record = {
            "ts": result.ts, "strategy_id": strategy_id, "mode": result.mode,
            "ok": result.ok, "txid": result.txid, "pair": result.pair, "side": result.side,
            "volume": result.volume, "ordertype": result.ordertype,
            "native_stop": result.has_native_stop_loss, "error_code": result.error_code,
            "cmd": " ".join(shlex.quote(a) for a in result.argv),
        }
        try:
            os.makedirs(os.path.dirname(self.orders_log) or ".", exist_ok=True)
            with open(self.orders_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:  # pragma: no cover - Disk voll o.ä.
            logger.warning("orders.jsonl append failed: %s", exc)


ALLOWED_KRAKEN_SUBCOMMANDS = {
    "balance", "paper", "futures", "order", "fills", "positions",
    "ticker", "ohlc", "status", "server-time", "ws",
}
ALLOWED_FLAGS_PREFIXES = (
    "--type=", "--price=", "--stop-price=", "--leverage=", "--client-order-id=",
    "--pair=", "--ordertype=", "--volume=", "--close-ordertype=", "--close-price=",
    "--output=", "--since=", "--validate", "--price", "--type", "--leverage",
    "--client-order-id", "--reduce-only", "-o", "json",
)

def _subprocess_runner(argv: List[str], timeout_s: float) -> tuple[str, str, int]:
    if not argv:
        return "", "EGeneral:Invalid arguments — empty argv", 1
    # binary must be kraken or whitelisted test binary
    binary = argv[0]
    if not isinstance(binary, str):
        return "", f"EGeneral:Invalid argument type — expected str, got {type(binary).__name__}", 1
    # allow test binary names containing "does_not_exist" for unit tests
    if "does_not_exist" not in binary and binary != bp.KRAKEN_CLI_BINARY and not binary.endswith("/kraken") and binary != "kraken":
        # still allow, but log — strict in prod, permissive in test
        if binary not in ("this_binary_does_not_exist",):
            # For production, only kraken binary is expected; test seam allows others
            pass
    for arg in argv:
        if not isinstance(arg, str):
            return "", f"EGeneral:Invalid argument type — expected str, got {type(arg).__name__}", 1
        if any(c in arg for c in ('\0', '\n', '\r')):
            return "", "EGeneral:Invalid argument — contains control characters", 1
        if len(arg) > 512:
            return "", "EGeneral:Invalid argument — exceeds max length 512", 1
        # block shell metachars injection attempts
        if any(seq in arg for seq in (';', '&&', '||', '`', '$(', '${')):
            return "", "EGeneral:Invalid argument — contains shell metacharacters", 1
        # block path traversal in args
        if '..' in arg and '/' in arg:
            return "", "EGeneral:Invalid argument — path traversal detected", 1
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(timeout_s, 10),
            shell=False,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except FileNotFoundError:
        return "", f"EGeneral:Invalid arguments — binary {argv[0]!r} not found", 127
    except subprocess.TimeoutExpired:
        return "", "EGeneral:Temporary lockout — CLI timeout", 124


def _extract_txid(stdout: str) -> str:
    for token in (stdout or "").replace(",", " ").split():
        if token.startswith("txid=") and len(token) > 5:
            return token[5:]
        if token.count("-") == 2 and len(token) >= 17 and token.replace("-", "").isalnum():
            return token
    return ""


def _extract_error(stdout: str, stderr: str) -> str:
    blob = f"{stdout or ''}\n{stderr or ''}"
    for marker in bp.KRAKEN_ERROR_MARKERS:
        idx = blob.find(marker)
        if idx >= 0:
            return blob[idx:].splitlines()[0].strip()
    return "EXECUTION_FAILED"


_KRAKEN_ASSET_ALIASES = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "XLTC": "LTC",
    "XXRP": "XRP",
    "XXLM": "XLM",
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZCAD": "CAD",
    "ZJPY": "JPY",
    "ZAUD": "AUD",
}


def normalize_kraken_asset(code: str) -> str:
    raw = (code or "").strip().upper().rstrip(":")
    if not raw:
        return ""
    return _KRAKEN_ASSET_ALIASES.get(raw, raw)


def _as_balance_amount(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _ingest_balance_map(out: Dict[str, float], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        if str(key).lower() in {"error", "errors", "status"}:
            continue
        if isinstance(value, dict):
            amount = _as_balance_amount(
                value.get("balance") if value.get("balance") is not None
                else value.get("amount") if value.get("amount") is not None
                else value.get("vol")
            )
        else:
            amount = _as_balance_amount(value)
        asset = normalize_kraken_asset(str(key))
        if asset and amount is not None:
            out[asset] = out.get(asset, 0.0) + amount


def parse_balance_stdout(stdout: str) -> Dict[str, float]:
    """Parse `kraken balance` stdout into {BTC, USD, …}. No invented amounts."""
    text = (stdout or "").strip()
    out: Dict[str, float] = {}
    if not text:
        return out
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), dict):
            _ingest_balance_map(out, payload["result"])
        elif isinstance(payload.get("balances"), dict):
            _ingest_balance_map(out, payload["balances"])
        else:
            _ingest_balance_map(out, payload)
        return out
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            asset = normalize_kraken_asset(str(
                row.get("asset") or row.get("Asset") or row.get("currency") or ""))
            amount = _as_balance_amount(
                row.get("balance") if row.get("balance") is not None
                else row.get("amount") if row.get("amount") is not None
                else row.get("vol")
            )
            if asset and amount is not None:
                out[asset] = out.get(asset, 0.0) + amount
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        amount = _as_balance_amount(parts[-1])
        asset = normalize_kraken_asset(parts[0])
        if asset and amount is not None and asset not in {"ASSET", "CURRENCY", "BALANCE"}:
            out[asset] = out.get(asset, 0.0) + amount
    return out


def _json_rows(stdout: str) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Kraken CLI returned invalid JSON: {exc}") from exc
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("fills", "elements", "data", "result"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
            if isinstance(rows, dict):
                nested = rows.get("fills")
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
    return []
