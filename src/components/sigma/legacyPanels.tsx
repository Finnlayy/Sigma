import { useEffect, useState } from 'react';
import MetricsPanel from '../MetricsPanel';
import QueueMatrixPanel from '../QueueMatrixPanel';
import KrakenLedgersPanel from '../KrakenLedgersPanel';
import { DataLakePanel } from '../DataLakePanel';
import { BacktestingPanel } from '../BacktestingPanel';
import { GeneticOptimizerPanel } from '../GeneticOptimizerPanel';
import { SystemHealthPanel as QuantHealth } from '../quant/SystemHealthPanel';
import { QuantitativeRegimePanel } from '../quant/QuantitativeRegimePanel';
import { ExecutionRiskPanel as QuantExec } from '../quant/ExecutionRiskPanel';
import { AcademyRegistryPanel as QuantAcademy } from '../quant/AcademyRegistryPanel';
import SettingsPage from '../SettingsPage';
import { useStrategyWorkspace } from '@/hooks/useStrategyWorkspace';
import { safeFetchJson } from '@/lib/api';
import type { MarketTicker, QueueMatrixData, RunnerMetrics, StrategyPnL } from '@/types';

export function OverviewMetricsPanel() {
  const { strategies, select } = useStrategyWorkspace();
  const [metrics, setMetrics] = useState<RunnerMetrics | null>(null);
  const [balances, setBalances] = useState<Record<string, number> | null>(null);
  const [strategyPnL, setStrategyPnL] = useState<StrategyPnL[]>([]);
  const [tickers, setTickers] = useState<MarketTicker[]>([]);
  const [queues, setQueues] = useState<{ paper: QueueMatrixData; live: QueueMatrixData } | null>(null);

  useEffect(() => {
    const load = async () => {
      // Bolt: Overview polls /api/logs (10k-row scan, ~35 ms @ 8k trades) plus
      // market-data + queue-matrices every 8s. Hidden-tab skip: ~7.5 of those
      // triples/min — the hottest dashboard poll path — drop to zero.
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      const logs = await safeFetchJson<{
        metrics: RunnerMetrics; balances: Record<string, number>; strategyPnL?: StrategyPnL[];
      }>('/api/logs');
      if (logs) {
        setMetrics(logs.metrics || null);
        setBalances(logs.balances || null);
        if (logs.strategyPnL) setStrategyPnL(logs.strategyPnL);
      }
      const t = await safeFetchJson<MarketTicker[]>('/api/market-data');
      if (t) setTickers(t);
      const q = await safeFetchJson<{ paper: QueueMatrixData; live: QueueMatrixData }>('/api/queue-matrices');
      if (q) setQueues(q);
    };
    void load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="h-full overflow-auto p-2">
      <MetricsPanel
        metrics={metrics}
        balances={balances}
        strategyPnL={strategyPnL}
        strategies={strategies}
        selectedStrategy={strategies[0] ?? null}
        queueMatrices={queues}
        tickers={tickers}
        onSelectStrategy={(id) => {
          const s = strategies.find((x) => x.id === id);
          if (s) select(s);
        }}
      />
    </div>
  );
}

export function QueueMatrixPanelView() {
  const { strategies, toggleRun } = useStrategyWorkspace();
  const [queues, setQueues] = useState<{ paper: QueueMatrixData; live: QueueMatrixData } | null>(null);
  const load = () => {
    void safeFetchJson<{ paper: QueueMatrixData; live: QueueMatrixData }>('/api/queue-matrices').then((q) => q && setQueues(q));
  };
  useEffect(() => {
    load();
    // Bolt: queue-matrices is O(S×T) over up to 5k trades; skip when hidden.
    // Manual onRefresh (below) still fetches so the user can force a reload.
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      load();
    }, 8000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="h-full overflow-auto p-2">
      <QueueMatrixPanel
        queueMatrices={queues}
        onToggleStrategy={(id, action, mode) => void toggleRun(id, action, mode)}
        onRefresh={load}
        onSelectStrategy={() => undefined}
      />
      <span className="hidden">{strategies.length}</span>
    </div>
  );
}

export function LedgersPanel() {
  const [hasCreds, setHasCreds] = useState(false);
  const [paper, setPaper] = useState(true);
  const refresh = () => {
    void safeFetchJson<{ hasCredentials: boolean; paperTrading: boolean }>('/api/kraken/status').then((d) => {
      if (!d) return;
      setHasCreds(d.hasCredentials);
      setPaper(d.paperTrading);
    });
  };
  useEffect(() => { refresh(); }, []);
  return (
    <div className="h-full overflow-auto p-2">
      <KrakenLedgersPanel isPaperTrading={paper} hasCredentials={hasCreds} onRefreshTrigger={refresh} />
    </div>
  );
}

export function DataLakePanelView() {
  return (
    <div className="h-full overflow-auto p-2">
      <DataLakePanel activeSymbol="BTC/USD" />
    </div>
  );
}

export function BacktestPanel() {
  const ws = useStrategyWorkspace();
  return (
    <div className="h-full overflow-auto p-2">
      <BacktestingPanel
        strategies={ws.strategies}
        selectedStrategy={ws.selected}
        onSelectStrategy={ws.select}
        onOpenOrchestrator={ws.select}
        onUpdateStrategyParams={(id, params) => ws.update(id, { parameters: params })}
      />
    </div>
  );
}

export function GeneticPanel() {
  const ws = useStrategyWorkspace();
  return (
    <div className="h-full overflow-auto p-2">
      <GeneticOptimizerPanel
        strategies={ws.strategies}
        onOpenOrchestrator={ws.select}
        onOpenBacktester={() => undefined}
        onReloadStrategies={() => void ws.reload()}
      />
    </div>
  );
}

export function SystemHealthPanel() {
  return (
    <div className="h-full overflow-auto p-2">
      <QuantHealth />
    </div>
  );
}

export function RegimePanel() {
  return (
    <div className="h-full overflow-auto p-2">
      <QuantitativeRegimePanel />
    </div>
  );
}

export function ExecutionRiskPanel() {
  return (
    <div className="h-full overflow-auto p-2">
      <QuantExec />
    </div>
  );
}

export function AcademyRegistryPanel() {
  return (
    <div className="h-full overflow-auto p-2">
      <QuantAcademy />
    </div>
  );
}

export function SettingsPanel() {
  return (
    <div className="h-full overflow-auto p-2">
      <SettingsPage />
    </div>
  );
}
