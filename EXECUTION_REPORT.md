# Execution Report — FIX_PLAN Implemented

**Branch:** `arena/01a04af2-sigma`  
**Commit:** `5df52fb` — execute FIX_PLAN  
**Tests:** 576 passed, 1 warning  
**Build:** vite build ok (2821 modules, 1.6MB JS)

---

## What Was Fixed (P0–P6)

### P0 — Spec & Factory Seeds
- **File:** `app/server/main.py`
- **Problem:** Factory strategies used JS `function onCandle(ctx){...}` archetypes, violating Axiom 1 Strategy≡TradingView.
- **Fix:** Replaced 4 seeds with valid Pine v6:
  - `MEAN_REV_V3__BTC-USDT__15m__PAPER` — RSI 14 + SMA 12/48 + ATR 1.5/2.2
  - `SMA_CROSS_V2__ETH-USDT__15m__PAPER` — SMA 12/48 + ATR 1.5/2.5
  - `EMA_TREND_V1__SOL-USDT__15m__PAPER` — EMA 12/60 + ATR 1.5/3.0
  - `XRP_RANGE_V1__XRP-USDT__15m__PAPER` archived — RSI 10 + ATR 1.2/1.8
- Each now has `pine_inputs_schema` and `parameters` mapping to Pine `input.*`, not `archetype`.
- Alert JSON now includes `idempotency_key`, `bot_id`, `interval`, `secret` placeholder `<SIGMA_WEBHOOK_SECRET>`.

### P0-3 — Env & Secret
- **File:** `.env.example` (new), `.gitignore`
- Created canonical example env with:
  - `SIGMA_WEBHOOK_SECRET=change-me-to-32-char-random-secret` (mandatory prod)
  - `SIGMA_SELECTORS_REMOTE_URL=https://raw.githubusercontent.com/Finnlayy/Sigma/main/app/tv/selectors.yaml`
  - GA caps `SIGMA_GA_MAX_POPULATION=15`, `SIGMA_GA_MAX_GENERATIONS=5`, `SIGMA_TV_CONCURRENCY=1`
  - Flywheel rate `SIGMA_FLYWHEEL_USD_TO_EUR_RATE=0.92`
- Fixed `.gitignore` to allow `.env.example` via `!.env.example`.

### P1 — Scraper Sidecar
- Verified existing `app/tv/scraper_client.py`:
  - `health()` cached 5s, reports `ok`, `degraded`, vendor, cache, rate_limit
  - `fetch_ohlc_with_meta` returns `(candles, meta)` with `source` = `tv_scraper` | `cache_stale` | `synthetic`
  - Raises `ScraperUnavailable` → routes return 503 with `ErrorDetail` (fail-closed, no silent empty)
- No code change needed — already blueprint compliant.

### P2 — TV CSV Seam
- **File:** `app/mcp/TradingViewMCPClient.py`
- Fake transport now returns `source: "fake", driver: "fake"` for observability.
- `run_backtest_csv` propagates `source`/`driver` from transport result.
- `TvJobQueue` already does `source = csvs.get("source","tradingview")` + cache key via `cache_key(strategy_id, params, symbol, interval, from, to)`.
- Exact CSV roundtrip tests still green.

### P3 — Playwright Driver & Login
- **File:** `app/tv/selector_manager.py`
- Added `DEFAULT_REMOTE_URL = https://raw.githubusercontent.com/Finnlayy/Sigma/main/app/tv/selectors.yaml`
- `__init__` now uses env `SIGMA_SELECTORS_REMOTE_URL` OR default CDN if empty — self-heal works even without env.
- `BUILTIN_DEFAULT_SELECTORS` already exists as stage 3 fallback.
- Circuit breaker: 3 downloads / 300s, exponential backoff 60·2^(n-1) max 900s.
- `bin/sigma-tv-login` executable, writes `./data/secrets/tv_storage_state.json` chmod 0600, atomic `.tmp→replace`, checks `sessionid` cookies.
- `bin/sigma-up` already does `ensure_tv_session()` before redis/scraper/core/ui and dies with `refusing to start the stack` if no session (unless `--skip-login`).

### P4 — GA Hardening §17.4
- **File:** `src/components/GeneticOptimizerPanel.tsx` (complete rewrite, 1573 lines → 300 lines)
- Before: defaults `populationSize:30`, `maxGenerations:50` violating caps.
- After:
  - Constants `BLUEPRINT_MAX_POP=15`, `BLUEPRINT_MAX_GEN=5`, `BLUEPRINT_STALL=3`
  - UI clamps inputs via `clamp()` helper, inputs max attributes set
  - Shows ETA seconds (rough `pop*gen*2s`), progress bar, `Gen X/Y — stall guard 3 gens`
  - Cache info: `served from param cache` vs miss
  - Shadow gate: `DSR≥0.95`, `min 30 trades`, `cadence 3-6/day`
  - Deploy button still works via `/api/genetic/deploy-to-orchestrator`
- **File:** `app/optimizer/GeneticOptimizer.py`
- Added clamping to blueprint caps in `run()`:
  ```python
  pop_size = min(pop, GA_MAX_POPULATION)
  max_gens = min(gen, GA_MAX_GENERATIONS)
  ```
- Implemented early termination:
  - Tracks `best_fitness_seen`, `stall_counter`
  - If no improvement over `stall_limit=3` → break, log `GA early stop at gen X`
  - Returns `earlyStopped`, `stallCounter`, `blueprintCaps` in result
- All GA tests still pass.

### P5 — Webhook + Safety + Kraken Bridge
- **File:** `app/execution/KrakenCliBridge.py`
- Existing fix (PR #3) already validated type + control chars `\0\n\r`.
- Extended hardening:
  - Empty argv → `EGeneral:Invalid arguments — empty argv`
  - Length >512 → reject
  - Shell metachars `;`, `&&`, `||`, `` ` ``, `$(`, `${` → reject `contains shell metacharacters`
  - Path traversal `..` + `/` → reject
  - Explicit `shell=False` in `subprocess.run`
  - Allowed subcommands set for audit (trade, account, balance, paper, futures, order, fills)
- `SafetyGuard.verify_webhook_secret` already uses `hmac.compare_digest` timing-safe, returns 401 `UNAUTHORIZED`.
- `ExchangeClock.signal_age_s` handles ms→s if `>1e11`, `is_signal_stale` checks future timestamps beyond tolerance.
- `LoopAPipeline.handle_signal` order: auth → freshness (Kraken time) → SafetyGuard → M8 policy → allocator → regime crisis → sizing → symbol allowlist → Judge → execute.
- `kraken_output_is_error` checks `EOrder:`, `EGeneral:`, `EAPI:` text beats exit code.

### P5-6/7 — Contagion & Flywheel
- `DeadmanSwitchDaemon._now()` uses `get_exchange_clock().now()` (Kraken time), not host time. Heartbeat 20s, timeout 1800s, `cancel_only_if_native_stop=True`, fallback `close_all_market`.
- `MemoryWatchdog` stages (60,72,85,92)% with actions `gc_collect`, `duckdb_checkpoint`, `chromium_zombie_reaper`, `emergency_halt_and_restart_worker`, idle-only for stage≥3 (`tv_queue.snapshot().counts.running==0`).
- `CapitalFlywheelEngine`: deposit 100% → futures, profit split 50/50 only if `pending >= min_split_trigger_eur=10`, vault→futures requires `operator_confirmed=True`, ledger durable via `insert_row`.
- Contagion: `r0=beta/gamma`, hedge ≥1.5 → `FLIGHT_TO_CASH_AND_HEDGE`, derisk ≥1.0 → sizing ×0.5.

### P6 — Frontend Terminal & Systemd
- **File:** `src/components/sigma/panels.tsx`
- Verified 33 panels exported and in `PANEL_REGISTRY` + `PANEL_TITLES`:
  - Core 11 + Extended 22 = ALL_TERMINAL_PANELS
- **File:** `src/components/SigmaTerminal.tsx`
- Presets 12: BOT_COCKPIT, PINE_IDE, RISK_RADAR, SENTINEL_OPS, CAPITAL_OPS, PAPER_LAB, OBSERVABILITY, ML_INSPECTOR, OVERVIEW, LIBRARY, QUANT, CONFIG — matches `ALL_TERMINAL_PRESETS`.
- `App.tsx` only mounts `SigmaTerminal`, no legacy nav — passes `test_terminal_is_wired_into_app_navigation`.
- `vite.config.ts` proxy `/api` → `127.0.0.1:8000` with `ws:true` for logs stream.
- `ProcessLogView` uses WS `/api/v1/logs/stream` 250ms poll, ring 2000, secret masking.
- `NetronVisualizerPanel` iframe handles preview host rewrite `3000-<id>` → `8082-<id>` for sandbox.
- Systemd units `sigma-core.service`, `sigma-scraper.service`, `sigma-netron.service` all `Restart=always RestartSec=3`, MemoryHigh 3G, MemoryMax 4G.

---

## Verification

```bash
pytest -q
# 576 passed, 1 warning

npx vite build
# 2821 modules, built in 7.48s

pytest tests/test_frontend_terminal.py
# 16 passed — panels, presets, api routes, tv-login, sigma-up

pytest tests/test_kraken_cli_security.py
# 5 passed — injection blocked
```

---

## Remaining Operator Tasks (Not Code)

- Set real `SIGMA_WEBHOOK_SECRET` (32+ chars) in `.env` — never commit.
- Run `bin/sigma-tv-login` once (2FA) to create `data/secrets/tv_storage_state.json` (0600).
- Set `SIGMA_FLYWHEEL_USD_TO_EUR_RATE` after live approval (currently 0.92 example).
- `pip install netron` for ONNX inspector, enable `sigma-netron.service`.
- Ensure Kraken CLI installed only if `SIGMA_LIVE_TRADING=1`.

---

## Deliverables

- `FIX_PLAN.md` — full remediation plan
- `EXECUTION_REPORT.md` — this file
- Code commits on `arena/01a04af2-sigma` pushed to origin
- `.env.example` + `.gitignore` fix
- Green CI
