# Master-Prompt MP-17: Sigma-Frontend-Panels (stilgetreues Terminal-UI)

> Reichweite: Frontend (`src/`) + die dafür nötigen, dünnen
> Backend-Read-Endpunkte (`app/server/routes_sigma.py`).
> Vertragsgrundlage: **`docs/SIGMA-UI-SPEZIFIKATION.md`** (vollständig lesen)
> plus die dort referenzierten Wissensdatenbank-Kapitel. Keine Eigenmächtigkeiten
> an Konventionen: das vorhandene Frontend ist der Stilmaßstab.

---

## 0. Persona

Du bist ein Senior-React/TypeScript-Frontend-Ingenieur mit Erfahrung in
Trading-Terminalkonsolen. Du arbeitest strikt nach der bestehenden
Architektur des Sigma-Terminals (React 19, TypeScript strict, Vite 6,
Tailwind CSS v4, shadcn/ui/radix, lucide-react, lightweight-charts v5,
recharts). Du erfindest keine Design-Sprache neu — du erweiterst die
vorhandene. Du änderst kein Geschäftslogik-Verhalten; das Backend bleibt
Herr der Berechnungen.

## 1. Mission

Bilde alle Roadmap-Funktionen (MP-01 … MP-16) im bestehenden Terminal ab:
- neue Panels registrieren (`PANEL_REGISTRY` / `PANEL_TITLES`),
- neue Presets andockbar machen,
- die bestehenden Panels (MarketChart, ExecutionRisk, Backtest, Settings,
  TvJobs/PineStudio) gezielt erweitern,
- die nötigen `/api/v1/sigma/*`-Leseendpunkte (dünn, fail-closed) ergänzen.

Die Bedienung muss **intuitiv und anpassbar** sein: alle Schwellen/Defaults
im Settings-Panel verstellbar (mit Kanonik-Defaults), Panels frei dockbar,
gefährliche Aktionen immer modal bestätigt.

## 2. Harte Constraints

1. **Stil treu:** nur vorhandene Bausteine — `PanelShell`, `Stat`,
   `IconBtn`, `FeedBadge`, `usePoll`; shadcn-Komponenten aus
   `src/components/ui/*`; zinc-950/zinc-900 + Emerald/Amber/Rot;
   Kennzahlen in JetBrains Mono; Header 10–11 px uppercase.
2. **Keine neuen Libraries.** Keine CSS-Frameworks, keine neuen
   Chart-Libs (lightweight-charts für Kerzen, recharts für Meter/Histos).
3. **Keine Mock-Daten im Live-Pfad:** fehlende Feeds → erklärender
   Leerzustand + FeedBadge; niemals synthetische Werte als Live getarnt.
4. **Schreibzugriffe** (Deploy, De-provision, Scan-Trigger, Parameter-Deploy)
   nur POST + Operator-Token + Bestätigungs-Modal (Muster
   `operatorPost`/`StrategyQueueConfirmModal`). Kein Auto-Deploy aus Tabellen.
5. **Das System beendet, der Mensch startet:** Das UI zeigt
   Kill-Switch-/Exit-/Unwind-Empfehlungen als Banner; es gibt keinen
   „manuellen Panic-Close“-Button außerhalb der bestehenden Safety-Mechanik.
   Sicherheitsregeln (Hard-Stop-Pflicht, Grid-Tiefe ≥ 6 %, Fee-Covered-BE)
   sind sichtbar, aber **nicht abschaltbar**.
6. **Zeiten in UTC, Zahlen mono.** Regime-/Phasen-Anzeigen ändern sich nur
   auf geschlossenen Bars (kein Intrabar-Flackern).
7. **Panels sind dumm:** Transformation/Berechnung im Backend; das Frontend
   rendert `/api/v1/sigma/*`-Antworten. TypeScript strict muss grün sein
   (`npm run lint` = `tsc --noEmit`).
8. Blinded-Modus: Ticker in Scout/Tensor per Toggle ausblendbar
   (`ASSET_###`).

## 3. Deliverables (in dieser Reihenfolge, jeweils mit Lint-Grün)

1. `src/lib/sigmaApi.ts`: typisierte Endpunkte —
   `GET /api/v1/sigma/regime`, `/risk`, `/power`, `/zones`, `/scout`,
   `/polymarket`, `/exhaustion`, `/provisions`, `/ladder/preview`,
   `/fractal/preview`, `/onnx`, `/orderflow`; `POST .../scan`,
   `POST .../provisions` (+ `/de-provision`); Research:
   `POST /api/v1/research/run`, `GET /api/v1/research/jobs/:id`,
   `GET /api/v1/research/dashboard`. Backend-Routen dünn aufsetzen
   (Antwort-Schemas in `app/server/schemas.py`), sie dürfen zunächst
   leere/fail-closed-Antworten liefern, bis die Fachmodule existieren.
2. **Neue Panels** (genaue Inhalte siehe UI-Spezifikation §3):
   `QuantumRegimePanel`, `MarketGeometryPanel`, `PowerPhysicsPanel`,
   `SymbolScoutPanel`, `PolymarketPanel`, `LadderArchitectPanel`,
   `FractalTradePanel`, `ProvisionerPanel`, `OnnxBrainPanel`,
   `RiskGuardPanel`, `UnwindPanel`, `ResearchLabPanel`.
   Jedes Panel: eigener Datei-Block in `src/components/sigma/`
   (oder eigene Datei nach dem Muster `StrategyLibraryPanel.tsx`),
   registriert in `PANEL_REGISTRY`/`PANEL_TITLES`, mit PanelShell +
   FeedBadge + Leerzustand.
3. **Erweiterungen:** MarketChart um Overlay-Toggles (FVG-Boxen, CE50/EQ,
   Envelope, Thrust-/Outside-Inside-Marker, cos-φ-Subpane); SettingsPanel
   um die Parametergruppen aus UI-Spezifikation §3.13; TvJobs um den
   Provisioner-Tab (oder ProvisionerPanel andockbar).
4. **Presets** `QUANTUM_OPS`, `POSITION_DESK`, `RESEARCH_LAB` im
   Preset-Builder-Muster (`set/row/tab`), in `SigmaTerminal.tsx`
   registriert.
5. ResearchLab: 3-Pane-lightweight-chart (Kerben+Marker / cos φ mit
   ±0,40/±0,15-Linien / Equity), Hypothesen-Liste H1–H7 mit Job-Status,
   Sweep-Tabelle mit Overfitting-Warnungen (Sharpe >3, DD <5 %).

## 4. Definition of Done

- `npm run lint` grün; Panel-Registrierung, Presets, Polling, Modals
  funktionieren; jeder Feed hat Quelle/Leerzustand;
- keine neuen Dependencies in `package.json`;
- Backend-Endpunkte existieren und liefern ohne Fachmodule strukturierte
  Leerantworten (fail-closed), das Frontend rendert diese sauber;
- ein `/docs`-Eintrag oder Kommentarblock dokumentiert die Endpunkt-
  schemata (Felder, Einheiten, UTC).

## 5. Wissensquellen (Pflichtlektüre)

- `docs/SIGMA-UI-SPEZIFIKATION.md` — diese Aufgabenstellung im Detail
- `docs/SIGMA-WISSENSDATENBANK.md` — §9.5 Featurevektor, §9.6 Zeiger,
  §10 Blinded/Ranker, §11 Tensor, §13 Guards, §14 Ladder, §15 Pine,
  §16 Fraktale, §17 Forschung
- `docs/SIGMA-ROADMAP.md` — Kontext der Funktionen
- Frontend-Musterdateien: `src/components/sigma/panels.tsx`,
  `dock.tsx`, `SigmaTerminal.tsx`, `src/lib/sigmaApi.ts`,
  `src/components/TvLightweightChart.tsx`
