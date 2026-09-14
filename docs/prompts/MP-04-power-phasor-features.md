# Master-Prompt MP-04 — Price-Action-Physics & Phasor-Features

Lies `docs/SIGMA-WISSENSDATENBANK.md` §9 (insbesondere §9.5:
Price-Action-Physics-Featurevektor mit kanonischen Formeln, §9.6:
konjugiert-komplexe Zeigerrechnung und MTF-Harmonik).
Wichtig: operative Zielgrößen werden in **reeller Algebra** gerechnet
(P, Q, S, cos φ aus Kerzendaten); der Phasenwinkel ist ein fester Wert
aus dem Zeithorizont, kein frei gedrehter Trading-Winkel.
Die Chat-Module `breakout_power_triangle.py`/`complex_power_engine.py`/
`three_phase_oscillator.py` sind Konzeptreferenzen, kein Code-Grundgerüst.

## Auftrag — drei Module unter `sigma/signals/`

### 1. `power_triangle.py`
- `price_action_physics(candles, atr_period=14)` → ein DataFrame oder
  eine Liste von Dataclass-Datensätzen (Bar-Timestamp + Felder),
  berechnet exakt nach §9.5:
  - True Range (max(H−L, |H−C_prev|, |L−C_prev|))
  - ATR als Wilder-RMA (`ewm(alpha=1/period, adjust=False)`)
  - `S_norm = (High−Low)/ATR`
  - `P_norm = |Close−Open|/ATR` und `P_norm_signed = (Close−Open)/ATR`
  - obere Dochtlänge `High−max(Open,Close)`, untere `min(Open,Close)−Low`,
    daraus `Q_norm`, `Q_upper_norm`, `Q_lower_norm`,
    `Q_bias = Q_lower − Q_upper`
  - `eta_efficiency = |Close−Open|/(High−Low)` (Bar-Wirkungsgrad)
  - Jeder Nenner mit ε-Schutz (1e-9); keine NaN/Ausgaben
- `cos_phi_bar(candle)` = sign(Close−Open) · η ∈ [−1,1].
- `cos_phi_path(close, window=20, use_true_range=False, high=None, low=None)`:
  (C_t − C_{t−N}) / Σ|ΔC| (bzw. / ΣTR); 0,0 wenn der Pfad 0 ist;
  geclipped auf [−1,1]. Das ist die vorzeichenbehaftete Pfad-Effizienz
  (Kaufman Efficiency Ratio) über N Bars.
- Klassifikations-Helfer nach §9.2/§9.5:
  η ≥ 0,85 = `SOLID_TREND_EXPANSION`; η < 0,30 = `WICK_REJECTION`;
  P_norm > 1,2 = `EXPLOSIVE_EXPANSION`; S_norm > 2,0 = `VOLATILITY_CLIMAX`.
  Alle Schwellen als Benennungskonstanten.

### 2. `hilbert_phasor.py`
- Aus einer Preisserie In-Phase `I` und Quadratur `Q` über eine
  deterministische, nachvollziehbare Approximation (z. B. geglättete
  Preisdifferenz als Q, geglätteter Preis als I — nur numpy/pandas,
  keine externen DSP-Bibliotheken).
- Rückgabe: Amplitude √(I²+Q²), Phasenwinkel atan2(Q,I) in Grad.
- Deterministisch: gleiche Eingabe → gleiche Ausgabe.

### 3. `mtf_resonance.py`
- Zwei Phasoren (HTF, LTF): **Konjugatprodukt** `S = U · conj(I)`
  (niemals U·I — das addiert die Referenzwinkel und ist sinnlos,
  §9.6); `delta_phi = angle(S)`, `resonance = cos(Δφ)`.
- `resonance ≥ 0,75` → `CONSTRUCTIVE_RESONANCE`;
  `resonance < −0,5` bei HTF-bullisch/LTF-bärisch → `DIP_CHARGING`.

Alle Features skaleninvariant (ATR-/Range-normalisiert),
Dataclasses mit `to_dict()`, Modul-Header nach Repokonvention,
volle Typen.

## Tests (`tests/test_power_phasor.py`)
- Reine Marubozu-Kerze (keine Dochte): η ≈ 1, Q ≈ 0, P_norm ≈ S_norm.
- Kreuzkerze mit langen Dochten, kleinem Body: η < 0,3, Q_upper/Q_lower
  korrekt zugeordnet; S_norm > P_norm.
- Pfad-Effizienz: monotone 10-Steigungen-Serie → cos_phi_path ≈ +1;
  Serie, die auf dem Start schließt (Rundreise) → 0; monotone Abwärts −1.
- ε-Schutz: flache Bars (H==L) → keine NaN, keine Exceptions.
- Sinus-artige Preisserie für den Phasor: Amplitude stabil, Winkel
  dreht gleichmäßig; Determinismus über zwei Aufrufe identisch.
- Resonanz: gleichgerichtete Phasoren ≈ 1; gegenläufige → DIP-Charging.
- Klassifikation: konstruierte Climax-Kerze (S_norm > 2) wird als
  VOLATILITY_CLIMAX getaggt.

## Nicht im Scope
- Keine Strategien, kein Orchestrator-Deploy, keine Orders.
- Kein ONNX (MP-11), keine Backtest-Auswertung (MP-12/MP-16).

## Abnahme
Pytest grün; keine neuen Abhängigkeiten außer den im Repo vorhandenen
(numpy/pandas — `requirements.txt` prüfen).
