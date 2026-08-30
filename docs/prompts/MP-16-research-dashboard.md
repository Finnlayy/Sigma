# Master-Prompt MP-16 — Research-Dashboard (Lightweight Charts) & cos-φ-Backtester

Lies `docs/SIGMA-WISSENSDATENBANK.md` §17 (Forschungs-/Backtest-Werkzeuge)
sowie §9.2/§9.5 (cos φ, Price-Action-Physics). Voraussetzungen:
MP-04 (`power_triangle.py`, insbesondere `cos_phi_path`), MP-12
(Backtest-Harness, VectorBT). Das im Chat konzipierte HTML-Dashboard
dient als Referenz für Layout/Idee — es nutzt synthetische Daten und
steht nicht im Repo; der Export muss an die echten Backtest-Daten/
TV-CSV andocken.

## Auftrag

### 1. `sigma/backtest/power_factor_backtest.py`
- Vektorisierter cos-φ-Pfad-Backtester (Kaufman Efficiency Ratio aus
  MP-04; auf Wunsch VectorBT oder reines pandas — konsistent zu MP-12):
  - State-Machine mit Hysterese: `cos_phi_path ≥ +0,40` → Long,
    `≤ −0,40` → Short, `|cos_phi| ≤ 0,15` → Flat/Exit.
  - Position erst 1 Bar nach Signal wirksam (kein Look-ahead).
  - Transaktkosten: 0,06 % pro Roundtrip als Standard (Parameter).
  - Metriken: Total Return, Max Drawdown, Annualisierter Sharpe
    (1h-Bars → 8.760 Perioden/Jahr), Win-Rate, Profit-Faktor,
    Trade-Zahl; Equity-Curve + Signal-Labels pro Bar.
  - Parameter (Window N, Long/Short-/Exit-Schwellen, Fee) als
    Argumente für MP-12-Sweeps; Defaults N=20, ±0,40, 0,15.

### 2. `app/dashboard/tv_lightweight_export.py`
- Exportiert einen Backtest-Lauf (oder TV-CSV + berechnete Indikatoren)
  in eine eigenständige HTML-Datei:
  - Data-Layer: JSON-Payload mit Kerzen (`time` UNIX-Sekunden,
    aufsteigend sortiert), cos-φ-Serie, Equity-Serie, Markern.
  - Marker nur an Positionswechseln: grüner arrowUp (Long-Eintritt),
    roter arrowDown (Short-Eintritt), grauer circle (Chop-Exit).
  - Drei synchronisierte Panes (Lightweight Charts v4/v5 via CDN,
    standalone build, kein Bundler/Build-Prozess):
    1. Candlesticks + Marker,
    2. cos φ mit Preislinien bei +0,40 / −0,40 / ±0,15 / 0,
    3. Equity-Curve der Strategie (Benchmark optional).
  - Zeitachsen-Sync via `subscribeVisibleLogicalRangeChange`,
    Responsive-Resize; Dark-Theme wie im Referenz-Layout.
  - Reines Offline-Dashboard (CDN-Script), **keine Live-Verbindung**,
    keine Order-Funktion; Pfad für die erzeugte HTML konfigurierbar.

### 3. Tests `tests/backtest/test_power_factor_dashboard.py`
- Backtester auf synthetischen Sequenzen (vgl. MP-12-Stil):
  saubere Aufwärtstrend-Sequenz → Long-Signale mit Gewinn;
  reine Sinus/Chop-Sequenz → überwiegend flat (kein Whip-Saw-Tod);
  saubere Abwärtssequenz → short.
- Hysterese: einmal ausgelöstes Long-Signal bleibt bis zur
  Exit-Schwelle (kein Flattern bei geringem Rauschen).
- 1-Bar-Lag: Position zur Signal-Bar ist noch 0.
- Payload/Export: Zeitreihe streng aufsteigend und lückenfrei im
  geforderten Format; Marker nur bei Positionswechseln; HTML enthält
  die drei Chart-Container und die Schwellen-Preislinien.
- Determinismus: gleiche Eingabe → gleiche JSON-Payload.

## Nicht im Scope
- Live-Daten/WS-Feeds, echte Orderausführung, Alert-Versand,
- Modelltraining, React/Frontend-Build (statisches HTML genügt).

## Abnahme
Pytest grün; erzeugte HTML-Datei unabhängig im Browser öffnbar
(Dummy-Daten im Test erlaubt); keine Backtest-Artefakte in Git.
