const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const crypto = require('node:crypto');

const delay = (ms, unref = false) => new Promise(resolve => {
  const timer = setTimeout(resolve, ms);
  if (unref) timer.unref();
});
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

class Supervisor extends EventEmitter {
  constructor({ workspace, resources, python, sourceRoot, backendExecutable }) {
    super();
    this.workspace = workspace;
    this.resources = resources;
    this.python = python;
    this.backendExecutable = backendExecutable;
    this.sourceRoot = sourceRoot;
    this.children = [];
    this.stopping = false;
    this.token = crypto.randomBytes(32).toString('hex');
  }
  launch(service, port, env) {
    const executable = this.python || this.backendExecutable || path.join(this.resources, 'backend/sigma-backend');
    const prefix = this.python ? ['-m', 'desktop.backend'] : [];
    const args = [...prefix, '--service', service, '--workspace', this.workspace,
      '--port', String(port), '--parent-pid', String(process.pid)];
    if (service === 'redis') args.push('--redis-binary', path.join(this.resources, 'redis/redis-server'));
    const logs = path.join(this.workspace, 'data/logs');
    fs.mkdirSync(logs, { recursive: true, mode: 0o700 });
    const log = path.join(logs, service === 'core' ? 'sigma_core.log' : `${service}.log`);
    // Bound log growth across launches; the API's own order/error journals are preserved.
    if (fs.existsSync(log) && fs.statSync(log).size > 10 * 1024 * 1024) fs.renameSync(log, `${log}.previous`);
    const fd = fs.openSync(log, 'a', 0o600);
    let child;
    try {
      child = spawn(executable, args, {
        cwd: this.workspace, detached: true, stdio: ['ignore', fd, fd],
        env: { ...process.env, ...env, PYTHONPATH: this.sourceRoot || '', PYTHONUNBUFFERED: '1' },
      });
    } finally { fs.closeSync(fd); }
    const record = { service, child, exited: false };
    this.children.push(record);
    child.once('error', error => { record.exited = true; record.error = error; if (!this.stopping && !this.starting) this.emit('failure', service, error); });
    child.once('exit', (code, signal) => {
      record.exited = true;
      if (!this.stopping && !this.starting) this.emit('failure', service, new Error(`${service} exited (${code ?? signal}). See ${log}`));
    });
    return record;
  }
  async ready(record, probe, timeout = 90000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (this.stopping) throw new Error('Startup cancelled');
      if (record.exited) throw record.error || new Error(`${record.service} stopped during startup`);
      try { if (await probe()) return; } catch { /* transient until deadline */ }
      await delay(200);
    }
    throw new Error(`${record.service} readiness timed out. Check data/logs in ${this.workspace}`);
  }
  async start() {
    this.stopping = false;
    this.starting = true;
    fs.mkdirSync(this.workspace, { recursive: true, mode: 0o700 });
    fs.chmodSync(this.workspace, 0o700);
    const redisPort = await freePort();
    const scraperPort = await freePort();
    const corePort = await freePort();
    this.origin = `http://127.0.0.1:${corePort}`;
    const env = {
      SIGMA_DESKTOP: '1', SIGMA_DESKTOP_TOKEN: this.token,
      SIGMA_REDIS_URL: `redis://:${this.token}@127.0.0.1:${redisPort}/0`,
      SIGMA_REDIS_PASSWORD: this.token,
      SIGMA_SCRAPER_PORT: String(scraperPort),
      SIGMA_TV_SCRAPER_URL: `http://127.0.0.1:${scraperPort}`,
      PATH: `${path.join(this.resources, 'kraken')}${path.delimiter}${process.env.PATH || ''}`,
      PLAYWRIGHT_BROWSERS_PATH: path.join(this.resources, 'browsers'),
    };
    try {
      const redis = this.launch('redis', redisPort, env);
      await this.ready(redis, () => new Promise((resolve, reject) => {
        const socket = net.createConnection({ host: '127.0.0.1', port: redisPort });
        socket.setTimeout(1500);
        socket.once('connect', () => socket.write(`AUTH ${this.token}\r\nPING\r\n`));
        let response = '';
        socket.on('data', data => {
          response += data.toString();
          if (response.includes('+PONG')) { socket.destroy(); resolve(true); }
          else if (response.startsWith('-')) { socket.destroy(); resolve(false); }
        });
        socket.once('timeout', () => { socket.destroy(); reject(new Error('Redis timeout')); });
        socket.once('error', reject);
      }));
      const scraper = this.launch('scraper', scraperPort, env);
      // Readiness is transport readiness, NOT a claim of upstream connectivity.
      await this.ready(scraper, async () => (await fetch(`http://127.0.0.1:${scraperPort}/health`, { signal: AbortSignal.timeout(3000) })).ok);
      const core = this.launch('core', corePort, env);
      await this.ready(core, async () => {
        const response = await fetch(`${this.origin}/api/desktop/status`, {
          headers: { 'X-Sigma-Desktop': this.token }, signal: AbortSignal.timeout(3000),
        });
        const data = response.ok ? await response.json() : null;
        return data?.desktop === true && data.redis === 'persistent' && data.worker.running;
      }, 180000);
      let missed = 0;
      this.monitor = setInterval(async () => {
        if (this.stopping || this.checking) return;
        this.checking = true;
        try {
          const response = await fetch(`${this.origin}/api/desktop/status`, {
            headers: { 'X-Sigma-Desktop': this.token }, signal: AbortSignal.timeout(4000),
          });
          const status = response.ok ? await response.json() : null;
          if (!status?.worker.running || !status?.tasks_alive) throw new Error('Core worker/task unhealthy');
          missed = 0;
        } catch (error) {
          if (++missed >= 3 && !this.stopping) this.emit('failure', 'core-health', error);
        } finally { this.checking = false; }
      }, 5000);
      this.monitor.unref();
      return this.origin;
    } catch (error) { await this.stop(); throw error; }
    finally { this.starting = false; }
  }
  halt(reason) {
    const signals = path.join(this.workspace, 'data/signals');
    fs.mkdirSync(signals, { recursive: true, mode: 0o700 });
    fs.writeFileSync(path.join(signals, 'KILL_SWITCH'), `${new Date().toISOString()} ${reason}\n`, { mode: 0o600 });
  }
  async stop() {
    this.stopping = true;
    clearInterval(this.monitor);
    // Core flushes and joins workers first; Redis gets SIGTERM last for AOF sync.
    for (const record of [...this.children].reverse()) {
      if (record.exited || !record.child.pid) continue;
      const finished = new Promise(resolve => record.child.once('exit', resolve));
      try { process.kill(-record.child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      await Promise.race([finished, delay(20000, true)]);
      if (!record.exited) {
        try { process.kill(-record.child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
        await Promise.race([finished, delay(2000, true)]);
      }
    }
    this.children = [];
  }
}
module.exports = { Supervisor, freePort };
