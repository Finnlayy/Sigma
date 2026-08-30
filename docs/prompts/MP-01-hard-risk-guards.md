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
6. `fee_covered_stop(entry_price, side, offset_pct=0.0005)` —
   **Fee-Covered Break-Even** (Nutzer-Regel): nach TP1 wird der SL auf
   `entry × 1,0005` (long) bzw. `entry × 0,9995` (short) gezogen, nicht
   auf das exakte Entry. Begründung: Roundtrip-Taker-Fees (~0,04 %/Seite
   auf Notional) kosten bei 30x Hebel ~2,4 % Margin; der 0,05 %-Puffer
   deckt sie vollständig ab. Wird von MP-15 (fraktale Strategie) genutzt.

7. **Wick-/Liquidationsfallen-Guard (§8 Regel 10, Cross-Asset-Beleg über
   13 Paare):** `wick_buffer_pct(beta, expected_btc_wick_pct, extra_pct=0.01)`
   — erwarteter Alt-Wick = `β·BTC-Wick`; und
   `liq_outside_wick_zone(liquidation_price, wick_low_price, side)` prüft,
   dass der Liquidationspreis **unterhalb** der erwarteten Docht-Zone liegt
   (long) bzw. oberhalb (short); `assert_leverage_for_depth(beta,
   grid_depth_pct, leverage, expected_btc_wick_pct)` lehnt Hebel ab, bei
   denen der Liq-Preis in der Docht-Zone läge (Faustregel:
   Liq-Abstand ≥ Raster-Tiefe + β·BTC-Wick + Puffer). Hintergrund:
   BTC-Dips reißen β≈2,8–4,5-Alts in 60–180 s um −3…−8 % (V-Reversal);
   10x-Grids mit voller Margin wurden darin liquidiert/ausgestoppt,
   danach +31…+73 % Rebound.

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
- Fee-Covered: long `fee_covered_stop(100, "long") == 100.05`;
  short `== 99.95`; Wert liegt stets auf der sicheren Seite (long über,
  short unter Entry).
- **Wick-Guard:** Grid mit 8 % Tiefe, β=3,5, erwarteter BTC-Wick 1 %
  (→ erwarteter Alt-Wick ~3,5 %) bei 10x voller Margin → Konstellation
  abgelehnt (Liq in der Docht-Zone); gleicher Fall mit ausreichend
  niedrigem Hebel / Liq unterhalb Docht-Zone → passiert;
  `liq_outside_wick_zone` long mit Liq über dem erwarteten Docht-Tief
  → False.
- Nutze den im Repo üblichen Teststil (synthetische Bars als Listen/Dicts,
  vgl. `tests/test_loops_cde.py`).

## Nicht im Scope

- Keine Orderplatzierung, keine Exchange-Aufrufe.
- Keine Strategie-Logik, keine neuen Strategien.
- Keine Änderungen am Orchestrator-Gating.

## Abnahme

`pytest tests/test_risk_guards.py` grün, gesamte bestehende Suite grün,
keine neuen Abhängigkeiten.
