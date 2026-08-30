# Master-Prompt MP-08 — Volatilitäts-Exhaustion & asynchroner Unwind

Lies `docs/SIGMA-WISSENSDATENBANK.md` §10.1 (Exhaustion-Signale) und
§10.2 (Unwind-Reihenfolge). Prüfe `sigma/signals/volatility_throttle.py`
(ATR-Threshold-Logik existiert) und die bestehenden Strategien,
insbesondere `sigma/strategies/dual_hedge_grid.py` (Unwind-relevant).

## Auftrag

### 1. `sigma/signals/volatility_exhaustion.py`
- `exhaustion_score(bars_5m, open_interest_series, cvd_series) -> Exhaustion`:
  - BBW-Einbruch: Bollinger-Band-Breite auf 5m fällt > 40 % vom
    Tageshoch (skaleninvariant, Ratio).
  - OI-Divergenz: Preis neues Hoch, Open Interest fallend (letzte N Bars).
  - CVD-Flachlinie/Umkehr trotz Preisfortsetzung.
  - Score 0–1 gewichtet; Schwellen als Konstanten; `exhausted` bool.
  - Fehlt eine Datenreihe (OI/CVD optional) → entsprechender Teil 0,
    aber Score nur gültig, wenn mindestens BBW vorhanden (fail-closed
    ohne Bars).

### 1b. Sentiment-/Sättigungs-Exhaustion (optional, fail-closed, §4.6)
- Zusätzlicher, rein optionaler Eingang: Funding Rate, Retail-
  Long/Short-Ratio, Open Interest (Börsen-/Client-Daten) und — falls
  ein Feed existiert — Social-Sentiment (X/Reddit). Ohne Feed bleibt
  dieser Teil 0 (Telemetrie wie Polymarket, kein Gate).
- `sentiment_saturation(funding, ls_ratio, oi_series, social=None)`:
  Sättigung wenn Funding extrem positiv, Retail-Long > ~4:1, OI hoch
  bei flachem Spot-Volumen und (optional) Social bullisch > ~0,85.
  Kennzeichnung `mean_reversion_bias=True` als Kontext — **kein
  automatischer Short**: ein Mean-Reversion-Trade braucht zusätzlich
  die Bar-Close-Bestätigung aus §1/MP-03 (1m/5m Structure-Shift/
  Rejection); Blind-Short gegen laufenden Hype ist verboten.

### 2. `sigma/strategies/async_unwind.py`
- `AsyncUnwind(BaseStrategy)` (oder reine Unwind-Planer-Funktion,
  falls besser passend — entscheide nach `base_strategy.py`-Muster):
  Reihenfolge als Intent-Sequenz:
  1. Gewinnerseite zu 100 % schließen (Gewinn realisieren).
  2. Auf Pullback zu VWAP/EMA20 warten (max. Wartezeit als Konstante,
     danach Zwangsschritt).
  3. Verliererseite schließen.
  - Net-PnL-Guard: wenn der Verlust der Verliererseite > 50 % des
    realisierten Gewinns beträgt, trotzdem schließen (Schutz vor
    Backflip), aber als `forced=True` markiert.
  - Keine gleichzeitigen Market-Orders beider Seiten (Slippage-Schutz):
    Intents klar sequenziert mit Warte-Bedingung.
  - TTL-Beachtung: spätestens Minute 55 der Stunde muss alles flat sein.

## Tests (`tests/test_exhaustion_unwind.py`)
- Synthetische 5m-Bars mit BBW-Kollaps + OI-Divergenz + CVD-Umkehr →
  `exhausted=True`; reiner Trend ohne BBW-Einbruch → False.
- Unwind-Sequenz: erst Gewinner-, dann Verlierer-Seite; im Intent-Record
  nachprüfbar (Reihenfolge + Wartebedingung).
- Guard: Verlust > 50 % des Gewinns → `forced=True`, trotzdem Schließen.
- Ohne OI/CVD-Daten → Score aus BBW allein möglich; ohne Bars → ungültig.
- Ohne Sentiment-Feed → Sättigungsanteil 0, kein Fehler (fail-closed);
  mit extremem Funding/Long-Quote ohne Bar-Close-Bestätigung →
  `mean_reversion_bias=True`, aber kein Entry-Intent.

## Nicht im Scope
- Keine Live-Ausführung, keine neue Entry-Strategie.
- VWAP/EMA20 können als einfache Indikatorfunktionen im Modul liegen,
  keine neuen Indikator-Bibliotheken.

## Abnahme
Pytest grün; bestehende Hedge-Grid-Tests bleiben grün.
