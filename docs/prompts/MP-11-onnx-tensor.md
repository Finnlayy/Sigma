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
