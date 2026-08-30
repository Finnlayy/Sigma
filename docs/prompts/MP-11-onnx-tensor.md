# Master-Prompt MP-11 — ONNX-Observation-Tensor & Inferenz

Lies `docs/SIGMA-WISSENSDATENBANK.md` §11 (ONNX-Tensor mit den
kanonischen 9-Kern-Feature-Formeln, Dual-Head-Modellarchitektur,
Bar-Lock, Zwei-Stufen-Grenze) und §9.5 (Price-Action-Physics-Features).
Voraussetzungen: MP-04 (cos φ / P_norm / Q_norm / η), MP-05
(Ranker/Screening), MP-06 (Polymarket-Kalibrierung) liefern die Eingaben.
Prüfe, ob `onnxruntime` in `requirements.txt` steht bzw. als optionale
Abhängigkeit geführt werden kann. Chat-Module
(`onnx_quantum_tensor_pipeline.py` o.ä.) sind Referenzen, keine Basis —
ihr 9D-Tensor mit teils kaputten Indizierungen (`tensor =` statt
`tensor[0,k] =`) darf nicht kopiert werden.

## Feature-Verdrahtung (Schnittstellen / Quellen)

Der Tensor bekommt **nur geschlossene Bars** (Loop-C/Feed-Pfad,
HTF mit `[:-1]`/`htf_ready`, Hartregel 7) — kein eigener WebSocket-/
Tick-Orchestrator. Jede Feature-Quelle ist ein bestehendes bzw. per
Roadmap entstehendes Modul; fehlt eine Quelle → sicherer Default
(0 bzw. neutral), niemals Ausnahme schlucken oder synthetisieren:

| # | Feature | Quelle / Modul | Anmerkung |
|---|---|---|---|
| 1 | `cos_phi` (Bar) | MP-04 Price-Action-Physics | OHLCV geschlossen |
| 2 | `P_norm`, `Q_norm`, `Q_upper/lower`, `Q_bias` | MP-04 (`price_action_physics`) | ATR = Wilder-RMA(TR,14) |
| 3 | `pos_00`, `m_tangent` | MP-03 (`daily_open_envelope`) | 00:00-UTC-Anker |
| 4 | `P_cal` | `polymarket_layer0` / MP-06 | **Platt-kalibriert**, nicht Rohquote; ohne Feed neutral, kein Gate |
| 5 | `pos_EQ` | `quantum_wave_collider` (Range H/L) | existiert |
| 6 | `d_CE` (FVG-CE50) | `quantum_wave_collider`/`htf_features` (CE50 existiert in fractal_scaling) | |
| 7 | `TTL_norm` | `hourly_screening_gate` (MP-05): **Restminuten bis 1h-Bar-Close / 60** | NICHT Sekunden-der-Minuten-Uhr |
| 8 | UTC-Safe-Flag | `session_clock` (existiert; 21:00–22:00-Quarantäne) | |
| 9 | RVOL | `correlation_scout`/Universum-Feed (MP-05) | |
| 10 | CVD-Absorption | MP-10 Orderflow-Port (optional) | ohne L2-Feed → 0, fail-closed |
| 11 | Hurst | `dual_hurst` (existiert) | |
| 12 | Liq-Distanz | MP-01 Risk-Guards / Venue-Daten | ohne Daten → neutral |
| 13 | Two-Bar-Thrust | MP-03 | |
| 14 | FVG-Touch | `htf_features` (existiert) / MP-03 | |
| — | Output | Orchestrator `ctx["onnx"]` | nur BTC-Makro Long/Flat/Short + Hebel; **keine Symbole im Tensor** (Ranker = Stufe 2) |

Modelldatei nur über Konfigpfad + optionalen onnxruntime-Import;
ohne beides läuft die deterministische Fallback-Policy.

### Achtung: Chat-Entwürfe nur als Formelreferenz
Im Chat kursierende Gemini-Skripte (`sigma_onnx_quantum_pipeline.py`,
`sigma_model_exporter.py`, `sigma_live_feed_orchestrator.py`,
`knn_physics_engine.py`, `power_factor_backtest.py`) sind **nicht
kanonisch** und werden nicht kopiert: Tensor dort nur **9D** (Sigma =
16D), `TTL_norm` falsch aus Sekunden-der-Minute berechnet, Fallback
ohne TTL-/21:00-Gates, `P_cal` ohne Platt-Skalierung, ein eigener
WebSocket-`LiveFeedOrchestrator` mit `except: pass` (fail-open!)
außerhalb der Loop-Architektur, und kNN statt ONNX-Dual-Head. Die
PyTorch-Export-Struktur (2× Linear→LayerNorm→GELU, Softmax 3,
10+15·σ, opset 14, I/O `tensor_x`/`action_probs`/`leverage_factor`)
stützt die §11-Spezifikation — Input-Dim ist aber 16, nicht 9.

## Auftrag — `sigma/core/onnx_quantum_tensor.py`

1. **Feature-Builder (reine Einzelfunktionen + Gesamt-Tensor):**
   Tensor `[1,16]` float32, Kern-Features exakt nach §11:
   - `cos_phi = clip((C−O)/(H−L+ε), −1, 1)`
   - `P_norm = |C−O|/ATR14`, `Q_norm = (upper+lower wick)/ATR14`
   - `pos_00 = tanh((C − open_00:00)/(2·ATR))`
   - `m_tangent = arctan((C − open_00:00)/min_since_00) · 2/π`
   - `P_cal = clip(platt_scale(poly_raw), 0, 1)`
   - `pos_EQ = clip((C − range_low)/(range_high − range_low + ε), 0, 1)`
   - `d_CE = tanh((C − ce50)/ATR)`
   - `TTL_norm = restminuten_der_1h_Bar/60`
   - Features 10–16 nach §11 (UTC-Safe-Flag, RVOL, CVD, Hurst,
     Liq-Distanz, Two-Bar-Thrust, FVG-Touch).
   - Fehlt eine Quelle → sicherer Default (fail-closed, Gate-Wert 0).
   - Skaleninvarianz zwingend: BTC 78.000 und Alt 0,014 mit gleichen
     Ratios → gleiche Tensorwerte (Test).
2. **Inferenz-Wrapper:** onnxruntime nur, wenn Pfad konfiguriert UND
   importierbar; sonst `model_available=False`. Erwartete Modell-
   Schnittstelle (späteres Training, in dieser Phase NICHT trainieren):
   Input `tensor_x [N,16]`; Outputs `action_probs` (Softmax über
   Long/Flat/Short) und `leverage_factor` (10 + 15·sigmoid ∈ [10,25]).
   Ein Dummy-Modell (gleiche I/O-Spezifikation) darf nur für Tests
   erzeugt werden.
3. **Deterministische Fallback-Policy (produktiv ohne Modell):**
   TTL_norm < 0,15 oder UTC 21:00–22:00 → FLAT; P_cal ≥ 0,65 und
   (cos_phi ≥ 0,75 oder pos_EQ im Discount mit Kauf-Tail Q_bias) → LONG;
   spiegelbildlich SHORT; sonst FLAT/HOLD.
4. **Bar-Level Lock:** pro Bar-Zeitstempel höchstens eine Aktion;
   wiederholter Aufruf innerhalb des Bars → `BLOCKED_BY_BAR_LOCK`.
5. **Zwei-Stufen-Grenze:** der Wrapper kennt KEINE Symbole; er
   klassifiziert nur das BTC-Makro-Regime. Symbol-Auswahl bleibt beim
   Ranker (MP-05). Keine Ticker-Strings im Tensor.
6. Orchestrator: `ctx["onnx"]` befüllen; FLAT aus Modell/Fallback →
   bestehender unwind-Pfad; keine Orders aus dem Orchestrator.

## Tests (`tests/test_onnx_tensor.py`)
- Shape `(1,16)`, dtype float32, alle Werte im definierten Bereich.
- Jede Feature-Funktion einzeln gegen konstruierte Kerzen
  (Marubozu → cos_phi≈1, P_norm hoch; Doji → ~0; pos_EQ unter 0,5 in
  Discount; TTL_norm = Rest/60; pos_00 Vorzeichen korrekt).
- Skaleninvarianz wie oben.
- Fallback ohne Modell: 21:30 UTC → FLAT; TTL 8 min → FLAT;
  alle Long-Bedingungen erfüllt → LONG; ohne P_cal → FLAT.
- Bar-Lock: zweiter Aufruf mit gleichem Bar-Timestamp → BLOCKED.
- Determinismus: 100 Aufrufe gleiche Eingabe → gleiche Ausgabe.
- Latenz: Builder+Fallback p99 < 2 ms (tolerante CI-Schwelle).

## Nicht im Scope
- Modelltraining, Echt-Markt-Feeds, neue Strategien,
- Symbol-Logik im Tensor (ausschließliche Ranker-Zuständigkeit).

## Abnahme
Pytest grün; ohne onnxruntime/Modell-Datei läuft alles über die
Fallback-Policy, nichts darf abstürzen.
