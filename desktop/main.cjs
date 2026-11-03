const { app, BrowserWindow, Menu, Tray, dialog, shell, session } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { Supervisor } = require('./supervisor.cjs');

app.setName('SIGMA');
const locked = app.requestSingleInstanceLock();
if (!locked) app.quit();
let window, tray, supervisor, quitting = false, recovery = null, failures = 0, currentOrigin = '';
const icon = path.join(__dirname, 'icon.png');
const show = () => { if (window) { window.show(); window.focus(); } };
app.on('second-instance', show);

function configureSession() {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  // No Node APIs or token are exposed to the renderer. Include WebSocket handshakes.
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const httpURL = details.url.replace(/^ws:/, 'http:');
    if (currentOrigin && new URL(httpURL).origin === currentOrigin) {
      details.requestHeaders['X-Sigma-Desktop'] = supervisor.token;
    }
    callback({ requestHeaders: details.requestHeaders });
  });
}
async function start() {
  await window.loadFile(path.join(__dirname, 'loading.html'));
  currentOrigin = await supervisor.start();
  await window.loadURL(currentOrigin);
  if (process.env.SIGMA_DESKTOP_SMOKE === '1') {
    const status = await window.webContents.executeJavaScript("fetch('/api/desktop/status').then(r => r.json())");
    if (!status.desktop || status.redis !== 'persistent' || !status.worker.running) throw new Error('Packaged renderer smoke failed');
    const panels = await window.webContents.executeJavaScript("Boolean(document.querySelector('button'))");
    if (!panels) throw new Error('React terminal did not mount');
    console.log('SIGMA_DESKTOP_SMOKE_OK');
    app.quit();
  }
}
async function recover(service, error) {
  if (quitting || supervisor.stopping || recovery) return;
  recovery = (async () => {
    supervisor.halt(`${service} failed: ${error.message}`);
    await supervisor.stop();
    if (++failures <= 3) {
      await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** failures));
      if (quitting) return;
      try { await start(); return; } catch (next) { error = next; }
    }
    if (!quitting) {
      dialog.showErrorBox('SIGMA services stopped', `${error.message}\n\nExecution is halted. Logs: ${supervisor.workspace}/data/logs\nUse System → Restart services after resolving the error.`);
    }
  })().finally(() => { recovery = null; });
  await recovery;
}
function setAutostart(enabled) {
  const directory = path.join(app.getPath('appData'), 'autostart');
  const file = path.join(directory, 'sigma.desktop');
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (!enabled) { fs.rmSync(file, { force: true }); return; }
  // AppImage needs its persistent outer path, not a temporary mount executable.
  const executable = process.env.APPIMAGE || process.execPath;
  if (/[\n\r]/.test(executable)) throw new Error('Invalid executable path');
  const quoted = executable.replace(/([\\"`$])/g, '\\$1').replace(/%/g, '%%');
  fs.writeFileSync(file, `[Desktop Entry]\nType=Application\nName=SIGMA\nExec="${quoted}" --hidden\nTerminal=false\nX-GNOME-Autostart-enabled=true\n`, { mode: 0o600 });
}
function menu() {
  const autostart = fs.existsSync(path.join(app.getPath('appData'), 'autostart/sigma.desktop'));
  return Menu.buildFromTemplate([
    { label: 'SIGMA', submenu: [
      { label: 'Show Command Center', click: show },
      { label: 'Open logs', click: () => shell.openPath(path.join(supervisor.workspace, 'data/logs')) },
      { label: 'Open configuration folder', click: () => shell.openPath(supervisor.workspace) },
      { type: 'separator' },
      { label: 'Start at login', type: 'checkbox', checked: autostart, click: item => setAutostart(item.checked) },
      { label: 'Restart services', click: async () => {
        if (recovery) return;
        const answer = await dialog.showMessageBox(window, { type: 'warning', buttons: ['Cancel', 'Restart'],
          message: 'Restart all SIGMA services?', detail: 'Execution will be halted. Paper state and history are preserved.' });
        if (answer.response !== 1) return;
        supervisor.halt('Operator restart');
        await supervisor.stop(); failures = 0;
        try { await start(); } catch (error) { dialog.showErrorBox('SIGMA startup failed', error.message); }
      } },
      { type: 'separator' }, { label: 'Quit SIGMA (stop all services)', click: () => app.quit() },
    ] },
    { label: 'View', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] },
    { role: 'editMenu' },
  ]);
}

if (locked) app.whenReady().then(async () => {
  const workspace = process.env.SIGMA_DESKTOP_WORKSPACE || path.join(app.getPath('userData'), 'runtime');
  supervisor = new Supervisor({ workspace,
    resources: app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '../build'),
    python: app.isPackaged ? undefined : process.env.SIGMA_PYTHON || path.resolve(__dirname, '../.venv/bin/python'),
    sourceRoot: app.isPackaged ? undefined : path.resolve(__dirname, '..'),
  });
  supervisor.on('failure', (service, error) => { void recover(service, error); });
  configureSession();
  window = new BrowserWindow({ width: 1500, height: 960, minWidth: 900, minHeight: 600, icon,
    show: !process.argv.includes('--hidden'),
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true },
  });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.on('will-navigate', (event, url) => {
    if (currentOrigin && new URL(url).origin !== currentOrigin) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (['https:', 'http:'].includes(new URL(url).protocol)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.on('close', event => {
    if (!quitting && tray) { event.preventDefault(); window.hide(); }
  });
  const appMenu = menu();
  Menu.setApplicationMenu(appMenu);
  try {
    tray = new Tray(icon);
    tray.setToolTip('SIGMA — Paper Trading');
    tray.setContextMenu(appMenu);
    tray.on('click', show);
  } catch { /* No indicator host: closing the window quits instead of hiding it. */ }
  try { await start(); }
  catch (error) { dialog.showErrorBox('SIGMA could not start', `${error.message}\nLogs: ${workspace}/data/logs`); app.quit(); }
}).catch(error => { console.error(error); app.exit(1); });

app.on('window-all-closed', () => { if (!tray) app.quit(); });
app.on('before-quit', event => {
  if (!quitting && supervisor) {
    event.preventDefault(); quitting = true;
    void (async () => {
      if (recovery) await recovery;
      await supervisor.stop();
      tray?.destroy(); app.quit();
    })().catch(error => { console.error(error); app.exit(1); });
  }
});
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => app.quit());
