/**
 * GeneticOptimizerPanel — Blueprint §17.4 GA Hardening
 * - Max population 15, max generations 5, concurrency 1
 * - Param cache required, early termination stall 3 gens
 * - Shows ETA / progress / shadow gate
 */
import { useState, useEffect } from "react";
import { Dna, Play, RotateCcw, CheckCircle2, AlertTriangle, BarChart3, RefreshCw, Zap, Clock, Database } from "lucide-react";
import { TradingStrategy } from "../types";

interface GeneticOptimizerPanelProps {
  strategies: TradingStrategy[];
  onOpenOrchestrator: (strategy: TradingStrategy) => void;
  onOpenBacktester: (pair: string, interval: number, code: string, params: Record<string, any>) => void;
  onReloadStrategies?: () => void;
}

const BLUEPRINT_MAX_POP = 15;
const BLUEPRINT_MAX_GEN = 5;
const BLUEPRINT_STALL = 3;

export function GeneticOptimizerPanel({
  strategies,
  onOpenOrchestrator,
  onOpenBacktester,
  onReloadStrategies
}: GeneticOptimizerPanelProps) {
  const [config, setConfig] = useState({
    populationSize: BLUEPRINT_MAX_POP,
    maxGenerations: BLUEPRINT_MAX_GEN,
    survivorsCount: 3,
    mutationRate: 0.18,
    crossoverRate: 0.80,
    walkForwardSplitPercent: 70,
    assetPair: "BTC/USD",
    interval: 15,
    candleCount: 500,
    initialBalance: 10000,
    feePercent: 0.26,
    slippagePercent: 0.05,
    baselineStrategyId: undefined as string | undefined,
    baselineStrategyName: undefined as string | undefined,
  });

  const [isRunning, setIsRunning] = useState(false);
  const [progressGen, setProgressGen] = useState(0);
  const [progressPercent, setProgressPercent] = useState(0);
  const [etaSeconds, setEtaSeconds] = useState<number | null>(null);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [cacheInfo, setCacheInfo] = useState<string>("");

  const activeBaseline = config.baselineStrategyId ? strategies.find(s => s.id === config.baselineStrategyId) : undefined;

  const clamp = (pop: number, gen: number) => ({
    pop: Math.min(BLUEPRINT_MAX_POP, Math.max(1, Math.floor(pop))),
    gen: Math.min(BLUEPRINT_MAX_GEN, Math.max(1, Math.floor(gen))),
  });

  const handleSelectBaseline = (stratId: string) => {
    if (!stratId || stratId === 'none') {
      setConfig(prev => ({ ...prev, baselineStrategyId: undefined, baselineStrategyName: undefined }));
      return;
    }
    const strat = strategies.find(s => s.id === stratId);
    if (strat) {
      setConfig(prev => ({
        ...prev,
        baselineStrategyId: strat.id,
        baselineStrategyName: strat.name,
        assetPair: strat.assetPair || prev.assetPair,
        interval: typeof strat.interval === 'number' && strat.interval >= 60 ? Math.round(strat.interval / 60) : (strat.interval as any) || 15,
      }));
    }
  };

  const handleRun = async () => {
    const { pop, gen } = clamp(config.populationSize, config.maxGenerations);
    const payload = { ...config, populationSize: pop, maxGenerations: gen };
    setIsRunning(true);
    setProgressGen(0);
    setProgressPercent(0);
    setError(null);
    setResult(null);
    setEtaSeconds(pop * gen * 2); // rough: 2s per eval via TV CSV seam

    const start = Date.now();
    const tick = setInterval(() => {
      setProgressGen(prev => {
        const next = prev + 1;
        if (next <= gen) {
          const elapsed = (Date.now() - start) / 1000;
          const perGen = elapsed / Math.max(1, next);
          const remaining = Math.max(0, (gen - next) * perGen);
          setEtaSeconds(Math.round(remaining));
          setProgressPercent(Math.round((next / gen) * 100));
          return next;
        }
        return prev;
      });
    }, 800);

    try {
      const res = await fetch("/api/genetic/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      clearInterval(tick);
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`Server ${res.status}: ${txt.slice(0,200)}`);
      }
      const data = await res.json();
      setResult(data);
      setProgressGen(gen);
      setProgressPercent(100);
      setEtaSeconds(0);
      setCacheInfo(data.cacheHit ? "served from param cache" : `cache miss — ${data.population?.length || pop} genomes evaluated`);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      clearInterval(tick);
      setIsRunning(false);
    }
  };

  const best = result?.bestIndividual;

  return (
    <div className="space-y-4 max-w-6xl mx-auto pb-8 font-sans">
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 shadow-sm">
        <div className="flex items-center gap-3 mb-3">
          <div className="p-2 bg-purple-950/70 border border-purple-800/60 text-purple-400 rounded-lg">
            <Dna className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-mono font-bold text-white tracking-tight flex items-center gap-2">
              Genetic Optimizer — Blueprint §17.4 Hardening
              <span className="bg-purple-900/60 text-purple-300 border border-purple-700/60 text-[10px] px-2 py-0.5 rounded font-mono uppercase">POP {BLUEPRINT_MAX_POP} · GEN {BLUEPRINT_MAX_GEN} · C1</span>
            </h1>
            <p className="text-[11px] text-zinc-400 font-mono">
              TV Strategy Tester via CSV seam — cache required, early stop stall {BLUEPRINT_STALL} gens, DSR gate 0.95, min 30 trades. No silent fallback to local engine.
            </p>
          </div>
        </div>

        <div className="bg-zinc-950/70 border border-amber-900/40 rounded-lg p-3 mb-3">
          <div className="flex items-center gap-2 text-[11px] font-mono text-amber-300 mb-2">
            <AlertTriangle className="w-3.5 h-3.5" /> Blueprint Guard: UI cannot exceed GA caps
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 items-end">
            <div>
              <label className="block text-[10px] font-mono text-zinc-400 uppercase mb-1">Baseline Strategy</label>
              <select
                value={config.baselineStrategyId || 'none'}
                onChange={e => handleSelectBaseline(e.target.value)}
                className="w-full bg-zinc-900 border border-purple-800/60 text-zinc-200 rounded px-2 py-1.5 text-xs font-mono focus:border-purple-400 focus:outline-none"
              >
                <option value="none">Pure Exploration</option>
                {strategies.filter(s => s.status !== 'archived').map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.assetPair})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-mono text-zinc-400 uppercase mb-1">Asset Pair</label>
              <select value={config.assetPair} onChange={e => setConfig({ ...config, assetPair: e.target.value })} className="w-full bg-zinc-950 border border-zinc-700 text-white rounded px-2 py-1.5 text-xs font-mono">
                <option>BTC/USD</option><option>ETH/USD</option><option>SOL/USD</option><option>XRP/USD</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-mono text-zinc-400 uppercase mb-1">Interval (m)</label>
              <select value={config.interval} onChange={e => setConfig({ ...config, interval: Number(e.target.value) })} className="w-full bg-zinc-950 border border-zinc-700 text-white rounded px-2 py-1.5 text-xs font-mono">
                <option value={1}>1m</option><option value={5}>5m</option><option value={15}>15m</option><option value={30}>30m</option><option value={60}>1h</option><option value={240}>4h</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-mono text-zinc-400 uppercase mb-1">Population (max {BLUEPRINT_MAX_POP})</label>
              <input type="number" min={1} max={BLUEPRINT_MAX_POP} value={config.populationSize} onChange={e => {
                const { pop } = clamp(Number(e.target.value), config.maxGenerations);
                setConfig({ ...config, populationSize: pop });
              }} className="w-full bg-zinc-950 border border-zinc-700 text-white rounded px-2 py-1.5 text-xs font-mono" />
            </div>
            <div>
              <label className="block text-[10px] font-mono text-zinc-400 uppercase mb-1">Generations (max {BLUEPRINT_MAX_GEN})</label>
              <input type="number" min={1} max={BLUEPRINT_MAX_GEN} value={config.maxGenerations} onChange={e => {
                const { gen } = clamp(config.populationSize, Number(e.target.value));
                setConfig({ ...config, maxGenerations: gen });
              }} className="w-full bg-zinc-950 border border-zinc-700 text-white rounded px-2 py-1.5 text-xs font-mono" />
            </div>
            <div>
              <label className="block text-[10px] font-mono text-zinc-400 uppercase mb-1">WFO Split</label>
              <select value={config.walkForwardSplitPercent} onChange={e => setConfig({ ...config, walkForwardSplitPercent: Number(e.target.value) })} className="w-full bg-zinc-950 border border-zinc-700 text-white rounded px-2 py-1.5 text-xs font-mono">
                <option value={70}>70/30</option><option value={75}>75/25</option>
              </select>
            </div>
            <div>
              <button onClick={handleRun} disabled={isRunning} className={`w-full py-2 px-3 rounded-md text-xs font-mono font-bold flex items-center justify-center gap-2 ${isRunning ? 'bg-purple-950/60 text-purple-400 border border-purple-700/60' : 'bg-purple-600 hover:bg-purple-500 text-white border border-purple-500'}`}>
                {isRunning ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" /><span>{progressPercent}% · ETA {etaSeconds ?? '—'}s</span></> : <><Play className="w-3.5 h-3.5 fill-current" /><span>{activeBaseline ? 'Evolve' : 'Run GA'}</span></>}
              </button>
            </div>
          </div>
          {isRunning && (
            <div className="mt-3">
              <div className="flex justify-between text-[11px] font-mono text-zinc-400 mb-1">
                <span className="flex items-center gap-1.5 text-purple-400"><Clock className="w-3 h-3" /> Gen {progressGen}/{config.maxGenerations} — stall guard {BLUEPRINT_STALL} gens</span>
                <span className="flex items-center gap-1"><Database className="w-3 h-3" /> cache required · param cache hit = instant</span>
              </div>
              <div className="w-full h-2 bg-zinc-950 rounded-full overflow-hidden border border-zinc-800">
                <div className="h-full bg-gradient-to-r from-purple-600 to-emerald-500 transition-all" style={{ width: `${progressPercent}%` }} />
              </div>
            </div>
          )}
          {error && <div className="mt-2 text-[11px] font-mono text-rose-400 border border-rose-900/50 bg-rose-950/30 p-2 rounded">{error}</div>}
          {cacheInfo && <div className="mt-2 text-[10px] font-mono text-zinc-500">{cacheInfo} · early termination if no fitness improvement over {BLUEPRINT_STALL} gens</div>}
        </div>

        {result && (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
            <div className="bg-zinc-900 border border-zinc-800 p-3 rounded-lg">
              <span className="text-[10px] font-mono text-zinc-400 uppercase block">Best Fitness</span>
              <div className="text-lg font-mono font-bold text-purple-400 mt-1">{best?.fitness?.toFixed?.(2) ?? result.bestFitness ?? '—'}</div>
              <span className="text-[10px] font-mono text-zinc-500">DSR {best?.dsr ?? result.dsr ?? '—'} · Gate {result.shadowGate?.passed ? 'PASS' : 'FAIL'}</span>
            </div>
            <div className="bg-zinc-900 border border-zinc-800 p-3 rounded-lg">
              <span className="text-[10px] font-mono text-zinc-400 uppercase block">Return</span>
              <div className="text-lg font-mono font-bold text-emerald-400 mt-1">{best?.overallReturn ?? best?.totalReturn ?? '—'}%</div>
              <span className="text-[10px] font-mono text-zinc-500">PF {best?.profitFactor ?? result.profitFactor ?? '—'}</span>
            </div>
            <div className="bg-zinc-900 border border-zinc-800 p-3 rounded-lg">
              <span className="text-[10px] font-mono text-zinc-400 uppercase block">Sharpe</span>
              <div className="text-lg font-mono font-bold text-white mt-1">{best?.sharpeRatio?.toFixed?.(2) ?? '—'}</div>
              <span className="text-[10px] font-mono text-zinc-500">trades {best?.tradesCount ?? best?.totalTrades ?? '—'}</span>
            </div>
            <div className="bg-zinc-900 border border-zinc-800 p-3 rounded-lg">
              <span className="text-[10px] font-mono text-zinc-400 uppercase block">Max DD</span>
              <div className="text-lg font-mono font-bold text-rose-400 mt-1">-{best?.overallDrawdown ?? best?.maxDrawdown ?? '—'}%</div>
              <span className="text-[10px] font-mono text-zinc-500">winRate {best?.winRate ?? '—'}%</span>
            </div>
            <div className="bg-zinc-900 border border-zinc-800 p-3 rounded-lg">
              <span className="text-[10px] font-mono text-zinc-400 uppercase block">Shadow Gate</span>
              <div className={`text-sm font-mono font-bold mt-1 ${result.shadowGate?.passed ? 'text-emerald-400' : 'text-rose-400'}`}>{result.shadowGate?.passed ? 'PASS' : 'FAIL'}</div>
              <span className="text-[10px] font-mono text-zinc-500">min {result.minTrades ?? 30} trades · DSR≥0.95</span>
            </div>
            <div className="bg-zinc-900 border border-purple-800/50 p-3 rounded-lg">
              <span className="text-[10px] font-mono text-purple-300 uppercase block">Deploy</span>
              <button onClick={async () => {
                if (!best) return;
                const res = await fetch("/api/genetic/deploy-to-orchestrator", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ individual: best, assetPair: config.assetPair, interval: config.interval, strategyName: `Evolved ${config.assetPair} v${Date.now()}`, autoActivate: true, baselineStrategyId: config.baselineStrategyId }) });
                if (res.ok) {
                  const data = await res.json();
                  if (onReloadStrategies) onReloadStrategies();
                  onOpenOrchestrator(data.strategy);
                }
              }} className="mt-1 w-full py-1.5 bg-purple-600 hover:bg-purple-500 text-white rounded text-xs font-mono font-bold flex items-center justify-center gap-1"><Zap className="w-3 h-3" /> Deploy Winner</button>
            </div>
          </div>
        )}
      </div>

      {result?.history && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
          <h3 className="text-xs font-mono font-bold text-white uppercase tracking-wider mb-2 flex items-center gap-2"><BarChart3 className="w-4 h-4 text-purple-400" /> Convergence — {result.history.length} generations (early stop after {BLUEPRINT_STALL} stall)</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead><tr className="text-[10px] text-zinc-400 uppercase border-b border-zinc-800"><th className="py-1.5">Gen</th><th className="py-1.5 text-right">Best Fitness</th><th className="py-1.5 text-right">Avg Fitness</th><th className="py-1.5 text-right">Cache Hits</th></tr></thead>
              <tbody className="divide-y divide-zinc-800/60">
                {result.history.slice(0, BLUEPRINT_MAX_GEN).map((h: any, i: number) => (
                  <tr key={i} className="hover:bg-zinc-800/40"><td className="py-1">{h.generation ?? i+1}</td><td className="py-1 text-right text-emerald-400">{h.bestFitness?.toFixed?.(2) ?? '—'}</td><td className="py-1 text-right text-purple-300">{h.avgFitness?.toFixed?.(2) ?? '—'}</td><td className="py-1 text-right text-zinc-500">{h.cacheHits ?? 0}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {result?.population && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
          <div className="p-3 border-b border-zinc-800 flex items-center justify-between">
            <h3 className="text-xs font-mono font-bold text-white uppercase flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-400" /> Population — {result.population.length} genomes (capped {BLUEPRINT_MAX_POP})</h3>
            <span className="text-[10px] font-mono text-zinc-500">concurrency 1 · Playwright serial · param cache required</span>
          </div>
          <div className="overflow-x-auto max-h-[320px]">
            <table className="w-full text-left text-xs font-mono">
              <thead><tr className="bg-zinc-950/80 border-b border-zinc-800 text-[10px] text-zinc-400 uppercase sticky top-0"><th className="py-2 px-3">Rank</th><th className="py-2 px-3">ID</th><th className="py-2 px-3 text-right">Fitness</th><th className="py-2 px-3 text-right">Return</th><th className="py-2 px-3 text-right">Sharpe</th><th className="py-2 px-3 text-right">DD</th><th className="py-2 px-3 text-right">Trades</th></tr></thead>
              <tbody className="divide-y divide-zinc-800/60">
                {result.population.slice(0, BLUEPRINT_MAX_POP).map((ind: any, idx: number) => (
                  <tr key={ind.id || idx} className="hover:bg-zinc-800/40"><td className="py-1.5 px-3">#{ind.rank ?? idx+1}</td><td className="py-1.5 px-3">{ind.id}</td><td className="py-1.5 px-3 text-right text-purple-400">{ind.fitness?.toFixed?.(1) ?? '—'}</td><td className="py-1.5 px-3 text-right">{ind.overallReturn ?? ind.totalReturn ?? '—'}%</td><td className="py-1.5 px-3 text-right">{ind.sharpeRatio?.toFixed?.(2) ?? '—'}</td><td className="py-1.5 px-3 text-right text-rose-400">-{ind.overallDrawdown ?? ind.maxDrawdown ?? '—'}%</td><td className="py-1.5 px-3 text-right">{ind.tradesCount ?? ind.totalTrades ?? '—'}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!result && !isRunning && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-8 text-center text-zinc-500 font-mono text-xs">
          GA is hardened to Blueprint §17.4: population ≤{BLUEPRINT_MAX_POP}, generations ≤{BLUEPRINT_MAX_GEN}, concurrency 1, param-cache required, early stop after {BLUEPRINT_STALL} stall gens. ETA shown during run.
        </div>
      )}
    </div>
  );
}
