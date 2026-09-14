"""
=========================================================
Datei:      sigma/strategies/dynamic_pine_provisioner.py
Zweck:      MP-09 dynamischer Pine-v6-Provisionierer: deterministische
            v6-Scripts (Sigma-Standard-Header, Schema-A-Alerts mit
            eindeutigen idempotency_keys, Bar-Close/barmerge.lookahead_off,
            Fraktal-TPs fuer MP-15) + Auto-Haertung fremder Pine-Skripte
            (harden_pine_code, fail-closed). KEIN TV-Upload/Login,
            KEINE Orderausfuehrung — Upload bleibt Loop B / app/tv.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Pine) / Noir (kein Repaint, Fail-Closed)
=========================================================
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sigma.strategies.pine_v6_generator import (
    standard_strategy_header,
    static_pine_checks,
)

# --- Fraktal (MP-15) Konstanten -----------------------------------------
TP1_QTY_PCT = 40
TP2_QTY_PCT = 30
TP3_QTY_PCT = 20
RUNNER_QTY_PCT = 10
FEE_COVERED_BE_OFFSET = 0.0005  # new_sl = entry x (1 +- 0,0005)

# Seq je Alert-Zustand (Prompt: Entry=01, TP1=02, TP2=03, TP3=04,
# UPDATE_SL=05, CLOSE=06)
_SEQ_BY_ACTION = {
    "BUY": 1, "SELL": 1,
    "TP1": 2, "TP2": 3, "TP3": 4,
    "UPDATE_SL": 5,
    "CLOSE": 6,
}


# ------------------------------------------------------------------- data

@dataclass(frozen=True)
class ProvisionRequest:
    """Eingabe fuer den Eigen-Generator (deterministisch)."""

    symbol: str
    strategy_id: str
    side: str = "buy"                 # buy | sell
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    fixed_leverage: int = 5
    webhook_secret: str = ""
    ttl_minutes: int = 120
    bar_close_only: bool = True
    # Fraktal-Modus (MP-15): optional gestaffelte TPs
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    generated_ts: int = 0   # Determinismus: injizierbar, Default 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class PineHardeningRequest:
    """Fremdes Pine v5/v6 + gleiche Felder wie ProvisionRequest."""

    raw_code: str
    symbol: str
    strategy_id: str
    side: str = "buy"
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    fixed_leverage: int = 5
    webhook_secret: str = ""
    ttl_minutes: int = 120
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    generated_ts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class HardenedPineResult:
    """Ergebnis der Haertung: v6-Code, Transformationen, ok/reasons."""

    code: str = ""
    transformations: List[str] = field(default_factory=list)
    hardening_ok: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ---------------------------------------------------------------- payload

def idempotency_key(strategy_id: str, action: str, seq: int) -> str:
    """Eindeutige Tracking-ID: {strategy_id}_{action}_{seq:02d}_{bar_unix}.
    bar_unix wird von TV beim Feuern substituiert ({{timenow}})."""
    return f"{strategy_id}_{action.upper()}_{int(seq):02d}_{{{{timenow}}}}"


def build_schema_a_payload(
    *,
    action: str,
    ticker: str,
    price: float,
    stop_loss: float,
    take_profit: Optional[float],
    fixed_leverage: int,
    strategy_id: str,
    secret: str,
    seq: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Schema-A-Payload (SigmaL4AlertPayload) + idempotency_key."""
    payload: Dict[str, Any] = {
        "action": str(action).upper(),
        "ticker": ticker,
        "price": round(float(price), 10),
        "stop_loss": round(float(stop_loss), 10),
        "take_profit": round(float(take_profit), 10) if take_profit else None,
        "fixed_leverage": int(fixed_leverage),
        "strategy_id": strategy_id,
        "secret": secret or "<SIGMA_WEBHOOK_SECRET>",
        "idempotency_key": idempotency_key(strategy_id, action, seq),
    }
    if extra:
        payload.update(extra)
    return payload


def _json_pine(payload: Dict[str, Any]) -> str:
    """JSON ohne Leerzeichen, einfache Anführungszeichen fuer Pine-Literal."""
    return json.dumps(payload, separators=(",", ":")).replace("'", "\\'")


def de_provision_hint(request: ProvisionRequest) -> str:
    """Kennung, unter der das Skript nach TTL/TP entfernbar ist
    (Loop B kümmert sich um TV selbst)."""
    return f"sigma:{request.strategy_id}:{request.symbol}"


# ------------------------------------------------------------ generator

def generate_dynamic_pine(request: ProvisionRequest) -> str:
    """Vollstaendiges, eigenstaendiges Pine-v6-Script (deterministisch).
    Nur Bar-Close-Alerts (barstate.isconfirmed), lookahead_off, Schema-A-
    Payloads mit eindeutigen idempotency_keys, Fraktal-Modus bei tp1-3."""
    if not request.symbol or not request.strategy_id:
        raise ValueError("symbol und strategy_id sind Pflicht")
    side = str(request.side).lower()
    if side not in ("buy", "sell"):
        raise ValueError("side muss buy/sell sein")
    if request.entry <= 0 or request.stop_loss <= 0:
        raise ValueError("entry und stop_loss muessen > 0 sein")

    fractal = request.tp1 is not None or request.tp2 is not None or request.tp3 is not None
    ticker = request.symbol
    sid = request.strategy_id
    lev = int(request.fixed_leverage)
    sl = float(request.stop_loss)
    entry = float(request.entry)
    tp = float(request.take_profit) if request.take_profit else (float(request.tp3) if request.tp3 else 0.0)

    seq = _SEQ_BY_ACTION
    entry_action = "BUY" if side == "buy" else "SELL"
    exit_action = "CLOSE"

    if fractal:
        tp1 = float(request.tp1 or 0.0)
        tp2 = float(request.tp2 or 0.0)
        tp3 = float(request.tp3 or 0.0)
        if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
            raise ValueError("Fraktal-Modus braucht tp1/tp2/tp3 > 0")
        entry_extra = {
            "tp1": {"price": round(tp1, 10), "qty_pct": TP1_QTY_PCT},
            "tp2": {"price": round(tp2, 10), "qty_pct": TP2_QTY_PCT},
            "tp3": {"price": round(tp3, 10), "qty_pct": TP3_QTY_PCT},
            "runner_qty_pct": RUNNER_QTY_PCT,
            "fee_covered_be_offset": FEE_COVERED_BE_OFFSET,
        }
        entry_payload = build_schema_a_payload(
            action=entry_action, ticker=ticker, price=entry, stop_loss=sl,
            take_profit=tp3, fixed_leverage=lev, strategy_id=sid,
            secret=request.webhook_secret, seq=seq[entry_action], extra=entry_extra)
        new_sl = entry * (1.0 + FEE_COVERED_BE_OFFSET) if side == "buy" \
            else entry * (1.0 - FEE_COVERED_BE_OFFSET)
        update_payload = build_schema_a_payload(
            action="UPDATE_SL", ticker=ticker, price=entry, stop_loss=sl,
            take_profit=tp3, fixed_leverage=lev, strategy_id=sid,
            secret=request.webhook_secret, seq=seq["UPDATE_SL"],
            extra={"new_sl": round(new_sl, 10),
                   "reason": "TP1_HIT_FEE_COVERED_BREAKEVEN"})
        close_payload = build_schema_a_payload(
            action=exit_action, ticker=ticker, price=entry, stop_loss=sl,
            take_profit=tp3, fixed_leverage=lev, strategy_id=sid,
            secret=request.webhook_secret, seq=seq[exit_action])
        tp1_payload = build_schema_a_payload(
            action="TP1", ticker=ticker, price=tp1, stop_loss=sl,
            take_profit=tp3, fixed_leverage=lev, strategy_id=sid,
            secret=request.webhook_secret, seq=seq["TP1"],
            extra={"qty_pct": TP1_QTY_PCT})
        tp2_payload = build_schema_a_payload(
            action="TP2", ticker=ticker, price=tp2, stop_loss=sl,
            take_profit=tp3, fixed_leverage=lev, strategy_id=sid,
            secret=request.webhook_secret, seq=seq["TP2"],
            extra={"qty_pct": TP2_QTY_PCT})
        tp3_payload = build_schema_a_payload(
            action="TP3", ticker=ticker, price=tp3, stop_loss=sl,
            take_profit=tp3, fixed_leverage=lev, strategy_id=sid,
            secret=request.webhook_secret, seq=seq["TP3"],
            extra={"qty_pct": TP3_QTY_PCT})
    else:
        entry_payload = build_schema_a_payload(
            action=entry_action, ticker=ticker, price=entry, stop_loss=sl,
            take_profit=tp if tp else None, fixed_leverage=lev,
            strategy_id=sid, secret=request.webhook_secret, seq=seq[entry_action])
        close_payload = build_schema_a_payload(
            action=exit_action, ticker=ticker, price=entry, stop_loss=sl,
            take_profit=tp if tp else None, fixed_leverage=lev,
            strategy_id=sid, secret=request.webhook_secret, seq=seq[exit_action])
        tp1_payload = tp2_payload = tp3_payload = update_payload = None

    header = standard_strategy_header(f"Sigma {sid}")
    ts = int(request.generated_ts or 0)
    blocks: List[str] = [
        "//@version=6",
        header,
        f"// Sigma dynamic provisioner — strategy_id={sid} symbol={ticker} "
        f"side={side} ttl_minutes={request.ttl_minutes}",
        f"// de_provision_hint={de_provision_hint(request)} generated_ts={ts}",
        "",
        "entryPrice = " + repr(entry),
        "stopLoss   = " + repr(sl),
        "takeProfit = " + repr(tp),
        "lev        = " + repr(lev),
        "",
        "confirmed  = barstate.isconfirmed",
        "htfClose   = request.security(syminfo.tickerid, '60', close, lookahead=barmerge.lookahead_off)",
        "",
    ]

    if side == "buy":
        blocks.append("longCond = confirmed and ta.crossover(close, entryPrice)")
        blocks.append("if longCond")
        blocks.append(f"    strategy.entry(\"SIGMA_ENTRY\", strategy.long, alert_message = '{_json_pine(entry_payload)}')")
        blocks.append(f"    strategy.exit(\"SIGMA_EXIT\", \"SIGMA_ENTRY\", stop=stopLoss, limit=takeProfit, alert_message = '{_json_pine(close_payload)}')")
    else:
        blocks.append("shortCond = confirmed and ta.crossunder(close, entryPrice)")
        blocks.append("if shortCond")
        blocks.append(f"    strategy.entry(\"SIGMA_ENTRY\", strategy.short, alert_message = '{_json_pine(entry_payload)}')")
        blocks.append(f"    strategy.exit(\"SIGMA_EXIT\", \"SIGMA_ENTRY\", stop=stopLoss, limit=takeProfit, alert_message = '{_json_pine(close_payload)}')")

    if fractal:
        blocks.append("")
        blocks.append("// Fraktal-Teil-TPs (MP-15): Alerts nach Bar-Close, kein Repaint")
        blocks.append("tp1 = " + repr(tp1))
        blocks.append("tp2 = " + repr(tp2))
        blocks.append("tp3 = " + repr(tp3))
        blocks.append("tp1Hit = confirmed and high >= tp1")
        blocks.append("tp2Hit = confirmed and high >= tp2")
        blocks.append("tp3Hit = confirmed and high >= tp3")
        blocks.append("if tp1Hit")
        blocks.append(f"    alert('{_json_pine(tp1_payload)}', alert.freq_once_per_bar_close)")
        blocks.append("if tp2Hit")
        blocks.append(f"    alert('{_json_pine(tp2_payload)}', alert.freq_once_per_bar_close)")
        blocks.append("if tp3Hit")
        blocks.append(f"    alert('{_json_pine(tp3_payload)}', alert.freq_once_per_bar_close)")
        blocks.append("// Nach TP1: SL auf Fee-Covered Break-Even nachfuehren")
        blocks.append("newSl = " + repr(round(new_sl, 10)))
        blocks.append("if tp1Hit")
        blocks.append(f"    strategy.exit(\"SIGMA_EXIT\", \"SIGMA_ENTRY\", stop=newSl, limit=tp3, alert_message = '{_json_pine(update_payload)}')")

    blocks.append("")
    blocks.append("plot(close, \"px\", display=display.none)")
    return "\n".join(blocks) + "\n"


# ------------------------------------------------------------ hardening

def _extract_strategy_header(code: str) -> Optional[str]:
    m = re.search(r"strategy\s*\(([^)]*)\)", code, re.DOTALL)
    return m.group(0) if m else None


def _header_transformations(header: str) -> List[str]:
    """Protokolliert abweichende Header-Werte, die ueberschrieben werden."""
    trans: List[str] = []

    def _value(key: str) -> Optional[str]:
        m = re.search(key + r"\s*=\s*([^,\s)]+)", header)
        return m.group(1) if m else None

    expectations = {
        "initial_capital": "10000",
        "default_qty_type": "strategy.cash",
        "default_qty_value": "100",
        "pyramiding": "1",
        "commission_type": "strategy.commission.percent",
        "commission_value": "0.04",
        "calc_on_every_tick": "false",
    }
    for key, expected in expectations.items():
        value = _value(key)
        if value is not None and value != expected:
            trans.append(f"header_overwrite:{key}->{expected}")
    return trans


def _strip_foreign_alerts(code: str) -> tuple:
    """Entfernt fremde alert_message-Argumente und alert()-Aufrufe
    (fremde URLs/Payloads). -> (code, transformations)."""
    trans: List[str] = []
    # Einfach-gequotete Payloads (auch mit doppelten Anfuehrungszeichen im
    # JSON) und doppelt-gequotete Payloads.
    new_code, n = re.subn(
        r"alert_message\s*=\s*(?:'[^']*'|\"[^\"]*\")\s*,?", "", code)
    if n:
        trans.append(f"foreign_alert_message_removed:x{n}")
    out_lines: List[str] = []
    removed = 0
    for line in new_code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("alert(") or stripped.startswith("alert "):
            removed += 1
            continue
        out_lines.append(line)
    if removed:
        trans.append(f"foreign_alert_call_removed:x{removed}")
    # Aufraeumen: doppelte Kommata / Komma vor schliessender Klammer
    code = "\n".join(out_lines)
    code = re.sub(r",\s*,", ",", code)
    code = re.sub(r"\(\s*,", "(", code)
    code = re.sub(r",\s*\)", ")", code)
    return code, trans


def _balanced_call(code: str, start: int) -> int:
    """Index der schliessenden Klammer eines Funktionsaufrufs ab start
    (start = Index der oeffnenden Klammer); -1 wenn unbalanciert."""
    depth = 0
    in_str: Optional[str] = None
    i = start
    n = len(code)
    while i < n:
        ch = code[i]
        if in_str:
            if ch == in_str:
                in_str = None
            elif ch == "\\":
                i += 2
                continue
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _inject_alert_messages(
    code: str,
    payloads: Dict[str, Dict[str, Any]],
    used: Dict[str, int],
) -> tuple:
    """Versieht strategy.entry/exit/close-Aufrufe ohne alert_message mit
    Schema-A-Payload. Entry-Richtung aus strategy.long/short; bei
    wiederholten Aktionen wird seq hochgezaehlt (Eindeutigkeit)."""
    trans: List[str] = []
    out: List[str] = []
    i = 0
    n = len(code)
    while i < n:
        found: Optional[str] = None
        for call in ("strategy.entry", "strategy.exit", "strategy.close"):
            if code.startswith(call, i):
                found = call
                break
        if found is None:
            out.append(code[i])
            i += 1
            continue
        j = i + len(found)
        while j < n and code[j] != "(":
            out.append(code[j])
            j += 1
        if j >= n:
            out.append(code[i])
            i += 1
            continue
        end = _balanced_call(code, j)
        if end < 0:
            out.append(code[i])
            i += 1
            continue
        call_src = code[i:end + 1]
        inner_body = call_src[len(found) + 1: -1]
        if "alert_message" not in inner_body:
            key: Optional[str] = None
            if found == "strategy.entry":
                key = "entry_short" if "strategy.short" in inner_body else "entry_long"
            elif found == "strategy.exit":
                key = "exit"
            else:
                key = "close"
            base = payloads.get(key)  # type: ignore[arg-type]
            if base is not None:
                action = str(base["action"])
                used[action] = used.get(action, 0) + 1
                seq = _SEQ_BY_ACTION.get(action, 6) + used[action] - 1
                payload = dict(base)
                payload["idempotency_key"] = idempotency_key(
                    str(base["strategy_id"]), action, seq)
                trans.append(f"alert_message_injected:{key}")
                out.append(
                    found + "(" + inner_body + f", alert_message = '{_json_pine(payload)}')")
                i = end + 1
                continue
        out.append(call_src)
        i = end + 1
    return "".join(out), trans


def _guard_lookahead(code: str) -> tuple:
    """request.security ohne lookahead -> lookahead_off ergaenzen
    (balancierte Klammern); lookahead_on -> ersetzen."""
    trans: List[str] = []
    out: List[str] = []
    i = 0
    n = len(code)
    added = 0
    while i < n:
        if code.startswith("request.security", i):
            j = i + len("request.security")
            while j < n and code[j] != "(":
                out.append(code[j])
                j += 1
            if j >= n:
                out.append(code[i]); i += 1; continue
            end = _balanced_call(code, j)
            if end < 0:
                out.append(code[i]); i += 1; continue
            inner = code[j:end + 1]
            if "lookahead" not in inner:
                inner = inner[:-1] + ", lookahead=barmerge.lookahead_off)"
                added += 1
            out.append(inner)
            i = end + 1
            continue
        out.append(code[i])
        i += 1
    if added:
        trans.append(f"lookahead_off_added:x{added}")
    code = "".join(out)
    if "lookahead_on" in code:
        code = code.replace("lookahead_on", "barmerge.lookahead_off")
        trans.append("lookahead_on_replaced")
    return code, trans


def _guard_bar_close(code: str) -> tuple:
    """Bar-Close-Guard: wenn barstate.isconfirmed fehlt, wird die erste
    'if'-Zeile vor einem strategy-Aufruf gewrappt; nicht umschliessbar
    -> (code, [], False)."""
    if "barstate.isconfirmed" in code:
        return code, [], True
    lines = code.split("\n")
    out: List[str] = []
    wrapped = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("if ") and any(
            "strategy." in lines[j] for j in range(i + 1, min(i + 12, len(lines)))
        ):
            cond = stripped[3:].rstrip(":")
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + f"if (barstate.isconfirmed and ({cond}))")
            wrapped += 1
        else:
            out.append(line)
        i += 1
    if wrapped == 0:
        return code, [], False
    return "\n".join(out), ["barstate_isconfirmed_added:x" + str(wrapped)], True


def harden_pine_code(request: PineHardeningRequest) -> HardenedPineResult:
    """Haertet fremdes Pine v5/v6 auf Sigma-Standard (Transport):
    Version, Header, Webhook/Schema-A, Bar-Close/lookahead_off,
    Konstanten/Kopf. Nicht haertbar -> hardening_ok=False (fail-closed,
    kein Code fuer Deploy)."""
    fail = HardenedPineResult(hardening_ok=False)
    raw = (request.raw_code or "").strip()
    if not raw:
        return HardenedPineResult(hardening_ok=False, reasons=["empty_raw_code"])
    # Python-Quelle ist kein Pine
    if re.match(r"^\s*(import |from |#!/|def |class )", raw):
        return HardenedPineResult(hardening_ok=False, reasons=["python_source_not_pine"])
    # Intrabar-Logik (barstate.isrealtime) ist nicht statisch auf
    # Bar-Close umschliessbar -> fail-closed, kein Einsatzcode.
    if "barstate.isrealtime" in raw and re.search(r"strategy\.(entry|exit|close)", raw):
        return HardenedPineResult(
            hardening_ok=False, reasons=["intrabar_not_wrappable"])

    trans: List[str] = []
    code = raw

    # 1) Version
    if re.match(r"^\s*//@version=5\b", code):
        code = re.sub(r"^\s*//@version=5\b.*", "//@version=6", code, count=1)
        trans.append("version_upgrade:v5->v6")
    elif not re.match(r"^\s*//@version=6\b", code):
        code = "//@version=6\n" + code
        trans.append("version_added:v6")

    # 2) Header
    header = _extract_strategy_header(code)
    title = "Sigma Hardened"
    if header:
        m = re.search(r'strategy\s*\(\s*["\']([^"\']*)["\']', header)
        if m:
            title = m.group(1)
        trans.extend(_header_transformations(header))
        code = code.replace(header, standard_strategy_header(title))
        trans.append("header_standardized")
    else:
        code = code.replace("//@version=6", "//@version=6\n" + standard_strategy_header(title), 1)
        trans.append("header_added")

    # 3) Fremde Alerts entfernen, eigene Schema-A-Payloads injizieren
    code, t = _strip_foreign_alerts(code)
    trans.extend(t)
    payloads = _hardening_payloads(request)
    code, t = _inject_alert_messages(code, payloads, {})
    trans.extend(t)

    # 4) Bar-Close / lookahead
    code, t = _guard_lookahead(code)
    trans.extend(t)
    code, t, ok = _guard_bar_close(code)
    trans.extend(t)
    if not ok:
        return HardenedPineResult(
            hardening_ok=False, reasons=["barclose_not_wrappable"],
            transformations=trans)

    # 5) Konstanten & Kopf (nach der //@version-Zeile, die in TV die
    # erste Zeile sein muss)
    ts = int(request.generated_ts or 0)
    comment = (f"// Sigma hardened — strategy_id={request.strategy_id} "
               f"symbol={request.symbol} ttl_minutes={request.ttl_minutes}\n"
               f"// secret=<SIGMA_WEBHOOK_SECRET> generated_ts={ts}\n"
               f"// de_provision_hint={de_provision_hint(_as_provision(request))}\n")
    lines = code.split("\n")
    if lines and lines[0].strip().startswith("//@version"):
        code = lines[0] + "\n" + comment + "\n".join(lines[1:])
    else:
        code = comment + code
    trans.append("header_comment_added")

    # 6) Statische Checks
    issues = static_pine_checks(code)
    if issues:
        return HardenedPineResult(
            hardening_ok=False, reasons=issues, transformations=trans)
    return HardenedPineResult(
        code=code, transformations=trans, hardening_ok=True, reasons=[])


def _hardening_payloads(request: PineHardeningRequest) -> Dict[str, Dict[str, Any]]:
    """Schema-A-Basis-Payloads fuer gehaertete Skripte (entry_long,
    entry_short, exit, close). idempotency_key wird je Aufruf eindeutig
    vergeben (seq hochzaehlbar)."""
    tp = request.take_profit if request.take_profit else (
        request.tp3 if request.tp3 else 0.0)
    extra: Dict[str, Any] = {}
    if request.tp1 and request.tp2 and request.tp3:
        extra = {
            "tp1": {"price": round(float(request.tp1), 10), "qty_pct": TP1_QTY_PCT},
            "tp2": {"price": round(float(request.tp2), 10), "qty_pct": TP2_QTY_PCT},
            "tp3": {"price": round(float(request.tp3), 10), "qty_pct": TP3_QTY_PCT},
            "runner_qty_pct": RUNNER_QTY_PCT,
            "fee_covered_be_offset": FEE_COVERED_BE_OFFSET,
        }
    def _base(action: str) -> Dict[str, Any]:
        return build_schema_a_payload(
            action=action, ticker=request.symbol, price=request.entry,
            stop_loss=request.stop_loss, take_profit=tp if tp else None,
            fixed_leverage=request.fixed_leverage, strategy_id=request.strategy_id,
            secret=request.webhook_secret, seq=_SEQ_BY_ACTION.get(action, 6),
            extra=extra)
    close_p = _base("CLOSE")
    return {
        "entry_long": _base("BUY"),
        "entry_short": _base("SELL"),
        "exit": close_p,
        "close": close_p,
    }


def _as_provision(request: PineHardeningRequest) -> ProvisionRequest:
    return ProvisionRequest(
        symbol=request.symbol, strategy_id=request.strategy_id,
        side=request.side, entry=request.entry, stop_loss=request.stop_loss,
        take_profit=request.take_profit, fixed_leverage=request.fixed_leverage,
        webhook_secret=request.webhook_secret, ttl_minutes=request.ttl_minutes,
        tp1=request.tp1, tp2=request.tp2, tp3=request.tp3)


__all__ = [
    "FEE_COVERED_BE_OFFSET",
    "HardenedPineResult",
    "PineHardeningRequest",
    "ProvisionRequest",
    "RUNNER_QTY_PCT",
    "TP1_QTY_PCT",
    "TP2_QTY_PCT",
    "TP3_QTY_PCT",
    "build_schema_a_payload",
    "de_provision_hint",
    "generate_dynamic_pine",
    "harden_pine_code",
    "idempotency_key",
]
