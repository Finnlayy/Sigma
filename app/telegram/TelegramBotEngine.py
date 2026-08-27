"""
=========================================================
Datei:      app/telegram/TelegramBotEngine.py (v1.6.2)
Zweck:      Telegram Bot & WebApp Challenge Buttons
Knoten:     Jaune (Carrera-Engine)
=========================================================
[MOCK-SEAM] Ohne TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID im Env wird der Bot
no-op (nur Log). Mit Token werden Alerts über die Bot-API POST-vertreten
(https://api.telegram.org/bot<TOKEN>/sendMessage) — im Sandbox ohne
Telegram-Zugriff automatisch deaktiviert.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("app.telegram.bot_engine")


class TelegramBotEngine:
    def __init__(self, config=None):
        from app.core.config import load_config

        self.config = config or load_config()
        self.sent: list = []

    @property
    def enabled(self) -> bool:
        return bool(self.config.telegram_bot_token and self.config.telegram_chat_id)

    async def send_alert(self, text: str, category: str = "ALERT") -> bool:
        if not self.enabled:
            logger.debug("[MOCK] Telegram no-op: %s | %s", category, text)
            return False
        import httpx

        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": f"[{category}] {text}",
                    "parse_mode": "HTML",
                })
            ok = res.status_code == 200
            self.sent.append({"text": text, "category": category, "ok": ok})
            return ok
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    async def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Event-Bridge: Quarantäne, Vault-Sweep, Liquidation, EOD-Gate."""
        if event_type == "QUARANTINE":
            msg = (f"🚨 M8 QUARANTINE: {payload.get('instance_id')} "
                   f"({payload.get('reason')})")
        elif event_type == "VAULT_SWEEP":
            msg = (f"💰 Vault-Sweep: {payload.get('amount_usd', 0):.2f} USD "
                   f"von {payload.get('strategy_id')}")
        elif event_type == "LIQUIDATION":
            msg = (f"💀 PAPER-LIQUIDATION: {payload.get('symbol')} "
                   f"({payload.get('strategy_id')})")
        elif event_type == "EOD":
            msg = (f"🌙 EOD {payload.get('day')}: "
                   f"{payload.get('results', 0)} Strategien abgerechnet")
        else:
            msg = f"[{event_type}] {json.dumps(payload)[:300]}"
        await self.send_alert(msg, category=event_type)
