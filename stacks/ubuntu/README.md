# Ubuntu Core-Stack (Projekt:Sigma)

**Host:** Ubuntu 24.04+ · always-on (sole host — Windows portal removed)

```
Ingestion: OmniStream / Glint / CCXT WS
M8 Judge (Noir) · Shadow Execution (Jaune)
Redis :6379 AOF · DuckDB / Parquet
Backtest: TradingView MCP (parameter + result CSV seam)
Stack: ccxt · httpx · fastapi · uvicorn · pydantic · pyarrow
```

## Env

| Variable | Purpose |
|----------|---------|
| `SIGMA_TV_MCP_URL` | TradingView MCP endpoint (`fake` for sandbox) |
| `SIGMA_TV_MCP_TIMEOUT_S` | MCP timeout seconds (default 120) |
| `SIGMA_TV_MCP_CONCURRENCY` | Max parallel MCP backtest jobs (default 4) |
| `SIGMA_REDIS_URL` / `SIGMA_DATA_DIR` | Redis + data lake |

## Option A — Docker

```bash
cd stacks/ubuntu
docker compose up -d --build
# API: http://127.0.0.1:8000/api/dashboard/init
```

## Option B — Bare-Metal (systemd)

```bash
sudo bash setup_ubuntu.sh /pfad/zum/repo
journalctl -u alpha-core -f   # or sigma-core if renamed
```

NFS export for a Windows portal is **not** part of Sigma. Data stays on the Ubuntu host.

## Backtesting

All backtests (manual UI + autonomous GA/WFO) go through `TvMcpBacktest`:
parameter CSV → TradingView MCP → result CSV → `BacktestResult`.
There is no local `BacktestEngine` fallback.

## Notfall-CLI (`bin/m8-ctl`)

Unchanged from Alpha M8 lineage — status/states/vault/halt/promote/quarantine/eod/cancel-all.
