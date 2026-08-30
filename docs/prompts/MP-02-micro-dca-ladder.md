# Master-Prompt MP-02 — Micro-DCA-Ladder-Generator

Vorarbeit: `docs/SIGMA-WISSENSDATENBANK.md` §5.1 (Micro-DCA-Parameter),
§13.2 (Beispielrechnung), Phase MP-02 in `docs/SIGMA-ROADMAP.md`.
Setze voraus, dass MP-01 (`sigma/execution/risk_guards.py`) existiert;
importiere dessen Tiefen-Guard, statt ihn zu duplizieren.
Prüfe `sigma/strategies/dynamic_channel_dca.py` und
`sigma/strategies/base_strategy.py` auf vorhandene Raster-Logik —
erweitere statt neu zu erfinden, wo möglich.

## Auftrag

Erstelle `sigma/strategies/dca_ladder.py`:

- `build_ladder(entry_price, *, side="buy", n_safety=6, step_pct=0.002,
  step_mult=1.10, base_margin_pct, volume_mult=1.15)` → geordnete Liste
  von Sprossen (Preis, Margin-Anteil, kumulierte Distanz in %);
  Schrittweite wächst geometrisch um `step_mult`, Volumen wächst um
  `volume_mult`.
- `dynamic_step_from_range(high_2h, low_2h, current_price, n_safety,
  range_factor=0.618)` — Schrittweite aus Rolling-Range:
  `(Range / Preis × 0,618) / Stufen` (§5.1).
- `average_fill_price(filled_rungs)` — echtgewichteter Avg-Preis.
- `take_profit_price(avg_price, side, tp_pct=0.015)` — TP 1,5–2,0 % über
  Avg (long).
- `ladder_ttl_seconds = 7200` Konstante + `ttl_expired(opened_ts, now_ts)`.
- Eine Validierungsfunktion, die über die MP-01-Guards sicherstellt:
  Gesamt-Tiefe ≥ 6 % (Meme-Perp), erster Step ≥ Spread+Fee-Floor
  (FLOOR als Konstante, z. B. 0,10 %).

Dataclass `LadderRung`/`DcaLadder` mit `to_dict()` (Repokonvention).

## Tests (`tests/test_dca_ladder.py`)

- Reproduziere das Beispiel aus §13.2: Entry 1,00, Step 0,15 %, 8 Stufen,
  Vol-Faktor 1,15 — Avg nach allen Füllungen bei 0,9899, TP bei 1,0047.
- Dynamischer Range-Step: Range 3 % / 6 Stufen → Step ≈ 0,3 %.
- Tiefen-Guard: 8 × 0,15 % (~1,1 %) wird abgelehnt; generiertes
  Range-basiertes Raster passiert.
- Avg-Preis sinkt mit jeder Füllung (long); TP relativ zu Avg, nicht Entry.
- TTL: nach 2 h 1 min abgelaufen.

## Nicht im Scope

- Keine Live-/Paper-Orderausführung (das ist Loop A).
- Keine Hedge-/Straddle-Logik.
- Kein Orchestrator-Deployment.

## Abnahme

Pytest grün (neue Datei + gesamte Suite), volle Typisierung, keine
Platzhalter.
