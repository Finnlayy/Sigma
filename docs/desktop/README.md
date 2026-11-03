# SIGMA Ubuntu desktop

## Architecture (integration, not a replacement)

Electron loads the **existing Vite production build** from the **existing FastAPI process**. There is no Vite, npm, pip, Docker, system Redis, or separately started backend required at runtime.

The launcher starts, probes, supervises and stops, in dependency order:

1. A private bundled Redis 7.2.7 instance, authenticated, loopback only, persistent AOF.
2. The existing TradingView scraper sidecar (loopback only).
3. The existing FastAPI composition root: execution/risk engines, ingestion, scheduler, existing in-process agents, and **its own existing TradingView job queue consumer**. A separate worker process would own a different Python queue and is deliberately not used.
4. The existing local Netron visualizer, owned by the Core process. Chromium for TradingView automation and official Kraken CLI 0.4.1 are bundled.

`agents/*.md` are system prompts, not daemon entrypoints. Ollama, Telegram, exchange/public-data APIs and a TradingView MCP endpoint are external integrations, **not services this application invents or impersonates**.

All existing strategy/math/backtest/paper/risk/orchestration modules remain in place. The historical Node `server.ts` mock API has been replaced with a compatibility Vite entrypoint that proxies to real FastAPI. The unused alternate Node ledger remains source code, not a second account book.

### Safety

- Desktop is **paper only**. Live mode, fake MCP, fake TV drivers and synthetic market fallback cannot be enabled through desktop Settings.
- Missing credentials, network, session, models or endpoint configuration do not count as a live connection.
- Every Core HTTP/WebSocket request requires a private launch token injected by Electron, not exposed to renderer JavaScript. Unknown API requests remain 404, not successful SPA HTML.
- Electron uses a sandboxed, context-isolated renderer without Node integration. External web links open in the system browser. No installer disables Chromium's sandbox.
- Unexpected service failure creates the existing `KILL_SWITCH` file, stops dependants and retries the stack at most three times. The halt is **not automatically cleared**. Health monitoring also detects dead Core tasks/job workers.
- Graceful exit stops workers and browser sessions, flushes DuckDB, then stops Redis last. A process-group watchdog terminates descendants if the launcher disappears. SIGKILL/power loss cannot guarantee a final fsync; Redis AOF uses `everysec`.

## Install and launch

Download `SIGMA-1.0.0-amd64.deb` (the filename may use `x64`, depending on builder architecture naming) from the **Ubuntu desktop installers** workflow's `SIGMA-Ubuntu-x64` artifact. Check `SHA256SUMS` before installation.

```sh
sudo apt install ./SIGMA-1.0.0-*.deb
sigma
```

Or use the matching `.AppImage`:

```sh
chmod +x SIGMA-1.0.0-*.AppImage
./SIGMA-1.0.0-*.AppImage
```

Ubuntu 22.04+ x86-64 with a graphical desktop is the build baseline. On installations without FUSE, use `--appimage-extract-and-run`. AppImage does not replace the distribution's GTK/NSS/audio/graphics libraries. The `.deb` declares runtime dependencies.

The SIGMA menu and tray provide show/hide, logs, configuration, **Start at login** (opt-in XDG autostart), restart services and **Quit SIGMA (stop all services)**. On GNOME, tray icons may require the Ubuntu AppIndicator extension. If a tray cannot be created, closing the window quits instead of hiding an inaccessible window.

## First use

1. Open CONFIG → Desktop Services and System Health. An unconfigured backtest or unavailable market feed is expected and explicitly reported.
2. In Pine Studio use the existing TradingView login action. Sign in yourself in the visible bundled browser. Session cookies are saved privately; they are not shipped in the installer.
3. Configure a **real** `SIGMA_TV_MCP_URL` through Settings for MCP backtests/GA. TradingView browser automation uses the existing selector/CSV path and requires an appropriate account/plan. There is no local-engine fallback.
4. Restart services after changing connection configuration or establishing a TV session. Existing hot-reload risk settings remain supported. A restart intentionally halts execution; review existing risk controls before clearing the halt.
5. Kraken paper books are deliberately separate from a host's Kraken credentials/configuration. No funded account, exchange history or repository dataset is copied into a fresh installation. The official CLI and current paper-book initialization are exposed through Desktop Services; actual fills still require real market data.
6. Configure optional Telegram and an existing Ollama installation/model if those features are required. Neither is presented as connected before a successful request.

### Persistent files

Default workspace: `~/.config/SIGMA/runtime/` (Electron's XDG user-data location).

- `.env`: existing Settings manager's persistent configuration, private directory, never included in packages.
- `data/`: DuckDB, lake, AOF, jobs, exports, logs, secrets, safety signals.
- `cli-config/kraken/`: desktop-owned official Kraken paper journals, no reuse of host exchange credentials.
- `models/`, `config/`, `app/tv/selectors.yaml`: initial copies from the repository, existing user copies preserved on upgrade.
- Layout remains the existing renderer localStorage layout.

Back up the whole workspace while SIGMA is stopped. Uninstallation does not delete it. `SIGMA_DESKTOP_WORKSPACE` can explicitly select another private workspace (useful for testing). Concurrent desktop launch is prevented with Electron's single-instance lock.

## Build and verification

```sh
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-desktop.lock
npm ci
.venv/bin/python -m pytest tests/ -q
npm run lint
npm run test:desktop
scripts/build-desktop.sh
```

Build dependencies: shared-library Python 3.11, Node 22, gcc/make, desktop libraries, and internet for signed/version-pinned upstream distributions. `scripts/build-desktop.sh` validates pinned Redis/CLI archive hashes, bundles Playwright Chromium, freezes the existing Python modules/resources, runs real service smoke tests, and builds both installers. It never runs `pip`/`npm` at application startup.

`.github/workflows/ubuntu-desktop.yml` performs this on Ubuntu 22.04, runs all tests, probes the frozen services, launches the **packaged** Electron renderer under Xvfb, checks `.deb` metadata and launches the extracted AppImage. Installers are uploaded **only if all gates pass**. No automated merge or release publishing is configured.

## Known external blockers / verification boundary

- This Arena sandbox blocks GitHub release-asset redirects and browser downloads, and its system Python lacks `libpython3.11.so`. Thus **native installer creation and graphical packaged launch cannot be claimed from this sandbox**. The real GitHub Actions job is the delivery path; consult its result rather than assuming a queued build succeeded.
- This environment cannot reach Kraken, TradingView, Telegram or download an Ollama model. No user TV session/MCP service was supplied. Authenticated end-to-end TV backtests, external webhooks, exchange fills, Telegram sends and LLM responses require those real services; unit tests are not evidence of live connectivity.
- External TradingView webhooks require a configured public ingress. The default private desktop API accepts only the Electron session, so blindly pointing a tunnel at it is **not** an enabled webhook connection. Do not expose unrelated settings/control APIs.
- Existing optional research modules and missing model/regime artifacts fail closed; packaging does not fabricate model weights or promise economic performance.
- Upstream dependency audit warnings are tracked in the audit; no broad strategy/UI rewrites are performed to silence them.
