# Master-Prompts für Project Sigma

Diese Prompts sind dafür gedacht, **nacheinander von KI-Agenten**
(z. B. Cursor / Claude Code / GitHub-Copilot-Workspace) abgearbeitet zu werden.

## Vor jeder Aufgabe

1. Lies `docs/SIGMA-WISSENSDATENBANK.md` (kanonische Wissensquelle) und
   `docs/SIGMA-ROADMAP.md` (Phasenplan).
2. Der bestehende Code unter `sigma/` ist maßgeblich — erfinde keine
   Module/Pfade, prüfe mit `find`/`grep`, was existiert.
3. Eine Phase = ein PR. Phasen in numerischer Reihenfolge; optionale Phasen
   (MP-13, MP-14) nur auf explizite Anforderung.
4. Globale Definition of Done: siehe Ende der Roadmap
   (Pytest grün, Typen, `to_dict()`, fail-closed, kein Look-Ahead,
   Paper-only, keine Duplizierung, Modul-Header-Konvention).

## Reihenfolge

| Prompt | Phase | Pflicht? |
|---|---|---|
| MP-01 | Hard Risk Guards | ja, zuerst |
| MP-02 | Micro-DCA-Ladder | ja |
| MP-03 | Candle-/Regime-Signale | ja |
| MP-04 | Power-Triangle/Phasor | ja |
| MP-05 | Hourly Gate + Symbol-Ranker | ja |
| MP-06 | Polymarket Feed/Dichte/Trajektorie | ja |
| MP-07 | Quantum-Sniper-Strategie | ja |
| MP-08 | Exhaustion + Async-Unwind | ja |
| MP-09 | Dynamischer Pine-Provisionierer | ja |
| MP-15 | Fraktaler High-Leverage-Einzeltrade (40/30/20/10, Fee-Covered BE) | ja |
| MP-10 | Orderflow-Validator | optional |
| MP-11 | ONNX-Tensor + Inferenz | ja |
| MP-12 | Backtest-Harness/Hypothesen | ja |
| MP-13 | Multi-Asset XAU/Forex | optional |
| MP-14 | Event-Straddle | optional |
