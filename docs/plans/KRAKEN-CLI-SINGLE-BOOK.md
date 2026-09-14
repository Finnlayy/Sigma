# Plan: Ein Buch — Kraken CLI ist die Execution-Engine

**Status:** BAU MOSTLY DONE (2026-09-12)  
**Datum:** 2026-09-12  
**Kontext:** Nach Restore auf `13d07f2`. Diagnose: Sigma hat denselben Dual-Ledger-Fehler wie Strategie-Runner (Peter): UI/Kapital/Fills aus Seed+DuckDB, Orders teilweise CLI. Blueprint §4 sagt CLI-Executor, Laufzeit weicht ab.

## Progress (bau)

| Schritt | Status | Notes |
|---------|--------|-------|
| 0. CLI Latest | **DONE** | `kraken 0.4.1` = official `krakenfx/kraken-cli` (nicht crates.io 0.27) |
| A. Inventar + Registry | **DONE** | `kraken_cli_registry.json` — **181** leaves; **0 stubs** |
| A. Bridge argv 0.4.1 | **DONE** | Specialized + generic `run_leaf`; paper `cancel-all`; fail-closed |
| B. Kapital-SoT | **DONE** | `_paper_balances` → `paper status`+`paper balance` via `kraken_paper_sot.py` |
| C. Act nur CLI | **DONE** | LoopA/Dispatcher → CLI paper; dangerous leaves need `confirmed=` (+ live gate) |
| D. Journal = Spiegel | **DONE** | `kraken_paper_history_mirror.py` ← `paper history`; DuckDB upsert mirror only |
| E. Aufräumen Lab/Deck | **DONE** | `paper_seeds` leer; Lab `balance_usd` = CLI `current_value`; MCP quarantined |
| F. Beweis | **DONE** | Fixture parity test + `scripts/verify_ui_cli_capital.py` (+ optional `SIGMA_CLI_LIVE_SMOKE=1`) |
| G. Remote Wipe | **OUT OF SCOPE** | `e59ee9c` unberührt |

### Neu / geändert (dieser Slice)

- `KrakenCliBridge.run_leaf` — registry argv runner for all leaves
- `KrakenCliBridge.paper_history` + history → DuckDB mirror
- Registry: every leaf `adapter != stub` (`run_leaf` or specialized)
- `KrakenMCPBridge` — **QUARANTINED** (no fake paper execute on `/api/mcp/*`)
- Lab panel / `panel_state` — CLI capital SoT fields
- `scripts/verify_ui_cli_capital.py` — manual UI==CLI proof
- `close_all_market` — remain `CLI_UNSUPPORTED` (0.4.1 has no flatten leaf)

## Nicht verwechseln

| Incident | Was | Status |
|----------|-----|--------|
| Express-Wipe `e59ee9c` | Python-Backend gelöscht | Lokal restored (`13d07f2`); Remote noch Wipe |
| Dual Paper-Buch | 50k-Seed + DuckDB + In-Memory-FILLs neben `kraken paper` | **Dieser Plan — SoT umgelegt** |

## Soll-Zustand (nicht verhandelbar)

1. **CLI ist die Engine.** Sigma orchestriert, klassifiziert, gated — sie **implementiert kein zweites Trading-System**.
2. **Ein Buch pro Modus, Quelle immer CLI:**
   - Paper-Kapital / PnL / Trades = `kraken paper status` (+ `paper balance` / `paper history` wo nötig).
   - Live-Kapital = `kraken balance` / `extended-balance` / `trade-balance`.
3. **Act nur per CLI-Karte:**
   - Paper: `kraken paper buy|sell|cancel…` — ändert nur das CLI-Paper-Ledger.
   - Live: `kraken order …` **nur** bei `SIGMA_LIVE_TRADING=1` ∧ `LIVE_APPROVED` ∧ Overlay/Gates. LLM signiert nie Live.
4. **Keine lokalen SoT-Fills.** Kein `PaperExecutionEngine` als Fill-Buch. Kein Seed-Basket als Kapital. Kein 10k-Lab-Zähler als „Balance“. Kein erfundener `PAPER-*` Fill, wenn CLI fehlt → **fail-closed** (`ok=False`, UI zeigt „CLI offline“).
5. **DuckDB bleibt Journal/Autopsy** (Strategie-Logs, Scorecards, Academy) — **Kopie/Telemetrie**, nie Source of Truth für Kapital oder „ob ein Fill echt war“. Ein Fill zählt nur, wenn die CLI ihn kennt.
6. **Marktdaten:** OHLC weiter Scraper/CCXT (Feed ≠ Execution). Preise für Sizing/Live-Eligibility aus CLI `ticker` / `ws` wo die CLI die SoT ist; kein synthetischer Tick als Order-Grundlage. Registry leaves `ticker`/`ohlc`/… remain **callable** via `run_leaf` (`FEED_OTHER`).
7. **Orchestrator** klassifiziert und gated weiter; platziert **nur** über `KrakenCliBridge` → subprocess. Kein Parallel-REST-Private für dieselben Jobs.

## Remaining / impossibles (ehrlich)

| Gap | Status | Reason |
|-----|--------|--------|
| Spot `close_all_market` flatten | **IMPOSSIBLE on 0.4.1** | No `order close-all` leaf. Fail-closed `CLI_UNSUPPORTED`. |
| Futures deadman flatten | **BEST-EFFORT** | `cancel-all` + gated reduce-only closes from `futures/[paper/]positions`. Not a native flatten leaf. |
| Market feed = scraper | **BY DESIGN** | Dual-Hurst/Wave/ATR stay on scraper OHLC; sizing/risk consume CLI capital/fees/ticker. |
| `ws/*` long-lived streams | **CALLABLE / LIMITED** | `run_leaf` can spawn argv; no production WS supervisor for all leaves yet. |
| VirtualBotDeck soft-cap equity | Soft | Ringfence budget ≠ CLI paper book; capital UI uses CLI. |
| Remote wipe `e59ee9c` | Out of scope | Do not push wipe. |
| L4 YAML + LWC charts | Sibling | `docs/plans/L4-AUTONOMY-AND-LWC-CHARTS.md`. |

## Futures SoT (L4 Nachbesserung — 2026-09-12)

| Surface | SoT |
|---------|-----|
| Capital / Lab (futures-primary) | `kraken futures paper status\|balance` via `fetch_paper_capital(..., futures=True)` |
| Pro positions | `futures/paper/positions` (paper) / `futures/positions` (live) |
| History mirror | `futures/paper/fills` → DuckDB journal |
| cancel-all | `futures [paper] cancel-all` (never spot `order cancel-all` on futures bridge) |
| cancel-after | Session start+refresh; paper no-op; live L4 only |
| Fees | `futures/feeschedules` TTL cache → FeeEngine; paper `fee_rate` when present |
| Funding | `futures/historical-funding-rates` (Pro UI never fakes `0.01`) |
| Kelly equity | `fetch_paper_capital().current_value` (correct book) |
| Allowlist | Canonical `PF_*`; `PI_*` = TV alias |
| L5 | Hard-deny withdraw / wallet-transfer in `run_leaf` (confirmed= ignored) |

Verify futures book:

```bash
kraken futures paper status -o json
.venv/bin/python scripts/verify_futures_ui_cli_capital.py
.venv/bin/python -m pytest tests/test_l4_futures_nachbesserung.py -q
```

## How Finn verifies UI == CLI

```bash
# 1) CLI book
kraken --version          # expect 0.4.1 (krakenfx), not crates.io 0.27
kraken paper status -o json
kraken paper balance -o json

# 2) SoT helper == CLI
.venv/bin/python scripts/verify_ui_cli_capital.py

# 3) Optional pytest live smoke
SIGMA_CLI_LIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_kraken_single_book.py -k live_smoke -q

# 4) With Sigma up: capital / logs panel balances must match status.current_value
#    and balance asset totals — never paper_seeds / 50000.
```

## Definition of Done

0. `kraken --version` ist die zum Bauzeitpunkt aktuelle stabile CLI (Upgrade-Gate erfüllt). ✅ 0.4.1
1. Grep: keine produktive Nutzung von `paper_seeds` / `_paper_balances` für Kapital-UI. ✅ seeds leer; `_paper_balances` = CLI
2. Paper-Act ohne CLI → fail, nie `PAPER-*` ok=True. ✅
3. Manueller Beweis F (Paper) — ✅ script + fixture test (+ optional live smoke)
4. Tests asserten CLI-Mock-Status, nicht 50000-Seed. ✅
5. Docs (dieser Plan) an Soll-Zustand — ✅
6. Registry: every leaf callable (generic `run_leaf` OK); zero stubs. ✅
7. MCP mock not on production happy path. ✅ quarantined
