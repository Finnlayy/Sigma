# Projekt:Sigma — UI-Spezifikation der neuen Funktionen (Frontend-Analyse)

> **Zweck:** Analyse aller durch die Roadmap (MP-01 … MP-16) neu
> hinzukommenden Funktionen und ihrer Darstellung im bestehenden
> Sigma-Terminal-Frontend. Stil, Komponenten und Interaktionsmuster
> richten sich **ausschließlich** nach dem vorhandenen Frontend
> (`src/components/sigma/`, Dock-Registry, shadcn/ui, Tailwind v4,
> Liteweight-Charts). Diese Datei ist die Vertragsgrundlage für
> `docs/prompts/MP-17-frontend-panels.md`.

---

## 1. Bestehende Frontend-Konventionen (unveränderlich)

- **Architektur:** `SigmaTerminal` (`src/components/SigmaTerminal.tsx`)
  mit Dock-Baum (`src/components/sigma/dock.tsx`): Resizable-PanelGroups +
  Tabs; Panels werden über `PANEL_REGISTRY` / `PANEL_TITLES`
  (`src/components/sigma/panels.tsx`) registriert; Presets sind
  vordefinierte Dock-Layouts, Layout in `localStorage`.
- **Panel-Bausteine:** `PanelShell` (Titel + lucide-Icon + Actions,
  11px uppercase Header, ScrollArea, Text xs), `Stat`-Kacheln
  (Zinc-800-Border, JetBrains-Mono), `IconBtn`, `FeedBadge`
  (LIVE :8001 / STALE CACHE / SYNTHETIC), `usePoll(fn, ms)`.
- **Design:** Dark Terminal (zinc-950/zinc-900), Emerald-Akzent,
  amber=Warnung, rot=Gefahr; `terminal-glow`; Fonts Inter (Sans) +
  JetBrains Mono; Kennzahlen immer mono.
- **Charts:** `TvLightweightChart` (lightweight-charts v5) für Kerzen;
  Recharts für kleine Meter/Historien; drei synchronisierte Panes im
  Research-Dashboard (MP-16).
- **Daten:** Polling gegen `/api/v1/...` über `sigmaApi`
  (`src/lib/sigmaApi.ts`); **Fail-Closed auch im UI:** leere States statt
  Fake-Daten, FeedBadge zeigt die Datenherkunft; keine synthetischen Werte
  als Live tarnen.
- **Schreibzugriffe:** gefährliche Aktionen (Deploy, Kill, Parameter ändern)
  nur per POST mit Operator-Token + Bestätigungs-Modal
  (Muster `StrategyQueueConfirmModal`, `deadmanBeat`/`operatorPost`).
- **Konfiguration:** veränderliche Parameter landen im Settings-Panel
  (`SettingsPanel`/`SettingsPage`) mit Defaults aus der Wissensdatenbank;
  kein Hardcoding im Panel.

**Neue Panels werden** in `panels.tsx` registriert, in `sigmaApi.ts` um
typisierte Endpunkte erweitert und über neue Presets andockbar.
Bestehende Panels werden **erweitert, nicht dupliziert**
(MarketChart, ExecutionRiskPanel, BacktestPanel, PineStudio/TvJobs,
SettingsPanel).

---

## 2. Funktions- → Panel-Mapping (Übersicht)

| Roadmap-Funktion | Daten / Endpoint (Vorschlag) | UI-Heimat |
|---|---|---|
| MP-01 Risk Guards (SL-Distanz, Grid-Tiefe, BTC-Makro, Liq-Nähe, Cooldown, Fee-BE) | `GET /api/v1/sigma/risk` | **RiskGuardPanel** (neu) + Erweiterung ExecutionRiskPanel |
| MP-02 Micro-DCA-Ladder | `GET /api/v1/sigma/ladder/preview` | **LadderArchitectPanel** (neu) |
| MP-03 Thrust / Marubozu-FVG / 00:00-Envelope | `GET /api/v1/sigma/zones` | **MarketGeometryPanel** (neu) + Overlays im MarketChart |
| MP-04 Price-Action-Physics (S/P/Q, η, cos φ, Phasor/Resonanz) | `GET /api/v1/sigma/power` | **PowerPhysicsPanel** (neu) + cos-φ-Subpane im Chart |
| MP-05 Hourly Gate + High-Beta-Ranker | `GET /api/v1/sigma/scout`, `POST .../scan` | **SymbolScoutPanel** (neu) |
| MP-06 Polymarket Dichte/Trajektorie | `GET /api/v1/sigma/polymarket` | **PolymarketPanel** (neu) |
| MP-07 Sniper-Pipeline (15m→1m, TTL-Phasen) | `GET /api/v1/sigma/regime` | **QuantumRegimePanel** + Sniper-Statusstreifen |
| MP-08 Exhaustion + Async-Unwind | `GET /api/v1/sigma/exhaustion` | **UnwindPanel** (neu) |
| MP-09 Dynamischer Pine-Provisionierer + Auto-Härtung fremder Skripte | `GET/POST /api/v1/sigma/provisions`, `POST .../provisions/harden` | **ProvisionerPanel** (neu, bei TvJobs/PineStudio angedockt) |
| MP-15 Fraktaler Einzeltrade (40/30/20/10, Fee-BE, Kill-Switch) | `GET /api/v1/sigma/fractal/preview` | **FractalTradePanel** (neu) |
| MP-11 ONNX-Tensor/Inferenz | `GET /api/v1/sigma/onnx` | **OnnxBrainPanel** (neu; NetronVisualizer bleibt Modellgraph) |
| MP-12/MP-16 Backtest H1–H7 + Dashboard | `GET/POST /api/v1/research/...` | **ResearchLabPanel** (neu; erweitert BacktestPanel) |
| MP-10 Orderflow (optional) | `GET /api/v1/sigma/orderflow` | OrderflowPanel (später, fail-closed leer) |
| MP-13/14 Multi-Asset/Straddle (optional) | — | Tabs in Scout-/Fractal-Panels, keine Extra-Panels jetzt |

---

## 3. Panel-Spezifikationen

### 3.1 QuantumRegimePanel — „Mission Control“ (MP-05/07/06/11)
- **Inhalt (von oben nach unten):**
  1. **Hourly-Cycle-Band:** breiter Phasenbalken mit den vier Minuten-Phasen
     der laufenden 1h-BTC-Kerze: SCAN&DEPLOY (00–05, emerald) /
     ACTIVE EXECUTION (05–48) / PRE-CLOSE UNWIND (48–55, amber) /
     IDLE (55–60, zinc); Marker für aktuelle Minute + Countdown;
     „last scan @ <bar-ts>“ (Idempotenz sichtbar).
  2. **Wave-Collapse-Status:** Badge IDLE / COLLAPSED_INTO_ZONE (emerald) /
     INVALIDATED (rot) / HTF_OPEN (zinc); daneben Range High/Low, EQ,
     CE50 als Mono-Werte.
  3. **SessionClock-Zeile:** aktuelles UTC-Fenster (EU 08–09 / US 14–16
     emerald; 21:00–22:00 QUARANTÄNE rot; Weekend amber „reduced size“).
  4. **Throttle:** SLEEP (0 Bots) / NORMAL (3) / AGGRESSIVE (8) mit
     ATR-Ratio; Dual-Hurst H-Wert.
  5. **Polymarket-Bias:** kompakter Badge (STRONG_BULLISH … CHOP) +
     kalibrierte Wahrscheinlichkeit vs. Gate 0,60–0,65.
  6. **ONNX-Kopfzeile:** LONG/FLAT/SHORT-Wahrscheinlichkeiten als Mini-Balken
     + Hebel-Head-Wert + „fallback policy“-Badge wenn kein Modell;
     FLAT-Grund (21:00 / TTL) als Klartext.
- **Interaktion:** keine Schreibzugriffe; Klick auf eine Zeile öffnet das
  zugehörige Fachpanel (Scout/Wave/ONNX) im aktiven Tabset (bestehendes
  `addPanelToActive`-Muster).
- **Leerzustand:** vor erstem Tick „waiting for closed 1h bar …“,
  FeedBadge sichtbar.

### 3.2 MarketGeometryPanel — Zonen & Tagesanker (MP-03)
- **Zonentabelle** (pro Symbol/Intervall, umschaltbar 15m/1h):
  Dealing Range High/Low/EQ, aktive FVGs mit Zone (low–high), CE50,
  Alter in Bars, ATR-Größe, Bias-Ausrichtung (aligned/counter),
  Touch-Status. Discount/Premium als Farbbadge der `pos_t`-Spalte
  (<0,5 emerald Long-Zone, >0,5 amber Short-Zone).
- **00:00-Envelope:** obere/untere Kanal-Linie (heutige Steigung
  steigend/fallend als Pfeil), Outside-Inside-Reversal-Marker
  (Badge + Bar-Zeit).
- **Signale der Stunde:** Two-Bar-Thrust / Marubozu als chronologische
  Event-Liste mit Bar-Zeit und Kontext-Flags (Support/EMA/Sweep — als
  separate Häkchen, nie als harte Bedingung getarnt).
- **Chart-Kopplung:** Buttons „Zonen im Chart einblenden“ → Overlays im
  MarketChart (FVG-Boxen, CE50-Linie, EQ, Envelope-Linien) via
  lightweight-charts PriceLines/Box-Serie; Kerzen-Marker für Thrust/
  Outside-Inside.
- **Leerzustand:** „keine geschlossenen Bars / keine Zone“ statt leerer
  Tabelle; niemals gezeichnete Zonen ohne Feed.

### 3.3 PowerPhysicsPanel — Wirkungsgrad-Messplatz (MP-04)
- **Oben:** großer cos-φ-Meter (−1 … +1, Halbbogen oder Recharts-Gauge):
  Zeiger = `cos_phi_bar` der letzten geschlossenen Kerze; Zonen farbig
  (|φ|≥0,85 fester Move emerald, <0,30 Chop/Rejection rot);
  Cluster-Badge (SOLID_TREND / WICK_REJECTION / EXPLOSIVE / CLIMAX /
  BATTLEGROUND_DOJI nach §9.5-Schwellen).
- **Mitte:** Drei Mono-Balken je letzte Kerze: S_norm (Spanne/ATR),
  P_norm (Körper/ATR, signed), Q_norm (Dochte/ATR) mit Q_upper/Q_lower-
  Split und Q_bias (+ = Kauf-Tail).
- **Unten:** cos-φ-Pfad-Serie (Efficiency Ratio, Window N einstellbar
  10/14/20/30) als kleine Linie mit Schwellenlinien ±0,40 (Entry) und
  ±0,15 (Exit) — dieselben Linien wie im MP-16-Dashboard.
- **MTF-Resonanz:** HTF/LTF-Phasor-Meter: resonance = cos(Δφ) mit
  Badge CONSTRUCTIVE ≥0,75 (emerald) / DIP_CHARGING < −0,5 (amber,
  „Limit-Leiter vorbereiten“) / neutral.
- **Interaktion:** rein lesend; Parameter N über Select; Erklärungstooltip
  pro Größe (Formula-Hint, z. B. „P_norm = |Close−Open|/ATR“).

### 3.4 SymbolScoutPanel — Stufe-2-Ranker (MP-05)
- **Gate-Zeile:** letztes Screening (Bar-Zeitstempel), Scan-Button
  (operator-bestätigt, nur in Phase SCAN&DEPLOY aktiv; sonst disabled mit
  Phasenhinweis), „1 Scan pro geschlossener 1h-Bar“ als Badge.
- **Ranking-Tabelle:** Symbol (Blinded-Modus-Umschalter: Ticker ausblendbar
  → nur `ASSET_###`, Blinded-Prinzip §10), β, r, RVOL, Spread %,
  Score (Farbskala m8Color), Empfehlung (`sniper_hedge 25x` emerald /
  `dca 5–10x` zinc), Blacklist-Grund als Tooltip (thin_book/unlock/…).
- **Aktion pro Zeile:** „Provisionieren“ öffnet Fractal-/Ladder-Panel
  vorbefüllt mit Symbol + empfohlener Strategie; Bestätigungs-Modal
  (Queue-Confirm-Muster). Kein Auto-Deploy aus der Tabelle heraus.
- **Leerzustand:** „Universe wird gescannt …“ / „kein Coin erfüllt die
  Hard-Filter“ mit den Filter-Schwellen (r≥0,75, β≥1,5, RVOL≥1,5).

### 3.5 PolymarketPanel — Layer 0 (MP-06)
- **Dichte-Histogramm** (Recharts-Barbins): implizite Bin-Wahrscheinlichkeiten
  zwischen den Strikes; μ (Erwartungswert) als senkrechte Markerlinie;
  wahrscheinlichster Korridor hervorgehoben.
- **Term-Struktur-Kurve:** μ(T) für T+1h/T+2h/T+4h/EOD als Linie mit
  Δμ/ΔT-Steigung; Bias-Badge (STRONG_BULLISH … STRONG_BEARISH);
  optimales Entry-Fenster `T×0,75` als schraffierten Bereich;
  Spätfenster (<0,25 T) als rot „kein Entry mehr“.
- **Kalibrierung:** Platt-Parameter + Brier-Score-Anzeige;
  Gate-Vergleich P_cal vs. 0,60–0,65 (Schalter grün/rot).
- **Fail-Closed:** ohne Port/Feed nur grauer Kasten „Polymarket feed
  unavailable — gate inaktiv“, niemals geratene Wahrscheinlichkeiten.

### 3.6 LadderArchitectPanel — DCA-Leiter-Werkbank (MP-02 + MP-01)
- **Parameter-Form (linke Spalte):** Entry-Preis, Side, Sprossen 5–8,
  Step % (Default 0,2), Step-Wachstum, Volumen-Faktor (Default 1,15),
  Basis-Margin; Umschalter **statisch ↔ dynamisch (2h-Range ×0,618)**;
  TP % (1,5–2,0), TTL (Default 2 h).
- **Leiter-Visualisierung (rechte Spalte):** horizontale Sprossen-Linie
  mit Preis je Stufe, Margin-Balken je Sprosse, kumulierte Tiefe %;
  avg-fill-Preis nach Vollfüllung, TP-Preis-Marker.
- **Guard-Leiste (MP-01, Echtzeitprüfung der Preview):**
  Gesamt-Tiefe ≥ 6 % für Meme-Perps (grün/rot), erster Step ≥
  Spread+Fee-Floor, Liq-Distanz vs. Tiefe, Hard-Stop-Preis
  (0,5 % über Liq) — jeder Guard mit Häkchen/Kreuz; bei rotem Guard
  ist der „Deploy“-Button deaktiviert + Begründung.
- **TTL-Timer:** Countdown ab Deploy; Ablauf → Hinweis „FLAT-Intent wird
  vorbereitet“ (Anzeige, kein Auto-Klick).

### 3.7 FractalTradePanel — Fraktaler Einzeltrade (MP-15 + MP-01)
- **Trade-Karte:** Side, Leverage (10/25 nach Empfehlung, nicht frei über
  Empfehlung hinaus), Entry;
  **TP-Staffel als vertikale Leiter:** TP1 40 % @ +1,0 % / TP2 30 % @ +2,0 % /
  TP3 20 % @ +3,5 % / Runner 10 % mit ATR-Trail — jede Stufe mit Preis,
  qty %, Status (offen/gefüllt); nach TP1-Fill wechselt der SL-Marker auf
  **Fee-Covered Break-Even** (entry×1,0005 long / ×0,9995 short) mit
  explizitem Badge „+0,05 % fee-covered, Pflicht-Auto-Move“.
- **Initial-SL:** 0,6 % vs. MP-01-Liq-Puffer — der strengere wird
  automatisch gewählt und als Wert angezeigt.
- **Kill-Switch-Zeile:** Exhaustion-Flag (MP-08), Sweep-der-Zielliqui-
  Flag, Minuten-Position (≥55 → FLAT); bei Auslösung roter Banner
  „auto-exit recommended — system beendet, Mensch startet“.
- **Interaktion:** „Plan berechnen“ (Preview, read-only), danach
  „Provisionieren“ mit Bestätigungs-Modal; Slider nur für die
  freigegebenen Parameter (TP % im research-kalibrierten Bereich),
  Hebel nie über Ranker-Empfehlung.

### 3.8 ProvisionerPanel — Ephemere Pine-Agenten (MP-09)
- **Tabelle provisionierter Strategien:** strategy_id, Symbol, Side,
  TTL/Countdown, Status (provisioned/triggered/tp-hit/de-provisioned),
  Webhook-Empfänger.
- **Aktionen:** Code-Vorschau (generiertes Pine v6, read-only Dialog),
  Payload-Vorschau (Schema A / Fraktal-Payload mit tp1–3 +
  fee_covered_be_offset), „De-provisionieren“ (operator-bestätigt).
- **Fremd-Pine-Härtung (Auto-Harden):** Button „Externes Pine härten“
  öffnet ein Dialog mit Code-Textarea (Einfügen von Gemini-/manuellem
  Skript) + Symbol/Parameterauswahl. Backend (`harden_pine_code`,
  MP-09) liefert `code` + `transformations` + `hardening_ok` +
  `reasons`. Der Dialog zeigt:
  - Vorher/Nachher-Diff oder Transformations-Liste
    (z. B. „v5→v6 portiert“, „Webhook-Payload an 3 strategy.entry
    injiziert“, „Fremd-Webhook entfernt“, „barstate.isconfirmed
    ergänzt“, „pyramiding=0 gesetzt“) als Häkchenliste,
  - bei `hardening_ok=false`: roter Block mit den Ablehnungsgründen,
    Deploy-Button deaktiviert (fail-closed),
  - bei Erfolg: nur Pfad „Provisionieren“ über Bestätigungs-Modal
    (Scout-Symbol + kraken_paper + TTL sichtbar) — kein Direkt-Upload.
- **Wächter-Anzeige:** statische Prüfergebnisse des Generators
  (lookahead_off vorhanden, bar-close-Alert, Schema-A-Payload,
  pyramiding=0, calc_on_every_tick=false) als Häkchenliste — dieselben
  Checks für eigen-generierte und gehärtete Skripte.
- Dock-Andockung bei TvJobs/PineStudio (neuer Tab, kein Ersatz).

### 3.9 OnnxBrainPanel — KNN-Tensor-Inspektor (MP-11)
- **Tensor-16:** horizontale 16 Balken im Wertebereich [−1,1]/[0,1],
  jeder mit Tooltip (Name + Formel, z. B. `pos_EQ = (C−R_low)/(R_high−R_low)`);
  fehlende Quellen = 0 mit „fail-closed default“-Hinweis.
- **Decision:** drei Wahrscheinlichkeitsbalken LONG/FLAT/SHORT,
  Hebel-Head (10–25x), Entropie-Wert (>0,65 → FLAT-Warnung),
  Inferenz-Latenz ms (p99 < 2 ms), Bar-Lock-Status
  (EXECUTED / BLOCKED_BY_BAR_LOCK).
- **Modellstatus:** „ONNX model live“ vs. „deterministic fallback policy“
  als Badge; Modellgraph öffnet NetronVisualizerPanel (bereits vorhanden).
- **Zwei-Stufen-Hinweis:** sichtbarer Text „Tensor klassifiziert nur das
  BTC-Makro — Symbolwahl erfolgt im Scout (Stufe 2)“.

### 3.10 RiskGuardPanel — Schutzschicht (MP-01, erweitert ExecutionRisk)
- Je aktiver Position/Bot: Hard-Stop-Distanz vs. Liq-Preis (Soll:
  Stop 0,5 % über Liq), Liq-Abstand % (<5 % → roter HITL-Banner mit
  Optionen Stop/Margin — Entscheidung Stopp bevorzugt), Cooldown-Timer
  nach Exit (30 min), Fee-BE-Status (ja/nein nach TP1),
  BTC-Makro-Gate (offen/geschlossen mit letztem Supportbruch-Event).
- **Globale Regel-Badges (nicht abschaltbar):** „Hard-Stop-Pflicht aktiv“,
  „keine manuellen Panic-Exits — System beendet“, „Grid-Tiefe ≥ 6 %
  erzwungen“. Diese Regeln sind im UI nur sichtbar, nicht togglebar.

### 3.11 UnwindPanel — Exhaustion & geordnetes Glattstellen (MP-08)
- Exhaustion-Score-Gauge (BBW-Einbruch %, OI-Divergenz, CVD-Flach) mit
  Datenverfügbarkeits-Häkchen (ohne OI/CVD nur BBW-Anteil).
- Bei aktivem Hedge-Grid: Unwind-Sequenz-Liste mit Status:
  ① Gewinner 100 % schließen → ② Pullback VWAP/EMA20 warten
  (Countdown/Max-Wartezeit) → ③ Verlierer schließen;
  Net-PnL-Guard: Verlust >50 % des realisierten Gewinns →
  „forced close“-Kennzeichnung.

### 3.12 ResearchLabPanel — Hypothesen & Dashboard (MP-12/16)
- **Hypothesen-Liste H1–H7:** Status confirmed/open/rejected,
  Effektgröße, Stichprobe (Trades), Slippage-Szenario;
  „Run“-Button je Hypothese (operator, async-Job wie TV-Jobs).
- **cos-φ-Strategie-View:** eingebetteter Liteweight-Chart mit 3 Panes
  (Kerzen+Marker / cos φ mit ±0,40/±0,15-Linien / Equity vs. Benchmark),
  Parameter N per Select, Hysterese sichtbar; „HTML-Dashboard exportieren“
  (MP-16-Datei) als Download/öffnen-Link.
- **Sweep-Tabelle (H3/H7):** Hebel-/Window-Sweeps mit Walk-Forward-
  Metriken (Return, Max-DD, Sharpe, Liq-Häufigkeit) als Tabelle;
  Overfitting-Red-Flags (Sharpe >3, DD <5 %) als Warn-Badge.

### 3.13 Settings-Ergänzungen
Neue, im UI verstellbare Parametergruppen (Defaults aus Wissensdatenbank):
Screening-Phasen/Minuten, Ranker-Schwellen (r/β/RVOL/Spread-Cap),
Polymarket-Gate (0,60–0,65) + Platt-Kalibrierung, Throttle-ATR-Bänder,
Ladder-Defaults (Step/Vol/Tiefe/TTL), Fraktal-TP-Verteilungen &
Fee-Offset 0,0005, Cooldown 30 min, ONNX-Fallback-Toggles,
Blinded-Modus (Ticker ausblenden), Weekend/Paper-Umschalter.
Sicherheitsregeln aus MP-01 sind **nicht** abschaltbar.

---

## 4. Neue Dock-Presets

| Preset | Inhalt (Tabsets) |
|---|---|
| `QUANTUM_OPS` | QuantumRegime + MarketChart(Geometry-Overlays) ∥ PowerPhysics + SymbolScout ∥ Polymarket + OnnxBrain |
| `POSITION_DESK` | LadderArchitect + FractalTrade ∥ MarketChart ∥ RiskGuard + Unwind + Provisioner |
| `RESEARCH_LAB` | ResearchLab (3-Pane-Chart) ∥ PowerPhysics + Hypothesen ∥ Backtest/Genetic (bestand) |

---

## 5. Sicherheits- & Interaktionsregeln im UI

1. **Lesen ist Polling, Schreiben ist Modal + Operator-Token**
   (Deploy/De-provision/Scan-Trigger/Parameter-Deploy).
2. **Fail-Closed sichtbar machen:** jeder Datenfeed zeigt Quelle
   (FeedBadge); leere/fehlende Daten → erklärender Leerzustand, niemals
   geratene Werte; Polymarket/Orderflow ohne Feed = inaktives Gate.
3. **Das System beendet, der Mensch startet:** Kill-Switch-/Exit-Empfehlungen
   werden als Banner gezeigt, manuelle Panic-Aktionen gibt es im UI nicht
   (kein „Sofort schließen“ außerhalb der bestehenden Safety-Kill-Mechanik).
4. **Blinded-Umschalter** für Ranker/Tensor (Ticker ausblendbar) —
   das Asset-Agnostic-Prinzip auch im Frontend erfahrbar.
5. **Alle Zahlen mono, alle Zeiten UTC**, Phasen/Countdowns laufen auf
   geschlossenen Bars (kein Intrabar-Flackern bei Regime-Anzeigen).
6. Keine neuen Abhängigkeiten: nur vorhandene Libraries
   (shadcn/radix, lucide, recharts, lightweight-charts, motion).
7. Panel-Komponenten bleiben dumm und klein: Datentransformation liegt
   im Backend; das Frontend rendert nur die `/api/v1/sigma/*`-Antworten.
