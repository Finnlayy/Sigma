/**
 * =========================================================
 * Datei:      src/components/SigmaTerminal.tsx
 * Zweck:      §3.2 / §8 — Sigma Terminal: shadcn Resizable+Tabs Dock
 *             mit Panel-Registry und Presets. Layout in localStorage.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { LayoutGrid, Plus, RotateCcw, ShieldAlert, Wifi, WifiOff } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { PANEL_REGISTRY, PANEL_TITLES } from './sigma/panels';
import {
  SigmaDock, addPanelToActive, collectPanels, fromFlexLayout, type DockNode,
} from './sigma/dock';
import { sigmaApi, type HealthResponse } from '../lib/sigmaApi';

const STORAGE_KEY = 'sigma.terminal.layout.v2';
const PRESET_KEY = 'sigma.terminal.preset.v1';

export const PRESETS = [
  'BOT_COCKPIT', 'PINE_IDE', 'RISK_RADAR', 'SENTINEL_OPS',
  'CAPITAL_OPS', 'PAPER_LAB', 'OBSERVABILITY', 'ML_INSPECTOR',
  'OVERVIEW', 'LIBRARY', 'QUANT', 'CONFIG',
] as const;
export type Preset = (typeof PRESETS)[number];

const tab = (component: string) => ({ type: 'tab' as const, component });
const row = (weight: number, children: unknown[]) => ({ type: 'row', weight, children });
const set = (weight: number, components: string[]) => ({
  type: 'tabset', weight, children: components.map(tab),
});

const PRESET_LAYOUTS: Record<Preset, { type: string; weight?: number; children?: unknown[] }> = {
  BOT_COCKPIT: {
    type: 'row',
    children: [
      row(70, [
        set(60, ['MarketChart']),
        set(40, ['VirtualBotDeck']),
      ]),
      row(30, [
        set(50, ['RewardXPMatrixPanel', 'MarketRadarPanel']),
        set(50, ['AcademyBadgeMatrix', 'TelegramOperatorPanel']),
      ]),
    ],
  },
  PINE_IDE: {
    type: 'row',
    children: [
      set(55, ['PineStudio']),
      row(45, [
        set(60, ['MarketChart']),
        set(40, ['TvJobsPanel', 'AcademyBadgeMatrix']),
      ]),
    ],
  },
  RISK_RADAR: {
    type: 'row',
    children: [
      row(60, [
        set(45, ['RiskGauges']),
        set(55, ['MarketChart']),
      ]),
      row(40, [
        set(34, ['DeadmanSwitchPanel', 'MarketRadarPanel']),
        set(33, ['MemoryWatchdogPanel']),
        set(33, ['SelfOptimizingMLPanel']),
      ]),
    ],
  },
  SENTINEL_OPS: {
    type: 'row',
    children: [
      row(55, [
        set(34, ['DeadmanSwitchPanel']),
        set(33, ['MemoryWatchdogPanel']),
        set(33, ['RiskGauges']),
      ]),
      row(45, [
        set(50, ['TelegramOperatorPanel', 'LLMConsole']),
        set(50, ['TvJobsPanel', 'SelfOptimizingMLPanel']),
      ]),
    ],
  },
  CAPITAL_OPS: {
    type: 'row',
    children: [
      row(55, [
        set(34, ['FlywheelBudgetPanel']),
        set(33, ['OrderReceiptsPanel']),
        set(33, ['ContagionRadarPanel']),
      ]),
      row(45, [
        set(34, ['OrderbookConfluencePanel']),
        set(33, ['SchedulerTelemetryPanel']),
        set(33, ['RateLimiterPanel']),
      ]),
    ],
  },
  ML_INSPECTOR: {
    type: 'row',
    children: [
      set(60, ['NetronVisualizerPanel']),
      set(40, ['SelfOptimizingMLPanel', 'AcademyBadgeMatrix']),
    ],
  },
  OBSERVABILITY: {
    type: 'row',
    children: [
      set(60, ['ProcessLogView']),
      row(40, [
        set(50, ['DiagnosticsErrorPanel']),
        set(50, ['SchedulerTelemetryPanel']),
      ]),
    ],
  },
  PAPER_LAB: {
    type: 'row',
    children: [
      row(60, [
        set(50, ['PaperLabPanel']),
        set(50, ['MarketChart']),
      ]),
      row(40, [
        set(50, ['VirtualBotDeck']),
        set(50, ['AcademyBadgeMatrix', 'OrderReceiptsPanel']),
      ]),
    ],
  },
  OVERVIEW: {
    type: 'row',
    children: [
      row(65, [
        set(60, ['OverviewMetricsPanel']),
        set(40, ['MarketChart']),
      ]),
      row(35, [
        set(50, ['QueueMatrixPanel']),
        set(50, ['LedgersPanel']),
      ]),
    ],
  },
  LIBRARY: {
    type: 'row',
    children: [
      set(55, ['StrategyLibraryPanel']),
      row(45, [
        set(55, ['PineStudio']),
        set(45, ['BacktestPanel', 'GeneticPanel']),
      ]),
    ],
  },
  QUANT: {
    type: 'row',
    children: [
      set(40, ['SystemHealthPanel']),
      row(60, [
        set(50, ['RegimePanel']),
        set(50, ['ExecutionRiskPanel', 'AcademyRegistryPanel']),
      ]),
    ],
  },
  CONFIG: {
    type: 'row',
    children: [
      set(55, ['SettingsPanel']),
      row(45, [
        set(50, ['DeadmanSwitchPanel']),
        set(50, ['RiskGauges']),
      ]),
    ],
  },
};

function buildTree(preset: Preset): DockNode {
  return fromFlexLayout(PRESET_LAYOUTS[preset] as Parameters<typeof fromFlexLayout>[0]);
}

function loadTree(preset: Preset): DockNode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as { preset?: string; tree?: DockNode };
      if (parsed.preset === preset && parsed.tree) return parsed.tree;
    }
  } catch { /* ignore */ }
  return buildTree(preset);
}

export default function SigmaTerminal() {
  const [preset, setPreset] = useState<Preset>(() =>
    (localStorage.getItem(PRESET_KEY) as Preset) || 'BOT_COCKPIT');
  const [tree, setTree] = useState<DockNode>(() => loadTree(
    (localStorage.getItem(PRESET_KEY) as Preset) || 'BOT_COCKPIT'));
  const [activeTabset, setActiveTabset] = useState('');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  useEffect(() => {
    const load = () => {
      void sigmaApi.health().then(setHealth);
      void fetch('/api/kraken/status')
        .then((r) => r.ok ? r.json() : null)
        .then((d) => { if (d?.latencyMs != null) setLatencyMs(d.latencyMs); })
        .catch(() => undefined);
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const persist = useCallback((next: DockNode, p = preset) => {
    setTree(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ preset: p, tree: next }));
    } catch { /* quota */ }
  }, [preset]);

  const applyPreset = (p: Preset) => {
    setPreset(p);
    localStorage.setItem(PRESET_KEY, p);
    localStorage.removeItem(STORAGE_KEY);
    persist(buildTree(p), p);
  };

  const open = collectPanels(tree);
  const missing = useMemo(
    () => Object.keys(PANEL_REGISTRY).filter((k) => !open.has(k)),
    [tree],
  );

  const online = !!health;
  const killed = !!health?.kill_switch;

  return (
    <div className="flex h-screen min-h-0 flex-col bg-background text-foreground">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-1.5">
        <div className="flex items-center gap-2 text-sm font-semibold tracking-wide">
          <LayoutGrid size={15} className="text-sky-400" /> SIGMA TERMINAL
        </div>

        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          value={preset}
          onValueChange={(v) => { if (v) applyPreset(v as Preset); }}
          className="flex-wrap justify-start"
        >
          {PRESETS.map((p) => (
            <ToggleGroupItem key={p} value={p} className="h-7 px-2 text-[10px] font-semibold tracking-wide">
              {p.replace(/_/g, ' ')}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <Button variant="outline" size="icon-xs" title="Reset layout" onClick={() => applyPreset(preset)}>
          <RotateCcw className="size-3" />
        </Button>

        {missing.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="h-7 text-[10px]">
                <Plus className="size-3" /> Panel
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="max-h-72 overflow-auto">
              {missing.map((m) => (
                <DropdownMenuItem
                  key={m}
                  onClick={() => {
                    const target = activeTabset || collectFirstTabset(tree);
                    persist(addPanelToActive(tree, target, m));
                  }}
                >
                  {PANEL_TITLES[m] ?? m}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}

        <div className="ml-auto flex items-center gap-2 font-mono text-[10px]">
          <Badge variant={online ? 'outline' : 'destructive'} className="gap-1">
            {online ? <Wifi size={11} /> : <WifiOff size={11} />} core:8000
          </Badge>
          <span className={health?.scraper_ok ? 'text-emerald-400' : 'text-muted-foreground'}>scraper:8001</span>
          <span className={health?.tv_worker_ok ? 'text-emerald-400' : 'text-muted-foreground'}>tv-worker</span>
          <Badge variant={health?.live_trading ? 'destructive' : 'secondary'}>
            {health?.live_trading ? 'LIVE' : 'SHADOW'}
          </Badge>
          <span className="text-muted-foreground">
            {latencyMs != null ? `${latencyMs}ms` : '—'}
          </span>
          <span className="text-muted-foreground">
            L{health?.blueprint?.autonomy_level ?? 4} · v{health?.blueprint?.blueprint_version ?? '1.2.0'}
          </span>
        </div>
      </div>

      {killed && (
        <Alert variant="destructive" className="rounded-none border-x-0 border-t-0">
          <ShieldAlert />
          <AlertTitle>KILL SWITCH ENGAGED</AlertTitle>
          <AlertDescription>
            Loop A rejects every webhook (403 BLOCKED) until released.
          </AlertDescription>
        </Alert>
      )}

      <div className="relative min-h-0 flex-1">
        <SigmaDock
          tree={tree}
          onChange={persist}
          activeTabsetId={activeTabset}
          onActiveTabset={setActiveTabset}
        />
      </div>
    </div>
  );
}

function collectFirstTabset(node: DockNode): string {
  if (node.type === 'tabset') return node.id;
  return collectFirstTabset(node.children[0]);
}
