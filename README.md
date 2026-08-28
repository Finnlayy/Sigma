# Projekt:Sigma

**Manas: Ciel Core Matrix** — M8 Execution & Risk Architecture.
Hybrid fork of Alpha: **Ubuntu-only**, **TradingView MCP** backtesting via **CSV seam**.

| | |
|---|---|
| **Blueprint Spec** | [BLUEPRINT-SIGMA.md](docs/BLUEPRINT-SIGMA.md) |
| **Alpha lineage** | [ALPHA-BLUEPRINT-v1.2.0.md](docs/ALPHA-BLUEPRINT-v1.2.0.md) (frozen M8 reference) |
| **Autonomy** | Level 4 — High Operational Autonomy |
| **Stack** | [Ubuntu Core](stacks/ubuntu) only — Windows TS portal removed |

## Sigma vs Alpha

| Feature | Alpha | Sigma |
|---|---|---|
| Hosts | Ubuntu + optional Windows TS | **Ubuntu only** |
| Backtesting | Local `BacktestEngine` + TS-GA | **TradingView MCP** (parameter + result **CSV**) |
| GA / WFO | Local engine evaluations | Same Python orchestrator; fitness from **TV result CSVs** |
| UI | Command Center | Same app; backtest flows → TV MCP |

## Hard-coded Blueprint (Spec Freeze v3.0)

Die Blaupause ist nicht nur Prosa — sie ist im Code festverdrahtet:

| Artefakt | Rolle |
|----------|-------|
| [`app/core/blueprint.py`](app/core/blueprint.py) | **Maschinenlesbarer Spec-Freeze**: Ports, Pfade, Loops A–E, Risk-Limits, M8-Alert-Matrix, GA-Härtung, Regime-/ONNX-/Reward-/Badge-Schwellen, Panels, Redis-Keys, Delivery-Phasen — eingefroren (`tuple` / `MappingProxyType`) + normative Helfer (`calculate_kelly`, `badge_rating`, `alert_policy_for_state`, …) |
| [`config/autonomy-level-4.yaml`](config/autonomy-level-4.yaml) | Deploy-Config nach Blueprint §9 |
| [`app/core/l4_config.py`](app/core/l4_config.py) | Lädt die YAML, fällt bei Fehlen/Parse-Fehler **hart auf die Blueprint-Defaults** zurück (kein stiller Zufallswert) |
| [`app/core/config.py`](app/core/config.py) | `SigmaConfig` bezieht alle L4-Defaults aus `blueprint.py`; `SIGMA_*`-Env darf Caps (GA-Population/Generationen, Playwright-Concurrency 1) **nicht** überschreiten |
| [`tests/test_blueprint_spec.py`](tests/test_blueprint_spec.py) | Noir-Gate: verifiziert `blueprint.py` ⟷ `docs/BLUEPRINT-SIGMA.md` ⟷ `docs/MASTERPROMPT.md` ⟷ YAML |

Laufzeit-Introspektion: `GET /api/v1/health` (Spec-Fingerprint, Kill-Switch, Scraper/Worker)
und `GET /api/v1/blueprint` (Loops, M8-Alert-Matrix, API-Vertrag, geladene Config).

## CSV Seam

1. **Parameter CSV** ↔ Pine `input.*` / GA genes (`app/backtest/tv_csv.py`)
2. **Result CSV** (list-of-trades ± performance) → `BacktestResult` for UI + DSR

No silent fallback to the local Alpha backtest engine.

## Quick Start

```bash
# Optional: live TradingView MCP endpoint (default: fake transport for sandbox)
export SIGMA_TV_MCP_URL=fake   # or https://your-tv-mcp-endpoint

pip install -r requirements.txt
python3 -m uvicorn app.server.main:app --host 0.0.0.0 --port 8000

npm install
npm run dev          # → http://localhost:3000

pytest tests/ -v
```

## Repository Structure

```
Sigma/
├── docs/BLUEPRINT-SIGMA.md
├── docs/ALPHA-BLUEPRINT-v1.2.0.md
├── app/
│   ├── backtest/tv_csv.py          # CSV parse/map
│   ├── backtest/TvMcpBacktest.py   # Adapter + queue + cache
│   ├── mcp/TradingViewMCPClient.py
│   ├── execution/                  # M8 (unchanged lineage)
│   ├── optimizer/GeneticOptimizer.py
│   └── server/main.py
├── src/                            # React command center
└── stacks/ubuntu/                  # Sole host stack
```

## Env (Sigma)

| Variable | Purpose |
|----------|---------|
| `SIGMA_TV_MCP_URL` | TV MCP URL (`fake` for tests) |
| `SIGMA_TV_MCP_TIMEOUT_S` | Timeout (default 120) |
| `SIGMA_TV_MCP_CONCURRENCY` | Parallel MCP jobs (default 4) |
| `SIGMA_DATA_DIR` / `SIGMA_REDIS_URL` | Data + Redis (Alpha env names still accepted) |
