# Vendor Sources

Third-party source trees vendored into Sigma for Loop C (Market Radar / Scraper Sidecar).

## tradingview-scraper

| Field | Value |
|-------|-------|
| Path | `vendor/tradingview-scraper/` |
| Upstream | [MrChartist/tradingview-scraper](https://github.com/MrChartist/tradingview-scraper) |
| Package | `tradingview-scraper` v0.4.20 (MIT) |
| Sigma role | Sidecar API on port `:8001` — OHLCV, market movers, screener feeds |

Imported from `tradingview-scraper-main.zip` (2026-04-09 snapshot).

**Run (dev):**

```bash
cd vendor/tradingview-scraper
pip install -r requirements.txt
uvicorn api.main:app --host 127.0.0.1 --port 8001
```

See `docs/BLUEPRINT-SIGMA.md` for integration contract.
