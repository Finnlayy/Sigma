# Master-Prompt MP-13 — Multi-Asset-Erweiterung (optional: XAU/XAG/Forex)

Lies `docs/SIGMA-WISSENSDATENBANK.md` §6.2 (Multi-Asset-Logik,
Wochenenden, Lead-Lag). Prüfe `sigma/orchestration/multi_asset_router.py`
(Router existiert) und `sigma/ports/` (Port-Muster). Diese Phase ist
optional und rein additiv — Krypto-Pfade dürfen sich nicht ändern.

## Auftrag

1. `sigma/ports/metals_port.py` / `forex_port.py` (Interfaces nach
   Kraken/MT5/IB-Port-Muster): gleiche Methoden wie der Krypto-Port
   (Bars, Ticker, Konto/Orders als Papier-Schnittstelle), Adapter
   zunächst als konfigurierbarer Stub mit `available=False` —
   fail-closed, keine Synthese.
2. `sigma/signals/market_hours.py`: Marktzeiten-Kalender (XAU/XAG
   ca. 23h/Tag mit Daily-Break, Forex 24/5 mit Session-Umschlägen,
   Krypto 24/7) — Deterministisch aus UTC-Zeitstempeln, keine
   Online-Kalender nötig (Feiertage als Datenfeld/Default offen).
3. Lead-Lag-Paarung analog BTC→Alt: XAU als Makro-Anker für XAG und
   industrielle Metalle; bestehenden `lead_lag_detector.py`
   wiederverwenden (nicht neu schreiben).
4. `multi_asset_router.py` um die neuen Venues als konfigurierte
   Instanzen erweitern: Venue ist Source of Truth (bestehendes
   Prinzip aus `execution/universe.py`); Krypto füllt Wochenenden,
   Metalle/Forex unter der Woche.
5. Leverage-Defaults pro Asset-Klasse als Konstante (kein 25x bei
   Metallen — konservativ, H4-Backtest vor Scharfschaltung).

## Tests (`tests/test_multi_asset_metals.py`)
- Marktzeiten: Samstag 12:00 UTC → Forex geschlossen, Krypto offen,
  XAU im Weekend-Break-Fenster korrekt klassifiziert.
- Router ohne konfigurierten Port → Venue `unavailable`, kein Signal
  (fail-closed).
- Lead-Lag: synthetische XAU→XAG-Nachlaufserie wird erkannt.
- Krypto-Suite unverändert (Regression).

## Nicht im Scope
- Echte MT5/IB-Anbindungen mit Credentials, Live-Trading,
- Keine neuen Strategien (bestehende Templates auf neuen Venues nur
  über Konfiguration, Paper-Modus).

## Abnahme
Pytest grün; ohne Ports/Konfiguration verhält sich das System exakt
wie heute.
