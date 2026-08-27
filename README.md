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
