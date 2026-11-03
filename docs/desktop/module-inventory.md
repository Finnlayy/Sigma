# Module inventory

Generated from the actual source AST on 2026-11-03. Counts are inventory, not proof of external connectivity.

| Area | Python modules | Functions/classes | Literal routes |
|---|---:|---:|---:|
| `app/backtest` | 4 | 41 | 0 |
| `app/core` | 15 | 312 | 0 |
| `app/dashboard` | 1 | 9 | 0 |
| `app/execution` | 33 | 362 | 0 |
| `app/ingestion` | 4 | 36 | 0 |
| `app/llm` | 3 | 35 | 0 |
| `app/mcp` | 2 | 21 | 0 |
| `app/optimizer` | 9 | 136 | 0 |
| `app/quant` | 7 | 86 | 0 |
| `app/scout` | 2 | 12 | 0 |
| `app/scraper` | 6 | 76 | 15 |
| `app/security` | 2 | 25 | 0 |
| `app/server` | 6 | 395 | 199 |
| `app/services` | 5 | 85 | 0 |
| `app/telegram` | 1 | 5 | 0 |
| `app/tv` | 11 | 196 | 0 |
| `sigma/backtest` | 3 | 24 | 0 |
| `sigma/core` | 4 | 49 | 0 |
| `sigma/execution` | 5 | 61 | 0 |
| `sigma/loops` | 6 | 40 | 0 |
| `sigma/orchestration` | 6 | 75 | 0 |
| `sigma/ports` | 2 | 7 | 0 |
| `sigma/signals` | 23 | 176 | 0 |
| `sigma/strategies` | 12 | 101 | 0 |

The renderer is the existing `src/App.tsx` → `SigmaTerminal` dock. All existing registry panels remain registered; `DesktopServicesPanel` adds packaging diagnostics only. All 15 existing presets remain available. `src/server/kraken` is a legacy alternate Node ledger, not the Python single-book source of truth, and is not mounted by the desktop.
