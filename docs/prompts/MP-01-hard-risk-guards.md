# Master-Prompt MP-01 — Hard Risk Guards

Du arbeitest im Repository `Finnlayy/Sigma` (Python-Bibliothek `sigma/`).
Lies zuerst `docs/SIGMA-WISSENSDATENBANK.md`, insbesondere §8 (Verlust-Lehren)
und §14 (Verhaltensregeln), sowie `docs/SIGMA-ROADMAP.md` (Phase MP-01).
Schaue dir vorhandene Execution-Module an (`sigma/execution/`,
`sigma/loops/`), bevor du Code schreibst — du baust wiederverwendbare
Guards, keinen Orderpfad.

## Auftrag

Erstelle `sigma/execution/risk_guards.py` mit reinen, testbaren Funktionen:

1. `hard_stop_distance(entry_price, liquidation_price, side, buffer_pct=0.005)`
   — Hard-Stop **im Markt**, gepuffert 0,5 % über dem Liq-Preis (short) bzw.
   unter ihm (long). Stop wird als Order gesetzt, nicht als manueller Close.
2. `grid_total_depth_pct(ladder_prices, anchor_price, side)` — kumulierte
   Tiefe eines DCA-Rasters; `assert_grid_depth(depth_pct, symbol_spec,
   min_meme_depth=0.06)` lehnt Meme-Perp-Raster mit < 6 % Gesamt-Tiefe ab
   (festes 0,15 %-Raster über 8 Stufen = nur ~1,1 % Tiefe = unzulässig).
3. `btc_macro_breach(btc_closed_bars, support_price, side)` — BTC-Close
   (nur geschlossene Bars, 15m/1h) unter Support → Makro-Gate geschlossen
   für Alt-Käufe.
4. `liquidation_proximity_pct(mark_price, liq_price, side)` — Abstand in %;
   `< 0.05` (5 %) gibt `needs_hitl=True` zurück.
5. `cooldown_active(last_exit_ts, now_ts, min_seconds=1800)` — 30 min
   Cooldown nach einem Exit (Verlust oder TTL-Flat).

Alle Funktionen sind pure Funktionen ohne Seiteneffekte, mit vollständigen
Typannotationen und Docstrings. Werte als Prozentsätze als dezimale Floats
(0,06 = 6 %). Modul-Header nach der im Repo üblichen Konvention
(Datei/Zweck/System/Knoten als Docstring).

Ergänze in `sigma/execution/base_bridge.py` (bzw. dem bestehenden
Dispatch/Intent-Pfad — prüfe die tatsächliche Struktur) ein optionales
`requires_hitl: bool`-Feld, das von den Guards befüllt werden kann. Ändere
keine bestehenden Gate-Werte oder Session-Logik.

## Tests (`tests/test_risk_guards.py`)

- Long: Liq bei 0,96 × Entry → Stop bei Liq × 1,005, also < Entry und
  garantiert über Liq. Short spiegelbildlich.
- Raster mit 8 Stufen à 0,15 % wird für Meme-Perp abgelehnt; Raster mit
  ≥ 6 % Tiefe passiert.
- BTC schließt unter Support → `macro_gate_closed=True`; offene (letzte)
  Bar wird ignoriert.
- 4,3 % Liq-Distanz → `needs_hitl=True`; 12 % → False.
- Cooldown blockiert bei 29 min, erlaubt bei 31 min.
- Nutze den im Repo üblichen Teststil (synthetische Bars als Listen/Dicts,
  vgl. `tests/test_loops_cde.py`).

## Nicht im Scope

- Keine Orderplatzierung, keine Exchange-Aufrufe.
- Keine Strategie-Logik, keine neuen Strategien.
- Keine Änderungen am Orchestrator-Gating.

## Abnahme

`pytest tests/test_risk_guards.py` grün, gesamte bestehende Suite grün,
keine neuen Abhängigkeiten.
