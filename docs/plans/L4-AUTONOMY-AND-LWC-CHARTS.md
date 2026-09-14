# Plan: L4 Autonomy YAML + Lightweight Charts Guide

**Status:** INTEGRATED (2026-09-12)  
**Artifacts:** Finn Downloads → in-repo canonical paths  
**Related:** `docs/plans/KRAKEN-CLI-SINGLE-BOOK.md`, Cursor skill `kraken-autonomy-levels`

## Artifacts

| Download | Canonical path | Status |
|----------|----------------|--------|
| `autonomy-level-4.yaml` | `config/autonomy-level-4.yaml` | Already present; **byte-identical** to Downloads copy. Loader: `app/core/l4_config.py`. Spec mirror: `app/core/blueprint.py` + `tests/test_blueprint_spec.py`. |
| TradingView LWC Integration Guide.docx | `docs/guides/TradingView-Lightweight-Charts-Integration.md` | Extracted; critical path wired into React chart + WS. |

Pointer index: `docs/artifacts/README.md`.

## Autonomy (YAML + Kraken skill)

YAML defines production L4 surface (exchange allowlists, `risk_guard`, deadman, safety `live_trading_env: SIGMA_LIVE_TRADING`, etc.).

Kraken skill mapping (`app/core/autonomy_levels.py`):

| Level | Meaning | Sigma gate |
|-------|---------|------------|
| 1 | Read-only | Public/CLI reads |
| 2 | Paper | Default (`kraken paper`) |
| 3 | Supervised | Live UI / env without `LIVE_APPROVED` |
| 4 | Autonomous | `SIGMA_LIVE_TRADING=1` ∧ `LIVE_APPROVED` (+ `confirmed=` on dangerous leaves) |
| 5 | Fund mgmt | **Forbidden** |

UI metrics / toggle-mode / blueprint now emit this snapshot instead of naïvely mapping `!paper → L4`.

## Lightweight Charts (guide vs Sigma)

| Guide item | Sigma |
|------------|-------|
| Decoupled visualization plane | Chart mirrors backend; no client execution math |
| `lightweight-charts` npm | v5.x (`createSeries` + `createSeriesMarkers`) |
| Magnet crosshair, dark terminal option | `CrosshairMode.Magnet`; `theme="terminal"` optional |
| `setData` / `update` | Historical load + incremental last-bar |
| Markers BUY/SELL | `markers` prop → `createSeriesMarkers` |
| Price lines ENTRY/SL/TP | `priceLines` prop → `createPriceLine` |
| WS `/ws/market-feed/{symbol}` | Implemented — Loop C scraper poll + optional Redis pub/sub |
| Redis candle pub/sub | Optional multiplex; scraper is primary SoT |
| rAF batching | MarketChart WS handler |
| Clear on symbol switch | `seriesKey` clears lines/markers |

Offline MP-16 HTML export (`app/dashboard/tv_lightweight_export.py`) remains CDN v4 research-only.

## Remaining gaps (honest)

- Redis is not required for chart streaming; until producers publish `market:candles:*` / `alpha:executions:live`, markers/price lines arrive only when those channels fire (or future paper-history overlay).
- Full guide Redis fan-out from execution engine is not a second fill book — Single-Book still owns CLI SoT.
- L5 withdrawals stay out of scope forever under product rules.

## Verify

```bash
.venv/bin/python -m pytest tests/test_autonomy_levels.py tests/test_blueprint_spec.py tests/test_frontend_terminal.py tests/test_market_feed_ws.py -q
npm run lint
```
