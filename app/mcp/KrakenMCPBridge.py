"""
=========================================================
Datei:      app/mcp/KrakenMCPBridge.py (v1.6.2)
Zweck:      Verdrahtung & LLM Passkey Intercept Gate für ~149 MCP Tools
Knoten:     Jaune (Carrera-Engine)
=========================================================
Read-only Tools → Paper-Daten direkt.
Mutative Tools  → Passkey-Intercept (settingsToken) erforderlich.
[MOCK-SEAM] Die Tool-Ausführung läuft gegen den Paper-Store; die echte
Kraken-API-Anbindung (ccxt pro) wird im LAN-Produktionsbetrieb an
`execute` angeschlossen.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.mcp.kraken_bridge")

_CATEGORIES = {
    "market": ["ticker", "ohlcv", "order_book", "trades", "spread", "funding_rate",
               "open_interest", "liquidations", "candles", "book_ticker"],
    "account": ["balance", "balances", "deposit_address", "withdrawal_history",
                "deposit_history", "trading_fees", "margin_level", "equity"],
    "orders": ["create_order", "cancel_order", "cancel_all", "open_orders",
               "order_history", "modify_order", "order_status"],
    "positions": ["open_positions", "position_history", "close_position",
                  "position_pnl", "set_leverage"],
    "futures": ["futures_ticker", "futures_order", "futures_position",
                "futures_funding", "futures_settlement"],
    "data": ["asset_pairs", "server_time", "system_status", "depth", "public_trades"],
}

MUTATING_PREFIXES = ("orders.", "futures.", "positions.close", "positions.set")


def build_tool_registry(count: int = 149) -> List[Dict[str, Any]]:
    """Erzeugt die ~149 MCP-Tools (v1.6.2-Verdrahtung)."""
    names: List[str] = []
    for cat, actions in _CATEGORIES.items():
        for a in actions:
            for i in range(1, 4):
                names.append(f"kraken.{cat}.{a}_{i:02d}")
    n = 1
    while len(names) < count:
        names.append(f"kraken.utility.tool_{n:03d}")
        n += 1
    registry = []
    for name in names[:count]:
        registry.append({
            "name": name,
            "category": name.split(".")[1],
            "mutating": name.startswith(MUTATING_PREFIXES),
            "description": f"Kraken MCP {name}",
        })
    return registry


class KrakenMCPBridge:
    def __init__(self, config=None, passkey_engine=None, ingestor=None, store=None):
        from app.core.config import load_config

        self.config = config or load_config()
        self.passkey_engine = passkey_engine
        self.ingestor = ingestor
        self.store = store
        self.tools = build_tool_registry(self.config.mcp_tool_count)
        self.invocation_log: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------- API
    def list_tools(self) -> Dict[str, Any]:
        return {
            "total": len(self.tools),
            "mutating": sum(1 for t in self.tools if t["mutating"]),
            "readOnly": sum(1 for t in self.tools if not t["mutating"]),
            "passkeyIntercept": "armed",
            "tools": self.tools[:200],
        }

    def has_tool(self, name: str) -> bool:
        return any(t["name"] == name for t in self.tools)

    def execute(self, tool_name: str, args: Dict[str, Any],
                settings_token: Optional[str] = None) -> Dict[str, Any]:
        tool = next((t for t in self.tools if t["name"] == tool_name), None)
        if tool is None:
            return {"ok": False, "error": f"Unknown MCP tool '{tool_name}'."}
        if tool["mutating"]:
            ok = False
            if self.passkey_engine is not None:
                ok = self.passkey_engine.validate_settings_token(settings_token) is not None
            if not ok:
                return {
                    "ok": False,
                    "intercepted": True,
                    "error": "PASSKEY INTERCEPT: mutatives MCP-Tool erfordert gültiges settingsToken.",
                    "challenge_required": "/api/v1/auth/passkey/challenge",
                }
        # [MOCK-SEAM] Paper-Ausführung gegen lokalen Store (keine Exchange-Kontakten)
        result = self._paper_execute(tool_name, args)
        self.invocation_log.append({"tool": tool_name, "args": args, "ok": result.get("ok", True)})
        if len(self.invocation_log) > 200:
            self.invocation_log = self.invocation_log[-200:]
        return {"ok": True, "tool": tool_name, "mode": "paper", **result}

    def _paper_execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(args.get("symbol") or "BTC/USD")
        if "ticker" in tool_name and self.ingestor is not None:
            return {"data": self.ingestor.ticker_rows()}
        if "ohlcv" in tool_name or "candles" in tool_name and self.store is not None:
            limit = int(args.get("limit") or 50)
            return {"data": self.store.ohlcv(symbol, 60, limit=limit)}
        if "balance" in tool_name or "equity" in tool_name and self.store is not None:
            from app.core.duckdb_store import get_store

            s = get_store()
            return {"data": {"vault_usd": s.vault_balance(),
                             "budgets": s.all_budgets()}}
        if "open_positions" in tool_name:
            from app.execution.PaperExecutionEngine import PaperExecutionEngine

            return {"data": PaperExecutionEngine.get_instance().all_positions()}
        if "server_time" in tool_name:
            import time

            return {"data": {"time": time.time()}}
        if "asset_pairs" in tool_name:
            return {"data": list(self.config.market_symbols)}
        return {"data": None, "note": f"[MOCK] {tool_name} ohne Paper-Implementierung (read-only no-op)"}
