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
## 2024-05-20 - Set.has() for O(1) lookups in React iterative methods
**Learning:** Found an $O(N \times M)$ anti-pattern in `CalendarHeatmap.tsx` where `.includes()` on an array was used inside a `.filter()` callback. When working with large sets, this creates significant iteration overhead. Also found multiple consecutive `.filter()` passes over the same array instead of doing a single $O(N)$ pass.
**Action:** When filtering arrays against a list of IDs, always cast the lookup list to a `Set` first to achieve $O(1)$ lookup time inside the loop (`new Set(ids)` then `set.has(id)`). Condense consecutive `.filter()` or `.reduce()` passes over the same array into a single `for` loop traversal.
## 2025-03-09 - [O(1) Set Lookups inside tight render loops]
**Learning:** Found O(N) array `.includes()` operations inside `.filter()` blocks during React rendering (like `selectedMultiIds.includes(s.id)` in CalendarHeatmap or `mainQuotes.includes(q)` in KrakenSymbolModal). These cause quadratic time complexity on large collections, and running them frequently can drop frames on interactive UI actions.
**Action:** Always convert lookup arrays to Sets for O(1) `.has()` checks before iterating with `.filter()`. Crucially, when doing this in a React component's body, the Set must be wrapped in `useMemo` so it's not reallocated from scratch on every render pass.
## 2024-09-12 - [RegExp and Lookup Arrays in React Render Loops]
**Learning:** Instantiating `new RegExp()` inside a tight iteration loop such as `lines.filter()` results in high overhead, as it reallocates and recompiles the expression for every item on every render cycle. Additionally, performing lookups via `.includes()` on arrays inside filter blocks adds O(N) overhead per item.
**Action:** Extract inline `new RegExp()` logic, as well as lookup arrays, and wrap them in a `useMemo` block. For arrays, convert them into `Set` instances to ensure O(1) `.has()` checks during array iterations, saving significant main-thread block time.
## 2024-05-24 - [React.memo in StrategyCard]
**Learning:** In the `ExecutionRiskPanel`, the parent component was passing inline arrow functions (`onPromote={(sid) => m8Action(sid, "promote")}`) and creating a lot of cards. Standard `React.memo` fails here because referential equality of those functions changes on every render.
**Action:** When memoizing React components that receive inline functions, write a custom `areEqual` function that compares the specific data properties rather than just using the default shallow prop comparison.

## 2025-03-01 - O(N) Sets vs Arrays for Filtering
**Learning:** Found array `.includes()` within `.filter()` on React renders (e.g. `CalendarHeatmap.tsx:103` - `strategies.filter(s => selectedMultiIds.includes(s.id))`). React re-renders might call this often, leading to O(N*M) time complexity. Also learning: just instantiating a Set inside a React render causes an allocation on every render, which is bad, so we need to use `useMemo`.
**Action:** Replaced array lookups in filters with `Set.has()` to ensure O(1) membership checks, reducing time complexity to O(N), and wrapped it in `useMemo` to prevent allocation on every render.
