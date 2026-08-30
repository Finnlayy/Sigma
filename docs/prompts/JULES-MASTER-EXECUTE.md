# Jules — Master Execute Loop (vollständig, ohne Fake)

Paste this **kickoff** into Jules (repo `Finnlayy/Sigma`, start from `main`). Approve the plan **once**. After that the loop must run unattended.

```
Read AGENTS.md and docs/plans/EXECUTE-STATE.md.
Loop is ACTIVE. Operator cannot restart. Do not wait for humans.

Implement CHAIN until Next is STOP, EACH PHASE IN FULL:
no mocks, no stubs, no placeholders, no fake returns, no TODO, no pass-bodies.
Hardcode every named constant. Implement every formula. Write every listed test
with numeric/boolean assertions. Then immediately start the next phase.

MP-01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 15 → 11 → 12 → 16 → 17 → STOP

Branch jules/mp-execute (create from main). One accumulating PR.
After each COMPLETE phase: completeness-gate + pytest green → update
EXECUTE-STATE Next → commit → start the next phase in this session.
If this VM dies, GitHub Actions respawns from jules/mp-execute at EXECUTE-STATE Next.

Contracts: this file (every step) + docs/plans/MP-XX.md + docs/prompts/MP-XX-*.md
+ cited KB sections. Skip MP-10, MP-13, MP-14.
```

---

## 0. Completeness law (read before every phase)

You implement **production algorithms**, not sketches.

### Forbidden in `sigma/`, `app/`, `src/` (except comments that quote this rule)

| Token / pattern | Why it is rejected |
|---|---|
| `TODO` `FIXME` `XXX` `HACK` | unfinished |
| `NotImplementedError` / `pass` as function body | stub |
| `placeholder` `coming soon` `not yet implemented` `stub` `dummy` | fake |
| `return 0` / `None` / `{}` / `[]` / `False` as the entire implementation of a specified function | fake |
| `unittest.mock` / monkeypatch of the unit under test in production | mock |
| `random.` in feature/strategy math | fake |
| invented ticker prices (`50000.0` as “BTC”) inside library logic | fake |
| empty `except: pass` / swallowed errors that fail-open | forbidden |
| `# type: ignore` to hide missing attributes | cover-up |

### Required

- Every function listed in the phase **exists**, with the **exact contract** (names, defaults, decimal percents).
- Arithmetic is the KB/prompt formula, not a lookalike. Named constants, not magic numbers scattered once.
- Dataclasses: all specified fields + `to_dict()` that returns a real `dict` of those fields.
- Tests assert **values** (`== 100.05`, `is True`, `abs(x-0.003)<1e-6`), never only `assert result is not None`.
- Module header: Datei / Zweck / System: `Manas: Ciel Core Matrix — Projekt:Sigma` / Knoten.
- Full type annotations. No new dependencies unless already in `requirements.txt` / `package.json`.

### Completeness gate (run before marking DONE)

```bash
# must find ZERO hits in new/changed production files of this phase
rg -n -i "TODO|FIXME|XXX|NotImplementedError|coming soon|placeholder|not yet implemented" sigma app src
# functions whose body is only pass:
rg -n -U "def .+:\n(?:    \"\"\"[\s\S]*?\"\"\"\n)?    pass$" sigma app src || true
.venv/bin/python -m pytest tests/ -q
```

If any listed function is missing or is a stub → **do not** set DONE. Finish it.

### Allowed (this is not “fake”)

- Tests construct synthetic OHLCV dicts/lists. That is required.
- Missing optional feed → `valid=False` / `available=False` / empty array **after** validating the payload. Density/trajectory/guards must still compute correctly when a complete payload is injected.
- MP-17 GET without a fachmodul: **schema-stable empty** (`valid: false`, `items: []`, `source: "none"`). Never invent LIVE numbers. Never badge LIVE.

---

## 1. Access and sources

GitHub `Finnlayy/Sigma` only. Fetch before every phase. Unsure → fail-closed, never invent paths.

Priority:

1. `docs/plans/EXECUTE-STATE.md` — **Next** only
2. **This file** — step-by-step for Next
3. `docs/plans/MP-XX.md` + `docs/prompts/MP-XX-*.md` — if this file and the prompt differ, implement **both** (union). Prompt formulas win over chat memory.
4. `docs/SIGMA-ROADMAP.md`, `docs/SIGMA-WISSENSDATENBANK.md` (cited §)
5. `docs/SIGMA-UI-SPEZIFIKATION.md` (MP-17)
6. Existing `sigma/` `app/` `tests/` — grep/find; extend, do not duplicate

---

## 2. Session protocol (never skip)

1. Read EXECUTE-STATE. Next=`STOP` → summary table, halt.
2. Open this file’s section for Next. Open the plan + prompt. Implement **every numbered step** in order.
3. Completeness gate + pytest (MP-17: also `npm run lint`).
4. Update EXECUTE-STATE (DONE + SHA, Next = next CHAIN item, HTML comments).
5. Commit `feat(mp-XX): <name>` including the state file. Push `jules/mp-execute`. One accumulating PR.
6. **Goto 1 immediately.** No review wait. No `request_user_input`.

Do not wait for merge. Later phases import code already on `jules/mp-execute`.
Do not skip a phase because “UI can show empty”. Build the library first.

---

## 3. Global hartregeln (every phase)

- `execution_mode = "kraken_paper"`. No live order path, no credentials, no network in tests.
- Orchestrator: `ctx` + `template.plan(ctx)` only. No `add_order`, no panic-close, no auto-deploy.
- Closed bars only. Drop or ignore the last open candle. Prove look-ahead with a test: result up to bar `k` unchanged after appending later bars.
- Percents as decimals: `0.06` = 6 %, `0.0005` = 0.05 %.
- Do not copy SessionClock / Dual-Hurst / throttle / Wave gate **values**.
- New Pine only after MP-07/09: `calc_on_every_tick=false`, `lookahead_off`, `barstate.isconfirmed`.
- Fee-BE, depth ≥ 6 %, hard stop in the book, wick-liq zone: not toggleable.
- Optional MP-10/13/14: no code, no panels, no “empty stubs for later”.

---

## 4. CHAIN — implement Next only, then continue

### MP-01 — Hard Risk Guards

**Read:** `docs/prompts/MP-01-hard-risk-guards.md`, KB §8 R1–6+10, §14, plan `docs/plans/MP-01.md`.

**Step 1.** Create `sigma/execution/risk_guards.py` with a real module header. All functions pure.

**Step 2.** Hardcode named constants:

- `HARD_STOP_BUFFER_PCT = 0.005`
- `MIN_MEME_GRID_DEPTH = 0.06`
- `HITL_LIQ_PROXIMITY = 0.05`
- `COOLDOWN_SECONDS = 1800`
- `FEE_COVER_OFFSET_PCT = 0.0005`
- `WICK_EXTRA_PCT = 0.01`
- `EPS = 1e-12`

**Step 3.** Implement exactly:

1. `hard_stop_distance(entry_price, liquidation_price, side, buffer_pct=0.005) -> float`  
   Long: stop = `liquidation_price * (1 + buffer_pct)` and stop < entry, stop > liq.  
   Short: stop = `liquidation_price * (1 - buffer_pct)` and stop > entry, stop < liq.  
   Invalid prices / unknown side → fail-closed (`ValueError` or documented sentinel; tests must cover).
2. `grid_total_depth_pct(ladder_prices, anchor_price, side) -> float`  
   Cumulative distance from anchor to farthest rung / anchor, decimal.  
   `assert_grid_depth(depth_pct, symbol_spec, min_meme_depth=0.06) -> None`  
   For meme-perp spec, `depth_pct < 0.06` raises / returns rejected. 8 × 0.15 % ≈ 0.011 must fail.
3. `btc_macro_breach(btc_closed_bars, support_price, side) -> bool` (or dataclass with `macro_gate_closed`)  
   Use **only closed** bars. Ignore a trailing open bar if flagged. Close < support on last closed bar → gate closed for alt **buys**.
4. `liquidation_proximity_pct(mark_price, liq_price, side) -> float` plus `needs_hitl = proximity < 0.05`.
5. `cooldown_active(last_exit_ts, now_ts, min_seconds=1800) -> bool`  
   `now - last < 1800` → True. 29 min True, 31 min False.
6. `fee_covered_stop(entry_price, side, offset_pct=0.0005) -> float`  
   Long: `entry * (1 + offset_pct)` → `fee_covered_stop(100, "long") == 100.05`.  
   Short: `entry * (1 - offset_pct)` → `99.95`. Always on the safe side of entry.
7. `wick_buffer_pct(beta, expected_btc_wick_pct, extra_pct=0.01) -> float` = `beta * expected_btc_wick_pct + extra_pct` (or as prompt: expected alt wick = β·BTC-wick; extra is the extra buffer used in leverage assert).
8. `liq_outside_wick_zone(liquidation_price, wick_low_price, side) -> bool`  
   Long: liq **below** expected wick low. Short: liq **above** expected wick high. Long + liq above wick low → False.
9. `assert_leverage_for_depth(beta, grid_depth_pct, leverage, expected_btc_wick_pct)`  
   Reject when implied liq sits inside the wick zone. Rule: required distance ≥ `grid_depth + β·btc_wick + extra`.  
   Case: depth 0.08, β=3.5, btc wick 0.01, leverage 10 full margin → **reject**. Same with low enough leverage / liq below zone → **pass**.

**Step 4.** EXT `sigma/execution/base_bridge.py`: add optional `requires_hitl: bool = False` on the existing intent/receipt/dispatch dataclass. Passive metadata only. Do not change gate numbers. Do not add order calls.

**Step 5.** `tests/test_risk_guards.py` — implement **every** bullet in the prompt Tests section with constructed bars (style `tests/test_loops_cde.py`). All asserts numeric/boolean.

**Do not:** touch `app/execution/*` order paths, strategies, orchestrator gating.

**Next → MP-02**

---

### MP-02 — Micro-DCA-Ladder

**Read:** prompt MP-02, KB §5.1, §5.6, §13.2. **Requires** `risk_guards.py` from MP-01. Import `assert_grid_depth`. If missing → implement MP-01 first, do not copy depth math.

**Step 1.** `sigma/strategies/dca_ladder.py`

Constants:

- `DEFAULT_STEP_PCT = 0.002` (0.20 % default; example uses 0.0015)
- `DEFAULT_STEP_MULT = 1.10`
- `DEFAULT_VOLUME_MULT = 1.15`
- `DEFAULT_N_SAFETY = 6`
- `DEFAULT_TP_PCT = 0.015`
- `LADDER_TTL_SECONDS = 7200`
- `SPREAD_FEE_FLOOR_PCT = 0.001`  # 0.10 %
- `RANGE_FACTOR = 0.618`

**Step 2.** Dataclasses `LadderRung` (price, margin_share, cumulative_depth_pct, volume_mult_applied) and `DcaLadder` (rungs, side, avg_price, tp_price, ttl_seconds, accepted, reject_reason) both with real `to_dict()`.

**Step 3.** Functions:

- `build_ladder(entry_price, *, side="buy", n_safety=6, step_pct=0.002, step_mult=1.10, base_margin_pct, volume_mult=1.15) -> DcaLadder`  
  Geometric steps: rung i distance = step_pct * step_mult**i (document the exact recurrence and match §13.2). Volume share grows by `volume_mult`. Prices on the safety side of entry (long below, short above).
- `dynamic_step_from_range(high_2h, low_2h, current_price, n_safety, range_factor=0.618) -> float`  
  `((high-low)/current_price * range_factor) / n_safety`. Example: range 3 %, 6 rungs → ≈ 0.003.
- `average_fill_price(filled_rungs) -> float` — volume-weighted, not simple mean.
- `take_profit_price(avg_price, side, tp_pct=0.015) -> float` — long above avg, short below avg. **Never vs entry.**
- `ttl_expired(opened_ts, now_ts, ttl_seconds=7200) -> bool`
- `validate_ladder(ladder, symbol_spec) -> DcaLadder`  
  Calls MP-01 `assert_grid_depth` (meme ≥ 0.06). First step ≥ `SPREAD_FEE_FLOOR_PCT`. On failure: `accepted=False`, reason set, **do not** invent a passing ladder.

**Step 4.** KEEP `dynamic_channel_dca.py` and `base_strategy.py` gates/intents unchanged.

**Step 5.** `tests/test_dca_ladder.py` — reproduce §13.2: entry 1.00, step 0.15 %, 8 rungs, vol 1.15 → avg after all fills **0.9899**, TP **1.0047** (tolerance documented, e.g. 1e-4). Tight 8×0.15 % rejected. Range-based ladder accepted. Avg falls each long fill. TTL 7200+60 expired.

**Next → MP-03**

---

### MP-03 — Candle / regime signals

**Read:** prompt MP-03, KB §3, §4.1–4.3, §9.1, §14. Reuse `htf_features.fvg_flags` and `fractal_scaling` CE50. **Do not** write a second FVG engine.

**Step 1.** Helper: `closed_only(bars) -> list` drops a trailing incomplete bar.

**Step 2.** `sigma/signals/two_bar_thrust.py`

Pattern on closed bars `[..., bar2, bar1, bar0]` (bar0 = last closed):

- bar2 bearish (`close < open`)
- bar1 and bar0 bullish
- `(body1 + body0) > body2` where `body = abs(close-open)`
- `bar0.close > bar2.high`

Dataclass: `detected: bool`, optional **separate** flags `support_confluence`, `ema_distance_ok`, `session_sweep` — evidence only, **never** AND-gated as hard entry. `to_dict()`.

Look-ahead: `evaluate(bars[:k+1])` equals prefix of `evaluate(bars)` for all k.

**Step 3.** `sigma/signals/marubozu_fvg.py`

- Marubozu: `abs(C-O) / (H-L+ε) >= 0.80`
- FVG size in **ATR units** (not raw ticks). Zone `(low, high)`, CE50 from existing fractal helper.
- Import/extend `htf_features.fvg_flags`.

**Step 4.** `sigma/signals/daily_open_envelope.py`

- Anchor 00:00 **UTC**. Top-N volume bars since session open define upper/lower envelope (deterministic extrema or simple linear fit — pick one, document, no ML).
- Drift = slope of envelope.
- Outside-inside: bar closes outside envelope, next bar green **and** closes back inside → `reversal=True`.
- `< N` bars since midnight → no signal (`valid=False`). Fail-closed.

**Step 5.** `tests/test_candle_signals.py` — all prompt cases, including look-ahead invariance.

No Pine strings. No orchestrator deploy.

**Next → MP-04**

---

### MP-04 — Power / phasor

**Read:** prompt MP-04, KB §9.2–9.6. Chat files `breakout_power_triangle.py` etc. are **not** source.

**Step 1.** Constants in `power_triangle.py`:

- `ATR_PERIOD = 14`
- `EPS = 1e-9`
- `ETA_SOLID = 0.85`
- `ETA_WICK = 0.30`
- `P_EXPLOSIVE = 1.2`
- `S_CLIMAX = 2.0`

**Step 2.** True range: `max(H-L, abs(H-C_prev), abs(L-C_prev))`.  
ATR = Wilder RMA: `ewm(alpha=1/14, adjust=False)` or equivalent recursive Wilder. Not SMA.

**Step 3.** Per closed bar, all of:

- `S_norm = (H-L) / ATR`
- `P_norm = abs(C-O) / ATR`
- `P_norm_signed = (C-O) / ATR`
- `wick_up = H - max(O,C)`, `wick_dn = min(O,C) - L`
- `Q_upper_norm`, `Q_lower_norm`, `Q_norm = (wick_up+wick_dn)/ATR`, `Q_bias = Q_lower - Q_upper`
- `eta_efficiency = abs(C-O) / (H-L+EPS)`
- Classify: η≥0.85 `SOLID_TREND_EXPANSION`; η<0.30 `WICK_REJECTION`; P_norm>1.2 `EXPLOSIVE_EXPANSION`; S_norm>2.0 `VOLATILITY_CLIMAX`

Every denominator uses `EPS`. No NaN. Flat bar (H==L) returns finite numbers.

**Step 4.** `cos_phi_bar(candle) = sign(C-O) * eta` clipped to [-1, 1].

**Step 5.** `cos_phi_path(close, window=20, use_true_range=False, high=None, low=None)`  
`(C_t - C_{t-N}) / sum(|ΔC|)` or `/ sum(TR)`. Path 0 → 0.0. Clip [-1, 1].

**Step 6.** `hilbert_phasor.py` — deterministic I/Q from numpy/pandas only (smoothed price = I, smoothed diff = Q or equivalent, documented). Return amplitude `sqrt(I²+Q²)`, angle `atan2(Q,I)` in degrees. Same input → same output.

**Step 7.** `mtf_resonance.py` — `S = U * conj(I)` **never** `U*I`. `delta_phi = angle(S)`, `resonance = cos(Δφ)`.  
`resonance >= 0.75` → `CONSTRUCTIVE_RESONANCE`.  
`resonance < -0.5` and HTF bull / LTF bear → `DIP_CHARGING`.

**Step 8.** Tests: marubozu η≈1 Q≈0 P≈S; long-wick η<0.3 S>P; monotonic path +1 / round-trip 0 / down -1; flat bars; phasor determinism; aligned vs opposite resonance; climax tag.

No ONNX, no strategy.

**Next → MP-05**

---

### MP-05 — Hourly gate + ranker

**Read:** prompt MP-05, KB §4.4, §6. Extend scout — do not fork a second scout.

**Step 1.** `hourly_screening_gate.py` — UTC minute-of-hour on the **closed** BTC 1h bar clock:

| Minute | Phase constant |
|---|---|
| 0 ≤ m < 5 | `SCAN_AND_DEPLOY` — at most **one** scan per bar timestamp |
| 5 ≤ m < 48 | `ACTIVE_EXECUTION` — no new screens |
| 48 ≤ m < 55 | `PRE_CLOSE_UNWIND` — no new entries |
| 55 ≤ m < 60 | `IDLE_WAIT` |

Persist `last_scan_bar_ts` via `to_dict()` / `from_dict()`. Same hour second call → `scan_allowed=False`.

**Step 2.** `high_beta_ranker.py` (or extend `correlation_scout.py` and re-export — one implementation).

Hard filters (named constants): `R_MIN = 0.75`, `BETA_ABS_MIN = 1.5`, `RVOL_MIN = 1.5`, `SPREAD_CAP = 0.0008`, `DECOUPLED_ABS_R = 0.30`, `POS_EQ_CONSOL = (0.40, 0.65)`, sniper: `BETA_SNIPER = 2.8`, `RVOL_SNIPER = 2.5`.

Rules (compute, do not stub):

- Long candidate: `r >= 0.75` **and** `beta >= 1.5`
- Short candidate: `r >= 0.75` **and** `beta <= -1.5`
- `r < 0` → never auto-long (`inverse_long_blocked`)
- `|r| < 0.30` → `decoupled`, fail-closed, not traded
- Columns: signed r, signed β, RVOL, spread penalty, `perf_24h_pct`, `pos_EQ`, reject `reason`, conductor `BTC|ETH` (higher |r|), weekend paper flag
- `score_long = beta * RVOL * r * rs_factor(perf_24h) - spread_penalty` only if r>0, β>0
- `score_short = abs(beta) * RVOL * r * rs_factor(abs(perf_24h)) - spread_penalty` only if r>0, β<0
- Separate long/short ranked lists, descending
- Rec `sniper_hedge` vs `dca` from β/RVOL/liq buffer (liq via MP-01). Field only — **not** a deploy.

**Step 3.** Dual conductor: compute r/β vs BTC **and** ETH; `conductor` = benchmark with larger |r|. Same signed rules vs that conductor.

**Step 4.** `shadow_plan.py` — watchlist + α/β scenarios, `binding=False`. Does **not** call the gate or place scans.

**Step 5.** Orchestrator: set `ctx["screening"]` only.

**Step 6.** Tests: minutes 2/20/50/57; second scan same hour blocked; next hour allowed; signed direction cases; top-gainer+RVOL+pos_EQ; thin/unlock/weekend visible; rotation only on next hourly scan.

**Next → MP-06**

---

### MP-06 — Polymarket Layer 0

**Read:** prompt MP-06, KB §2, §7, §8 R9. **Telemetry only.** Do not turn 0.60–0.65 into a live gate.

**Step 1.** `sigma/ports/polymarket_port.py` — protocol/dataclass: strikes, yes prices, volume, expiry. No API keys. No HTTP in tests. Missing config / missing fields / `synthetic=True` → `available=False`.

**Step 2.** `polymarket_density.py` — real binning: ordered strikes, differentiate yes-prices into bin probabilities, μ, corridor. Platt calibration stays in `[0, 1]`. Do not invent bins.

**Step 3.** `polymarket_trajectory.py` — series μ at T+1h, T+2h, T+4h, EOD. `dμ/dT`, bias, window. Rising μ → bullish label; flat → `CHOP`. Late window: `T * 0.75` rule from the plan — implement, do not skip.

**Step 4.** EXT `polymarket_layer0.py` — without feed keep `valid=False`. With injected valid payload, fill density+trajectory fields for real.

**Step 5.** Tests: strikes 95k/100k/105k at 0.85/0.62/0.25 → bins + μ; rising vs flat; Platt clip; no port / missing / synthetic fail-closed. **No network.**

**Next → MP-07**

---

### MP-07 — Quantum-Sniper

**Requires** MP-01, 02, 03, 05 on this branch.

**Step 1.** `quantum_sniper_pipeline.py` — pure functions: 15m wave → if COLLAPSED evaluate 1m/5m retest → ranker check → intent draft. No exchange I/O.

**Step 2.** `QuantumSniperDCA(BaseStrategy).plan(ctx) -> StrategyIntent`

ALL of these must be true for a non-FLAT intent (implement each check, no shortcuts):

1. Wave `COLLAPSED_INTO_ZONE` on closed 15m
2. LTF **retest** (not first touch): two-bar thrust **or** FVG-touch in CE50 (call MP-03)
3. Ranker: symbol on list and rec `sniper_hedge`
4. Minute phase `ACTIVE_EXECUTION` (5 ≤ m < 48). `m >= 48` → FLAT

On entry: `build_ladder` 4–6 rungs, step 0.2 %, vol 1.15, TP 1.5–3 % on **avg**; hard SL from MP-01 (0.5 % beyond liq or just beyond range low — take the **stricter**); `execution_mode="kraken_paper"`; TTL flat by minute 48.

Falsify → FLAT: range-low breach, wave `INVALIDATED`.

**Step 3.** Path α vs path β in intent: α = edge entry; β = wait for closed-bar confirmed conductor+alt breakout (`confirmed_breakout_retest`). Both closed-bar only. If edge unclear, **do not** fire α.

**Step 4.** Orchestrator: register template name, call `plan`. Zero new order code.

**Step 5.** Tests: full cycle → paper BUY with TP/SL/ladder; minute 50 FLAT; no ranker FLAT; first touch without retest FLAT; INVALIDATED / range-low FLAT; look-ahead invariant.

**Next → MP-08**

---

### MP-08 — Exhaustion + async unwind

**Read:** prompt MP-08, KB **§8** (beats prompt §10).

**Step 1.** `volatility_exhaustion.py` — real BBW (bollinger width) on closed 5m; OI divergence; CVD flattening. Score 0–1 with component breakdown + `available_*` flags. Missing OI/CVD: **lower that component**, do not crash, do not invent series. `exhausted` from documented threshold.

Sentiment (funding / long-short / social): **only** `mean_reversion_bias`. Never open a short from sentiment alone.

**Step 2.** `async_unwind.py` — sequenced plan, not a single flatten:

1. Close winners
2. Wait VWAP or EMA20 pullback **or** timeout
3. Close losers
4. Minute ≥ 55 → `forced=True` flat
5. Throttle SLEEP / session gap / wave INVALIDATED → unwind-only (no entries)

KEEP `volatility_throttle.py` values unchanged.

**Step 3.** Tests: BBW+OI+CVD exhausted; trend without BBW collapse not exhausted; missing feeds; winner-before-loser; pullback wait; `forced` at 55; sentiment sets bias only.

**Next → MP-09**

---

### MP-09 — Dynamic Pine v6 provisioner

**Step 1.** Thin façade `dynamic_pine_provisioner.py` reusing `pine_v6_generator.py` builders. Do not clone TV login/upload (`app/tv/*` KEEP).

**Step 2.** `ProvisionRequest` → deterministic v6 script string. Same request → same script. Different symbols/keys → different `idempotency_key` and script.

**Hardcode into every generated script:**

- `initial_capital=10000`
- `currency=currency.USD` / Cash 100 as specified in generator
- `pyramiding=1`
- `commission_type` + **0.04 %**
- `calc_on_every_tick=false`
- Schema A alert payloads
- `barstate.isconfirmed`
- `lookahead_off`
- TTL comment/logic
- unique `idempotency_key`

**Step 3.** `PineHardeningRequest` → `HardenedPineResult` with `hardening_ok`, list of transforms, reasons.  
Reject: `lookahead_on`, foreign webhook URL, intra-bar unconfirmed logic.  
v5 without webhook: inject webhook + bar-close, record transforms.  
Unhardenable → `hardening_ok=False` (not a silent “ok”).  
Fractal payload: TP 40/30/20/10 + `UPDATE_SL`.

**Step 4.** Tests: two requests differ and are stable; header/schema/IDs present; rejects as above; v5 harden path; fractal TPs; unhardenable False.

No TV upload, no live 25x.

**Next → MP-15** (not MP-10)

---

### MP-15 — Fractal directional

**Requires** 01, 05, 09; exhaustion from 08.

**Step 1.** `fractal_directional.py` `plan(ctx) -> StrategyIntent`

Constants: `TP1_PCT = 0.40`, `TP2_PCT = 0.30`, `TP3_PCT = 0.20`, `RUNNER_PCT = 0.10` (size shares; sum **exactly 1.0**). `DEFAULT_SL_PCT = 0.006`.

- Need ranker release + signed lead/retest + minute 5–48 + closed bars. Else FLAT.
- Long TPs above entry, short mirrored below. Sizes 40/30/20/10.
- Initial SL = **tighter** of 0.6 % and MP-01 liq-buffered stop.
- After TP1: `details["update_sl"] = fee_covered_stop(...)` — **import** MP-01, do not write `* 1.0005` again. Long 100.05 / short 99.95.
- Flatten runner: exhaustion **or** target sweep **or** minute ≥ 55.
- Leverage: copy ranker bound only; never invent 50x.

**Step 2.** Tests: mirror TPs; qty sum 1.0; TP1 forces fee-BE; nearer liq buffer wins; flatten cases; no ranker/lead → FLAT; look-ahead.

**Next → MP-11**

---

### MP-11 — ONNX 16-D tensor

**Read:** prompt MP-11, KB §11, §9.5. Do **not** copy 9-D chat pipelines.

**Step 1.** `sigma/core/onnx_quantum_tensor.py` — shape `(1, 16)`, `float32`. Clip as specified. Missing source → **neutral 0 / fail-closed**, never synthesize a market.

Implement each feature as its own function, then assemble:

| i | Name | Formula |
|---|---|---|
| 0 | cos_phi | `clip((C-O)/(H-L+ε), -1, 1)` |
| 1 | P_norm | `abs(C-O)/ATR14` |
| 2 | Q_norm | `(wick_up+wick_dn)/ATR14` |
| 3 | pos_00 | `tanh((C-open_00)/(2*ATR))` |
| 4 | m_tangent | `atan((C-open_00)/minutes_since_00) * 2/π` |
| 5 | P_cal | `clip(platt_scale(poly_raw), 0, 1)` or 0 without feed |
| 6 | pos_EQ | `clip((C-L)/(H-L+ε), 0, 1)` on dealing range |
| 7 | d_CE | `tanh((C-ce50)/ATR)` |
| 8 | TTL_norm | **remaining minutes to 1h close / 60** (not clock-seconds) |
| 9 | utc_safe | 0 if SessionClock 21:00–22:00 quarantine else 1 |
| 10 | RVOL | from scout or 0 |
| 11 | CVD | 0 without MP-10/L2 |
| 12 | Hurst | from `dual_hurst` or 0 |
| 13 | liq_dist | from MP-01 or 0 |
| 14 | thrust | 0/1 from MP-03 |
| 15 | fvg_touch | 0/1 from htf/MP-03 |

Scale invariance: BTC 78000 vs alt 0.014 with same ratios → **same** tensor (test).

**Step 2.** Inference wrapper: onnxruntime **only if** configured path exists **and** import works. Else `model_available=False`. Do not train. Dummy ONNX file allowed **in tests only**.

**Step 3.** Fallback policy (real branches, not `return "FLAT"` always):

- `TTL_norm < 0.15` **or** UTC 21:00–22:00 → FLAT
- `P_cal >= 0.65` and (`cos_phi >= 0.75` or discount pos_EQ with buy-tail Q_bias) → LONG
- Mirror → SHORT
- Else FLAT

**Step 4.** Bar lock: same bar ts second call → `BLOCKED_BY_BAR_LOCK`.

**Step 5.** No symbols in the tensor. Orchestrator `ctx["onnx"]` only.

**Step 6.** Tests: shape/dtype/ranges; per-feature candles; scale invariance; 21:30 FLAT; TTL 8 min FLAT; all long conds → LONG; no P_cal → FLAT; bar lock; 100× determinism.

KEEP existing `app/quant/onnx_kelly.py`. No second Kelly path.

**Next → MP-12**

---

### MP-12 — Backtest H1–H7

**Step 1.** `sigma/backtest/lookahead_pipeline_check.py`

- `assert_no_lookahead(tick_ctx_series)` — HTF at t may see closes **≤ t−1** only.
- A dedicated test **injects** a leak and **must fail** the assertion (test of the test).
- Walk-forward chronological 2:1, **no** random split.

**Step 2.** Prefer filename `tests/backtest/test_hypotheses_h1_h7.py` (H7 required). If you keep `h1_h6` name, H7 tests still live there.

Each hypothesis is a **real** backtest on synthetic (and TV-CSV if present) with taker 0.04 %/side or 0.06 % roundtrip. Report mean + a simple significance measure + label `confirmed|open|rejected`. No single-number “proof”.

- H1 bias-aligned vs counter-trend FVG
- H2 overlap 07–09 / 14–16 UTC vs off-session
- H3 leverage sweep 2×…30× walk-forward: return, max DD, liq frequency
- H4 weekend alt-longs slippage +0.1/+0.3/+0.6 %
- H5 Hurst/MFDFA gate on vs off, DD compare
- H6 weekend fakeout vs weekday; Monday 10:00 UTC momentum; sweep→reclaim
- H7 `cos_phi_path` entry |φ|≥0.40, exit |φ|≤0.15, N∈{10,14,20,30}, **1-bar lag**, fees; return, max DD, Sharpe, win rate, PF, trade count

**Step 3.** Engine: pandas/existing TV-CSV/`BacktestEngine`. **Do not** add VectorBT if it is not in `requirements.txt`.

**Step 4.** Optional tiny `sigma/backtest/report.py` writing under `tests/backtest/results/` — gitignore artifacts, do not commit dumps.

No live feed. No UI.

**Next → MP-16**

---

### MP-16 — Research dashboard

**Step 1.** `power_factor_backtest.py` — real state machine:

- Entry long if `cos_phi >= 0.40`, short if `<= -0.40`
- Stay in position until `|cos_phi| <= 0.15` (hysteresis — do not flatten at 0.39)
- Position applies with **1-bar lag** (signal bar stays flat)
- Fee 0.06 % roundtrip on each close
- Metrics: return, max DD, Sharpe with **8760** 1h bars/year, win rate, profit factor, trade count
- Parameter N sweep

**Step 2.** `app/dashboard/tv_lightweight_export.py`

- JSON: unix seconds **strictly increasing**, OHLCV + cosφ + equity + markers
- Markers **only** on position changes
- Standalone HTML: CDN `lightweight-charts`, **three** synced panes (candles+markers, cosφ with ±0.40 and ±0.15 lines, equity vs benchmark)
- **No** live URL, **no** polling, **no** React rewrite of `TvLightweightChart.tsx`

**Step 3.** Tests: trend→long, chop→flat, bear→short; hysteresis; lag; sort; HTML has 3 containers + threshold lines; identical JSON on repeat.

**Next → MP-17**

---

### MP-17 — Frontend panels

**Read all of** `docs/SIGMA-UI-SPEZIFIKATION.md`. Build **every** listed panel section, not a subset.

**Step 1.** Types in `src/lib/sigmaApi.ts` for all `/api/v1/sigma/*` reads and operator writes.

**Step 2.** `app/server/schemas.py` + `routes_sigma.py` — **new** namespace `/api/v1/sigma/...`. Do not break `/api/v1/regime` etc.

When a fachmodul is absent: return the **full schema** with `valid=false` / empty arrays / `source="none"`. Never random demo prices. Never `"LIVE"`.

Endpoints at minimum (all exist, all typed):

`/risk` `/ladder/preview` `/zones` `/power` `/scout` `POST /scout/scan` `/polymarket` `/regime` `/exhaustion` `/provisions` `POST /provisions/harden` `/fractal/preview` `/onnx` `/research/...`

**Step 3.** Twelve real panels using `PanelShell` / `Stat` / `FeedBadge` / `usePoll` — each implements the **content blocks** in the UI spec (not a title and one “coming soon” line):

QuantumRegime, MarketGeometry, PowerPhysics, SymbolScout, Polymarket, LadderArchitect, FractalTrade, Provisioner, OnnxBrain, RiskGuard, Unwind, ResearchLab

**Step 4.** Registry + presets `QUANTUM_OPS`, `POSITION_DESK`, `RESEARCH_LAB`. Extend MarketChart overlays, Settings (KB defaults; **cannot** toggle hard-stop / 6 % depth / fee-BE), TvJobs, ExecutionRisk.

**Step 5.** Writes: operator token + confirm modal or **403**. No order buttons. Blinded → `ASSET_###`. UTC only. FeedBadge honest.

**Step 6.** `tests/test_frontend_terminal.py`: 12 IDs, 3 presets, fail-closed schemas, write without token rejected, blinded, lint green. `npm run lint`.

No new npm deps. No MP-10/13/14 panels.

**Next → STOP**

---

## 5. STOP

Loop `DONE` and Next `STOP` only when all 14 required rows are **complete** (not stub-DONE).

Final PR body: table Phase | files | tests | completeness-gate.

Never force-push `main`. Never live-trade.
