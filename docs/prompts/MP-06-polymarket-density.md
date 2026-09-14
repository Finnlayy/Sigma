# Master-Prompt MP-06 — Polymarket Layer 0: Feed, Dichte, Term-Struktur

Lies `docs/SIGMA-WISSENSDATENBANK.md` §7 (Polymarket Layer 0) und prüfe
`sigma/signals/polymarket_layer0.py` — dort existiert nur ein
Payload-Stub (`layer0_pre_regime`). Diesen ersetzen/erweitern, nicht
danebenstellen. Port-Muster siehe `sigma/ports/` (Kraken/KoinX).

## Auftrag

### 1. `sigma/ports/polymarket_port.py` (optionaler Port)
- Interface `PolymarketPort` mit `fetch_event_odds(event_slug) -> dict`:
  Strikes (Schwellen), Yes-Preise, Volumen, Zeitstempel.
- Nur liquide Märkte (Volumen-Mindestfilter als Konstante).
- Kein Netzwerk in Tests: Port wird injiziert; ohne Konfiguration
  `available=False` — Orchestrator läuft unverändert weiter (fail-closed,
  kein synthetischer Payload).

### 2. `sigma/signals/polymarket_density.py`
- Aus Streikpreisen + Yes-Preisen: implizite Bin-Wahrscheinlichkeiten
  (Differenz der Kontraktpreise = Dichte zwischen zwei Schwellen,
  Breeden-Litzenberger-Analogie für binäre Leiter).
- Erwartungswert μ (wahrscheinlichster Preisbereich), wahrscheinlichster
  Korridor (Bin mit höchster Dichte ± Nachbar-Bins).
- Kalibrierung: einfaches Platt-Scaling mit Parametern, die aus
  historischem Brier-Abgleich befüllt werden (Default konservativ,
  als Konstante; Kalibrierung nur verschieben, nie Signale erfinden).

### 3. `sigma/signals/polymarket_trajectory.py`
- Term-Struktur aus T+1h / T+2h / T+4h / EOD-Quoten: μ(T)-Kurve,
  Δμ/ΔT-Geschwindigkeit.
- Bias-Klassifikation: `STRONG_BULLISH / BULLISH / CHOP / BEARISH /
  STRONG_BEARISH` nach Schwellwerten aus §7 (z. B. Δμ > +3 %/h).
- Optimales Entry-Fenster: `T_opt ≈ Expiry × 0,75`; spätes Fenster
  (< Expiry × 0,25) → kein Entry mehr.

### 4. Orchestrator
- Bestehenden Layer-0-Block im Orchestrator mit echter Port-Injektion
  verdrahten: ohne Port/Feed bleibt `valid=False` wie heute; mit Feed
  gehen Bias/Fenster in den Kontext ein. Gate-Schwelle kalibrierte
  Wahrscheinlichkeit > 0,60–0,65 als Konstante.

## Tests (`tests/test_polymarket_layer0.py`)
- Konstruierte Leiter (Strikes 95k/100k/105k, Preise 0,85/0,62/0,25):
  Bin-Dichten aufsteigend/absteigend korrekt, μ plausibel.
- Term-Struktur: μ steigt über T → bullish; flach → CHOP.
- Degradierter/synthetischer Payload (`synthetic=True`, fehlende Felder)
  → abgelehnt.
- T_opt-Berechnung und Spät-Fenster-Sperre.
- Kein Netzwerk: Port als Fake injiziert.

## Nicht im Scope
- Keine echten Polymarket-API-Credentials, keine Orders bei Polymarket.
- Keine Strategien (nur Kontext/Gate).

## Abnahme
Pytest grün; Orchestrator ohne Port verhält sich exakt wie heute
(Regressionstest).
