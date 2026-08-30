# Projekt:Sigma — Implementierungs-Roadmap (Master-Prompt-Rückgrat)

> **Zweck:** Diese Roadmap gliedert die Wissensdatenbank (`SIGMA-WISSENSDATENBANK.md`)
> in sequenziell von KI-Agenten (GitHub/Cursor/Claude Code) abarbeitbare Phasen.
> Jede Phase hat einen eigenen Master-Prompt unter `docs/prompts/`.
>
> **Stand der Baseline:** Der `sigma/`-Baum ist bereits funktionsfähig
> (Orchestrator, Wave-Collider, Sessions, Hurst, Throttle, Ladder, Scout,
> drei Strategie-Templates, Loops A–E, Tests). Die Phasen bauen darauf auf —
> keine Phase darf bestehende Gates schwächen.

---

## Reihenfolge-Prinzipien

1. **Regime zuerst, Strategie danach.** Jeder neue Marktbaustein ist erst
   Signal/Feature (`signals/`/`core/`), dann Orchestrator-Kontext, dann ggf.
   Strategie.
2. **Paper zuerst.** Neue Strategien sind `kraken_paper`, bis sie H3/H4/H5
   (Backtest → Paper → Reife-Graduierung) bestehen.
3. **Pro Phase:** eine logische Änderung, volle Typisierung, Pytest,
   keine Platzhalter, kein Look-Ahead.
4. **Abhängigkeit:** Phasen dürfen parallelisiert werden, wo „Abhängigkeiten“
   es erlauben; MP-01 sollte vor allen Strategie-Phasen stehen.
5. Jeder Master-Prompt referenziert die Wissensdatenbank-Kapitel (§).

---

## Phasenübersicht

| Phase | Master-Prompt | Inhalt | Hängt von |
|---|---|---|---|
| MP-01 | `MP-01-hard-risk-guards.md` | Hard-Risk-Guards: Stop-Pflicht, Rastertiefen-Guard, BTC-Makro-Gate, Liq-Distanz, Cooldown, Exhaustion-Signale | — |
| MP-02 | `MP-02-micro-dca-ladder.md` | Micro-DCA-Ladder-Generator (0,15–0,2 % Steps, 1,15x Vol, 1,5 % TP, dynamische Range-Steps, 6–10 %-Tiefen-Guard, TTL) | MP-01 |
| MP-03 | `MP-03-candle-regime-signals.md` | Two-Bar-Thrust, Marubozu/FVG-Quant, Daily-Open-Envelope (00:00, Volumen-Anker, Outside-Inside) | — |
| MP-04 | `MP-04-power-phasor-features.md` | Leistungsdreieck (P/Q/S, cos φ), Hilbert-Phasor, MTF-Resonanz, reellwertige Target-Algebra | MP-03 |
| MP-05 | `MP-05-hourly-gate-symbol-ranker.md` | 1-Scan-pro-1h-Bar-Gate + High-Beta-Symbol-Ranker (signiertes r/β, Long/Short-Richtung, 24h-Top-Gainer-Relativstärke-Vorselektion + Post-Breakout-pos_EQ, RVOL/Spread-Score, Leader-Rotation pro Scan, Strategieempfehlung) | MP-01, MP-03 |
| MP-06 | `MP-06-polymarket-density.md` | Polymarket-Feed-Adapter (optionaler Port), implizite Dichte, Term-Struktur-Trajektorie, Brier/Platt-Kalibrierung | MP-05 |
| MP-07 | `MP-07-sniper-strategy-phase2.md` | Quantum-Sniper-Strategie (15m→1m Retest + DCA-Ladder, TTL 45–48 min) als BaseStrategy | MP-02, MP-03, MP-05 |
| MP-08 | `MP-08-exhaustion-unwind.md` | Volatilitäts-Exhaustion-Detektor (BBW/OI/CVD) + asynchroner Unwind-Template | MP-01, MP-04 |
| MP-09 | `MP-09-dynamic-pine-provisioner.md` | Dynamischer Pine-v6-Provisionierer pro gescoutetem Symbol + Schema-A-Webhook (inkl. Multi-TP-Fraktal-Payload) + Auto-Härtung fremder Pine-Skripte (Webhook/Bar-Close/Payload automatisch injiziert, sonst fail-closed) | MP-07 |
| MP-15 | `MP-15-fractal-directional.md` | Fraktale High-Leverage-Einzeltrade-Strategie (TP1 40 % / TP2 30 % / TP3 20 % / Runner 10 %, Fee-Covered Break-Even +0,05 %, 20–50x, ATR-Trailing-Kill-Switch) | MP-01, MP-05, MP-09 |
| MP-10 | `MP-10-orderflow-validator.md` | L2/Footprint-Orderflow-Validator (Stacked Imbalances, CVD-Absorption, POC-Konfluenz, Iceberg) — optional, fail-closed ohne Tiefe | MP-04 |
| MP-11 | `MP-11-onnx-tensor.md` | 16-Feature-Observation-Tensor + ONNX-Runtime-Inferenz mit deterministischem Fallback | MP-04, MP-05, MP-06 |
| MP-12 | `MP-12-backtest-hypotheses.md` | Backtest-Harness (VectorBT): Faktor-Sweep H3, Hypothesen H1–H7 (inkl. Weekend-Fakeout, cos-φ-Strategie), Look-ahead-Pipeline-Test, Walk-Forward | MP-02, MP-04, MP-07 |
| MP-16 | `MP-16-research-dashboard.md` | Lightweight-Charts-Dashboard (3 Pane, Marker, Equity) + cos-φ-Pfad-Backtester mit Hysterese | MP-04, MP-12 |
| MP-17 | `MP-17-frontend-panels.md` | Frontend-Darstellung aller neuen Funktionen im bestehenden Terminal (12 neue Panels, 3 Presets, dünne `/api/v1/sigma/*`-Endpunkte, fail-closed); Vertrag: `docs/SIGMA-UI-SPEZIFIKATION.md` | kann parallel zu MP-01…MP-16 laufen (Endpunkte zunächst leer) |
| MP-13 (optional) | `MP-13-multi-asset.md` | Multi-Asset-Erweiterung XAU/XAG/Forex (Venue-Ports, Marktzeiten) | MP-05 |
| MP-14 (optional) | `MP-14-event-straddle.md` | Pre-Event Doppel-Hedge-Straddle-Template (Event-Waffe mit TTL/Net-Profit-Guarantee) | MP-01, MP-08 |

---

## Phase MP-01 — Hard Risk Guards (Basis)

**Warum zuerst:** Der Live-Verlust (§8 der Wissensdatenbank) entstand aus
fehlenden strukturellen Guards. Jedes spätere Strategie-Modul braucht diese
Funktionen.

**Dateien:**
- `sigma/execution/risk_guards.py` (neu):
  - `hard_stop_distance(liquidation_price, direction, buffer_pct=0.005)` —
    Stop 0,5 % über Liq-Preis (long) / unter (short)
  - `grid_total_depth_pct(ladder_prices, entry_price)` — Gesamt-Tiefe;
    `assert_grid_depth(depth_pct, min_depth=0.06)` für Meme-Perps
  - `btc_macro_breach(btc_htf, support_price)` — BTC-Close unter
    15m/1h-Support → Kauf-Sperre
  - `liquidation_proximity(mark_price, liq_price)` → Abstand in %;
    < 5 % → HITL-Flag
  - `cooldown_active(last_exit_ts, min_s=1800)` — Post-Trade-Cooldown
- `sigma/execution/base_bridge.py` (ergänzen): HITL-Feld im Intent/Dispatch
- Tests: `tests/test_risk_guards.py`
  - Stop liegt korrekt gepuffert über/unter Liq-Preis
  - Grid mit 1,1 % Tiefe wird für Meme-Perps abgelehnt
  - BTC-Supportbruch liefert `macro_gate_closed=True`
  - 4,3 % Liq-Distanz löst HITL aus; Cooldown blockiert 30 min

**Nicht im Scope:** Orderausführung selbst (Loop A), Strategie-Logik.

---

## Phase MP-02 — Micro-DCA-Ladder-Generator

**Dateien:**
- `sigma/strategies/dca_ladder.py` (neu):
  - `build_ladder(entry_price, *, n_safety=6, step_pct=0.002,
    volume_mult=1.15, step_mult=1.10, side="buy")` → Listen aus
    Preis/Margin/kumulierter Distanz
  - `dynamic_step_from_range(high_2h, low_2h, current_price,
    n_safety, range_factor=0.618)` — Rolling-Range-Step
  - `avg_fill_price(ladder_fills)`
  - `tp_price(avg_price, tp_pct=0.015)`
  - Integration mit MP-01-Guards: Gesamt-Tiefe ≥ 6 % erzwungen;
    Step ≥ Spread+Fee-Floor
  - `ttl_seconds=7200` Standard; TTL-Ablauf → FLAT-Intent
- Tests: `tests/test_dca_ladder.py`
  - Parameterbeispiel aus §5.1 (8 Stufen, 0,15 %, 1,15x) reproduzierbar
  - Dynamischer Range-Step: 3 %-Amplitude/6 Stufen ≈ 0,3 % Step
  - Tiefen-Guard lehnt zu enge Raster ab
  - avg_price sinkt korrekt mit Füllungen; TP bei +1,5 %

**Nicht im Scope:** Live-Platzierung, Hedge-Grid-Straddle (MP-14).

---

## Phase MP-03 — Candle- & Regime-Signale (Thrust, FVG, 00:00-Envelope)

**Dateien:**
- `sigma/signals/two_bar_thrust.py` (neu):
  - Bär-Bar + 2 Bull-Bars, Body-Summe > Bär-Body, Close > High[2];
    Kontext-Filter (Support/EMA/Session-Sweep) als optionale Flags;
    nur closed bars; Rückgabe Series/Liste + Signal-Dict
- `sigma/signals/marubozu_fvg.py` (neu, komplementär zu `htf_features.fvg_flags`):
  - Body/Range ≥ 0,80; FVG-Größe in ATR-Einheiten (skaleninvariant);
  - bull/bear; kein Market-Entry-Flag, nur Zone + CE50
- `sigma/signals/daily_open_envelope.py` (neu):
  - 00:00-UTC-Anker; Top-N-Volumen-Bars seit Tagesbeginn;
    obere/untere Regressions-Hüllkurve; Steigungs-Drift;
    **Outside-Inside-Reversal** (Bar außerhalb, Folgebar grün innerhalb)
- Pine-v6-Snippets nur als String-Generatoren in `pine_v6_generator.py`
  (keine neuen TV-Templates in dieser Phase)
- Tests: `tests/test_candle_signals.py`
  - Synthetische Thrust-Sequenz erkennt Signal; Einzel-Grün tut es nicht
  - Marubozu+FVG auf konstruierten Kerzen; CE50 korrekt
  - Envelope: konstruierte 00:00-Top-Volumen-Bars + Outside-Inside → Signal
  - Look-ahead: offene letzte Bar verändert nichts

**Nicht im Scope:** Orchestrator-Deploy (Signale werden nur berechnet),
Sniper-Strategie (MP-07).

---

## Phase MP-04 — Leistungsdreieck & Phasor-Features

**Dateien:**
- `sigma/signals/power_triangle.py` (neu) — Formeln exakt nach
  Wissensdatenbank §9.5 (Price-Action-Physics-Featurevektor):
  - `price_action_physics(candles_df, atr_period=14)` → DataFrame/Dataclass
    mit S_norm, P_norm (Betrag + signed), Q_norm, Q_upper_norm,
    Q_lower_norm, Q_bias, eta_efficiency — ATR als Wilder-RMA,
    ε-Schutz auf jeden Nenner
  - `cos_phi_bar` = sign(Close−Open)·|Close−Open|/(High−Low)
  - `cos_phi_path(close, window=20)` = (C_t−C_{t−N})/Σ|ΔC|
    (Kaufman-Efficiency-Ratio; TR-Variante als Option)
  - Klassifikation: η ≥ 0,85 fester Move; < 0,30 Docht-Fakeout;
    P_norm > 1,2 Expansion; S_norm > 2,0 Climax; Cluster-Tabelle aus §9.2
- `sigma/signals/hilbert_phasor.py` (neu):
  - In-Phase I / Quadratur Q (vereinfachte Hilbert/Differenz-Approximation),
    Amplitude, Phasenwinkel; keine Abhängigkeit von externen Libs
- `sigma/signals/mtf_resonance.py` (neu):
  - HTF-/LTF-Phasor → Winkeldifferenz via **Konjugatprodukt**
    `S = U·I*` (NICHT U·I — Winkelsumme ist sinnlos);
    `resonance = cos(Δφ)`; ≥ 0,75 konstruktive Resonanz,
    < −0,5 Dip-Charging (HTF bullish, LTF gegenläufig)
- Tests: `tests/test_power_phasor.py`
  - Reine Marubozu-Kerze: cos φ ≈ 1, Q ≈ 0, η ≈ 1
  - Docht-Kerze: cos φ klein, S > P, Q_upper/Q_lower korrekt
  - Pfad-Effizienz: monotone Serie → +1; Rundreise auf Start → 0
  - Gleichgerichtete Phasoren → Resonanz ≈ 1; gegenläufige → Dip-State
  - Determinismus: identische Eingaben → identische Ausgaben

**Hinweis:** Phasor-Metapher ist bewusst einfach zu halten; operative
Targets werden in reeller Algebra gerechnet (Winkel = fester Wert aus
Horizont T, siehe §9.6 der Wissensdatenbank). Die im Chat entstandenen
Module `breakout_power_triangle.py`/`complex_power_engine.py` sind
Konzeptreferenzen — diese eine Modul-Gruppe baut.

---

## Phase MP-05 — Hourly Screening Gate & Symbol-Ranker

**Dateien:**
- `sigma/orchestration/hourly_screening_gate.py` (neu):
  - 1 Scan pro geschlossener 1h-BTC-Bar; Phasen
    `SCAN_AND_DEPLOY` (Min 00–05), `ACTIVE_EXECUTION` (05–48),
    `PRE_CLOSE_UNWIND` (48–55), `IDLE_WAIT` (55–60)
  - Idempotenz: Scan pro Bar-Zeitstempel nur einmal
- `sigma/signals/high_beta_ranker.py` (neu):
  - Score aus β, r, RVOL, Spread/Tiefe (Penalty);
    Hard-Filter: r ≥ 0,70–0,85, β ≥ 1,5, RVOL ≥ 1,2–1,8, Spread-Cap;
    Strategieempfehlung: β ≥ 2,8 & RVOL ≥ 2,5 → Sniper/25x-Modus,
    sonst DCA 5–10x; Blacklist-Gründe (Unlocks/Thin-Book) als Feld
  - baut auf `correlation_scout` auf (ersetzen/erweitern, nicht duplizieren)
- Orchestrator: Ranker-Gate nur als Klassifikation/ctx-Feld;
  **kein neuer Auto-Deploy** bis MP-07 existiert
- Tests: `tests/test_hourly_ranker.py`
  - Zweiter Scan in derselben Stunde wird gesperrt
  - Ranking sortiert synthetische Universen korrekt
  - Thin-Spread-Symbol fliegt raus; Paper-Flag auf Weekend-Routen bleibt

---

## Phase MP-06 — Polymarket Layer 0 (Feed + Dichte + Term-Struktur)

**Dateien:**
- `sigma/ports/polymarket_port.py` (neu, Port-Interface; Adapter optional):
  - `fetch_event_odds(event_slug)` — nur liquide Märkte (Volumen-Filter),
    fail-closed ohne API
- `sigma/signals/polymarket_density.py` (neu):
  - Strikes + Yes-Preise → implizite Bin-Wahrscheinlichkeiten
    (Breeden-Litzenberger-Analogie); Erwartungswert μ, wahrscheinlichster Korridor
  - Platt-Scaling-Kalibrierung (Parameter aus historischem Brier-Abgleich,
    Default konservativ)
- `sigma/signals/polymarket_trajectory.py` (neu):
  - T+1h/T+2h/T+4h/EOD → μ(T)-Kurve, Δμ/ΔT-Geschwindigkeit,
    Bias (`STRONG_BULLISH`/`CHOP` …), optimales Fenster T×0,75
- Orchestrator: bestehendes `layer0_pre_regime` um echte Feed-Daten
  erweitern (optionaler Port wie andere Ports); ohne Feed weiter
  `valid=False`
- Tests: `tests/test_polymarket_layer0.py`
  - Konstruierte Strikes (0,85/0,62/0,25) → korrekte Bin-Dichten
  - Term-Struktur mit steigendem μ → bullish; flach → CHOP
  - Degraded/synthetic Payload bleibt abgelehnt
  - Kein Netzwerk im Test — Payloads injiziert

---

## Phase MP-07 — Quantum-Sniper-Strategie (Phase-2 der Wave-Diskussion)

**Dateien:**
- `sigma/strategies/quantum_sniper_dca.py` (neu, `BaseStrategy`):
  - Plan nur wenn: `wave.status == COLLAPSED_INTO_ZONE` (15m BTC),
    LTF-Retest-Signal (Two-Bar-Thrust auf 1m/5m oder FVG-Touch),
    Ranking-Freigabe durch MP-05-Ranker
  - DCA-Ladder aus MP-02 (4–6 Stufen, 0,2 %, 1,15x, 1,5–3 % TP)
  - TTL: Trades nur in Minute 0–48 der 1h-Bar; danach FLAT;
    SL aus MP-01 (0,5 % über Liq bzw. unter Range-Low)
- `sigma/execution/quantum_sniper_pipeline.py` (neu):
  - Orchestrierung der Datenfluss-Kette (15m-Evaluation → 1m-Retest →
    Intent); nur Paper; keine echten Orderaufrufe
- Orchestrator: Template-Registrierung `quantum_sniper_dca`,
  Session-Unabhängig von NY/London nur bei Ranking+Wave
- Tests: `tests/test_quantum_sniper.py`
  - Vollzyklus: 15m-Expansion → FVG → 1m-Dip in CE50 → BUY-Intent mit TP/SL
  - TTL: Minute 50 → FLAT
  - Range-Low-Breach → kein Intent (unwind)
  - Ohne Ranker-Freigabe → FLAT

---

## Phase MP-08 — Exhaustion & Async-Unwind

**Dateien:**
- `sigma/signals/volatility_exhaustion.py` (neu):
  - BBW-Einbruch > 40 % vom Tageshoch (5m), OI-Divergenz (Preis neues
    Hoch, OI fallend), CVD-Flachlinie/Umkehr → Exhaustion-Score
- `sigma/strategies/async_unwind.py` (neu, `BaseStrategy`):
  - Reihenfolge: Gewinner-Seite 100 % schließen → auf Pullback zu
    VWAP/EMA20 warten → Verlierer-Seite schließen; Net-PnL-Guard
    (Verlierer-Verlust > 50 % des realisierten Gewinns → trotzdem schließen)
  - Slippage-Schonung: keine gleichzeitigen Market-Dumps
- Tests: `tests/test_exhaustion_unwind.py`
  - BBW/OI/CVD-Konstruktion → Exhaustion erkannt
  - Unwind-Reihenfolge im Intent-Record korrekt
  - Kein Unwind intakter Trends

---

## Phase MP-09 — Dynamischer Pine-Provisionierer

**Dateien:**
- `sigma/strategies/dynamic_pine_provisioner.py` (neu):
  - Input: Strategie-Request (Symbol, Entry/TP/SL/Leverage/strategy_id/Secret)
  - Output: vollständiges, kompilierfähiges Pine-v6-Skript mit
    injizierten Konstanten, Schema-A-Webhook-Payload
    (BUY/SELL/CLOSE groß, ticker, price, stop_loss, fixed_leverage, secret),
    bar-close-Alerts, look-ahead-frei (`barmerge.lookahead_off`, [1]-Offset)
  - De-Provisioning-Hinweis nach TTL/TP
  - **Auto-Härtung fremder Pine-Skripte (`harden_pine_code()`):**
    v5/v6-Fremdcode (Gemini, manuell, TV-Bibliothek) wird beim
    Provisionieren automatisch umgeschrieben — Schema-A-`alert_message`
    an jeden Entry/Exit, Fremd-Webhooks ersetzt, Bar-Close-Guard
    (`barstate.isconfirmed`) + `lookahead_off` ergänzt, Standard-Header
    (`initial_capital=10000`, Order 100 USD cash, `pyramiding=1`,
    Commission 0,04 %, `calc_on_every_tick=false`), eindeutige
    `idempotency_key` je Alert (Tracking gegen Vertauschung/Doppel-
    ausführung), strategy_id/Secret/TTL injiziert; nicht härtbar →
    fail-closed ohne Deploy. Transport-Härtung nur über den regulären
    Scout→Modal→kraken_paper-Pfad.
- Tests: `tests/test_dynamic_pine.py`
  - Generierter Code enthält alle Konstanten + Schema-A-Felder
  - Statische Checks: kein `lookahead_on`, kein Repaint-Muster;
    zwei verschiedene Requests → zwei verschiedene Skripte

**Nicht im Scope:** TradingView-Upload selbst (Loop B/TV-Seitig vorhanden).

---

## Phase MP-15 — Fraktaler High-Leverage-Einzeltrade

**Warum:** Live-Beleg (§8 Regel 8 der Wissensdatenbank): der größte
Verlust im erfolgreichen Bot-Run war die manuelle Exit-Latenz
(15–20 % Peak-PnL). Der fraktale Einzeltrade bindet keine DCA-Marge,
erlaubt 20–50x Hebel und sichert über gestaffelte TPs + automatischen
Fee-Covered-Break-Even ab TP1.

**Dateien:**
- `sigma/execution/fee_covered_breakeven.py` (neu, oder in MP-01
  `risk_guards.py` integriert — dann importieren):
  `fee_covered_stop(entry_price, side, offset_pct=0.0005)`;
  Richtungslogik long `×1,0005` / short `×0,9995`.
- `sigma/strategies/fractal_directional.py` (neu, `BaseStrategy`):
  - Entry nur bei Ranker-Freigabe (`sniper_hedge`/High-β) UND
    BTC-Lead-Signal (Breakout/Retest, MP-03/MP-05) UND Minute 5–48.
  - Plan-Intent mit TP-Staffel: TP1 40 % bei +1,0 %, TP2 30 % bei
    +2,0 %, TP3 20 % bei +3,5 % (Defaults als Konstanten, ATR-Skalierung
    optional), Runner 10 % mit ATR-Trailing.
  - Initialer SL 0,6 % bzw. MP-01-Liq-Puffer (strengerer gewinnt).
  - Nach TP1-Fill: `update_sl`-Intent auf Fee-Covered Break-Even
    (Pflicht, automatisiert, kein manueller Eingriff).
  - Kill-Switch: Exhaustion (MP-08) ODER Sweep der Zielliquidität ODER
    Minute 55 → FLAT des Runners; kein Warten auf Menschen.
- Webhook-Payload nach §12 (open mit tp1..3 + runner + offset;
  update_sl nach TP1).
- Tests: `tests/test_fractal_directional.py`
  - TP-Preise/Mengen-Verhältnisse (40/30/20/10, Summe 100).
  - Fee-Covered: long SL = entry×1,0005 > entry; short = entry×0,9995.
  - SL-Logik: Liq-Puffer-Regel schlägt 0,6 % wenn strenger.
  - TP1-Event → update_sl-Intent existiert zwingend.
  - Minute 55 / Exhaustion → Runner-FLAT.
  - Ohne Ranker-Freigabe → kein Entry.

**Nicht im Scope:** Live-Orders (Paper), DCA-Grid-Logik (MP-02 bleibt
unverändert), keine Hebel-Freigabe > MP-05-Empfehlung.

---

## Phase MP-10 — Orderflow-Validator (optional, L2)

**Dateien:**
- `sigma/signals/orderflow_validator.py` (neu):
  - FootprintBar-Datenstruktur; Stacked Diagonal Imbalances (≥3 Level, 3:1);
    CVD-Absorption (neues Tief, Delta positiv); POC/HVN-Konfluenz;
    Iceberg-Erkennung (erneuerte sichtbare Größe);
    Konfidenz-Score 0–1; ohne Tiefen-Daten → `valid=False` (fail-closed)
- Tests: `tests/test_orderflow_validator.py`
  - Konstruierte Absorption → Score ≥ Schwelle
  - Leerer/nicht-L2-Feed → kein Signal

---

## Phase MP-11 — ONNX-Tensor & Inferenz

**Dateien:**
- `sigma/core/onnx_quantum_tensor.py` (neu):
  - 16-Feature-Tensor `[1,16]` float32, strikt auf [−1,1]/[0,1] geclipt;
    die Kern-9-Features mit den Formeln aus Wissensdatenbank §11
    (cos_φ-Bar, P_norm, Q_norm, pos_00=tanh, m_tangent=arctan·2/π,
    P_cal, pos_EQ, d_CE=tanh, TTL_norm), Features 10–16 wie dort gelistet
  - Jede Feature-Berechnung als reine, einzeln testbare Funktion
  - onnxruntime-Session optional (Modell-Pfad konfiguriert + importierbar);
    deterministische Regel-Fallback-Policy (UTC-safe, TTL_norm ≥ 0,15,
    P_cal ≥ 0,65 + (cos φ ≥ 0,75 oder Discount/Tail) → LONG; symm. SHORT;
    sonst FLAT)
  - **Bar-Lock:** höchstens eine Inferenz-Aktion je Bar-Zeitstempel
  - **Zwei-Stufen-Grenze:** Tensor/Inferenz klassifiziert nur das
    BTC-Makro-Regime (Long/Flat/Short + Hebel); KEINE Symbolauswahl —
    die macht der Ranker (MP-05)
  - Modell-Architektur (für späteres Training, NICHT in dieser Phase zu
    trainieren): Dual-Head 2×(Linear(16→64)+LayerNorm+GELU), Policy-Head
    Softmax(3), Leverage-Head Sigmoid → 10+15·σ; opset 14, Ein-/Ausgänge
    `tensor_x`/`action_probs`/`leverage_factor`; Dummy-Export nur für
    Tests erlaubt
  - Latenz-Test: < 2 ms p99
- Orchestrator: `ctx["onnx"]`, FLAT-Entscheidung erzwingt unwind
- Tests: `tests/test_onnx_tensor.py`
  - Shape `(1,16)`/float32; jede Feature-Funktion gegen Konstruktion
    (z. B. Marubozu → cos_φ≈1; pos_EQ in Discount < 0,5; TTL_norm=rest/60)
  - Preis-Skalen-Invarianz (78.000 vs 0,014 → selbe Feature-Wertebereiche)
  - Fallback-Policy ohne Modell; Bar-Lock sperrt zweite Aktion
  - 21:00-UTC / TTL < 10 min → FLAT

---

## Phase MP-12 — Backtest-Harness & Hypothesen

**Dateien:**
- `tests/backtest/test_hypotheses_h1_h6.py` (neu):
  - H1 bias-aligned vs. counter-trend FVGs; H2 Overlap-vs-Off-Session-Fill-Raten;
    H3 Faktor-Sweep 2x–30x mit Walk-Forward; H4 Weekend-Alt-Longs
    (Slippage-Sensitivität); H5 Hurst/MFDFA-Gate-Drawdown-Vergleich;
    **H6 Wochenend-Fakeout-These (Nutzer):** Breakout-Signale Sa/So vs.
    Mo–Fr, inkl. Montag-10:00-UTC-Momentum und Sweep-Reclaim-Muster,
    Slippage-Szenarien (+0,1/+0,3/+0,6 %)
  - H7 (neu): cos-φ-Pfad-Strategie (Efficiency-Ratio): Entry |cos φ| ≥ 0,40
    mit Hysterese, Exit |cos φ| ≤ 0,15, Window-Sweep N = 10/14/20/30,
    Fee 0,06 %/Roundtrip, 1-Bar-Lag — Erwartungswert vs. Benchmark
- `sigma/backtest/lookahead_pipeline_check.py` (neu):
  - „Break the pipeline on purpose“: bewusstes Leck muss erkannt werden
  - HTF-Closed-Bar-Invariante als Assertion über alle Ticks
- **VectorBT als Standard-Backtest-Engine** (vektorisierte Sweeps);
  bestehende TV-CSV-Infrastruktur (`app/backtest/`, `app/optimizer/`)
  als Datenquelle, keine Doppelung

## Phase MP-16 — Research-Dashboard (Lightweight Charts) & cos-φ-Backtest

**Warum:** Hypothesen brauchen ein visuelles Prüfwerkzeug; die im Chat
konzipierte Lightweight-Charts-App validiert Features (cos φ, P/Q/S,
Signale/Marker/Equity) unabhängig von der Live-Pipeline.

**Dateien:**
- `sigma/backtest/power_factor_backtest.py` (neu):
  - cos-φ-Strategie (Pfad-Wirkungsgrad) mit Hysterese-State-Machine:
    long ≥ +0,40 / short ≤ −0,40 / flat bei |cos φ| ≤ 0,15;
    Position mit 1-Bar-Lag, Roundtrip-Fee 0,06 %;
    Metriken: Return, Max-DD, Sharpe (8760 1h-Bars/Jahr), Win-Rate,
    Profit-Faktor, Trade-Zahl; Parameter (N, Schwellen) als Sweep
- `app/dashboard/tv_lightweight_export.py` (neu):
  - Export Kerzen/Indikator/Equity/Marker als JSON (UNIX-Sekunden,
    aufsteigend); eigenständiges HTML/JS mit 3 synchronisierten Panes
    (Candles+Marker, cos φ mit Schwellenlinien, Equity vs. Benchmark)
  - Reines CDN-Dashboard, kein Build-Prozess; Daten aus TV-CSV/
    Backtest-Results, keine Live-Verbindung
- Tests: `tests/backtest/test_power_factor_dashboard.py`
  - Marker nur an Positionswechseln; Zeitreihe streng sortiert;
    Backtest auf synthetischen Trend-/Chop-/Bär-Sequenzen (Stil §MP-12):
    Trend → long-Gewinne, Chop → flat (kein Whip-Saw-Tod), Bär → short
  - Hysterese: Signal bleibt bis Exit-Schwelle bestehen
- **Nicht im Scope:** Live-Daten, Orderausführung, Alert-Versand.

---

## Phase MP-13 (optional) — Multi-Asset XAU/XAG/Forex

---

## Phase MP-13 (optional) — Multi-Asset XAU/XAG/Forex

- Venue-Ports (MT5/IB-Schnittstelle) als optionale Adapter hinter
  bestehendem `MultiAssetRouter`; Marktzeiten-Kalender;
  XAU→XAG Lead-Lag wie BTC→Alt; Krypto füllt Wochenenden.
- Keine Krypto-Logik ändern.

## Phase MP-14 (optional) — Pre-Event Doppel-Hedge-Straddle

- `sigma/strategies/event_straddle.py`: doppelte DCA-Leitern ±2/4/6,5 %,
  Mini-Base 10 %/10 %, TTL 2–4 h Neutral-Abbruch, Trailing ab +3 %,
  Net-Profit-Guarantee (Verlierer-SL bei 50 % des realisierten Gewinns),
  Event-Trigger nur aus Layer-0/Session-Kalender.

---

## Phase MP-17 — Frontend-Panels (stilgetreues Terminal-UI)

Vollständige Spezifikation: **`docs/SIGMA-UI-SPEZIFIKATION.md`** (Funktions-→Panel-Mapping,
Panel-Inhalte, Presets, Sicherheitsregeln). Kernpunkte:

- 12 neue Panels im bestehenden Dock/Registry-Muster (`PANEL_REGISTRY`,
  `PanelShell`/`Stat`/`FeedBadge`/`usePoll`): QuantumRegime (Hourly-Cycle-Band,
  Wellen-Status, SessionClock, Throttle, Polymarket-Bias, ONNX-Köpfe),
  MarketGeometry (Zonen/FVG/Envelope + Chart-Overlays), PowerPhysics
  (cos-φ-Meter, S/P/Q-Normen, Pfad-Serie, MTF-Resonanz), SymbolScout
  (Ranker-Tabelle, Blinded-Toggle, 1-Scan-pro-Bar), Polymarket
  (Dichte-Histogramm, Term-Struktur, Kalibrierung), LadderArchitect
  (Leiter-Werkbank mit Live-Guard-Leiste), FractalTrade (TP-Staffel 40/30/20/10,
  Fee-Covered-BE-Badge, Kill-Switch), Provisioner (Pine-Agenten-Tabelle),
  OnnxBrain (16er-Tensor, Dual-Head, Bar-Lock), RiskGuard (Liq-Puffer,
  Cooldown, nicht abschaltbare Regel-Badges), Unwind (Exhaustion-Gauge,
  Unwind-Sequenz), ResearchLab (H1–H7, 3-Pane-Dashboard, Sweep-Tabelle).
- 3 neue Presets: `QUANTUM_OPS`, `POSITION_DESK`, `RESEARCH_LAB`.
- Dünne `/api/v1/sigma/*`-Leseendpunkte + Research-Jobs; ohne Fachmodule
  strukturierte Leerantworten (fail-closed, Leerzustände im UI).
- Alle Schwellen/Defaults im Settings-Panel verstellbar; Sicherheitsregeln
  (Hard-Stop, Grid-Tiefe ≥ 6 %, Fee-BE) nicht abschaltbar; Schreibzugriffe
  nur modal mit Operator-Token; keine neuen Dependencies; `npm run lint` grün.

---

## Globale Definition of Done (jede Phase)

1. `pytest` grün (neue Tests + bestehende Suite bleibt grün)
2. Keine neuen Platzhalter/Stubs; vollständige Typannotationen
3. Dataclasses mit `to_dict()`; Fail-Closed bei fehlenden Daten
4. Kein Look-Ahead (nur closed bars); Beweis per Test
5. Kein Live-Orderpfad in neuen Modulen — `kraken_paper` only
6. Keine Duplizierung bestehender Module; bestehende Gates unverändert
7. Kurzer Modul-Header nach Sigma-Konvention (Datei/Zweck/System/Knoten)
