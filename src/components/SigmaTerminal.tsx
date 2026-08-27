/**
 * =========================================================
 * Datei:      src/components/SigmaTerminal.tsx
 * Zweck:      §3.2 / §8 — Sigma Terminal: FlexLayout-Dock mit
 *             der 11er Panel-Registry und den 4 Presets
 *             (BOT_COCKPIT, PINE_IDE, RISK_RADAR, SENTINEL_OPS).
 *             Layout wird in localStorage persistiert.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Layout, Model, type IJsonModel, type ILayoutApi, type TabNode } from 'flexlayout-react';
import 'flexlayout-react/style/dark.css';
import { LayoutGrid, RotateCcw, ShieldAlert, Wifi, WifiOff } from 'lucide-react';
import { PANEL_REGISTRY, PANEL_TITLES } from './sigma/panels';
import { sigmaApi, type HealthResponse } from '../lib/sigmaApi';

const STORAGE_KEY = 'sigma.terminal.layout.v1';
const PRESET_KEY = 'sigma.terminal.preset.v1';

export const PRESETS = ['BOT_COCKPIT', 'PINE_IDE', 'RISK_RADAR', 'SENTINEL_OPS'] as const;
export type Preset = (typeof PRESETS)[number];

const tab = (component: string) => ({
  type: 'tab' as const,
  name: PANEL_TITLES[component] ?? component,
  component,
  enableClose: true,
});

const row = (weight: number, children: unknown[]) => ({ type: 'row', weight, children });
const set = (weight: number, components: string[]) => ({
  type: 'tabset', weight, children: components.map(tab),
});

const PRESET_LAYOUTS: Record<Preset, IJsonModel> = {
  BOT_COCKPIT: {
    global: {},
    borders: [],
    layout: row(100, [
      row(70, [
        set(60, ['MarketChart']),
        set(40, ['VirtualBotDeck']),
      ]),
      row(30, [
        set(50, ['RewardXPMatrixPanel']),
        set(50, ['AcademyBadgeMatrix', 'TelegramOperatorPanel']),
      ]),
    ]) as IJsonModel['layout'],
  },
  PINE_IDE: {
    global: {},
    borders: [],
    layout: row(100, [
      set(55, ['PineStudio']),
      row(45, [
        set(60, ['MarketChart']),
        set(40, ['TvJobsPanel', 'AcademyBadgeMatrix']),
      ]),
    ]) as IJsonModel['layout'],
  },
  RISK_RADAR: {
    global: {},
    borders: [],
    layout: row(100, [
      row(60, [
        set(45, ['RiskGauges']),
        set(55, ['MarketChart']),
      ]),
      row(40, [
        set(34, ['DeadmanSwitchPanel']),
        set(33, ['MemoryWatchdogPanel']),
        set(33, ['SelfOptimizingMLPanel']),
      ]),
    ]) as IJsonModel['layout'],
  },
  SENTINEL_OPS: {
    global: {},
    borders: [],
    layout: row(100, [
      row(55, [
        set(34, ['DeadmanSwitchPanel']),
        set(33, ['MemoryWatchdogPanel']),
        set(33, ['RiskGauges']),
      ]),
      row(45, [
        set(50, ['TelegramOperatorPanel', 'LLMConsole']),
        set(50, ['TvJobsPanel', 'SelfOptimizingMLPanel']),
      ]),
    ]) as IJsonModel['layout'],
  },
};

const GLOBAL = {
  tabEnableFloat: false,
  tabSetEnableMaximize: true,
  splitterSize: 4,
  tabSetHeaderHeight: 26,
  tabSetTabStripHeight: 26,
  borderBarSize: 26,
};

function buildModel(preset: Preset, json?: IJsonModel): Model {
  const base = json ?? PRESET_LAYOUTS[preset];
  return Model.fromJson({ ...base, global: { ...GLOBAL, ...(base.global ?? {}) } });
}

export default function SigmaTerminal() {
  const [preset, setPreset] = useState<Preset>(() =>
    (localStorage.getItem(PRESET_KEY) as Preset) || 'BOT_COCKPIT');
  const [model, setModel] = useState<Model>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    const p = (localStorage.getItem(PRESET_KEY) as Preset) || 'BOT_COCKPIT';
    if (stored) {
      try { return buildModel(p, JSON.parse(stored) as IJsonModel); } catch { /* fall through */ }
    }
    return buildModel(p);
  });
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const layoutRef = useRef<ILayoutApi | null>(null);

  useEffect(() => {
    const load = () => void sigmaApi.health().then(setHealth);
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const persist = useCallback((m: Model) => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(m.toJson())); } catch { /* quota */ }
  }, []);

  const applyPreset = (p: Preset) => {
    setPreset(p);
    localStorage.setItem(PRESET_KEY, p);
    const m = buildModel(p);
    localStorage.removeItem(STORAGE_KEY);
    setModel(m);
  };

  const factory = useCallback((node: TabNode) => {
    const component = node.getComponent() ?? '';
    const Panel = PANEL_REGISTRY[component];
    if (!Panel) return <div className="p-3 text-xs text-zinc-500">Unknown panel: {component}</div>;
    return <Panel />;
  }, []);

  const missing = useMemo(() => {
    const open = new Set<string>();
    model.visitNodes((n) => {
      const c = (n as TabNode).getComponent?.();
      if (c) open.add(c);
    });
    return Object.keys(PANEL_REGISTRY).filter((k) => !open.has(k));
  }, [model, preset]);

  const addPanel = (component: string) => {
    layoutRef.current?.addTabToActiveTabSet({
      type: 'tab', name: PANEL_TITLES[component] ?? component, component,
    });
  };

  const online = !!health;
  const killed = !!health?.kill_switch;

  return (
    <div className="flex h-full min-h-0 flex-col bg-zinc-950">
      {/* Command bar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 bg-zinc-900/70 px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold tracking-wide text-zinc-100">
          <LayoutGrid size={15} className="text-sky-400" /> SIGMA TERMINAL
        </div>

        <div className="ml-2 flex items-center gap-1">
          {PRESETS.map((p) => (
            <button key={p} onClick={() => applyPreset(p)}
              className={`rounded px-2 py-1 text-[10px] font-semibold tracking-wide transition ${
                preset === p
                  ? 'bg-sky-600 text-white'
                  : 'border border-zinc-700 text-zinc-400 hover:border-sky-500 hover:text-sky-400'
              }`}>
              {p.replace('_', ' ')}
            </button>
          ))}
          <button onClick={() => applyPreset(preset)} title="Reset layout"
            className="rounded border border-zinc-700 p-1 text-zinc-400 hover:border-sky-500 hover:text-sky-400">
            <RotateCcw size={12} />
          </button>
        </div>

        {missing.length > 0 && (
          <select value="" onChange={(e) => e.target.value && addPanel(e.target.value)}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-300">
            <option value="">+ Panel…</option>
            {missing.map((m) => <option key={m} value={m}>{PANEL_TITLES[m] ?? m}</option>)}
          </select>
        )}

        <div className="ml-auto flex items-center gap-3 font-mono text-[10px]">
          <span className={online ? 'flex items-center gap-1 text-emerald-400' : 'flex items-center gap-1 text-red-400'}>
            {online ? <Wifi size={11} /> : <WifiOff size={11} />} core:8000
          </span>
          <span className={health?.scraper_ok ? 'text-emerald-400' : 'text-zinc-600'}>scraper:8001</span>
          <span className={health?.tv_worker_ok ? 'text-emerald-400' : 'text-zinc-600'}>tv-worker</span>
          <span className={health?.live_trading ? 'text-red-400' : 'text-sky-400'}>
            {health?.live_trading ? 'LIVE' : 'SHADOW'}
          </span>
          <span className="text-zinc-600">L{health?.blueprint?.autonomy_level ?? 4} · v{health?.blueprint?.blueprint_version ?? '1.2.0'}</span>
        </div>
      </div>

      {killed && (
        <div className="flex items-center gap-2 border-b border-red-900/60 bg-red-950/50 px-3 py-1 text-[11px] text-red-300">
          <ShieldAlert size={12} /> KILL SWITCH ENGAGED — Loop A rejects every webhook (403 BLOCKED) until released.
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        <Layout
          ref={layoutRef}
          model={model}
          factory={factory}
          onModelChange={persist}
          realtimeResize
        />
      </div>
    </div>
  );
}
