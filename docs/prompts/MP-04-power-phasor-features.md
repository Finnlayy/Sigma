# Master-Prompt MP-04 — Leistungsdreieck & Hilbert-Phasor

Lies `docs/SIGMA-WISSENSDATENBANK.md` §9 (Elektrotechnik-Features:
Leistungsdreieck, Phasor, MTF-Resonanz, Winkel als fester Wert).
Wichtig: operative Zielzonen werden in **reeller Algebra** gerechnet
(P, Q, S, cos φ aus Kerzendaten) — der Winkel ist ein fester Wert aus
dem Zeithorizont, kein frei gedrehter Trading-Winkel.

## Auftrag — drei Module unter `sigma/signals/`

### 1. `power_triangle.py`
- `power_triangle(candle, atr, volume_ratio=1.0)` → Dataclass mit:
  - `P` = Körperkraft (|close−open|, in ATR-Einheiten)
  - `Q` = Docht-Blindleistung (Summe der Dochte, in ATR-Einheiten)
  - `S` = Scheinleistung = √(P² + Q²)
  - `cos_phi = P / S` (S = 0 → 0, fail-closed)
- Klassifikation: `cos_phi ≥ 0,85` → fester/effizienter Move
  (Ziel ≈ S als TP-Projektion); `cos_phi < 0,30` → Docht-Fakeout
  (Rekalibrierung, Ziel ≈ P).
- Volumen-Service-Faktor: `S × volume_ratio` als Konfidenzgewicht.

### 2. `hilbert_phasor.py`
- Aus einer Preisserie: In-Phase-Komponente `I` und Quadratur `Q`
  über eine deterministische, nachvollziehbare Approximation
  (z. B. geglättete Preisdifferenz als Q, geglätteter Preis als I —
  keine externen DSP-Bibliothezen nötig; wenn, dann nur numpy).
- Rückgabe: Amplitude `√(I²+Q²)`, Phasenwinkel `atan2(Q, I)`.
- Determinismus: gleiche Eingabe → gleiche Ausgabe (Seed/Festkomma).

### 3. `mtf_resonance.py`
- Zwei Phasoren (HTF, LTF): Winkeldifferenz über Konjugat-Produkt,
  `resonance = cos(Δφ)`.
- `resonance ≥ 0,75` → konstruktive Resonanz (Setup gültig);
  `resonance < −0,5` bei HTF-bullish/LTF-bärisch → Dip-Charging-Zustand
  (für Sniper-DCA relevant).
- Reine Funktion zweier Winkel/Phasoren.

Alle Features skaleninvariant (ATR-normalisiert), Dataclasses mit
`to_dict()`, Modul-Header, volle Typen.

## Tests (`tests/test_power_phasor.py`)

- Reine Marubozu-Kerze (keine Dochte): cos φ ≈ 1, Q ≈ 0, S ≈ P.
- Kreuz/Kerze mit langen Dochten, kleinem Body: cos φ < 0,30, S > P.
- Sinus-artige synthetische Preisserie: Phasor dreht gleichmäßig,
  Amplitude stabil.
- Zwei gleichgerichtete Phasoren → resonance ≈ 1; gegenläufige →
  Dip-Charging-Flag.
- Determinismus-Check: zweimal aufgerufen identisch.

## Nicht im Scope
- Keine Strategien, kein Orchestrator, keine Orders.
- Keine ONNX-Modellierung (Phase MP-11).

## Abnahme
Pytest grün; keine neuen schweren Abhängigkeiten (numpy ist im Repo
vorhanden — prüfe `requirements.txt`).
