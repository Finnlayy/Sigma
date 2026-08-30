# Master-Prompt MP-10 — Orderflow-Validator (optional, L2)

Lies `docs/SIGMA-WISSENSDATENBANK.md` §3.3 (Orderflow-Konfluenz) und
§16 (Evidenz: OFI/Square-Root-Impact sind belegt, SMC-Label nicht).
Dieses Modul ist **optional** und rein additiv — es validiert Einträge,
es erzwingt sie nicht.

## Auftrag — `sigma/signals/orderflow_validator.py`

- Dataclass `FootprintBar`: Preisbänder mit bid/ask-Volumen, Delta,
  POC (Point of Control), HVN/Volume-Nodes.
- Funktionen:
  1. `stacked_imbalances(bars, min_levels=3, ratio=3.0)` — diagonale
     Stacked Imbalances: ≥3 aufeinanderfolgende Bänder mit
     Ask/Bid-Verhältnis ≥ 3:1 (bzw. umgekehrt).
  2. `cvd_absorption(bars)` — Preis macht neues Tief, kumuliertes Delta
     dreht positiv/bleibt flach (Absorption).
  3. `poc_confluence(bars, fvg_zone)` — POC/HVN liegt innerhalb der
     FVG/CE50-Zone.
  4. `iceberg_detection(book_snapshots)` — gleiche sichtbare Größe
     erscheint nach Teilausführung erneut (Mindestwiederholungen).
- `orderflow_score(...)` → 0–1 Konfidenz; Konfluenz mehrerer Signale
  hebt den Score.
- **Fail-closed:** ohne L2-Tiefe/Footprint-Daten (`valid=False` vom
  Feed) liefert alles `score=0.0, valid=False` — niemals Synthese.
  Reine Zeitreihen (OHLCV) reichen nicht.

## Tests (`tests/test_orderflow_validator.py`)
- Konstruierte Footprint-Bars mit 4 diagonalen 4:1-Imbalances → erkannt.
- Absorption: neue Tiefs bei positivem Delta → erkannt.
- POC in FVG-Zone → konfluent; außerhalb → nicht.
- Leerer Feed / nur OHLCV → `valid=False`, Score 0.
- Kein Signal blockiert Einträge per se (Validator ist additiv — im
  Test nur den Score prüfen, keine Orchestrator-Kopplung).

## Nicht im Scope
- Kein Exchange-L2-Anschluss in dieser Phase (Datenstruktur +
  injizierter Feed), keine Orderausführung.

## Abnahme
Pytest grün; Modul ist ohne L2-Feed komplett inert.
