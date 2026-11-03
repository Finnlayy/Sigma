"""Launch actual managed services; verify storage, UI/API, safety, and clean exit.

No fake upstream is started. Unconfigured integrations must remain unavailable.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
const fs = require('node:fs');
const path = require('node:path');
const { Supervisor } = require('./desktop/supervisor.cjs');
(async () => {
  const supervisor = new Supervisor({
    workspace: process.env.SMOKE_WORKSPACE, resources: path.resolve('build'),
    python: process.env.SIGMA_SMOKE_FROZEN === '1' ? undefined : process.env.SMOKE_PYTHON,
    sourceRoot: process.cwd(),
    backendExecutable: path.resolve("build/backend/sigma-backend/sigma-backend"),
  });
  const failures = [];
  supervisor.on('failure', (service, error) => failures.push(`${service}: ${error.message}`));
  try {
    const origin = await supervisor.start();
    const headers = { 'X-Sigma-Desktop': supervisor.token };
    const request = (route, init = {}) => fetch(origin + route, { ...init, headers: { ...headers, ...init.headers } });
    if ((await fetch(origin + '/api/strategies')).status !== 403) throw Error('Session isolation failed');
    if ((await request('/')).status !== 200) throw Error('Static UI unavailable');
    if ((await request('/api/does-not-exist')).status !== 404) throw Error('Unknown API masked');
    const status = await (await request('/api/desktop/status')).json();
    if (status.mode !== 'paper' || status.redis !== 'persistent' || !status.worker.running || !status.tasks_alive) throw Error(JSON.stringify(status));
    if (status.backtest_available) throw Error('An unconfigured backtest was presented as available');
    const strategies = await (await request('/api/strategies')).json();
    if (!Array.isArray(strategies)) throw Error('Strategy API unavailable');
    const mode = await request('/api/kraken/toggle-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paperTrading: false }) });
    if (mode.status !== 403) throw Error('Live mode was not blocked');
    const settings = await request('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: 'ALPHA_BASE_BUDGET_USD', value: '73' }) });
    if (!settings.ok) throw Error('Settings mutation failed: ' + await settings.text());
    if (!fs.readFileSync(path.join(supervisor.workspace, '.env'), 'utf8').includes('ALPHA_BASE_BUDGET_USD=73')) throw Error('Settings not persisted');
    if (status.kraken_cli_available) {
      for (const book of ['spot', 'futures']) {
        const initialized = await request('/api/desktop/paper/initialize', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ book, balance: 10000, confirmation: 'INITIALIZE PAPER BOOK' }),
        });
        if (!initialized.ok) throw Error('Official CLI paper initialization failed: ' + await initialized.text());
      }
    }
    const children = supervisor.children.map(r => r.child.pid);
    await supervisor.stop();
    for (const pid of children) {
      let alive = false; try { process.kill(pid, 0); alive = true; } catch {}
      if (alive) throw Error('Orphan service: ' + pid);
    }
    // AOF survives a complete supervised stack restart; do not import repository account data.
    await supervisor.start();
    const saved = await fetch(supervisor.origin + '/api/settings', { headers });
    const payload = await saved.json();
    if (!payload.settings.some(s => s.key === 'ALPHA_BASE_BUDGET_USD' && s.value === '73')) throw Error('Configuration lost after restart');
    if (failures.length) throw Error(failures.join('\n'));
    console.log(JSON.stringify({ passed: true, mode: status.mode, redis: status.redis, strategies: strategies.length, upstream: 'not configured; no simulated success' }));
  } finally { await supervisor.stop(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


def main():
    with tempfile.TemporaryDirectory(prefix='sigma-smoke-') as directory:
        env = dict(os.environ, SMOKE_WORKSPACE=directory, SMOKE_PYTHON=sys.executable,
                   SIGMA_TV_MCP_URL='', SIGMA_SCRAPER_OFFLINE='1')
        result = subprocess.run(['node', '-e', SCRIPT], cwd=ROOT, env=env)
        if result.returncode:
            for file in Path(directory, 'data/logs').glob('*.log'):
                print(f'--- {file.name} ---', file=sys.stderr)
                print(file.read_text(errors='replace')[-12000:], file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
