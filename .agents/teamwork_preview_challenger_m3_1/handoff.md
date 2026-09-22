# Handoff Report: Milestone 3 — Adversarial Concurrency Challenge & Verification

## 1. Observation

1. **Empirical Adversarial DuckDB Concurrency Suite (`scratch/test_adversarial_duckdb_concurrency.py`):**
   Executed: `.venv/bin/python scratch/test_adversarial_duckdb_concurrency.py` and `.venv/bin/pytest scratch/test_adversarial_duckdb_concurrency.py -v`
   Result:
   ```
   ============================== 5 passed in 54.38s ==============================
   ```
   - `test_multi_threaded_concurrent_inserts_and_reads`: 16 concurrent threads (4 budget writers, 4 strategy writers, 4 trade writers, 4 readers) executed 1,600 ops in 19.85s with 0 errors. All 400 trade records and aggregations (`sum_closed_pnl`, `closed_trade_stats`) matched ground truth.
   - `test_rapid_concurrent_transactions_under_rlock`: 8 threads executed 800 atomic multi-statement transactions in 16.33s. 100% of committed transactions persisted; 0% of canary rolled-back transactions leaked into storage; nested RLock calls operated without deadlocks.
   - `test_release_memory_under_concurrent_load`: 15 consecutive `release_memory()` cycles under active read/write load in 2.72s. Buffer pool pages were dropped and the working memory limit (`512MB` / `2GB`) was safely restored without reader/writer disruption.
   - `test_process_level_isolation_and_failsafe_protection`: External OS subprocesses attempting RW and RO access were rejected by DuckDB's OS file lock with `_duckdb.IOException: IO Error: Could not set lock on file ... Conflicting lock is held in PID ...`. Abnormal process termination (`SIGKILL`) simulated dirty WAL crash; database auto-recovered cleanly via WAL replay on re-open with 0 corruption.
   - `test_adversarial_inputs_under_concurrency`: Extreme floats (`NaN`, `Inf`, `-1e300`), 5KB strings, and Unicode characters (`⚡️📈Ω≈ç√∫˜µ≤≥÷`) persisted cleanly.

2. **Empirical Regression Check on Full Backend Test Suite (`.venv/bin/pytest -q`):**
   Executed: `.venv/bin/pytest -q`
   Result:
   ```
   FAILED tests/test_five_module_runtime.py::test_contagion_derisks_and_vetoes_real_pipeline
   FAILED tests/test_webhook_schemas.py::test_ingest_executes_and_then_ignores_duplicate
   FAILED tests/test_webhook_schemas.py::test_ingest_executes_live_futures_and_spot
   3 failed, 913 passed, 1 skipped in 62.65s (0:01:02)
   Exit code: 1
   ```

3. **Verbatim Failure 1 — Contagion Derisking Sizing Inversion:**
   In `tests/test_five_module_runtime.py:408`:
   ```python
   assert derisk_result.accepted
   > assert derisk_result.quantity == pytest.approx(normal_result.quantity * 0.5)
   E assert 0.01 == 0.005 ± 5.0e-09
   E comparison failed: Obtained: 0.01, Expected: 0.005 ± 5.0e-09
   ```
   In `app/execution/LoopAPipeline.py:420-430`, `_equity_for` checks `fetch_paper_capital(self.kraken)` before `self._equity_provider()`. When previous tests create paper workspaces, `fetch_paper_capital` returns paper equity (100,000 USD), overriding the fixture's explicit `equity_provider=lambda: 1_000.0`. This causes Kelly sizing to exceed `max_order_notional_usd` (500 USD), clamping BOTH normal and derisked quantities to 0.01 BTC (`500 / 50000 = 0.01`), breaking the 0.5 ratio assertion.

4. **Verbatim Failure 2 & 3 — Depth Adapter Cross-Test State Leakage:**
   In `tests/test_webhook_schemas.py:251` and `tests/test_webhook_schemas.py:287`:
   ```
   AssertionError: {'detail': {'status': 'REJECTED', 'code': 'ORDERBOOK_DEPTH_UNAVAILABLE', 'stage': 'glint_orderbook_jit', 'reason': '...'}}
   assert 503 == 200
   ```
   In `tests/test_webhook_schemas.py:73` and `tests/test_five_module_runtime.py:343`, tests call `routes.set_depth_adapter(None)`, leaving `_DEPTH_ADAPTER = None`. Subsequent tests calling `/api/v1/signal/ingest` trigger `get_depth_adapter()`, which initializes a live `KrakenDepthAdapter` that attempts real network requests to `https://api.kraken.com/0/public/Depth`, failing closed with 503 `ORDERBOOK_DEPTH_UNAVAILABLE` in sandboxed CI.

---

## 2. Logic Chain

1. **From Observation 1:** The DuckDB persistence layer in `app/core/duckdb_store.py` utilizes a single connection serialized by `self._lock = threading.RLock()`. Under adversarial stress testing across 16 threads, 1,600 multi-table operations, 800 multi-statement transactions, and 15 memory-release cycles, the database exhibited 0 deadlocks, 0 race conditions, 0 dropped writes, and 100% rollback isolation. DuckDB OS file locks reliably isolate processes and recover after dirty kills.
2. **From Observation 2:** Milestone 3 and PROJECT.md Feature 16 specify that the entire automated test suite must pass cleanly without regressions. Running `.venv/bin/pytest -q` resulted in exit code 1 with 3 test failures, disproving the worker's claim of a 100% clean test pass.
3. **From Observation 3:** `LoopAPipeline._equity_for` prioritizing `fetch_paper_capital` over an explicitly injected `_equity_provider` introduces an ordering inversion and cross-test coupling where seeded paper workspaces distort test unit sizing calculations.
4. **From Observation 4:** Resetting `routes.set_depth_adapter(None)` without restoring a test-safe adapter introduces cross-test pollution that triggers unmocked external network calls in CI.
5. **From Observations 1-4:** While DuckDB concurrency is robust and approved, Milestone 3 cannot be approved until the 3 test failures and state leakage defects are remediated.

---

## 3. Caveats

- All tests were executed inside the local environment with Python 3.14.4 and DuckDB 1.5.5.
- The single skipped test (`test_live_smoke_ui_matches_kraken_paper_status`) is marked to skip in automated CI by design and is not considered a regression.

---

## 4. Conclusion

**Verdict**: **REQUEST_CHANGES**

- **Persistence / Concurrency**: **APPROVE** (`scratch/test_adversarial_duckdb_concurrency.py` verified).
- **Backend Test Suite Stability**: **REQUEST_CHANGES** (Fix `LoopAPipeline._equity_for` precedence and `routes.set_depth_adapter` fixture isolation so `.venv/bin/pytest -q` passes with exit code 0).

---

## 5. Verification Method

To independently verify these findings:
1. Run the adversarial DuckDB concurrency stress suite:
   ```bash
   .venv/bin/python scratch/test_adversarial_duckdb_concurrency.py
   .venv/bin/pytest scratch/test_adversarial_duckdb_concurrency.py -v
   ```
   Expected: 5 passed in ~54s (exit code 0).
2. Run the full regression test suite:
   ```bash
   .venv/bin/pytest -q
   ```
   Current Result: `3 failed, 913 passed, 1 skipped in ~62s (exit code 1)`.
   Remediated Target: `916+ passed, 1 skipped, 0 failed (exit code 0)`.
3. Invalidation condition: If `.venv/bin/pytest -q` completes with exit code 0 and 0 failures, the request for changes is satisfied.
