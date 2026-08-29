# Bolt journal

## 2026-08-29 - /api/logs re-scanned trades four times
**Learning:** `GET /api/logs` is polled every 5–8s (`SigmaTerminal`, `legacyPanels`) and each helper (`_paper_balances`, `_build_metrics`, `_strategy_pnl`, plus the handler) independently called `store.trades(status="closed")`. One poll materialized up to ~36k trade rows. At 8k trades that was ~110 ms vs ~35 ms for a single scan (~3×, ~75 ms/poll).
**Action:** On dashboard/poll endpoints, fetch the closed-trade snapshot once and pass it down. Do not assume each helper should own its own `trades()` call.

## 2026-08-29 - DuckDB ART indexes on trades were unused
**Learning:** `CREATE INDEX ON trades(status, entry_time)` and `(strategy_id, status, entry_time)` did **not** change the plan: DuckDB still chose `SEQ_SCAN` for `WHERE status='closed' ORDER BY entry_time DESC` and for `strategy_id + status` filters (EXPLAIN, 200–8k rows). Columnar scan beat ART; indexes would only add write cost on every `upsert_trade`.
**Action:** Do not add DuckDB secondary indexes without an EXPLAIN that shows INDEX_SCAN. Prefer fewer queries / SQL aggregates over speculative ART indexes.

## 2026-08-29 - SSE telemetry double-scanned ohlcv
**Learning:** `TelemetryCenter.build_frame` (SSE every 2s) called `lake_summary()` twice (`_l2_files` + `_l2_mb`) but only reads parquet `total_files` / `total_size_mb`. Each `lake_summary` full-scans ohlcv (`COUNT` + `GROUP BY`) and holds the DuckDB lock on the asyncio loop. At 80k 1m bars that was ~9 ms/frame vs ~0.04 ms for a parquet walk. Cost grows with the lake; parquet walk does not.
**Action:** For health/SSE frames, never call `lake_summary()`. Use `lake_storage_stats()` once. Do not assume a summary helper is cheap just because the caller only reads two keys.
