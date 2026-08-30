"""
=========================================================
Datei:      app/core/duckdb_store.py
Zweck:      DuckDB / Parquet Datenfundament (L2 Storage-Tiering)
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================
Enthält das gefrorene v1.2.0 vault_ledger-Schema (Blueprint §2) sowie
strategy_budgets-Sync (write-through aus dem Redis M8-State-Engine).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger("app.core.duckdb")

VAULT_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS vault_ledger (
  entry_id VARCHAR PRIMARY KEY,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  strategy_id VARCHAR,
  type VARCHAR,
  amount_usd DOUBLE,
  balance_snapshot DOUBLE
);
"""

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS vault_ledger (
  entry_id VARCHAR PRIMARY KEY,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  strategy_id VARCHAR,
  type VARCHAR,
  amount_usd DOUBLE,
  balance_snapshot DOUBLE
);

CREATE TABLE IF NOT EXISTS strategy_budgets (
  instance_id VARCHAR PRIMARY KEY,
  strategy_id VARCHAR,
  status VARCHAR,
  base_budget_usd DOUBLE,
  current_budget_usd DOUBLE,
  budget_multiplier DOUBLE,
  consecutive_losses INTEGER,
  consecutive_low_pf_days INTEGER,
  shadow_trades_count INTEGER,
  shadow_wins INTEGER,
  last_ga_recalibration_ts VARCHAR,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategies (
  id VARCHAR PRIMARY KEY,
  name VARCHAR,
  description VARCHAR,
  code VARCHAR,
  status VARCHAR,
  asset_pair VARCHAR,
  interval_min INTEGER,
  execution_mode VARCHAR,
  parameters VARCHAR,
  hard_stop_enabled INTEGER,
  hard_stop_percent DOUBLE,
  created_at TIMESTAMP,
  seeded_from_id VARCHAR,
  seeded_from_name VARCHAR,
  version INTEGER,
  archived_at TIMESTAMP,
  evolution_generation INTEGER,
  evolution_fitness DOUBLE,
  last_ga_recalibration_ts TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trades (
  trade_id VARCHAR PRIMARY KEY,
  instance_id VARCHAR,
  strategy_id VARCHAR,
  strategy_name VARCHAR,
  symbol VARCHAR,
  execution_mode VARCHAR,
  market_type VARCHAR,
  direction VARCHAR,
  side VARCHAR,
  status VARCHAR,
  entry_time TIMESTAMP,
  exit_time TIMESTAMP,
  entry_price DOUBLE,
  exit_price DOUBLE,
  quantity DOUBLE,
  margin_usd DOUBLE,
  leverage DOUBLE,
  notional_usd DOUBLE,
  gross_pnl_usd DOUBLE,
  fees_usd DOUBLE,
  funding_usd DOUBLE,
  net_pnl_usd DOUBLE,
  pnl_r DOUBLE,
  mfe_r DOUBLE,
  mae_r DOUBLE,
  capture_ratio DOUBLE,
  autopsy_zone VARCHAR,
  exit_reason VARCHAR,
  stop_slippage_bps DOUBLE,
  fee_hurdle_multiple DOUBLE,
  hold_seconds DOUBLE
);

CREATE TABLE IF NOT EXISTS ohlcv (
  symbol VARCHAR,
  interval_sec INTEGER,
  ts TIMESTAMP,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  PRIMARY KEY (symbol, interval_sec, ts)
);

CREATE TABLE IF NOT EXISTS daily_pnl (
  strategy_id VARCHAR,
  day DATE,
  gross_win DOUBLE,
  gross_loss DOUBLE,
  profit_factor DOUBLE,
  net_pnl_usd DOUBLE,
  trades_count INTEGER,
  wins INTEGER,
  losses INTEGER,
  PRIMARY KEY (strategy_id, day)
);

CREATE TABLE IF NOT EXISTS flywheel_ledger (
  entry_id VARCHAR PRIMARY KEY,
  ts DOUBLE,
  kind VARCHAR,
  amount_eur DOUBLE,
  futures_delta_eur DOUBLE,
  vault_delta_eur DOUBLE,
  asset VARCHAR,
  strategy_id VARCHAR,
  note VARCHAR,
  external_ref VARCHAR UNIQUE
);

CREATE TABLE IF NOT EXISTS contagion_history (
  ts DOUBLE,
  r0 DOUBLE,
  beta DOUBLE,
  gamma DOUBLE,
  mode VARCHAR,
  size_multiplier DOUBLE,
  allow_altcoin_treasury INTEGER,
  reason VARCHAR,
  inputs VARCHAR,
  veto_code VARCHAR
);

CREATE TABLE IF NOT EXISTS reconciled_fills (
  fill_id VARCHAR PRIMARY KEY,
  ts DOUBLE,
  strategy_id VARCHAR,
  symbol VARCHAR,
  net_pnl_usd DOUBLE,
  status VARCHAR,
  payload VARCHAR
);

CREATE TABLE IF NOT EXISTS genomes (
  genome_id VARCHAR PRIMARY KEY,
  strategy_id VARCHAR,
  asset_pair VARCHAR,
  interval_min INTEGER,
  genes VARCHAR,
  generation INTEGER,
  fitness DOUBLE,
  dsr DOUBLE,
  cadence_per_day DOUBLE,
  in_sample_summary VARCHAR,
  oos_sample_summary VARCHAR,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS academy_registry (
  id VARCHAR PRIMARY KEY,
  name VARCHAR,
  symbol VARCHAR,
  interval_min INTEGER,
  archetype VARCHAR,
  graduation_level VARCHAR,
  wfo_return DOUBLE,
  wfo_sharpe DOUBLE,
  dsr DOUBLE,
  drills_passed INTEGER,
  drills_total INTEGER,
  last_drill_ts TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_slots (
  strategy_id VARCHAR,
  symbol VARCHAR,
  timeframe VARCHAR,
  regime VARCHAR,
  origin VARCHAR,
  lamp VARCHAR,
  locked INTEGER,
  favorite INTEGER,
  pf_after_fees DOUBLE,
  last_job_id VARCHAR,
  verified_at DOUBLE,
  updated_at DOUBLE,
  PRIMARY KEY (strategy_id, symbol, timeframe, regime)
);

CREATE TABLE IF NOT EXISTS strategy_scorecard (
  strategy_id VARCHAR PRIMARY KEY,
  lamp VARCHAR,
  initialized_at DOUBLE,
  stage1_done INTEGER,
  options_opened_at DOUBLE,
  last_init_job_id VARCHAR,
  last_pull_job_id VARCHAR,
  last_validate_job_id VARCHAR,
  pf_after_fees DOUBLE,
  net_pnl DOUBLE,
  trade_count INTEGER,
  win_rate DOUBLE,
  updated_at DOUBLE
);
"""


class DuckDBStore:
    """Thread-safe single-connection DuckDB wrapper."""

    def __init__(self, db_path: str, memory_limit: str = "2GB", threads: int = 4):
        self.db_path = db_path
        self._memory_limit = memory_limit
        self._threads = int(threads)
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = duckdb.connect(db_path)
        try:
            self._conn.execute(f"SET memory_limit='{memory_limit}'")
            self._conn.execute(f"SET threads={int(threads)}")
        except Exception:
            pass
        self.initialize()
        logger.info("DuckDB store ready at %s", db_path)

    def initialize(self) -> None:
        with self._lock:
            self._conn.execute(SCHEMA_DDL)
            self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Additive upgrades for stores created before the five-module ledger columns."""
        self._conn.execute(
            "ALTER TABLE flywheel_ledger ADD COLUMN IF NOT EXISTS external_ref VARCHAR"
        )
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS flywheel_ledger_external_ref "
                "ON flywheel_ledger(external_ref) WHERE external_ref IS NOT NULL"
            )
        except Exception:
            pass
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_slots (
              strategy_id VARCHAR, symbol VARCHAR, timeframe VARCHAR, regime VARCHAR,
              origin VARCHAR, lamp VARCHAR, locked INTEGER, favorite INTEGER,
              pf_after_fees DOUBLE, last_job_id VARCHAR, verified_at DOUBLE, updated_at DOUBLE,
              PRIMARY KEY (strategy_id, symbol, timeframe, regime)
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS strategy_scorecard (
              strategy_id VARCHAR PRIMARY KEY, lamp VARCHAR, initialized_at DOUBLE,
              stage1_done INTEGER, options_opened_at DOUBLE, last_init_job_id VARCHAR,
              last_pull_job_id VARCHAR, last_validate_job_id VARCHAR, pf_after_fees DOUBLE,
              net_pnl DOUBLE, trade_count INTEGER, win_rate DOUBLE, updated_at DOUBLE
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS live_kraken_snapshots (
              snapshot_id VARCHAR PRIMARY KEY,
              ts DOUBLE,
              balances_json VARCHAR,
              error VARCHAR,
              has_credentials INTEGER
            )"""
        )

    # ------------------------------------------------------------------ helpers
    def _rows(self, sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _one(self, sql: str, params: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    def _exec(self, sql: str, params: Optional[List[Any]] = None) -> None:
        with self._lock:
            self._conn.execute(sql, params or [])

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def checkpoint(self) -> None:
        with self._lock:
            self._conn.execute("CHECKPOINT")

    def release_memory(self) -> str:
        """CHECKPOINT plus a brief memory_limit cycle so DuckDB drops buffer pool pages."""
        with self._lock:
            self._conn.execute("CHECKPOINT")
            try:
                self._conn.execute("SET memory_limit='256MB'")
                self._conn.execute(f"SET memory_limit='{self._memory_limit}'")
            except Exception as exc:
                return f"duckdb checkpoint ok; limit-cycle skipped: {exc}"
        return "duckdb checkpoint + memory_limit cycle"

    # -------------------------------------------------------------------- vault
    def vault_credit(self, entry_id: str, strategy_id: str, vtype: str,
                     amount_usd: float, balance_snapshot: float) -> None:
        self._exec(
            "INSERT OR REPLACE INTO vault_ledger (entry_id, strategy_id, type, amount_usd, balance_snapshot) "
            "VALUES (?, ?, ?, ?, ?)",
            [entry_id, strategy_id, vtype, float(amount_usd), float(balance_snapshot)],
        )

    def vault_balance(self) -> float:
        row = self._one("SELECT COALESCE(SUM(amount_usd), 0) AS b FROM vault_ledger")
        return float(row["b"]) if row else 0.0

    def put_live_kraken_snapshot(self, *, balances: Dict[str, float],
                                 ts: Optional[float], error: Optional[str],
                                 has_credentials: bool) -> None:
        self._exec(
            """INSERT OR REPLACE INTO live_kraken_snapshots
               (snapshot_id, ts, balances_json, error, has_credentials)
               VALUES (?, ?, ?, ?, ?)""",
            ["latest", ts, json.dumps(balances or {}), error or "",
             1 if has_credentials else 0],
        )

    def get_live_kraken_snapshot(self) -> Optional[Dict[str, Any]]:
        row = self._one(
            "SELECT ts, balances_json, error, has_credentials "
            "FROM live_kraken_snapshots WHERE snapshot_id = ?",
            ["latest"],
        )
        if not row:
            return None
        try:
            raw = json.loads(row.get("balances_json") or "{}")
        except json.JSONDecodeError:
            raw = {}
        balances: Dict[str, float] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                try:
                    balances[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
        return {
            "ts": row.get("ts"),
            "balances": balances,
            "error": row.get("error") or None,
            "has_credentials": bool(row.get("has_credentials")),
        }

    def vault_entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM vault_ledger ORDER BY timestamp DESC LIMIT ?", [int(limit)]
        )
        for r in rows:
            if r.get("timestamp") is not None:
                r["timestamp"] = str(r["timestamp"])
        return rows

    # --------------------------------------------------------- strategy budgets
    def sync_budget(self, state: Dict[str, Any]) -> None:
        """Write-through sync vom M8-State-Engine nach DuckDB (v1.2.0 §'still missing')."""
        ga_ts = state.get("last_ga_recalibration_ts")
        if isinstance(ga_ts, (int, float)):
            import datetime

            ga_ts = datetime.datetime.fromtimestamp(
                float(ga_ts), datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        self._exec(
            """INSERT OR REPLACE INTO strategy_budgets
               (instance_id, strategy_id, status, base_budget_usd, current_budget_usd,
                budget_multiplier, consecutive_losses, consecutive_low_pf_days,
                shadow_trades_count, shadow_wins, last_ga_recalibration_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                state.get("strategy_id") or state.get("instance_id"),
                state.get("strategy_id"),
                state.get("status"),
                _f(state.get("base_budget_usd")),
                _f(state.get("current_budget_usd")),
                _f(state.get("budget_multiplier"), 1.0),
                int(_f(state.get("consecutive_losses"), 0)),
                int(_f(state.get("consecutive_low_pf_days"), 0)),
                int(_f(state.get("shadow_trades_count"), 0)),
                int(_f(state.get("shadow_wins"), 0)),
                ga_ts,
            ],
        )

    def all_budgets(self) -> List[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM strategy_budgets ORDER BY instance_id")
        for r in rows:
            if r.get("updated_at") is not None:
                r["updated_at"] = str(r["updated_at"])
        return rows

    # -------------------------------------------------------------- strategies
    def upsert_strategy(self, s: Dict[str, Any]) -> None:
        params = s.get("parameters") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        if not isinstance(params, dict):
            params = {}
        else:
            params = dict(params)
        tv_script_id = s.get("tv_script_id") or params.get("tv_script_id") or ""
        if tv_script_id:
            params["tv_script_id"] = tv_script_id
        execution_mode = s.get("executionMode") or s.get("execution_mode") or "paper"
        if str(execution_mode).lower() == "live" and tv_script_id:
            execution_mode = "paper"
        self._exec(
            """INSERT OR REPLACE INTO strategies
               (id, name, description, code, status, asset_pair, interval_min,
                execution_mode, parameters, hard_stop_enabled, hard_stop_percent,
                created_at, seeded_from_id, seeded_from_name, version, archived_at,
                evolution_generation, evolution_fitness, last_ga_recalibration_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                s["id"], s.get("name"), s.get("description", ""), s.get("code", ""),
                s.get("status", "inactive"), s.get("assetPair") or s.get("asset_pair"),
                int(s.get("interval") or s.get("interval_min") or 15),
                execution_mode,
                json.dumps(params),
                int(1 if s.get("hardStopEnabled", True) else 0),
                _f(s.get("hardStopPercent"), 5.0),
                s.get("createdAt") or s.get("created_at"),
                s.get("seededFromId"), s.get("seededFromName"),
                int(s.get("version") or 1), s.get("archivedAt"),
                int(_f(s.get("evolutionGeneration"), 0)),
                _f(s.get("evolutionFitness"), 0.0),
                s.get("lastGaRecalibrationTs"),
            ],
        )

    def get_strategy(self, sid: str) -> Optional[Dict[str, Any]]:
        row = self._one("SELECT * FROM strategies WHERE id = ?", [sid])
        return self._strategy_row_to_api(row) if row else None

    def list_strategies(self, include_archived: bool = True) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM strategies"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY created_at"
        return [self._strategy_row_to_api(r) for r in self._rows(sql)]

    def find_strategy_by_tv_script_id(self, tv_script_id: str) -> Optional[Dict[str, Any]]:
        needle = (tv_script_id or "").strip()
        if not needle:
            return None
        for row in self.list_strategies():
            params = row.get("parameters") or {}
            stored = str(row.get("tv_script_id") or params.get("tv_script_id") or "")
            if stored == needle:
                return row
        return None

    @staticmethod
    def _strategy_row_to_api(r: Dict[str, Any]) -> Dict[str, Any]:
        raw_params = r.get("parameters") or {}
        if isinstance(raw_params, dict):
            params = dict(raw_params)
        else:
            try:
                params = json.loads(raw_params or "{}")
            except (TypeError, json.JSONDecodeError):
                params = {}
        if not isinstance(params, dict):
            params = {}
        return {
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description") or "",
            "code": r.get("code") or "",
            "status": r.get("status") or "inactive",
            "assetPair": r.get("asset_pair"),
            "interval": int(r.get("interval_min") or 15),
            "executionMode": r.get("execution_mode") or "paper",
            "parameters": params,
            "tv_script_id": params.get("tv_script_id") or "",
            "hardStopEnabled": bool(r.get("hard_stop_enabled", 1)),
            "hardStopPercent": float(r.get("hard_stop_percent") or 5.0),
            "createdAt": str(r.get("created_at")) if r.get("created_at") else None,
            "seededFromId": r.get("seeded_from_id"),
            "seededFromName": r.get("seeded_from_name"),
            "version": int(r.get("version") or 1),
            "archivedAt": str(r.get("archived_at")) if r.get("archived_at") else None,
            "evolutionGeneration": int(r.get("evolution_generation") or 0),
            "evolutionFitness": float(r.get("evolution_fitness") or 0.0),
            "lastGaRecalibrationTs": str(r.get("last_ga_recalibration_ts"))
            if r.get("last_ga_recalibration_ts") else None,
        }

    # ------------------------------------------------------------------- trades
    def upsert_trade(self, t: Dict[str, Any]) -> None:
        self._exec(
            """INSERT OR REPLACE INTO trades
               (trade_id, instance_id, strategy_id, strategy_name, symbol, execution_mode,
                market_type, direction, side, status, entry_time, exit_time, entry_price,
                exit_price, quantity, margin_usd, leverage, notional_usd, gross_pnl_usd,
                fees_usd, funding_usd, net_pnl_usd, pnl_r, mfe_r, mae_r, capture_ratio,
                autopsy_zone, exit_reason, stop_slippage_bps, fee_hurdle_multiple, hold_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                t["trade_id"], t.get("instance_id"), t.get("strategy_id"),
                t.get("strategy_name"), t.get("symbol"), t.get("execution_mode"),
                t.get("market_type"), t.get("direction"), t.get("side"),
                t.get("status", "open"), t.get("entry_time"), t.get("exit_time"),
                _f(t.get("entry_price")), _f(t.get("exit_price")), _f(t.get("quantity")),
                _f(t.get("margin_usd")), _f(t.get("leverage"), 1.0), _f(t.get("notional_usd")),
                _f(t.get("gross_pnl_usd")), _f(t.get("fees_usd")), _f(t.get("funding_usd")),
                _f(t.get("net_pnl_usd")), _f(t.get("pnl_r")), _f(t.get("mfe_r")),
                _f(t.get("mae_r")), _f(t.get("capture_ratio")), t.get("autopsy_zone"),
                t.get("exit_reason"), _f(t.get("stop_slippage_bps")),
                _f(t.get("fee_hurdle_multiple")), _f(t.get("hold_seconds")),
            ],
        )

    def trades(self, strategy_id: Optional[str] = None, status: Optional[str] = None,
               limit: int = 500, execution_mode: Optional[str] = None,
               strategy_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if strategy_ids is not None and len(strategy_ids) == 0:
            return []
        sql = "SELECT * FROM trades WHERE 1=1"
        params: List[Any] = []
        if strategy_ids:
            placeholders = ", ".join("?" for _ in strategy_ids)
            sql += f" AND strategy_id IN ({placeholders})"
            params.extend(strategy_ids)
        elif strategy_id:
            sql += " AND strategy_id = ?"
            params.append(strategy_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if execution_mode:
            sql += " AND execution_mode = ?"
            params.append(execution_mode)
        sql += " ORDER BY entry_time DESC LIMIT ?"
        params.append(int(limit))
        rows = self._rows(sql, params)
        for r in rows:
            for k in ("entry_time", "exit_time"):
                if r.get(k) is not None:
                    r[k] = str(r[k])
        return rows

    def closed_pnl_stats(self, execution_mode: str = "paper") -> Dict[str, float]:
        """SUM + COUNT in DuckDB — do not materialize up to 10k trade rows.

        Matches Python `(execution_mode or "paper") == mode` including NULL/''.
        Bench @ 8k closed rows (full /api/logs path): dump+Python ~30 ms vs
        this + GROUP BY + 80-row tape ~4 ms (~8×).
        """
        row = self._one(
            "SELECT COALESCE(SUM(net_pnl_usd), 0) AS pnl, COUNT(*) AS n FROM trades "
            "WHERE status = 'closed' "
            "AND COALESCE(NULLIF(CAST(execution_mode AS VARCHAR), ''), ?) = ?",
            [execution_mode, execution_mode],
        )
        return {
            "pnl": float(row["pnl"]) if row else 0.0,
            "n": float(row["n"]) if row else 0.0,
        }

    def sum_closed_pnl(self, execution_mode: str = "paper") -> float:
        """SUM net_pnl in DuckDB — do not materialize up to 10k trade rows.

        Matches Python `(execution_mode or "paper") == mode` including NULL/''.
        Bench @ 4k closed rows: trades()+sum ~22 ms vs this ~0.5 ms (~45×).
        """
        return self.closed_pnl_stats(execution_mode)["pnl"]

    def closed_pnl_by_strategy(self) -> Dict[str, Dict[str, float]]:
        """GROUP BY strategy_id in DuckDB — one hash-aggregate, no row dump.

        Replaces trades(limit=5000) + Python group on the /api/logs poll.
        Full-table aggregate (no LIMIT) so totals stay correct past 5k/10k.
        """
        rows = self._rows(
            """SELECT strategy_id,
                      COUNT(*) AS n,
                      SUM(CASE WHEN COALESCE(net_pnl_usd, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                      COALESCE(SUM(net_pnl_usd), 0) AS realized,
                      COALESCE(SUM(notional_usd), 0) AS volume
               FROM trades
               WHERE status = 'closed'
               GROUP BY strategy_id"""
        )
        out: Dict[str, Dict[str, float]] = {}
        for r in rows:
            out[str(r.get("strategy_id") or "")] = {
                "n": float(r.get("n") or 0),
                "wins": float(r.get("wins") or 0),
                "realized": float(r.get("realized") or 0.0),
                "volume": float(r.get("volume") or 0.0),
            }
        return out

    # -------------------------------------------------------------------- ohlcv
    def seed_ohlcv(self, symbol: str, interval_sec: int, candles: List[Dict[str, Any]]) -> int:
        if not candles:
            return 0
        arr_ts = [c["ts"] for c in candles]
        arr_o = [float(c["open"]) for c in candles]
        arr_h = [float(c["high"]) for c in candles]
        arr_l = [float(c["low"]) for c in candles]
        arr_c = [float(c["close"]) for c in candles]
        arr_v = [float(c["volume"]) for c in candles]
        with self._lock:
            self._conn.register("_ohlcv_batch", pa.table({
                "symbol": pa.array([symbol] * len(candles), type=pa.string()),
                "interval_sec": pa.array([interval_sec] * len(candles), type=pa.int64()),
                "ts": pa.array(arr_ts, type=pa.timestamp("us")),
                "open": pa.array(arr_o, type=pa.float64()),
                "high": pa.array(arr_h, type=pa.float64()),
                "low": pa.array(arr_l, type=pa.float64()),
                "close": pa.array(arr_c, type=pa.float64()),
                "volume": pa.array(arr_v, type=pa.float64()),
            }))
            self._conn.execute(
                "INSERT OR REPLACE INTO ohlcv SELECT * FROM _ohlcv_batch"
            )
            self._conn.unregister("_ohlcv_batch")
        return len(candles)

    def ohlcv(self, symbol: str, interval_sec: int, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol = ? AND interval_sec = ? ORDER BY ts DESC LIMIT ?",
            [symbol, int(interval_sec), int(limit)],
        )
        rows.reverse()
        for r in rows:
            r["ts"] = str(r["ts"])
        return rows

    def latest_close(self, symbol: str) -> Optional[float]:
        row = self._one(
            "SELECT close FROM ohlcv WHERE symbol = ? ORDER BY ts DESC LIMIT 1", [symbol]
        )
        return float(row["close"]) if row and row.get("close") is not None else None

    def lake_summary(self) -> Dict[str, Any]:
        row = self._one(
            "SELECT COUNT(*) AS n, MIN(ts) AS start_time, MAX(ts) AS end_time FROM ohlcv"
        )
        per_symbol = self._rows(
            "SELECT symbol, interval_sec, COUNT(*) AS rows, MIN(ts) AS start_time, "
            "MAX(ts) AS end_time, SUM(volume) AS vol FROM ohlcv GROUP BY symbol, interval_sec"
        )
        symbols = []
        for p in per_symbol:
            symbols.append({
                "symbol": p["symbol"],
                "intervalSec": int(p["interval_sec"]),
                "rows": int(p["rows"]),
                "startTime": str(p["start_time"]) if p.get("start_time") else None,
                "endTime": str(p["end_time"]) if p.get("end_time") else None,
            })
        parquet_count, parquet_mb = self._parquet_stats()
        return {
            "total_rows": int(row["n"]) if row else 0,
            "total_size_mb": round(parquet_mb, 2),
            "total_files": parquet_count,
            "symbols": symbols,
            "storage_config": {
                "duckdb_memory_limit": self._memory_limit,
                "duckdb_threads": self._threads,
                "compression_level": 7,
                "compression": "ZSTD",
            },
            "cloud_sync": {
                # [MOCK] Rclone/Google-Drive Sync — im Sandbox-Run nur Statusbericht.
                # Ersetze durch echten rclone-Call (siehe stacks/ubuntu README).
                "configured": False,
                "remote_base_path": "Backtest_Data/OHLCV",
                "last_sync": None,
            },
        }

    _memory_limit = "2GB"
    _threads = 4

    def _parquet_stats(self):
        from app.core.config import load_config  # local import to avoid cycle

        cfg = load_config()
        base = cfg.resolved_parquet_dir
        count, size = 0, 0
        if os.path.isdir(base):
            for root, _dirs, files in os.walk(base):
                for f in files:
                    if f.endswith(".parquet"):
                        count += 1
                        size += os.path.getsize(os.path.join(root, f))
        return count, round(size / (1024 * 1024), 2)

    def lake_query(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.ohlcv(symbol, 60, limit=limit)

    # -------------------------------------------------------------- daily pnl
    def upsert_daily_pnl(self, row: Dict[str, Any]) -> None:
        self._exec(
            """INSERT OR REPLACE INTO daily_pnl
               (strategy_id, day, gross_win, gross_loss, profit_factor,
                net_pnl_usd, trades_count, wins, losses)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [row["strategy_id"], row["day"], _f(row.get("gross_win")),
             _f(row.get("gross_loss")), _f(row.get("profit_factor")),
             _f(row.get("net_pnl_usd")), int(_f(row.get("trades_count"), 0)),
             int(_f(row.get("wins"), 0)), int(_f(row.get("losses"), 0))],
        )

    def daily_pnl(self, strategy_id: Optional[str] = None, days: int = 90) -> List[Dict[str, Any]]:
        if strategy_id:
            rows = self._rows(
                "SELECT * FROM daily_pnl WHERE strategy_id = ? ORDER BY day DESC LIMIT ?",
                [strategy_id, int(days)],
            )
        else:
            rows = self._rows(
                "SELECT * FROM daily_pnl ORDER BY day DESC LIMIT ?", [int(days)]
            )
        for r in rows:
            if r.get("day") is not None:
                r["day"] = str(r["day"])
        return rows

    # -------------------------------------------------------------- flywheel
    def flywheel_append(self, row: Dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO flywheel_ledger
               (entry_id, ts, kind, amount_eur, futures_delta_eur, vault_delta_eur,
                asset, strategy_id, note, external_ref)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                row["entry_id"], _f(row.get("ts")), row.get("kind"),
                _f(row.get("amount_eur")), _f(row.get("futures_delta_eur")),
                _f(row.get("vault_delta_eur")), row.get("asset"),
                row.get("strategy_id"), row.get("note"),
                row.get("external_ref") or None,
            ],
        )

    def flywheel_external_ref_seen(self, external_ref: str) -> bool:
        if not external_ref:
            return False
        return self._one(
            "SELECT entry_id FROM flywheel_ledger WHERE external_ref = ?",
            [external_ref],
        ) is not None

    def flywheel_entries(self, limit: int = 1000, *, ascending: bool = False
                         ) -> List[Dict[str, Any]]:
        if ascending:
            return self._rows(
                """SELECT * FROM (
                     SELECT * FROM flywheel_ledger ORDER BY ts DESC LIMIT ?
                   ) recent ORDER BY ts ASC""",
                [int(limit)],
            )
        return self._rows(
            "SELECT * FROM flywheel_ledger ORDER BY ts DESC LIMIT ?",
            [int(limit)],
        )

    def flywheel_state(self) -> Dict[str, Any]:
        totals = self._one(
            """SELECT
                 COALESCE(SUM(futures_delta_eur), 0) AS futures_balance_eur,
                 COALESCE(SUM(vault_delta_eur), 0) AS vault_balance_eur,
                 COALESCE(SUM(CASE
                   WHEN kind = 'bot_allocation' THEN amount_eur
                   WHEN kind = 'bot_release' THEN -amount_eur
                   ELSE 0 END), 0) AS allocated_eur
               FROM flywheel_ledger"""
        ) or {}
        latest_split = self._one(
            "SELECT COALESCE(MAX(ts), 0) AS ts FROM flywheel_ledger "
            "WHERE kind = 'profit_split'"
        ) or {"ts": 0.0}
        pending = self._one(
            """SELECT COALESCE(SUM(amount_eur), 0) AS pending_profit_eur
               FROM flywheel_ledger
               WHERE kind = 'realized_profit' AND ts > ?""",
            [_f(latest_split.get("ts"))],
        ) or {}
        control = self._one(
            """SELECT entry_id, kind FROM flywheel_ledger
               WHERE kind IN ('vault_purchase_pending', 'vault_purchase_cancelled',
                              'profit_split')
               ORDER BY ts DESC LIMIT 1"""
        )
        reconciliation_required = bool(
            control and control.get("kind") == "vault_purchase_pending"
        )
        return {
            "futures_balance_eur": _f(totals.get("futures_balance_eur")),
            "vault_balance_eur": _f(totals.get("vault_balance_eur")),
            "allocated_eur": max(0.0, _f(totals.get("allocated_eur"))),
            "pending_profit_eur": _f(pending.get("pending_profit_eur")),
            "reconciliation_required": reconciliation_required,
            "pending_vault_operation_id": (
                str(control.get("entry_id")) if reconciliation_required else ""
            ),
        }

    # ------------------------------------------------------------- contagion
    def contagion_append(self, row: Dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO contagion_history
               (ts, r0, beta, gamma, mode, size_multiplier,
                allow_altcoin_treasury, reason, inputs, veto_code)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                _f(row.get("ts")), _f(row.get("r0")), _f(row.get("beta")),
                _f(row.get("gamma")), row.get("mode"),
                _f(row.get("size_multiplier"), 1.0),
                int(bool(row.get("allow_altcoin_treasury"))),
                row.get("reason"), json.dumps(row.get("inputs") or {}),
                row.get("veto_code"),
            ],
        )

    def contagion_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM contagion_history ORDER BY ts DESC LIMIT ?",
            [int(limit)],
        )
        for row in rows:
            row["inputs"] = json.loads(row.get("inputs") or "{}")
            row["allow_altcoin_treasury"] = bool(row.get("allow_altcoin_treasury"))
        return rows

    # ------------------------------------------------------ live fill ledger
    def reconciled_fill_status(self, fill_id: str) -> Optional[str]:
        row = self._one(
            "SELECT status FROM reconciled_fills WHERE fill_id = ?",
            [fill_id],
        )
        return str(row.get("status")) if row else None

    def record_reconciled_fill(self, row: Dict[str, Any],
                               status: str = "pending") -> None:
        self._exec(
            """INSERT OR IGNORE INTO reconciled_fills
               (fill_id, ts, strategy_id, symbol, net_pnl_usd, status, payload)
               VALUES (?,?,?,?,?,?,?)""",
            [
                row["fill_id"], _f(row.get("ts")), row.get("strategy_id"),
                row.get("symbol"), _f(row.get("net_pnl_usd")),
                status,
                json.dumps(row.get("payload") or {}),
            ],
        )

    def set_reconciled_fill_status(self, fill_id: str, status: str) -> None:
        self._exec(
            "UPDATE reconciled_fills SET status = ? WHERE fill_id = ?",
            [status, fill_id],
        )

    def reconciled_fill_watermark(self) -> float:
        row = self._one("SELECT COALESCE(MAX(ts), 0) AS ts FROM reconciled_fills")
        return _f(row.get("ts")) if row else 0.0

    def unapplied_reconciled_fills(self) -> List[Dict[str, Any]]:
        """Pending/failed fills that must be retried even outside the CLI window."""
        rows = self._rows(
            "SELECT * FROM reconciled_fills WHERE status IN ('pending', 'failed')"
        )
        out: List[Dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload or "{}")
                except json.JSONDecodeError:
                    payload = {}
            out.append({
                "fill_id": str(row.get("fill_id") or ""),
                "ts": _f(row.get("ts")),
                "strategy_id": str(row.get("strategy_id") or ""),
                "symbol": str(row.get("symbol") or ""),
                "net_pnl_usd": _f(row.get("net_pnl_usd")),
                "payload": payload or {},
                "status": str(row.get("status") or ""),
            })
        return out

    # ------------------------------------------------------------------ genomes
    def upsert_genome(self, g: Dict[str, Any]) -> None:
        self._exec(
            """INSERT OR REPLACE INTO genomes
               (genome_id, strategy_id, asset_pair, interval_min, genes, generation,
                fitness, dsr, cadence_per_day, in_sample_summary, oos_sample_summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [g["genome_id"], g.get("strategy_id"), g.get("asset_pair"),
             int(g.get("interval_min") or 15), json.dumps(g.get("genes") or {}),
             int(_f(g.get("generation"), 0)), _f(g.get("fitness")), _f(g.get("dsr")),
             _f(g.get("cadence_per_day")), json.dumps(g.get("in_sample_summary") or {}),
             json.dumps(g.get("oos_sample_summary") or {})],
        )

    def genomes(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM genomes ORDER BY created_at DESC LIMIT ?", [int(limit)])
        for r in rows:
            r["genes"] = json.loads(r.get("genes") or "{}")
            r["in_sample_summary"] = json.loads(r.get("in_sample_summary") or "{}")
            r["oos_sample_summary"] = json.loads(r.get("oos_sample_summary") or "{}")
        return rows

    # ----------------------------------------------------------------- academy
    def upsert_academy_entry(self, a: Dict[str, Any]) -> None:
        self._exec(
            """INSERT OR REPLACE INTO academy_registry
               (id, name, symbol, interval_min, archetype, graduation_level,
                wfo_return, wfo_sharpe, dsr, drills_passed, drills_total, last_drill_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [a["id"], a.get("name"), a.get("symbol"), int(a.get("interval_min") or 15),
             a.get("archetype"), a.get("graduation_level"), _f(a.get("wfo_return")),
             _f(a.get("wfo_sharpe")), _f(a.get("dsr")), int(_f(a.get("drills_passed"), 0)),
             int(_f(a.get("drills_total"), 5)), a.get("last_drill_ts")],
        )

    def academy_entries(self) -> List[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM academy_registry ORDER BY updated_at DESC")
        for r in rows:
            for k in ("last_drill_ts", "updated_at"):
                if r.get(k) is not None:
                    r[k] = str(r[k])
        return rows

    # ---------------------------------------------------------- scorecard slots
    def upsert_strategy_slot(self, slot: Dict[str, Any]) -> None:
        self._exec(
            """INSERT OR REPLACE INTO strategy_slots
               (strategy_id, symbol, timeframe, regime, origin, lamp, locked, favorite,
                pf_after_fees, last_job_id, verified_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                slot["strategy_id"], slot.get("symbol") or "",
                str(slot.get("timeframe") or ""), slot.get("regime") or "",
                slot.get("origin") or "academy", slot.get("lamp") or "gray",
                int(1 if slot.get("locked") else 0),
                int(1 if slot.get("favorite", True) else 0),
                _f(slot.get("pf_after_fees")),
                slot.get("last_job_id") or "",
                slot.get("verified_at"),
                float(slot.get("updated_at") or time.time()),
            ],
        )

    def get_strategy_slot(self, strategy_id: str, symbol: str, timeframe: Any,
                          regime: str = "") -> Optional[Dict[str, Any]]:
        row = self._one(
            "SELECT * FROM strategy_slots WHERE strategy_id=? AND symbol=? AND timeframe=? AND regime=?",
            [strategy_id, symbol, str(timeframe), regime or ""],
        )
        return self._slot_row(row) if row else None

    def list_strategy_slots(self, strategy_id: str = "") -> List[Dict[str, Any]]:
        if strategy_id:
            rows = self._rows(
                "SELECT * FROM strategy_slots WHERE strategy_id=? ORDER BY favorite DESC, symbol, timeframe",
                [strategy_id],
            )
        else:
            rows = self._rows("SELECT * FROM strategy_slots ORDER BY strategy_id, symbol")
        return [self._slot_row(r) for r in rows]

    @staticmethod
    def _slot_row(r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "strategy_id": r["strategy_id"],
            "symbol": r.get("symbol") or "",
            "timeframe": r.get("timeframe") or "",
            "regime": r.get("regime") or "",
            "origin": r.get("origin") or "academy",
            "lamp": r.get("lamp") or "gray",
            "locked": bool(r.get("locked")),
            "favorite": bool(r.get("favorite", 1)),
            "pf_after_fees": float(r.get("pf_after_fees") or 0.0),
            "last_job_id": r.get("last_job_id") or "",
            "verified_at": r.get("verified_at"),
            "updated_at": r.get("updated_at"),
        }

    def upsert_scorecard_header(self, header: Dict[str, Any]) -> None:
        self._exec(
            """INSERT OR REPLACE INTO strategy_scorecard
               (strategy_id, lamp, initialized_at, stage1_done, options_opened_at,
                last_init_job_id, last_pull_job_id, last_validate_job_id,
                pf_after_fees, net_pnl, trade_count, win_rate, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                header["strategy_id"],
                header.get("lamp") or "gray",
                header.get("initialized_at"),
                int(1 if header.get("stage1_done") else 0),
                header.get("options_opened_at"),
                header.get("last_init_job_id") or "",
                header.get("last_pull_job_id") or "",
                header.get("last_validate_job_id") or "",
                _f(header.get("pf_after_fees")),
                _f(header.get("net_pnl")),
                int(_f(header.get("trade_count"), 0)),
                _f(header.get("win_rate")),
                header.get("updated_at") or time.time(),
            ],
        )

    def get_scorecard_header(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        row = self._one("SELECT * FROM strategy_scorecard WHERE strategy_id=?", [strategy_id])
        return self._scorecard_row(row) if row else None

    def list_scorecard_headers(self) -> List[Dict[str, Any]]:
        return [self._scorecard_row(r) for r in self._rows("SELECT * FROM strategy_scorecard")]

    @staticmethod
    def _scorecard_row(r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "strategy_id": r["strategy_id"],
            "lamp": r.get("lamp") or "gray",
            "initialized_at": r.get("initialized_at"),
            "stage1_done": bool(r.get("stage1_done")),
            "options_opened_at": r.get("options_opened_at"),
            "last_init_job_id": r.get("last_init_job_id") or "",
            "last_pull_job_id": r.get("last_pull_job_id") or "",
            "last_validate_job_id": r.get("last_validate_job_id") or "",
            "pf_after_fees": float(r.get("pf_after_fees") or 0.0),
            "net_pnl": float(r.get("net_pnl") or 0.0),
            "trade_count": int(r.get("trade_count") or 0),
            "win_rate": float(r.get("win_rate") or 0.0),
            "updated_at": r.get("updated_at"),
        }

    def strategy_trade_kpis(self, strategy_id: str) -> Dict[str, float]:
        row = self._one(
            """SELECT COUNT(*) AS n,
                      SUM(CASE WHEN COALESCE(net_pnl_usd, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                      COALESCE(SUM(net_pnl_usd), 0) AS net_pnl,
                      COALESCE(SUM(CASE WHEN COALESCE(net_pnl_usd, 0) > 0 THEN net_pnl_usd ELSE 0 END), 0) AS gp,
                      COALESCE(SUM(CASE WHEN COALESCE(net_pnl_usd, 0) < 0 THEN ABS(net_pnl_usd) ELSE 0 END), 0) AS gl
               FROM trades WHERE strategy_id=? AND COALESCE(status, 'closed') != 'open'""",
            [strategy_id],
        ) or {}
        n = int(row.get("n") or 0)
        wins = int(row.get("wins") or 0)
        gp = float(row.get("gp") or 0.0)
        gl = float(row.get("gl") or 0.0)
        pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
        return {
            "trade_count": n,
            "win_rate": (wins / n) if n else 0.0,
            "profit_factor": pf,
            "net_pnl": float(row.get("net_pnl") or 0.0),
        }


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


_store: Optional[DuckDBStore] = None


def get_store(config=None) -> DuckDBStore:
    global _store
    if _store is None:
        from app.core.config import load_config

        cfg = config or load_config()
        _store = DuckDBStore(cfg.resolved_duckdb_path,
                             memory_limit=cfg.duckdb_memory_limit,
                             threads=cfg.duckdb_threads)
        _store._memory_limit = cfg.duckdb_memory_limit
        _store._threads = cfg.duckdb_threads
    return _store


def close_store() -> None:
    """Drop the process-wide singleton so a later startup can reconnect."""
    global _store
    store, _store = _store, None
    if store is not None:
        store.close()
