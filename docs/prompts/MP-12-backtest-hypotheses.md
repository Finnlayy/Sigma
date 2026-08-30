# Master-Prompt MP-12 — Backtest-Harness & Hypothesen H1–H5

Lies `docs/SIGMA-WISSENSDATENBANK.md` §15 (Forschungsmethodik, Evidenz-
klassen, Look-ahead-Bann) und das Backtest-Kapitel im
`docs/BLUEPRINT-SIGMA.md`. Prüfe vorhandene Infrastruktur:
`app/backtest/`, `app/optimizer/` (GA), `tests/test_quantum_wave_regime.py`
und die `_bars()`-Helfer in `tests/test_loops_cde.py`. Nutze, was da ist.

## Auftrag

### 1. `sigma/backtest/lookahead_pipeline_check.py`
- Testwerkzeug, das die Pipeline **bewusst mit einem Look-ahead-Leck**
  versieht und nachweist, dass die bestehende Invariante es erkennt:
  HTF-Indikatoren dürfen zur Zeit t nur Daten bis Bar t−1 sehen
  (geschlossene Bars). Als Assertion über alle Ticks ausführbar.
- Funktion `assert_no_lookahead(tick_ctx_series)` für Nutzung in
  allen Backtest-Tests.

### 2. `tests/backtest/test_hypotheses_h1_h5.py` (neues Verzeichnis)
Auf synthetischen + (falls vorhanden) exportierten TV-CSV-Daten:
- **H1**: bias-aligned FVGs (in Makro-Trendrichtung) haben bessere
  Trefferquote/Erwartungswert als counter-trend FVGs.
- **H2**: Overlap-Session-Fills (London/NY) füllen bessere Raten als
  Off-Session (Asia/Weekend).
- **H3**: Faktor-Sweep 2x → 30x in Walk-Forward-Scheiben (z. B.
  2:1 train/test); Metriken: Return, Max-DD, Liq-Häufigkeit; 25x
  als spekulativer Außenbereich markiert, >10x Standard-Optimum.
- **H4**: Weekend-Alt-Longs: Baseline vs. Slippage-Szenarien
  (+0,1 % / +0,3 % / +0,6 % Slippage) — Erwartungswert bricht ein.
- **H5**: Hurst/MFDFA-Gate AN vs. AUS: Drawdown-Vergleich auf
  denselben Daten.
Jeder Test wertet statistisch aus (Mittelwert + einfaches
Signifikanzmaß), nicht nur Einzelwerte; Hypothesen gelten als
„bestätigt“ nur bei klarer Trennschärfe, sonst „offen“ dokumentiert.

### 3. Report-Ausgabe
- `sigma/backtest/report.py` (klein): Ergebnisse als Markdown/JSON
  nach `tests/backtest/results/` (in `.gitignore`, keine Artefakte
  committen).

## Nicht im Scope
- Keine Live-Ausführung, keine Parameter-Optimierung gegen echte
  Gelder, keine Magic-Thresholds ohne Sweep.

## Abnahme
- `pytest tests/backtest/` läuft ohne Netzwerk.
- Der Look-ahead-Check schlägt nachweisbar fehl, wenn man ein Leck
  einbaut (Test des Tests).
- Keine Backtest-Artefakte in Git.
