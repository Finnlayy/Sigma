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

## 2026-09-10 - [O(1) Set Search & RegExp Memoization]
**Learning:** During heavy log streaming (e.g. up to 2000 lines matching via WebSocket), using `Array.includes()` for checking if a subsystem is selected or inline-compiling a regex using `new RegExp()` in a tight filtering loop causes main thread blockage and memory spikes.
**Action:** When filtering large arrays or streaming logs inside React, use `new Set()` wrapped in `useMemo` for O(1) membership testing and memoize the regex outside the loop to prevent repeated re-allocation and re-compilation on every render cycle.

## 2024-09-11 - [Optimize RegExp/Set within Array Filter loops]
**Learning:** Avoid compiling `new RegExp()` or instantiating a `new Set()` inside a tight iteration loop such as `array.filter()` during React render phases, as it reallocates and recompiles for each item, and on every render cycle.
**Action:** Memoize loop-invariant operations like building a `Set` or compiling a `RegExp` using `useMemo` outside of the `.filter()` / `.map()` blocks to prevent unnecessary reallocations and O(N) penalties.

## 2026-09-12 - Avoid Spread Operator on large series
**Learning:** Using `Math.min(...array)` and `Math.max(...array)` on large arrays (like time series in charts) can trigger "Maximum call stack size exceeded" errors and allocates intermediate arrays if `.map()` is used first.
**Action:** Always use manual iterative loops or `.reduce()` to calculate min/max over large datasets in frontend visualizations to save memory and avoid stack overflows.

## 2026-09-13 - Memoize array filtering based on inputs in React
**Learning:** Found `.filter()` chained with `.toLowerCase().includes()` inside the render logic of `StrategyLibraryPanel.tsx`, calculating the derived `visible` array directly in the render body. This unmemoized operation triggered expensive text search array filtering overhead (O(N) time with string matching inside) repeatedly on every re-render.
**Action:** Always wrap `.filter()` operations on derived arrays bounded by state or props in `useMemo` hooks (e.g. `const visible = useMemo(() => ..., [ws.strategies, filter]);`). Cache string computations like `filter.toLowerCase()` outside of the inner loop to prevent repeating string manipulations for every element on every render cycle.

## 2024-05-23 - [Regex String Allocation Optimization in Rendering Loops]
**Learning:** Replacing chained string methods (like `s.symbol.toUpperCase().includes(q)`) inside tight filtering or rendering loops with a pre-compiled case-insensitive RegExp (`new RegExp(q, 'i')`) significantly reduces memory allocation and execution time. However, user input must always be escaped (`searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')`) before passing it to `new RegExp` to avoid runtime `SyntaxError` crashes when users enter regex-reserved characters.
**Action:** When refactoring O(N) string operations inside React render loops to use RegExp, verify that any dynamic variables passed to the RegExp constructor are safely escaped to prevent application crashes.

## 2026-09-14 - Replace inline array filters calculating lengths in JSX
**Learning:** Found multiple instances of `pairOrders.filter(o => o.type === 'buy').length` executing directly inside the JSX component render output in `MarketPanel.tsx`. Although the parent array (`pairOrders`) was memoized, these `.filter()` calls still executed on every re-render (which occurs frequently due to live market ticker updates). This creates duplicated O(N) penalties during the render cycle.
**Action:** When calculating sub-group counts or simple metrics from an array for UI display, never compute them using inline `.filter().length` in the JSX. Instead, calculate the counts in a single O(N) iterative loop wrapped in a `useMemo` block, and reference the memoized counts in the JSX.

## 2024-10-24 - [Avoid inline array filters for length metrics in render]
**Learning:** Found `.filter(s => s.status === 'active').length` in `CalendarHeatmap.tsx` rendering logic, creating O(N) array overhead per render.
**Action:** Calculate simple counts using a single `for` loop inside a `useMemo` hook rather than relying on inline `.filter().length`.

## 2026-09-24 - [O(1) lookups in Recharts Custom Tooltips]
**Learning:** Recharts `Tooltip` custom `content` renderers are hot paths that trigger frequently on mouse move over charts. Performing O(N) array filtering operations (like `.filter()`) inside these functions to find matching data points for the hovered area causes rapid, expensive re-evaluations, leading to measurable chart UI lag and CPU spikes.
**Action:** Always pre-compute data maps (e.g. `Map<label, items[]>`) using `useMemo` outside the chart component for O(1) lookups. In the tooltip renderer, use `map.get(label)` instead of iterating the entire data array.
