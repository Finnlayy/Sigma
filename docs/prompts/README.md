# Master-Prompts für Project Sigma

Unattended Implementierungs-Loop (Jules): `docs/prompts/JULES-MASTER-EXECUTE.md`
plus Zustand `docs/plans/EXECUTE-STATE.md` und `AGENTS.md`. Eine Phase nach der
anderen bis STOP; GitHub Action `.github/workflows/jules-mp-execute.yml`
startet neu, solange **Next** nicht `STOP` ist.

Diese Prompts sind dafür gedacht, **nacheinander von KI-Agenten**
(z. B. Cursor / Claude Code / GitHub-Copilot-Workspace) abgearbeitet zu werden.

## Vor jeder Aufgabe

1. Lies `docs/SIGMA-WISSENSDATENBANK.md` (kanonische Wissensquelle) und
   `docs/SIGMA-ROADMAP.md` (Phasenplan).
2. Der bestehende Code unter `sigma/` ist maßgeblich — erfinde keine
   Module/Pfade, prüfe mit `find`/`grep`, was existiert.
3. Eine Phase = ein PR. Phasen in numerischer Reihenfolge; optionale Phasen
   (MP-10, MP-13, MP-14) nur auf explizite Anforderung. **MP-17 (Frontend)**
   darf parallel zu den Fachmodulen laufen: seine Endpunkte liefern bis
   dahin strukturierte Leerantworten (fail-closed); Vertrag ist
   `docs/SIGMA-UI-SPEZIFIKATION.md`.
4. Globale Definition of Done: siehe Ende der Roadmap
   (Pytest grün, Typen, `to_dict()`, fail-closed, kein Look-Ahead,
   Paper-only, keine Duplizierung, Modul-Header-Konvention).

## Reihenfolge

| Prompt | Phase | Pflicht? |
|---|---|---|
| MP-01 | Hard Risk Guards (inkl. Fee-Covered Break-Even) | ja, zuerst |
| MP-02 | Micro-DCA-Ladder | ja |
| MP-03 | Candle-/Regime-Signale (Thrust, Marubozu/FVG, 00:00-Envelope) | ja |
| MP-04 | Price-Action-Physics (S/P/Q_norm, η, cos φ Pfad+Bar), Hilbert-Phasor, MTF-Resonanz | ja |
| MP-05 | Hourly Gate + High-Beta-Symbol-Ranker (Stufe 2 der Pipeline) | ja |
| MP-06 | Polymarket Feed/Dichte/Trajektorie | ja |
| MP-07 | Quantum-Sniper-Strategie (Phase 2) | ja |
| MP-08 | Exhaustion + Async-Unwind | ja |
| MP-09 | Dynamischer Pine-v6-Provisionierer (inkl. Fraktal-Multi-TP) | ja |
| MP-15 | Fraktaler High-Leverage-Einzeltrade (40/30/20/10, Fee-Covered BE) | ja |
| MP-11 | ONNX 16-Feature-Tensor (Formeln nach §11) + Dual-Head-Fallback | ja |
| MP-12 | Backtest-Harness (VectorBT), Hypothesen H1–H7, Look-ahead-Check | ja |
| MP-16 | Lightweight-Charts-Dashboard + cos-φ-Hysterese-Backtester | ja |
| MP-17 | Frontend-Panels für alle neuen Funktionen (Vertrag: `docs/SIGMA-UI-SPEZIFIKATION.md`) | ja, parallel möglich |
| MP-10 | Orderflow-Validator | optional |
| MP-13 | Multi-Asset XAU/Forex | optional |
| MP-14 | Event-Straddle | optional |
