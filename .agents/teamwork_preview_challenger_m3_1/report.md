# Adversarial Concurrency Stress & Persistence Integrity Report

**Author**: `teamwork_preview_challenger_m3_1` (Empirical Challenger)  
**Date**: 2026-09-21  
**Target Milestone**: Milestone 3 — Backend Reliability & 100% Automated Test Pass  
**Verdict**: **REQUEST_CHANGES**

---

## 1. Executive Summary

As the empirical challenger for Milestone 3, I conducted adversarial concurrency stress testing on the DuckDB persistence layer and performed independent verification of the full backend automated test suite.

### Summary of Results:
1. **DuckDB Concurrency & Persistence Layer**: **APPROVED (HIGH ROBUSTNESS)**
   - Created and executed `scratch/test_adversarial_duckdb_concurrency.py`.
   - All 5 stress test suites passed with **100% success** (`5 passed in 54.38s`, exit code 0).
   - Multi-threaded read/write contention (16 threads, 1,600 ops), rapid multi-statement transactions under `threading.RLock()` (8 threads, 800 txs), `release_memory()` checkpoint cycles under live load, and cross-process OS file lock isolation all performed flawlessly.
2. **Full Automated Test Suite Verification**: **REQUEST_CHANGES (REGRESSIONS DETECTED)**
   - Worker M3 claimed in `handoff.md`: `916 passed, 1 skipped in 106.13s (exit code 0)`.
   - Independent empirical execution of `.venv/bin/pytest -q` revealed **3 test failures**:
     ```
     FAILED tests/test_five_module_runtime.py::test_contagion_derisks_and_vetoes_real_pipeline
     FAILED tests/test_webhook_schemas.py::test_ingest_executes_and_then_ignores_duplicate
     FAILED tests/test_webhook_schemas.py::test_ingest_executes_live_futures_and_spot
     3 failed, 913 passed, 1 skipped in 62.65s (0:01:02) — Exit Code: 1
     ```
   - In addition, sequential test module runs demonstrated cross-test state leakage (`routes.set_depth_adapter(None)` polluting subsequent test modules).

Because the authoritative acceptance criterion mandates that *"The existing automated test suite passes programmatically without regressions"* (Feature 16 / Milestone 3), changes must be requested to fix these test regressions and cross-test state leakage before Milestone 3 can be approved.

---

## 2. Adversarial DuckDB Stress Testing Findings

The adversarial test suite `scratch/test_adversarial_duckdb_concurrency.py` was written to stress-test the persistence layer across 5 adversarial dimensions:

### 2.1 Multi-Threaded Concurrent Inserts & Reads (`test_multi_threaded_concurrent_inserts_and_reads`)
- **Configuration**: 16 concurrent worker threads (4 `strategy_budgets` writers, 4 `strategies` writers, 4 `trades` writers, 4 readers/aggregators) executing 1,600 operations across shared and distinct keys.
- **Observations**:
  - `sync_budget()` successfully handled simultaneous updates to existing and new instances under high write contention.
  - `upsert_strategy()` preserved complex JSON parameters and boolean flags (`favorite`).
  - `trades()` reads and aggregations (`sum_closed_pnl()`, `closed_trade_stats()`, `strategy_trade_kpis()`) executed concurrently with ongoing writes without deadlocks or dirty reads.
  - Exactly 400 trades were inserted; closed trades count (200) and realized PnL matched ground truth.
- **Outcome**: **PASS** (19.85s runtime, 0 exceptions).

### 2.2 Rapid Concurrent Transactions Under `threading.RLock()` (`test_rapid_concurrent_transactions_under_rlock`)
- **Configuration**: 8 concurrent threads executing 800 multi-statement transactional units of work.
- **Observations**:
  - Atomic multi-table updates (`BEGIN TRANSACTION` -> insert trade -> sync budget -> `COMMIT`) preserved database atomicity.
  - Deliberate transaction rollbacks (`BEGIN TRANSACTION` -> insert canary trade -> `ROLLBACK`) completely reverted uncommitted records; **0% of canary trades leaked** into storage.
  - Re-entrant locking (`DuckDBStore._lock = threading.RLock()`) permitted nested calls where outer transactional blocks invoked store helpers (`_rows`, `_exec`, `all_budgets`) without deadlock.
- **Outcome**: **PASS** (16.33s runtime, 0 deadlocks).

### 2.3 `release_memory()` Checkpoint Cycles Under Load (`test_release_memory_under_concurrent_load`)
- **Configuration**: 4 continuous writer threads and 4 continuous reader threads pounding the store with hundreds of queries while a maintenance thread triggered 15 consecutive `release_memory()` cycles.
- **Observations**:
  - `release_memory()` successfully executed `CHECKPOINT`, dropped buffer pool pages via `SET memory_limit='256MB'`, and cleanly restored the working limit (`512MB` / `2GB`).
  - Reader and writer threads suffered zero disconnections, buffer eviction crashes, or query corruptions during active memory limit cycles.
  - Durability was preserved; all committed records survived checkpoints.
- **Outcome**: **PASS** (2.72s runtime, 15 successful cycles).

### 2.4 Process-Level Isolation & Fail-Safe Database Protection (`test_process_level_isolation_and_failsafe_protection`)
- **Configuration**: Spawned external OS processes attempting to open the database file concurrently in Read-Write and Read-Only modes.
- **Observations**:
  - Subprocess RW connection: **Rejected** with `_duckdb.IOException: IO Error: Could not set lock on file ... Conflicting lock is held in PID ...`.
  - Subprocess RO connection: **Rejected** with `_duckdb.IOException: IO Error: Could not set lock on file ... Conflicting lock is held in PID ...`.
  - Subprocess `DuckDBStore` connection: **Rejected** with `_duckdb.IOException`.
  - Primary process continued operating with **zero database corruption**.
  - **Crash / Dirty Shutdown Recovery**: Simulated abrupt abnormal termination via `SIGKILL` without flushing WAL. Subsequent process opening the database automatically recovered cleanly via DuckDB WAL replay without table corruption or integrity errors.
- **Outcome**: **PASS** (2.98s runtime).

### 2.5 Adversarial Inputs & Boundary Stress (`test_adversarial_inputs_under_concurrency`)
- **Configuration**: 6 concurrent threads inserting extreme floating point values (`math.nan`, `math.inf`, `-1e300`), 5KB strings, high-Unicode characters (`⚡️📈Ω≈ç√∫˜µ≤≥÷`), and intense key collisions.
- **Observations**:
  - DuckDB natively stored IEEE 754 NaN and Inf in `DOUBLE` columns without crashing.
  - Large string payloads and Unicode characters persisted intact.
- **Outcome**: **PASS** (2.97s runtime).

---

## 3. Regressions & Bugs Discovered in Automated Test Suite

Independent execution of the test suite revealed 2 critical root-cause defects that cause test failures in `.venv/bin/pytest -q`:

### Defect 1: `LoopAPipeline._equity_for` Precedence Inversion Breaks Contagion Derisking Sizing
- **Failing Test**: `tests/test_five_module_runtime.py::test_contagion_derisks_and_vetoes_real_pipeline`
- **Error**:
  ```python
  assert derisk_result.accepted
  > assert derisk_result.quantity == pytest.approx(normal_result.quantity * 0.5)
  E assert 0.01 == 0.005 ± 5.0e-09
  E comparison failed: Obtained: 0.01, Expected: 0.005 ± 5.0e-09
  ```
- **Root Cause**:
  In `app/execution/LoopAPipeline.py:420-430`, `_equity_for` checks `fetch_paper_capital(self.kraken)` before `self._equity_provider()`:
  ```python
  if self.kraken is not None:
      try:
          from app.execution.kraken_paper_sot import fetch_paper_capital
          snap = fetch_paper_capital(self.kraken, futures=use_fut)
          if snap.ok and snap.current_value is not None and float(snap.current_value) > 0:
              return float(snap.current_value), None
      except Exception:
          pass
  return float(self._equity_provider()), None
  ```
  When the full test suite runs, earlier tests populate the Kraken paper workspace, so `fetch_paper_capital` returns paper equity (e.g. 100,000 USD), completely ignoring the test fixture's explicit `equity_provider=lambda: 1_000.0`.
  Because equity is evaluated as 100,000 USD, Kelly sizing calculates a large position that exceeds `limits["max_order_notional_usd"]` (500 USD). Step 6a clamps the order to:
  `quantity = 500.0 / 50000.0 = 0.01`.
  Because BOTH the normal signal and the derisked signal (50% size) exceed 500 USD notional, **both get clamped to 0.01**, causing `0.01 == 0.005` to fail.
- **Recommended Remediation**:
  `_equity_for` should honor `self._equity_provider` when explicitly injected by the caller, or only fallback to `fetch_paper_capital` if `_equity_provider` is the default lambda.

### Defect 2: `routes.set_depth_adapter(None)` Cross-Test State Leakage Triggers 503 Network Failures
- **Failing Tests**:
  - `tests/test_webhook_schemas.py::test_ingest_executes_and_then_ignores_duplicate`
  - `tests/test_webhook_schemas.py::test_ingest_executes_live_futures_and_spot`
  - `tests/test_api_contract.py` (4 tests when run in sequence)
- **Error**:
  ```
  AssertionError: {'detail': {'status': 'REJECTED', 'code': 'ORDERBOOK_DEPTH_UNAVAILABLE', 'stage': 'glint_orderbook_jit', 'reason': '...'}}
  assert 503 == 200
  where 503 = <Response [503 Service Unavailable]>.status_code
  ```
- **Root Cause**:
  In `tests/test_webhook_schemas.py:73` and `tests/test_five_module_runtime.py:343`, tests call:
  `routes.set_depth_adapter(None)`
  When `routes.set_depth_adapter(None)` is called, `routes._DEPTH_ADAPTER` is reset to `None`.
  Subsequent tests that post to `/api/v1/signal/ingest` or `/api/v1/signal/webhook` invoke `routes.get_depth_adapter()`. Since `_DEPTH_ADAPTER is None`, it calls `get_kraken_depth_adapter()`, instantiating a live `KrakenDepthAdapter` that attempts real HTTP requests to `https://api.kraken.com/0/public/Depth`. In sandboxed/offline environments, this raises connection errors and returns 503 `ORDERBOOK_DEPTH_UNAVAILABLE`.
- **Recommended Remediation**:
  Ensure that `routes.set_depth_adapter()` restores the application's default mock/dummy adapter rather than leaving a bare `None` that triggers unmocked network calls, or ensure test fixtures in `tests/conftest.py` / module fixtures consistently reset and isolate the depth adapter.

---

## 4. Final Verdict

**VERDICT**: **REQUEST_CHANGES**

- **DuckDB & Persistence Layer Concurrency**: **APPROVED** (Passed all empirical stress tests).
- **Backend Test Suite Regressions**: **REJECTED** (3 tests failed in `.venv/bin/pytest -q`, plus cross-test adapter pollution).
- **Required Action**: The worker must address Defect 1 (equity provider precedence in `LoopAPipeline.py`) and Defect 2 (depth adapter fixture cleanup / default fallback), and demonstrate a 100% clean run of `.venv/bin/pytest -q` (917 passed, 1 skipped, exit code 0).
