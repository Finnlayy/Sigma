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
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

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
    external_ref: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FlywheelState:
    futures_balance_eur: float = 0.0
    vault_balance_eur: float = 0.0
    pending_profit_eur: float = 0.0
    allocated_eur: float = 0.0
    reconciliation_required: bool = False
    pending_vault_operation_id: str = ""
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
        treasury_guard: Optional[Callable[[str], bool]] = None,
        vault_executor: Optional[Callable[[str, float], Dict[str, Any]]] = None,
    ) -> None:
        self.store = store
        self.vault_asset = vault_asset
        self.min_split_trigger_eur = min_split_trigger_eur
        self.reinvest_pct = reinvest_pct
        self.vault_pct = 1.0 - reinvest_pct
        self._clock = clock
        self._treasury_guard = treasury_guard
        self._vault_executor = vault_executor
        self._lock = threading.RLock()
        self.state = FlywheelState()
        self._hydrate()

    # ------------------------------------------------------------ ledger ---
    def _write(self, kind: str, amount: float, futures_delta: float,
               vault_delta: float, *, strategy_id: str = "", note: str = "",
               asset: Optional[str] = None,
               require_persist: bool = False, external_ref: str = "") -> LedgerEntry:
        with self._lock:
            entry = LedgerEntry(
                entry_id=uuid.uuid4().hex[:12], ts=self._clock(), kind=kind,
                amount_eur=round(amount, 8), futures_delta_eur=round(futures_delta, 8),
                vault_delta_eur=round(vault_delta, 8),
                asset=asset or self.vault_asset, strategy_id=strategy_id, note=note,
                external_ref=external_ref,
            )
            self.state.futures_balance_eur += futures_delta
            self.state.vault_balance_eur += vault_delta
            self.state.entries.append(entry)
            del self.state.entries[:-1000]
            persisted = self._persist(entry)
            if require_persist and not persisted:
                self.state.entries.pop()
                self.state.futures_balance_eur -= futures_delta
                self.state.vault_balance_eur -= vault_delta
                raise RuntimeError("durable flywheel ledger write required")
            return entry

    def _persist(self, entry: LedgerEntry) -> bool:
        if self.store is None:
            return False
        writer = getattr(self.store, "flywheel_append", None)
        if writer is not None:
            try:
                writer(entry.as_dict())
            except Exception as exc:  # pragma: no cover - DuckDB darf nie blocken
                logger.warning("flywheel_ledger persist failed: %s", exc)
                return False
            return True
        writer = getattr(self.store, "insert_row", None) or getattr(self.store, "append_row", None)
        if writer is None:
            return False
        try:
            writer(bp.FLYWHEEL_LEDGER_TABLE, entry.as_dict())
            return True
        except Exception as exc:  # pragma: no cover - DuckDB darf nie blocken
            logger.warning("flywheel_ledger persist failed: %s", exc)
            return False

    def _hydrate(self) -> None:
        """Rekonstruiert Salden und Reservierungen deterministisch aus dem Ledger."""
        reader = getattr(self.store, "flywheel_entries", None) if self.store is not None else None
        if reader is None:
            return
        try:
            rows = reader(limit=10_000, ascending=True)
            state_reader = getattr(self.store, "flywheel_state", None)
            aggregate = state_reader() if state_reader is not None else None
        except Exception as exc:  # pragma: no cover - Start bleibt verfuegbar
            logger.warning("flywheel_ledger hydrate failed: %s", exc)
            return
        if aggregate is not None:
            self.state.futures_balance_eur = float(
                aggregate.get("futures_balance_eur") or 0.0
            )
            self.state.vault_balance_eur = float(
                aggregate.get("vault_balance_eur") or 0.0
            )
            self.state.pending_profit_eur = float(
                aggregate.get("pending_profit_eur") or 0.0
            )
            self.state.allocated_eur = float(aggregate.get("allocated_eur") or 0.0)
            self.state.reconciliation_required = bool(
                aggregate.get("reconciliation_required")
            )
            self.state.pending_vault_operation_id = str(
                aggregate.get("pending_vault_operation_id") or ""
            )
        for row in rows:
            entry = LedgerEntry(
                entry_id=str(row.get("entry_id") or ""),
                ts=float(row.get("ts") or 0.0),
                kind=str(row.get("kind") or ""),
                amount_eur=float(row.get("amount_eur") or 0.0),
                futures_delta_eur=float(row.get("futures_delta_eur") or 0.0),
                vault_delta_eur=float(row.get("vault_delta_eur") or 0.0),
                asset=str(row.get("asset") or self.vault_asset),
                strategy_id=str(row.get("strategy_id") or ""),
                note=str(row.get("note") or ""),
                external_ref=str(row.get("external_ref") or ""),
            )
            if aggregate is None:
                self.state.futures_balance_eur += entry.futures_delta_eur
                self.state.vault_balance_eur += entry.vault_delta_eur
                if entry.kind == "realized_profit" and entry.amount_eur > 0:
                    self.state.pending_profit_eur += entry.amount_eur
                elif entry.kind == "profit_split":
                    self.state.pending_profit_eur = 0.0
                elif entry.kind == "bot_allocation":
                    self.state.allocated_eur += entry.amount_eur
                elif entry.kind == "bot_release":
                    self.state.allocated_eur = max(
                        0.0, self.state.allocated_eur - entry.amount_eur
                    )
                elif entry.kind == "vault_purchase_pending":
                    self.state.reconciliation_required = True
                    self.state.pending_vault_operation_id = entry.entry_id
                elif entry.kind == "vault_purchase_cancelled":
                    self.state.reconciliation_required = False
                    self.state.pending_vault_operation_id = ""
                if entry.kind == "profit_split":
                    self.state.reconciliation_required = False
                    self.state.pending_vault_operation_id = ""
            self.state.entries.append(entry)
        self.state.entries = self.state.entries[-1000:]

    # ----------------------------------------------------------- flows  ---
    def deposit(self, amount_eur: float, note: str = "operator deposit") -> LedgerEntry:
        """100 % einer Einzahlung geht ins Futures-Arbeitskonto (§28)."""
        with self._lock:
            if amount_eur <= 0:
                raise ValueError("deposit muss > 0 sein")
            return self._write(
                "deposit", amount_eur,
                futures_delta=amount_eur * bp.FLYWHEEL_DEPOSIT_TO_FUTURES_PCT,
                vault_delta=0.0, note=note,
            )

    def register_realized_profit(self, amount_eur: float, *, strategy_id: str = "",
                                 external_ref: str = "",
                                 ) -> Dict[str, Any]:
        """Realisierten Gewinn buchen; splittet erst ab Trigger-Schwelle."""
        with self._lock:
            seen = any(
                entry.external_ref == external_ref
                for entry in self.state.entries if external_ref
            )
            store_seen = getattr(self.store, "flywheel_external_ref_seen", None)
            if external_ref and (seen or (store_seen is not None and store_seen(external_ref))):
                return {"split": False, "reason": "duplicate_external_ref",
                        "external_ref": external_ref}
            if amount_eur <= 0:
                # Verluste mindern direkt das Arbeitskonto, kein Vault-Effekt.
                self._write("realized_loss", amount_eur, futures_delta=amount_eur,
                            vault_delta=0.0, strategy_id=strategy_id, note="realized loss",
                            external_ref=external_ref,
                            require_persist=bool(external_ref))
                return {"split": False, "reason": "loss",
                        "pending_eur": self.state.pending_profit_eur}

            self._write("realized_profit", amount_eur, futures_delta=amount_eur,
                        vault_delta=0.0, strategy_id=strategy_id, note="realized profit",
                        external_ref=external_ref,
                        require_persist=bool(external_ref))
            self.state.pending_profit_eur += amount_eur
            if self.state.pending_profit_eur < self.min_split_trigger_eur:
                return {
                    "split": False,
                    "reason": f"unter min_split_trigger_eur ({self.min_split_trigger_eur})",
                    "pending_eur": round(self.state.pending_profit_eur, 6),
                }
            return self.sweep(strategy_id=strategy_id)

    def sweep(self, *, strategy_id: str = "") -> Dict[str, Any]:
        """Tier-4-Flywheel-Sweep: 50 % Reinvest / 50 % Spot-Tresor."""
        with self._lock:
            pending = self.state.pending_profit_eur
            if self.state.reconciliation_required:
                return {
                    "split": False,
                    "reason": "vault purchase requires operator reconciliation",
                    "pending_eur": round(pending, 6),
                    "operation_id": self.state.pending_vault_operation_id,
                }
            if pending < self.min_split_trigger_eur:
                return {"split": False, "reason": "nichts zu sweepen",
                        "pending_eur": round(pending, 6)}
            if self._treasury_guard is not None and not self._treasury_guard(self.vault_asset):
                return {
                    "split": False,
                    "reason": f"contagion treasury veto for {self.vault_asset}",
                    "pending_eur": round(pending, 6),
                }
            vault_share = pending * self.vault_pct
            reinvest_share = pending - vault_share
            execution: Optional[Dict[str, Any]] = None
            live_execution = bool(
                self._vault_executor is not None
                and getattr(self._vault_executor, "enabled", True)
            )
            pending_entry: Optional[LedgerEntry] = None
            if live_execution:
                try:
                    pending_entry = self._write(
                        "vault_purchase_pending", vault_share,
                        futures_delta=0.0, vault_delta=0.0,
                        strategy_id=strategy_id,
                        note=f"pending spot purchase {self.vault_asset}",
                        require_persist=True,
                    )
                except Exception as exc:
                    return {
                        "split": False,
                        "reason": str(exc),
                        "pending_eur": round(pending, 6),
                    }
                self.state.reconciliation_required = True
                self.state.pending_vault_operation_id = pending_entry.entry_id
            if self._vault_executor is not None:
                try:
                    execution = self._vault_executor(self.vault_asset, vault_share)
                except Exception as exc:
                    logger.error("flywheel vault execution failed: %s", exc)
                    return {
                        "split": False,
                        "reason": f"vault execution failed: {exc}",
                        "pending_eur": round(pending, 6),
                        "operation_id": pending_entry.entry_id if pending_entry else "",
                    }
                if not execution.get("ok"):
                    if pending_entry is not None and not execution.get("executed"):
                        self._write(
                            "vault_purchase_cancelled", vault_share,
                            futures_delta=0.0, vault_delta=0.0,
                            strategy_id=strategy_id,
                            note=f"cancelled {pending_entry.entry_id}",
                        )
                        self.state.reconciliation_required = False
                        self.state.pending_vault_operation_id = ""
                    return {
                        "split": False,
                        "reason": execution.get("reason") or execution.get("error_code")
                        or "vault execution rejected",
                        "pending_eur": round(pending, 6),
                        "execution": execution,
                    }
                if pending_entry is not None:
                    self._write(
                        "vault_purchase_executed", vault_share,
                        futures_delta=0.0, vault_delta=0.0,
                        strategy_id=strategy_id,
                        note=f"{pending_entry.entry_id}:{execution.get('order_id', '')}",
                        require_persist=True,
                    )
            entry = self._write(
                "profit_split", pending,
                futures_delta=-vault_share,
                vault_delta=vault_share, strategy_id=strategy_id,
                note=f"50/50 split -> vault {self.vault_asset}",
                require_persist=live_execution,
            )
            self.state.pending_profit_eur = 0.0
            self.state.reconciliation_required = False
            self.state.pending_vault_operation_id = ""
            return {
                "split": True,
                "total_eur": round(pending, 6),
                "reinvest_eur": round(reinvest_share, 6),
                "vault_eur": round(vault_share, 6),
                "vault_asset": self.vault_asset,
                "entry_id": entry.entry_id,
                "execution": execution,
            }

    def allocate_bot_budget(self, strategy_id: str, amount_eur: float) -> Dict[str, Any]:
        """Reserviert isoliertes Bot-Budget aus dem freien Futures-Kapital (§31.4)."""
        with self._lock:
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
        with self._lock:
            self.state.allocated_eur = max(0.0, self.state.allocated_eur - amount_eur)
            self._write("bot_release", amount_eur, futures_delta=0.0, vault_delta=0.0,
                        strategy_id=strategy_id, note="Bot-Budget freigegeben")

    def transfer_vault_to_futures(self, amount_eur: float, *,
                                  operator_confirmed: bool = False) -> LedgerEntry:
        """Einbahnstrasse: nur mit expliziter Operator-Bestaetigung (§28)."""
        with self._lock:
            if not operator_confirmed:
                raise FlywheelViolation(
                    "Spot -> Futures ist nie automatisch; operator_confirmed=True noetig"
                )
            if amount_eur > self.state.vault_balance_eur:
                raise ValueError("Vault-Deckung unzureichend")
            return self._write("manual_vault_transfer", amount_eur,
                               futures_delta=amount_eur, vault_delta=-amount_eur,
                               note="manueller Operator-Transfer")

    def reconcile_vault_purchase(self, *, executed: bool,
                                 order_id: str = "") -> Dict[str, Any]:
        """Operator resolution for an ambiguous, durably journaled spot purchase."""
        with self._lock:
            operation_id = self.state.pending_vault_operation_id
            if not self.state.reconciliation_required or not operation_id:
                return {"reconciled": False, "reason": "no pending vault operation"}
            pending = self.state.pending_profit_eur
            vault_share = pending * self.vault_pct
            if not executed:
                self._write(
                    "vault_purchase_cancelled", vault_share,
                    futures_delta=0.0, vault_delta=0.0,
                    note=f"operator confirmed not executed: {operation_id}",
                    require_persist=True,
                )
                self.state.reconciliation_required = False
                self.state.pending_vault_operation_id = ""
                return {"reconciled": True, "executed": False,
                        "pending_eur": round(pending, 6)}
            self._write(
                "vault_purchase_reconciled", vault_share,
                futures_delta=0.0, vault_delta=0.0,
                note=f"{operation_id}:{order_id}",
                require_persist=True,
            )
            entry = self._write(
                "profit_split", pending,
                futures_delta=-vault_share, vault_delta=vault_share,
                note=f"reconciled split -> vault {self.vault_asset}",
                require_persist=True,
            )
            self.state.pending_profit_eur = 0.0
            self.state.reconciliation_required = False
            self.state.pending_vault_operation_id = ""
            return {"reconciled": True, "executed": True,
                    "entry_id": entry.entry_id, "order_id": order_id}

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
            "reconciliation_required": self.state.reconciliation_required,
            "pending_vault_operation_id": self.state.pending_vault_operation_id,
            "recent_entries": [e.as_dict() for e in self.state.entries[-20:]],
        }
