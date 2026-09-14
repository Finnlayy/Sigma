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
- Pro Symbol: β vs. BTC, r (Rolling-Korrelation, **signiert**),
  RVOL (Volumen-Ratio), Spread/Book-Tiefe als Penalty.
- **Richtungsprüfung (hart, signed):** r und β sind signierte Größen.
  - **Long-Kandidat:** r ≥ 0,75 **und** β ≥ 1,5 (beide positiv) —
    BTC-Rückenwind, Alt hebelte ihn historisch in dieselbe Richtung.
  - **Short-Kandidat:** r ≥ 0,75 mit **negativem** β (β ≤ −1,5), d. h.
    der Alt fällt zuverlässig, wenn BTC schwach wird (Bucket 2, §6) —
    nur in BTC-bearishem Makro-Regime routbar.
  - **Inverse Longs verboten:** Ein Alt mit r < 0 wird **niemals**
    automatisch gelongt, nur weil BTC schwach ist (Alt-Long gegen den
    BTC-Makro-Kontext verstößt gegen das MP-01 BTC-Makro-Gate; die
    „Safe-Haven-Long"-Idee bleibt Forschungshypothese, kein Auto-Trade).
  - **Decoupled (|r| < 0,30):** keine BTC-Führungsbeziehung; werden
    in v1 nicht über den Scout gehandelt (idiosynkratisch, fail-closed),
    nicht als Konfluenz getarnt.
- **24h-Relativstärke-Vorselektion (manueller Nutzer-Workflow,
  automatisiert):** Vorselektions-Filter/Kennzahl `perf_24h_pct` +
  RVOL: nur Coins, die bereits den primären Move hatten (24h-Top-Gainer
  mit Volumenbestätigung) kommen in die Leader-Wertung — Kapital ist
  bereits geflossen (Relative-Stärke-Selektion statt Bottom-Fishing).
  Post-Breakout-Zustand über skaleninvariante Position
  (`pos_EQ ∈ [0,40; 0,65]` = erste Konsolidierung nach dem Ausbruch)
  kennzeichnen, damit nicht in die vertikale Kerze gechast wird.
  Der Scout-Tabelle beide Werte als Spalten mitgeben.
- Hard-Filter (Defaults als Benennungskonstanten, Werte aus §4.4/§6):
  |r| ≥ 0,75 (Long: r positiv; Short: β negativ), |β| ≥ 1,5,
  RVOL ≥ 1,5, Spread-Cap (z. B. 0,08 %);
  Blacklist-Gründe als Feld (`reason`: thin_book / unlock_window /
  decoupled / inverse_long_blocked / …).
- Score gewichtet die Faktoren, Long- und Short-Seite werden
  **getrennt** gerankt (Richtung ergibt sich aus dem Vorzeichen der
  Faktoren, nicht aus dem Score-Betrag):
  `score_long = β × RVOL × r × rs_factor(perf_24h) − spread_penalty`
  (nur für r>0, β>0);
  `score_short = |β| × RVOL × r × rs_factor(|perf_24h|) − spread_penalty`
  (nur für r>0, β<0). Ranking je Richtung absteigend.
- **Leader-Rotation pro Scan:** Da jeder Scan auf einer neuen
  geschlossenen 1h-Bar läuft, fällt ein Alt mit abkühlender Volatilität
  (RVOL/β zerfällt, r bricht weg) automatisch aus der Wertung und ein
  neuer Leader mit frischem Volumen rückt nach — kein Tick-Redeploy,
  Rotation nur über den 1h-Scan-Takt.
- Strategieempfehlung pro Symbol: β ≥ 2,8 & RVOL ≥ 2,5 & Liq-Puffer klein
  → `sniper_hedge` (25x-Modus); sonst `dca` (5–10x). Liq-Distanz via
  MP-01-Guard berücksichtigen.
- Weekend: reduzierte Größe/Paper-Flag beibehalten (bestehende
  SessionClock-Logik respektieren).

### 2b. Dual-Dirigent (BTC & ETH)
- Pro Symbol zusätzlich β und r gegen **ETH** berechnen; Dirigent =
  der Benchmark mit dem höheren |r| (Nutzerbeobachtung §6: KI-/Tech-/
  DePIN-Assets hängen enger an ETH als an BTC). Die signed-
  Richtungsregeln aus §2 (Long nur r>0/β>0; Short r>0/β<0; inverse
  Longs verboten; decoupled fail-closed) gelten identisch gegen den
  gewählten Dirigenten. Konkrete Schwellenwerte sind zu messen, nicht
  zu kodieren; die Benchmark-Wahl fließt als Feld in das Ranker-Ergebnis.

### 2c. Nacht-Schattenplan (`sigma/orchestration/shadow_plan.py`)
- Reiner, nicht bindender Planungs-Output (Orchestrator bleibt
  klassifizierend/gatend — kein Auto-Deploy, Hartregel 1/12; der Plan
  löst auch kein Screening außerhalb des 1h-Takts aus).
- Rhythmus (UTC, MEZ=UTC+2): Überwachung der beim letzten 1h-Screen
  gewählten Symbole in der Ruhephase **21:00–00:30 UTC** (21:00–22:00 =
  Quarantäne, nur Beobachtung), Synthese **00:30–01:00 UTC**,
  Veröffentlichung **~01:00 UTC** (03:00 MEZ).
- Inhalt (Dataclass, `to_dict()`): Watchlist, pro Symbol antizipierte
  Szenarien (Sweep-Zone, Breakout-Level, Session-Bias, Dirigent),
  vorgemerkte Strategie-Option aus den vorhandenen Werkzeugen
  (Ranker-Empfehlung dca/sniper, SessionClock-Fenster, Wave-Zustand),
  plus die zwei Ausführungspfade:
  - **Pfad α (proaktiv, Sniper — Phase 2, MP-07):** Einstieg an der
    Kante bei passender Konfluenz und niedrigem Risiko.
  - **Pfad β (reaktiv, Bestätigung):** Einstieg erst im ersten Retest,
    nachdem **Dirigent UND Alt** den Breakout auf geschlossenen Bars
    vollzogen haben (Korrelation dann live bestätigt).
- Sentiment-Abgleich (optional, fail-closed wie Polymarket): Funding/
  Long-Short-Ratio/OI und — falls Feed existiert — Social-Sentiment als
  Gegenfilter; Sättigung (Funding extrem, Retail-Long > 4:1, Social >
  ~85 % bullisch) → Kennzeichnung `mean_reversion_bias` (Kontext für
  MP-08), nie als harter Trigger.
- SessionClock ist Eigentümerin der UTC-Fenster (inkl. EU-Open-
  KI/Tech-Bias und US-Open-Mean-Reversion, §3) — nicht neu bauen.

### 3. Orchestrator-Anbindung
- Ranker-Ergebnis als `ctx["screening"]`/Feld im Orchestrator-Kontext;
  Gate blockiert Mehrfach-Scans. **Kein Auto-Deploy neuer Strategien**
  (kommt mit MP-07) — in dieser Phase nur Klassifikation/Kontext.

## Tests (`tests/test_hourly_ranker.py`)
- Zeitphasen: Minuten 2/20/50/57 → korrekte Zustände.
- Zweiter Aufruf in derselben Stunde → Scan gesperrt; nächste Stunde frei.
- Synthetisches Universum: High-β-High-RVOL-Symbol rankt vor Low-β;
  Thin-Spread-Symbol wird mit Grund gefiltert.
- **Richtung:** positiv korrelierter High-β-Alt (r>0, β>0) →
  Long-Seite; positiv r mit negativem β → Short-Seite; r<0-Alt wird
  als **inverse_long_blocked** gefiltert (nie Auto-Long); |r|<0,30 →
  `decoupled` herausgefiltert.
- **Relativstärke-Vorselektion:** 24h-Top-Gainer mit RVOL ≥ 1,5
  ranken vor dem schwachen Rest; Asset nach Ausbruch in der
  Konsolidierung (`pos_EQ` 0,40–0,65) bekommt Leader-Kennzeichnung;
  vertikaler Spike (pos_EQ > 0,9) wird nicht als Einstieg markiert.
- **Rotation:** Ein Alt mit zerfallendem β/RVOL fällt im nächsten
  1h-Scan aus der Wertung; der frische Leader rückt nach (kein
  Tick-Redeploy, Rotation nur im Scan-Takt).
- Empfehlung: extremes Symbol → `sniper_hedge`; moderates → `dca`.
- Kein Look-Ahead (nur geschlossene Stundenbars).

## Nicht im Scope
- Keine Orderausführung, kein Pine-Deploy (MP-09), keine Sniper-Strategie
  (MP-07).

## Abnahme
Pytest grün; Orchestrator-Suite (`tests/test_orchestration*.py`,
`tests/test_quantum_wave_regime.py`) weiterhin grün.
