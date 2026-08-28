import { useEffect, useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { ScrollArea } from '@/components/ui/scroll-area';
import StrategyEditor from '../StrategyEditor';
import AIReviewer from '../AIReviewer';
import { BacktestingPanel } from '../BacktestingPanel';
import { GeneticOptimizerPanel } from '../GeneticOptimizerPanel';
import { useStrategyWorkspace } from '@/hooks/useStrategyWorkspace';
import { sigmaApi, type LibrarySnapshotRow, type TvLibraryCatalog, type TvLibraryScript } from '@/lib/sigmaApi';
import { formatTimeframe } from '@/types';
import { PasskeyWebAuthnClient } from '../../optimizer/PasskeyWebAuthnClient';
import { AmpelDot, StrategyScorecardTab } from './StrategyScorecardTab';

const STRATEGY_DETAIL_TABS = [
  'Code', 'Parameters', 'Alerts', 'Backtest', 'Optimize', 'Live / M8', 'Audit', 'Academy Badges & Profiling',
] as const;

const TEMPLATES = [
  { id: 'cisd', label: 'CISD Momentum' },
  { id: 'rsi', label: 'RSI Reversion' },
  { id: 'empty', label: 'Empty v6' },
] as const;

export function StrategyLibraryPanel() {
  const ws = useStrategyWorkspace();
  const [filter, setFilter] = useState('');
  const [status, setStatus] = useState('');
  const [paramText, setParamText] = useState('{}');
  const [alertMsg, setAlertMsg] = useState('');
  const [tvOpen, setTvOpen] = useState(false);
  const [tvCatalog, setTvCatalog] = useState<TvLibraryCatalog | null>(null);
  const [tvPicked, setTvPicked] = useState<Record<string, boolean>>({});
  const [tvBusy, setTvBusy] = useState(false);
  const [snapRows, setSnapRows] = useState<Record<string, LibrarySnapshotRow>>({});

  const selected = ws.selected;
  const visible = ws.strategies.filter((s) => {
    const q = filter.toLowerCase();
    if (q && !`${s.name} ${s.assetPair} ${s.id} ${s.tv_script_id || ''}`.toLowerCase().includes(q)) return false;
    return true;
  });

  useEffect(() => {
    setParamText(JSON.stringify(selected?.parameters ?? {}, null, 2));
  }, [selected?.id]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void sigmaApi.librarySnapshot().then((d) => {
        if (!alive || !d?.strategies) return;
        const map: Record<string, LibrarySnapshotRow> = {};
        for (const row of d.strategies) map[row.id] = row;
        setSnapRows(map);
      });
    };
    load();
    const id = window.setInterval(load, 12000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  const fromTemplate = async (template: string) => {
    setStatus('creating…');
    const out = await sigmaApi.fromTemplate(template);
    setStatus(out ? `created ${out.name || out.id}` : 'from-template failed');
    await ws.reload();
  };

  const push = async () => {
    if (!selected) return;
    setStatus('pushing…');
    const job = await sigmaApi.pushCode(selected.id, {
      symbol: selected.assetPair, interval: selected.interval, code: selected.code,
    });
    setStatus(job ? `job ${job.job_id}` : 'push failed');
  };

  const loginTv = async () => {
    setStatus('opening Chrome…');
    const out = await sigmaApi.tvLogin();
    if (!out) {
      setStatus('Chrome login failed');
      return;
    }
    const how = out.reused ? 'reused' : 'opened';
    setStatus(`${how} ${out.url || 'TradingView'} (${out.mode || 'chrome'})`);
  };

  const syncAlert = async () => {
    if (!selected) return;
    setAlertMsg('syncing…');
    const rec = await sigmaApi.syncAlert(selected.id, selected.assetPair, selected.interval);
    setAlertMsg(rec ? `${rec.status} ${rec.tv_alert_id || ''}` : 'alert sync failed');
  };

  const operatorAct = async (kind: 'initialize' | 'validate') => {
    if (!selected) return;
    const token = await PasskeyWebAuthnClient.authenticatePasskeyForSettings('master@alpha.local');
    if (!token) return;
    setStatus(kind === 'initialize' ? 'initializing…' : 'validating…');
    const out = kind === 'initialize'
      ? await sigmaApi.initializeStrategy(selected.id, token)
      : await sigmaApi.validateStrategy(selected.id, token);
    setStatus(out ? `${kind} ${out.lamp || out.job_id || 'ok'}` : `${kind} failed`);
  };

  const loadTvScripts = async () => {
    setTvOpen(true);
    setTvBusy(true);
    setStatus('loading TV scripts…');
    const catalog = await sigmaApi.tvScripts();
    setTvBusy(false);
    if (!catalog) {
      setStatus('TV catalog failed');
      return;
    }
    setTvCatalog(catalog);
    const next: Record<string, boolean> = {};
    for (const row of catalog.scripts) {
      next[row.tv_script_id] = !row.already_imported;
    }
    setTvPicked(next);
    if (!catalog.session_present) {
      setStatus(catalog.reason || 'TV session missing — run bin/sigma-tv-login');
    } else {
      setStatus(`${catalog.count} TV script${catalog.count === 1 ? '' : 's'} (${catalog.source})`);
    }
  };

  const importTv = async (ids?: string[]) => {
    const scriptIds = ids ?? Object.entries(tvPicked).filter(([, on]) => on).map(([id]) => id);
    if (!scriptIds.length) {
      setStatus('select at least one TV script');
      return;
    }
    setTvBusy(true);
    setStatus('importing…');
    const out = await sigmaApi.syncTvLibrary({ script_ids: scriptIds });
    setTvBusy(false);
    if (!out) {
      setStatus('TV import failed');
      return;
    }
    setStatus(
      `imported ${out.imported_count}, skipped ${out.skipped_count}`
      + (out.missing?.length ? `, missing ${out.missing.length}` : '')
      + ' — paper/inactive',
    );
    await ws.reload();
    const refreshed = await sigmaApi.tvScripts();
    if (refreshed) {
      setTvCatalog(refreshed);
      const next: Record<string, boolean> = {};
      for (const row of refreshed.scripts) {
        next[row.tv_script_id] = !row.already_imported;
      }
      setTvPicked(next);
    }
  };

  const tvRows: TvLibraryScript[] = tvCatalog?.scripts ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-2">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter library…"
          className="h-7 w-40 text-xs"
        />
        {TEMPLATES.map((t) => (
          <Button key={t.id} size="sm" variant="outline" className="h-7 text-[10px]"
            onClick={() => void fromTemplate(t.id)}>
            New {t.label}
          </Button>
        ))}
        <Button size="sm" variant="outline" className="h-7 text-[10px]"
          onClick={() => void loadTvScripts()} disabled={tvBusy}>
          Load from TV
        </Button>
        <Button size="sm" className="h-7 text-[10px]" onClick={() => void push()} disabled={!selected}>
          Push to TV
        </Button>
        <Button size="sm" variant="outline" className="h-7 text-[10px]"
          onClick={() => void loginTv()}>
          Login TV
        </Button>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">{status}</span>
      </div>
      {tvOpen && (
        <div className="border-b border-border bg-zinc-950/40 p-2">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium">TradingView scripts</span>
            <Badge variant="outline" className="h-4 text-[9px]">
              {tvCatalog?.session_present ? (tvCatalog.driver || 'session') : 'no session'}
            </Badge>
            <span className="font-mono text-[10px] text-muted-foreground">
              {tvCatalog?.reason || tvCatalog?.source || (tvBusy ? 'loading…' : '')}
            </span>
            <Button size="sm" variant="outline" className="ml-auto h-6 text-[10px]"
              onClick={() => void importTv()} disabled={tvBusy || !tvRows.length}>
              Import selected
            </Button>
            <Button size="sm" variant="outline" className="h-6 text-[10px]"
              onClick={() => void importTv(tvRows.map((r) => r.tv_script_id))}
              disabled={tvBusy || !tvRows.length}>
              Import all
            </Button>
            <Button size="sm" variant="ghost" className="h-6 text-[10px]"
              onClick={() => setTvOpen(false)}>
              Hide
            </Button>
          </div>
          {!tvRows.length && !tvBusy && (
            <div className="text-[11px] text-muted-foreground">
              {tvCatalog?.session_present
                ? 'No saved or published scripts on this TradingView session.'
                : 'Log in with bin/sigma-tv-login so Sigma can read your TV library. Imports always start paper/inactive.'}
            </div>
          )}
          {!!tvRows.length && (
            <div className="max-h-36 space-y-1 overflow-auto">
              {tvRows.map((row) => (
                <label key={row.tv_script_id}
                  className="flex items-center gap-2 rounded border border-border px-2 py-1 text-[11px]">
                  <input
                    type="checkbox"
                    checked={Boolean(tvPicked[row.tv_script_id])}
                    onChange={(e) => setTvPicked((prev) => ({ ...prev, [row.tv_script_id]: e.target.checked }))}
                    disabled={tvBusy}
                  />
                  <span className="min-w-0 flex-1 truncate">{row.name}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">{row.origin || row.type}</span>
                  {row.already_imported && (
                    <Badge variant="outline" className="h-4 text-[9px]">in library</Badge>
                  )}
                </label>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(12rem,18%)_1fr]">
        <ScrollArea className="border-r border-border">
          <div className="space-y-1 p-2">
            {visible.map((s) => {
              const snap = snapRows[s.id];
              return (
              <button
                key={s.id}
                onClick={() => ws.select(s)}
                className={`w-full rounded-md border px-2 py-1.5 text-left text-[11px] ${
                  selected?.id === s.id ? 'border-sky-600 bg-sky-950/40' : 'border-border hover:bg-muted/50'
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <AmpelDot lamp={snap?.lamp} glow={selected?.id === s.id} />
                    <span className="truncate font-medium">{s.name}</span>
                  </span>
                  <Badge variant="outline" className="h-4 text-[9px]">{s.executionMode || 'paper'}</Badge>
                </div>
                <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {snap?.best_symbol || s.assetPair} · {formatTimeframe(s.interval)} · {s.status}
                  {s.tv_script_id ? ' · TV' : ''}
                  {snap?.primary_badge ? ` · ${snap.primary_badge}` : ''}
                </div>
              </button>
              );
            })}
            {!visible.length && <div className="text-[11px] text-muted-foreground">No strategies</div>}
          </div>
        </ScrollArea>
        <Tabs defaultValue="Code" className="flex min-h-0 flex-col gap-0">
          {selected && (
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5">
              <AmpelDot lamp={snapRows[selected.id]?.lamp} glow />
              <span className="truncate text-[12px] font-medium">{selected.name}</span>
              <Button size="sm" className="h-6 text-[10px]" disabled={!selected}
                onClick={() => void operatorAct('initialize')}>Initialisieren</Button>
              <div className="flex flex-col">
                <Button size="sm" variant="outline" className="h-6 text-[10px]"
                  onClick={() => void operatorAct('validate')}>Validieren</Button>
              </div>
            </div>
          )}
          <TabsList variant="line" className="h-8 w-full justify-start overflow-x-auto rounded-none">
            {STRATEGY_DETAIL_TABS.map((t) => (
              <TabsTrigger key={t} value={t} className="h-7 flex-none text-[10px]">{t}</TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="Code" className="mt-0 min-h-0 flex-1 overflow-auto">
            <StrategyEditor
              strategies={ws.strategies}
              selectedStrategy={ws.selected}
              onSelectStrategy={ws.select}
              onUpdateStrategy={ws.update}
              onCreateStrategy={ws.create}
              onDeleteStrategy={ws.remove}
              onToggleRun={ws.toggleRun}
              onReloadStrategies={ws.reload}
              onArchiveStrategy={ws.archive}
              onRestoreStrategy={ws.restore}
            />
          </TabsContent>
          <TabsContent value="Parameters" className="mt-0 space-y-2 overflow-auto p-3">
            <Textarea className="min-h-48 font-mono text-xs" value={paramText}
              onChange={(e) => setParamText(e.target.value)} />
            <Button size="sm" disabled={!selected} onClick={() => {
              try {
                const parameters = JSON.parse(paramText);
                if (selected) void ws.update(selected.id, { parameters });
                setStatus('parameters saved');
              } catch { setStatus('invalid JSON'); }
            }}>Save parameters</Button>
          </TabsContent>
          <TabsContent value="Alerts" className="mt-0 space-y-2 overflow-auto p-3">
            <p className="text-xs text-muted-foreground">
              Webhook upsert for the selected strategy. Secret lives in Settings.
            </p>
            <Button size="sm" disabled={!selected} onClick={() => void syncAlert()}>Sync alert</Button>
            <div className="font-mono text-[11px] text-muted-foreground">{alertMsg}</div>
          </TabsContent>
          <TabsContent value="Backtest" className="mt-0 min-h-0 flex-1 overflow-auto">
            <BacktestingPanel
              strategies={ws.strategies}
              selectedStrategy={ws.selected}
              onSelectStrategy={ws.select}
              onOpenOrchestrator={ws.select}
              onUpdateStrategyParams={(id, params) => ws.update(id, { parameters: params })}
            />
          </TabsContent>
          <TabsContent value="Optimize" className="mt-0 min-h-0 flex-1 overflow-auto">
            <GeneticOptimizerPanel
              strategies={ws.strategies}
              onOpenOrchestrator={ws.select}
              onOpenBacktester={() => undefined}
              onReloadStrategies={() => void ws.reload()}
            />
          </TabsContent>
          <TabsContent value="Live / M8" className="mt-0 min-h-0 flex-1 overflow-auto p-2">
            <AIReviewer
              currentStrategy={ws.selected}
              strategies={ws.strategies}
              onInsertGeneratedStrategy={(s) => void ws.create(s)}
              onUpdateStrategy={async (partial) => {
                if (ws.selected) await ws.update(ws.selected.id, partial);
              }}
              onReloadStrategies={ws.reload}
            />
          </TabsContent>
          <TabsContent value="Audit" className="mt-0 overflow-auto p-3 text-xs text-muted-foreground">
            Lifecycle, receipts and reject reasons for {selected?.id || '—'}. Use Order Receipts / Process Log panels for the live stream.
          </TabsContent>
          <TabsContent value="Academy Badges & Profiling" className="mt-0 min-h-0 flex-1 overflow-auto">
            {selected ? (
              <StrategyScorecardTab
                strategyId={selected.id}
                snapshot={snapRows[selected.id]}
                onBusy={setStatus}
              />
            ) : (
              <p className="p-3 text-xs text-muted-foreground">Select a strategy.</p>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
