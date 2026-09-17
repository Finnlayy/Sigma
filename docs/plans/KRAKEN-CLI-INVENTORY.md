# Kraken-CLI Inventar — Gegenüberstellung (Sigma)

**Binary:** `kraken 0.4.1` (`~/.cargo/bin/kraken`, krakenfx) — **181** Leaves  
**Registry:** [`app/execution/kraken_cli_registry.json`](../../app/execution/kraken_cli_registry.json)  
**Stand:** Inventory plan implemented on `jules/mp-execute` (argv 0.4.1, Single-Book SoT, `run_leaf`, flatten orchestration).

## Schichten

| Schicht | Anzahl | Notes |
|---------|--------|-------|
| Top-level | 52 (+ help) | `kraken help` |
| Leaves | **181** | registry `leaf_count` |
| Plugin catalog | 151 | Cursor plugin (älter) |
| MCP default | 27 | `-s market,paper` only |
| Sigma | Registry + Bridge | alle Leaves via `run_leaf` oder spezialisiert |

## Legende

| Code | Bedeutung |
|------|-----------|
| `CLI_CALL` | Bridge/subprocess |
| `CLI_FIXED` | Früher `trade`/`account` — jetzt `order`/`balance` |
| `HOMEMADE_REMOVED` | Seed/Fake-Fill entfernt; fail-closed |
| `FEED_OTHER` | Chart-OHLC weiter Scraper; Leaf trotzdem callable |
| `L5_DENY` | withdraw/wallet-transfer hard-deny |

## Kritische argv (0.4.1)

| Absicht | argv |
|---------|------|
| Spot paper buy/sell | `kraken paper buy\|sell PAIR VOL` |
| Spot live | `kraken order buy\|sell PAIR VOL` |
| Futures paper | `kraken futures paper buy\|sell …` |
| Futures live | `kraken futures order buy\|sell …` |
| Balance | `kraken balance` / `kraken [futures] paper balance` |
| Cancel | `kraken order cancel-all` / `paper cancel-all` / `futures [paper] cancel-all` |
| Flatten | Orchestrierung: cancel-all → positions → reduce-only closes → verify/retry (`kraken_cli_flatten.py`) |

## SoT

- Paper-Kapital: `paper status` + `paper balance` (bzw. futures paper) — **kein** `paper_seeds`
- History: CLI history/fills → DuckDB nur Spiegel
- Fehlende CLI → `ok=False`, `ERR_KRAKEN_CLI_NOT_FOUND` — **keine** `PAPER-*` / `SIM-BALANCE` Fakes

## Nicht vermischen

- `KrakenMCPBridge` 149 Fake-Tools: quarantined  
- Cursor MCP `market,paper` ≠ volle CLI  
- L5 Fund Management: forbidden  

Siehe auch: [`KRAKEN-CLI-SINGLE-BOOK.md`](KRAKEN-CLI-SINGLE-BOOK.md).
