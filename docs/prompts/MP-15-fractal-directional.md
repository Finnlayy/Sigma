# Master-Prompt MP-15 — Fraktale High-Leverage-Einzeltrade-Strategie

Lies `docs/SIGMA-WISSENSDATENBANK.md` §5.5 (fraktaler Einzeltrade,
Parameter + Beispielrechnung), §8 Regeln 6 und 8 (Fee-Covered Break-Even,
automatischer Kill-Switch mit Live-Beleg), §12 (Webhook-Schema inkl.
Fraktal-Payload) und Phase MP-15 in `docs/SIGMA-ROADMAP.md`.

Voraussetzungen: MP-01 (`fee_covered_stop` in `risk_guards.py`),
MP-05 (Ranker + Hourly Gate), MP-09 (Pine-Provisionierer mit
Fraktal-Payload). MP-08 (Exhaustion) sollte für den Kill-Switch
existieren; falls nicht, nutze zunächst das Volatility-Throttle und
hinterlasse einen klaren TODO-Verweis auf MP-08.

## Hintergrund (Nutzer-Log, 30.08.2026)

Live-DCA-Bots auf 我踏马来了 (10x Long, BTC-Lead-Breakout):
Bot #1 +52,76 % (Arbitrage +66 %, Trend −13 %), aber manueller Close
erst nach dem Spike-Top — ~15–20 % Peak-PnL durch Exit-Latenz verloren.
Der fraktale Einzeltrade löst das: Risiko über Positionsgröße/harten SL
statt Nachkauf-Marge, dadurch 20–50x Hebel vertretbar, und **Sigma
beendet automatisch** — der Mensch startet nur.

## Auftrag

### 1. `sigma/strategies/fractal_directional.py`
- `FractalDirectional(BaseStrategy)` mit `plan(ctx) -> StrategyIntent`.
- Entry nur wenn **alle** Bedingungen:
  - Ranker-Freigabe (MP-05, Empfehlung `sniper_hedge`, High-β/RVOL)
  - BTC-Lead-Signal: Breakout/Retest auf geschlossener Bar
    (MP-03-Thrust/FVG oder Wave-Zone, wie im Kontext vorhanden)
  - Minuten-Phase `ACTIVE_EXECUTION` (Minute 5–48 der 1h-Bar)
  - Liq-Puffer-Wache aus MP-01 grün
- Fraktale TP-Staffel als Konstanten mit ATR-Option:
  TP1 40 % @ +1,0 %, TP2 30 % @ +2,0 %, TP3 20 % @ +3,5 %,
  Runner 10 % mit ATR-Trailing (Trail-Abstand als Konstante).
- Initialer Hard-SL: 0,6 % gegen Entry, **ersetzt** durch den MP-01-
  Liq-Puffer, falls dieser näher/strenger ist (geringeres Risiko gewinnt).
- **Pflicht nach TP1-Fill:** `update_sl`-Intent auf
  `fee_covered_stop(entry, side)` (entry × 1,0005 long / × 0,9995 short)
  — automatisch, ohne Option auf Überschreibung.
- **Kill-Switch (automatischer Exit):** Exhaustion-Signal (MP-08) ODER
  Preis erreicht die Zielliquiditätszone (Sweep über TP3-Zone) ODER
  Minute ≥ 55 → FLAT-Intent für den Runner; keine Warte auf Menschen.
- Intent-Dataclass mit allen Staffeln, SL-Zustand (initial / fee_be),
  TTL; `to_dict()`.

### 2. Orchestrator-Anbindung
- Template `fractal_directional` registrieren; Orchestrator ruft nur
  `plan(ctx)` auf und gatet — keine Orders aus dem Orchestrator.
- Empfehlungslogik des Rankers (MP-05) um diese Strategie erweitern:
  extreme β/RVOL + sauberer Lead-Break → `fractal_directional`;
  moderat → DCA-Pfaden.

### 3. Webhook/Pine
- Payload-Erzeugung über MP-09-Generator mit Fraktal-Modus
  (tp1..tp3 + runner + fee_covered_be_offset, UPDATE_SL nach TP1).
  Keine eigene Webhook-Logik in der Strategie.

## Tests (`tests/test_fractal_directional.py`)
- TP-Staffel: Mengen 40/30/20/10 summieren auf 100; Preise long
  aufsteigend, short spiegelbildlich.
- Fee-Covered: nach simuliertem TP1-Fill enthält der Intent
  zwingend `update_sl` mit long entry×1,0005 (> Entry) bzw. short
  entry×0,9995 (< Entry).
- SL-Wahl: konstruierter enger Liq-Puffer (z. B. 0,4 %) schlägt den
  0,6 %-Default; weiter Puffer → 0,6 % greift.
- Kill-Switch: Kontext Minute 55 → Runner-FLAT; Exhaustion-Flag →
  FLAT; intakter Trend vor Minute 48 → kein Zwangs-Flat.
- Ohne Ranker-Freigabe / ohne Lead-Signal → FLAT/kein Entry.
- Look-ahead: nur geschlossene Bars (Stil `test_quantum_wave_regime.py`).

## Nicht im Scope
- Live-Orders (Paper only), keine Änderung der DCA-Templates (MP-02),
- Kein Hebel > Ranker-Empfehlung, keine neue Indikator-Bibliothek.

## Abnahme
Pytest grün inkl. Orchestrator-Regression; der SL-Nachzieh-Intent nach
TP1 ist per Test erzwungen (fehlt er, schlägt der Test fehl).
