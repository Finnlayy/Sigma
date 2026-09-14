"""
=========================================================
Datei:      app/execution/VaultEngine.py
Zweck:      USD-Vault mit 100%-Profit-Sweep & DuckDB vault_ledger (v1.2.0 §2)
Knoten:     Jaune (Carrera-Engine)
=========================================================
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.execution.vault_engine")


class VaultEngine:
    """Dauert Vault in DuckDB (vault_ledger), Mirror-Balance in Redis."""

    def __init__(self, store, redis_client=None):
        self.store = store
        self.redis = redis_client

    def credit_sweep(self, strategy_id: str, amount_usd: float,
                     trade_id: Optional[str] = None, reason: str = "PROFIT_SWEEP_V120") -> Dict[str, Any]:
        entry_id = f"vault_{uuid.uuid4().hex[:12]}"
        balance_after = self.store.vault_balance() + float(amount_usd)
        self.store.vault_credit(entry_id, strategy_id, reason, amount_usd, balance_after)
        if self.redis:
            self.redis.hincrbyfloat("vault:balance", float(amount_usd))
            self.redis.hset("vault:last_sweep", mapping={
                "entry_id": entry_id,
                "strategy_id": strategy_id,
                "amount_usd": f"{float(amount_usd):.4f}",
                "ts": str(time.time()),
            })
        return {
            "entry_id": entry_id,
            "strategy_id": strategy_id,
            "type": reason,
            "amount_usd": float(amount_usd),
            "balance_snapshot": balance_after,
        }

    def balance(self) -> float:
        try:
            return self.store.vault_balance()
        except Exception:
            return 0.0

    def last_sweep(self) -> Optional[Dict[str, Any]]:
        entries = self.store.vault_entries(limit=1)
        return entries[0] if entries else None

    def entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.vault_entries(limit)
