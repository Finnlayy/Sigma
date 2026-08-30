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
| MP-05 | `MP-05-hourly-gate-symbol-ranker.md` | 1-Scan-pro-1h-Bar-Gate + High-Beta-Symbol-Ranker (β/RVOL/Spread-Score, Strategieempfehlung) | MP-01, MP-03 |
| MP-06 | `MP-06-polymarket-density.md` | Polymarket-Feed-Adapter (optionaler Port), implizite Dichte, Term-Struktur-Trajektorie, Brier/Platt-Kalibrierung | MP-05 |
| MP-07 | `MP-07-sniper-strategy-phase2.md` | Quantum-Sniper-Strategie (15m→1m Retest + DCA-Ladder, TTL 45–48 min) als BaseStrategy | MP-02, MP-03, MP-05 |
| MP-08 | `MP-08-exhaustion-unwind.md` | Volatilitäts-Exhaustion-Detektor (BBW/OI/CVD) + asynchroner Unwind-Template | MP-01, MP-04 |
| MP-09 | `MP-09-dynamic-pine-provisioner.md` | Dynamischer Pine-v6-Provisionierer pro gescoutetem Symbol + Schema-A-Webhook | MP-07 |
| MP-10 | `MP-10-orderflow-validator.md` | L2/Footprint-Orderflow-Validator (Stacked Imbalances, CVD-Absorption, POC-Konfluenz, Iceberg) — optional, fail-closed ohne Tiefe | MP-04 |
| MP-11 | `MP-11-onnx-tensor.md` | 16-Feature-Observation-Tensor + ONNX-Runtime-Inferenz mit deterministischem Fallback | MP-04, MP-05, MP-06 |
| MP-12 | `MP-12-backtest-hypotheses.md` | Backtest-Harness: Faktor-Sweep H3, Hypothesen H1–H5, Look-ahead-Pipeline-Test, Walk-Forward | MP-02, MP-07 |
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
- `sigma/signals/power_triangle.py` (neu):
  - `power_triangle(candle, atr, volume_ratio)` → P (Körper),
    Q (Docht), S = √(P²+Q²), `cos_phi = P/S`
  - Klassifikation: `cos_phi ≥ 0,85` → fester Move;
    `< 0,30` → Docht-Fakeout; Zielzonen P (Rekalibrierung) und S (TP)
  - Alles skaleninvariant (ATR-Einheiten)
- `sigma/signals/hilbert_phasor.py` (neu):
  - In-Phase I / Quadratur Q (vereinfachte Hilbert/Differenz-Approximation),
    Amplitude, Phasenwinkel; keine Abhängigkeit von externen Libs
- `sigma/signals/mtf_resonance.py` (neu):
  - HTF-/LTF-Phasor → Winkeldifferenz via Konjugat-Produkt;
    `resonance = cos(Δφ)`; ≥ 0,75 konstruktive Resonanz,
    < −0,5 Dip-Charging (HTF bullish, LTF gegenläufig)
- Tests: `tests/test_power_phasor.py`
  - Reine Marubozu-Kerze: cos φ ≈ 1, Q ≈ 0
  - Docht-Kerze: cos φ klein, S > P
  - Gleichgerichtete Phasoren → Resonanz ≈ 1; gegenläufige → Dip-State
  - Determinismus: identische Eingaben → identische Ausgaben

**Hinweis:** Phasor-Metapher ist bewusst einfach zu halten; operative
Targets werden in reeller Algebra gerechnet (Winkel = fester Wert aus
Horizont T, siehe §9 der Wissensdatenbank).

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
- Tests: `tests/test_dynamic_pine.py`
  - Generierter Code enthält alle Konstanten + Schema-A-Felder
  - Statische Checks: kein `lookahead_on`, kein Repaint-Muster;
    zwei verschiedene Requests → zwei verschiedene Skripte

**Nicht im Scope:** TradingView-Upload selbst (Loop B/TV-Seitig vorhanden).

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
  - 16-Feature-Tensor `[1,16]` float32, strikt auf [−1,1]/[0,1] geclipt
    (Feature-Liste siehe Wissensdatenbank §11), skaleninvariant
  - onnxruntime-Session optional; deterministische Regel-Fallback-Policy
    (UTC-safe, TTL ≥ 0,15, poly ≥ 0,65 + cos φ ≥ 0,75 bzw. Discount → Aktion)
  - Latenz-Test: < 2 ms p99
- Orchestrator: `ctx["onnx"]`, FLAT-Entscheidung erzwingt unwind
- Tests: `tests/test_onnx_tensor.py`
  - Shape/Dtype; Preis-Skalen-Invarianz (78.000 vs 0,014 → selbe Feature-Wertebereiche)
  - Fallback-Policy ohne Modell
  - 21:00-UTC / TTL < 10 min → FLAT

---

## Phase MP-12 — Backtest-Harness & Hypothesen

**Dateien:**
- `tests/backtest/test_hypotheses_h1_h5.py` (neu):
  - H1 bias-aligned vs. counter-trend FVGs; H2 Overlap-vs-Off-Session-Fill-Raten;
    H3 Faktor-Sweep 2x–30x mit Walk-Forward; H4 Weekend-Alt-Longs
    (Slippage-Sensitivität); H5 Hurst/MFDFA-Gate-Drawdown-Vergleich
- `sigma/backtest/lookahead_pipeline_check.py` (neu):
  - „Break the pipeline on purpose“: bewusstes Leck muss erkannt werden
  - HTF-Closed-Bar-Invariante als Assertion über alle Ticks
- Nutzung bestehender TV-CSV/VectorBT-Infrastruktur (`app/backtest/`,
  `app/optimizer/`), keine Doppelung

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

## Globale Definition of Done (jede Phase)

1. `pytest` grün (neue Tests + bestehende Suite bleibt grün)
2. Keine neuen Platzhalter/Stubs; vollständige Typannotationen
3. Dataclasses mit `to_dict()`; Fail-Closed bei fehlenden Daten
4. Kein Look-Ahead (nur closed bars); Beweis per Test
5. Kein Live-Orderpfad in neuen Modulen — `kraken_paper` only
6. Keine Duplizierung bestehender Module; bestehende Gates unverändert
7. Kurzer Modul-Header nach Sigma-Konvention (Datei/Zweck/System/Knoten)
