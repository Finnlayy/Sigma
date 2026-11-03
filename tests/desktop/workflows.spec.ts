/** Genuine renderer-to-Core workflows. No HTTP route interception or fake servers. */
import { test, expect, _electron as electron } from '@playwright/test';
import { mkdtemp, rm, cp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

const presets = ['BOT COCKPIT', 'PINE IDE', 'RISK RADAR', 'SENTINEL OPS', 'CAPITAL OPS',
  'PAPER LAB', 'OBSERVABILITY', 'ML INSPECTOR', 'OVERVIEW', 'LIBRARY', 'QUANT', 'CONFIG',
  'QUANTUM OPS', 'POSITION DESK', 'RESEARCH LAB'];

test('existing navigation, CSV results, Settings, persistence and paper-only guards', async () => {
  const workspace = await mkdtemp(path.join(tmpdir(), 'sigma-e2e-'));
  const executablePath = process.env.SIGMA_E2E_EXECUTABLE;
  const application = await electron.launch({
    ...(executablePath ? { executablePath } : {}),
    args: executablePath ? [] : ['.'],
    env: { ...process.env, SIGMA_DESKTOP_WORKSPACE: workspace,
      SIGMA_SCRAPER_OFFLINE: '1', SIGMA_TV_MCP_URL: '', SIGMA_DESKTOP_SMOKE: '0' },
    timeout: 200_000,
  });
  const window = await application.firstWindow();
  const errors: string[] = [];
  const pageErrors: string[] = [];
  window.on('pageerror', error => { pageErrors.push(error.message); errors.push(error.stack || error.message); });
  window.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  try {
    await expect(window.getByRole('radio', { name: 'BOT COCKPIT', exact: true })).toBeVisible({ timeout: 200_000 });
    for (const preset of presets) {
      const toggle = window.getByRole('radio', { name: preset, exact: true });
      await toggle.click();
      await expect(toggle).toHaveAttribute('data-state', 'on');
      await expect(window.getByText('Projekt:Sigma — Interface Interruption', { exact: true })).toHaveCount(0);
      if (preset === 'OVERVIEW') {
        // Wait for genuine async matrix data, then exercise both one- and two-matrix renders.
        await expect(window.getByText('Queue Performance Matrices', { exact: true })).toBeVisible();
        await expect(window.getByText('Compiling all-time queue performance matrix...', { exact: true })).toHaveCount(0);
        await window.getByRole('button', { name: 'Dual Comparison', exact: true }).click();
        await expect(window.getByText('L2 Paper Execution Queue Matrix', { exact: true })).toBeVisible();
        await expect(window.getByText('L4 Live Execution Queue Matrix', { exact: true })).toBeVisible();
        await window.getByRole('button', { name: 'L4 Live Queue Matrix', exact: true }).click();
        await window.getByRole('button', { name: 'L2 Paper Queue Matrix', exact: true }).click();
      }
    }
    await window.getByRole('radio', { name: 'CONFIG', exact: true }).click();
    await window.getByRole('tab', { name: /^Desktop Services/ }).click();
    await expect(window.getByText('Ubuntu Desktop · Paper only', { exact: true })).toBeVisible();
    await expect(window.getByText('persistent', { exact: true })).toBeVisible();
    await expect(window.getByText('Unavailable — set a real SIGMA_TV_MCP_URL', { exact: true })).toBeVisible();
    const marketSocket = await window.evaluate(() => new Promise<{ type: string; channels?: string[] }>((resolve, reject) => {
      const socket = new WebSocket(`ws://${location.host}/ws/market-feed/BTC%2FUSD?interval=15`);
      const timer = setTimeout(() => { socket.close(); reject(new Error('Market WebSocket timed out')); }, 10_000);
      socket.onmessage = event => { clearTimeout(timer); resolve(JSON.parse(event.data)); socket.close(); };
      socket.onerror = () => { clearTimeout(timer); reject(new Error('Market WebSocket handshake failed')); };
    }));
    expect(['init', 'error']).toContain(marketSocket.type); // Offline error is honest; a failed handshake is not.
    expect(marketSocket.channels).toContain('market:candles:BTC/USD');

    const operator = await window.evaluate(async () => {
      const blocked = await fetch('/api/v1/auth/passkey/challenge');
      const authorized = await fetch('/api/desktop/operator/authorize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation: 'AUTHORIZE PAPER OPERATOR' }),
      });
      const result = await authorized.json();
      // No TV session exists in this clean workspace: 503 means operator auth passed,
      // while the real external operation correctly remains unavailable.
      const attach = await fetch('/api/v1/alerts/driver/attach', {
        method: 'POST', headers: { 'X-Sigma-Settings-Token': result.settingsToken },
      });
      return { blocked: blocked.status, mode: result.mode, attach: attach.status };
    });
    expect(operator).toEqual({ blocked: 503, mode: 'desktop-session', attach: 503 });

    const guard = await window.evaluate(async () => {
      const live = await fetch('/api/kraken/toggle-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paperTrading: false }) });
      const fake = await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: 'SIGMA_TV_MCP_URL', value: 'fake' }) });
      return [live.status, fake.status];
    });
    expect(guard).toEqual([403, 400]);

    await window.getByRole('tab', { name: /^Settings/ }).click();
    // Existing setting-row save is exercised through its actual UI, not a test-only API.
    const save = window.getByRole('button', { name: 'Save ALPHA_BASE_BUDGET_USD', exact: true });
    const row = save.locator('..');
    await row.locator('input').fill('73');
    await save.click();
    await expect(row.locator('input')).toHaveValue('73');

    // Backtesting is already in the panel registry; add it via existing dock navigation.
    await window.getByRole('button', { name: /Panel$/ }).click();
    await window.getByRole('menuitem', { name: 'Backtest', exact: true }).click();
    await window.getByRole('tab', { name: /^Backtest/ }).click();
    const csv = 'Trade #,Type,Date/Time,Signal,Price USD,Position size (qty),Net P&L USD\n1,Entry long,2024-06-01 10:00,long,100,1,0\n1,Exit long,2024-06-01 14:00,long,110,1,9.8\n';
    await window.getByLabel('Import TradingView result CSV').setInputFiles({ name: 'operator-results.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
    await expect(window.getByText(/tradingview-csv/).first()).toBeVisible();
    // An offline run must report an error, not turn imported history into a live backtest.
    await window.getByRole('button', { name: 'RUN TV BACKTEST', exact: true }).click();
    await expect(window.getByText(/TV MCP error:/)).toBeVisible();
    expect(pageErrors).toEqual([]);
    // HTTP/CSP console diagnostics are retained below; uncaught page errors remain a gate.
    const interruption = window.getByText('Projekt:Sigma — Interface Interruption', { exact: true });
    await expect(interruption).toHaveCount(0);
  } catch (error) {
    const body = await window.locator('body').innerText({ timeout: 5000 }).catch(() => 'Renderer unavailable');
    await writeFile(test.info().outputPath('renderer-state.txt'), body + '\n\nRenderer errors:\n' + errors.join('\n'));
    await window.screenshot({ path: test.info().outputPath('renderer.png'), timeout: 5000 }).catch(() => {});
    throw error;
  } finally {
    if (test.info().status !== test.info().expectedStatus) {
      await cp(path.join(workspace, 'data/logs'), test.info().outputPath('runtime-logs'), { recursive: true }).catch(() => {});
    }
    // Application.close can otherwise behave like hide-to-tray; explicitly use app.quit.
    await application.evaluate(({ app }) => app.quit()).catch(() => {});
    await application.close().catch(() => {});
    await rm(workspace, { recursive: true, force: true });
  }
});
