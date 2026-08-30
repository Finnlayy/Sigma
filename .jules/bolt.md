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

## 2026-08-30 - /api/logs snapshot defeated SQL SUM
**Learning:** After the 4→1 `trades()` reuse and `sum_closed_pnl`, GET /api/logs still `SELECT * LIMIT 10000` every 5–8s and passed that list into `_paper_balances` / `_build_metrics` / `_strategy_pnl`. The snapshot path skipped the SQL aggregate, so the hottest poll still paid ~37 ms (8k rows) to produce COUNT/SUM + per-strategy rollups. Only the order tape needs recent rows (80).
**Action:** On dashboard polls, default helpers to SQL aggregates (`closed_pnl_stats`, `strategy_closed_stats`). Fetch `trades()` only for the rows the UI lists. Do not pass a fat snapshot just because one field needs recent fills.
