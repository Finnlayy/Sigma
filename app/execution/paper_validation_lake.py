"""
=========================================================
Datei:      app/execution/paper_validation_lake.py
Zweck:      Signal/Fill-Parquet je strategy_id + cohort_id (Paper-Validierung).
=========================================================
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import pyarrow as pa

from app.core import blueprint as bp
from app.core.config import load_config
from app.execution.StorageUtils import write_parquet_atomically

logger = logging.getLogger("app.execution.paper_validation_lake")

_SIGNAL_FIELDS = (
    "ts", "cohort_id", "strategy_id", "symbol", "action", "outcome",
    "reason", "code", "price", "idempotency_key",
)
_FILL_FIELDS = (
    "ts", "cohort_id", "strategy_id", "symbol", "side", "ack",
    "volume", "price", "order_id", "pnl_pct", "idempotency_key",
)


def _lake_root(base_dir: Optional[str] = None) -> str:
    if base_dir:
        return base_dir
    configured = getattr(bp, "PATH_PAPER_VALIDATION_LAKE", "") or ""
    if configured:
        return os.path.abspath(configured)
    cfg = load_config()
    return os.path.join(cfg.resolved_parquet_dir, "paper_validation")


class PaperValidationLake:
    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = _lake_root(base_dir)
        self._signals: Dict[str, List[Dict[str, Any]]] = {}
        self._fills: Dict[str, List[Dict[str, Any]]] = {}

    def _bucket(self, strategy_id: str, cohort_id: str) -> str:
        return f"{cohort_id}::{strategy_id}"

    def record_signal(
        self,
        *,
        strategy_id: str,
        cohort_id: str,
        symbol: str = "",
        action: str = "",
        outcome: str = "",
        reason: str = "",
        code: str = "",
        price: float = 0.0,
        idempotency_key: str = "",
        ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        row = {
            "ts": float(ts if ts is not None else time.time()),
            "cohort_id": cohort_id or "unattributed",
            "strategy_id": strategy_id or "unattributed",
            "symbol": symbol,
            "action": action,
            "outcome": outcome,
            "reason": reason,
            "code": code,
            "price": float(price or 0.0),
            "idempotency_key": idempotency_key,
        }
        self._signals.setdefault(self._bucket(row["strategy_id"], row["cohort_id"]), []).append(row)
        return row

    def record_fill(
        self,
        *,
        strategy_id: str,
        cohort_id: str,
        symbol: str = "",
        side: str = "",
        ack: str = "",
        volume: float = 0.0,
        price: float = 0.0,
        order_id: str = "",
        pnl_pct: float = 0.0,
        idempotency_key: str = "",
        ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        row = {
            "ts": float(ts if ts is not None else time.time()),
            "cohort_id": cohort_id or "unattributed",
            "strategy_id": strategy_id or "unattributed",
            "symbol": symbol,
            "side": side,
            "ack": ack,
            "volume": float(volume or 0.0),
            "price": float(price or 0.0),
            "order_id": order_id,
            "pnl_pct": float(pnl_pct or 0.0),
            "idempotency_key": idempotency_key,
        }
        self._fills.setdefault(self._bucket(row["strategy_id"], row["cohort_id"]), []).append(row)
        return row

    def record_fill_from_receipt(self, receipt: Any, *, cohort_id: str = "") -> Optional[Dict[str, Any]]:
        data = receipt.as_dict() if hasattr(receipt, "as_dict") else dict(receipt)
        sid = str(data.get("strategy_id") or "")
        if not sid:
            return None
        return self.record_fill(
            strategy_id=sid,
            cohort_id=cohort_id or "unattributed",
            symbol=str(data.get("pair") or ""),
            side=str(data.get("side") or ""),
            ack=str(data.get("ack") or ""),
            volume=float(data.get("volume") or 0.0),
            order_id=str(data.get("order_id") or ""),
            idempotency_key=str(data.get("idempotency_key") or ""),
            ts=float(data.get("ts") or time.time()),
        )

    def path_for(self, strategy_id: str, cohort_id: str, kind: str) -> str:
        safe_sid = str(strategy_id).replace("/", "-")
        safe_cid = str(cohort_id).replace("/", "-")
        return os.path.join(self.base_dir, safe_sid, safe_cid, f"{kind}.parquet")

    def counts(self, strategy_id: str, cohort_id: str) -> Dict[str, int]:
        key = self._bucket(strategy_id, cohort_id)
        sigs = self._signals.get(key) or []
        fills = self._fills.get(key) or []
        rejected = sum(1 for r in sigs if str(r.get("outcome") or "").upper() not in
                       ("EXECUTED", "FILLED", ""))
        return {
            "signals": len(sigs),
            "fills": len(fills),
            "rejected": rejected,
        }

    def summary(self, strategy_id: str, cohort_id: str) -> Dict[str, Any]:
        stats = self.counts(strategy_id, cohort_id)
        fills = self._fills.get(self._bucket(strategy_id, cohort_id)) or []
        pnls = [float(r.get("pnl_pct") or 0.0) for r in fills]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
        stats["net_pnl_pct"] = sum(pnls)
        stats["profit_factor"] = pf
        stats["win_rate"] = (len(wins) / len(pnls)) if pnls else 0.0
        return stats

    def flush(self, cohort_id: str) -> List[str]:
        written: List[str] = []
        prefix = f"{cohort_id}::"
        for key, rows in list(self._signals.items()):
            if not key.startswith(prefix) or not rows:
                continue
            _, sid = key.split("::", 1)
            written.append(self._write(self.path_for(sid, cohort_id, "signals"), rows, _SIGNAL_FIELDS))
        for key, rows in list(self._fills.items()):
            if not key.startswith(prefix) or not rows:
                continue
            _, sid = key.split("::", 1)
            written.append(self._write(self.path_for(sid, cohort_id, "fills"), rows, _FILL_FIELDS))
        return written

    def _write(self, path: str, rows: List[Dict[str, Any]], fields: tuple) -> str:
        cols = {}
        for field in fields:
            values = [r.get(field) for r in rows]
            if field in ("ts", "price", "volume", "pnl_pct"):
                cols[field] = pa.array([float(v or 0.0) for v in values], type=pa.float64())
            else:
                cols[field] = pa.array([str(v if v is not None else "") for v in values], type=pa.string())
        return write_parquet_atomically(path, pa.table(cols))


_lake: Optional[PaperValidationLake] = None


def get_paper_validation_lake(**kwargs) -> PaperValidationLake:
    global _lake
    if _lake is None:
        _lake = PaperValidationLake(**kwargs)
    return _lake


def set_paper_validation_lake(lake: Optional[PaperValidationLake]) -> None:
    global _lake
    _lake = lake


def record_ingest_outcome(
    *,
    strategy_id: str,
    symbol: str = "",
    action: str = "",
    outcome: str = "",
    reason: str = "",
    code: str = "",
    price: float = 0.0,
    idempotency_key: str = "",
) -> Dict[str, Any]:
    cohort_id = "unattributed"
    try:
        # Rotator imports this module at top-level; keep the reverse lazy.
        from sigma.orchestration.paper_validation_rotator import get_paper_validation_rotator
        cohort_id = get_paper_validation_rotator().cohort_id_for(strategy_id) or "unattributed"
    except Exception:
        cohort_id = "unattributed"
    if cohort_id == "unattributed":
        logger.info("paper validation unattributed strategy_id=%s outcome=%s",
                    strategy_id, outcome)
    return get_paper_validation_lake().record_signal(
        strategy_id=strategy_id or "unattributed",
        cohort_id=cohort_id,
        symbol=symbol,
        action=action,
        outcome=outcome,
        reason=reason,
        code=code,
        price=price,
        idempotency_key=idempotency_key,
    )


def notify_paper_fill(receipt: Any) -> Optional[Dict[str, Any]]:
    data = receipt.as_dict() if hasattr(receipt, "as_dict") else dict(receipt)
    sid = str(data.get("strategy_id") or "")
    if not sid:
        return None
    cohort_id = "unattributed"
    try:
        from sigma.orchestration.paper_validation_rotator import get_paper_validation_rotator
        cohort_id = get_paper_validation_rotator().cohort_id_for(sid) or "unattributed"
    except Exception:
        cohort_id = "unattributed"
    return get_paper_validation_lake().record_fill_from_receipt(
        receipt, cohort_id=cohort_id,
    )
