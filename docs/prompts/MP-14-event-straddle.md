# Master-Prompt MP-14 — Pre-Event Doppel-Hedge-Straddle (optional)

Lies `docs/SIGMA-WISSENSDATENBANK.md` §10.3 (Event-Waffe) und §8
(Risiko-Lehren). Voraussetzung: MP-01 (Guards), MP-08 (Unwind).
Prüfe `sigma/strategies/dual_hedge_grid.py` (Hedge-Grid-Logik
existiert — darauf aufbauen).

## Auftrag — `sigma/strategies/event_straddle.py`

- `EventStraddle(BaseStrategy)` mit `plan(ctx) -> StrategyIntent`:
  - Trigger nur aus Layer-0/Event-Kalender (Polymarket/Session-Kalender):
    Eintritt T−15 bis T−30 min vor dem Event; kein Trigger ohne
    Event-Flag (`valid=False`).
  - Doppelte DCA-Leitern: ±2 %, ±4 %, ±6,5 % um den Entry,
    Mini-Base-Size 10 %/10 % auf beide Seiten.
  - Verliererseite: Stop bei −2 % bis −4 %; Gewinnerseite:
    Trailing-TP ab +3 %, dann nachziehen.
  - Neutral-Abbruch: bewegt sich der Markt nach T+2h/T+4h nicht,
    beide Seiten schließen (TTL 2–4 h).
  - **Net-Profit-Guarantee**: Verliererseite wird erst geschlossen,
    wenn ihr Verlust ≤ 50 % des realisierten Gewinns der Gewinnerseite
    ist (Partial-Close-Logik; asynchrones Unwind aus MP-08 wiederverwenden).
- Alle Größen/Abstände als Benennungskonstanten; Leverage konservativ
  (kein 25x für Straddles — Margin beider Seiten vorhalten).
- Intent mit beiden Leitern, TTL, Event-ID; Paper-only.

## Tests (`tests/test_event_straddle.py`)
- Ohne Event-Flag → kein Intent.
- Trigger 20 min vor Event → beide Leitern mit korrekten
  ±2/4/6,5 %-Sprossen.
- Gewinnerseite +6 %, Verliererseite −2 %: Schließen erlaubt
  (Verlust < 50 % des Gewinns); Verlust −4 % bei nur +3 % Gewinn →
  gesperrt/teilweise, bis Quote passt (oder TTL-Zwang mit `forced`).
- T+4h ohne Bewegung → FLAT beider Seiten.
- Margin-Check: Gesamt-Margin beider Seiten innerhalb verfügbarer
  Quote (Guard aus MP-01-Mustern).

## Nicht im Scope
- Keine Live-Orders, kein Event-Kalender-Scraping in dieser Phase
  (Event-Flag wird injiziert), keine Änderung bestehender
  Hedge-Grid-Strategie.

## Abnahme
Pytest grün; ohne Event-Kontext ist die Strategie vollständig inert.
