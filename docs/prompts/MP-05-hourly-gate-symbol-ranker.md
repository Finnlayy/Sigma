# Master-Prompt MP-05 — Hourly Screening Gate & High-Beta-Symbol-Ranker

Lies `docs/SIGMA-WISSENSDATENBANK.md` §4.4 (1-Scan-pro-geschlossene-1h-Bar,
Minuten-Phasen) und §6 (Symbol-Universum, Buckets). Prüfe
`sigma/signals/correlation_scout.py` (Bucket-Logik r>0,80/β>1,8
existiert), `sigma/orchestration/master_orchestrator.py` (Screen-Block)
und `sigma/execution/universe.py`. **Erweitere den Scout, statt ihn zu
duplizieren.**

## Auftrag

### 1. `sigma/orchestration/hourly_screening_gate.py`
- Zustandsautomat aus geschlossener 1h-BTC-Bar-Zeit (Minute der Stunde):
  - `SCAN_AND_DEPLOY` (Min 00–05): genau ein Scan-Versuch pro Bar-Zeitstempel
  - `ACTIVE_EXECUTION` (05–48): laufende Positionen managen, keine neuen
    Screenings
  - `PRE_CLOSE_UNWIND` (48–55): keine neuen Entries, TTL-Flats vorbereiten
  - `IDLE_WAIT` (55–60): warten auf nächsten Bar-Close
- Idempotenz: Gate merkt sich den letzten verarbeiteten Bar-Zeitstempel
  (In-Memory + `to_dict()`/Restore), wiederholter Aufruf in derselben
  Stunde → kein erneuter Scan.
- UTC-Basis; ausschließliche Verwendung geschlossener Bars.

### 2. `sigma/signals/high_beta_ranker.py`
- Pro Symbol: β vs. BTC, r (Rolling-Korrelation), RVOL (Volumen-Ratio),
  Spread/Book-Tiefe als Penalty.
- Hard-Filter (Defaults als Benennungskonstanten, Werte aus §4.4/§6):
  r ≥ 0,75, β ≥ 1,5, RVOL ≥ 1,5, Spread-Cap (z. B. 0,08 %);
  Blacklist-Gründe als Feld (`reason`: thin_book / unlock_window / …).
- Score gewichtet die Faktoren; Ranking absteigend.
- Strategieempfehlung pro Symbol: β ≥ 2,8 & RVOL ≥ 2,5 & Liq-Puffer klein
  → `sniper_hedge` (25x-Modus); sonst `dca` (5–10x). Liq-Distanz via
  MP-01-Guard berücksichtigen.
- Weekend: reduzierte Größe/Paper-Flag beibehalten (bestehende
  SessionClock-Logik respektieren).

### 3. Orchestrator-Anbindung
- Ranker-Ergebnis als `ctx["screening"]`/Feld im Orchestrator-Kontext;
  Gate blockiert Mehrfach-Scans. **Kein Auto-Deploy neuer Strategien**
  (kommt mit MP-07) — in dieser Phase nur Klassifikation/Kontext.

## Tests (`tests/test_hourly_ranker.py`)
- Zeitphasen: Minuten 2/20/50/57 → korrekte Zustände.
- Zweiter Aufruf in derselben Stunde → Scan gesperrt; nächste Stunde frei.
- Synthetisches Universum: High-β-High-RVOL-Symbol rankt vor Low-β;
  Thin-Spread-Symbol wird mit Grund gefiltert.
- Empfehlung: extremes Symbol → `sniper_hedge`; moderates → `dca`.
- Kein Look-Ahead (nur geschlossene Stundenbars).

## Nicht im Scope
- Keine Orderausführung, kein Pine-Deploy (MP-09), keine Sniper-Strategie
  (MP-07).

## Abnahme
Pytest grün; Orchestrator-Suite (`tests/test_orchestration*.py`,
`tests/test_quantum_wave_regime.py`) weiterhin grün.
