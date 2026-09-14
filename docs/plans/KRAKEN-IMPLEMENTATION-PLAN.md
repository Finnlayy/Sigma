# KRAKEN-IMPLEMENTATION-PLAN

Status: **Loop: ACTIVE** · Phase 1 **DONE** · Next: **K-2**

Repo reality this plan is written against (verified 2026-09-13 on `main` @ `3a1b81c`):

- This repository is the **frontend + dev/mock server**: `src/**` (Vite/React 19) and
  `server.ts` (Express 5 + Vite middleware). The Python product (`app/`, `sigma/`, `tests/`)
  referenced by `AGENTS.md` / `FIX_PLAN.md` is **not in this checkout**.
- `server.ts` answers **every** `/api/*` with a blanket mock
  (`{status:'mocked', ok:true, available:false}`), so all Kraken panels currently render
  fail-closed with no data.
- Kraken is already a first-class concept in the UI: 6 endpoints are called by 6 components,
  and the response types already exist in `src/types.ts`.

## 1. Existing Kraken surface (what already exists — do not rewrite)

| Consumer | Endpoint it calls | Expected shape |
| --- | --- | --- |
| `SigmaTerminal.tsx:245` | `GET /api/kraken/status` | reads `latencyMs` |
| `sigma/legacyPanels.tsx:92` | `GET /api/kraken/status` | reads `hasCredentials`, `paperTrading` |
| `MarketPanel.tsx:174` | `GET /api/kraken/symbols` | `{ symbols: KrakenSymbolInfo[] }` |
| `StrategyEditor.tsx` | `GET /api/kraken/symbols` | same |
| `KrakenLedgersPanel.tsx:33` | `GET /api/kraken/ledgers` | `KrakenAccountLedgers` |
| `KrakenLedgersPanel.tsx:56` | `POST /api/kraken/ledgers/sync` | `{ timestamp, spot, pro }` |
| `MetricsPanel.tsx:87` | `GET /api/kraken/positions/pro` | `totalCollateralUSD`, `freeMarginUSD`, `totalUnrealizedPnL`, `positions[]`, `reason?` |
| `MetricsPanel.tsx:113` | `POST /api/kraken/sync-balance` | `{ hasCredentials, liveKrakenBalances?, error? }` |

Already present and reused as-is:

- `src/types.ts` — `KrakenSpotPosition`, `KrakenProPosition`, `KrakenAccountLedgers`,
  `KrakenSymbolInfo`, `KrakenAssetPairsResponse`.
- `src/lib/symbolNormalizer.ts` — `ExchangeSymbolNormalizer` (`KRAKEN_BASE_MAP`,
  `toKrakenSpot`, `toKrakenProFutures`, `resolveAll`).
- `src/lib/api.ts` — `safeFetchJson` (4 s AbortController timeout, returns `null` on failure).

## 2. Hard rules (inherited from `AGENTS.md`, non-negotiable)

1. **Paper only** (`kraken_paper`). No `add_order`, no live orders, no exchange credentials,
   no private Kraken endpoint. Only `/0/public/*` is called.
2. **Fail closed.** Missing/unreachable feed ⇒ structured empty schema, `ok:false`,
   `available:false`, `reason` set. **Never** invented live numbers, never a LIVE badge.
3. No `TODO`/`FIXME`/`NotImplementedError`/`placeholder`/stub bodies in `src/`.
4. Constants are **named and hardcoded**, not "configurable later".
5. Every formula in this plan is implemented, not sketched.

## 3. Phases

### K-1 — Contract-complete Kraken layer on the dev server — **DONE**

Delivered files:

- `src/server/kraken/client.ts` — public REST client. `KRAKEN_API_BASE`,
  `KRAKEN_TIMEOUT_MS = 8000`, `ASSET_PAIRS_TTL_MS = 900000` (15 min), `TICKER_TTL_MS = 5000`.
  `krakenGet()` returns `{ ok, data, error, latencyMs }`; error taxonomy
  `ERR_KRAKEN_UNREACHABLE | ERR_KRAKEN_TIMEOUT | ERR_KRAKEN_HTTP | ERR_KRAKEN_SHAPE | ERR_KRAKEN_EAPI`.
  Kraken's own `error[]` strings (`EAPI:`, `EGeneral:`, `EQuery:`) beat the HTTP status —
  same rule as `blueprint.kraken_output_is_error` in `EXECUTION_REPORT.md` §P5.
- `src/server/kraken/symbols.ts` — `mapAssetPairs()`: raw `AssetPairs` result →
  `KrakenAssetPairsResponse`. Pair display name = `wsname` when present (Kraken uses
  `XBT/USD`), else `altname`, else the pair key. `hasLeverage = leverage_buy.length > 0`.
  Leverage strings are parsed to numbers (`"5:1"` → `5`) and de-duplicated.
  `quotes` = `POPULAR_QUOTES` order first, remaining quotes alphabetically.
  `popularSymbols` = every listable pair whose **quote** is in `POPULAR_QUOTES`,
  sorted by symbol. Pairs with `status: delisted` (anything outside `LISTABLE_STATUS`)
  are dropped.
- `src/server/kraken/ledger.ts` — pure paper-ledger math (no I/O, no network):
  - `PAPER_INITIAL_CASH_USD = 10000` — named starting balance of the paper account.
    This is configuration, **not** market data; every valuation below is computed.
  - `spotLedger(balances, prices)`: per asset
    `totalValueUSD = amount * unitPriceUSD`,
    `available = amount - inOrders`,
    `portfolioPercentage = totalValueUSD / spotTotalValueUSD * 100` (0 when the total is 0),
    spot totals `totalValueUSD = Σ`, `cryptoValueUSD = Σ(non-fiat)`,
    `freeCashUSD = Σ(fiat ∪ stablecoin)`,
    `change24hUSD = Σ(amount * (price - open24h))`,
    `change24hPercent = change24hUSD / (spotTotalValueUSD - change24hUSD) * 100`.
  - `proLedger(positions)`: `usedMarginUSD = Σ collateralUSD`,
    `totalUnrealizedPnL = Σ unrealizedPnLUSD`,
    `effectiveLeverage = Σ notionalValueUSD / totalCollateralUSD` (0 when collateral is 0),
    `marginLevelPercent = totalCollateralUSD / usedMarginUSD * 100`,
    per position `unrealizedPnLPercent = unrealizedPnLUSD / collateralUSD * 100` and
    `markPrice`-derived `liquidationPrice` for perpetuals:
    long `entry * (1 - 1/leverage + maintenanceMarginFraction)`,
    short `entry * (1 + 1/leverage - maintenanceMarginFraction)`,
    `MAINTENANCE_MARGIN_FRACTION = 0.005`.
  - `assetType(asset)` — `fiat` / `stablecoin` / `crypto` from named sets
    (`FIAT_ASSETS`, `STABLECOIN_ASSETS`).
- `src/server/kraken/store.ts` — file-backed paper store at `data/kraken-paper-ledger.json`
  (`data/` is already gitignored). Atomic write (tmp + rename). Holds `cash`, `balances`,
  `orders_locked`, `pro_positions`, `last_sync`. Seeds an empty paper account
  (`cash = PAPER_INITIAL_CASH_USD`, no positions) — **no fabricated positions**.
- `src/server/kraken/routes.ts` — the 6 endpoints above, mounted from `server.ts`.
  Every response carries `{ ok, available, reason? }` next to the shape the components
  already read, so no component had to change.
- `scripts/check-kraken.ts` — synthetic-payload assertions (no network), run with
  `npm run check:kraken`.

Fail-closed behaviour, per endpoint:

| Endpoint | Upstream down ⇒ |
| --- | --- |
| `GET /api/kraken/status` | `ok:false, available:false, latencyMs:null, reason` |
| `GET /api/kraken/symbols` | last good cache, else `total:0, symbols:[], quotes:[], popularSymbols:[], available:false` |
| `GET /api/kraken/ledgers` | paper balances with `unitPriceUSD:0`, totals 0, `available:false` |
| `POST /api/kraken/ledgers/sync` | `error` set, previous ledger untouched (no stale write) |
| `GET /api/kraken/positions/pro` | zeros + `positions:[]` + `reason` |
| `POST /api/kraken/sync-balance` | `hasCredentials:false, liveKrakenBalances:null, error` |

### K-2 — Feed freshness + WS ticker (next)

- `GET /api/kraken/ohlcv?pair&interval&count` over `/0/public/OHLC`, **closed bars only**
  (drop the last, still-forming bucket — no look-ahead), mapped to the existing `Candle` type.
- Kraken WS `ticker` subscription for the watchlist, pushed to the UI on the existing
  log-stream socket pattern; `feed.source` transitions `unknown → kraken_ws`.
- `age_s` / `degraded` fields per `SigmaFeedMeta` so the FeedBadge can never read LIVE on
  a stale cache.

### K-3 — Paper execution engine

- `POST /api/kraken/paper/order` (market/limit) validated against `ordermin`/`costmin` and
  `lot`/`pair` decimals from `AssetPairs`; fills at ticker price + named slippage constant.
- Ledger mutation goes through `store.ts`; every fill appended to an audit trail.
- Still no `add_order`, still no credentials.

### K-4 — Safety wiring

- `kraken_rtt_ms` / `kraken_ok` (already typed in `sigmaApi.ts:114`) fed from the real
  `Time` round trip into the Deadman panel; `has_native_stop_loss` stays `false` in paper.
- Rate-limiter counters per Kraken's public tier budget; soft-cap + backoff ladder surfaced
  in the existing Rate Limiter panel.

## 4. Definition of done per phase

1. Every named function computes the formula above (arithmetic implemented, not sketched).
2. `npm run check:kraken` asserts **numeric/boolean** contracts on synthetic payloads.
3. `grep` over new files finds no forbidden token from `AGENTS.md`.
4. `npm run lint` (tsc) **and** `npm run build` green.
5. `curl` smoke test of every route returns the documented fail-closed schema.
