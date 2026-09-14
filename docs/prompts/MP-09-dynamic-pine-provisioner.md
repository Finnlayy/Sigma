# Master-Prompt MP-09 — Dynamischer Pine-v6-Provisionierer

Lies `docs/SIGMA-WISSENSDATENBANK.md` §12 (Pine-Provisioning, Schema A,
Alerts, TTL-De-Provisioning) und das API/Webhook-Kapitel im
`docs/BLUEPRINT-SIGMA.md` (Webhook Schema A, Loop B). Prüfe
`sigma/strategies/pine_v6_generator.py` (existiert bereits!),
`app/tv/alert_provisioner.py`, `app/tv/script_catalog.py`,
`app/tv/worker.py`. **Erweitere den bestehenden Generator.**

## Auftrag

### `sigma/strategies/dynamic_pine_provisioner.py`
- Eingabe-Dataclass `ProvisionRequest`: Symbol, entry/stop_loss/
  take_profit, fixed_leverage, strategy_id, webhook_secret,
  ttl_minutes, side (buy/sell), bar_close_only=True.
- Ausgabe: vollständiger, eigenständiger Pine-Script-v6-String:
  - **Standard-Header (Pflicht für jede generierte/gehärtete Strategie):**
    `initial_capital=10000`, `currency=currency.USD`,
    `default_qty_type=strategy.cash`, `default_qty_value=100`
    (100 USD Standard-Ordergröße), `pyramiding=1` (genau eine Position
    pro Richtung — Chart-Pyramiding läuft nicht; DCA/Leitern bleiben
    serverseitige Templates),
    `commission_type=strategy.commission.percent`,
    `commission_value=0.04` (0,04 % Standard-Gebühr pro Seite),
    `calc_on_every_tick=false`, `overlay=true`.
  - Alle Werte als injizierte Konstanten (keine freien Variablen,
    die der Nutzer setzen muss).
  - **Eindeutige Tracking-ID pro Alert:** Jeder `alert_message`-Payload
    trägt eine eigene, einzigartige `idempotency_key` nach dem Muster
    `{strategy_id}_{action}_{seq:02d}_{bar_unix}` (seq fortlaufend je
    Strategie/Bar: Entry = 01, TP1 = 02, TP2 = 03, TP3 = 04,
    UPDATE_SL = 05, CLOSE = 06 …). Kein Alert teilt sich eine ID;
    der Ingest antwortet auf Wiederholungen mit `DUPLICATE_IGNORED`.
    Zweck: keine Vertauschungen/Doppelausführungen bei mehreren
    ephemeren Strategien und Mehrfach-Alerts.
  - Alerts feuern auf **Bar-Close** (kein Intrabar-Repaint):
    `barmerge.lookahead_off`, Signale mit [1]-Offset berechnet.
  - Webhook-Payload exakt Schema A: `action` (BUY/SELL/CLOSE groß),
    `ticker`, `price` (Close der geschlossenen Bar), `stop_loss`,
    `take_profit`, `fixed_leverage`, `strategy_id`, `secret`.
  - **Zusätzlich Fraktal-Modus (für MP-15):** Entry-Payload mit
    gestaffelten TPs `tp1`/`tp2`/`tp3` je `{price, qty_pct}` (40/30/20),
    `runner_qty_pct` (10), `fee_covered_be_offset=0.0005`; nach
    TP1-Bar-Close ein Alert `action=UPDATE_SL` mit
    `new_sl = entry × 1,0005` (long) / `× 0,9995` (short) und
    `reason: TP1_HIT_FEE_COVERED_BREAKEVEN`.
  - Alert-Zustände: Entry, Teil-TP1/2/3, SL-Nachführung, Stop/Take-Exit —
    jeder mit korrekter `action` (CLOSE bei SL/TP).
  - **Umgang mit Fremd-Skripten (z. B. Gemini-Pine-v5-Entwürfe):**
    nicht direkt kopieren — entweder sauberes v6 eigen-generieren oder
    über `harden_pine_code()` laufen lassen. Rohfremde Skripte enthalten
    typischerweise intrabar-Alerts, fehlende Webhooks/Fremd-URLs und
    z. T. Python-Header; die Härtungsschritte oben decken genau das ab,
    nicht härtbare Exemplare werden abgelehnt (fail-closed).
  - Kopfkommentar mit strategy_id + TTL-Zeitstempel für das
    De-Provisioning nach Move/TTL.
- `de_provision_hint(request)` erzeugt die Kennung, unter der das
  Skript nach TTP/TP entfernbar ist (Loop B kümmert sich um TV selbst).

### Auto-Härtung fremder Pine-Skripte — `harden_pine_code()`

Fremde Skripte (Gemini-Ausgabe, manuell geschriebenes Pine, Skripte aus
der TV-Bibliothek) werden **nicht abgelehnt, weil ihnen die
Webhook-Anbindung fehlt** — der Provisionierer schreibt sie beim
Provisionieren automatisch auf Sigma-Standard um:

- Eingabe `PineHardeningRequest`: `raw_code` (beliebiges Pine v5/v6)
  + die gleichen Felder wie `ProvisionRequest` (Symbol, side, entry,
  stop_loss, take_profit bzw. tp1–tp3, fixed_leverage, strategy_id,
  webhook_secret, ttl_minutes).
- Ausgabe `HardenedPineResult`: `code` (v6-String), `transformations`
  (Liste der angewendeten Änderungen), `hardening_ok: bool`,
  `reasons` (Ablehnungsgründe). Transformationen mindestens:
  1. **Version:** fehlende Versionszeile → `//@version=6` ergänzen;
     `//@version=5` → v6 umschreiben (strategy.entry/exit kompatibel;
     nicht portierbare Konstrukte → fail-closed mit Grund).
  2. **strategy()-Header auf Sigma-Standard bringen/erzwingen:**
     `initial_capital=10000`, `currency=currency.USD`,
     `default_qty_type=strategy.cash`, `default_qty_value=100`,
     `pyramiding=1`, `commission_type=strategy.commission.percent`,
     `commission_value=0.04`, `calc_on_every_tick=false`;
     abweichende Werte (z. B. `initial_capital=100000`, qty in %,
     pyramiding>1, `calc_on_every_tick=true`, andere Commission)
     überschreiben und als Transformation loggen.
  3. **Webhook:** jeden `strategy.entry`/`strategy.exit`/
     `strategy.close` ohne Sigma-Payload mit Schema-A-`alert_message`
     (bzw. Fraktal-Payload mit tp1–3/UPDATE_SL) über
     `build_alert_message` versehen; vorhandene fremde
     `alert_message`/`alert()`-Aufrufe (fremde URLs) ersetzen bzw.
     entfernen und als Transformation loggen. In jeden Payload die
     eindeutige `idempotency_key` nach dem Muster
     `{strategy_id}_{action}_{seq:02d}_{bar_unix}` einsetzen.
  4. **Bar-Close-Guard:** Entry-/Exit-Bedingungen um
     `barstate.isconfirmed` ergänzen (oder [1]-Offset);
     `request.security(...)` ohne `lookahead=barmerge.lookahead_off`
     ergänzen; `lookahead_on` ersetzen; nicht statisch umschreibbar
     → fail-closed.
  5. **Konstanten & Kopf:** strategy_id, Secret-Platzhalter,
     TTL-Zeitstempel als Kopfkommentar; Order-relevante freie Eingaben
     wo statisch erkennbar durch die injizierten Konstanten
     (entry/TP/SL/Leverage/Ticker) ersetzen.
  6. Danach dieselben statischen String-Checks wie beim Eigen-Generator.
- **Fail-closed:** ist ein Skript nicht härtbar (Intrabar-Logik nicht
  umschließbar, Fremd-Webhook nicht entfernbar, v5-Konstrukt nicht
  portierbar), wird **kein Code deployed**; `hardening_ok=False` mit
  konkreten Gründen zurückgegeben (im UI sichtbar).
- **Grenze:** Die Härtung macht nur den *Transport* Sigma-konform
  (Webhook, Bar-Close, Payload, TTL). Sie macht die fremde
  *Signallogik* nicht kanonisch — fremde Skripte gelangen ausschließlich
  über den normalen Provisioning-Pfad (Scout-Symbol,
  Operator-Bestätigungs-Modal, `execution_mode="kraken_paper"`,
  TTL-De-Provisioning) in den Markt, niemals durch direkten Upload.

## Tests (`tests/test_dynamic_pine.py`)
- Generierter Code enthält: alle injizierten Konstanten, alle
  Schema-A-Feldnamen, `alert_message`/`alert()` mit Webhook-URL-Platzhalter.
- **Standard-Header:** Code enthält `initial_capital=10000`,
  `default_qty_type=strategy.cash` mit `default_qty_value=100`,
  `pyramiding=1`, `commission_type=strategy.commission.percent` mit
  `commission_value=0.04`, `calc_on_every_tick=false`.
- **Eindeutige IDs:** jeder generierte `alert_message`-Payload enthält
  eine `idempotency_key`; bei mehreren Alerts (Entry, TP1, TP2, TP3,
  UPDATE_SL, CLOSE) sind alle Keys paarweise verschieden und folgen
  dem Muster `{strategy_id}_{action}_{seq}_{bar_unix}`.
- Statische String-Checks: `lookahead_on` kommt nicht vor; kein
  `request.security(..., lookahead=...)` mit Repaint-Muster; Bar-Close-
  Bedingung (`barstate.isconfirmed` oder [1]-Offset) vorhanden.
- Zwei verschiedene Requests → zwei verschiedene Skripte (Kennzeichnung
  über strategy_id/Konstanten).
- CLOSE-Payload bei SL/TP enthält korrekte action.
- Fraktal-Modus: generierter Entry enthält tp1/tp2/tp3 mit Preis+qty_pct
  (40/30/20) und runner 10; UPDATE_SL-Alert referenziert entry×1,0005
  (long) bzw. entry×0,9995 (short).
- Deterministisch: gleiche Eingabe → identischer String.
- **Härtung (fremdes Pine):**
  - v5-`strategy.entry`-Skript ohne Alert → nach Härtung v6, mit
    Schema-A-`alert_message`, Secret/strategy_id, Standard-Header
    (10000 / cash 100 / `pyramiding=1` / 0,04 % /
    `calc_on_every_tick=false`), eindeutige `idempotency_key` je Alert,
    Bar-Close-Bedingung; `transformations` listet die Änderungen.
  - Skript mit fremdem Webhook/`alert_message` → Fremd-Payload ersetzt,
    keine Fremd-URL mehr enthalten.
  - `request.security` ohne `lookahead_off` → ergänzt;
    `lookahead_on` nicht mehr enthalten.
  - Intrabare Strategie (`calc_on_every_tick=true`, nicht statisch
    auf bar-close umschließbar) → `hardening_ok=False` mit Gründen,
    es wird **kein** Einsatzcode erzeugt.
  - Skript mit abweichenden Header-Werten (`initial_capital=100000`,
    qty in Prozent, `pyramiding=10`, feste Commission oder
    `calc_on_every_tick=true`) → nach Härtung stehen die
    Sigma-Standards (10000 / cash 100 / pyramiding=1 / 0.04 % /
    bar-close), die Überschreibungen sind in `transformations` gelistet.
  - Gehärteter Code besteht dieselben statischen Checks wie
    eigen-generierter Code (inkl. eindeutiger idempotency_keys je
    Alert); Härtung ist deterministisch.

## Nicht im Scope
- Kein TradingView-Upload/Login (Loop B / `app/tv/` bleibt zuständig).
- Kein Orderpfad auf Sigma-Seite (Webhook kommt von TV zurück und
  wird vom bestehenden Ingetest verarbeitet).

## Abnahme
Pytest grün; keine Netzwerkkontakte im Test.
