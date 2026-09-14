# Projekt:Sigma — Funktions-, Berechnungs- & Schnittstellen-Referenz

> **Stand:** main `653f190` (nach MP-01…MP-09, MP-15, MP-11, MP-12, MP-16, MP-17 und GLINT-POLYMARKET-WIRING)
> **System:** Manas: Ciel Core Matrix — Projekt:Sigma · Knoten: Rouge (Triage) / Blanche (Ingestion) / Jaune (Valuation) / Noir (Audit) / Ciel (Synthese)
> **Vollsuite:** 859 Tests grün · Frontend: `tsc --noEmit` grün · `vite build` grün

---

## 1. Allgemeine Programmstruktur

```
Sigma/
├── app/                      # Runtime, Server, Exekution (Blueprint-Layer)
│   ├── server/               #   FastAPI-App (:8000), Routen, Schemas
│   │   ├── main.py           #     App-Startup, Lifespan, Scheduler-Loop
│   │   ├── routes_sigma.py   #     Sigma-/Blueprint-Routen (§7, §30–§38, MP-17)
│   │   ├── routes_quant.py   #     Quant-/Lake-/Academy-Routen
│   │   ├── routes_logs.py    #     Live-Log-Konsole
│   │   └── schemas.py        #     Pydantic-Verträge (Webhook A/B/C + MP-17)
│   ├── core/                 #   Config, Clock, Scheduler, DuckDB, Redis, Telemetrie
│   ├── execution/            #   Loop-A-Pipeline, Paper-Engine, Safety, Deadman,
│   │                         #   Kraken-Bridge, Flywheel, Vault, M8, Lifecycle-Steps
│   ├── ingestion/            #   OmniStream, Kraken-Depth-Adapter, Contagion-Feed
│   ├── quant/                #   Regime, Hurst, ONNX-Kelly, Contagion, Glint-Verifier
│   ├── backtest/             #   BacktestEngine, TV-CSV-Interchange
│   ├── optimizer/            #   GeneticOptimizer (Walk-Forward GA), Scorecard, Allocator
│   ├── scout/                #   Loop-D ScoutDaemon & Incubator
│   ├── tv/                   #   TradingView Worker, Alert-Provisioner, Script-Catalog
│   ├── services/             #   Lifecycle-Service, Netron, Telegram, TV-Library
│   ├── mcp/                  #   TradingView-MCP-Client, Kraken-MCP-Bridge
│   ├── security/             #   Passkey/WebAuthn, SettingsEnvManager (RUNTIME_MAP)
│   ├── dashboard/            #   MP-16 HTML-Forschungs-Export (Lightweight-Charts)
│   └── telegram/             #   Telegram-Bot & WebApp
├── sigma/                    # Core-Engine (Plan-Phasen MP-01…MP-17)
│   ├── core/                 #   ONNX-Tensor (MP-11), Math-Engine, Fractal-Scaling
│   ├── signals/              #   Price-Action-Physik, Wave, Hurst, Ranker, Session …
│   ├── strategies/           #   Sniper, DCA, Fraktal, Pine-Provisioner, Unwind …
│   ├── orchestration/        #   MasterOrchestrator, Hourly-Gate, Shadow-Plan, Router
│   ├── execution/            #   Risk-Guards (MP-01), Universe, Execution-Bridges
│   ├── ports/                #   Polymarket-Port, Gamma-Feeder (Wiring)
│   ├── loops/                #   Loop-A…E Ports (Papier-Pfade, keine Orders)
│   └── backtest/             #   Look-ahead-Prüfer, Power-Factor-Backtest, Report
├── src/                      # Frontend (React 19, TS strict, Vite, Tailwind v4)
│   ├── components/sigma/     #   Dock, panels.tsx, mp17Panels.tsx (12 Panels), Terminal
│   ├── components/ui/        #   shadcn/ui-Komponenten
│   └── lib/sigmaApi.ts       #   Typisierter API-Client (+ sigmaResearchApi, MP-17)
├── tests/                    # 50 Testdateien (pytest, offline, deterministisch)
│   ├── backtest/             #   H1–H7-Harness + Power-Factor-Dashboard (MP-12/16)
│   └── …                     #   je Phase eigene Testdatei
├── docs/                     # Plan-Karten (MP-01…MP-17), KB, UI-Spezifikation, Wiring
└── bin/                      # sigma-up/-down, sigma-tv-login (Bootstrap)
```

### Laufzeit-Datenfluss (Loops)

```
TV-Webhook/Alert ──► Loop A (Signal → Safety → Confluence → Paper-Execution)
Scheduler (Tier 0–5) ──► Loop B (Backtest/Replay) · Loop C (Feed/Store) ·
                         Loop D (Scout/Incubator) · Loop E (Alert-Plan)
MasterOrchestrator (Phase-3-Tick): klassifiziert/gated nur — platziert KEINE Orders
  → screening → dual → wave → throttle → pair → polymarket → onnx (ctx)
```

---

## 2. Berechnungen & Formeln (Kern)

### 2.1 Price-Action-Physik (MP-04, `sigma/signals/power_triangle.py`)

| Formel | Definition |
|---|---|
| ATR₁₄ | Wilder-RMA der True Ranges (Periode 14; vor dem Seed Expanding-Mean) |
| `cos_phi_bar` | `sign(C−O) · (|C−O|/(H−L+ε))`, geclippt [−1, 1] |
| `P_norm` | `|C−O| / ATR₁₄` (geclippt [0,1] im Tensor) |
| `Q_norm` | `(oberer + unterer Docht) / ATR₁₄` (geclippt [0,1] im Tensor) |
| `Q_upper/Q_lower` | einzelne Docht-Anteile / ATR₁₄ |
| `Q_bias` | `(unterer − oberer Docht) / ATR₁₄` (positiv = Kauf-Tail) |
| `cos_phi_path` | Kaufman Efficiency Ratio: `(C_t − C_{t−N}) / Σ|ΔC|` (bzw. ΣTR), [−1, 1] |
| Cluster | `classify_bar`: SOLID_TREND / WICK_REJECTION / EXPLOSIVE / CLIMAX / BATTLEGROUND_DOJI |

### 2.2 ONNX-Observation-Tensor (MP-11, `sigma/core/onnx_quantum_tensor.py`)

Tensor `[1,16]` float32, skaleninvariant (78.000 vs. 0,014 → gleiche Werte):

| # | Feature | Formel |
|---|---|---|
| 1 | `cos_phi` | `(C−O)/(H−L+ε)`, clip [−1,1] |
| 2 | `p_norm` | `|C−O|/ATR14`, clip [0,1] |
| 3 | `q_norm` | `(Wick_oben+Wick_unten)/ATR14`, clip [0,1] |
| 4 | `pos_00` | `tanh((C − Open_00:00UTC)/(2·ATR))` |
| 5 | `m_tangent` | `arctan((C − Open_00:00)/Minuten_seit_00)·2/π` |
| 6 | `p_cal` | `platt_scale(poly_raw)`, clip [0,1]; ohne Feed → 0 |
| 7 | `pos_eq` | `(C − Range_Low)/(Range_High − Range_Low + ε)`, [0,1]; ohne → 0.5 |
| 8 | `d_ce` | `tanh((C − CE50)/ATR)` |
| 9 | `ttl_norm` | `Restminuten der 1h-Bar / 60`, clip [0,1] |
| 10 | `utc_safe` | 0 bei 21:00–22:00-Quarantäne (sonst 1; unbekannt → 0) |
| 11 | `rvol` | RVOL/3, clip [0,1] |
| 12 | `cvd` | CVD-Absorption [−1,1]; ohne L2-Feed → 0 |
| 13 | `hurst` | `(H − 0.35)/0.3`, clip [0,1]; ohne → 0.5 |
| 14 | `liq_dist` | Liq-Distanz/10 %, clip [0,1]; ohne → 0.5 |
| 15 | `thrust` | Two-Bar-Thrust (0/1) |
| 16 | `fvg_touch` | FVG-Touch (0/1) |

**Fallback-Policy (produktiv ohne Modell):** `TTL_norm < 0.15` oder UTC 21–22 → FLAT; `P_cal ≥ 0.65` und (`cos_phi ≥ 0.75` **oder** Discount `pos_eq<0.5` mit Kauf-Tail `Q_bias>0`) → LONG; spiegelbildlich SHORT; sonst FLAT. **Modell:** optional onnxruntime (nur wenn Pfad konfiguriert + importierbar), I/O `tensor_x [N,16]` → `action_probs` (Softmax Long/Flat/Short) + `leverage_factor` (10+15·sigmoid → Hebel 10–25). Entropie > 0.65 → Zwangs-Flat. **Bar-Lock:** max. 1 Aktion je Bar-Zeitstempel → `BLOCKED_BY_BAR_LOCK`.

### 2.3 Risk-Guards (MP-01, `sigma/execution/risk_guards.py`)

| Guard | Regel |
|---|---|
| Hard-Stop | Stop muss ≤ Liq-Preis − 0,5 % (bzw. ≥ +0,5 % bei Short) liegen |
| Grid-Tiefe | Gesamttiefe ≥ 6 % (Meme-Perps; sonst min. 3 %), `assert_grid_depth` |
| Liq-Nähe | Distanz < 5 % → HITL-Banner (Entscheidung: Stop bevorzugt) |
| Wick-Liq-Zone | Liq-Preis darf nicht in der Wick-Zone liegen (`liq_outside_wick_zone`) |
| Cooldown | 30 min nach Exit (`cooldown_active`) |
| Fee-BE | `fee_covered_stop(entry, side)` = `entry × 1.0005` (long) / `× 0.9995` (short); Pflicht nach TP1 (Reason `TP1_HIT_FEE_COVERED_BREAKEVEN`), nicht abschaltbar |
| Hebel-Tiefe | `assert_leverage_for_depth` — Hebel zur Grid-Tiefe passend |

### 2.4 Fraktaler Einzeltrade (MP-15, `sigma/strategies/fractal_directional.py`)

- **TP-Staffel:** TP1 40 % @ +1,0 % · TP2 30 % @ +2,0 % · TP3 20 % @ +3,5 % · Runner 10 % @ Entry (Summe 100 %; short spiegelbildlich). Runner-Trail: 3×ATR.
- **Initial-SL:** `min(0.006, liq_puffer_pct)`; Basis = engere von Liq-Puffer / 0,6 %.
- **Pflicht nach TP1:** `update_sl = fee_covered_stop(entry, side)` (Reason `TP1_HIT_FEE_COVERED_BREAKEVEN`).
- **Kill-Switch (FLAT):** UTC-Minute ≥ 55, `exhaustion.exhausted`, Close erreicht `sweep_zone`.
- **Entry-Fenster:** Minute 5–48; Freigabe: Ranker-Rec `fractal_directional`/`sniper_hedge` + `entry_ready`; `lead.confirmed` ODER Wave-COLLAPSED + Thrust; Hebel ≤ 50 und ≤ Ranker-Cap; nur geschlossene Bars.

### 2.5 High-Beta-Ranker (MP-05, `sigma/signals/high_beta_ranker.py`)

| Empfehlung | Bedingung |
|---|---|
| `fractal_directional` | β ≥ 3,5 UND RVOL ≥ 3,0 UND Liq-Distanz ≤ 0,10 |
| `sniper_hedge` | β ≥ 2,8 UND RVOL ≥ 2,5 UND Liq-Distanz ≤ 0,10 |
| `dca` | sonst |

Weitere Kennzahlen: signierte β/r, RVOL, Spread %, 24h-Performance %, `pos_eq` (Post-Breakout-Position; 0,40–0,65 = „Leader bereit“, > 0,9 = Chasing rot), Score.

### 2.6 Wave / Hurst / Session / Throttle (unverändert)

- **Dual-Hurst:** DFA-Hurst HTF + LTF; `htf_ready` (nur geschlossene Bars); Schwellen Trend ≥ 0,55 / Reversion ≤ 0,45.
- **Wave-Collider:** `COLLAPSED_INTO_ZONE` / `INVALIDATED` / `HTF_OPEN` / `IDLE`; Dealing-Range H/L, EQ, CE50, FVG-Touch.
- **SessionClock:** EU 08–09 UTC, US 14–16 UTC (emerald); 21:00–22:00 Quarantäne (rot); Weekend „reduced size“.
- **Volatility-Throttle:** SLEEP (0 Bots) / NORMAL (3) / AGGRESSIVE (8) nach ATR-Ratio (Wilder-RMA).
- **Hourly-Gate-Phasen:** SCAN&DEPLOY 00–05 · ACTIVE EXECUTION 05–48 · PRE-CLOSE UNWIND 48–55 · IDLE 55–60.

### 2.7 Polymarket Layer-0 (MP-06 + Wiring)

- **Dichte:** `P([Kᵢ,Kᵢ₊₁)) = P(Kᵢ) − P(Kᵢ₊₁)`; Ränder `1 − P(K₁)` bzw. `P(Kₙ)`; Summe ≈ 1 (sonst fail-closed).
- **μ:** `Σ Midpointₖ · Pₖ`; **Bias_%:** `(μ − p_spot)/p_spot · 100`.
- **Trajektorien:** `p̂(T) = p_spot + (μ − p_spot)·w(T)` mit w = {1h: 0.15, 2h: 0.30, 4h: 0.55, EOD: 0.85, Res: 1.00}.
- **Gate 0.60:** ∃ Strike > spot mit P ≥ 0,60 — **nur Telemetrie**, nie Trade-Blocker.
- **Gamma-Filter:** `volume24hr ≥ 1.000.000 USD`, `synthetic=True` → verworfen; TTL-Stale (`POLYMARKET_TTL_S`, Default 300 s).

### 2.8 Glint-Kraken-L2-JIT (Wiring, `app/quant/glint_orderbook_verifier.py`)

- **Mid/Spread:** `p_mid = (best_bid + best_ask)/2`; `Spread_bps = (ask − bid)/mid · 10.000`.
- **2 %-Band-Volumen:** `V_bid,2% = Σ pᵢ·qᵢ` für `pᵢ ≥ mid·0.98`; analog asks `≤ mid·1.02`.
- **Imbalance:** `I_depth = (V_bid,2% − V_ask,2%)/(V_bid,2% + V_ask,2%)` ∈ [−1, 1].
- **Entscheidungsmatrix:** Snapshot-Alter > 3 s → `STALE_SNAPSHOT_VETO` (×0); Long ∧ I ≤ −0,20 → `LIQUIDITY_TRAP_VETO` (×0); Long ∧ I ≥ +0,30 ∧ Spread ≤ 15 bps → `CONFIRM_TAILWIND` (×1,25); sonst `NEUTRAL_FLOW` (×1,0). (Short spiegelbildlich über signiertes I.)

### 2.9 Backtest & Forschung (MP-12/MP-16)

- **Look-ahead-Prüfer:** `assert_no_lookahead` (HTF-Bars zur Zeit t nur bis t−1); `walk_forward_split` 2:1 (Mitte verworfen); `walk_forward_folds`; `check_series_closed` (offene letzte Bar → Assert).
- **Power-Factor-Backtest:** Hysterese `cos_phi_path ≥ +0.40` Long / `≤ −0.40` Short / `|cos| ≤ 0.15` Exit; Position 1 Bar verzögert; Fee 0,06 % Roundtrip; Metriken: Total Return, Max-DD, annualisierter Sharpe (8.760 Perioden), Win-Rate, Profit-Faktor, Trades; Fenster N ∈ {10,14,20,30}.
- **H1–H7-Harness:** H1 FVG-bias, H2 Overlap-Session, H3 Hebel-Sweep 2×–30× (Walk-Forward), H4 Weekend-Slippage, H5 Hurst-Gate, H6 Weekend-Fakeout + Montag-Sweep/Reclaim, H7 cos-φ-Strategie; Overfitting-Flags (Sharpe > 3, DD < 5 %).
- **Report:** `sigma/backtest/report.py` → Markdown/JSON nach `tests/backtest/results/` (gitignored).
- **HTML-Export:** `app/dashboard/tv_lightweight_export.py` — 3 synchronisierte Panes (Candles+Marker / cos φ ±0,40/±0,15 / Equity+Benchmark), CDN standalone, `</script>`-escaped.

### 2.10 DCA-Leiter (MP-02) & Sniper (MP-07/MP-09)

- **Ladder:** Sprossen 5–8, Step % (Default 0,2 %), Step-Wachstum, Volumen-Faktor 1,15, TP 1,5–2,0 %, TTL 2 h; dynamisch: 2h-Range × 0,618; `average_fill_price`, `take_profit_price`, `ttl_expired`; Guards: Tiefe ≥ 6 %, erster Step ≥ Spread+Fee-Floor, Liq-Distanz, Hard-Stop.
- **Sniper-Pipeline:** 15m→1m-Retest (`retest_confirmed`, `beta_retest_confirmed`), Minuten-Phasen, TTL-Gates; Pine v6-Provisionierung (MP-09): `calc_on_every_tick=false`, `lookahead_off`, `barstate.isconfirmed`, Standard-Header (initial_capital 10.000, cash 100, pyramiding=1, Commission 0,04 %), eindeutige `idempotency_key` je Alert (`{sid}_{ACTION}_{seq}_{{timenow}}`), Härtung fremder Skripte fail-closed (`hardening_ok=false` bei Intrabar etc.).

---

## 3. Schnittstellen

### 3.1 REST-API (FastAPI :8000) — Auswahl

**Webhook/Signal:** `POST {WEBHOOK_ROUTE}` (Schema A `SigmaL4AlertPayload` / B Pionex / C ML-Telemetrie, `extra="forbid"`), `POST /api/v1/signal/ingest`, `GET /api/v1/signal/schemas`, `GET /api/v1/signal/pipeline`.

**Safety/Deadman:** `GET/POST /api/v1/safety` (kill/release/pause), `GET /api/v1/deadman`, `POST /api/v1/deadman/beat` (Operator-Token).

**Bots/Strategien:** `GET/POST /api/v1/bots`, `POST /api/v1/bots/{id}/start|pause`, `POST /api/v1/bots/{id}/m8/{state}`, `GET /api/v1/strategies/library-snapshot`, `GET /api/v1/strategies/{id}/scorecard`, `PUT /api/v1/strategies/{id}/slots`, `POST /api/v1/strategies/{id}/initialize|validate`, `POST /api/strategies/{id}/start|pause|resume|quarantine`, `GET /api/v1/lifecycle`.

**Markt/Scout:** `GET /api/v1/market/ohlc|indicators|overview|movers|screener`, `GET /api/v1/regime`, `GET /api/v1/scout`, `POST /api/v1/scout/plan`.

**TV:** `GET/POST /api/tv/jobs*` (backtest/pull-parameters/push/cancel), `GET /api/tv/session/status`, `POST /api/tv/session/login`, `GET/POST /api/strategies/tv/scripts|sync-library`, Alerts `/api/strategies/{id}/alerts/*`.

**Execution-Plane:** `GET /api/v1/clock`, `GET /api/v1/scheduler`, `GET/POST /api/v1/orderbook/confluence`, `GET /api/orders/receipts`, `GET /api/v1/rate-limiter`, `GET/POST /api/v1/contagion`, `GET/POST /api/v1/flywheel*`, `GET /api/v1/leverage/{strategy_id}`, `GET/POST /api/v1/paper-lab*`.

**Quant/Lake/Academy:** `POST /api/quant/state-machine/set-state`, `POST /api/quant/execution/m8-judge`, `GET /api/quant/dfa/hurst`, `GET /api/quant/regime/ampel`, `GET /api/quant/dual-hurst`, `GET /api/quant/htf-features`, `GET /api/quant/h-tests`, `GET /api/quant/polymarket/layer0`, `GET /api/quant/session`, `GET /api/quant/throttle`, `GET/POST /api/lake/*` (summary/seed/query/resample/compact/sync), `GET/POST /api/academy/*`, `POST /api/quant/postmortem/analyze`, `POST /api/quant/evolution/run`.

**MP-17 `/api/v1/sigma/*` (fail-closed Read):** `regime`, `risk`, `power`, `zones`, `scout`, `polymarket`, `exhaustion`, `provisions`, `ladder/preview`, `fractal/preview`, `onnx`, `orderflow` — alle GET; ohne Fachmodul `ok=false, available=false, feed.source="unknown"`. Schreibzugriffe (Operator-Token + Modal): `POST /api/v1/sigma/scan`, `POST /api/v1/sigma/provisions`, `POST /api/v1/sigma/provisions/de-provision`, `POST /api/v1/sigma/provisions/harden` (fail-closed `hardening_ok=false`).

**MP-12/16 `/api/v1/research/*`:** `POST /api/v1/research/run` (H1–H7, Operator), `GET /api/v1/research/jobs/{job_id}`, `GET /api/v1/research/dashboard`.

**Sonstiges:** LLM-Tools (`/api/v1/llm/*`, WS `LLM_STREAM_ROUTE`), Netron (`/api/v1/models/netron/*`), Telegram (`/api/v1/telegram*`), Diagnose (`/api/v1/diagnostics/*`), Logs (`/api/v1/logs/*`, WS-Stream).

### 3.2 WebSocket / Streams

| Route | Zweck |
|---|---|
| `LLM_STREAM_ROUTE` (WS) | Live-LLM-Stream |
| `/api/v1/logs/stream` (WS) | Live-Prozess-/AI-Log-Konsole |
| externe Feeds | Kraken REST `Depth` (:0/public/Depth), Polymarket Gamma `events?slug=` (nur Mapper; Tests offline) |

### 3.3 Ports & Injektionspunkte

- `sigma/ports/polymarket_port.py` + `polymarket_gamma_feeder.py` (Singleton `get_gamma_port`/`set_gamma_port`) → `GET /api/v1/sigma/polymarket`.
- `app/quant/glint_orderbook_verifier.py` (Singleton `get_verifier`) + `app/ingestion/kraken_depth_adapter.py` (`snapshot_from_payload`, `verify_payload`) → `GET /api/v1/sigma/orderflow`, Webhook-Confluence, `_preflight`-JIT-Audit.
- `app/core/scheduler_matrix.py`: `install_canonical_tasks(..., gamma_port=None)` — Port nur bei Feed, nie Fake.
- `sigma/loops/loop_a…e.py`: Loop-Ports (Paper-only).
- `app/security/SettingsEnvManager.py`: `RUNTIME_MAP` inkl. `POLYMARKET_GAMMA_URL`, `POLYMARKET_MIN_VOLUME_USD`, `POLYMARKET_TTL_S`.

### 3.4 Frontend (src/)

- `src/lib/sigmaApi.ts`: typisierter Client (`sigmaApi`) + `sigmaResearchApi` (MP-17) + `blindedSymbol` (Blinded-Modus `ASSET_###`).
- `PANEL_REGISTRY`/`PANEL_TITLES` (36 Panels): 12 MP-17-Panels (`QuantumRegimePanel_`, `MarketGeometryPanel_`, `PowerPhysicsPanel_`, `SymbolScoutPanel_`, `PolymarketPanel_`, `LadderArchitectPanel_`, `FractalTradePanel_`, `ProvisionerPanel_`, `OnnxBrainPanel_`, `RiskGuardPanel_`, `UnwindPanel_`, `ResearchLabPanel_`) + Bestand (BotDeck, MarketChart, RiskGauges, TvJobs, Netron, …).
- Presets: `BOT_COCKPIT … CONFIG` + MP-17 `QUANTUM_OPS`, `POSITION_DESK`, `RESEARCH_LAB`.
- Chart: `TvLightweightChart.tsx` (lightweight-charts v5); Research-Export: standalone HTML (3 Panes).

---

## 4. Tests (50 Dateien, 859 Tests — offline, deterministisch)

| Bereich | Dateien (Auswahl) |
|---|---|
| Plan-Phasen | `test_fractal_directional.py`, `test_onnx_tensor.py`, `test_dynamic_pine.py`, `test_hourly_ranker.py`, `test_exhaustion_unwind.py`, `test_risk_guards.py`, `test_quantum_wave_regime.py` u. a. |
| Backtest | `tests/backtest/test_hypotheses_h1_h7.py`, `test_power_factor_dashboard.py` |
| API/Server | `test_api_contract.py` (inkl. Wiring-API), `test_mp17_sigma_panels.py`, `test_frontend_terminal.py`, `test_execution_plane.py` |
| Adapter/Wiring | `test_sigma_live_adapters.py` (Gamma + Kraken-L2-JIT, JSON-Fixtures, kein Netz) |
| Sonstiges | `test_exact_csv_roundtrip.py`, `test_ga_shadow_gate.py`, `test_kraken_cli_security.py`, `test_netron_stack.py`, `test_lan_events_idempotency.py` |

---

## 5. Konventionen & Hartregeln

- **Paper-only:** `execution_mode = kraken_paper`; kein Live-Orderpfad, keine Exchange-Credentials im Test.
- **Orchestrator:** klassifiziert/gated nur (setzt `ctx`, ruft `Templates.plan()`); platziert **nie** Orders, kein Panic-Close, kein Auto-Deploy.
- **Nur geschlossene Bars;** offene letzte Kerze wird verworfen; kein Look-ahead (per Test bewiesen).
- **Fail-closed:** fehlende Daten / Feed / `synthetic=True` → neutral/FLAT, nie erraten.
- **Prozente als Dezimalen** (0.06 = 6 %); Dataclasses mit `to_dict()`; volle Typannotationen; keine Stubs.
- **Keine Duplikate:** Guards, FVG, SessionClock, Dual-Hurst, Throttle, Wave-Gates, TV-Lifecycle werden nicht neu gebaut — EXTEND nur am benannten Vertrag.
- **Keine neuen Dependencies** ohne `requirements.txt`/`package.json`-Nachweis (kein VectorBT, kein onnxruntime-Force).
- **Sicherheitsregeln nicht abschaltbar:** Fee-BE, Grid-Tiefe ≥ 6 %, Hard-Stop im Buch, Wick-Liq-Zone.
- **Zeiten in UTC, Zahlen mono; Regime-Anzeigen nur auf geschlossenen Bars.**
- **Modul-Header:** Datei / Zweck / System: Manas: Ciel Core Matrix — Projekt:Sigma / Knoten.
