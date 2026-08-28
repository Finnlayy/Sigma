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

### Sigma-Ergänzungen zum Vendor-Tree

| Pfad | Warum |
|------|-------|
| `tradingview_scraper/data/` | Fehlte im importierten Zip-Snapshot. `Indicators` und `News` laden `exchanges.txt`, `indicators.txt`, `timeframes.json`, `areas.json`, `languages.json`, `news_providers.txt` relativ zum Paket — ohne diese Dateien lehnt der Scraper **jede** Exchange ab (`This exchange is not supported!`). Sigma liefert einen kuratierten Datensatz inkl. `KRAKEN`. |

Der Vendor-Code selbst bleibt unverändert. Die Sigma-Integration liegt in
`app/scraper/` (Overlay-Sidecar) und `app/tv/scraper_client.py` (Client).

**Run (Sigma):**

```bash
bin/sigma-scraper            # Overlay: Cache, Rate-Limit, Retry, Offline-Fallback
bin/sigma-scraper --vendor   # unverändertes vendored api.main:app
bin/sigma-scraper --check    # Bereitschaft prüfen
```
