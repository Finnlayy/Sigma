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
import { sigmaApi } from '@/lib/sigmaApi';
import { formatTimeframe } from '@/types';

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

  const selected = ws.selected;
  const visible = ws.strategies.filter((s) => {
    const q = filter.toLowerCase();
    if (q && !`${s.name} ${s.assetPair} ${s.id}`.toLowerCase().includes(q)) return false;
    return true;
  });

  useEffect(() => {
    setParamText(JSON.stringify(selected?.parameters ?? {}, null, 2));
  }, [selected?.id]);

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

  const syncAlert = async () => {
    if (!selected) return;
    setAlertMsg('syncing…');
    const rec = await sigmaApi.syncAlert(selected.id, selected.assetPair, selected.interval);
    setAlertMsg(rec ? `${rec.status} ${rec.tv_alert_id || ''}` : 'alert sync failed');
  };

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
        <Button size="sm" className="h-7 text-[10px]" onClick={() => void push()} disabled={!selected}>
          Push to TV
        </Button>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">{status}</span>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(12rem,18%)_1fr]">
        <ScrollArea className="border-r border-border">
          <div className="space-y-1 p-2">
            {visible.map((s) => (
              <button
                key={s.id}
                onClick={() => ws.select(s)}
                className={`w-full rounded-md border px-2 py-1.5 text-left text-[11px] ${
                  selected?.id === s.id ? 'border-sky-600 bg-sky-950/40' : 'border-border hover:bg-muted/50'
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate font-medium">{s.name}</span>
                  <Badge variant="outline" className="h-4 text-[9px]">{s.executionMode || 'paper'}</Badge>
                </div>
                <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {s.assetPair} · {formatTimeframe(s.interval)} · {s.status}
                </div>
              </button>
            ))}
            {!visible.length && <div className="text-[11px] text-muted-foreground">No strategies</div>}
          </div>
        </ScrollArea>
        <Tabs defaultValue="Code" className="flex min-h-0 flex-col gap-0">
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
          <TabsContent value="Academy Badges & Profiling" className="mt-0 min-h-0 flex-1 overflow-auto p-3 text-xs text-muted-foreground">
            Academy scorecard lives in the Academy Badges dock panel for the full matrix. This tab is bound to {selected?.id || 'the selected strategy'}.
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
