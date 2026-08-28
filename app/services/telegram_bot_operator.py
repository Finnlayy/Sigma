"""
=========================================================
Datei:      app/services/telegram_bot_operator.py
Zweck:      §21 / Masterprompt §4.B — 24/7 Mobile Operator.
            Whitelist, Fast-Path (<50 ms, ohne LLM): /status /pause
            /resume /kill; Freitext -> lokales Ollama-LLM; Push bei
            Fills und Quarantäne.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Kommunikation) / Noir (Whitelist)
=========================================================
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.services.telegram")


@dataclass
class TelegramMessage:
    direction: str          # IN | OUT | PUSH
    chat_id: str
    text: str
    latency_ms: float = 0.0
    fast_path: bool = False
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class TelegramBotOperator:
    """Kein offener Bot: nur die eigene Chat-ID darf etwas auslösen."""

    def __init__(self, config: Optional[SigmaConfig] = None, safety_guard=None,
                 telemetry=None, virtual_bots=None, llm_client=None, sender=None):
        self.config = config or load_config()
        self.safety = safety_guard
        self.telemetry = telemetry
        self.virtual_bots = virtual_bots
        self.llm = llm_client                 # callable(prompt) -> str
        self._sender: Optional[Callable[[str, str], None]] = sender
        self.log: List[TelegramMessage] = []

    # ----------------------------------------------------------- whitelist
    @property
    def whitelist(self) -> List[str]:
        raw = (self.config.telegram_chat_id or "").strip()
        return [c.strip() for c in raw.split(",") if c.strip()]

    def is_authorized(self, chat_id: Any) -> bool:
        wl = self.whitelist
        return bool(wl) and str(chat_id) in wl

    # ------------------------------------------------------------- handling
    def handle(self, chat_id: Any, text: str) -> Dict[str, Any]:
        started = time.perf_counter()
        chat = str(chat_id)
        self._record("IN", chat, text)
        if not self.is_authorized(chat):
            logger.warning("telegram message from unauthorized chat %s dropped", chat)
            return self._reply(chat, "unauthorized", started, fast_path=True, authorized=False)

        command = text.strip().split()[0].lower() if text.strip() else ""
        if command in bp.TELEGRAM_FAST_PATH_COMMANDS:
            answer = self._fast_path(command)
            return self._reply(chat, answer, started, fast_path=True)

        answer = self._llm_answer(text)
        return self._reply(chat, answer, started, fast_path=False)

    def _fast_path(self, command: str) -> str:
        """Ohne LLM, ohne Netzwerk — Budget < 50 ms."""
        if command == "/status":
            return self._status_text()
        if command == "/pause":
            if self.safety:
                self.safety.engage_pause("telegram")
            return "PAUSE engaged — no new entries."
        if command == "/resume":
            if self.safety:
                self.safety.release_pause()
            return "PAUSE released — runners active again."
        if command == "/kill":
            if self.safety:
                self.safety.engage_kill_switch("telegram")
            if self.telemetry:
                try:
                    self.telemetry.set_state("EMERGENCY_HALT", reason="telegram_kill")
                except Exception:  # pragma: no cover
                    pass
            return "KILL_SWITCH engaged — alerts off, cancel_all issued."
        return "unknown command"

    def _status_text(self) -> str:
        parts = [f"Sigma L4 · blueprint {bp.BLUEPRINT_VERSION}"]
        if self.safety:
            s = self.safety.snapshot()
            parts.append(f"kill={s['kill_switch']} pause={s['pause']} "
                         f"pnl={s['daily_pnl_usd']:.2f}/{-s['max_daily_loss_usd']:.0f} "
                         f"errors={s['consecutive_errors']}")
        if self.virtual_bots:
            snap = self.virtual_bots.snapshot()
            parts.append(f"bots={len(snap['bots'])} equity={snap['total_equity_eur']:.2f} EUR")
        return " | ".join(parts)

    def _llm_answer(self, text: str) -> str:
        if self.llm is None:
            return (f"LLM offline ({self.config.ollama_url}). "
                    f"Fast-path commands: {', '.join(bp.TELEGRAM_FAST_PATH_COMMANDS)}")
        try:
            return str(self.llm(text))
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return f"LLM error: {exc}"

    # ----------------------------------------------------------------- push
    def push(self, text: str) -> Dict[str, Any]:
        """Proaktive Meldung (Fill, Quarantäne, Deadman)."""
        results = []
        for chat in self.whitelist:
            self._record("PUSH", chat, text)
            self._send(chat, text)
            results.append(chat)
        return {"sent_to": results, "text": text}

    def notify_fill(self, strategy_id: str, side: str, symbol: str,
                    qty: float, price: float, mode: str) -> Dict[str, Any]:
        return self.push(f"FILL [{mode}] {side.upper()} {qty:.6f} {symbol} @ {price:.2f} ({strategy_id})")

    def notify_quarantine(self, strategy_id: str, reason: str) -> Dict[str, Any]:
        return self.push(f"QUARANTINE {strategy_id} — {reason}. Alert disabled.")

    # ------------------------------------------------------------ internals
    def _reply(self, chat: str, text: str, started: float, *, fast_path: bool,
               authorized: bool = True) -> Dict[str, Any]:
        latency_ms = (time.perf_counter() - started) * 1000.0
        self._record("OUT", chat, text, latency_ms, fast_path)
        if authorized:
            self._send(chat, text)
        return {
            "chat_id": chat, "text": text, "fast_path": fast_path,
            "authorized": authorized, "latency_ms": round(latency_ms, 3),
            "within_budget": latency_ms <= bp.TELEGRAM_FAST_PATH_BUDGET_MS if fast_path else None,
        }

    def _record(self, direction: str, chat: str, text: str,
                latency_ms: float = 0.0, fast_path: bool = False) -> None:
        self.log.append(TelegramMessage(direction, chat, text, latency_ms, fast_path))
        self.log = self.log[-200:]

    def _send(self, chat: str, text: str) -> None:
        if self._sender is not None:
            try:
                self._sender(chat, text)
            except Exception as exc:  # pragma: no cover
                logger.error("telegram send failed: %s", exc)
            return
        if not self.config.telegram_bot_token:
            return
        try:  # pragma: no cover - Netzwerkpfad
            import httpx

            httpx.post(
                f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                json={"chat_id": chat, "text": text}, timeout=10)
        except Exception as exc:
            logger.error("telegram API send failed: %s", exc)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.config.telegram_bot_token),
            "whitelist": self.whitelist,
            "fast_path_commands": list(bp.TELEGRAM_FAST_PATH_COMMANDS),
            "fast_path_budget_ms": bp.TELEGRAM_FAST_PATH_BUDGET_MS,
            "llm_url": self.config.ollama_url,
            "log": [m.to_dict() for m in self.log[-20:]],
        }


_operator: Optional[TelegramBotOperator] = None


def get_telegram_operator(**kwargs) -> TelegramBotOperator:
    global _operator
    if _operator is None:
        _operator = TelegramBotOperator(**kwargs)
    return _operator
