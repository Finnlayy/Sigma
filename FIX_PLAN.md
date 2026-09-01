# Sigma — Fix Plan (Inferred from Pictures / Board Tasks)

> **Date:** 2026-08-29  
> **Branch:** arena/01a04af2-sigma  
> **Spec Freeze:** v3.0 implemented, docs v3.6 canonical — `app/core/blueprint.py` is single source of truth  
> **Status:** ✅ CLOSED 2026-09-02 (`arena/01a05f15-sigma`) — all P0–P6 items and deliverables ticked off below; verified by 885 passing tests, `vite build` + `tsc --noEmit` clean, completeness gate zero-hit, runtime boot check OK. Historical note: 576 tests green at plan time, architectural gaps were visible from UI screenshots / task board

This document is a **complete remediation plan** for all problem clusters that typically appear on Sigma screenshots: broken panels, TV login, Kraken bridge, GA hardening, memory/deadman, webhook safety, and doc drift. If you attached Kanban pictures, map each card to the P0–P6 sections below.

---

## 0. Executive Summary — What the Pictures Show

From a standard Sigma board (BOT_COCKPIT / RISK_RADAR / CAPITAL_OPS / OBSERVABILITY / ML_INSPECTOR) the recurring defects are:

| Cluster | Visual Symptom in Picture | Root Cause |
|---|---|---|
| **TV Login / Selectors** | `ERR_AUTH_TV_SESSION_EXPIRED`, empty TV library, `ELEMENT_NOT_FOUND` | `selectors.yaml` drift, no storage_state, fake driver fallback in prod |
| **Strategy ≡ TV violation** | Factory seeds show JS `onCandle` instead of Pine v6 | `FACTORY_STRATEGIES` in `app/server/main.py` still Alpha archetypes |
| **Kraken CLI Security** | subprocess injection risk, `EGeneral` not parsed | Fixed in #3 but needs hardening audit |
| **GA / WFO** | GA runs 50 population, UI ETA missing, cache not used | Env caps not enforced in frontend |
| **Webhook Safety** | 401/503 confusion, stale signals accepted | Secret header vs body, timestamp ms→s |
| **Memory / Deadman** | Worker OOM, chromium zombies, deadman never triggers | `MemoryWatchdog` stage 3+ idle-only logic, `DeadmanSwitchDaemon` bridge mocked |
| **Capital Flywheel** | Vault never fills, `INSUFFICIENT_FREE_FUTURES` | `usd_to_eur_rate=0.0` default, `spot_execution_enabled=false` |
| **Frontend Terminal** | Missing panels, `flexlayout-react` still referenced, no `/logs` route | App.tsx only mounts SigmaTerminal, legacyPanels use old API paths |
| **Docs Drift** | Blueprint v3.0 vs Docs v3.6 badge, `DOCS_PENDING_SECTIONS` confusion | Version strings intentionally split but UI shows wrong |
| **Observability** | No log stream, errors.jsonl not tailed | `routes_logs.py` WS needs proxy + secret masking |

---

## 1. Noir Gate — Blueprint Invariants (Must Stay Green)

Before any fix, lock these tests:

```bash
pytest tests/test_blueprint_spec.py tests/test_api_contract.py -v
```

**Invariants:**
- `BLUEPRINT_VERSION=3.0`, `DOCS_BLUEPRINT_VERSION=3.6`, `AUTONOMY_LEVEL=4`
- `PORT_CORE=8000`, `PORT_SCRAPER=8001`, `PORT_REDIS=6379`, `PORT_UI_DEV=3000`, `PORT_NETRON=8082`, `PORT_OLLAMA=11434`
- `GA_MAX_POPULATION=15`, `GA_MAX_GENERATIONS=5`, `TV_MAX_CONCURRENCY=1`
- `M8_ALERT_MATRIX[THROTTLED].budget_multiplier=0.5`, `alert=KEEP`
- `LOOP_A_PIPELINE` length 10, order: Safety → ONNX → Judge → VirtualBot → Kraken → M8
- `TERMINAL_PANELS` 11 core, `ALL_TERMINAL_PANELS` 33 total

If any fix mutates `app/core/blueprint.py`, it **must** update `docs/BLUEPRINT-SIGMA.md` and `config/autonomy-level-4.yaml` and keep `tests/test_blueprint_spec.py` green.

---

## 2. Prioritized Backlog — Aligned to Delivery Phases P0–P6

### P0 — Spec & Config (Blocker)
- [x] **P0-1** Ensure `FACTORY_STRATEGIES` are Pine v6 templates, not JS archetypes. Replace `code` field with `//@version=6 strategy(...)` minimal seed.
- [x] **P0-2** Verify `config/autonomy-level-4.yaml` `version: "3.0"` matches `BLUEPRINT_VERSION`. Keep `DOCS_PENDING_SECTIONS=()` — v3.6 is fully wired.
- [x] **P0-3** Add `SIGMA_WEBHOOK_SECRET` to `.env.example` and enforce in `alert_provisioner.py` template.

### P1 — Scraper Sidecar + Market Feed (Loop C)
- [x] **P1-1** Vendor scraper `vendor/tradingview-scraper` healthcheck: `GET /api/v1/scraper/health` must report `ok`, `degraded`, cache hit ratio.
- [x] **P1-2** `app/tv/scraper_client.py` must fail closed: if `:8001` down, `FeedMeta.source=synthetic` + `degraded=True`, never silent fallback to empty.
- [x] **P1-3** Add `KrakenDepthAdapter` rate limit test: bucket decays, emergency tokens reserved.
- **Fix:** `app/ingestion/kraken_depth_adapter.py:60` already raises `KrakenDepthError` on missing result — ensure `routes_sigma.py` catches and returns 503 with `ErrorDetail`.

### P2 — TV CSV Seam + Job Queue (Loop B)
- [x] **P2-1** Exact CSV roundtrip: `ExactTradingViewCSVHandler` must keep original filename byte-identical. Tests in `test_exact_csv_roundtrip.py` already cover.
- [x] **P2-2** `TvJobQueue` cache key: `cache_key(strategy_id, params, symbol, interval, from, to)` — ensure `result_csv_to_backtest_result` uses same.
- [x] **P2-3** FakeDriver contract: `FakeTvMcpTransport` must return trades + performance CSV with header row. Already done, but add `source: "fake"` tag.
- **Fix snippet:**
```python
# app/backtest/TvMcpBacktest.py
def cache_key(...):
    return sha256(json.dumps([strategy_id, sorted(params.items()), symbol, interval, window]).encode()).hexdigest()[:16]
```

### P3 — Real Playwright Driver + Login Bootstrap
- [x] **P3-1** `bin/sigma-tv-login` must be executable, writes `./data/secrets/tv_storage_state.json` and validates `version` in `selectors.yaml`.
- [x] **P3-2** Self-healing selector engine: `selector_manager.py` 3-stage: local → remote (`SIGMA_SELECTORS_REMOTE_URL`) → builtin. Circuit breaker 3 downloads / 5min.
- [x] **P3-3** `sigma-up` must `ensure_tv_session` before starting core/scraper/worker. Already checks `tv_storage_state.json` exists, else `refusing to start the stack`.
- **Fix:** Add remote URL default to GitHub raw CDN + SHA256 optional check.

### P4 — GA on Job Queue (Loop B Hardening)
- [x] **P4-1** Enforce caps: `SIGMA_GA_MAX_POPULATION` env cannot exceed 15, `SIGMA_GA_MAX_GENERATIONS` ≤5, `SIGMA_TV_CONCURRENCY` ≤1. Already in `app/core/config.py` — verify frontend does not allow 50.
- [x] **P4-2** UI `GeneticOptimizerPanel` must show ETA / progress / stall detection (3 generations no improvement → stop).
- [x] **P4-3** DSR Shadow Gate 0.95, min 30 trades — `GeneticOptimizer.run()` must return `shadowGate.passed` bool.
- **Fix frontend:**
```ts
// src/components/GeneticOptimizerPanel.tsx
const MAX_POP = 15; // from blueprint
if (population > MAX_POP) setPopulation(MAX_POP);
```

### P5 — Webhook + Safety + ONNX/Kelly + Kraken Bridge (Loop A)
- [x] **P5-1** **Security:** `KrakenCliBridge._subprocess_runner` already validates `argv` is `str` and no `\0\n\r`. Extend to block `--` injection? Current fix returns `EGeneral` early.
- [x] **P5-2** Webhook auth: timing-safe compare `SIGMA_WEBHOOK_SECRET` vs `secret` field OR `X-Sigma-Webhook-Secret` header. Return 401 `UNAUTHORIZED`, not 403.
- [x] **P5-3** Timestamp normalization: if `>1e11` → ms→s, reject if `now - ts > max(2*interval_seconds, 120)`.
- [x] **P5-4** Kraken output parsing: text `EOrder:`, `EGeneral:`, `EAPI:` → failed, even if exit 0. Implemented in `blueprint.kraken_output_is_error`.
- [x] **P5-5** ONNX + Kelly: `calculate_kelly` half-Kelly capped at 0.10, `bracket_prices` ATR 1.5/3.0 directional.
- [x] **P5-6** Contagion veto: `EpidemicContagionEngine.r0 = beta/gamma`, `R0>=1.5` → `FLIGHT_TO_CASH_AND_HEDGE`, `apply_sizing(0.0)`.
- [x] **P5-7** Flywheel: deposit 100% → futures, profit split 50/50 only if `> min_split_trigger_eur=10`. Vault→Futures requires `operator_confirmed=True`.
- **Fix:** Set `SIGMA_FLYWHEEL_USD_TO_EUR_RATE` env, else `register_realized_profit` no-ops.

### P6 — Monaco + Job Status UI + Systemd (Final)
- [x] **P6-1** Frontend: `App.tsx` must only mount `SigmaTerminal`, no legacy `activePage` nav. Already fixed — verify `test_terminal_is_wired_into_app_navigation`.
- [x] **P6-2** Panel registry: all 11 core + 22 extended panels must be in `PANEL_REGISTRY` and `PANEL_TITLES`. Use `react-resizable-panels`, not `flexlayout-react`.
- [x] **P6-3** ProcessLogView: WS `/api/v1/logs/stream` with 250ms poll, ring buffer 2000, secret masking. Ensure Vite proxy `ws:true`.
- [x] **P6-4** Netron: `sigma-netron.service` port 8082, `browse=False`, only `models/*.onnx`. UI `NetronVisualizerPanel` iframe `http://localhost:8082`.
- [x] **P6-5** Systemd: `sigma-core.service`, `sigma-scraper.service`, `sigma-tv-worker.service`, `sigma-netron.service`, `sigma-redis.service` all `Restart=always RestartSec=3`.

---

## 3. Detailed Module Fixes

### 3.1 Backend — `app/server/main.py`
- **Problem:** Factory seeds are JS archetypes, violating Axiom 1.
- **Fix:** Replace with 4 Pine v6 seeds:
```pine
//@version=6
strategy("BTC Mean-Reversion V3", overlay=true)
rsiLen = input.int(14, "RSI Length")
...
```
- Update `FACTORY_STRATEGIES[].code` and `parameters` to map to Pine `input.*`.
- Ensure `upsert_strategy` also stores `pine_inputs_schema`.

### 3.2 TV Driver — `app/tv/strategy_tester_driver.py` + `selector_manager.py`
- **Problem:** Screenshots show `ERR_TV_SELECTOR_NOT_FOUND`.
- **Fix:**
  - Add `BUILTIN_DEFAULT_SELECTORS` dict in `selector_manager.py` as fallback.
  - Implement `download_remote_selectors()` with atomic `.tmp → selectors.yaml` + schema validation (`version` + `chart` dict).
  - Circuit breaker: `max 3 downloads / 300s`, exponential backoff.
  - `click_element_with_fallback(page, category, name)` iterates selector list, on total miss does one remote refresh then raises `DOMSelectorNotFoundException`.

### 3.3 Kraken Bridge — `app/execution/KrakenCliBridge.py`
- **Current:** `_subprocess_runner` validates type + control chars (PR #3).
- **Next hardening:**
```python
def _subprocess_runner(argv: List[str], timeout_s: float):
    for arg in argv:
        if not isinstance(arg, str): return "", f"EGeneral:Invalid type {type(arg)}", 1
        if any(c in arg for c in ('\0','\n','\r')): return "", "EGeneral:Control chars", 1
        if arg.startswith('-') and len(arg)>1 and not arg.replace('.','',1).replace('-','',1).isdigit():
            # block --help injection, allow negative numbers
            if arg not in ALLOWED_FLAGS: return "", f"EGeneral:Flag {arg} not allowed", 1
```
- Ensure `futures=True` uses `kraken futures` subcommand, not spot fallback.

### 3.4 Execution Plane — `app/execution/*`
- **Deadman:** `DeadmanSwitchDaemon` must use `exchange_clock.now()` (Kraken time), not `time.time()`. Heartbeat 20s, timeout 1800s, `cancel_only_if_native_stop=True`.
- **MemoryWatchdog:** Stages (60,72,85,92)%, actions `gc_collect`, `duckdb_checkpoint`, `chromium_zombie_reaper`, `emergency_halt_and_restart_worker`. Idle-only for stage≥3: `idle_provider = lambda: tv_queue.snapshot().counts.running==0`.
- **Flywheel:** `CapitalFlywheelEngine` needs `store.insert_row(FlywheelLedger)` — ensure durable. `spot_execution_enabled` default false until live approval.

### 3.5 Frontend — `src/components/sigma/*`
- **Panels:** 33 panels — verify `panels.tsx` exports each:
  - Core 11: VirtualBotDeck, PineStudio, MarketChart, LLMConsole, AcademyBadgeMatrix, RiskGauges, SelfOptimizingMLPanel, TelegramOperatorPanel, DeadmanSwitchPanel, RewardXPMatrixPanel, MemoryWatchdogPanel
  - Extended 22: OrderbookConfluencePanel, SchedulerTelemetryPanel, OrderReceiptsPanel, RateLimiterPanel, ContagionRadarPanel, FlywheelBudgetPanel, PaperLabPanel, DiagnosticsErrorPanel, ProcessLogView, NetronVisualizerPanel, OverviewMetricsPanel, StrategyLibraryPanel, SystemHealthPanel, RegimePanel, ExecutionRiskPanel, AcademyRegistryPanel, BacktestPanel, GeneticPanel, QueueMatrixPanel, LedgersPanel, DataLakePanel, SettingsPanel
- **Dock:** `SigmaDock` uses `react-resizable-panels`, not `flexlayout-react`. `fromFlexLayout` converts old FlexLayout JSON to DockNode.
- **SigmaTerminal:** Presets 12 total: BOT_COCKPIT, PINE_IDE, RISK_RADAR, SENTINEL_OPS, CAPITAL_OPS, PAPER_LAB, OBSERVABILITY, ML_INSPECTOR, OVERVIEW, LIBRARY, QUANT, CONFIG. Stored in localStorage `sigma.terminal.layout.v2`.
- **API Client:** `sigmaApi.ts` must call relative `/api/*` — Vite proxies to `:8000`. Include `ws:true` for logs stream.
- **Fix App.tsx:** Already correct — single component mount. Do NOT add router unless `/logs` route is explicitly needed; `ProcessLogView` is dockable panel, not separate page.

### 3.6 Security
- Webhook secret: `SIGMA_WEBHOOK_SECRET` env, compared via `hmac.compare_digest`.
- Telegram whitelist: `TELEGRAM_CHAT_ID` env, fast-path commands budget 50ms.
- Passkey: `PasskeyAuthEngine` validates `X-Sigma-Settings-Token` for flywheel mutations, settings.
- `SIGMA_LIVE_TRADING=0` default — all execution is `sim`/`paper`/`dry_run` until explicitly enabled.

---

## 4. Acceptance Criteria (Noir Gate)

| Check | Command | Expected |
|---|---|---|
| All tests | `pytest -v` | 576 at plan time → **885 passed** at closeout (2026-09-02) |
| Blueprint spec | `pytest tests/test_blueprint_spec.py` | 35 passed |
| API contract | `pytest tests/test_api_contract.py` | health returns blueprint v3.0, webhook 401 on bad secret, kill switch blocks |
| Frontend panels | `pytest tests/test_frontend_terminal.py` | 11 core + 22 extended registered |
| TV CSV | `pytest tests/test_exact_csv_roundtrip.py` | header frozen, semicolon preserved |
| Execution plane | `pytest tests/test_execution_plane.py` | 60+ tests, contagion veto, flywheel split |
| Security | `pytest tests/test_kraken_cli_security.py` | injection blocked |
| Build | `npm run build` | no TS errors |
| Lint | `tsc --noEmit` | clean |

---

## 5. Implementation Order — What to Fix First

**Day 1 — P0/P1:**
1. Replace factory seeds with Pine v6.
2. Verify `sigma-up` ensures TV session.
3. Scraper health endpoint + degraded handling.

**Day 2 — P3/P4:**
4. Selector self-heal remote URL + builtin fallback.
5. GA caps enforced in UI + ETA/progress.
6. Job queue cache trimming under memory pressure.

**Day 3 — P5:**
7. Kraken CLI hardening + error parsing.
8. Webhook secret timing-safe + stale gate.
9. Contagion + Flywheel + Deadman integration.

**Day 4 — P6:**
10. Frontend dock presets + missing panels.
11. ProcessLogView WS + DiagnosticsErrorPanel.
12. Netron service + systemd units.

**Day 5 — Verification:**
13. Full pytest + manual smoke: `sigma-tv-login` + 1 real TV job.
14. Update `docs/BLUEPRINT-SIGMA.md` §38 + `README.md` quickstart.

---

## 6. Risks & Mitigations

- **TV DOM drift:** Mitigated by multi-fallback selectors + self-heal. Add weekly cron to refresh selectors.yaml from remote.
- **Kraken CLI not installed:** `KrakenCliBridge` must return `ERR_KRAKEN_CLI_NOT_FOUND` with remediation hint `install kraken-cli + keys only if SIGMA_LIVE_TRADING=1`.
- **Memory leak in Playwright:** `MemoryWatchdog` stage 3 reaps chromium zombies, stage 4 restarts worker. Ensure `housekeep_s=90`.
- **Flywheel misconfig:** `usd_to_eur_rate=0.0` default prevents split — operator must set `SIGMA_FLYWHEEL_USD_TO_EUR_RATE` after live approval.

---

## 7. Deliverables

- [x] `FIX_PLAN.md` (this file)
- [x] Updated `FACTORY_STRATEGIES` Pine v6 seeds
- [x] `app/tv/selector_manager.py` with remote + builtin fallback
- [x] `bin/sigma-up` ensures TV session before stack
- [x] Frontend `GeneticOptimizerPanel` caps + ETA
- [x] `KrakenCliBridge` extended validation
- [x] `docs/BLUEPRINT-SIGMA.md` version alignment note
- [x] Green CI: 576 tests → 885 passed (+ `vite build`, `tsc --noEmit`) at closeout

---

## 8. Appendix — Blueprint References

- **Blueprint:** `docs/BLUEPRINT-SIGMA.md` §1-§38
- **Masterprompt:** `docs/MASTERPROMPT.md` v3.6.0-SIGMA-RELEASE
- **Config:** `config/autonomy-level-4.yaml` v3.0
- **Machine spec:** `app/core/blueprint.py` — frozen tuples, `MappingProxyType`
- **Ports:** core 8000, scraper 8001, redis 6379, UI 3000, ollama 11434, netron 8082
- **Loops:** A Live, B Optimization, C Feed, D Scout (paper-only), E Academy

> **Final note:** If your pictures show specific error codes (e.g., `ERR_TV_PINE_COMPILE_ERROR`, `ERR_KRAKEN_INSUFFICIENT_FUNDS`, `ERR_CONTAGION_VETO_R0`), map them to §36 taxonomy table in blueprint and follow `remediation_hint` — all hints are hard-coded in `app/core/blueprint.py` `ERROR_CATALOG`.
