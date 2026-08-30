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

## 2026-08-30 - /api/pnl/daily materialized 50k trades then discarded the window
**Learning:** CalendarHeatmap's `GET /api/pnl/daily` did `trades(limit=min(5000*n, 50_000))` (`SELECT *`) and grouped by `exit_time[:10]` in Python, then only kept the last 90 days. At 8k closed rows that was ~39 ms vs ~3 ms for `GROUP BY day` with `since=` (≈14×). The LIMIT also dropped older-in-window days once history exceeded 50k.
**Action:** Heatmap/calendar endpoints must `GROUP BY` the day key in DuckDB and push the visible date window into `WHERE`. Do not pull full trade rows just to bin them.
