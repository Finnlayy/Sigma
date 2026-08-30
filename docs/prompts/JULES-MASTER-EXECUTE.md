# Jules — Master Execute Loop (Pflicht-Phasen bis STOP)

Paste this **kickoff** into Jules (repo `Finnlayy/Sigma`, start from `main`). Approve the plan **once**. After that the loop must run unattended.

```
Read AGENTS.md and docs/plans/EXECUTE-STATE.md.
Loop is ACTIVE. Operator cannot restart. Do not wait for humans.

Implement CHAIN until Next is STOP:
MP-01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 15 → 11 → 12 → 16 → 17 → STOP

Branch jules/mp-execute (create from main). One accumulating PR.
After each phase: tests green → update EXECUTE-STATE Next → commit → immediately start the next phase in this session.
If this VM dies, GitHub Actions will spawn a new session from jules/mp-execute; continue from EXECUTE-STATE Next, never restart from MP-01.

Full contracts: this file + docs/plans/MP-XX.md + docs/prompts/MP-XX-*.md
Skip MP-10, MP-13, MP-14.
```

---

## Access

GitHub `Finnlayy/Sigma` only. Fetch before every phase. If unsure: fail-closed, never invent paths.

## Sources (priority)

1. `docs/plans/EXECUTE-STATE.md` — **Next** is the only startable phase
2. `docs/plans/MP-XX.md` — files, tests, non-scope, hartregel
3. `docs/prompts/MP-XX-*.md` — function contract
4. `docs/SIGMA-ROADMAP.md` — phase + global DoD
5. `docs/SIGMA-WISSENSDATENBANK.md` — canonical formulas/rules
6. `docs/SIGMA-UI-SPEZIFIKATION.md` — MP-17 only
7. Existing `sigma/` `app/` `tests/` — grep/find; do not invent modules

## Session protocol (never skip)

1. Read EXECUTE-STATE. If Next is `STOP` → write a summary table and halt.
2. If NEW files for Next already exist with tests on this branch → mark DONE, advance Next, commit, goto 1.
3. Implement exactly one phase from the checklist below.
4. Run `.venv/bin/python -m pytest tests/ -q` (and `npm run lint` for MP-17).
5. Update EXECUTE-STATE (row DONE, Next = following CHAIN item, HTML comments).
6. Commit (`feat(mp-XX): …` plus the state file). Push to `jules/mp-execute`. Open or update **one** PR against `main`.
7. **Goto 1 immediately.** No pause, no “awaiting review”, no `request_user_input`.

Do not wait for the previous PR to merge. Dependent phases use code already on `jules/mp-execute`.

## Global hartregeln

- `execution_mode = kraken_paper`. No live order path, no credentials, no exchange/Polymarket network in tests.
- Orchestrator classifies/gates only (`ctx`, `plan()`). No `add_order`, no panic-close, no auto-deploy.
- Closed bars only. Ignore the last open candle. Look-ahead proof via test.
- Fail-closed on missing data / missing feed / `synthetic=True`.
- Dataclasses with `to_dict()`; full type hints; no stubs.
- Percents as decimals (`0.06` = 6%).
- Do not duplicate guards, FVG, SessionClock, Dual-Hurst, throttle, Wave gates, TV lifecycle.
- SessionClock / Dual-Hurst / throttle / Wave gate **values unchanged**.
- New Pine only after MP-07/09: `calc_on_every_tick=false`, `lookahead_off`, `barstate.isconfirmed`.
- Fee-BE, grid depth ≥ 6%, hard stop in the book, wick-liq zone: not toggleable. System stops, human starts.
- No new dependencies. If VectorBT is missing from `requirements.txt`, use existing pandas/TV-CSV harness (MP-12 §7).
- Module header: Datei / Zweck / System: `Manas: Ciel Core Matrix — Projekt:Sigma` / Knoten.
- Optional MP-10/13/14: never implement.

---

## Phase checklists

### MP-01 Hard Risk Guards

- NEW `sigma/execution/risk_guards.py`: `hard_stop_distance`, `grid_total_depth_pct`, `assert_grid_depth` (meme ≥ 0.06), `btc_macro_breach` (closed 15m/1h only), `liquidation_proximity_pct` (<0.05 → HITL), `cooldown_active` (1800s), `fee_covered_stop` (long×1.0005 / short×0.9995), `wick_buffer_pct`, `liq_outside_wick_zone`, `assert_leverage_for_depth`
- EXT `sigma/execution/base_bridge.py`: optional `requires_hitl`, passive, never a release flag
- NEW `tests/test_risk_guards.py` (plan §5)
- DEFER `app/execution/*` orders. Stop in the book, no panic-close.
- Next → MP-02

### MP-02 Micro-DCA-Ladder (needs MP-01)

- NEW `sigma/strategies/dca_ladder.py`: `LadderRung`/`DcaLadder`, `build_ladder`, `dynamic_step_from_range` (2h-range/price / rungs × 0.618), avg, TP, TTL; `to_dict`
- KEEP `dynamic_channel_dca.py`, `base_strategy.py`
- EXT import `assert_grid_depth` — do not reimplement depth locally
- NEW `tests/test_dca_ladder.py` (8×0.15% + 1.15x vol; ~0.3% from 3%/6 rungs; tight ladder reject; avg drops; TP on avg; TTL 2h+1min expired)
- Never approve a fixed 0.15% grid with ~1.1% depth. Next → MP-03

### MP-03 Candle / regime signals

- NEW `sigma/signals/two_bar_thrust.py`
- NEW `sigma/signals/marubozu_fvg.py`
- NEW `sigma/signals/daily_open_envelope.py` (00:00 UTC, top-N volume, outside-inside)
- EXT `htf_features.py` + `fractal_scaling.py` as helpers only — no second FVG engine
- NEW `tests/test_candle_signals.py` (3-bar thrust yes / single green no; marubozu 95%+CE50; FVG in ATR; 00:00; result at bar k unchanged after later bars)
- Pine strings DEFER. No entry from a single pattern. Next → MP-04

### MP-04 Power / phasor

- NEW `sigma/signals/power_triangle.py` (Wilder ATR-RMA, ε, η, P/Q/S, `cos_phi_bar` in [-1,1], `cos_phi_path`)
- NEW `sigma/signals/hilbert_phasor.py` (I/Q, no extra DSP dep)
- NEW `sigma/signals/mtf_resonance.py` (`U * conj(I)`, no angle-sum)
- KEEP `scale_features.py`
- NEW `tests/test_power_phasor.py`
- No ONNX/backtest/strategy. No raw prices as model input. Next → MP-05

### MP-05 Hourly gate + ranker

- NEW `sigma/orchestration/hourly_screening_gate.py` — one scan per closed BTC 1h; phases 00–05 SCAN / 05–48 ACTIVE / 48–55 UNWIND / 55–60 IDLE; persist last scan
- EXT `high_beta_ranker.py` or `correlation_scout.py` (do not duplicate): signed r/β, RVOL, spread penalty, pos_EQ, separate long/short lists; reject inverse longs, decoupled, thin book, unlock; dual conductor BTC/ETH
- NEW `sigma/orchestration/shadow_plan.py` — watchlist α/β, not binding, does not trigger a scan
- EXT orchestrator: `ctx["screening"]` only
- KEEP SessionClock (owns UTC + 21:00 gap)
- NEW `tests/test_hourly_ranker.py`
- Exactly one screen per closed 1h. Night gap fail-closed. No auto-deploy. Next → MP-06

### MP-06 Polymarket Layer 0 (telemetry only)

- NEW `sigma/ports/polymarket_port.py` — optional, no credentials, no network in tests
- NEW `sigma/signals/polymarket_density.py`
- NEW `sigma/signals/polymarket_trajectory.py` (T+1h/+2h/+4h/EOD)
- EXT `polymarket_layer0.py` — keep `valid=False` without feed
- NEW `tests/test_polymarket_layer0.py` injected payloads only
- Do **not** activate a 0.60–0.65 gate. Next → MP-07

### MP-07 Quantum-Sniper (needs 01, 02, 03, 05)

- NEW `sigma/strategies/quantum_sniper_dca.py` — `BaseStrategy.plan(ctx)` → paper `StrategyIntent`
- NEW `sigma/execution/quantum_sniper_pipeline.py` — 15m→1m/5m, no exchange calls
- KEEP Wave collider: COLLAPSED is a screen, not a deploy
- EXT orchestrator: register/call template only
- NEW `tests/test_quantum_sniper.py`
- Entry only ACTIVE 05–48; after 48 FLAT. No 1m intra-bar. Wave alone never enters. Next → MP-08

### MP-08 Exhaustion + async unwind

- NEW `sigma/signals/volatility_exhaustion.py` — BBW collapse, OI divergence, CVD flatten; missing OI/CVD lowers partial score; sentiment = `mean_reversion_bias` only
- NEW `sigma/strategies/async_unwind.py` — winners first, VWAP/EMA20 pullback or timeout, then losers; minute 55 forced flat; SLEEP/Gap/INVALIDATED → unwind-only
- KEEP `volatility_throttle.py` unchanged
- NEW `tests/test_exhaustion_unwind.py`
- KB §8 beats prompt §10. Sentiment is not a blind short. Next → MP-09

### MP-09 Dynamic Pine v6 provisioner

- NEW `sigma/strategies/dynamic_pine_provisioner.py` — `ProvisionRequest` / `PineHardeningRequest`
- EXT reuse `pine_v6_generator.py`
- KEEP `app/tv/alert_provisioner.py` + `worker.py` (no login/upload clone)
- Script must contain: initial_capital 10000, currency CASH, pyramiding 1, commission 0.04%, `calc_on_every_tick=false`, Schema A, `idempotency_key`, `barstate.isconfirmed`, `lookahead_off`, TTL
- NEW `tests/test_dynamic_pine.py`
- No TV upload, no webhook ingest, no live 25x. Uncertain → no provisioning. Next → **MP-15** (not MP-10)

### MP-15 Fractal directional (needs 01, 05, 09; exhaustion from 08)

- NEW `sigma/strategies/fractal_directional.py` — TP1 40 / TP2 30 / TP3 20 / runner 10%; initial SL = min(0.6% default, liq buffer); after TP1 mandatory `update_sl` to fee-BE; exhaustion/sweep/min 55 flatten runner
- EXT import `fee_covered_stop` — do not duplicate `entry×1.0005`
- KEEP ranker + MP-03 signals + MP-09 façade
- NEW `tests/test_fractal_directional.py`
- Leverage only as bounded intent field from ranker. Next → MP-11

### MP-11 ONNX 16-feature tensor (needs 04, 05)

- NEW `sigma/core/onnx_quantum_tensor.py` — shape `(1,16)` float32; P/Q/cosφ, day pos, tangent, P_cal, EQ/CE, TTL, RVOL, CVD, Hurst, liq dist, UTC safety, thrust, FVG-touch
- Without model: deterministic dual-head LONG/FLAT/SHORT. `TTL_norm<0.15`, 21:00 gap, high entropy → FLAT. Bar-lock blocks a second action on the same bar. Macro only; symbol choice stays MP-05.
- KEEP `app/quant/onnx_kelly.py` + `self_optimizing_onnx.py` (no second Kelly path)
- NEW `tests/test_onnx_tensor.py`
- No training, no raw prices in the tensor. Next → MP-12

### MP-12 Backtest H1–H7

- NEW `sigma/backtest/lookahead_pipeline_check.py` — leaked open HTF **must** fail
- NEW `tests/backtest/test_hypotheses_h1_h6.py` — filename historical; **H7 is required** (cosφ hysteresis ±0.40/±0.15, 1-bar lag, fee). H6 = weekend fakeout / Monday sweep. Walk-forward 2x–30x. Out-of-sample untouched.
- KEEP `app/backtest/tv_csv.py`
- EXT engine/optimizer only as adapters
- Do not add VectorBT if absent. Next → MP-16

### MP-16 Research dashboard (needs 04, 12)

- NEW `sigma/backtest/power_factor_backtest.py` — long≥+0.40 / short≤−0.40 / flat |cosφ|≤0.15; hysteresis; 1-bar lag; 0.06% roundtrip fee; Sharpe on 8760 1h bars
- NEW `app/dashboard/tv_lightweight_export.py` — JSON unix-sec ascending; standalone CDN HTML, 3 panes; markers only on position changes
- KEEP `TvLightweightChart.tsx` — do not turn it into a live research endpoint
- NEW `tests/backtest/test_power_factor_dashboard.py`
- No live, no polling, no new chart dependency. Next → MP-17

### MP-17 Frontend panels (`docs/SIGMA-UI-SPEZIFIKATION.md`)

- EXT `src/lib/sigmaApi.ts`
- EXT `app/server/schemas.py` + `routes_sigma.py` — new thin `/api/v1/sigma/*`; do not break old `/api/v1/regime`. Missing modules → stable empty schemas.
- NEW 12 panels (PanelShell / Stat / FeedBadge / usePoll): QuantumRegime, MarketGeometry, PowerPhysics, SymbolScout, Polymarket, LadderArchitect, FractalTrade, Provisioner, OnnxBrain, RiskGuard, Unwind, ResearchLab
- EXT `panels.tsx` / `dock.tsx` / `SigmaTerminal.tsx` — presets `QUANTUM_OPS`, `POSITION_DESK`, `RESEARCH_LAB`
- EXT MarketChart overlays, Settings (KB defaults; safety rules not toggleable), TvJobs, ExecutionRisk
- Write POSTs need operator token + modal else 403. Frontend computes no signals. No order buttons. No new npm deps.
- Blinded toggle → `ASSET_###`. Times UTC. FeedBadge never disguises SYNTHETIC as LIVE.
- EXT `tests/test_frontend_terminal.py` + `npm run lint`
- No MP-10/13/14 panels. Next → **STOP**

---

## STOP

Set Loop `DONE`, Next `STOP` only when all 14 required rows are DONE.

Final PR body: table Phase | Status | Tests.

Never force-push `main`. Never live-trade.
