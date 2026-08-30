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

## Nicht im Scope
- Keine Live-Ausführung, keine neue Entry-Strategie.
- VWAP/EMA20 können als einfache Indikatorfunktionen im Modul liegen,
  keine neuen Indikator-Bibliotheken.

## Abnahme
Pytest grün; bestehende Hedge-Grid-Tests bleiben grün.
