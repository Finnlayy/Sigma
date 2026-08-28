"""
=========================================================
Datei:      app/core/error_engine.py
Zweck:      §36 / Axiom 14 — Unified Error Taxonomy & Diagnostics Desk
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================

Kein blindes ``500 Internal Server Error``: jeder Fehler wird zu einem
strukturierten ``ErrorDetail`` (Code, Kategorie, Subsystem, remediation_hint,
technical_context, trace_id) normalisiert, nach ``./data/logs/errors.jsonl``
persistiert und bei ``HIGH``/``CRITICAL`` per Telegram gepusht (§36.4).

Codebereiche (§36.2):

    E1000 AUTHENTICATION · E2000 TRADINGVIEW · E3000 KRAKEN
    E4000 RISK_GUARD     · E5000 SYSTEM
"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core import blueprint as bp

logger = logging.getLogger("app.core.error_engine")

UNKNOWN_CODE = "ERR_SYS_UNHANDLED_EXCEPTION"

#: Abweichungen vom Default ``MEDIUM`` (§36.4 — Telegram ab HIGH).
SEVERITY_OVERRIDES: Dict[str, str] = {
    "ERR_AUTH_INVALID_SECRET": "HIGH",
    "ERR_AUTH_TV_SESSION_EXPIRED": "HIGH",
    "ERR_AUTH_WHITELIST_BLOCKED": "MEDIUM",
    "ERR_TV_ALERT_QUOTA_EXCEEDED": "HIGH",
    "ERR_TV_CSV_HEADER_MISMATCH": "HIGH",
    "ERR_KRAKEN_INSUFFICIENT_FUNDS": "HIGH",
    "ERR_KRAKEN_DEADMAN_TIMEOUT": "CRITICAL",
    "ERR_KRAKEN_CLI_NOT_FOUND": "CRITICAL",
    "ERR_RISK_MAX_DAILY_LOSS": "CRITICAL",
    "ERR_RISK_KILL_SWITCH_ACTIVE": "CRITICAL",
    "ERR_ORDERBOOK_LIQUIDITY_TRAP": "LOW",
    "ERR_CONTAGION_VETO_R0": "LOW",
    "ERR_STALE_SIGNAL_REJECT": "LOW",
    "ERR_SYS_RAM_SOFT_CAP": "HIGH",
    "ERR_SYS_DUCKDB_LOCK": "HIGH",
    "ERR_SYS_OLLAMA_OFFLINE": "MEDIUM",
    "ERR_SYS_UNHANDLED_EXCEPTION": "CRITICAL",
}

#: HTTP-Status je Codebereich — der Handler antwortet nie mit nacktem 500.
HTTP_STATUS_BY_RANGE: Dict[str, int] = {
    "E1000": 401, "E2000": 502, "E3000": 502, "E4000": 409, "E5000": 500,
}


def category_for(code: str) -> str:
    rng = bp.ERROR_CATALOG.get(code, ("E5000", "", ""))[0]
    return bp.ERROR_CATEGORIES.get(rng, "SYSTEM")


def severity_for(code: str) -> str:
    return SEVERITY_OVERRIDES.get(code, "MEDIUM")


def http_status_for(code: str) -> int:
    rng = bp.ERROR_CATALOG.get(code, ("E5000", "", ""))[0]
    return HTTP_STATUS_BY_RANGE.get(rng, 500)


# =============================================================================
# 36.1 ErrorDetail
# =============================================================================

class ErrorDetail(BaseModel):
    error_code: str
    category: str
    message: str
    subsystem: str
    remediation_hint: str
    technical_context: Dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None
    timestamp: int = 0
    severity: str = "MEDIUM"
    error_range: str = "E5000"

    def as_response(self) -> Dict[str, Any]:
        return {"error": self.model_dump()}


# =============================================================================
# Exception-Hierarchie (§36.3)
# =============================================================================

class SigmaBaseException(Exception):
    """Basis aller Sigma-Fehler — traegt immer einen Katalog-Code."""

    error_code: str = UNKNOWN_CODE

    def __init__(self, message: str = "", *, context: Optional[Dict[str, Any]] = None,
                 error_code: str = "", trace_id: str = "") -> None:
        if error_code:
            self.error_code = error_code
        super().__init__(message or self.error_code)
        self.message = message or self.error_code
        self.context: Dict[str, Any] = dict(context or {})
        self.trace_id = trace_id or f"trc_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------ ---
    @property
    def range(self) -> str:
        return bp.ERROR_CATALOG.get(self.error_code, ("E5000", "", ""))[0]

    @property
    def subsystem(self) -> str:
        return bp.ERROR_CATALOG.get(self.error_code, ("", "sigma-core", ""))[1]

    @property
    def remediation_hint(self) -> str:
        return bp.ERROR_CATALOG.get(
            self.error_code, ("", "", "errors.jsonl + Stacktrace pruefen"))[2]

    @property
    def severity(self) -> str:
        return severity_for(self.error_code)

    @property
    def http_status(self) -> int:
        return http_status_for(self.error_code)

    def to_detail(self) -> ErrorDetail:
        return ErrorDetail(
            error_code=self.error_code,
            category=category_for(self.error_code),
            message=self.message,
            subsystem=self.subsystem,
            remediation_hint=self.remediation_hint,
            technical_context=self.context,
            trace_id=self.trace_id,
            timestamp=int(time.time() * 1000),
            severity=self.severity,
            error_range=self.range,
        )


def _exc(name: str, code: str) -> type:
    return type(name, (SigmaBaseException,), {"error_code": code, "__doc__":
                                              f"§36 — {code}"})


# E1000 Auth & Security
InvalidWebhookSecretException = _exc("InvalidWebhookSecretException",
                                     "ERR_AUTH_INVALID_SECRET")
TradingViewSessionExpiredException = _exc("TradingViewSessionExpiredException",
                                          "ERR_AUTH_TV_SESSION_EXPIRED")
WhitelistBlockedException = _exc("WhitelistBlockedException",
                                 "ERR_AUTH_WHITELIST_BLOCKED")
# E2000 TradingView & Playwright
DOMSelectorNotFoundException = _exc("DOMSelectorNotFoundException",
                                    "ERR_TV_SELECTOR_NOT_FOUND")
PineCompilationException = _exc("PineCompilationException",
                                "ERR_TV_PINE_COMPILE_ERROR")
AlertQuotaExceededException = _exc("AlertQuotaExceededException",
                                   "ERR_TV_ALERT_QUOTA_EXCEEDED")
TvExportTimeoutException = _exc("TvExportTimeoutException", "ERR_TV_EXPORT_TIMEOUT")
CsvHeaderMismatchException = _exc("CsvHeaderMismatchException",
                                  "ERR_TV_CSV_HEADER_MISMATCH")
# E3000 Kraken & Execution
KrakenInsufficientFundsException = _exc("KrakenInsufficientFundsException",
                                        "ERR_KRAKEN_INSUFFICIENT_FUNDS")
KrakenRateLimitException = _exc("KrakenRateLimitException",
                                "ERR_KRAKEN_RATE_LIMIT_429")
KrakenDeadmanTimeoutException = _exc("KrakenDeadmanTimeoutException",
                                     "ERR_KRAKEN_DEADMAN_TIMEOUT")
KrakenCliNotFoundException = _exc("KrakenCliNotFoundException",
                                  "ERR_KRAKEN_CLI_NOT_FOUND")
# E4000 Quant, Risiko & Markt
MaxDailyLossException = _exc("MaxDailyLossException", "ERR_RISK_MAX_DAILY_LOSS")
KillSwitchActiveException = _exc("KillSwitchActiveException",
                                 "ERR_RISK_KILL_SWITCH_ACTIVE")
LiquidityTrapOrderbookException = _exc("LiquidityTrapOrderbookException",
                                       "ERR_ORDERBOOK_LIQUIDITY_TRAP")
ContagionVetoException = _exc("ContagionVetoException", "ERR_CONTAGION_VETO_R0")
StaleSignalException = _exc("StaleSignalException", "ERR_STALE_SIGNAL_REJECT")
# E5000 System & Ressourcen
RamSoftCapException = _exc("RamSoftCapException", "ERR_SYS_RAM_SOFT_CAP")
DuckDbLockException = _exc("DuckDbLockException", "ERR_SYS_DUCKDB_LOCK")
OllamaOfflineException = _exc("OllamaOfflineException", "ERR_SYS_OLLAMA_OFFLINE")
UnhandledSigmaException = _exc("UnhandledSigmaException", UNKNOWN_CODE)

EXCEPTION_BY_CODE: Dict[str, type] = {
    cls.error_code: cls for cls in (
        InvalidWebhookSecretException, TradingViewSessionExpiredException,
        WhitelistBlockedException, DOMSelectorNotFoundException,
        PineCompilationException, AlertQuotaExceededException,
        TvExportTimeoutException, CsvHeaderMismatchException,
        KrakenInsufficientFundsException, KrakenRateLimitException,
        KrakenDeadmanTimeoutException, KrakenCliNotFoundException,
        MaxDailyLossException, KillSwitchActiveException,
        LiquidityTrapOrderbookException, ContagionVetoException,
        StaleSignalException, RamSoftCapException, DuckDbLockException,
        OllamaOfflineException, UnhandledSigmaException,
    )
}


# =============================================================================
# Diagnostics Engine
# =============================================================================

class ErrorEngine:
    """Normalisiert, persistiert und eskaliert Fehler (§36)."""

    def __init__(self, log_path: str = bp.ERROR_LOG_PATH, notifier=None,
                 max_buffer: int = 500) -> None:
        self.log_path = log_path
        self.notifier = notifier
        self._buffer: Deque[ErrorDetail] = deque(maxlen=max_buffer)
        self._counts: Dict[str, int] = {}

    # ---------------------------------------------------------- normalise --
    def normalize(self, exc: BaseException, *, subsystem: str = "",
                  context: Optional[Dict[str, Any]] = None,
                  trace_id: str = "") -> ErrorDetail:
        if isinstance(exc, SigmaBaseException):
            detail = exc.to_detail()
            if context:
                detail.technical_context = {**detail.technical_context, **context}
        else:
            detail = ErrorDetail(
                error_code=UNKNOWN_CODE,
                category=category_for(UNKNOWN_CODE),
                message=f"{type(exc).__name__}: {exc}",
                subsystem=bp.ERROR_CATALOG[UNKNOWN_CODE][1],
                remediation_hint=bp.ERROR_CATALOG[UNKNOWN_CODE][2],
                technical_context={
                    "exception": type(exc).__name__,
                    "stacktrace": "".join(traceback.format_exception(
                        type(exc), exc, exc.__traceback__))[-2000:],
                    **(context or {}),
                },
                trace_id=trace_id or f"trc_{uuid.uuid4().hex[:12]}",
                timestamp=int(time.time() * 1000),
                severity=severity_for(UNKNOWN_CODE),
                error_range="E5000",
            )
        if subsystem:
            detail.subsystem = subsystem
        if trace_id:
            detail.trace_id = trace_id
        return detail

    # ------------------------------------------------------------ record --
    def record(self, exc: BaseException, *, subsystem: str = "",
               context: Optional[Dict[str, Any]] = None,
               trace_id: str = "") -> ErrorDetail:
        detail = self.normalize(exc, subsystem=subsystem, context=context,
                                trace_id=trace_id)
        self.capture(detail)
        return detail

    def capture(self, detail: ErrorDetail) -> ErrorDetail:
        self._buffer.append(detail)
        self._counts[detail.error_code] = self._counts.get(detail.error_code, 0) + 1
        self._persist(detail)
        if detail.severity in bp.ERROR_TELEGRAM_PUSH_SEVERITIES:
            self._push(detail)
        logger.log(logging.ERROR if detail.severity in ("HIGH", "CRITICAL")
                   else logging.WARNING,
                   "[%s] %s (%s) — %s", detail.severity, detail.error_code,
                   detail.subsystem, detail.message)
        return detail

    def _persist(self, detail: ErrorDetail) -> None:
        try:
            directory = os.path.dirname(self.log_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(detail.model_dump(), ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover - Logging darf nie werfen
            logger.warning("errors.jsonl nicht schreibbar: %s", exc)

    def _push(self, detail: ErrorDetail) -> None:
        if self.notifier is None:
            return
        text = self.telegram_text(detail)
        try:
            result = self.notifier.send_alert(text, category="ERROR")
            if hasattr(result, "__await__"):
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)
        except Exception as exc:  # pragma: no cover
            logger.warning("Telegram-Error-Push fehlgeschlagen: %s", exc)

    @staticmethod
    def telegram_text(detail: ErrorDetail) -> str:
        return (f"🛑 SIGMA {detail.severity} — {detail.error_code}\n"
                f"Subsystem: {detail.subsystem} ({detail.category})\n"
                f"Auswirkung: {detail.message}\n"
                f"Lösung: {detail.remediation_hint}\n"
                f"trace_id: {detail.trace_id}")

    # -------------------------------------------------------- telemetrie --
    def recent(self, limit: int = 50, *, severity: str = "",
               category: str = "") -> List[Dict[str, Any]]:
        rows = list(self._buffer)
        if severity:
            rows = [r for r in rows if r.severity == severity.upper()]
        if category:
            rows = [r for r in rows if r.category == category.upper()]
        return [r.model_dump() for r in rows[-limit:][::-1]]

    def counts(self) -> Dict[str, int]:
        return dict(self._counts)

    def clear(self) -> None:
        self._buffer.clear()
        self._counts.clear()

    def export_jsonl(self) -> str:
        return "\n".join(json.dumps(r.model_dump(), ensure_ascii=False)
                         for r in self._buffer)

    def catalog(self) -> List[Dict[str, Any]]:
        return [{
            "error_code": code,
            "error_range": rng,
            "category": bp.ERROR_CATEGORIES.get(rng, "SYSTEM"),
            "subsystem": subsystem,
            "remediation_hint": hint,
            "severity": severity_for(code),
            "http_status": HTTP_STATUS_BY_RANGE.get(rng, 500),
        } for code, (rng, subsystem, hint) in bp.ERROR_CATALOG.items()]

    def self_test(self) -> Dict[str, Any]:
        """§36.4 — Diagnose-Selbsttest fuer das Error-Desk."""
        checks = []
        try:
            self._persist(ErrorDetail(
                error_code="ERR_SYS_UNHANDLED_EXCEPTION", category="SYSTEM",
                message="diagnostics self-test", subsystem="sigma-core",
                remediation_hint="kein Eingriff — Selbsttest",
                technical_context={"self_test": True},
                trace_id="trc_selftest", timestamp=int(time.time() * 1000),
                severity="LOW", error_range="E5000"))
            checks.append({"check": "errors_jsonl_writable", "ok":
                           os.path.exists(self.log_path)})
        except Exception as exc:  # pragma: no cover
            checks.append({"check": "errors_jsonl_writable", "ok": False,
                           "detail": str(exc)})
        checks.append({"check": "catalog_complete",
                       "ok": len(bp.ERROR_CATALOG) == len(EXCEPTION_BY_CODE)})
        checks.append({"check": "telegram_notifier",
                       "ok": self.notifier is not None})
        return {"ok": all(c["ok"] for c in checks if c["check"] != "telegram_notifier"),
                "checks": checks, "log_path": self.log_path,
                "buffered_errors": len(self._buffer)}

    def panel_state(self, limit: int = 50) -> Dict[str, Any]:
        return {
            "log_path": self.log_path,
            "severities": list(bp.ERROR_SEVERITIES),
            "categories": dict(bp.ERROR_CATEGORIES),
            "push_severities": list(bp.ERROR_TELEGRAM_PUSH_SEVERITIES),
            "counts": self.counts(),
            "errors": self.recent(limit),
        }


_ENGINE: Optional[ErrorEngine] = None


def get_error_engine(**kwargs: Any) -> ErrorEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ErrorEngine(**kwargs)
    return _ENGINE


def set_error_engine(engine: Optional[ErrorEngine]) -> None:
    global _ENGINE
    _ENGINE = engine


def raise_for_code(code: str, message: str = "", **context: Any) -> None:
    """Katalog-Code -> passende Exception werfen (Convenience fuer Call-Sites)."""
    cls = EXCEPTION_BY_CODE.get(code, UnhandledSigmaException)
    raise cls(message or code, context=context or None)


# =============================================================================
# FastAPI-Anbindung (§36.1 — globaler Handler)
# =============================================================================

def install_error_handlers(app, *, engine: Optional[ErrorEngine] = None) -> None:
    """Registriert ``sigma_global_exception_handler`` — kein Server-Crash."""
    from fastapi.responses import JSONResponse

    eng = engine or get_error_engine()

    async def sigma_global_exception_handler(request, exc: SigmaBaseException):
        detail = eng.record(exc, context={"path": str(getattr(request, "url", ""))})
        return JSONResponse(status_code=detail_status(detail),
                            content=detail.as_response())

    async def unhandled_exception_handler(request, exc: Exception):
        detail = eng.record(exc, context={"path": str(getattr(request, "url", ""))})
        return JSONResponse(status_code=500, content=detail.as_response())

    app.add_exception_handler(SigmaBaseException, sigma_global_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


def detail_status(detail: ErrorDetail) -> int:
    return HTTP_STATUS_BY_RANGE.get(detail.error_range, 500)
