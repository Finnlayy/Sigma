const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { Supervisor, freePort } = require('../../desktop/supervisor.cjs');

test('OS assigns loopback ports', async () => {
  const port = await freePort();
  assert.ok(port > 0 && port < 65536);
});
test('ready rejects a failed child rather than accepting an unrelated service', async () => {
  const supervisor = new Supervisor({ workspace: '/unused' });
  await assert.rejects(supervisor.ready({ service: 'core', exited: true }, async () => true), /stopped/);
});
test('halt is persistent and private', () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'sigma-test-'));
  const supervisor = new Supervisor({ workspace });
  supervisor.halt('child failed');
  const file = path.join(workspace, 'data/signals/KILL_SWITCH');
  assert.match(fs.readFileSync(file, 'utf8'), /child failed/);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  fs.rmSync(workspace, { recursive: true });
});
test('shutdown reaps real process groups, is idempotent and follows reverse dependency order', async () => {
  const supervisor = new Supervisor({ workspace: '/unused' });
  const exited = [];
  for (const service of ['redis', 'scraper', 'core']) {
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { detached: true, stdio: 'ignore' });
    const record = { service, child, exited: false };
    child.on('exit', () => { record.exited = true; exited.push(service); });
    supervisor.children.push(record);
  }
  await supervisor.stop();
  assert.deepEqual(exited, ['core', 'scraper', 'redis']);
  assert.deepEqual(supervisor.children, []);
  await supervisor.stop();
});
