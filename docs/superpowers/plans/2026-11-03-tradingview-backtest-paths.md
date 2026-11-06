# TradingView Backtest Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SIGMA a real backtest path for its Pine strategies again. First the existing TradingView browser automation, then the official TradingView MCP. Optionally a local PineTS cross-check, clearly labelled as such.

**Architecture:** TradingView's Strategy Tester stays the source of truth (Axiom 1, "Pine v6 is Single Source of Truth"). Phase 1 repairs the existing Playwright driver (`app/tv/strategy_tester_driver.py`, `app/tv/selectors.yaml`). Phase 2 adds a second transport behind the existing `TvMcpTransport` protocol (`app/mcp/TradingViewMCPClient.py`) for the official endpoint, decided by a discovery gate. Phase 3 is optional and only starts after an explicit human go. It adds Vela PineTS as a separate engine whose results never feed graduation, M8 or execution gates.

**Tech Stack:** Python 3.14 venv (FastAPI, Playwright), Node 20 (`tsx`, `node --test`), pytest, TradingView account `finnp17` (pro_realtime).

**Spec:** this document's "Decision record" (no separate spec exists; it summarises the 2026-11-03 session).

## Decision record (the spec)

| Option | Verdict | Why |
|---|---|---|
| A: official TradingView MCP `https://mcp.tradingview.com/mcp` | **Phase 2 stopped 2026-11-06** | Discovery: HTTP 401, OAuth only (`authorization_code` + PKCE S256). No `serverInfo`, no `tools/list`. `BACKTEST_TOOL: none`, `OHLC_TOOL: none`. Tasks 4–5 stay unstarted. See `docs/desktop/tv-official-mcp.md` |
| B: local container `atilaahmet/tradingview-mcp` (:8080, v1.30.0) | **Not a backtest path** | `backtest_strategy`/`walk_forward_backtest_strategy` run its own preset strategies on its own data, not SIGMA's Pine |
| C: Playwright Strategy Tester (exists) | **Phase 1** | The only path that provably runs SIGMA's Pine on TradingView. Fails today at `pine_editor.tab` / `properties.settings_button` |
| D: LuxAlgo Vela + `@luxalgo/vela-pinets` | **Phase 3, optional** | Runs Pine v5/v6 `strategy()` locally (PineTS broker emulator) → cross-check only. Vela core Apache-2.0 + attribution watermark; PineTS **AGPL-3.0** |
| E: `~/Downloads/TradingView_Premium_Desktop/…(password_github).zip` | **Excluded** | Password-protected ZIP (2,601 of 3,039 entries encrypted) whose listing shows `usbutils/apache2/…`, not TradingView. Typical malware delivery; a "Premium" desktop crack would also break TradingView's terms. Do not extract or run it; deleting it is the user's call |

## Global Constraints

- Paper only: `SIGMA_LIVE_TRADING=0`; never `kraken order …`/`kraken futures order …` (live) in any task or test.
- No exchange or TradingView network access in pytest. `tests/conftest.py::_hide_host_kraken_cli` stays in place. Live probes are scripts under `scripts/`, never tests.
- No fake MCP or fake driver as product behaviour. A missing endpoint or tool fails closed with `TvMcpError` / `DriverError`. The existing `"fake"` URL stays test-only.
- Result provenance is explicit: TradingView results keep `source="tradingview"`, PineTS results use `source="pinets_local"`, and only `tradingview` may set `complete_initialize(...)`, lamps or graduation.
- Named constants stay hardcoded (AGENTS.md); no "configurable later" placeholders.
- One branch per phase: `feat/tv-playwright-selectors`, `feat/tv-official-mcp`, `feat/pinets-crosscheck`. Stage paths individually; never commit `.env*`, `data/`, `node_modules`.
- Prerequisite (human): the uncommitted install fixes from 2026-11-03 (bin/sigma-up, symbol map, contagion feed, paper argv, conftest) are committed or branched first, so each phase branches from a tree that contains them.

## Review Focus

1. Expired TradingView session (cookies older than TV accepts): the probe and the worker must report `TV_SESSION_INVALID`, not `ELEMENT_NOT_FOUND`. Test in Task 1.
2. A selector chain where a *later* fallback matches the wrong element (e.g. "Save" in another dialog): the probe must report the matched index, and Task 1's YAML puts the most specific `data-name` first.
3. A Pine script with a compile error: the push job must end `PINE_COMPILE_ERROR` with the editor's messages, and no backtest job for that strategy may run afterwards. Test in Task 2.
4. An official MCP endpoint that has no backtest tool: `tv.backtest` must raise `TvMcpError("…not offered by endpoint…")` and the UI must keep showing the Playwright path. It must never return an empty success. Test in Task 4.
5. PineTS and TradingView disagree on trade count or PnL: the PineTS result is shown side by side with its label and never overwrites the TradingView scorecard. Test in Task 6.

---

## Phase 1: Playwright Strategy Tester (option C)

### Task 1: Live selector probe and repaired selectors

**Files:**
- Create: `scripts/tv-selector-probe.py`
- Modify: `app/tv/selectors.yaml` (sections `pine_editor`, `properties`, `strategy_tester`), version `"3.1"`
- Modify: `app/tv/strategy_tester_driver.py` (`open_chart`: session check)
- Test: `tests/test_tv_selectors_contract.py`

**Interfaces:**
- Produces: `probe(categories: list[str]) -> dict[str, dict[str, int | None]]` (element → index of first matching chain entry, `None` = no match), CLI `scripts/tv-selector-probe.py [--json] [--keep-open]`.
- Produces: `DriverError(code="TV_SESSION_INVALID")` raised by `PlaywrightStrategyTesterDriver.open_chart` when `window.user` has no `username`.

- [ ] **Step 1: Write the failing contract test** `tests/test_tv_selectors_contract.py`:
  - `test_required_elements_have_chains`: loading `app/tv/selectors.yaml` gives a non-empty list for every element in `REQUIRED = {"pine_editor": ["tab","code_area","save_button","add_to_chart_button","compile_error"], "properties": ["settings_button","inputs_tab","ok_button"], "strategy_tester": ["tab","trades_tab","performance_tab","export_button"]}`, and `version == "3.1"`.
  - `test_open_chart_rejects_logged_out_session`: a `PlaywrightStrategyTesterDriver` whose `page.evaluate` is stubbed to return `None` raises `DriverError` with `.code == "TV_SESSION_INVALID"` from `open_chart("BTC/USD", 15)`.
- [ ] **Step 2: Run it.** `.venv/bin/python -m pytest tests/test_tv_selectors_contract.py -q`. Expected: FAIL (version `3.0`, no session check).
- [ ] **Step 3: Write `scripts/tv-selector-probe.py`.** It launches Chromium with `data/secrets/tv_storage_state.json`, opens `https://www.tradingview.com/chart/?symbol=KRAKEN:BTCUSD`, checks `window.user.username`, then opens the Pine editor and Strategy Tester through the *current* chains. For each element it reports the first chain entry that matches (`locator(sel).first.is_visible()`). With `--keep-open` it stays open headed for manual DOM inspection. It reads only and saves nothing on TradingView.
- [ ] **Step 4: Run the probe and record the result.** `.venv/bin/python scripts/tv-selector-probe.py --json > /tmp/probe-before.json`. Expected: `pine_editor.tab` and `properties.settings_button` show `null` (reproduces today's failures).
- [ ] **Step 5: Update `app/tv/selectors.yaml`.** For every `null` element, inspect the live DOM (`--keep-open`). Prepend the current `data-name`/ARIA selector, keep the old entries behind it as fallback, and bump `version: "3.1"`.
- [ ] **Step 6: Add the session check** in `open_chart`: after navigation, evaluate `window.user && window.user.username`; if it's falsy, raise `DriverError("TradingView session not logged in — run bin/sigma-tv-login", "TV_SESSION_INVALID")`.
- [ ] **Step 7: Verify.** Run the contract test → PASS; rerun the probe → every `REQUIRED` element has a non-null index; full suite `.venv/bin/python -m pytest tests/ -q` → no new failures.
- [ ] **Step 8: Commit** `scripts/tv-selector-probe.py app/tv/selectors.yaml app/tv/strategy_tester_driver.py tests/test_tv_selectors_contract.py` with message "Repair TradingView Pine editor/tester selectors and fail on logged-out sessions".

### Task 2: Real push → backtest end-to-end and no-backtest-after-compile-error

**Files:**
- Modify: `app/tv/worker.py` (`submit`: refuse backtest/pull for a strategy whose latest push failed `PINE_COMPILE_ERROR`)
- Create: `scripts/tv-backtest-e2e.py`
- Test: `tests/test_tv_worker_push_gate.py`

**Interfaces:**
- Consumes: Task 1's selectors and `TV_SESSION_INVALID`.
- Produces: `TvJobQueue.submit(...)` returns a job with `status="failed"`, `error_code="PINE_NOT_ON_CHART"` without queuing, when the strategy's newest `push_code` job is `failed` with `PINE_COMPILE_ERROR`.
- Produces: CLI `scripts/tv-backtest-e2e.py <strategy_id> [--port 8020]`. Through the running core's API it submits `POST /api/strategies/{id}/tv/push`, waits until done, then submits `POST /api/tv/jobs/backtest` and prints status, trade count and net PnL from `result`.

- [ ] **Step 1: Write the failing test** `tests/test_tv_worker_push_gate.py::test_backtest_refused_after_compile_error`. A queue with an injected fake driver whose `push_pine_code` returns `{"compiled": False, "errors": ["missing strategy()"]}`: running the push job ends `PINE_COMPILE_ERROR`; then `submit(JOB_KIND_BACKTEST, strategy_id=same)` returns `status == "failed"`, `error_code == "PINE_NOT_ON_CHART"`, and `snapshot()["queued"]` is unchanged.
- [ ] **Step 2: Run it.** Expected: FAIL (the backtest gets queued).
- [ ] **Step 3: Implement the gate** in `TvJobQueue.submit`: find the newest `push_code` job for `strategy_id` in `self._jobs` and short-circuit only when it's `failed` with `PINE_COMPILE_ERROR`; persist the failed job like any other.
- [ ] **Step 4: Run the test → PASS**, then the full suite → no new failures.
- [ ] **Step 5: Live end-to-end (human present, paper).** With the stack up, run `.venv/bin/python scripts/tv-backtest-e2e.py 4fb58b284141`. Expected: push `done`, backtest `done`, result with `tradesCsv` row count > 0, visible in the UI's Pine IDE job list and Strategy Scorecard. Paste the output into the PR.
- [ ] **Step 6: Commit** `app/tv/worker.py scripts/tv-backtest-e2e.py tests/test_tv_worker_push_gate.py` with message "Gate TV backtests on a compiled push; add live push+backtest probe".

## Phase 2: Official TradingView MCP (option A)

### Task 3: Discovery gate

**Files:**
- Create: `scripts/tv-mcp-discover.py`
- Create: `docs/desktop/tv-official-mcp.md`

**Interfaces:**
- Produces: `docs/desktop/tv-official-mcp.md` with the endpoint's auth method, `serverInfo`, the full `tools/list` (name + input schema keys), and a verdict line `BACKTEST_TOOL: <name> | none` plus `OHLC_TOOL: <name> | none`. Tasks 4 and 5 read these names.

- [x] **Step 1: Write the probe.** It runs MCP `initialize` → `notifications/initialized` → `tools/list` over streamable HTTP against `https://mcp.tradingview.com/mcp` (same handshake as the 2026-11-03 :8080 probe), with the auth header from env `SIGMA_TV_MCP_TOKEN`. It prints JSON and calls no other tool.
- [x] **Step 2: Human step.** The user gets the access credentials from TradingView for the official MCP and exports `SIGMA_TV_MCP_TOKEN` in the shell only, never in a committed file. If TradingView only offers OAuth, record that in the doc and stop Phase 2 here. **Result:** OAuth only. Phase 2 stopped.
- [x] **Step 3: Run it** and write `docs/desktop/tv-official-mcp.md` from the output, including the two verdict lines.
- [x] **Step 4: Commit** both files with message "Record official TradingView MCP tool surface".

**Stop.** Task 3's verdict is `BACKTEST_TOOL: none` and `OHLC_TOOL: none` because the endpoint requires OAuth and returned no tool list. Do not start Task 4 or Task 5 until `scripts/tv-mcp-discover.py` is re-run with an OAuth access token and prints real tool names.

### Task 4: Standard-MCP transport behind `TvMcpTransport`

**Files:**
- Modify: `app/mcp/TradingViewMCPClient.py` (add `McpToolsTransport`, choose it in `from_env`)
- Modify: `app/core/config.py` (`tv_mcp_token` from `SIGMA_TV_MCP_TOKEN`)
- Modify: `app/security/SettingsEnvManager.py` (register `SIGMA_TV_MCP_TOKEN` as secret)
- Test: `tests/test_tv_mcp_tools_transport.py`

**Interfaces:**
- Consumes: tool names from Task 3's verdict lines.
- Produces: `class McpToolsTransport` with `__init__(self, url: str, token: str, tool_map: Mapping[str, str], timeout_s: float = 120.0)` and `call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]`. It maps `tv.ohlc`/`tv.backtest` via `tool_map` to `tools/call` and returns the tool's structured content.
- Produces: the constant `OFFICIAL_TOOL_MAP: Dict[str, str]` from Task 3 (an absent backtest tool → key missing). `from_env(url, timeout_s, token="")` returns `McpToolsTransport` when the URL host is `mcp.tradingview.com`, and `HttpJsonRpcTransport` otherwise.

- [ ] **Step 1: Write the failing tests** (an `httpx.MockTransport` stands in for the endpoint; no network):
  - `test_ohlc_maps_to_official_tool`: `call("tv.ohlc", {...})` sends `{"method": "tools/call", "params": {"name": OFFICIAL_TOOL_MAP["tv.ohlc"], …}}` with `Authorization: Bearer <token>` and `mcp-session-id` from the initialize response.
  - `test_backtest_without_tool_fails_closed`: with no `tv.backtest` in the map, `call("tv.backtest", {})` raises `TvMcpError` whose message contains `"not offered by endpoint"`.
  - `test_tool_error_is_raised`: a `result.isError: true` response raises `TvMcpError`.
  - `test_from_env_selects_transport`: the official URL → `McpToolsTransport`; `http://127.0.0.1:9` → `HttpJsonRpcTransport`.
- [ ] **Step 2: Run them.** Expected: FAIL (no `McpToolsTransport`).
- [ ] **Step 3: Implement `McpToolsTransport`.** It initializes lazily once per instance, keeps the session id, and converts the official OHLC tool's output into the `{"candles": [...]}` shape `fetch_ohlc_csv` already accepts.
- [ ] **Step 4: Wire config and settings** (`tv_mcp_token`, secret masking in Settings).
- [ ] **Step 5: Run the new tests and the full suite → PASS / no new failures.**
- [ ] **Step 6: Commit** the four files with message "Add standard-MCP transport for the official TradingView MCP".

### Task 5: Switch SIGMA off tvremix

**Files:**
- Modify: `docs/desktop/README.md` ("Local source install" section: `SIGMA_TV_MCP_URL=https://mcp.tradingview.com/mcp`, `SIGMA_TV_MCP_TOKEN`)
- Human-only: `.env` (set the new URL and token, delete `SIGMA_TV_REMIX_API_KEY` and the tvremix URL)

- [ ] **Step 1: Human edits `.env`** as above; restart via `bin/sigma-desktop --stop` then the launcher.
- [ ] **Step 2: Verify** `curl -s "http://127.0.0.1:8020/api/backtest/ohlc?pair=BTC/USD"` → 200 with candles. If Task 3 found no backtest tool, `POST /api/backtest/run` → 502 containing `not offered by endpoint` (expected, fail-closed); backtests keep running through Phase 1.
- [ ] **Step 3: Commit** the README with message "Document official TradingView MCP configuration".

## Phase 3: Vela PineTS cross-check (option D, optional)

**Gate:** start only after the user explicitly accepts both points. (a) A local engine result is allowed in the UI as a labelled *cross-check*, even though `HttpJsonRpcTransport` says "no local BacktestEngine fallback". (b) The AGPL-3.0 obligations of `pinets`/`@luxalgo/vela-pinets` apply. If either answer is no, Phase 3 is dropped.

### Task 6: PineTS sidecar and labelled cross-check endpoint

**Files:**
- Modify: `package.json` (dependencies `@luxalgo/vela`, `@luxalgo/vela-pinets`, `pinets`, pinned exact versions)
- Create: `scripts/pinets-backtest.ts`
- Create: `app/backtest/pinets_crosscheck.py`
- Modify: `app/server/routes_sigma.py` (`POST /api/backtest/pinets-crosscheck`)
- Test: `tests/desktop/pinets-backtest.test.cjs`, `tests/test_pinets_crosscheck.py`

**Interfaces:**
- Produces: CLI `npx tsx scripts/pinets-backtest.ts` reading JSON `{pine: str, bars: [{t,o,h,l,c,v}]}` from stdin and writing `{trades: [{entry_ts, exit_ts, side, qty, pnl}], net_pnl: float, trade_count: int, engine: "pinets", engine_version: str}` to stdout.
- Produces: `run_pinets_crosscheck(pine: str, bars: list[dict], runner=None) -> dict`. It returns the sidecar output plus `source="pinets_local"`, and raises `RuntimeError` on a non-zero exit or invalid JSON.
- Produces: `POST /api/backtest/pinets-crosscheck {strategy_id, symbol, interval, count}`. It takes the bars from `get_scraper_client().fetch_ohlc_with_meta` and refuses unless `meta.source == "tv_scraper"` and not degraded. It returns `{source: "pinets_local", tradingview: <latest TV backtest result or null>, pinets: <run_pinets_crosscheck result>}` and writes nothing to the scorecard.

- [ ] **Step 1: Write the failing tests.**
  - Node `pinets-backtest.test.cjs`: a `strategy()` that enters long on bar 2 and closes on bar 4 over 6 synthetic bars → `trade_count === 1`, and `pnl === close[4]-close[2]` (qty 1).
  - Python: `test_crosscheck_labels_source` (injected runner → `source == "pinets_local"`); `test_crosscheck_rejects_synthetic_bars` (`meta.source == "synthetic"` → HTTP 409); `test_crosscheck_never_touches_scorecard` (`StrategyScorecard.complete_initialize` is not called).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement the sidecar, the adapter and the route** as specified; `npm install` the pinned packages.
- [ ] **Step 4: Run** `npm run test:desktop`, `npm run lint` and `.venv/bin/python -m pytest tests/ -q` → PASS / no new failures.
- [ ] **Step 5: Live comparison (human present).** Run the cross-check for `4fb58b284141` and record TradingView vs PineTS trade count and net PnL in the PR. A difference is expected and documented, not "fixed".
- [ ] **Step 6: Commit** the files with message "Add labelled PineTS cross-check next to TradingView backtests".
