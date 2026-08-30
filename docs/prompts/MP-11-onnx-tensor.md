# Master-Prompt MP-11 — ONNX-Observation-Tensor & Inferenz

Lies `docs/SIGMA-WISSENSDATENBANK.md` §11 (ONNX-Tensor, 16 Features,
Determinismus, Fallback-Policy). Voraussetzungen: MP-03/MP-04 (Signale),
MP-05 (Ranker/Screening), MP-06 (Polymarket-Bias) liefern die
Eingabewerte. Prüfe, ob `onnxruntime` in `requirements.txt` steht bzw.
als optionale Abhängigkeit geführt werden kann.

## Auftrag — `sigma/core/onnx_quantum_tensor.py`

1. **Feature-Builder** (reine Funktion, deterministisch):
   Tensor `[1,16]` float32 mit den 16 Werten aus §11:
   cos φ, Resonanz, Δμ, FVG-Tiefe (ATR), Hurst, Walk-Ratio,
   Session-Aktivität, Minuten-nach-Open, TTL, Throttle-Level,
   Wellenzone/-breite, 21:00-Flag, Ranker-Score, Liquidation-Puffer,
   Makro-Bias, ATR-Ratio.
   - Alle Werte auf definierte Bereiche clippen/normalisieren
     ([0,1] oder [−1,1]); skaleninvariant: BTC bei 78.000 $ und ein
     Alt bei 0,014 $ müssen identische Feature-Wertbereiche liefern.
   - Fehlt eine Quelle → sicherer Default (fail-closed: z. B.
     Gate-Wert = 0 statt 1).
2. **Inferenz-Wrapper**:
   - Lädt ONNX-Modell nur, wenn Datei konfiguriert UND onnxruntime
     importierbar; sonst `model_available=False`.
   - Inferenz-Ausgabe: Aktion {OPEN_LONG, OPEN_SHORT, HOLD, FLAT} +
     Konfidenz; FLAT erzwingt Unwind.
   - Latenz: p99 < 2 ms (Test mit Zeitmessung, tolerante Schwelle im CI).
3. **Deterministische Fallback-Policy** (reine Regel-Funktion,
   läuft ohne Modell): UTC-safe (21:00–22:00 Quarantäne → FLAT),
   TTL ≥ 10 min, Polymarket-Bias ≥ 0,65 UND (cos φ ≥ 0,75 oder
   Discount-Konfluenz) → OPEN; sonst HOLD/FLAT nach Rangfolge.
4. Orchestrator: `ctx["onnx"]`-Feld befüllen (hinter bestehendem
   Port-/Feature-Muster); FLAT aus dem Modell → bestehender
   Unwind-Pfad. Keine neuen Orders im Orchestrator.

## Tests (`tests/test_onnx_tensor.py`)
- Shape `(1,16)`, dtype float32, alle Werte im definierten Bereich.
- Skaleninvarianz: zwei Eingaben mit gleichen Ratios, aber 10⁶-fach
  verschiedenen Preisen → gleiche Tensorwerte.
- Fallback-Policy: 21:30 UTC → FLAT; TTL 8 min → FLAT; alle
  Bedingungen erfüllt → OPEN_LONG; ohne Poly-Bias → HOLD.
- Ohne onnxruntime/Modell → Fallback läuft, kein Absturz.
- Determinismus: 100 Aufrufe gleiche Eingabe → identische Ausgabe.

## Nicht im Scope
- Kein Training eines Modells (es wird nur konsumiert; ein
  Dummy-/Identitätsmodell darf für Tests erzeugt werden).
- Keine neuen Strategien.

## Abnahme
Pytest grün; ohne installiertes onnxruntime und ohne Modell-Datei
darf nichts abstürzen.
