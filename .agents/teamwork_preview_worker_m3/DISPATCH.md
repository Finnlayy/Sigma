## 2026-09-21T04:33:34Z
You are teamwork_preview_worker_m3.
Your working directory is /home/finn-powers/Sigma/.agents/teamwork_preview_worker_m3.
Authoritative user request file: /home/finn-powers/Sigma/.agents/ORIGINAL_REQUEST.md
Scope document: /home/finn-powers/Sigma/PROJECT.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. An auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY INSTRUCTIONS:
1. You MUST read /home/finn-powers/Sigma/.agents/ORIGINAL_REQUEST.md first before beginning work.
2. Read the 3 Explorer reports:
   - /home/finn-powers/Sigma/.agents/teamwork_preview_explorer_m3_1/report.md (DuckDB schema & sync_budget fix)
   - /home/finn-powers/Sigma/.agents/teamwork_preview_explorer_m3_2/report.md (FastAPI lifecycle task cleanup, pytest.ini, XDG_CONFIG_HOME)
   - /home/finn-powers/Sigma/.agents/teamwork_preview_explorer_m3_3_gen2/report.md (Pipeline sizing, reliable order dispatcher fallback, market feed WS handler, test helpers)

FILE WRITE OWNERSHIP:
You own exclusively:
- pytest.ini
- app/core/duckdb_store.py
- app/server/main.py
- app/server/routes_sigma.py
- app/execution/LoopAPipeline.py
- app/execution/reliable_order_dispatcher.py
- tests/conftest.py
- tests/test_five_module_runtime.py
- tests/test_api_contract.py

OBJECTIVE:
Execute Milestone 3: Backend Reliability & 100% Automated Test Pass:
1. `pytest.ini`: Add `pythonpath = .` to resolve the 51 collection errors.
2. `app/core/duckdb_store.py`:
   - In `sync_budget()`: Remove `, favorite` from column list and values in `INSERT OR REPLACE INTO strategy_budgets`.
   - In `SCHEMA_DDL`: Remove `favorite` column from `strategy_budgets` table definition.
   - In `_migrate_schema()`: Ensure `favorite BOOLEAN DEFAULT FALSE` is added to `strategies` table if missing.
3. `app/server/main.py`:
   - In `AppState.shutdown()`: Gather only active tasks from current event loop (`[t for t in self._tasks if not t.done() and t.get_loop() == asyncio.get_running_loop()]`) and execute `self._tasks.clear()`.
   - In `_kraken_credentials_present()`: Check `Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kraken" / "config.toml"`.
4. `app/server/routes_sigma.py`:
   - In `market_feed_ws`: Wire Redis PubSub subscriptions to `f"market:candles:{symbol}"` and `"alpha:executions:live"`, bootstrap with `fetch_ohlc_with_meta`, and stream messages over websocket.
5. `app/execution/LoopAPipeline.py`:
   - In `_step_sizing`: Clamp order notional to `max_order_notional_usd` BEFORE evaluating `used + notional > max_daily`.
   - Add rollback of accumulated daily notional on pipeline rejection/failure.
   - Add `reset_daily_notional()` helper for tests.
   - Ensure `price=sig.price` is passed in `OrderRequest`.
6. `app/execution/reliable_order_dispatcher.py`:
   - When order execution is called and `live_trading` is not enabled, fall back safely to paper execution mode rather than crashing or rejecting if in simulated/test context.
7. Test Fixtures:
   - In `tests/conftest.py`: Set `os.environ["XDG_CONFIG_HOME"] = str(tmp_path_factory.mktemp("xdg-config"))` to prevent read-only lock failures.
   - In `tests/test_five_module_runtime.py`: Configure `_pipeline` helper to use `execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value`.
   - In `tests/test_api_contract.py`: Add an autouse fixture calling `LoopAPipeline.reset_daily_notional()`.

VERIFICATION:
Run full pytest suite:
`pytest -q`
ALL 917+ tests MUST pass with 0 failures, 0 errors.

OUTPUT REQUIREMENTS:
Write your implementation report to /home/finn-powers/Sigma/.agents/teamwork_preview_worker_m3/report.md.
Write handoff.md and send a completion message to the orchestrator.

## 2026-09-21T04:50:14Z
**Context**: Milestone 3 Backend Reliability Implementation
**Content**: Heartbeat check. Please provide a status update on your implementation progress across pytest.ini, duckdb_store.py, main.py, routes_sigma.py, LoopAPipeline.py, reliable_order_dispatcher.py, and conftest.py.
**Action**: Report current step and resume execution.
