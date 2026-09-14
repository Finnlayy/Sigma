# Master-Prompt MP-03 — Candle- & Regime-Signale

Lies `docs/SIGMA-WISSENSDATENBANK.md` §3 (Two-Bar-Thrust / Marubozu-FVG /
Daily-Open-Envelope) und §16 (Wissens-Evidenz: Kontext-Filter kennzeichnen).
Prüfe vorhandene Signale: `sigma/signals/quantum_wave_collider.py`,
`sigma/signals/htf_features.py` (dort existiert bereits `fvg_flags`!),
`sigma/signals/scale_features.py`, `sigma/core/fractal_scaling.py`
(CE50/Discount existieren). **Nicht duplizieren** — wo FVG/CE50 schon da
sind, erweitere oder importiere.

## Auftrag — drei neue Module unter `sigma/signals/`

### 1. `two_bar_thrust.py`
- Eingabe: geschlossene Kerzen (Liste/Series mit open/high/low/close/volume).
- Muster: Bar[2] bärisch (close < open); Bar[1]+Bar[0] bullisch;
  Summe der Bull-Bodys > Bär-Body; Bar[0].close > Bar[2].high.
- Optionale Kontext-Flags (als separate bool-Felder, **nicht** als
  harte Bedingung, da Backtest-Offen): Support-Konfluenz, EMA-Abstand,
  Session-Sweep.
- Look-ahead-frei: nur abgeschlossene Bars; Eingabe ohne die zuletzt
  offene Bar verarbeiten bzw. offene Bar ignorieren.

### 2. `marubozu_fvg.py`
- Marubozu: Body/Range ≥ 0,80 (keine/kaum Dochte).
- FVG-Größe in ATR-Einheiten (skaleninvariant!), bull/bear; Zone als
  (low, high), CE50 über bestehendes `fractal_scaling`-Wissen.
- Wiederverwende/erweitere `htf_features.fvg_flags` statt Konkurrenzcode.

### 3. `daily_open_envelope.py`
- Anker 00:00 UTC; die Top-N-Volumen-Bars seit Tagesbeginn bestimmen
  obere/untere Hüllkurve (einfache Regression/Extrema — deterministisch,
  keine ML-Abhängigkeit).
- Steigungs-Drift der Hülle; **Outside-Inside-Reversal**: Bar schließt
  außerhalb, Folgebar grün und wieder innerhalb → Signal.
- Volumen-Anker muss auch bei dünnen Sessions funktionieren (fail-closed:
  < N Bars → kein Signal).

Jedes Modul: Dataclass für das Signal mit `to_dict()`, reine Funktionen,
volle Typen, Modul-Header.

## Tests (`tests/test_candle_signals.py`)

- Synthetische Thrust-Sequenz (1 Bär, 2 starke Bull, Close über Hoch)
  → Signal; Einzel-Grün oder Bär ohne Folge → kein Signal.
- Marubozu konstruiert (Body 95 % der Range) + 3-Bar-FVG → Zone/CE50
  korrekt; FVG-Größe in ATR-Einheiten plausibel.
- Envelope: Kerzen mit 00:00-Anker, Top-Volumen-Bars bilden Hülle;
  Outside-Bar + grüne Inside-Bar → Reversal-Signal.
- Look-ahead-Test: Ergebnis bis Bar[k] identisch, egal ob spätere Bars
  angehängt werden.

## Nicht im Scope
- Kein Orchestrator-Deploy, keine Strategien, keine Orders.
- Pine-Code nur, falls als String-Konstante für spätere Phasen nötig
  (sonst weglassen).

## Abnahme
Pytest grün; keine Duplizierung von `htf_features`/`fractal_scaling`.
