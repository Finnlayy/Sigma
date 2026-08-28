"""
=========================================================
Datei:      app/execution/capital_flywheel_engine.py
Zweck:      §28 / Axiom 7 — 50/50 Flywheel Kapitalarchitektur
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================

* **Einzahlung:** 100 % -> Kraken-Futures-Arbeitskonto -> Bot-Budgets
* **Realisierter Gewinn:** ab ``min_split_trigger_eur`` (10 EUR)
  50 % Reinvest ins Bot-Budget, 50 % in den Spot-Tresor (Default ``XBT``)
* **Einbahnstrasse:** Spot -> Futures niemals automatisch

Alle Bewegungen landen im DuckDB-Ledger ``flywheel_ledger``; ohne Store
haelt die Engine den Ledger nur im Speicher (Tests / Dry-Run).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.execution.capital_flywheel_engine")


class FlywheelViolation(RuntimeError):
    """Verstoss gegen die Einbahnstrassen-Regel (§28)."""


@dataclass
class LedgerEntry:
    entry_id: str
    ts: float
    kind: str                # deposit | profit_split | manual_vault_transfer | bot_allocation
    amount_eur: float
    futures_delta_eur: float
    vault_delta_eur: float
    asset: str = bp.FLYWHEEL_VAULT_QUOTE
    strategy_id: str = ""
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FlywheelState:
    futures_balance_eur: float = 0.0
    vault_balance_eur: float = 0.0
    pending_profit_eur: float = 0.0
    allocated_eur: float = 0.0
    entries: List[LedgerEntry] = field(default_factory=list)


class CapitalFlywheelEngine:
    """Kanonische Budgetverwaltung (§28)."""

    def __init__(
        self,
        store: Any = None,
        *,
        vault_asset: str = bp.FLYWHEEL_DEFAULT_VAULT_ASSET,
        min_split_trigger_eur: float = bp.FLYWHEEL_MIN_SPLIT_TRIGGER_EUR,
        reinvest_pct: float = bp.FLYWHEEL_PROFIT_REINVEST_PCT,
        clock=time.time,
    ) -> None:
        self.store = store
        self.vault_asset = vault_asset
        self.min_split_trigger_eur = min_split_trigger_eur
        self.reinvest_pct = reinvest_pct
        self.vault_pct = 1.0 - reinvest_pct
        self._clock = clock
        self.state = FlywheelState()

    # ------------------------------------------------------------ ledger ---
    def _write(self, kind: str, amount: float, futures_delta: float,
               vault_delta: float, *, strategy_id: str = "", note: str = "",
               asset: Optional[str] = None) -> LedgerEntry:
        entry = LedgerEntry(
            entry_id=uuid.uuid4().hex[:12], ts=self._clock(), kind=kind,
            amount_eur=round(amount, 8), futures_delta_eur=round(futures_delta, 8),
            vault_delta_eur=round(vault_delta, 8),
            asset=asset or self.vault_asset, strategy_id=strategy_id, note=note,
        )
        self.state.futures_balance_eur += futures_delta
        self.state.vault_balance_eur += vault_delta
        self.state.entries.append(entry)
        del self.state.entries[:-1000]
        self._persist(entry)
        return entry

    def _persist(self, entry: LedgerEntry) -> None:
        if self.store is None:
            return
        writer = getattr(self.store, "insert_row", None) or getattr(self.store, "append_row", None)
        if writer is None:
            return
        try:
            writer(bp.FLYWHEEL_LEDGER_TABLE, entry.as_dict())
        except Exception as exc:  # pragma: no cover - DuckDB darf nie blocken
            logger.warning("flywheel_ledger persist failed: %s", exc)

    # ----------------------------------------------------------- flows  ---
    def deposit(self, amount_eur: float, note: str = "operator deposit") -> LedgerEntry:
        """100 % einer Einzahlung geht ins Futures-Arbeitskonto (§28)."""
        if amount_eur <= 0:
            raise ValueError("deposit muss > 0 sein")
        return self._write("deposit", amount_eur,
                           futures_delta=amount_eur * bp.FLYWHEEL_DEPOSIT_TO_FUTURES_PCT,
                           vault_delta=0.0, note=note)

    def register_realized_profit(self, amount_eur: float, *, strategy_id: str = "",
                                 ) -> Dict[str, Any]:
        """Realisierten Gewinn buchen; splittet erst ab Trigger-Schwelle."""
        if amount_eur <= 0:
            # Verluste mindern direkt das Arbeitskonto, kein Vault-Effekt.
            self._write("realized_loss", amount_eur, futures_delta=amount_eur,
                        vault_delta=0.0, strategy_id=strategy_id, note="realized loss")
            return {"split": False, "reason": "loss", "pending_eur": self.state.pending_profit_eur}

        self.state.pending_profit_eur += amount_eur
        self._write("realized_profit", amount_eur, futures_delta=amount_eur,
                    vault_delta=0.0, strategy_id=strategy_id, note="realized profit")
        if self.state.pending_profit_eur < self.min_split_trigger_eur:
            return {
                "split": False,
                "reason": f"unter min_split_trigger_eur ({self.min_split_trigger_eur})",
                "pending_eur": round(self.state.pending_profit_eur, 6),
            }
        return self.sweep(strategy_id=strategy_id)

    def sweep(self, *, strategy_id: str = "") -> Dict[str, Any]:
        """Tier-4-Flywheel-Sweep: 50 % Reinvest / 50 % Spot-Tresor."""
        pending = self.state.pending_profit_eur
        if pending < self.min_split_trigger_eur:
            return {"split": False, "reason": "nichts zu sweepen",
                    "pending_eur": round(pending, 6)}
        vault_share = pending * self.vault_pct
        reinvest_share = pending - vault_share
        self.state.pending_profit_eur = 0.0
        entry = self._write(
            "profit_split", pending,
            futures_delta=-vault_share,  # Betrag wandert vom Futures- in den Spot-Topf
            vault_delta=vault_share, strategy_id=strategy_id,
            note=f"50/50 split -> vault {self.vault_asset}",
        )
        return {
            "split": True,
            "total_eur": round(pending, 6),
            "reinvest_eur": round(reinvest_share, 6),
            "vault_eur": round(vault_share, 6),
            "vault_asset": self.vault_asset,
            "entry_id": entry.entry_id,
        }

    def allocate_bot_budget(self, strategy_id: str, amount_eur: float) -> Dict[str, Any]:
        """Reserviert isoliertes Bot-Budget aus dem freien Futures-Kapital (§31.4)."""
        free = self.free_futures_eur
        if amount_eur <= 0:
            raise ValueError("budget muss > 0 sein")
        if amount_eur > free:
            return {"reserved": False, "reason": "INSUFFICIENT_FREE_FUTURES",
                    "free_eur": round(free, 6), "requested_eur": amount_eur}
        self.state.allocated_eur += amount_eur
        self._write("bot_allocation", amount_eur, futures_delta=0.0, vault_delta=0.0,
                    strategy_id=strategy_id, note="isoliertes Bot-Budget reserviert")
        return {"reserved": True, "strategy_id": strategy_id,
                "budget_eur": amount_eur, "free_eur": round(self.free_futures_eur, 6)}

    def release_bot_budget(self, strategy_id: str, amount_eur: float) -> None:
        self.state.allocated_eur = max(0.0, self.state.allocated_eur - amount_eur)
        self._write("bot_release", amount_eur, futures_delta=0.0, vault_delta=0.0,
                    strategy_id=strategy_id, note="Bot-Budget freigegeben")

    def transfer_vault_to_futures(self, amount_eur: float, *,
                                  operator_confirmed: bool = False) -> LedgerEntry:
        """Einbahnstrasse: nur mit expliziter Operator-Bestaetigung (§28)."""
        if not operator_confirmed:
            raise FlywheelViolation(
                "Spot -> Futures ist nie automatisch; operator_confirmed=True noetig"
            )
        if amount_eur > self.state.vault_balance_eur:
            raise ValueError("Vault-Deckung unzureichend")
        return self._write("manual_vault_transfer", amount_eur,
                           futures_delta=amount_eur, vault_delta=-amount_eur,
                           note="manueller Operator-Transfer")

    # ------------------------------------------------------------ state ---
    @property
    def free_futures_eur(self) -> float:
        return max(0.0, self.state.futures_balance_eur - self.state.allocated_eur)

    def panel_state(self) -> Dict[str, Any]:
        total = self.state.futures_balance_eur + self.state.vault_balance_eur
        return {
            "futures_balance_eur": round(self.state.futures_balance_eur, 2),
            "vault_balance_eur": round(self.state.vault_balance_eur, 2),
            "allocated_eur": round(self.state.allocated_eur, 2),
            "free_futures_eur": round(self.free_futures_eur, 2),
            "pending_profit_eur": round(self.state.pending_profit_eur, 2),
            "total_equity_eur": round(total, 2),
            "vault_asset": self.vault_asset,
            "split": {"reinvest_pct": self.reinvest_pct, "vault_pct": self.vault_pct,
                      "min_split_trigger_eur": self.min_split_trigger_eur},
            "one_way": bp.FLYWHEEL_ONE_WAY,
            "ledger_table": bp.FLYWHEEL_LEDGER_TABLE,
            "recent_entries": [e.as_dict() for e in self.state.entries[-20:]],
        }
