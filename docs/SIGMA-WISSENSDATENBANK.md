# Projekt:Sigma — Wissensdatenbank (extrahiert, dedupliziert, strukturiert)

> **Zweck:** Dieser Artikel ist die kanonische Wissensquelle für alle weiteren
> Implementierungen. Er entstand aus der Extraktion und Dedupplikation der
> Chat-Verläufe (Strategiediskussionen, Live-Trading-Lektionen, Research-Dossier
> zur Zeitebenen-Harmonik, Elektrotechnik-Analogien).
>
> **Status-Markierungen:**
> - ✅ **im Repo vorhanden** — Modul existiert unter `sigma/`
> - 🟡 **teilweise / Stub** — Schnittstelle existiert, Logik ist lückenhaft
> - ❌ **neu zu bauen** — siehe Roadmap (`SIGMA-ROADMAP.md`)
>
> **Regel:** Wissen, das hier steht, wird nicht neu erfunden. Vor jedem
> neuen Modul: existierende Dateien prüfen.

---

## 1. Was Sigma ist

**Sigma ist eine modulare Quantitative-Bibliotheks-Architektur — kein einzelner Bot.**

- Wiederverwendbare, deterministische, typsichere Python-Module
- Package-Layout: `sigma/core/`, `sigma/signals/`, `sigma/strategies/`,
  `sigma/execution/`, `sigma/orchestration/`, `sigma/loops/`
- Nutzbar als Python-Library **und** als Backend-Dienst
- Der **Master-Orchestrator klassifiziert, gatet und reicht Kontext an
  Strategie-Templates weiter — er platziert selbst keine Orders**
  ✅ (`sigma/orchestration/master_orchestrator.py`)
- Strategien planen nur **Intents** (`StrategyIntent`, Schema-A-fähig);
  Ausführung läuft über Loop E (Allokations-Gate) → Loop A (Execution)
- Standard-Ausführungsmodus: **kraken_paper** bis eine Strategie alle
  Reifegrade (H3/H4/H5) bestanden hat

### Architektur-Prinzipien (unveränderlich)

1. **Fail-Closed:** Fehlende Daten/Feeds/API-Keys → leeres Ergebnis / `None` /
   `valid=False`. Niemals synthetische Werte als echte Signale.
2. **Zero Look-Ahead:** Alle HTF-Features nur aus vollständig geschlossenen
   Kerzen (Slice `[:-1]` bei offener letzter Kerze; Pine: `[1]`-Offset,
   `barmerge.lookahead_off`, Alerts nur auf Bar-Close).
3. **Bar-Level Execution Lock:** Eine Aktion pro Kerze; ein Scan pro
   geschlossener HTF-Bar.
4. **Skaleninvarianz:** Features normiert (`pos_t ∈ [0,1]`, FVG-Größe in
   ATR-Einheiten, Ratios statt absoluter Volumina) — kein absoluter Dollar-Preis
   in Signalen/Modellen.
5. **Kein Manual-Panic-Close:** Exits laufen über im Markt liegende
   Stop-/TP-Orders, nie über manuellen Verkauf im Tief (siehe §8, Live-Lektion).
6. **Ordnung:** Orchestrator = Gehirn (Regime/Gates/Kontext); Strategie =
   Template (`plan(ctx) → Intent`); Execution = Loop A. Schichten mischen nicht.
7. **Code-Hygiene:** Volle Typannotationen, Dataclasses mit `to_dict()`,
   keine Platzhalter (`TODO`/`pass`-Stubs), Pytest für jedes Modul.

---

## 2. Die 4-Layer-Pipeline

```
LAYER 0  Pre-Regime ........ Polymarket / Vorhersagemärkte (Stunden-/Tagesbasis)
   │                              geld-gewichtete Wahrscheinlichkeiten
   ▼
LAYER 1  Makro-Lead .......... BTC als Dirigent: 15m/1h/4h Key-Levels,
   │                              FVG, Dealing Range, 30m-Slope
   ▼
LAYER 2  Asset-Scout ......... Rolling r/β vs BTC, RVOL, Spread/Tiefe,
   │                              Bucket-Klassifikation, Ranking
   ▼
LAYER 3  Execution ........... DCA-/Grid-/Sniper-Strategien, Hebel,
                                  Stop/TP, TTL — auf den gescouteten Symbolen
```

- **BTC wird nicht selbst gehandelt** (zu schwerfällig, ~1-2 %/Tag). BTC ist
  Taktgeber und Schutzschild; das Geld wird auf High-Beta-Alts gemacht.
- Analogie aus der Diskussion: *BTC ist der Dirigent, die Alts sind die
  Solisten, der Scout ist das Vorsingen.*
- Layer 0 (Polymarket) ist **optional und fail-closed**: ohne Feed kein Gate,
  aber auch keine synthetischen Odds 🟡 (`sigma/signals/polymarket_layer0.py`).

---

## 3. Regime-Signale (Orchestrator-Eingänge)

Alle werden pro Tick ausgewertet und als Dictionary in den Tick-Payload und
in den Planungs-Kontext (`ctx`) injiziert.

| Signal | Datei | Status | Kernlogik |
|---|---|---|---|
| Session-Clock | `sigma/signals/session_clock.py` | ✅ | Asia/London/NY + 21:00-UTC-Liquiditätslücke |
| Dual-Hurst | `sigma/signals/dual_hurst.py` | ✅ | DFA-Hurst HTF/LTF; H>0.55 Trend / H<0.45 Reversion |
| Volatility-Throttle | `sigma/signals/volatility_throttle.py` | ✅ | ATR-Ratio → Anzahl gleichzeitiger Bots |
| Wave-Collider | `sigma/signals/quantum_wave_collider.py` | ✅ | Dealing-Range/FVG/CE50 → IDLE/COLLAPSED/INVALIDATED/HTF_OPEN |
| Timeframe-Ladder | `sigma/signals/timeframe_ladder.py` | ✅ | 4–6x Bias-Paare, 12–16x Execution-Paare |
| FVG/HTF-Features | `sigma/signals/htf_features.py` | ✅ | 3-Bar-FVG-Flags, EQ-Position |
| Scale-Features | `sigma/signals/scale_features.py` | ✅ | Skaleninvariante Feature-Extraktion |
| Correlation-Scout | `sigma/signals/correlation_scout.py` | ✅ | Rolling r/β → Buckets 1–3 |
| Multi-Asset-Router | `sigma/orchestration/multi_asset_router.py` | ✅ | Routing je Session/Bucket, Weekend-Paper-Flag |
| Polymarket Layer-0 | `sigma/signals/polymarket_layer0.py` | 🟡 | Nur Stub: Payload-Validierung, kein Feed |

### Gating-Logik (im Orchestrator, ✅ implementiert)

- HTF nicht bereit (`htf_ready=False`) → `idle("htf_not_ready")`
- Throttle `SLEEP` **oder** Session-Liquiditätsgap (21:00 UTC) → `unwind_only`
- Wave `INVALIDATED` (BTC schließt unter Range-Low) → `unwind_only`
- `COLLAPSED_INTO_ZONE` / `IDLE` → normale Template-Planung läuft weiter
  (Kollaps ist **Klassifikation, kein Deploy-Trigger**, solange es keine
  Sniper-Strategie gibt — Phase 2 der Wave-Diskussion)

### Volatility-Throttle (Stellgrößen)

ATR-Ratio = aktuelle ATR(14, 15m) / Basis-ATR (96×15m ≈ 24h):

| Ratio | Modus | Max. parallele Bots | Cooldown |
|---|---|---|---|
| < 0.70 | SLEEP | 0 | 1800 s |
| 0.70–1.40 | NORMAL | 3 | 300 s |
| > 1.40 | AGGRESSIVE_HARVEST | 8 (Cap) | 60 s |

Formel: `N_max = clamp(floor(ATR_aktuell / ATR_basis × N_base), 0, Cap)`.

### Sessions (UTC, research-kalibriert)

| Session | UTC | Charakter | Strategie | Max. Hebel |
|---|---|---|---|---|
| Asia | 00:00–07:00 | Akkumulation, enge Range, niedrige Vol | Micro-Grid / Idle | 5x |
| London | 07:00–13:00 | „Judas Swing“ / Liquiditäts-Sweeps, Fakeouts | Dual-Hedge / Fade | 10x |
| New York | 13:00–20:00 | Echtes Trendvolumen, Expansion | High-Beta Momentum / HTF-LTF | 25x |
| US Close | 20:00–24:00 | Abschwung, Mean-Reversion | Unwind/Flat | 5x |

**Harte Liquiditätsfenster (Research Amberdata/Wang et al. 2020):**
- 🚫 **21:00–22:00 UTC:** −25 % Markttiefe → **Quarantäne, keine neuen Entries**
- ✅ **08:00–09:00 UTC:** EU-Open, +2.16 % Bid-Druck
- ✅ **14:00–16:00 UTC:** US-Overlap, ausgewogenste Orderbücher
  (Imbalance-Streuung −20 %)
- ⚠️ **Wochenende:** Volumen −20–25 %, Ask-Bias, „illusorische Tiefe“
  (unbetreute Orders verdunsten unter Stress) → reduzierte Größe / nur Paper
- ⚠️ **Montag 10:00 UTC:** Monday-Momentum-Effekt

---

## 4. Marktstruktur-Signale (Price Action / SMC, entmystifiziert)

> **Evidenzhinweis:** SMC/ICT-Begriffe (Dealing Range, FVG, Killzones) sind
> **nicht peer-reviewed** (umbenanntes Wyckoff/Raschke-Wissen — Evidenz:
> *schwach*). Die zugrunde liegenden Mikrostruktur-Mechanismen sind jedoch
> **stark belegt** (Cont/Kukanov/Stoikov 2014 OFI-Linearität;
> Square-Root-Impact-Law). Sigma implementiert die **Mathematik**, nicht die
> Narrative.

### 4.1 Fair Value Gap (FVG) / Displacement
- 3-Kerzen-Imbalance: bullische FVG wenn `Low[2] > High[0]` (Kerze 1 =
  Displacement), grüne Displacement-Kerze, Body/Range > 0.80 (Marubozu-nahe)
- **CE50** (Consequent Encroachment, 50-%-Mitte):
  `ce50 = fvg_bottom + (fvg_top − fvg_bottom) × 0.5` ✅ in fractal_scaling
- Regel: **kein Market-Kauf auf Displacement** (Anti-FOMO); Limit-Einstieg in
  den Retest der CE50; Stop unter die FVG-Fernkante + Buffer
- FVG-Qualität: Größe ≥ 1× ATR, Alter < 10–20 Kerzen, bias-aligned,
  Entstehung in Overlap-/Killzone stärker
- Inverse FVG (IFVG): Bruch in Gegenrichtung → Polaritätswechsel =
  Strukturbruch-Signal
- Timeframe-Eignung: M15/H1/H4; unter M5 füllen FVGs unzuverlässig
  (Mikrostruktur-Rauschen, NSR ≈ 36,6 %)

### 4.2 Dealing Range
- `Range_Low` = Tief vor dem Displacement (Origin),
  `Range_High` = Peak nach 3–4 Kerzen
- Equilibrium: `EQ = (High + Low)/2`; normalisierte Position
  `pos_t = (P − Range_Low)/(Range_High − Range_Low)`
- Discount: `pos_t < 0.5` (Long-Zone); Premium: `pos_t > 0.5` (Short-Zone)
- OTE (Optimal Trade Entry): 62–79 %-Retracement-Band (~70,5 % Mitte)
- **Invalidation:** BTC schließt auf HTF unter `Range_Low` → Setup tot
  ✅ im Wave-Collider als `INVALIDATED`
- Geschachtelte Ranges: HTF-Kontext hat Vorrang vor LTF (4H-Discount kann
  15m-Premium sein)

### 4.3 Two-Bar Bullish Thrust (Umkehrmuster)
- Eine bärische Kerze, gefolgt von **zwei bullischen Kerzen**, deren
  gemeinsamer Body den bärischen Body übertrifft und deren Close über
  `High[2]` liegt
- Eine einzelne grüne Kerze nach Dump = Dead-Cat-Bounce; zwei bestätigte =
  nachhaltige Nachfrage
- Höchste Trefferquote an Key-Support / EMA20/50 / nach Session-Sweep
- Stop: Tiefstes Tief der beiden Bullenkerzen
- ❌ noch nicht als eigenes Signal-Modul implementiert (Logik teils in
  htf_features/wave angedeutet)

### 4.4 Liquidity Sweeps / Doppelboden
- **SSL (Sell-Side Liquidity Sweep):** Bruch unter alte Tiefs → Stop-Run →
  Rejection zurück in die Range; Doppelboden = Equal-Lows-Sweep + Reclaim
- **BSL (Buy-Side):** alte Hochs als Zielzonen
- Liquiditätshierarchie (ICT, Praxiswissen): Previous Day H/L >
  Previous Session H/L > Previous Week H/L > etablierte M15-H/L
- **Liquidity-Void-Prinzip:** Nach schnellem einseitigen Move ist das
  Mindestziel der Gegenbewegung der **Ursprung des Moves** (die
  „türkise Linie“ im Chat)

### 4.5 Multi-Timeframe-Harmonik
- **Rule of 4–6x (Kontext/Setup, Elder Triple-Screen):** H1←M15 (4x),
  H4←H1 (4x), D←H4 (6x) ✅ in timeframe_ladder
- **Rule of 12–16x (Execution, ICT):** H1←M5 (12x), M15←M1 (15x),
  H4←M15 (16x) ✅ in timeframe_ladder
- **Standard-Paarungen:**
  - High-Beta-Meme-Sniper: **15m HTF → 1m LTF (15x)**
  - Makro-Intraday: **1h HTF → 5m LTF (12x)**
  - Core-Alt-Trend: **1h HTF → 15m LTF (4x)**
- Altcoins: Execution eine Stufe höher als bei BTC (M15 statt M5) —
  dünnere Bücher, mehr Rauschen
- HTF liefert **Richtung + Zonen**, LTF liefert **Timing + Risiko**
- MTF-Backtests: chronologische Splits, Walk-Forward, HTF-Werte nur nach
  Kerzenschluss, `request.security_lower_tf()` in Pine v6

---

## 5. Strategie-Bibliothek

### 5.1 Micro-DCA (Bullen-DCA auf High-Beta-Alts)

**Nutzer-Parameter (Live-verifiziert):**

| Parameter | Wert |
|---|---|
| Step-Abstand | 0,1–0,2 % (0,15–0,20 % bevorzugt) |
| Safety-Orders | 5–8 pro Richtung |
| Volumen-Multiplikator | 1,1–1,2 (1,15x Standard) |
| Step-Multiplikator (dynamisch) | ~1,10–1,15x (spätere Stufen weiter) |
| Take-Profit | 1,5–2,0 % auf Average Price (1,5 % bevorzugt) |
| TTL | max. 2 h je Bot-Lauf |

**Wichtige Korrektur aus der Forschung/Diskussion:**
- Fester Step 0,15 % × 8 Stufen deckt nur ~1,1 % Gesamt-Tiefe ab → bei
  echten BTC-Dumps sind alle Stufen in Sekunden gefüllt (Live passiert, §8).
- **Dynamischer Step aus der 1–2h-Range des Altcoins:**
  `step = (Rolling_High_2h − Rolling_Low_2h)/Preis × 0.618 / Anzahl_Stufen`
  (0.618 = Golden-Ratio-Faktor; deckt die untere Schwingungshälfte ab)
- **Gesamt-Gridtiefe für Meme-Perps: mindestens 6–10 %** (Hard-Guard)
- Step darf nie kleiner sein als Spread + Fees (sonst Gebühren-Verbrennung)
- TP an Rolling-Range-Oberkante koppeln, Range muss mitwandern (trailing)

🟡 `sigma/strategies/dynamic_channel_dca.py` existiert (EQ-basiert), aber
ohne explizite Ladder-Generierung und Tiefen-Guard → Roadmap-Phase MP-02.

### 5.2 25x-Hedging-Grid (für extrem-heble Meme-Perps wie 我踏马来了)

- Account: Cross-Margin + Hedge-Mode (gleichzeitig Long/Short)
- Grid-Spacing 0,35–0,60 % (0,40 % Beispiel), 20 Stufen (10/10)
- Max. 2–3 % Margin pro Grid-Order
- Trailing-Korridor: Grids wandern mit dem Kurs mit
- Funding-Rate-Kompensation beachten; Ein-Weg-Rallye ist Hauptrisiko
- **Delta-Stop:** Short-Verluste > 1,5× realisierte Grid-Gewinne → Glattstellung
- Circuit-Breaker: BTC-Trendbruch auf 1h → alles schließen

### 5.3 Pre-Event Doppel-Hedge-Straddle (Event-Waffe, kein Dauer-Bot)

- Deployment **15–30 min vor erwartetem Move** (CPI/FOMC/BTC-Breakout/Sweep)
- Mini-Base Long + Short (je ~10 %), Safety-Long bei −2/−4/−6,5 %,
  Safety-Short bei +2/+4/+6,5 %
- Phase A (Expansion): eine Seite profitabel, Gegenseite sammelt Schnitt
- Phase B (Mean-Reversion): Gegenseite schließt ebenfalls im Plus
- **TTL 2–4 h:** kein Move → neutraler Abbruch, Flat
- Trailing-TP aktiviert ab +3 % mit 0,8 % Toleranz
- **Net-Profit-Guarantee:** Verlierer-Seite wird spätestens geschlossen,
  wenn ihr Verlust 50 % des realisierten Gewinns der Sieger-Seite erreicht

### 5.4 15m→1m Quantum-Sniper (Phase 2 im Chat, ❌ noch nicht gebaut)

- 15m BTC: Wave-Collider meldet `COLLAPSED_INTO_ZONE` (Discount + FVG-Touch)
- 1m Alt: Scanner auf 1m-Retest (1m-FVG / Two-Bar Thrust / CVD-Absorption)
- DCA-Ladder (4–6 Stufen, 0,2 % Step, 1,15x Vol, 1,5 % TP) wird **vorab als
  Limit-Orders** platziert (manuelles Handeln auf 1m bei 25x ist unmöglich)
- SL 1,0–1,5 %, TP 1,5–3 %
- **TTL-Hard-Regel:** 1m-Trades nur innerhalb der ersten **45–48 Minuten**
  der bestätigten 1h-Kerze; Minute 48–50 Zwangs-Flat; 55–60 Idle
  (Begründung: Der 1h-Vektor gibt Richtungsschild preis; nach Kerzenschluss
  ist der Downscale-Vorteil weg)

### 5.5 Fraktaler High-Leverage-Einzeltrade (Directional mit TP-Staffel, ❌ noch nicht gebaut → MP-15)

Vom Nutzer am 30.08.2026 spezifiziert: Übertragung des DCA-Prinzips auf
**einzelne direktionale Trades** (Long/Short) mit fraktaler
Teil-Gewinnmitnahme. Da das Risiko über Positionsgröße + harten SL
definiert ist (statt über Nachkauf-Marge), sind Hebel **20x–50x**
vertretbar — das löst die Margin-Bindung der DCA-Grids.

- **Entry:** validierter Wendepunkt — Punkt C einer XABCD-Harmonik,
  Liquidity-Sweep oder Breakout nach BTC-Lead-Signal + Alt-Volume-Surge
  (Lead-Lag-Beleg: BTC 1m-Break über Neckline 78.124 →
  我踏马来了 0,01340 → >0,01460, ~+9 % im Move).
- **TP-Staffel (frak­tale Stückelung):**
  | Stufe | Anteil | Ziel | Begründung |
  |---|---|---|---|
  | TP1 | 40 % | ~+1,0 % | erste Widerstandszone/Schnellgewinn |
  | TP2 | 30 % | ~+2,0 % | Neckline/FVG |
  | TP3 | 20 % | ~+3,5 % | harmonisches Ziel D (1,618-Ext.) |
  | Runner | 10 % | offen | ATR-Trailing für parabolische Züge |
- **Pflicht-Guardrail nach TP1:** SL der Restposition zwingend auf
  **Fee-Covered Break-Even** ziehen (siehe §8 Regel 6) — Trade ist ab
  dann netto risikofrei.
- **Initialer Hard-SL:** 0,6 % gegen Entry (MP-01-Liq-Puffer-Regel hat
  Vorrang; SL 0,5 % über Liq-Preis).
- **Beispielrechnung (30x, 20 USDT Margin = 600 Notional):** SL-Hit
  −3,60 USDT (−18 % Margin); bei +2,5 % Move: TP1 +2,40 / TP2 +3,60 /
  TP3 +4,20 USDT = +10,20 USDT (+51 % ROI auf Margin).
- **Webhook:** `open_long`/`open_short` mit tp1..tp3 je `{price,
  qty_pct}` + Runner + `update_sl` nach TP1 (Schema in §12).

### 5.6 Vorhandene Templates ✅

- `htf_trend_ltf_reversion` (NY-Momentum, generiert Pine v6)
- `dynamic_channel_dca` (Asia/Channel-DCA)
- `dual_hedge_grid` (London Judas-Sweep-Fade)
- Session→Template-Mapping im Orchestrator: London=DualHedge,
  Asia=ChannelDCA, NY=HTF-LTF

---

## 6. Symbol-Universum

### Kern-Ticker

| Symbol | Hebel | Liq.-Schwelle | Rolle |
|---|---|---|---|
| 我踏马来了/USDT (WOTAMALAILE) | 25x (Diskussion) / 10x live | ~−4 % (25x) | **Kein tiefes DCA** — nur Sniper/Momentum/Hedge-Grid |
| 龙虾/USDT (LONGXIA / Lobster) | 5x | ~−20 % | **DCA-Kandidat**, 3 Tranchen (35/35/30 %) |
| CLOUD, HEMI, NIL, SKR, FOUR … | 5–10x je nach β | — | Dynamische Bucket-Zuordnung |

- Alt-Korrelation zu BTC **rotiert alle paar Tage**: Coins dekoupeln
  (Token-Unlocks, VC-Dumps, Funding-Squezes, Narrative-Rotation)
- **Bucket-Logik ✅ (correlation_scout):**
  - Bucket 1 (High-Beta Longs): r > 0,80, β > 1,8 → Long-DCA bei BTC-Support
  - Bucket 2 (Inverse/Weak): r < 0,20 / negativ → Short-Kandidaten bei
    BTC-Rejection
  - Bucket 3 (Chop/Blacklist): zu geringes Volumen/Spread → nicht anfassen
- Neuere/kleine Ticker brauchen zwingend Liquiditäts-Filter
  (Mindest-Orderbuchtiefe, Spread-Cap)
- **Scoring (❌ zu bauen):** `Score = β × RVOL × r − Spread_Penalty`;
  Zuordnung β ≥ 2,8 & RVOL ≥ 2,5 → 25x-Sniper; sonst 10x/5x-DCA
- **Screening-Takt:** 1× pro geschlossener 1h-BTC-Kerze
  (Minute 00–05 Scan&Deploy, 05–48 Execution, 48–55 Unwind, 55–60 Idle)
- **Live-Beleg Lead-Lag (30.08.2026):** BTC 1m XABCD-Break über Neckline
  78.124 → 78.177 (Book 65 % Bids / 35 % Asks); 我踏马来了 reagierte
  0,01340 → >0,01460. Folge-DCA-Bots: Bot #1 (10x Long, 1h51m)
  +52,76 % (+7,96 USDT; Arbitrage +66,06 % / Trend −13,31 %, Close 0,014351),
  Bot #2 (10x Long, 5m31s) +1,51 % (reiner Trend-PnL). Bestätigt:
  BTC-Lead-Signal → High-Beta-Alt-Ausführung funktioniert; Hauptleck ist
  der Exit (siehe §8 Regel 8).

---

## 7. Polymarket / Vorhersagemärkte (Layer 0)

**Warum:** Geld-gewichtete Wahrscheinlichkeiten aggregieren informiertes Geld
vor Spot-Volumen; auf Stunden-/Tagesbasis sehr treffsicher (~84–94 % bei
liquiden Märkten laut Chat-Recherche — mit Vorsicht zu genießen, eigene
Kalibrierung nötig). Auf Minutenebene manipulationsanfällig → **nur Makro-Horizont**.

**Mathematik (im Chat entwickelt):**
1. **Fixe Zeit T:** Auf Polymarket ist der Zeithorizont durch das
   Verfallsdatum fixiert (Randwertproblem) — eine von zwei Unbekannten des
   Tradings („wann UND wie weit“) fällt weg.
2. **Implizite Dichte (Breeden-Litzenberger-Analogie):**
   Binäre Strikes mit Yes-Preisen → kumulierte Wahrscheinlichkeit;
   Differenz zweier Strikes = Wahrscheinlichkeit des Preisbins dazwischen.
   `P(K_i ≤ P ≤ K_{i+1}) = prob(K_i) − prob(K_{i+1})`
3. **Term-Struktur:** Quoten für T+1h, T+2h, T+4h, Tagesende abfragen →
   Erwartungswerte μ(T); Differenz `Δμ/ΔT` = erwartete Move-Geschwindigkeit
   (Richtung, Amplitude, Beschleunigung)
4. **Kalibrierung:** Platt-Scaling / Brier-Score aus historischer
   Trefferquote (Roh-Quoten sind overconfident); nur Märkte mit
   Volumen > ~1 Mio $ / ausreichender Liquidität
5. **Gate:** Long-Freigabe wenn kalibrierte Wahrscheinlichkeit > 0,60–0,65
   UND 1h-Struktur kongruent; ≤ 0,50 → RISK_OFF/Flat
6. **Handelsfenster:** optimales Fenster ≈ Expiry × 0,75

**Status:** 🟡 Payload-Validierung existiert; Feed-Adapter, Term-Struktur,
Dichte-Extraktion und Kalibrierung fehlen → MP-06.

---

## 8. Risikomanagement — harte Regeln aus dem Live-Verlust

> **Hintergrund (Live-Post-Mortem, 30.08.2026):** Bullischer DCA-Bot auf
> 我踏马来了 (10x) hatte alle 8 Safety-Orders über nur ~1,1 % Tiefe gefüllt;
> ein zweiter BTC-Dump drückte den Coin auf 4,3 % vor Liquidation. Da kein
> Kapital nachschoss, wurde manuell mit **−9,40 USDT** im Tief geschlossen —
> exakt am Doppelboden. Der Kurs drehte sofort (Bestätigung der eigenen
> Chart-Analyse). Der Fehler war **nicht die Markteinschätzung**, sondern das
> Fehlen einer strukturierten Stop-Order und eines zu engen Rasters.

**Daraus abgeleitete, unverletzbare Regeln:**

1. **Hard-Stop-Pflicht:** Jede Position hat eine Stop-Order im Markt.
   Stop-Loss bei DCA/Grid **0,5 % über dem Liquidationspreis** (bzw. knapp
   unter dem strukturellen Support) — niemals manueller Panic-Close im Tief.
2. **Rastertiefen-Guard:** DCA-Grids auf Meme-Perps müssen ≥ 6–10 %
   Gesamt-Tiefe abdecken; vor Bot-Start prüfen, ob die Gesamt-Tiefe >
   Liquidationsabstand ist.
3. **BTC-Makro-Gate:** Schließt BTC auf 15m/1h unter den Key-Support,
   werden alle Alt-DCA-Käufe sofort pausiert (kein Nachkaufen gegen den
   Leader).
4. **Liquidationsdistanz-Monitoring:** < 5 % Abstand zur Liquidation →
   HITL-Eskalation (automatische Optionen: Stop oder Margin-Vorschlag —
   aber Entscheidung zu Gunsten des Stops, wenn kein Puffer existiert).
5. **Post-Trade-Cooldown:** 30 min Pause nach Verlust-Exit (Anti-Revenge).
6. **Kein manuelles Überschreiben von TP/Stops im Schmerzpunkt:**
   Take-Profit bleibt bei 1,5–2 % auf Average Price stehen;
   nach TP1 wird der Stop **automatisiert** auf Fee-Covered Break-Even
   gezogen — das ist Pflicht, keine Option.
   **Fee-Covered Break-Even (Nutzer-Regel, 30.08.2026):** SL nicht auf
   exaktes Entry, sondern `entry × 1,0005` (Long) bzw.
   `entry × 0,9995` (Short). Begründung: bei 30x Hebel kosten
   Roundtrip-Taker-Fees (~0,04 %/Seite auf Notional) ~2,4 % Margin;
   ein Stop auf 0,00 % ist damit ein Netto-Verlust. +0,05 % deckt die
   Gebühren vollständig ab und liegt unter dem 1m-Rauschen.
7. **Volatilitäts-Exhaustion überwachen (Grid-Unwind):** BBW fällt >40 %
   vom Tageshoch, OI fällt bei steigendem Preis, CVD flacht ab →
   asynchrone Glattstellung einleiten (Gewinner-Seite zuerst schließen,
   Verlierer-Seite am VWAP/EMA20-Pullback schließen; das senkt Slippage um
   bis zu ~70 % gegenüber gleichzeitigem Market-Dump).
8. **Automatischer Exit/Kill-Switch — keine menschliche Exit-Latenz
   (Live-Beleg, 30.08.2026):** Pionex-DCA-Bot auf 我踏马来了 (10x Long)
   schloss mit +52,76 % (+7,96 USDT; Arbitrage-PnL +9,97 USDT, Trend-PnL
   −2,01 USDT), der Close bei 0,014351 erfolgte aber erst nach dem Spike-Top
   bei 0,014600 — der manuelle Exit kostete schätzungsweise 15–20 % des
   Peak-PnL. Regel: TP-Trailing (ATR-basiert am FVG/Widerstand) und
   Kill-Switch am oberen Liquidity-Sweep müssen automatisiert feuern;
   der Mensch startet, Sigma beendet.
9. **Kapitalerhalt schlägt Alles:** „Survive to trade another day.“

---

## 9. Geometrie & Elektrotechnik-Analogien (Feature-Spezifikationen)

> Der Nutzer (Elektrotechnik-Hintergrund) hat Marktstrukturen intuitiv als
> elektrische Schwingungssysteme erkannt. Die Analogien sind **metaphorisch**,
> liefern aber sauber implementierbare Features. Regel aus der Diskussion:
> Durch Fixieren des Zeithorizonts T kollabiert die komplexe Schwingungsmathematik
> zu **reeller Algebra** (sin/cos fester Winkel) — operativ rechnen wir in ℝ.

### 9.1 00:00-UTC-Tagesanker & Hüllkurven (intuitiv verifiziert)
- Erste Kerze des Tages (00:00 UTC) = Referenz-Anker für den ganzen Tag
  (Daily-Open-Bias; institutionelle Bots resetten dort VWAP/ATR/Ranges)
- **Volumen-verankerte Hüllkurve:** Durch die 2–3 volumenstärksten Kerzen
  seit 00:00 obere und untere Kanallinie legen (lineare Regression/Strahl);
  die Steigung zeigt den Tages-Drift
- **Outside-Inside-Reversal:** Kerze schließt außerhalb der Hüllkurve,
  Folgekerze schließt wieder darin UND ist grün → Long-Signal
  (Bollinger-Re-Entry; funktioniert besonders in Weekend-Ranges)
- ❌ zu bauen → MP-03

### 9.2 Leistungsdreieck auf Breakout-Kerzen
- **Wirkleistung P = Kerzenkörper** (Close der Breakout-Kerze): echte,
  im Orderbuch verankerte Wertbildung
- **Blindleistung Q = Docht** über/unter dem Körper: Überhitzung,
  Stop-Hunts, Slippage ohne Akzeptanz
- **Scheinleistung S = √(P²+Q²) = absolute Dochtspitze** (Take-Profit-Zone)
- **Leistungsfaktor cos φ = P/S:** ≥ 0,85 = echter Move/Marubozu;
  < 0,30 = Chop/Fakeout
- Regel: TP an die Scheinleistungs-Spitze S; Wiedereinstieg erst nach
  Rekalibrierung auf Körperebene P
- **Zwei Berechnungsarten für cos φ (beide nutzen, verschiedene Horizonte):**
  1. **Kerzen-Effizienz (Bar-Level):** η = |Close−Open| / (High−Low) ∈ [0,1],
     vorzeichenbehaftet `cos_φ_bar = sign(Close−Open)·η`
  2. **Pfad-Effizienz (Window-Level, Kaufman Efficiency Ratio):**
     `cos_φ_path = (Close_t − Close_{t−N}) / Σ|Close_i − Close_{i−1}|` ∈ [−1,1];
     True-Range-Variante: Nenner = Σ TR_i. +1 = monotoner Trend,
     0 = reines Chop (Netto 0 bei langer Wegstrecke)
- **Signal-Schwellen (Hysterese, aus Backtest-Diskussion):**
  Long-Entry bei cos_φ_path ≥ +0,40; Short bei ≤ −0,40;
  Exit/Flat wenn |cos_φ_path| ≤ 0,15 (zurück in Blindleistung/Chop);
  Hysterese verhindert Whip-Saws. Strategie in MP-12 zu backtesten (H6).
- **KNN/ML-Cluster-Tabelle (Price-Action-Physik):**
  | Cluster | P_norm | Q_norm | η | Deutung |
  |---|---|---|---|---|
  | Reiner Trend/Impuls | >0,8 | <0,3 | >0,7 | Marubozu, Trendfolge |
  | Liquidity Trap/Pin Bar | <0,2 | >0,8 | <0,2 | Fakeout/Umkehr (Q_upper vs Q_lower prüfen) |
  | Battleground/Doji | <0,2 | >0,8 | <0,2 | beidseitige Dochte → Kompression vor Breakout |
  | Volatility Climax | >1,2 | >0,8 | ~0,5 | Blow-off/Exhaustion (S_norm > 2,0) |
- ❌ zu bauen → MP-04

### 9.3 Hilbert-Phasor & MTF-Resonanz
- Komplexe Zeigerdarstellung: `Z = I + jQ` (I = Preis/In-Phase,
  Q = Hilbert-Quadratur, 90°-phasenverschoben)
- Komplexe Scheinleistung: `S = U · I*` (konjugiert komplex!),
  Winkeldifferenz `φ = φ_U − φ_I` — **Phasendifferenz ist entscheidend,
  nicht Winkelsumme**
- Multi-Timeframe als Harmonische: 1h = Grundwelle, 15m = 4. Harmonische,
  1m = 60. Harmonische (Epizykel: kleine Zeiger rotieren auf der Spitze
  der großen)
- **Konstruktive Resonanz:** cos(Δφ_HTF,LTF) ≥ 0,75 → gleichphasige
  Überlagerung = starker Move; LTF in Gegenphase während HTF bullisch =
  Dip-Charging-Zustand (Vorbereitung für Limit-Entries)
- ❌ zu bauen → MP-04

### 9.4 Drei-Phasen-Analogie & Sternpunkt
- L1 = Zeit/Takt (Sessions, Kerzenschlüsse), L2 = Volumen/Orderflow,
  L3 = Liquidität/Orderbuchtiefe; 120° Phasenversatz
- Asymmetrie zwischen den Phasen (Move/Sweep/Reversal) ist die Profitquelle
- **Der Trader/das System ist der geerdete Sternpunkt (Neutralleiter):**
  nicht mitschwingen, nach Trade sofort zurück in 100 % Cash (Flat-State)
- Erdung = Disziplin/Stop-Loss/Sofort-Flat nach TTL

### 9.5 Price-Action-Physics-Featurevektor (kanonische Formeln)

Komplettes skaleninvariantes Feature-Set pro Bar aus OHLCV
(alle Nenner mit ε-Schutz; ATR als Wilder-RMA, Periode 14):

| Feature | Formel | Bedeutung |
|---|---|---|
| `S_norm` | `(High−Low)/ATR` | Scheinleistung: Gesamtspanne der Kerze |
| `P_norm` | `|Close−Open|/ATR` | Wirkleistung: Körper-Stärke (Betrag) |
| `P_norm_signed` | `(Close−Open)/ATR` | Wirkleistung mit Richtung |
| `Q_norm` | `((High−Low)−|Close−Open|)/ATR` | Blindleistung: beide Dochte |
| `Q_upper_norm` | `(High−max(Open,Close))/ATR` | obere Rejection (Bär-Absorption) |
| `Q_lower_norm` | `(min(Open,Close)−Low)/ATR` | untere Rejection (Bull-Absorption/Tail) |
| `Q_bias` | `Q_lower−Q_upper` | Rejection-Asymmetrie (+ = Kaufdruck) |
| `eta_efficiency` | `|Close−Open|/(High−Low)` | Kerzen-Wirkungsgrad ∈ [0,1] |

Schwellen: P_norm 0–0,2 Kompression/Doji; 0,5–0,9 gesunder Trend;
>1,2 explosive Expansion (Körper > ATR). S_norm > 2,0 = Climax.
Vektorisierte Berechnung O(N) pro Feature, reelle Zahlen, kein j im
operativen Pfad. → MP-04 (Formeln dorthin als Akzeptanzgrundlage).

### 9.6 Komplexe Zeigerrechnung & MTF-Harmonik (Theorie-Skelett)

- `S = U · I*` (konjugiert komplex); naiv `U·I` addiert Winkel
  (φ_u+φ_i = sinnlos, referenzpunktabhängig); nur die **Winkeldifferenz**
  φ = φ_U − φ_I misst die Energieübertragung (wie Strom/Spannung im Netz)
- Imaginäre Achse j = √−1 ist nur der 90°-Rotationsoperator; mit
  **festem Zeithorizont T** (Polymarket-Expiry, TTL-Fenster) ist
  ω·T ein fester Winkel → e^{jωT} ist Skalar → sämtliche Berechnung
  fällt in reelle Algebra zurück: Wirkanteil = U·cos(φ),
  Blindanteil = U·sin(φ)
- MTF-Harmonik: HTF = Grundwelle, LTF = Oberwelle/Epizykel (Zeiger auf
  Zeiger); Resonanz via `HTF · LTF*` (Konjugatprodukt),
  resonance = cos(Δφ); ≥ 0,75 konstruktiv (Entry-Zustand),
  < −0,5 bei HTF-bullisch/LTF-bärisch = Dip-Charging (Limit-Leiter
  vorbereiten). TTL-Regel: Trade-Fenster = verbleibende HTF-Zeit × 0,75

---

## 10. Blinded / Asset-Agnostic-Prinzip

- Forschungsthese (Glasserman/Lin „Distraction Effect“, BlindTrade-Diskussion):
  Werden Symbol, Timeframe und absoluter Preis ausgeblendet, steigt die
  Qualität der Mustererkennung (keine Narrative/Vorurteile)
- Umsetzung in Sigma:
  - Alle Features normiert, keine Ticker-Namen oder Dollar-Preise in
    Signal-/Modell-Inputs
  - Topologie schlägt Label: Doppelboden ist auf jedem Markt/TF derselbe
  - ONNX-Tensor bekommt nur Skalar-Features in [−1,1]/[0,1]
- Deckt sich mit dem Nutzerprofil: kein Makro/News-Hintergrund, dafür starke
  strukturgeometrische Intuition → das System formalisiert diese Intuition
  und liefert die emotionale Disziplin.

---

## 11. ONNX / KNN-Inferenz (Zielbild)

- 16-Feature-Observation-Tensor (Shape `[1,16]`, float32, skaleninvariant):
  cos φ, P/ATR, Q/ATR, tan(θ)-Slope, 00:00-Kanalposition, EQ-Position,
  FVG-CE50-Touch, Two-Bar-Thrust, Polymarket-Wahrscheinlichkeit,
  Poly-Zieldelta, TTL-normalisiert, UTC-Safe-Flag, RVOL, CVD-Absorption,
  Hurst, Liquidationsdistanz
- **Kern-9-Feature-Formeln (aus Chat-Forschungsdossier, kanonisch):**
  1. `cos_φ` = (Close−Open)/(High−Low+ε), geclipped [−1,1]
  2. `P_norm` = |Close−Open|/ATR₁₄
  3. `Q_norm` = (obere+untere Dochte)/ATR₁₄ (siehe §9.5)
  4. `pos_00` = tanh((Close−Open₀₀:₀₀)/(2·ATR₁₄))
  5. `m_tangent` = arctan((Close−Open₀₀)/Minuten_seit_00) · 2/π
  6. `P_cal` = PlattScale(Polymarket-Rohquote), geclipped [0,1]
  7. `pos_EQ` = (Close−Range_Low)/(Range_High−Range_Low+ε), [0,1]
  8. `d_CE` = tanh((Close−CE50)/ATR₁₄)
  9. `TTL_norm` = Restminuten bis 1h-Bar-Close / 60
  (Features 10–16: RVOL, CVD-Score, Hurst, Liq-Distanz, UTC-Flag,
  Two-Bar-Thrust, FVG-Touch — nach MP-11-Liste)
- **Modellarchitektur (PyTorch → ONNX-Export):**
  Dual-Head: Backbone 2× (Linear(16→64) + LayerNorm + GELU);
  Policy-Head: Linear(64→3) + Softmax → [P(Long), P(Flat), P(Short)];
  Leverage-Head: Linear(64→1) + Sigmoid → Hebel = 10 + 15·σ ∈ [10x,25x].
  opset 14, dynamische Batch-Achse, Input-Name `tensor_x`,
  Outputs `action_probs`, `leverage_factor`.
- Inferenz < 1–2 ms p99 (onnxruntime, CPU, intra-op 1–2 Threads,
  ORT_ENABLE_ALL); **deterministische Fallback-Policy ohne Modell:**
  TTL_norm < 0,15 oder UTC unsicher → FLAT; P_cal ≥ 0,65 und
  (cos_φ ≥ 0,75 oder Discount/Kauf-Tail) → LONG (symm. SHORT);
  sonst FLAT/DCA-scaled.
- **Bar-Level Execution Lock:** höchstens eine Aktion pro
  Bar-Zeitstempel (Duplikats-Sperre im Inferenz-Wrapper).
- **Zwei-Stufen-Architektur (Nutzer-Korrektur):** Das ONNX/der Orchestrator
  klassifiziert nur BTC-Makro-Regime + Long/Flat/Short — **keine
  Symbolauswahl im Tensor**. Welcher Alt gehandelt wird, entscheidet der
  High-Beta-Ranker (MP-05) in Stufe 2; erst dann wird die Strategie
  gedroppt (MP-09).
- Unsichere Verteilung (Entropie > 0,65) oder TTL < 10 min → Zwangs-Flat
- ❌ zu bauen → MP-11 (Tensor-Formeln sind damit Abnahmegrundlage;
  Modell-Training selbst ist nicht Teil der Roadmap, Fallback-Policy
  produktiv)

---

## 12. Dynamic Pine Provisioning (Headless TradingView)

- Wenn der Scout einen Coin mit Top-Score findet, **generiert der Orchestrator
  pro Symbol ein maßgeschneidertes Pine-v6-Script** mit injizierten Konstanten
  (Entry/TP/SL/Leverage/strategy_id/Webhook-Secret)
- TradingView hostet das Skript 24/7 headless; bei Trigger Webhook an
  Sigmas `/api/v1/signal/ingest` (Schema A: `action` BUY/SELL/CLOSE
  GROSS geschrieben, `ticker`, `price`, `stop_loss`, `fixed_leverage`,
  `secret`)
- Nach Move/TTL: Strategie de-provisionieren (ephemere Agenten)
- Backtest + Live nutzen identischen Code (Loop B testet, Loop A führt aus)
- **Schema-Erweiterung für fraktale Einzeltrades (MP-15):** Entry-Payload
  trägt gestaffelte TPs mit:
  `action` `open_long`/`open_short`, `ticker`, `price` (Close der
  geschlossenen Bar), `fixed_leverage`, `initial_sl`,
  `tp1..tp3` je `{price, qty_pct}` (40/30/20), `runner_qty_pct` (10),
  `fee_covered_be_offset=0.0005`, `strategy_id`, `secret`.
  Nach TP1 folgt `update_sl` mit `new_sl = entry×(1±0,0005)` und
  `reason: TP1_HIT_FEE_COVERED_BREAKEVEN`; Exit = CLOSE.
- 🟡 `pine_v6_generator.py` + `app/tv/alert_provisioner.py` existieren;
  der dynamische Provisionierer fehlt → MP-09
- **Achtung:** Der im Chat 2 von Gemini erzeugte Pine-v5-Entwurf ist
  **nicht kanonisch** (falsche alert-Frequenz, intrabar repaint-gefährdet,
  `strategy.entry` statt Webhook-only, Python-Header in .pine). MP-09
  erzeugt v6 mit Bar-Close-Alerts (`barstate.isconfirmed`/[1]-Offset,
  `lookahead_off`) — Entwurf nur als Payload-Feldreferenz nutzen.

---

## 13. Orderflow-Validator (L2/Footprint) — Zielbild

- **Stacked Diagonal Imbalances:** ≥ 3 aufeinanderfolgende Preislevel mit
  Ask/Bid ≥ 3:1 (300 %)
- **CVD-Absorption:** Preis macht tieferes Tief, Delta dreht positiv
  (passive Käufer schlucken den Verkaufsdruck)
- **POC/HVN-Konfluenz:** Entry-Zone am Point of Control / High Volume Node
- **Iceberg-Erkennung:** sichtbare Ordergröße wird wiederholt erneuert
- Delta-Verlauf über mehrere Kerzen wichtiger als Einzelkerzen
- Datenbedarf: Tick/AggTrades + Orderbuch-Snapshots; ohne Feed fail-closed
- ❌ zu bauen → MP-10

---

## 14. Evidenz-Klassifizierung & Test-Hypothesen

**Stark belegt:** FMH/Selbstähnlichkeit (Peters, Mandelbrot),
Volatilitäts-Skalierungsgesetze (Dacorogna/Müller/Olsen),
Trend-auf-Stunden-bis-Jahre / Reversion-darunter (Safari & Schmidhuber),
TSMOM, OFI-Linearität (Cont et al. 2014), Square-Root-Impact (Sato et al.
2024), Krypto-Session-Rhythmen (Wang et al. 2020, Amberdata 2025),
Look-ahead-Methodik, Mikrostruktur-Rauschen (NSR 36,6 %),
Bank-of-England FMH-Stabilität.

**Moderat:** 4–6x-Regel (Praxis-Konvergenz), Wochenend-Effekt,
Krypto-Hurst-Persistenz.

**Schwach:** SMC/ICT-Konzepte als solche (Wyckoff/Raschke-Rebranding),
12–15x-Regel, Killzone-Trefferquoten.

**Test-Hypothesen für eigene Backtests:**
- H1: bias-aligned FVG-Trades schlagen Counter-Trend-FVGs signifikant
- H2: Overlap-/Killzone-FVGs füllen häufiger (UTC statt EST bei Krypto)
- H3: 4–6x-Stacks sind out-of-sample stabiler als beliebige Paare
  (Faktor-Sweep 2x–30x, Walk-Forward)
- H4: Weekend-Momentum-Longs in Alts haben positives, aber
  kapazitätsbeschränktes Alpha
- H5: Regime-Gate über rollierende Hurst-/MFDFA-Breite reduziert Drawdowns
  in Fragilitätsphasen

**Backtest-Disziplin:** Parameter-Budget deckseln, runde ökonomisch
motivierte Werte, Sensitivitätskarte, unberührtes Out-of-Sample-Fenster,
Walk-Forward, Look-ahead-Pipeline-Test („break the pipeline on purpose“),
chronologische Splits, eine TF-Kombination mind. 30–50 Trades halten.

**Datenanforderungen:** OHLCV in ≥3 gekoppelten Auflösungen (Krypto ab M5),
ms-Zeitstempel, lückenfreie Kerzen; für OFI: Tick/AggTrades +
Orderbuch-Snapshots (Tiefe als Impact-Normalisierer).

**Offene Fragen:** Keine direkte Peer-Review-Studie zu 4–6x/12–15x;
instrumentenspezifische optimale Faktoren (Faktor-Sweep nötig);
Perp-Liquidationskaskaden als FVG-Verstärker unerforscht.

---

## 15. Multi-Asset-Erweiterung (optional/Zukunft)

- Wenn Krypto-Chop: Kapital rotiert zu Gold/Silber/Forex (kommunizierende
  Röhren); Silber = High-Beta zu Gold (wie Alt zu BTC)
- Gleiche Lead-Lag-DNA: XAU-Lead-Support → XAG-Scale-In
- Marktzeiten-Kalender nötig (Forex 24/5, Metalle mit Session-Gaps);
  Krypto füllt Wochenenden ab
- 🟡 Router-Placeholder existiert; Venue-Bridges (MT5/IB) sind optional

---

## 16. Glossar (Schnellreferenz)

| Begriff | Bedeutung |
|---|---|
| CE50 | Consequent Encroachment = 50-%-Mitte einer FVG |
| FVG | Fair Value Gap, 3-Kerzen-Imbalance |
| Dealing Range | Handelsspanne zwischen Displacement-Origin und Peak |
| EQ / pos_t | Equilibrium (50 %) / normalisierte Range-Position |
| Discount/Premium | pos_t < 0,5 (Long-Zone) / > 0,5 (Short-Zone) |
| SSL/BSL | Sell-/Buy-Side Liquidity (Stops unter Tiefs / über Hochs) |
| Sweep | Liquiditätsabgriff (Stop-Run mit Rejection) |
| OFI | Order Flow Imbalance (Cont et al.) |
| CVD | Cumulative Volume Delta (Kauf- vs. Verkaufs-Aggression) |
| RVOL | Relatives Volumen vs. Durchschnitt |
| TTL | Time-to-Live, maximale Strategie-Lebensdauer |
| HITL | Human-in-the-Loop-Eskalation |
| Schema A | Webhook-Payload-Format (BUY/SELL/CLOSE, secret, …) |
| Loop A–E | Execution / Tester / Feed / Scout / Allokations-Gate |
| cos φ | Leistungsfaktor: echter Trend vs. Chop |
| Unwind | Geordnete Glattstellung aller Positionen |

---

## 17. Forschungs-/Backtest-Werkzeuge (Zielbild)

- **VectorBT als primäre Backtest-Engine** (vektorisiert, schnell für
  Faktor-Sweeps); TV-CSV-Exporte aus Loop B als Datenbasis;
  GA-Optimizer (`app/optimizer/`) bleibt Parameter-Such-Schicht.
- **Lightweight-Charts-Dashboard (Forschung/Validierung):** eigenständige
  HTML/JS-App (CDN `lightweight-charts`, kein Build) mit drei synchronisierten
  Panes: Candles + Entry-/Exit-Marker (grün Long/rot Short/grau Chop-Exit),
  cos φ-Subpane mit Schwellenlinien (±0,40 Entry, ±0,15 Exit), Equity-Curve
  vs. Benchmark. Python-Export der Kerzen-/Indikator-/Marker-Serien
  (UNIX-Sekunden, aufsteigend). Dient der visuellen Hypothesenprüfung
  (H1–H6), nicht dem Live-Trading. → MP-16.
- **H6 (neue Hypothese, Nutzer-These 30.08.2026):** Wochenend-Breakouts
  überwiegen als Fakeout (dünnes Volumen, illusorische Tiefe,
  Ask-Bias Sonntag); der nachhaltige Move kommt erst nach dem
  Montags-Sweep (Monday-Momentum 10:00 UTC). Backtest: Long-Breakout-Signale
  Samstag/Sonntag vs. Montag–Freitag, mit Slippage-Szenarien
  (+0,1/+0,3/+0,6 %) — Deckung mit H4 (Weekend-Sizing-Regel) und der
  SessionClock-Wochenend-Reduktion.
- **cos-φ-Strategie-Praxisparameter (für MP-16-Backtest):** Window N = 20
  (1h-Bars); Entry |cos φ_path| ≥ 0,40 mit Hysterese; Exit bei |cos φ| ≤ 0,15;
  1-Bar-Lag-Ausführung (Signal T → Ausführung T+1); Taker-Fee/Slippage
  0,06 % pro Roundtrip; Metriken Return, Max-DD, Sharpe, Win-Rate,
  Profit-Faktor, Trade-Zahl. ACHTUNG: Schwelle/N = Faktor-Sweep-Kandidaten
  (kein „Magic-Threshold“ ohne Sweep, vgl. §15-Methodik/Red-Flags).

> **Hinweis zu Chat-Code:** Die im Chat 3 von Gemini entworfenen Module
> (`quantum_power_orchestration.py`, `high_beta_symbol_screener.py`,
> `onnx_quantum_tensor_pipeline.py` mit 9D-Tensor, `power_factor_backtest.py`,
> HTML-Dashboard) sind **Konzeptreferenzen**, nicht kanonisch: teils
> syntaktisch defekt (z. B. `tensor =` statt `tensor[0,k]`-Indizierung,
> `/* */`-Kommentare in .py), doppeln bestehende sigma/-Module
> (orchestrator, correlation_scout) und umgehen das Zwei-Stufen-Prinzip.
> Die Formeln sind übernommen (§9.5/§11), die Implementierung folgt den
> Master-Prompts MP-04/MP-05/MP-11/MP-16 auf den echten Modulstand.
