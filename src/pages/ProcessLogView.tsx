/**
 * =========================================================
 * Datei:      src/pages/ProcessLogView.tsx
 * Zweck:      §37 — Live Process & AI Log Console.
 *             Route /logs und dockbares FlexLayout-Panel.
 *             WS /api/v1/logs/stream mit HTTP-Poll-Fallback.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, Pause, Play, RefreshCw, Trash2 } from 'lucide-react';
import { sigmaApi, type LogLine, type LogSources } from '../lib/sigmaApi';

export const RING_BUFFER_LINES = 2000;

export const LEVEL_COLOR: Record<string, string> = {
  CRITICAL: 'text-rose-400',
  ERROR: 'text-rose-400',
  WARNING: 'text-amber-400',
  INFO: 'text-zinc-300',
  DEBUG: 'text-zinc-500',
};

export const SUBSYSTEM_COLOR: Record<string, string> = {
  AI_LAYER: 'text-fuchsia-400',
  ORDERS: 'text-emerald-400',
  TV_WORKER: 'text-sky-400',
  ERRORS: 'text-rose-400',
  CORE: 'text-zinc-400',
  SCRAPER: 'text-amber-300',
};

export function matches(line: LogLine, subsystems: string[], search: string): boolean {
  if (subsystems.length && !subsystems.includes(line.subsystem)) return false;
  if (!search) return true;
  try {
    return new RegExp(search, 'i').test(line.raw_line);
  } catch {
    return line.raw_line.toLowerCase().includes(search.toLowerCase());
  }
}

export default function ProcessLogView() {
  const [sources, setSources] = useState<LogSources | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [paused, setPaused] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const filterParam = useMemo(() => selected.join(','), [selected]);

  const push = useCallback((incoming: LogLine[]) => {
    if (!incoming.length || pausedRef.current) return;
    setLines((prev) => [...prev, ...incoming].slice(-RING_BUFFER_LINES));
  }, []);

  useEffect(() => { void sigmaApi.logSources().then((s) => s && setSources(s)); }, []);

  // WS-Stream mit HTTP-Poll-Fallback (§37.2 / §37.5)
  useEffect(() => {
    setLines([]);
    let ws: WebSocket | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let closed = false;

    const startPolling = () => {
      if (poll) return;
      void sigmaApi.logTail(filterParam, 200).then((r) => r && push(r.lines));
      poll = setInterval(() => {
        // Bolt: WS-fallback polls /api/v1/logs/poll every 1s. A hidden tab
        // used to keep the HTTP fallback hot (~60 GETs/min). Skip until focus.
        if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
        void sigmaApi.logPoll(filterParam).then((r) => r && push(r.lines));
      }, 1000);
    };

    try {
      ws = new WebSocket(sigmaApi.logStreamUrl(filterParam));
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => { try { push([JSON.parse(ev.data) as LogLine]); } catch { /* noop */ } };
      ws.onerror = () => { if (!closed) { setConnected(false); startPolling(); } };
      ws.onclose = () => { if (!closed) { setConnected(false); startPolling(); } };
    } catch {
      startPolling();
    }
    return () => {
      closed = true;
      if (poll) clearInterval(poll);
      ws?.close();
    };
  }, [filterParam, push]);

  useEffect(() => {
    if (autoScroll && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines, autoScroll]);

  const visible = useMemo(
    () => lines.filter((l) => matches(l, selected, search)),
    [lines, selected, search],
  );

  const toggle = (name: string) =>
    setSelected((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]));

  const download = () => {
    const blob = new Blob([visible.map((l) => JSON.stringify(l)).join('\n')],
      { type: 'application/x-ndjson' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'sigma_logs.jsonl'; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full flex-col bg-[#0e1117] text-[12px] text-zinc-200">
      <div className="flex flex-wrap items-center gap-1 border-b border-zinc-800 px-2 py-1.5">
        <span className="mr-1 text-[11px] font-semibold text-zinc-300">Process &amp; AI Logs</span>
        <span className={`rounded px-1 text-[9px] font-bold ${connected ? 'bg-emerald-600/70' : 'bg-zinc-700'}`}>
          {connected ? 'WS LIVE' : 'POLL'}
        </span>
        {(sources?.sources ?? []).map((s) => (
          <button key={s.subsystem} onClick={() => toggle(s.subsystem)}
            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
              selected.includes(s.subsystem) ? 'bg-zinc-200 text-zinc-900' : 'bg-zinc-800 text-zinc-400'}`}
            title={s.path}>
            {s.subsystem}
          </button>
        ))}
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Volltext / Regex"
          className="ml-1 w-40 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-[11px] outline-none focus:border-zinc-600" />
        <div className="ml-auto flex items-center gap-1">
          <label className="flex items-center gap-1 text-[10px] text-zinc-400">
            <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
            auto-scroll
          </label>
          <button onClick={() => setPaused((p) => !p)} title={paused ? 'Fortsetzen' : 'Pause'}
            className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">
            {paused ? <Play size={12} /> : <Pause size={12} />}
          </button>
          <button onClick={download} title="Sichtbare Logs exportieren"
            className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"><Download size={12} /></button>
          <button onClick={() => setLines([])} title="View leeren"
            className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"><Trash2 size={12} /></button>
          <button onClick={() => void sigmaApi.logTail(filterParam, 200).then((r) => r && setLines(r.lines))}
            title="Backfill" className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">
            <RefreshCw size={12} />
          </button>
        </div>
      </div>
      <div ref={boxRef} className="flex-1 overflow-auto px-2 py-1 font-mono text-[11px] leading-[1.35]">
        {visible.map((l, i) => (
          <div key={`${l.timestamp}-${i}`} className="whitespace-pre-wrap break-all">
            <span className="text-zinc-600">{new Date(l.timestamp * 1000).toLocaleTimeString()} </span>
            <span className={SUBSYSTEM_COLOR[l.subsystem] ?? 'text-zinc-400'}>[{l.subsystem}]</span>{' '}
            <span className={LEVEL_COLOR[l.level] ?? 'text-zinc-300'}>{l.raw_line}</span>
          </div>
        ))}
        {!visible.length && <div className="text-zinc-600">Keine Zeilen — Filter lockern oder warten.</div>}
      </div>
      <div className="border-t border-zinc-800 px-2 py-1 text-[10px] text-zinc-500">
        {visible.length}/{lines.length} Zeilen · Ringpuffer {RING_BUFFER_LINES} ·
        {paused ? ' PAUSIERT' : ' live'} · Secrets maskiert ({(sources?.masked_keys ?? []).join(', ')})
      </div>
    </div>
  );
}
