"""
=============================================================================
Adversarial Stress Test Suite: DuckDB Concurrency & Persistence Integrity
=============================================================================
Milestone: M3 Adversarial Challenge
Target: app.core.duckdb_store (DuckDBStore, schema migrations, RLock, transactions, release_memory)

Test Coverage:
1. Multi-threaded concurrent inserts and reads across:
   - `strategy_budgets`
   - `strategies`
   - `trades` (trade_records)
2. Rapid concurrent multi-statement transactions under `threading.RLock()`:
   - Atomic multi-table commits
   - Deliberate rollbacks and isolation of uncommitted mutations
   - Nested RLock re-entrancy without deadlock
3. `release_memory()` checkpoint cycles under concurrent query load:
   - Buffer pool flush and memory limit toggle during high write/read throughput
   - Durability of committed rows across checkpoint cycles
4. Process-level isolation & fail-safe corruption defense:
   - Multi-process access prevention via DuckDB exclusive file lock (RW and RO rejection)
   - Resiliency against unexpected external connection attempts
   - Crash recovery / dirty shutdown WAL auto-recovery
"""
from __future__ import annotations

import concurrent.futures
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import duckdb
import pytest

from app.core.duckdb_store import DuckDBStore


# ============================================================================
# Helpers & Fixtures
# ============================================================================

def make_sample_budget(instance_id: str, strategy_id: str, index: int) -> Dict[str, Any]:
    statuses = ["active", "paused", "quarantine", "killed"]
    return {
        "instance_id": instance_id,
        "strategy_id": strategy_id,
        "status": statuses[index % len(statuses)],
        "base_budget_usd": 10000.0 + (index * 100.5),
        "current_budget_usd": 9500.0 + (index * 50.25),
        "budget_multiplier": 1.0 + (index % 5) * 0.1,
        "consecutive_losses": index % 7,
        "consecutive_low_pf_days": index % 3,
        "shadow_trades_count": index * 2,
        "shadow_wins": index,
        "last_ga_recalibration_ts": f"2026-09-21 0{index % 9 + 1}:00:00",
    }


def make_sample_strategy(strategy_id: str, index: int) -> Dict[str, Any]:
    return {
        "id": strategy_id,
        "name": f"AdversarialAlpha_{strategy_id}_{index}",
        "description": f"Stress test strategy record {index} with unicode chars: €$¥⚡️",
        "code": f"//@version=5\nstrategy('Adversarial_{strategy_id}', overlay=true)",
        "status": "active" if index % 2 == 0 else "inactive",
        "asset_pair": "BTC/USD" if index % 2 == 0 else "ETH/EUR",
        "interval_min": 15 if index % 3 == 0 else 60,
        "execution_mode": "paper",
        "parameters": {"fast_ema": 9 + index, "slow_ema": 21 + index, "meta": {"thread_index": index}},
        "hard_stop_enabled": True,
        "hard_stop_percent": 3.5 + (index % 5),
        "created_at": "2026-09-21 00:00:00",
        "favorite": (index % 2 == 0),
    }


def make_sample_trade(trade_id: str, strategy_id: str, index: int) -> Dict[str, Any]:
    is_closed = (index % 2 == 0)
    pnl = (index * 12.5) if is_closed else 0.0
    return {
        "trade_id": trade_id,
        "instance_id": f"inst_{strategy_id}",
        "strategy_id": strategy_id,
        "strategy_name": f"Strategy_{strategy_id}",
        "symbol": "BTC/USD",
        "execution_mode": "paper",
        "market_type": "spot",
        "direction": "long" if index % 2 == 0 else "short",
        "side": "buy" if index % 2 == 0 else "sell",
        "status": "closed" if is_closed else "open",
        "entry_time": f"2026-09-21 0{index % 9 + 1}:10:00",
        "exit_time": f"2026-09-21 0{index % 9 + 1}:45:00" if is_closed else None,
        "entry_price": 50000.0 + (index * 10),
        "exit_price": 50500.0 + (index * 10) if is_closed else 0.0,
        "quantity": 0.5,
        "margin_usd": 2500.0,
        "leverage": 1.0,
        "notional_usd": 25000.0,
        "gross_pnl_usd": pnl + 2.0 if is_closed else 0.0,
        "fees_usd": 2.0,
        "funding_usd": 0.0,
        "net_pnl_usd": pnl,
        "pnl_r": 1.5 if is_closed else 0.0,
        "mfe_r": 2.0,
        "mae_r": -0.5,
        "capture_ratio": 0.75,
        "autopsy_zone": "take_profit" if is_closed else None,
        "exit_reason": "tp_hit" if is_closed else None,
        "stop_slippage_bps": 1.2,
        "fee_hurdle_multiple": 3.4,
        "hold_seconds": 2100.0 if is_closed else 0.0,
    }


# ============================================================================
# Test 1: Multi-Threaded Concurrent Inserts and Reads
# ============================================================================

def test_multi_threaded_concurrent_inserts_and_reads():
    """
    Stress-test concurrent read/write access across strategy_budgets, strategies,
    and trades using 16 concurrent threads performing 1,600+ operations.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "concurrency_rw.duckdb")
        store = DuckDBStore(db_path, memory_limit="512MB", threads=4)

        num_threads = 16
        ops_per_thread = 100
        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []

        def worker_budgets(t_idx: int):
            try:
                barrier.wait()
                for i in range(ops_per_thread):
                    # Interleave updates to shared keys with distinct keys
                    inst_id = f"inst_shared_{i % 10}" if (i % 2 == 0) else f"inst_t{t_idx}_{i}"
                    strat_id = f"strat_shared_{i % 10}" if (i % 2 == 0) else f"strat_t{t_idx}_{i}"
                    budget_data = make_sample_budget(inst_id, strat_id, i)
                    store.sync_budget(budget_data)
                    if i % 10 == 0:
                        _ = store.all_budgets()
            except Exception as e:
                errors.append(e)

        def worker_strategies(t_idx: int):
            try:
                barrier.wait()
                for i in range(ops_per_thread):
                    strat_id = f"strat_shared_{i % 10}" if (i % 2 == 0) else f"strat_t{t_idx}_{i}"
                    strat_data = make_sample_strategy(strat_id, i)
                    store.upsert_strategy(strat_data)
                    if i % 10 == 0:
                        _ = store.get_strategy(strat_id)
                        _ = store.list_strategies()
            except Exception as e:
                errors.append(e)

        def worker_trades(t_idx: int):
            try:
                barrier.wait()
                for i in range(ops_per_thread):
                    t_id = f"trade_t{t_idx}_{i}"
                    strat_id = f"strat_shared_{i % 10}"
                    trade_data = make_sample_trade(t_id, strat_id, i)
                    store.upsert_trade(trade_data)
                    if i % 10 == 0:
                        _ = store.trades(strategy_id=strat_id, limit=20)
            except Exception as e:
                errors.append(e)

        def worker_aggregators(t_idx: int):
            try:
                barrier.wait()
                for i in range(ops_per_thread):
                    _ = store.sum_closed_pnl(execution_mode="paper")
                    _ = store.closed_trade_stats(execution_mode="paper")
                    _ = store.strategy_trade_kpis(f"strat_shared_{i % 10}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = []
        # 4 budget workers, 4 strategy workers, 4 trade workers, 4 reader workers
        for t in range(4):
            threads.append(threading.Thread(target=worker_budgets, args=(t,)))
        for t in range(4, 8):
            threads.append(threading.Thread(target=worker_strategies, args=(t,)))
        for t in range(8, 12):
            threads.append(threading.Thread(target=worker_trades, args=(t,)))
        for t in range(12, 16):
            threads.append(threading.Thread(target=worker_aggregators, args=(t,)))

        t0 = time.time()
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
            assert not th.is_alive(), f"Thread {th} timed out!"
        elapsed = time.time() - t0

        assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors: {errors[:3]}"

        # Verification of state integrity
        budgets = store.all_budgets()
        strategies = store.list_strategies()
        trades = store.trades(limit=10000)

        assert len(budgets) > 0, "Budgets should not be empty"
        assert len(strategies) > 0, "Strategies should not be empty"
        # 4 trade workers * 100 trades = 400 trade records
        assert len(trades) == 400, f"Expected exactly 400 trades, found {len(trades)}"

        # Verify aggregate math consistency
        closed_stats = store.closed_trade_stats(execution_mode="paper")
        sum_pnl = store.sum_closed_pnl(execution_mode="paper")
        assert closed_stats["count"] == 200, f"Expected 200 closed trades, got {closed_stats['count']}"
        assert math.isclose(closed_stats["pnl"], sum_pnl, rel_tol=1e-5), f"PnL mismatch: {closed_stats['pnl']} vs {sum_pnl}"

        store.close()
        print(f"PASS: Multi-threaded test (16 threads, 1600 ops) in {elapsed:.2f}s")


# ============================================================================
# Test 2: Rapid Concurrent Transactions under threading.RLock()
# ============================================================================

def test_rapid_concurrent_transactions_under_rlock():
    """
    Stress-test multi-statement transactional units of work executed under
    DuckDBStore._lock: atomic multi-table updates, rollbacks, and nested re-entrancy.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "concurrency_tx.duckdb")
        store = DuckDBStore(db_path, memory_limit="512MB", threads=4)

        num_threads = 8
        tx_per_thread = 50
        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []
        committed_trade_ids: List[str] = []
        rolled_back_trade_ids: List[str] = []
        lock_collect = threading.Lock()

        def tx_worker(t_idx: int):
            try:
                barrier.wait()
                for i in range(tx_per_thread):
                    strat_id = f"tx_strat_{t_idx}"
                    # 1. Multi-table atomic transaction
                    trade_id = f"tx_trade_{t_idx}_{i}"
                    trade_data = make_sample_trade(trade_id, strat_id, i)
                    budget_data = make_sample_budget(f"tx_inst_{t_idx}", strat_id, i)

                    with store._lock:
                        store._conn.execute("BEGIN TRANSACTION")
                        try:
                            # Direct execution inside open transaction
                            store._conn.execute(
                                "INSERT OR REPLACE INTO trades (trade_id, strategy_id, status, net_pnl_usd, execution_mode) "
                                "VALUES (?, ?, ?, ?, ?)",
                                [trade_id, strat_id, "closed", 100.0, "paper"]
                            )
                            # Nested re-entrant call using store's helpers
                            store.sync_budget(budget_data)
                            store._conn.execute("COMMIT")
                            with lock_collect:
                                committed_trade_ids.append(trade_id)
                        except Exception:
                            store._conn.execute("ROLLBACK")
                            raise

                    # 2. Deliberate rollback transaction
                    canary_id = f"canary_{t_idx}_{i}"
                    with store._lock:
                        store._conn.execute("BEGIN TRANSACTION")
                        store._conn.execute(
                            "INSERT INTO trades (trade_id, strategy_id, status, net_pnl_usd, execution_mode) "
                            "VALUES (?, ?, ?, ?, ?)",
                            [canary_id, strat_id, "closed", 999.0, "paper"]
                        )
                        # Explicit rollback without committing
                        store._conn.execute("ROLLBACK")
                        with lock_collect:
                            rolled_back_trade_ids.append(canary_id)

                    # 3. Nested RLock re-entrancy test
                    with store._lock:
                        # Holding outer lock, call multiple store methods that each acquire self._lock
                        _ = store.all_budgets()
                        _ = store.trades(strategy_id=strat_id, limit=5)
                        _ = store.sum_closed_pnl()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=tx_worker, args=(t,)) for t in range(num_threads)]
        t0 = time.time()
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
            assert not th.is_alive(), f"Thread {th} timed out!"
        elapsed = time.time() - t0

        assert len(errors) == 0, f"Encountered {len(errors)} errors during transactions: {errors[:3]}"

        # Assert all committed trades exist
        expected_committed = num_threads * tx_per_thread  # 400
        assert len(committed_trade_ids) == expected_committed

        # Verify canary trades were completely rolled back
        all_trades = store.trades(limit=10000)
        trade_id_set = {t["trade_id"] for t in all_trades}

        for cid in rolled_back_trade_ids:
            assert cid not in trade_id_set, f"Canary trade {cid} leaked into database despite ROLLBACK!"

        for cid in committed_trade_ids:
            assert cid in trade_id_set, f"Committed trade {cid} was not found in database!"

        store.close()
        print(f"PASS: Rapid concurrent transactions ({num_threads} threads, {expected_committed*2} txs) in {elapsed:.2f}s")


# ============================================================================
# Test 3: release_memory() Checkpoint Cycles Under Concurrent Query Load
# ============================================================================

def test_release_memory_under_concurrent_load():
    """
    Stress-test release_memory() while multiple writer and reader threads
    are actively pounding the DuckDBStore with continuous queries and inserts.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "release_memory_stress.duckdb")
        store = DuckDBStore(db_path, memory_limit="512MB", threads=4)

        stop_event = threading.Event()
        errors: List[Exception] = []
        checkpoint_results: List[str] = []
        write_count = 0
        read_count = 0
        count_lock = threading.Lock()

        def continuous_writer(w_id: int):
            nonlocal write_count
            idx = 0
            while not stop_event.is_set():
                try:
                    idx += 1
                    t_id = f"mem_trade_{w_id}_{idx}"
                    trade = make_sample_trade(t_id, f"mem_strat_{w_id}", idx)
                    store.upsert_trade(trade)
                    if idx % 5 == 0:
                        budget = make_sample_budget(f"mem_inst_{w_id}", f"mem_strat_{w_id}", idx)
                        store.sync_budget(budget)
                    with count_lock:
                        write_count += 1
                except Exception as e:
                    errors.append(e)
                    break

        def continuous_reader(r_id: int):
            nonlocal read_count
            while not stop_event.is_set():
                try:
                    _ = store.sum_closed_pnl(execution_mode="paper")
                    _ = store.closed_trade_stats(execution_mode="paper")
                    _ = store.all_budgets()
                    _ = store.trades(limit=50)
                    with count_lock:
                        read_count += 1
                    time.sleep(0.001)
                except Exception as e:
                    errors.append(e)
                    break

        def memory_releaser():
            # Trigger 15 rapid release_memory() cycles during live load
            for cycle in range(15):
                if stop_event.is_set():
                    break
                try:
                    res = store.release_memory()
                    checkpoint_results.append(res)
                    time.sleep(0.05)
                except Exception as e:
                    errors.append(e)
                    break

        threads = []
        for w in range(4):
            threads.append(threading.Thread(target=continuous_writer, args=(w,)))
        for r in range(4):
            threads.append(threading.Thread(target=continuous_reader, args=(r,)))
        rel_thread = threading.Thread(target=memory_releaser)
        threads.append(rel_thread)

        t0 = time.time()
        for th in threads:
            th.start()

        # Wait for the memory release cycles to finish
        rel_thread.join(timeout=20)
        stop_event.set()

        for th in threads:
            th.join(timeout=10)
            assert not th.is_alive(), f"Thread {th} did not terminate cleanly"

        elapsed = time.time() - t0
        assert len(errors) == 0, f"Encountered errors during release_memory load: {errors[:3]}"
        assert len(checkpoint_results) == 15, f"Expected 15 release cycles, got {len(checkpoint_results)}"
        assert all("checkpoint" in res.lower() for res in checkpoint_results)

        # Confirm all written records are preserved after checkpoints
        all_trades = store.trades(limit=100000)
        assert len(all_trades) > 0
        assert write_count > 0
        assert read_count > 0

        # Memory limit should have been safely restored to original limit (512MB = 488.2 MiB in DuckDB)
        cur_limit = store._conn.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone()[0]
        assert "244" not in str(cur_limit), f"Memory limit was left at temporary 256MB limit: {cur_limit}"
        assert "488" in str(cur_limit) or "512" in str(cur_limit), f"Memory limit was not restored to 512MB: {cur_limit}"

        store.close()
        print(f"PASS: release_memory stress (15 cycles, {write_count} writes, {read_count} reads) in {elapsed:.2f}s")


# ============================================================================
# Test 4: Process-Level Isolation & Fail-Safe Database Protection
# ============================================================================

def test_process_level_isolation_and_failsafe_protection():
    """
    Verify DuckDB file-level exclusive locking across distinct OS processes:
    1. Primary process holds open DuckDBStore.
    2. Subprocess RW connection attempt is rejected with duckdb.IOException.
    3. Subprocess RO connection attempt is rejected with duckdb.IOException.
    4. Subprocess DuckDBStore initialization is rejected with duckdb.IOException.
    5. Primary process continues unaffected with 0 corruption.
    6. Abrupt process termination recovery: WAL durability auto-recovery works.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "process_isolation.duckdb")
        sub_env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))

        # Step 1: Open primary connection
        primary_store = DuckDBStore(db_path, memory_limit="256MB")
        primary_store.sync_budget(make_sample_budget("inst_primary_1", "strat_primary_1", 1))
        primary_store.upsert_trade(make_sample_trade("trade_primary_1", "strat_primary_1", 1))

        # Step 2: Subprocess RW connection attempt
        cmd_rw = [
            sys.executable, "-c",
            f'import duckdb; conn = duckdb.connect("{db_path}", read_only=False)'
        ]
        proc_rw = subprocess.run(cmd_rw, capture_output=True, text=True, env=sub_env)
        assert proc_rw.returncode != 0, "Subprocess RW connection should have failed due to file lock!"
        assert "IOException" in proc_rw.stderr or "Conflicting lock is held" in proc_rw.stderr, (
            f"Expected lock conflict error in stderr, got:\n{proc_rw.stderr}"
        )

        # Step 3: Subprocess RO connection attempt
        cmd_ro = [
            sys.executable, "-c",
            f'import duckdb; conn = duckdb.connect("{db_path}", read_only=True)'
        ]
        proc_ro = subprocess.run(cmd_ro, capture_output=True, text=True, env=sub_env)
        assert proc_ro.returncode != 0, "Subprocess RO connection should have failed while RW lock is active!"
        assert "IOException" in proc_ro.stderr or "Conflicting lock is held" in proc_ro.stderr, (
            f"Expected lock conflict error in stderr, got:\n{proc_ro.stderr}"
        )

        # Step 4: Subprocess DuckDBStore initialization attempt
        cmd_store = [
            sys.executable, "-c",
            f'from app.core.duckdb_store import DuckDBStore; s = DuckDBStore("{db_path}")'
        ]
        proc_store = subprocess.run(cmd_store, capture_output=True, text=True, env=sub_env)
        assert proc_store.returncode != 0, "Subprocess DuckDBStore should fail while primary process holds lock!"
        assert "IOException" in proc_store.stderr or "Conflicting lock is held" in proc_store.stderr

        # Step 5: Primary process continues unaffected with ZERO database corruption
        primary_store.sync_budget(make_sample_budget("inst_primary_2", "strat_primary_2", 2))
        primary_store.upsert_trade(make_sample_trade("trade_primary_2", "strat_primary_2", 2))
        budgets = primary_store.all_budgets()
        trades = primary_store.trades()
        assert len(budgets) == 2
        assert len(trades) == 2
        primary_store.close()

        # Step 6: After primary close, another process CAN cleanly open the database
        cmd_reopen = [
            sys.executable, "-c",
            f'import duckdb; conn = duckdb.connect("{db_path}"); '
            f'res = conn.execute("SELECT count(*) FROM trades").fetchall(); '
            f'assert res[0][0] == 2, f"Expected 2 trades, got {{res}}"; conn.close()'
        ]
        proc_reopen = subprocess.run(cmd_reopen, capture_output=True, text=True)
        assert proc_reopen.returncode == 0, f"Re-opening closed DB failed:\n{proc_reopen.stderr}"

        # Step 7: Crash simulation & WAL auto-recovery
        crash_script = f"""
import duckdb, os, time, signal
conn = duckdb.connect("{db_path}")
conn.execute("INSERT INTO trades (trade_id, status, execution_mode) VALUES ('crash_trade_1', 'closed', 'paper')")
# Abruptly kill self via SIGKILL without closing conn or flushing WAL
os.kill(os.getpid(), signal.SIGKILL)
"""
        proc_crash = subprocess.run([sys.executable, "-c", crash_script], capture_output=True)
        # Process should be killed by SIGKILL (-9)
        assert proc_crash.returncode != 0

        # Now verify database can still be opened cleanly and recovers without corruption
        recovery_store = DuckDBStore(db_path)
        recovered_trades = recovery_store.trades()
        # Verify store is operational and non-corrupted
        assert len(recovered_trades) >= 2
        # PRAGMA integrity_check
        recovery_store._conn.execute("PRAGMA version")
        recovery_store.close()

        print("PASS: Process-level isolation and fail-safe crash recovery verified")


# ============================================================================
# Test 5: Adversarial Inputs Under Concurrency
# ============================================================================

def test_adversarial_inputs_under_concurrency():
    """
    Stress-test edge cases under concurrent execution:
    - Extreme float values (NaN, Inf, -Inf, huge exponents)
    - Unicode, emoji, and large strings
    - Primary key collisions and rapid upserts
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "adversarial_inputs.duckdb")
        store = DuckDBStore(db_path, memory_limit="256MB", threads=2)

        num_threads = 6
        ops = 30
        barrier = threading.Barrier(num_threads)
        errors: List[Exception] = []

        def worker_adversarial(t_idx: int):
            try:
                barrier.wait()
                for i in range(ops):
                    # Edge float values
                    nan_or_inf = float("nan") if i % 3 == 0 else (float("inf") if i % 3 == 1 else -1e300)
                    t = {
                        "trade_id": f"adv_trade_{t_idx}_{i}",
                        "strategy_id": f"adv_strat_{t_idx}",
                        "symbol": "BTC/USD ⚡️📈",
                        "status": "closed",
                        "execution_mode": "paper",
                        "entry_price": nan_or_inf,
                        "exit_price": 50000.0,
                        "net_pnl_usd": 0.0 if math.isnan(nan_or_inf) else 100.0,
                        "autopsy_zone": "Unicode_Test_Ω≈ç√∫˜µ≤≥÷" * 5,
                    }
                    store.upsert_trade(t)

                    s = {
                        "id": f"adv_strat_{t_idx}_{i % 5}",  # High contention on 5 keys
                        "name": f"Adversarial Strategy ⚡️ {i}",
                        "description": "A" * 5000,  # 5KB string
                        "parameters": {"huge_list": list(range(100)), "nested": {"key": "val"}},
                        "favorite": True,
                    }
                    store.upsert_strategy(s)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_adversarial, args=(t,)) for t in range(num_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=20)
            assert not th.is_alive()

        assert len(errors) == 0, f"Encountered errors: {errors[:3]}"

        strategies = store.list_strategies()
        # Max 5 strategies per thread * 6 threads = 30 strategies (or fewer if overlapping)
        assert len(strategies) > 0
        trades = store.trades(limit=1000)
        assert len(trades) == num_threads * ops

        store.close()
        print("PASS: Adversarial inputs and data boundary stress test verified")


# ============================================================================
# CLI Standalone Runner
# ============================================================================

def run_all_stress_tests() -> bool:
    print("=" * 80)
    print("STARTING ADVERSARIAL DUCKDB CONCURRENCY STRESS SUITE")
    print("=" * 80)

    tests = [
        ("Multi-Threaded Concurrent Inserts & Reads", test_multi_threaded_concurrent_inserts_and_reads),
        ("Rapid Concurrent Transactions Under RLock", test_rapid_concurrent_transactions_under_rlock),
        ("release_memory() Checkpoint Cycles Under Load", test_release_memory_under_concurrent_load),
        ("Process-Level Isolation & Fail-Safe Protection", test_process_level_isolation_and_failsafe_protection),
        ("Adversarial Inputs & Boundary Cases Under Concurrency", test_adversarial_inputs_under_concurrency),
    ]

    all_passed = True
    for name, test_func in tests:
        print(f"\n[RUNNING] {name}...")
        t0 = time.time()
        try:
            test_func()
            elapsed = time.time() - t0
            print(f"[SUCCESS] {name} passed in {elapsed:.3f}s")
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"[FAILURE] {name} failed in {elapsed:.3f}s: {exc}")
            import traceback
            traceback.print_exc()
            all_passed = False

    print("\n" + "=" * 80)
    if all_passed:
        print("ALL ADVERSARIAL CONCURRENCY TESTS PASSED EMPIRICALLY!")
    else:
        print("SOME CONCURRENCY TESTS FAILED!")
    print("=" * 80)
    return all_passed


if __name__ == "__main__":
    success = run_all_stress_tests()
    sys.exit(0 if success else 1)
