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

## 2026-08-30 - React Render O(N^2) Anti-Patterns in UI Maps
**Learning:** Found an instance in `MetricsPanel.tsx` where `.find()` was being executed inside `.reduce()` and `.map()` iterations during render, turning a simple linear transformation into an $O(N \times M)$ scaling issue. Additionally, multiple consecutive `.reduce()` passes over the same array were found in `CalendarHeatmap.tsx`.
**Action:** Always pre-compute a `Map` (e.g. `const tickerMap = new Map()`) and wrap with `useMemo` when looking up reference data inside iterators during React renders. Use a single `.reduce()` pass when accumulating multiple stats from the same array.

## 2026-08-30 - COALESCE fallback must be 'paper', not the queried mode
**Learning:** `COALESCE(NULLIF(execution_mode, ''), ?)` with the queried mode as `?` treats `""` as live when filtering live. Python `(execution_mode or "paper") == "live"` is False for `""`. `sum_closed_pnl("paper")` hid this because both sides defaulted to paper.
**Action:** Hardcode the empty/NULL default to `'paper'`: `COALESCE(NULLIF(CAST(execution_mode AS VARCHAR), ''), 'paper') = ?`. Always test the live filter, not only paper.
