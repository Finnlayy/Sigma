# Master-Prompt MP-07 — Quantum-Sniper-Strategie (Phase 2)

Lies `docs/SIGMA-WISSENSDATENBANK.md` §4.1–4.4 (Sniper-Flow, TTL-Regeln,
Screening-Takt) und §13.1 (Beispielablauf). Voraussetzungen:
MP-01 (Risk Guards), MP-02 (DCA-Ladder), MP-03 (Thrust/FVG),
MP-05 (Ranker + Hourly Gate) müssen existieren.
Prüfe `sigma/strategies/base_strategy.py` (StrategyIntent),
`sigma/signals/quantum_wave_collider.py` (Zustandsautomat),
`sigma/orchestration/master_orchestrator.py`.

## Wichtig (Architektur-Korrektur)

Der Wave-Collider bleibt **Regime-Signal**, nicht Strategie. Die
Sniper-Logik wird eine eigene `BaseStrategy`: der Orchestrator
klassifiziert/gatet nur und ruft `template.plan(ctx)` auf — er
platziert niemals selbst Orders. Keine `add_order`-Aufrufe im
Orchestrator.

## Auftrag

### 1. `sigma/strategies/quantum_sniper_dca.py`
- Klasse `QuantumSniperDCA(BaseStrategy)` mit `plan(ctx) -> StrategyIntent`.
- Entry-Bedingungen (alle):
  1. Wave-Zustand `COLLAPSED_INTO_ZONE` auf 15m BTC (aus Kontext).
  2. LTF-Bestätigung auf 1m/5m: Two-Bar-Thrust **oder** FVG-Touch in der
     CE50-Zone (MP-03-Signale) — Retest, nicht der erste Touch.
  3. Ranker-Freigabe (MP-05): Symbol in Top-Rangliste, Strategieempfehlung
     `sniper_hedge`.
  4. Minuten-Phase `ACTIVE_EXECUTION` (Min 5–48 der Stunde);
     Minute ≥ 48 → nur FLAT.
- Bei Entry: DCA-Ladder via MP-02 (4–6 Sprossen, Step 0,2 %, Vol-Faktor
  1,15, TP 1,5–3 % auf Avg), Hard-SL via MP-01 (0,5 % über Liq-Preis
  bzw. knapp unter Range-Low), TTL spätestens Minute 48 → FLAT-Intent.
- Falsifizierung: Range-Low breach / Wave `INVALIDATED` → FLAT.
- Intent enthält Felder für SL/TP/TTL/ladder (Dataclass, `to_dict()`).

### 2. `sigma/execution/quantum_sniper_pipeline.py`
- Datenfluss-Kette als reine Funktionen/Schicht:
  15m-Wave-Evaluation → bei COLLAPSED LTF-Retest-Polling (1m/5m) →
  Ranker-Check → Intent. Keine Exchange-Aufrufe, nur Paper-Pfad.

### 3. Orchestrator
- Template registrieren (`quantum_sniper_dca`), aufrufbar über
  bestehendes Template-Mapping; keine neuen Orders im Orchestrator.

## Tests (`tests/test_quantum_sniper.py`)
- Vollzyklus mit synthetischen Bars (Stil wie
  `tests/test_quantum_wave_regime.py`): Expansion → FVG → Dip in CE50 →
  Thrust auf 1m → BUY-Intent mit TP/SL/Ladder.
- TTL: Kontext-Minute 50 → FLAT, kein Entry.
- Range-Low-Breach → FLAT/unwind.
- Ohne Ranker-Freigabe → FLAT.
- Retest ohne Thrust → kein Entry (erster Touch reicht nicht).

## Nicht im Scope
- Keine Live-Orders (Paper only), kein Pine-Deploy (MP-09),
  keine 1m-Sniper außerhalb des 05–48-Fensters.

## Abnahme
Pytest grün inkl. Orchestrator-Regression; Orchestrator enthält keine
Orderplatzierung.
