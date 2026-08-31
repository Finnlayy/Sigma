# Bolt journal

## 2026-08-29 - /api/logs re-scanned trades four times
**Learning:** `GET /api/logs` is polled every 5–8s (`SigmaTerminal`, `legacyPanels`) and each helper (`_paper_balances`, `_build_metrics`, `_strategy_pnl`, plus the handler) independently called `store.trades(status="closed")`. One poll materialized up to ~36k trade rows. At 8k trades that was ~110 ms vs ~35 ms for a single scan (~3×, ~75 ms/poll).
**Action:** On dashboard/poll endpoints, fetch the closed-trade snapshot once and pass it down. Do not assume each helper should own its own `trades()` call.

## 2026-08-29 - DuckDB ART indexes on trades were unused
**Learning:** `CREATE INDEX ON trades(status, entry_time)` and `(strategy_id, status, entry_time)` did **not** change the plan: DuckDB still chose `SEQ_SCAN` for `WHERE status='closed' ORDER BY entry_time DESC` and for `strategy_id + status` filters (EXPLAIN, 200–8k rows). Columnar scan beat ART; indexes would only add write cost on every `upsert_trade`.
**Action:** Do not add DuckDB secondary indexes without an EXPLAIN that shows INDEX_SCAN. Prefer fewer queries / SQL aggregates over speculative ART indexes.

## 2026-08-29 - empty execution_mode vs SQL COALESCE
**Learning:** Python `(execution_mode or "paper")` treats `""` as paper. SQL `COALESCE(execution_mode, 'paper')` does **not** — empty string is not NULL, so a SUM filter would drop those rows. Use `COALESCE(NULLIF(execution_mode, ''), 'paper')`.
**Action:** When replacing a Python `x or default` scan with SQL, match empty-string and NULL, not just NULL.

## 2026-08-31 - SSE L2 gauges paid for a full lake_summary twice per tick
**Learning:** `/api/quant/telemetry/stream` ticks every 2s. `build_frame` filled `l2_duckdb_parquet_files` and `l2_total_mb` via two helpers that **each** called `store.lake_summary()` — `COUNT(*)` + `GROUP BY symbol, interval_sec` on `ohlcv` plus `os.walk` of parquet. The L2 fields only display parquet file count and MB. Compact/seed are the only writers; inventory does not change every 2s. Caching full `lake_summary` would also hide GET `/api/lake/summary` freshness if applied on the store.
**Action:** SSE L2 must call walk-only `parquet_inventory()` once and TTL-cache (~5s) on `TelemetryCenter`, never on the store. Leave GET `/api/lake/summary` uncached. Do not use `lake_summary()` to populate two integers.

## 2026-08-31 - Concurrent Bolt runs collide on the same hotspot
**Learning:** Two cron Bolts independently implemented parquet_inventory + 5s TTL for SSE L2. #66 merged first; the second PR conflicted as a duplicate because memories already named that hotspot.
**Action:** `git fetch origin main` before picking the daily boost. Skip work already in recent `⚡ Bolt` commits. `GET /api/logs` still did `SELECT * LIMIT 10000` every 8s after the shared-scan fix — use SQL COUNT/SUM/GROUP BY next.
