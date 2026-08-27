# Projekt:Sigma — Hybrid Blueprint

> **Status:** Active  
> **Lineage:** Fork of [ALPHA-BLUEPRINT-v1.2.0](ALPHA-BLUEPRINT-v1.2.0.md) (M8 frozen)  
> **Delta:** Ubuntu-only · TradingView MCP backtesting · CSV interchange · no Windows TS portal

## Identity

| Field | Value |
|-------|-------|
| System | Manas: Ciel Core Matrix (M8 Execution & Risk Architecture) |
| Project | Projekt:Sigma |
| Spec | Sigma Hybrid 1.0 |
| Host | Ubuntu Linux only (server / container / local) |
| Backtest source | TradingView MCP via **CSV seam** |
| Execution | Python FastAPI M8 core (paper/shadow; Kraken as before) |

## Architecture Delta vs Alpha

| Dimension | Alpha | Sigma |
|-----------|-------|-------|
| Hosts | Ubuntu + optional Windows TS portal | **Ubuntu only** |
| Backtest engine | Local `BacktestEngine` + TS-GA duplicate | **TradingView MCP** |
| Interchange | Opaque in-process dicts | **Parameter CSV + Result CSV** |
| GA fitness | Local `run_backtest` | Same orchestrator; evaluations consume TV result CSVs |
| UI | Command Center | Same app; backtest flows labeled TradingView MCP |

## CSV Seam (canonical interchange)

TradingView Strategy Tester exports **parameter CSVs** and **backtest result CSVs**.
Sigma treats these as the only adapter contract between TV and the Python orchestrator:

1. **Parameter CSV** → Pine `input.*` / GA gene space (`genes_to_params` / `params_to_csv` round-trip)
2. **Result CSV** (list-of-trades + optional performance summary) → `BacktestResult` for UI + GA fitness/DSR

```
GA / UI ──params CSV──► TradingView MCP ──result CSV──► TvMcpBacktest ──► BacktestResult
```

- No silent fallback to the local Alpha `BacktestEngine`.
- Autonomous WFO loops use the same adapter (queue + result cache).
- MCP offline → hard error / paused GA run.

## Autonomous Circles

| Circle | Orchestration | Evaluation |
|--------|---------------|------------|
| Manual UI backtest | FastAPI `/api/backtest/run` | TV MCP → result CSV |
| WFO GeneticOptimizer | Python population / DSR gate | Per individual IS+OOS via result CSVs |
| Counterfactual replay | Python | TV MCP result CSV |
| Academy drills | Python (synthetic) | unchanged |

## Modules

| Path | Role |
|------|------|
| `app/backtest/tv_csv.py` | Parse/serialize parameter & result CSVs |
| `app/mcp/TradingViewMCPClient.py` | JSON-RPC client (`SIGMA_TV_MCP_URL`) |
| `app/backtest/TvMcpBacktest.py` | Facade + queue + cache → `BacktestResult` |
| `app/optimizer/GeneticOptimizer.py` | WFO/DSR; calls TvMcpBacktest only |
| `stacks/ubuntu/` | Sole host stack |

## Env

| Variable | Purpose |
|----------|---------|
| `SIGMA_TV_MCP_URL` | TradingView MCP endpoint (required for live backtests) |
| `SIGMA_TV_MCP_TIMEOUT_S` | Request timeout (default 120) |
| `SIGMA_TV_MCP_CONCURRENCY` | Max parallel MCP jobs (default 4) |
| `SIGMA_DATA_DIR` | Data lake root (alias of former `ALPHA_DATA_DIR`) |
| `SIGMA_REDIS_URL` | Redis URL |

## Delivery

1. Bootstrap from Alpha (M8 + React preserved)
2. Remove `stacks/windows`
3. CSV-backed TV MCP adapter
4. Rewire `/api/backtest/*` + GA
5. UI labels / MCP progress states
6. Unit tests for CSV mapping + adapter (fake transport)
