# Desktop integration audit — 2026-11-03

## Scope and sources

Base: `cc6604bf4df16e11f92a104bb81006f848661c91` (`main`), work branch `arena/205890b2-sigma`.

Fetched all remote heads without switching branches. [Branch inventory](branch-inventory.json) records 125 branch heads plus origin/HEAD, their tip SHAs and differences from main. No Electron/Tauri implementation exists on these heads; the large `claude-desktop*.deb.part-*` files are an unrelated product, **not SIGMA installers**. Historical branches were inventoried, not blindly merged. Current main is the source of truth.

[Module inventory](module-inventory.md) covers app/, sigma/, route definitions and renderer entry/registry. Read the composition root, scheduler/worker/service lifecycle, feeds, backtest/GA seam, settings, CLI, WebSocket and existing Ubuntu deployment entrypoints. All existing tests were executed, not replaced with desktop-only tests.

## Genuine gaps and fixes

| Finding | Integration response |
|---|---|
| No native application manifest/build | Electron 45 + electron-builder, real `.deb`/AppImage pipeline |
| Separate manual development services | Bundled dependencies, dependency-ordered readiness/supervision/exit |
| API and TV worker in separate Python queues | Start the existing queue consumer inside Core, where jobs are submitted |
| Vite only proxies `/api`, market WebSocket is `/ws/...` | Proxy both namespaces; production UI shares the Core origin |
| Market WebSocket calls `get_redis()` without required config | Pass actual config; HTTP feed operations run off event loop |
| Legacy Node preview returns mocked success | Remove mock endpoints, use existing real Vite proxy; don't mount a second ledger |
| Synthetic data between real scraper polls / when offline | Strict desktop mode emits no fabricated ticks or history |
| Fake MCP/TV driver default without a session | Reject fake MCP and use real TV driver that fails on missing session |
| TV driver factory error can kill the queue thread before try/except | Persist a failed job, retain worker liveness |
| Health treats a jobs directory as a running worker | Report actual queue thread state |
| Writable paths assume source checkout | Private persistent workspace; copied initial configs/models/selectors, no account datasets |
| No authentication boundary for a privileged local desktop API | Per-launch secret for HTTP and WS, Redis authentication, no renderer Node APIs |
| Settings claims no backend despite implemented persistence | Correct existing UI copy; preserve existing settings manager |
| Single-quoted import assertion fails after harmless formatting | Accept either quote style, same twelve-panel registry assertions |
| API tests react to actual host memory and leave PAUSE | Inject deterministic memory input only in lifespan API tests; production watchdog unchanged |

## Verification so far

- Baseline: 912 passed, 1 skipped, 1 failed (quote-style assertion). Existing TypeScript check and production Vite build pass.
- Integration: 918 passed, 1 skipped; 4 Node supervisor tests pass; TypeScript and production Vite build pass.
- Real local service smoke: compiled actual Redis, launched actual scraper/Core/queue, checked authenticated API + UI assets, unknown-route 404, live-mode denial, persistent Settings across complete restart, no orphan processes on shutdown. External upstreams intentionally offline, no fabricated connection success.
- Native local freeze attempt: blocked by missing shared libpython. Electron download attempt: blocked at GitHub asset redirect. CI must supply native verification; no installer is asserted to exist until its artifact is uploaded.

The original suite has one existing skipped test and a Starlette/httpx deprecation warning. Vite retains its original large-bundle warning; code splitting is not required to package existing functionality. `npm audit fix` applied compatible fixes (including the original critical proxy-addr finding). Remaining major-version-only/tooling advisories are not a license to redesign dependencies. Renderer code is compiled; npm runtime dependencies are excluded from the native package.

See [desktop guide](README.md) for external connectivity prerequisites and honest verification limits.
