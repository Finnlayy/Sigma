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

## 2024-05-19 - [O(N) Loops Condensation and Binary Search on Frontend]
**Learning:** In backtest parsing (e.g. `tv_csv.py`), Python generator expressions and list comprehensions to calculate single values across an array of objects can create high `O(N)` repeated overhead for big backtests. In frontend React logic, matching arrays against sequential time series arrays can degrade to $O(N \times M)$ if a linear search is done for finding closest timestamps.
**Action:** Replace multiple sequential traversals calculating single aggregated metrics over trades with a single `for` loop traversal. Use Binary Search when querying values from pre-sorted time series arrays.

## 2026-09-01 - Avoid Spread Operator on Large OHLC Arrays
**Learning:** Found an instance in `MarketPanel.tsx` where a large array of OHLC chart candles was mapped and then spread into `Math.min(...prices)` and `Math.max(...prices)`. For arrays larger than the JavaScript engine's call stack limit (often around 10k-100k items), this throws `RangeError: Maximum call stack size exceeded`. It also incurs unnecessary memory allocation by creating intermediate arrays with `.map()`.
**Action:** When calculating min/max over potentially large time series or OHLC arrays on the frontend, always use a single iterative O(N) loop instead of `Math.min(...array)` or `Math.max(...array)`.
## 2026-09-02 - Use useMemo for expensive derived arrays based on props in modals
**Learning:** Component `StrategyMatrixModal` processes large datasets of trades via `.filter` and `.map` including `.sort` and string operations (like formatting times) on every render (e.g. when changing tabs). Modals tracking hundreds of orders will experience heavy slowdown.
**Action:** Always wrap `filter` and `.sort()` chains on prop arrays (e.g., arrays of trades) in `useMemo` hooks, specifying exactly what props affect them, to avoid O(N log N) or O(N) operations running on every tab switch.
## 2026-09-03 - Memoizing prop-dependent filters in panels
**Learning:** In React components like `QueueMatrixPanel.tsx` and `BacktestingPanel.tsx`, iterating and filtering large arrays via `.filter()` directly inside the render logic creates an O(N) penalty (or more with nested loop string matching like `.includes`) on every re-render. We saw instances where `filteredTrades` was calculated on every keystroke in search inputs because it was unmemoized.
**Action:** Always wrap `.filter()` operations on arrays (especially derived arrays or those bound to input search states) in `useMemo`. Cache string transformations like `.toLowerCase()` outside the `.filter` loop to further micro-optimize.
