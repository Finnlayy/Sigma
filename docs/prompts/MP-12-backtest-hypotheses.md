# Master-Prompt MP-12 — Backtest-Harness & Hypothesen H1–H7

Lies `docs/SIGMA-WISSENSDATENBANK.md` §14 (Forschungsmethodik,
Evidenzklassen, Look-ahead-Bann) und §17 (Backtest-Werkzeuge, H6).
Prüfe vorhandene Infrastruktur: `app/backtest/`, `app/optimizer/`
(GA), TV-CSV-Exporte, `tests/test_quantum_wave_regime.py`,
die `_bars()`-Helfer in `tests/test_loops_cde.py`. **VectorBT ist die
Standard-Backtest-Engine** für Faktor-Sweeps (vektorisiert); nutze
bestehende Datenladepfade, dupliziere keine Loader.

## Auftrag

### 1. `sigma/backtest/lookahead_pipeline_check.py`
- Testwerkzeug, das die Pipeline bewusst mit einem Look-ahead-Leck
  versieht und nachweist, dass die Invariante zuschlägt:
  HTF-Indikatoren dürfen zur Zeit t nur geschlossene Bars bis t−1 sehen.
- `assert_no_lookahead(tick_ctx_series)` als Assertion zur Nutzung
  in allen Backtest-Tests; chronologische Splits (keine Random-Splits),
  Walk-Forward-Scheiben (z. B. 2:1 train/test).

### 2. `tests/backtest/test_hypotheses_h1_h7.py`
Auf synthetischen und (wo vorhanden) exportierten TV-CSV-Daten,
mit Slippage-/Fee-Modell (Taker 0,04 %/Seite bzw. 0,06 % Roundtrip):
- **H1:** bias-aligned FVGs vs. counter-trend FVGs (Trefferquote/EW).
- **H2:** Overlap-Session-Fills (07–09/14–16 UTC) vs. Off-Session.
- **H3:** Hebel-Faktor-Sweep 2x→30x, Walk-Forward; Metriken Return,
  Max-DD, Liq-Häufigkeit; 25x als spekulativer Außenbereich.
- **H4:** Weekend-Alt-Longs, Slippage-Szenarien +0,1/+0,3/+0,6 %.
- **H5:** Hurst/MFDFA-Gate an/aus — Drawdown-Vergleich.
- **H6 (Nutzer-These):** Wochenend-Breakouts (Sa/So) sind
  überproportional Fakeouts vs. Mo–Fr; Montag-10:00-UTC-Momentum und
  das Sweep→Reclaim-Muster (Wyckoff-Spring) als Long-Trigger.
  Auswertung getrennt nach Wochentag + Slippage.
- **H7:** cos-φ-Pfad-Strategie (MP-04 `cos_phi_path`, Efficiency
  Ratio): Entry |cos φ| ≥ 0,40 mit Hysterese, Exit bei |cos φ| ≤ 0,15;
  Fenster-Sweep N ∈ {10,14,20,30}; Position 1-Bar verzögert;
  Metriken Return, Max-DD, Sharpe, Win-Rate, Profit-Faktor, Trades.
Jede Hypothese: Mittelwert + einfaches Signifikanzmaß, nicht nur
Einzelwerte; „bestätigt / offen / verworfen“ klar dokumentiert;
keine Magic-Thresholds ohne Sweep (Overfitting-Red-Flags beachten:
Sharpe > 3 o.ä. ist Verdachtsmoment).

### 3. Report
- `sigma/backtest/report.py` (klein): Ergebnisse als Markdown/JSON
  nach `tests/backtest/results/` (Verzeichnis in `.gitignore` —
  keine Artefakte committen).

## Nicht im Scope
- Live-Ausführung, Parameter-Optimierung gegen echtes Geld,
- Das visuelle Dashboard (→ MP-16, baut auf diesem Harness auf).

## Abnahme
- `pytest tests/backtest/` läuft ohne Netzwerk.
- Der Look-ahead-Check schlägt nachweisbar fehl, wenn man ein Leck
  einbaut (Test des Tests).
- Keine Backtest-Artefakte in Git; VectorBT als Engine genutzt.
