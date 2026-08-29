# Wave-Screen: Execution-Universe + Academy

Reviewed plan for the next slice. Wave-regime v1 (collider, CE50, BTC `INVALIDATED` unwind) is already on the orchestrator. This document is the symbol-screener follow-up: find *where* paper bots are worth testing, without spending Academy work on symbols Loop A cannot trade.

## Intent

Wave is not a timeframe filter. It is a screener: which symbols in the live execution universe currently sit in a dealing-range / FVG / CE50 discount, so Scout and Academy can paper-test existing templates there.

The attached venue is the source of truth for *what may be traded*. The TV scraper (`:8001`) is the parallel source of *OHLC*. Those two must not be mixed.

Today the execution allowlist is Kraken spot (`XBTUSD`, `ETHUSD`) plus Kraken Pro / futures (`PI_XBTUSD`, `PI_ETHUSD`) via [`is_allowed()`](../app/tv/symbol_map.py). [`market_symbols`](../app/core/config.py) also lists SOL/XRP — that is feed only. Loop A rejects those with `SYMBOL_NOT_ALLOWED`.

If Pionex or a live CCXT bridge is attached later, that venue's market list becomes the universe. Screen, Scout, and Academy stay unchanged.

```
ExecutionUniverse.list_symbols
        │
        ▼
tradable watchlist ──► parallel scraper OHLC (Loop-C cache first)
        │                         │
        │                    skip synthetic / degraded
        ▼
QuantumWaveCollider.screen
        │
        ├── BTC INVALIDATED → orchestrator unwind_only
        └── tradable COLLAPSED → Loop D Scout → Academy paper drills
```

## Already shipped (v1)

- [`sigma/core/fractal_scaling.py`](../sigma/core/fractal_scaling.py) — CE50, closed-bar slice, discount zone
- [`sigma/signals/quantum_wave_collider.py`](../sigma/signals/quantum_wave_collider.py) — `IDLE` | `COLLAPSED_INTO_ZONE` | `INVALIDATED` | `HTF_OPEN`
- [`MasterOrchestrator.tick`](../sigma/orchestration/master_orchestrator.py) — publishes `wave`, passes `ctx["wave"]`, unwinds on leader `INVALIDATED`
- [`tests/test_quantum_wave_regime.py`](../tests/test_quantum_wave_regime.py)

Sniper-DCA, 1m ladders, and Pine stay out of scope.

## Next slice

### Execution universe port

[`sigma/execution/universe.py`](../sigma/execution/universe.py) (to add):

```python
class ExecutionUniverse(ABC):
    def list_symbols(self) -> list[str]: ...   # canonical BTC/USD
    def is_tradable(self, symbol: str) -> bool: ...
```

- **Now:** `KrakenExecutionUniverse` — spot + futures through `is_allowed()`. No hardcoded tickers in the collider.
- **Later Pionex:** `PionexExecutionUniverse`, same port.
- **Later CCXT:** only from a *live-registered* bridge (`load_markets`). Do not read [`CcxtExecutionBridge`](../sigma/execution/base_bridge.py) while it is `NotImplemented`.
- **Several venues:** `CompositeExecutionUniverse` = union of live adapters.

Canonical form is Sigma (`BTC/USD`). Mapping to `XBTUSD` stays inside `to_kraken_pair` / `is_allowed`. Scout must not be fed raw Kraken pair codes.

### Parallel scraper hydrate

- `wanted = universe.list_symbols()`
- Reuse `htf_series` from [`LoopCPort.poll_pair`](../sigma/loops/loop_c.py)
- Fill gaps in parallel (`ThreadPoolExecutor`, worker cap ~4) via [`fetch_ohlc_with_meta`](../app/tv/scraper_client.py)
- Drop `synthetic` / `degraded` the same way Loop C does
- [`movers()`](../app/tv/scraper_client.py) may reorder; it must not enlarge the universe
- Sidecar down → empty screen; Scout falls back to `universe.list_symbols()`

### Screen and Academy

- `QuantumWaveCollider.screen(...)` — candidate iff tradable **and** `COLLAPSED_INTO_ZONE`
- Orchestrator tick adds `wave_screen`; leader `INVALIDATED` still unwinds
- [`ScoutDaemon.plan`](../app/scout/ScoutDaemon.py) gets symbols per tick (do not mutate the `get_scout()` singleton permanently)
- [`AcademyRegistry.ingest_wave_screen`](../app/optimizer/AcademyRegistry.py) — in-memory watchlist on `list()` first, no new DuckDB table in this slice
- Paper only. `COLLAPSED` is not a live deploy trigger

## Tests (when implementing)

- Collapsed `SOL/USD` with today's allowlist → absent from the screen (even if the scraper has the series)
- Same series with a fake universe that allows SOL → present
- Parallel hydrate: one symbol cached, one fetched; synthetic meta discarded
- Scout creates no tasks for untradable symbols
- Empty screen → Academy watchlist empty; defaults = universe, not `market_symbols`

## Review notes

The split is correct. Tightening before code: pair canonicalization, Kraken Pro = `EXCHANGE_FUTURES` in this repo, per-tick Scout symbols, scraper worker cap, in-memory Academy watchlist, no stub CCXT markets.
