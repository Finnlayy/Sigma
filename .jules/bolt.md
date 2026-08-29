# Bolt journal

## 2026-08-29 - /api/logs re-scanned trades four times
**Learning:** `GET /api/logs` is polled every 5–8s (`SigmaTerminal`, `legacyPanels`) and each helper (`_paper_balances`, `_build_metrics`, `_strategy_pnl`, plus the handler) independently called `store.trades(status="closed")`. One poll materialized up to ~36k trade rows. At 8k trades that was ~110 ms vs ~35 ms for a single scan (~3×, ~75 ms/poll).
**Action:** On dashboard/poll endpoints, fetch the closed-trade snapshot once and pass it down. Do not assume each helper should own its own `trades()` call.

## 2026-08-29 - DuckDB ART indexes on trades were unused
**Learning:** `CREATE INDEX ON trades(status, entry_time)` and `(strategy_id, status, entry_time)` did **not** change the plan: DuckDB still chose `SEQ_SCAN` for `WHERE status='closed' ORDER BY entry_time DESC` and for `strategy_id + status` filters (EXPLAIN, 200–8k rows). Columnar scan beat ART; indexes would only add write cost on every `upsert_trade`.
**Action:** Do not add DuckDB secondary indexes without an EXPLAIN that shows INDEX_SCAN. Prefer fewer queries / SQL aggregates over speculative ART indexes.

## 2026-08-29 - /api/logs still paid for 10k Python trade dicts
**Learning:** After the 4× `trades()` reuse, one `SELECT * LIMIT 10000` still cost ~38 ms at 8k rows (full-width dicts + timestamp `str()` per row). DuckDB `COUNT`/`SUM`/`GROUP BY` plus `LIMIT 80` for the order tape was ~4 ms (~10×). The 5000-row cap on `_strategy_pnl` also under-counted vs the 10k metrics scan.
**Action:** Dashboard poll helpers should consume SQL aggregates, not a full closed-trade materialization. Keep the row scan only for the small order list.
