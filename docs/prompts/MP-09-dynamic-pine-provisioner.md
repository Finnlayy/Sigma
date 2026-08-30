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
  - Alle Werte als injizierte Konstanten (keine freien Variablen,
    die der Nutzer setzen muss).
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
  - **Warnung:** Ein im Chat kursierender Gemini-Pine-v5-Entwurf ist
    fehlerhaft (intrabar-Alerts, `strategy.entry`, Python-Header) und
    darf nicht kopiert werden; erzeuge sauberes v6 mit
    Bar-Close-Bedingung.
  - Kopfkommentar mit strategy_id + TTL-Zeitstempel für das
    De-Provisioning nach Move/TTL.
- `de_provision_hint(request)` erzeugt die Kennung, unter der das
  Skript nach TTP/TP entfernbar ist (Loop B kümmert sich um TV selbst).

## Tests (`tests/test_dynamic_pine.py`)
- Generierter Code enthält: alle injizierten Konstanten, alle
  Schema-A-Feldnamen, `alert_message`/`alert()` mit Webhook-URL-Platzhalter.
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

## Nicht im Scope
- Kein TradingView-Upload/Login (Loop B / `app/tv/` bleibt zuständig).
- Kein Orderpfad auf Sigma-Seite (Webhook kommt von TV zurück und
  wird vom bestehenden Ingetest verarbeitet).

## Abnahme
Pytest grün; keine Netzwerkkontakte im Test.
