/**
 * =========================================================
 * Datei:      src/components/sigma/panels.tsx
 * Zweck:      §8 / Masterprompt §4.D — die 11 Panels der
 *             Panel-Registry des Sigma Terminals.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, Beaker, Bot, Brain, Code2, Cpu, Gauge, HeartPulse, Radar,
  Download, MemoryStick, MessageSquare, Pause, Play, RefreshCw, Send, ShieldAlert,
  Trash2,
  Sparkles, Trophy, Zap,
} from 'lucide-react';
import {
  sigmaApi, m8Color, ratingColor,
  type FeedMeta, type MoverRow, type ScraperHealth,
  type BadgeRow, type BotCard, type Candle, type DeadmanSnapshot, type MemorySnapshot,
  type MlSnapshot, type RegimeVector, type RewardRow, type SafetySnapshot,
  type TelegramSnapshot, type TvJob,
} from '../../lib/sigmaApi';
import TvLightweightChart from '../TvLightweightChart';

/* ------------------------------------------------------------------ shared */

function usePoll<T>(fn: () => Promise<T | null>, ms = 5000): [T | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const refresh = useCallback(() => { void fn().then((d) => d && setData(d)); }, [fn]);
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, ms);
    return () => clearInterval(id);
  }, [refresh, ms]);
  return [data, refresh];
}

function PanelShell({ title, icon, actions, children }: {
  title: string; icon: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col bg-zinc-950/60 text-zinc-200">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-1.5">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
          {icon}{title}
        </div>
        <div className="flex items-center gap-1">{actions}</div>
      </div>
      <div className="flex-1 overflow-auto p-3 text-xs">{children}</div>
    </div>
  );
}

const Stat = ({ label, value, tone = 'text-zinc-100' }: { label: string; value: React.ReactNode; tone?: string }) => (
  <div className="rounded border border-zinc-800 bg-zinc-900/50 px-2 py-1.5">
    <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
    <div className={`font-mono text-sm ${tone}`}>{value}</div>
  </div>
);

const IconBtn = ({ onClick, title, children }: { onClick: () => void; title: string; children: React.ReactNode }) => (
  <button onClick={onClick} title={title}
    className="rounded border border-zinc-700 p-1 text-zinc-400 transition hover:border-sky-500 hover:text-sky-400">
    {children}
  </button>
);

/** Loop-C-Herkunftsbadge: macht sichtbar, ob Daten echt vom Sidecar kommen. */
function FeedBadge({ feed }: { feed?: FeedMeta | null }) {
  if (!feed) return null;
  const tone = feed.source === 'tv_scraper'
    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400'
    : feed.source === 'cache_stale'
      ? 'border-amber-500/40 bg-amber-500/10 text-amber-400'
      : 'border-zinc-600/40 bg-zinc-700/20 text-zinc-400';
  const label = feed.source === 'tv_scraper' ? 'LIVE :8001'
    : feed.source === 'cache_stale' ? 'STALE CACHE' : 'SYNTHETIC';
  return (
    <span className={`rounded border px-1 py-0.5 text-[9px] font-bold tracking-wide ${tone}`}
      title={feed.upstream_error || `source=${feed.source}`}>
      {label}{feed.age_s ? ` ${Math.round(feed.age_s)}s` : ''}
    </span>
  );
}

/* ------------------------------------------------------- 1 VirtualBotDeck */

export function VirtualBotDeck() {
  const [data, refresh] = usePoll(sigmaApi.bots, 4000);
  const [form, setForm] = useState({ strategy_id: '', symbol: 'BTC/USD', budget_eur: 250 });

  const act = async (fn: Promise<unknown>) => { await fn; refresh(); };

  return (
    <PanelShell title="Virtual Bot Deck" icon={<Bot size={13} />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="mb-3 grid grid-cols-3 gap-2">
        <Stat label="Bots" value={data?.bots.length ?? 0} />
        <Stat label="Budget EUR" value={(data?.total_budget_eur ?? 0).toFixed(0)} />
        <Stat label="Equity EUR" value={(data?.total_equity_eur ?? 0).toFixed(2)} tone="text-emerald-400" />
      </div>

      <div className="mb-3 flex gap-1">
        <input value={form.strategy_id} placeholder="strategy_id"
          onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}
          className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]" />
        <input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}
          className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]" />
        <input type="number" value={form.budget_eur}
          onChange={(e) => setForm({ ...form, budget_eur: Number(e.target.value) })}
          className="w-20 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]" />
        <button
          onClick={() => form.strategy_id && act(sigmaApi.createBot(form) as Promise<unknown>)}
          className="rounded bg-sky-600/80 px-2 py-1 text-[11px] font-semibold hover:bg-sky-500">Add</button>
      </div>

      <div className="space-y-2">
        {(data?.bots ?? []).map((bot: BotCard) => (
          <div key={bot.bot_id} className="rounded border border-zinc-800 bg-zinc-900/40 p-2">
            <div className="flex items-center justify-between">
              <div className="font-mono text-[12px] text-zinc-100">{bot.strategy_id}
                <span className="ml-2 text-zinc-500">{bot.symbol} · {bot.timeframe}m</span>
              </div>
              <div className="flex items-center gap-1">
                {bot.leverage_badge && (
                  <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1 text-[9px] font-bold text-amber-400"
                    title="§29 fester Hebel pro Strategie">{bot.leverage_badge}</span>
                )}
                {bot.execution_mode === 'kraken_paper' && (
                  <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1 text-[9px] font-bold text-sky-400"
                    title="§32 Kraken Paper Lab">PAPER</span>
                )}
                <span className={`text-[10px] font-bold ${m8Color(bot.m8_state)}`}>{bot.m8_state}</span>
                {bot.runner_status === 'RUNNING'
                  ? <IconBtn onClick={() => act(sigmaApi.pauseBot(bot.bot_id))} title="Pause"><Pause size={12} /></IconBtn>
                  : <IconBtn onClick={() => act(sigmaApi.startBot(bot.bot_id))} title="Start"><Play size={12} /></IconBtn>}
              </div>
            </div>
            <div className="mt-2 grid grid-cols-4 gap-1 font-mono text-[11px]">
              <div><span className="text-zinc-500">cap </span>{bot.capital_eur}€</div>
              <div><span className="text-zinc-500">eq </span>{bot.equity_eur.toFixed(2)}€</div>
              <div className={bot.bot_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                <span className="text-zinc-500">pnl </span>{bot.bot_pnl.toFixed(2)}€
              </div>
              <div><span className="text-zinc-500">maxL </span>{bot.max_loss}€</div>
            </div>
            <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-500">
              <span>{bot.style} · x{bot.budget_multiplier}{bot.trigger_path ? ` · ${bot.trigger_path}` : ''}</span>
              <span>XP {bot.xp_strikes.xp} · Strikes {bot.xp_strikes.strikes}</span>
              <span className={bot.runner_status === 'QUARANTINED' ? 'text-red-400' : 'text-zinc-400'}>
                {bot.runner_status}
              </span>
            </div>
          </div>
        ))}
        {!data?.bots.length && <div className="text-zinc-600">No virtual bots yet — Pionex-style cards appear here.</div>}
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------------------------ 2 PineStudio */

const PINE_TEMPLATE = `//@version=6
strategy("Sigma CISD Momentum", overlay=true, initial_capital=1000)

fastLen = input.int(12, "Fast EMA")
slowLen = input.int(60, "Slow EMA")
atrLen  = input.int(14, "ATR Period")
atrMult = input.float(1.5, "ATR Stop Multiplier")

fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
atr  = ta.atr(atrLen)

longCond = ta.crossover(fast, slow)
if longCond
    strategy.entry("L", strategy.long, alert_message = '{"symbol":"{{ticker}}","action":"BUY","price":{{close}},"rsi":50,"atr":0,"timestamp":{{timenow}},"strategy_id":"REPLACE_ME","secret":"REPLACE_SECRET"}')
    strategy.exit("X", "L", stop = close - atr * atrMult, limit = close + atr * atrMult * 2)
`;

export function PineStudio() {
  const [code, setCode] = useState(PINE_TEMPLATE);
  const [strategyId, setStrategyId] = useState('cisd_momentum');
  const [symbol, setSymbol] = useState('BTC/USD');
  const [status, setStatus] = useState('');

  const push = async () => {
    setStatus('pushing…');
    const job = await sigmaApi.pushCode(strategyId, { symbol, interval: 15, code });
    setStatus(job ? `job ${job.job_id} (${job.status})` : 'push failed');
  };
  const pull = async () => {
    const job = await sigmaApi.pullParameters(strategyId, symbol, 15);
    setStatus(job ? `pull job ${job.job_id}` : 'pull failed');
  };

  return (
    <PanelShell title="Pine Studio (v6)" icon={<Code2 size={13} />}
      actions={<>
        <button onClick={pull} className="rounded border border-zinc-700 px-2 py-0.5 text-[10px] hover:border-sky-500">Pull Params</button>
        <button onClick={push} className="rounded bg-sky-600/80 px-2 py-0.5 text-[10px] font-semibold hover:bg-sky-500">Push to TV</button>
      </>}>
      <div className="mb-2 flex gap-1">
        <input value={strategyId} onChange={(e) => setStrategyId(e.target.value)}
          className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]" />
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)}
          className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]" />
      </div>
      <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
        className="h-[calc(100%-4rem)] min-h-40 w-full resize-none rounded border border-zinc-800 bg-black/60 p-2 font-mono text-[11px] leading-relaxed text-emerald-300" />
      <div className="mt-1 text-[10px] text-zinc-500">{status || 'Strategy ≡ TradingView — code lives in Pine, Sigma only orchestrates.'}</div>
    </PanelShell>
  );
}

/* ------------------------------------------------------------ 3 MarketChart */

export function MarketChart() {
  const [symbol, setSymbol] = useState('BTC/USD');
  const [interval, setIntervalMin] = useState(15);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [feed, setFeed] = useState<FeedMeta | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const primary = await sigmaApi.ohlc(symbol, interval, 300);
    const out = primary ?? await sigmaApi.ohlcFallback(symbol, interval, 300);
    setFeed(primary?.feed ?? null);
    if (out?.candles?.length) { setCandles(out.candles); setError(''); }
    else setError('no candles — start the sidecar: bin/sigma-scraper');
  }, [symbol, interval]);

  useEffect(() => { void load(); }, [load]);

  return (
    <PanelShell title="Market Chart" icon={<Activity size={13} />}
      actions={<>
        <FeedBadge feed={feed} />
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
          className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 text-[10px]">
          {['BTC/USD', 'ETH/USD', 'XRP/USD', 'SOL/USD'].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={interval} onChange={(e) => setIntervalMin(Number(e.target.value))}
          className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 text-[10px]">
          {[1, 5, 15, 60, 240].map((i) => <option key={i} value={i}>{i}m</option>)}
        </select>
        <IconBtn onClick={load} title="Reload"><RefreshCw size={12} /></IconBtn>
      </>}>
      {candles.length ? <TvLightweightChart candles={candles} height={240} />
        : <div className="text-zinc-600">{error || 'loading…'}</div>}
      <div className="mt-2 text-[10px] text-zinc-500">
        Lightweight Charts · Loop C sidecar (:8001) · {candles.length} candles
        {feed?.source === 'synthetic' && ' · deterministic offline feed — not for live decisions'}
      </div>
    </PanelShell>
  );
}

/* -------------------------------------------------------------- 4 LLMConsole */

export function LLMConsole() {
  const [tele, setTele] = useState<TelegramSnapshot | null>(null);
  const [input, setInput] = useState('/status');
  const [lines, setLines] = useState<string[]>([]);

  useEffect(() => { void sigmaApi.telegram().then((t) => t && setTele(t)); }, []);

  const send = async () => {
    const chat = tele?.whitelist?.[0] ?? '0';
    const out = await sigmaApi.telegramSend(chat, input);
    setLines((l) => [...l.slice(-40), `> ${input}`, out ? `${out.text} (${out.latency_ms}ms)` : 'no response']);
    setInput('');
  };

  return (
    <PanelShell title="LLM Console (Ollama)" icon={<Brain size={13} />}>
      <div className="mb-2 text-[10px] text-zinc-500">
        Offline endpoint {tele?.llm_url ?? 'http://127.0.0.1:11434'} · tools: update_risk_settings, control_strategy_runner, edit_strategy_pine_code, query_telemetry
      </div>
      <div className="mb-2 h-[calc(100%-6rem)] min-h-24 overflow-auto rounded border border-zinc-800 bg-black/60 p-2 font-mono text-[11px] text-emerald-300">
        {lines.length ? lines.map((l, i) => <div key={i}>{l}</div>) : <span className="text-zinc-600">Fast-path: /status /pause /resume /kill</span>}
      </div>
      <div className="flex gap-1">
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]" />
        <button onClick={send} className="rounded bg-sky-600/80 px-2 py-1 text-[11px] hover:bg-sky-500"><Send size={12} /></button>
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------------------- 5 AcademyBadgeMatrix */

export function AcademyBadgeMatrix() {
  const [data, refresh] = usePoll(() => sigmaApi.badges(), 8000);
  const rows: BadgeRow[] = data?.matrix ?? [];

  return (
    <PanelShell title="Academy Badges & Profiling" icon={<Trophy size={13} />}
      actions={<IconBtn onClick={refresh} title="Recalculate"><RefreshCw size={12} /></IconBtn>}>
      <div className="mb-2 flex gap-2 text-[10px] text-zinc-500">
        <span>profiles {data?.profiles ?? 0}</span>
        {Object.entries(data?.ratings ?? {}).map(([k, v]) => <span key={k}>{k}:{String(v)}</span>)}
        <span className="ml-auto">badge ab N ≥ 30</span>
      </div>
      <table className="w-full text-left font-mono text-[11px]">
        <thead className="text-[10px] uppercase text-zinc-500">
          <tr><th className="py-1">Strategy</th><th>Sym</th><th>TF</th><th>Regime</th><th>N</th><th>WR</th><th>PF</th><th>Badge</th></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-zinc-800/60">
              <td className="py-1 pr-2 text-zinc-300">{r.strategy_id}</td>
              <td>{r.symbol}</td><td>{r.timeframe}</td>
              <td className="pr-2 text-zinc-500">{r.regime}</td>
              <td>{r.trade_count}</td>
              <td>{(r.win_rate * 100).toFixed(0)}%</td>
              <td>{r.profit_factor.toFixed(2)}</td>
              <td><span className={`rounded border px-1 ${ratingColor(r.rating)}`}>{r.badge}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <div className="text-zinc-600">No profiles yet — Loop D scout & live autopsies feed this matrix.</div>}
    </PanelShell>
  );
}

/* -------------------------------------------------------------- 6 RiskGauges */

export function RiskGauges() {
  const [safety, refreshSafety] = usePoll(sigmaApi.safety, 4000);
  const [regime, setRegime] = useState<RegimeVector | null>(null);
  const [jobs] = usePoll(() => sigmaApi.jobs(), 6000);

  useEffect(() => { void sigmaApi.regime('BTC/USD', 15).then((r) => r && setRegime(r)); }, []);
  const s: SafetySnapshot | null = safety;

  return (
    <PanelShell title="Risk Gauges" icon={<Gauge size={13} />}
      actions={<>
        <button onClick={() => sigmaApi.pause().then(refreshSafety)}
          className="rounded border border-amber-600/60 px-2 py-0.5 text-[10px] text-amber-400 hover:bg-amber-500/10">PAUSE</button>
        <button onClick={() => sigmaApi.kill().then(refreshSafety)}
          className="rounded border border-red-600/60 px-2 py-0.5 text-[10px] text-red-400 hover:bg-red-500/10">KILL</button>
        <button onClick={() => sigmaApi.release().then(refreshSafety)}
          className="rounded border border-emerald-600/60 px-2 py-0.5 text-[10px] text-emerald-400 hover:bg-emerald-500/10">RELEASE</button>
      </>}>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Kill Switch" value={s?.kill_switch ? 'ENGAGED' : 'clear'} tone={s?.kill_switch ? 'text-red-400' : 'text-emerald-400'} />
        <Stat label="Pause" value={s?.pause ? 'ACTIVE' : 'clear'} tone={s?.pause ? 'text-amber-400' : 'text-emerald-400'} />
        <Stat label="Daily PnL / Limit" value={`${(s?.daily_pnl_usd ?? 0).toFixed(2)} / -${s?.max_daily_loss_usd ?? 600}`} />
        <Stat label="Errors" value={`${s?.consecutive_errors ?? 0} / ${s?.max_consecutive_errors ?? 3}`} />
        <Stat label="Live Trading" value={s?.live_trading ? 'LIVE' : 'SHADOW'} tone={s?.live_trading ? 'text-red-400' : 'text-sky-400'} />
        <Stat label="Halt Action" value={s?.halt_action ?? 'cancel_all'} />
      </div>
      <div className="mt-3 rounded border border-zinc-800 bg-zinc-900/40 p-2">
        <div className="mb-1 text-[10px] uppercase text-zinc-500">Regime BTC/USD 15m</div>
        {regime ? (
          <div className="grid grid-cols-2 gap-1 font-mono text-[11px]">
            <div className={regime.crisis ? 'text-red-400' : 'text-emerald-400'}>{regime.regime}</div>
            <div>ATR pctl {regime.atr_percentile.toFixed(1)}</div>
            <div>EMAΔ {regime.ema_delta_pct.toFixed(2)}%</div>
            <div>H {regime.hurst.toFixed(2)} · {regime.hurst_class}</div>
          </div>
        ) : <div className="text-zinc-600">scraper offline — regime unavailable</div>}
      </div>
      <div className="mt-2 text-[10px] text-zinc-500">
        TV jobs queued {jobs?.queued ?? 0} · concurrency {jobs?.concurrency ?? 1} · cache {jobs?.cache_entries ?? 0}
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------------- 7 SelfOptimizingMLPanel */

export function SelfOptimizingMLPanel() {
  const [ml, refresh] = usePoll(sigmaApi.mlState, 6000);
  const m: MlSnapshot | null = ml;
  return (
    <PanelShell title="Self-Optimizing ML" icon={<Sparkles size={13} />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Brier Score" value={(m?.brier ?? 0).toFixed(4)}
          tone={(m?.brier ?? 0) > (m?.brier_threshold ?? 0.28) ? 'text-red-400' : 'text-emerald-400'} />
        <Stat label="Threshold" value={m?.brier_threshold ?? 0.28} />
        <Stat label="Temperature" value={(m?.temperature ?? 1).toFixed(2)} />
        <Stat label="Samples" value={`${m?.samples ?? 0} / ${m?.min_samples ?? 30}`} />
        <Stat label="Drift" value={m?.drift ? 'YES' : 'no'} tone={m?.drift ? 'text-red-400' : 'text-emerald-400'} />
        <Stat label="ONNX Model" value={m?.model_available ? 'loaded' : 'heuristic'} />
      </div>
      <div className="mt-2 text-[10px] leading-relaxed text-zinc-500">
        BS &gt; 0.28 ⇒ Temperatur steigt ⇒ Konfidenz gedämpft ⇒ Kelly schrumpft.
        Re-Training läuft nur nach bestandenem Shadow-Gate (Hot-Reload, zero downtime).
        Retrains: {m?.retrain_count ?? 0}{m?.retrain_requested ? ' · retrain requested' : ''}
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------------- 8 TelegramOperatorPanel */

export function TelegramOperatorPanel() {
  const [t, refresh] = usePoll(sigmaApi.telegram, 8000);
  return (
    <PanelShell title="Telegram Operator" icon={<MessageSquare size={13} />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="mb-2 grid grid-cols-2 gap-2">
        <Stat label="Bot" value={t?.enabled ? 'connected' : 'token missing'} tone={t?.enabled ? 'text-emerald-400' : 'text-zinc-500'} />
        <Stat label="Whitelist" value={t?.whitelist?.join(', ') || '—'} />
      </div>
      <div className="mb-2 text-[10px] text-zinc-500">
        Fast-Path (&lt;{t?.fast_path_budget_ms ?? 50}ms): {(t?.fast_path_commands ?? []).join(' ')}
      </div>
      <div className="space-y-1 font-mono text-[11px]">
        {(t?.log ?? []).slice(-14).reverse().map((m, i) => (
          <div key={i} className="flex gap-2">
            <span className={m.direction === 'IN' ? 'text-sky-400' : m.direction === 'PUSH' ? 'text-amber-400' : 'text-emerald-400'}>
              {m.direction}
            </span>
            <span className="flex-1 truncate text-zinc-300">{m.text}</span>
            {m.latency_ms > 0 && <span className="text-zinc-600">{m.latency_ms.toFixed(1)}ms</span>}
          </div>
        ))}
        {!t?.log?.length && <div className="text-zinc-600">No traffic yet.</div>}
      </div>
    </PanelShell>
  );
}

/* --------------------------------------------------- 9 DeadmanSwitchPanel */

export function DeadmanSwitchPanel() {
  const [dm, refresh] = usePoll(sigmaApi.deadman, 3000);
  const d: DeadmanSnapshot | null = dm;
  const pct = Math.min(100, ((d?.age_s ?? 0) / (d?.timeout_s || 60)) * 100);
  return (
    <PanelShell title="Deadman Switch" icon={<HeartPulse size={13} />}
      actions={<button onClick={() => sigmaApi.deadmanBeat(true).then(refresh)}
        className="rounded border border-emerald-600/60 px-2 py-0.5 text-[10px] text-emerald-400 hover:bg-emerald-500/10">BEAT</button>}>
      <div className="mb-2 h-2 w-full overflow-hidden rounded bg-zinc-800">
        <div className={`h-full transition-all ${pct > 80 ? 'bg-red-500' : pct > 50 ? 'bg-amber-500' : 'bg-emerald-500'}`}
          style={{ width: `${pct}%` }} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Heartbeat Age" value={`${(d?.age_s ?? 0).toFixed(1)}s`} />
        <Stat label="Timeout" value={`${d?.timeout_s ?? 60}s`} />
        <Stat label="Native Bracket-SL" value={d?.has_native_stop_loss ? 'yes' : 'no'}
          tone={d?.has_native_stop_loss ? 'text-emerald-400' : 'text-amber-400'} />
        <Stat label="Triggers" value={d?.trigger_count ?? 0} tone={(d?.trigger_count ?? 0) > 0 ? 'text-red-400' : undefined} />
      </div>
      <div className="mt-2 text-[10px] text-zinc-500">
        Timeout ⇒ {d?.has_native_stop_loss ? 'cancel open entry limits (exchange stop stays alive)' : 'close_all_market'}
        {d?.last_action ? ` · last: ${d.last_action}` : ''}
      </div>
    </PanelShell>
  );
}

/* -------------------------------------------------- 10 RewardXPMatrixPanel */

export function RewardXPMatrixPanel() {
  const [data, refresh] = usePoll(sigmaApi.rewardMatrix, 8000);
  const rows: RewardRow[] = data?.matrix ?? [];
  return (
    <PanelShell title="Reward · XP / Strikes" icon={<Zap size={13} />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="mb-2 text-[10px] text-zinc-500">
        R = w1·PnL + w2·(MFE/MAE) − w3·Time − w4·Fee · 3 Strikes ⇒ Quarantäne
      </div>
      <table className="w-full text-left font-mono text-[11px]">
        <thead className="text-[10px] uppercase text-zinc-500">
          <tr><th className="py-1">Strategy</th><th>XP</th><th>Strikes</th><th>Trades</th><th>Ø R</th><th>x</th><th>Grades</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strategy_id} className="border-t border-zinc-800/60">
              <td className="py-1 pr-2 text-zinc-300">{r.strategy_id}</td>
              <td className="text-emerald-400">{r.xp}</td>
              <td className={r.strikes >= 3 ? 'text-red-400' : 'text-amber-400'}>{r.strikes}</td>
              <td>{r.trades}</td>
              <td>{r.avg_reward.toFixed(2)}</td>
              <td className={r.quarantined ? 'text-red-400' : ''}>{r.budget_multiplier}</td>
              <td className="text-zinc-500">{r.recent_grades.slice(-6).join('')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <div className="text-zinc-600">No scored trades yet.</div>}
    </PanelShell>
  );
}

/* -------------------------------------------------- 11 MemoryWatchdogPanel */

export function MemoryWatchdogPanel() {
  const [mem, refresh] = usePoll(sigmaApi.memory, 6000);
  const m: MemorySnapshot | null = mem;
  return (
    <PanelShell title="Memory Watchdog" icon={<MemoryStick size={13} />}
      actions={<button onClick={() => sigmaApi.memoryCheck(true).then(refresh)}
        className="rounded border border-zinc-700 px-2 py-0.5 text-[10px] hover:border-sky-500">CHECK</button>}>
      <div className="mb-2 h-2 w-full overflow-hidden rounded bg-zinc-800">
        <div className={`h-full ${(m?.percent ?? 0) > 92 ? 'bg-red-500' : (m?.percent ?? 0) > 85 ? 'bg-amber-500' : 'bg-emerald-500'}`}
          style={{ width: `${Math.min(100, m?.percent ?? 0)}%` }} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="RAM" value={`${(m?.percent ?? 0).toFixed(1)}%`} />
        <Stat label="Stage" value={`${m?.stage ?? 0} / 4`} tone={(m?.stage ?? 0) >= 3 ? 'text-red-400' : undefined} />
        <Stat label="CGroup Max" value={m?.cgroup_memory_max ?? '4G'} />
        <Stat label="Chromium reaped" value={m?.chromium_zombies_reaped ?? 0} />
      </div>
      <div className="mt-2 space-y-0.5 text-[10px] text-zinc-500">
        {(m?.stages_pct ?? [75, 85, 92, 96]).map((p, i) => (
          <div key={p} className={(m?.stage ?? 0) === i + 1 ? 'text-amber-400' : ''}>
            {p}% → {(m?.actions ?? [])[i] ?? '—'}
          </div>
        ))}
      </div>
    </PanelShell>
  );
}

/* --------------------------------------------- extra: TV job / ops footer  */

export function TvJobsPanel() {
  const [data, refresh] = usePoll(() => sigmaApi.jobs(), 5000);
  const jobs: TvJob[] = data?.jobs ?? [];
  return (
    <PanelShell title="TV Job Queue" icon={<Cpu size={13} />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="space-y-1 font-mono text-[11px]">
        {jobs.slice(0, 20).map((j) => (
          <div key={j.job_id} className="flex items-center gap-2 border-b border-zinc-800/60 py-1">
            <span className="w-28 truncate text-zinc-400">{j.job_id}</span>
            <span className="w-20 text-zinc-500">{j.kind}</span>
            <span className="flex-1 truncate">{j.strategy_id || j.symbol}</span>
            <span className={j.status === 'failed' ? 'text-red-400' : j.status === 'done' ? 'text-emerald-400' : 'text-amber-400'}>
              {j.status}{j.error_code ? ` (${j.error_code})` : ''}
            </span>
            {j.status === 'queued' && (
              <button onClick={() => sigmaApi.cancelJob(j.job_id).then(refresh)}
                className="text-[10px] text-zinc-500 hover:text-red-400">cancel</button>
            )}
          </div>
        ))}
        {!jobs.length && <div className="text-zinc-600">No TV jobs — concurrency stays at 1 by spec.</div>}
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------ extra: Loop C Market Radar */

export function MarketRadarPanel() {
  const [category, setCategory] = useState<'gainers' | 'losers'>('gainers');
  const [rows, setRows] = useState<MoverRow[]>([]);
  const [feed, setFeed] = useState<FeedMeta | null>(null);
  const [health, setHealth] = useState<ScraperHealth | null>(null);

  const load = useCallback(async () => {
    const [movers, hp] = await Promise.all([
      sigmaApi.movers('crypto', category, 15),
      sigmaApi.scraperHealth(),
    ]);
    setRows(movers?.rows ?? []);
    setFeed(movers?.feed ?? null);
    setHealth(hp);
  }, [category]);

  useEffect(() => { void load(); const id = setInterval(load, 60_000); return () => clearInterval(id); }, [load]);

  return (
    <PanelShell title="Market Radar (Loop C)" icon={<Radar size={13} />}
      actions={<>
        <FeedBadge feed={feed} />
        <select value={category} onChange={(e) => setCategory(e.target.value as 'gainers' | 'losers')}
          className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 text-[10px]">
          <option value="gainers">gainers</option>
          <option value="losers">losers</option>
        </select>
        <IconBtn onClick={load} title="Refresh"><RefreshCw size={12} /></IconBtn>
      </>}>
      <div className="mb-2 grid grid-cols-3 gap-2">
        <Stat label="Sidecar" value={health?.ok ? 'online' : 'offline'}
          tone={health?.ok ? 'text-emerald-400' : 'text-red-400'} />
        <Stat label="Vendor" value={health?.vendor?.importable ? 'loaded' : 'missing'}
          tone={health?.vendor?.importable ? 'text-emerald-400' : 'text-amber-400'} />
        <Stat label="Cache hit" value={`${Math.round((health?.cache?.hit_ratio ?? 0) * 100)}%`} />
      </div>
      <table className="w-full text-left font-mono text-[11px]">
        <thead className="text-[10px] uppercase text-zinc-500">
          <tr><th className="py-1">Symbol</th><th>Last</th><th>Change</th><th>Volume</th></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.name}-${i}`} className="border-t border-zinc-800/60">
              <td className="py-1 text-zinc-300">{r.name}</td>
              <td>{Number(r.close).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
              <td className={r.change >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                {r.change >= 0 ? '+' : ''}{Number(r.change).toFixed(2)}%
              </td>
              <td className="text-zinc-500">{Number(r.volume).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <div className="text-zinc-600">No movers — is bin/sigma-scraper running on :8001?</div>}
      <div className="mt-2 text-[10px] text-zinc-500">
        {health?.base_url ?? 'http://127.0.0.1:8001'} · rate {health?.rate_limit?.rate_per_min ?? 60}/min
        {health?.rate_limit?.rejected ? ` · ${health.rate_limit.rejected} throttled` : ''}
      </div>
    </PanelShell>
  );
}


/* ------------------------------------------- 14 OrderbookConfluencePanel §24 */

export function OrderbookConfluencePanel() {
  const [data, refresh] = usePoll(sigmaApi.confluence, 6000);
  const audits: any[] = data?.recent_audits ?? [];
  return (
    <PanelShell title="Orderbook Confluence" icon={<Radar size={13} className="text-sky-400" />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Confirm ≥" value={data?.confirm_threshold ?? '0.30'} />
        <Stat label="Veto ≤" value={data?.veto_threshold ?? '-0.20'} tone="text-rose-400" />
        <Stat label="Max Spread" value={`${data?.max_spread_bps ?? 15} bps`} />
      </div>
      <div className="mt-2 text-[10px] text-zinc-500">
        JIT only — Depth-Snapshot max {data?.max_cached_depth_age_seconds ?? 3}s alt · Bonus ×{data?.size_multiplier ?? 1.25}
      </div>
      <table className="mt-2 w-full text-left font-mono text-[11px]">
        <thead className="text-zinc-500"><tr><th>Symbol</th><th>Dir</th><th>I_depth</th><th>Spread</th><th>Verdict</th></tr></thead>
        <tbody>
          {audits.slice().reverse().map((a, i) => (
            <tr key={i} className="border-t border-zinc-800/60">
              <td>{a.symbol}</td><td>{a.direction}</td>
              <td className={a.depth_imbalance >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                {Number(a.depth_imbalance).toFixed(2)}
              </td>
              <td>{Number(a.spread_bps).toFixed(1)}</td>
              <td className={a.verdict === 'LIQUIDITY_TRAP_VETO' ? 'text-rose-400'
                : a.verdict === 'CONFLUENCE_CONFIRMED' ? 'text-emerald-400' : 'text-zinc-400'}>
                {a.verdict}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!audits.length && <div className="mt-2 text-zinc-600">Noch kein JIT-Audit ausgeführt.</div>}
    </PanelShell>
  );
}

/* ------------------------------------------ 15 SchedulerTelemetryPanel §23.2 */

export function SchedulerTelemetryPanel() {
  const [data, refresh] = usePoll(sigmaApi.scheduler, 5000);
  const tiers: any[] = data?.tiers ?? [];
  const clock = data?.clock;
  return (
    <PanelShell title="Scheduler Matrix" icon={<Activity size={13} className="text-violet-400" />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Zeitbasis" value={clock?.synced ? 'KRAKEN' : 'HOST'}
          tone={clock?.synced ? 'text-emerald-400' : 'text-amber-400'} />
        <Stat label="Offset" value={`${clock?.offset_s ?? 0}s`}
          tone={clock?.drift_warning ? 'text-amber-400' : 'text-zinc-100'} />
        <Stat label="TZ" value={data?.timezone ?? 'UTC'} />
      </div>
      <div className="mt-2 space-y-1">
        {tiers.map((t) => (
          <div key={t.tier} className="rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1">
            <div className="flex justify-between">
              <span className="font-semibold text-zinc-300">T{t.tier} · {t.label}</span>
              <span className="font-mono text-[10px] text-zinc-500">
                {t.cron ? `cron ${t.cron}` : t.cadence_s ? `${t.cadence_s}s` : 'event'}
              </span>
            </div>
            <div className="font-mono text-[10px] text-zinc-500">{t.spec_tasks.join(' · ')}</div>
            {t.registered.map((r: any) => (
              <div key={r.name} className="font-mono text-[10px] text-sky-400">
                {r.name} · runs {r.runs} · err {r.errors}
              </div>
            ))}
          </div>
        ))}
      </div>
    </PanelShell>
  );
}

/* ----------------------------------------------- 16 OrderReceiptsPanel §25 */

export function OrderReceiptsPanel() {
  const [data, refresh] = usePoll(() => sigmaApi.receipts(50), 5000);
  const rows: any[] = data?.receipts ?? [];
  const tone = (ack: string) => ack === 'FILLED' || ack === 'RETRY_SUCCESS' ? 'text-emerald-400'
    : ack === 'DUPLICATE_IGNORED' ? 'text-zinc-400'
      : ack === 'VETO_ORDERBOOK' ? 'text-amber-400' : 'text-rose-400';
  return (
    <PanelShell title="Order Receipts" icon={<Zap size={13} className="text-emerald-400" />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Max Retries" value={data?.max_retries ?? 2} />
        <Stat label="Ghost-Check" value={`${data?.ghost_fill_timeout_ms ?? 200} ms`} />
        <Stat label="Receipts" value={rows.length} />
      </div>
      <table className="mt-2 w-full text-left font-mono text-[11px]">
        <thead className="text-zinc-500"><tr><th>Pair</th><th>Side</th><th>ACK</th><th>Try</th><th>Order</th></tr></thead>
        <tbody>
          {rows.slice().reverse().map((r, i) => (
            <tr key={i} className="border-t border-zinc-800/60">
              <td>{r.pair}</td><td>{r.side}</td>
              <td className={tone(r.ack)}>{r.ack}</td>
              <td>{r.attempts}</td>
              <td className="truncate text-zinc-500">{r.order_id || r.error_code}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <div className="mt-2 text-zinc-600">Keine Orders im Closed Loop.</div>}
    </PanelShell>
  );
}

/* -------------------------------------------------- 17 RateLimiterPanel §26 */

export function RateLimiterPanel() {
  const [data, refresh] = usePoll(sigmaApi.rateLimiter, 4000);
  const kraken = data?.kraken_api;
  const tv = data?.tradingview_subscription;
  const pct = Math.round((kraken?.utilisation ?? 0) * 100);
  return (
    <PanelShell title="Rate Limiter" icon={<Gauge size={13} className="text-amber-400" />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Kraken Counter" value={`${kraken?.counter ?? 0} / ${kraken?.max_counter ?? 15}`}
          tone={kraken?.soft_cap_reached ? 'text-amber-400' : 'text-zinc-100'} />
        <Stat label="Reserve" value={kraken?.reserve_emergency_tokens ?? 3} />
      </div>
      <div className="mt-2 h-1.5 w-full rounded bg-zinc-800">
        <div className={`h-1.5 rounded ${pct >= 80 ? 'bg-amber-500' : 'bg-sky-500'}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <div className="mt-1 text-[10px] text-zinc-500">
        Soft-Cap bei {Math.round((kraken?.soft_cap_pct ?? 0.8) * 100)}% · Backoff {(data?.backoff_ladder_s ?? []).join('s / ')}s
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Stat label={`TV Tier (${tv?.tier ?? '-'})`} value={`${Object.keys(tv?.active ?? {}).length} / ${tv?.max_active_alerts ?? 0}`} />
        <Stat label="Rotation Queue" value={(tv?.rotation_queue ?? []).length} />
      </div>
      <div className="mt-2 font-mono text-[10px] text-zinc-500">
        {Object.entries(tv?.active ?? {}).map(([sid, score]) => `${sid}:${score}`).join(' · ') || 'keine aktiven Alerts'}
      </div>
    </PanelShell>
  );
}

/* ------------------------------------------------ 18 ContagionRadarPanel §27 */

export function ContagionRadarPanel() {
  const [data, refresh] = usePoll(sigmaApi.contagion, 8000);
  const cur = data?.current;
  const tone = cur?.mode === 'FLIGHT_TO_CASH_AND_HEDGE' ? 'text-rose-400'
    : cur?.mode === 'DERISK' ? 'text-amber-400' : 'text-emerald-400';
  return (
    <PanelShell title="Contagion Radar" icon={<AlertTriangle size={13} className="text-rose-400" />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="R₀" value={cur?.r0 ?? '—'} tone={tone} />
        <Stat label="β" value={cur?.beta ?? '—'} />
        <Stat label="γ" value={cur?.gamma ?? '—'} />
      </div>
      <div className={`mt-2 rounded border border-zinc-800 bg-zinc-900/50 px-2 py-1.5 ${tone}`}>
        {cur?.mode ?? 'NORMAL'} · Sizing ×{cur?.size_multiplier ?? 1}
      </div>
      <div className="mt-1 text-[11px] text-zinc-400">{cur?.reason}</div>
      <div className="mt-2 font-mono text-[10px] text-zinc-500">
        {Object.entries(cur?.inputs ?? {}).map(([k, v]) => `${k}=${v}`).join(' · ')}
      </div>
      <div className="mt-2 text-[10px] text-zinc-500">
        Hedge ≥ {cur?.thresholds?.hedge ?? 1.5} · Derisk ≥ {cur?.thresholds?.derisk ?? 1.0}
      </div>
    </PanelShell>
  );
}

/* ----------------------------------------------- 19 FlywheelBudgetPanel §28 */

export function FlywheelBudgetPanel() {
  const [data, refresh] = usePoll(sigmaApi.flywheel, 6000);
  const sweep = async () => { await sigmaApi.flywheelSweep(); refresh(); };
  return (
    <PanelShell title="Flywheel Budget" icon={<Trophy size={13} className="text-emerald-400" />}
      actions={<>
        <IconBtn onClick={sweep} title="50/50 Sweep"><Sparkles size={12} /></IconBtn>
        <IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>
      </>}>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Futures" value={`${data?.futures_balance_eur ?? 0} €`} />
        <Stat label="Spot-Tresor" value={`${data?.vault_balance_eur ?? 0} €`} tone="text-emerald-400" />
        <Stat label="Frei" value={`${data?.free_futures_eur ?? 0} €`} />
        <Stat label="Reserviert" value={`${data?.allocated_eur ?? 0} €`} />
      </div>
      <div className="mt-2 text-[10px] text-zinc-500">
        Split {Math.round((data?.split?.reinvest_pct ?? 0.5) * 100)}/{Math.round((data?.split?.vault_pct ?? 0.5) * 100)} ab{' '}
        {data?.split?.min_split_trigger_eur ?? 10} € · Tresor {data?.vault_asset ?? 'XBT'} · Einbahnstraße{' '}
        {data?.one_way ? 'aktiv' : 'aus'} · pending {data?.pending_profit_eur ?? 0} €
      </div>
      <table className="mt-2 w-full text-left font-mono text-[11px]">
        <thead className="text-zinc-500"><tr><th>Typ</th><th>Betrag</th><th>Futures</th><th>Vault</th></tr></thead>
        <tbody>
          {(data?.recent_entries ?? []).slice().reverse().map((e: any) => (
            <tr key={e.entry_id} className="border-t border-zinc-800/60">
              <td>{e.kind}</td><td>{e.amount_eur}</td><td>{e.futures_delta_eur}</td><td>{e.vault_delta_eur}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </PanelShell>
  );
}


/* ---------------------------------------------------- 20 PaperLabPanel §32 */

export function PaperLabPanel() {
  const [data, refresh] = usePoll(() => sigmaApi.paperLab(50), 6000);
  const strategies: any[] = data?.strategies ?? [];
  const grad = data?.graduation;
  const promote = async (sid: string) => { await sigmaApi.promotePaperStrategy(sid); refresh(); };

  return (
    <PanelShell title="Kraken Paper Lab" icon={<Beaker size={13} className="text-sky-400" />}
      actions={<IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>}>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Start-Balance" value={`${data?.initial_balance_usd ?? 10000} $`} />
        <Stat label="Min Trades" value={grad?.min_paper_trades ?? 20} />
        <Stat label="Gates" value={`PF ${grad?.min_paper_profit_factor ?? 1.6} · WR ${grad?.min_paper_win_rate_pct ?? 55}%`} />
      </div>
      <div className="mt-2 space-y-2">
        {strategies.map((row) => {
          const stats = row.stats ?? {};
          const ready = row.eligible && !row.graduated;
          return (
            <div key={row.strategy_id} className="rounded border border-zinc-800 bg-zinc-900/40 p-2">
              <div className="flex items-center justify-between">
                <span className="font-mono text-[12px] text-zinc-100">{row.strategy_id}</span>
                <span className={`text-[10px] font-bold ${row.graduated ? 'text-emerald-400' : 'text-sky-400'}`}>
                  {row.graduated ? 'STUFE 3 · LIVE' : 'STUFE 2 · PAPER'}
                </span>
              </div>
              <div className="mt-1 grid grid-cols-4 gap-1 font-mono text-[11px]">
                <div><span className="text-zinc-500">n </span>{stats.trades ?? 0}</div>
                <div><span className="text-zinc-500">wr </span>{stats.win_rate_pct ?? 0}%</div>
                <div><span className="text-zinc-500">pf </span>{stats.profit_factor ?? '∞'}</div>
                <div className={(stats.net_pnl_eur ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                  <span className="text-zinc-500">pnl </span>{stats.net_pnl_eur ?? 0}€
                </div>
              </div>
              <div className="mt-1 flex items-center justify-between">
                <span className="text-[10px] text-zinc-500">
                  {row.failed_gates?.length ? `offen: ${row.failed_gates.join(', ')}` : 'alle Gates erfüllt'}
                </span>
                {ready && (
                  <button onClick={() => promote(row.strategy_id)}
                    className="rounded bg-emerald-600/80 px-2 py-0.5 text-[10px] font-semibold hover:bg-emerald-500">
                    Zu Live befördern
                  </button>
                )}
              </div>
            </div>
          );
        })}
        {!strategies.length && <div className="text-zinc-600">Noch keine Paper-Trades — Scout Loop D füllt das Labor.</div>}
      </div>
      <div className="mt-2 font-mono text-[10px] text-zinc-500">
        {data?.commands?.futures_order ?? 'kraken futures paper order ...'}
      </div>
    </PanelShell>
  );
}


/* ------------------------------------------- 21 DiagnosticsErrorPanel §36 */

const SEV_STYLE: Record<string, string> = {
  CRITICAL: 'bg-rose-600/80 text-white',
  HIGH: 'bg-amber-500/80 text-black',
  MEDIUM: 'bg-sky-600/70 text-white',
  LOW: 'bg-zinc-700 text-zinc-200',
};

export function DiagnosticsErrorPanel() {
  const [sev, setSev] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const fetcher = useCallback(() => sigmaApi.diagnostics(50, sev), [sev]);
  const [data, refresh] = usePoll(fetcher, 5000);
  const errors: any[] = data?.errors ?? [];
  const counts: Record<string, number> = data?.counts ?? {};

  const selfTest = async () => { await sigmaApi.diagnosticsSelfTest(); refresh(); };
  const clear = async () => { await sigmaApi.clearDiagnostics(); refresh(); };

  return (
    <PanelShell title="Diagnostics Desk" icon={<AlertTriangle size={13} className="text-rose-400" />}
      actions={<>
        <IconBtn onClick={selfTest} title="Selbsttest"><HeartPulse size={12} /></IconBtn>
        <a href={sigmaApi.diagnosticsExportUrl()} download
          className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" title="errors.jsonl exportieren">
          <Download size={12} />
        </a>
        <IconBtn onClick={clear} title="Puffer leeren"><Trash2 size={12} /></IconBtn>
        <IconBtn onClick={refresh} title="Refresh"><RefreshCw size={12} /></IconBtn>
      </>}>
      <div className="mb-2 flex flex-wrap gap-1">
        {['', ...(data?.severities ?? ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])].map((s: string) => (
          <button key={s || 'ALL'} onClick={() => setSev(s)}
            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${sev === s ? 'bg-zinc-200 text-zinc-900' : 'bg-zinc-800 text-zinc-400'}`}>
            {s || 'ALLE'}
          </button>
        ))}
        <span className="ml-auto font-mono text-[10px] text-zinc-500">
          {Object.values(counts).reduce((a, b) => a + b, 0)} events · {data?.log_path}
        </span>
      </div>
      <div className="space-y-1">
        {errors.map((e) => (
          <div key={`${e.trace_id}-${e.timestamp}`} className="rounded border border-zinc-800 bg-zinc-900/40 p-1.5">
            <div className="flex items-center gap-2">
              <span className={`rounded px-1 text-[9px] font-bold ${SEV_STYLE[e.severity] ?? SEV_STYLE.LOW}`}>{e.severity}</span>
              <span className="font-mono text-[11px] text-zinc-100">{e.error_code}</span>
              <span className="text-[10px] text-zinc-500">{e.subsystem} · {e.category}</span>
              <button onClick={() => setOpen(open === e.trace_id ? null : e.trace_id)}
                className="ml-auto text-[10px] text-zinc-500 hover:text-zinc-200">
                {new Date(e.timestamp).toLocaleTimeString()}
              </button>
            </div>
            <div className="mt-0.5 text-[11px] text-zinc-300">{e.message}</div>
            <div className="text-[10px] text-emerald-400/80">→ {e.remediation_hint}</div>
            {open === e.trace_id && (
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-black/50 p-1 font-mono text-[10px] text-zinc-400">
                {JSON.stringify(e.technical_context, null, 2)}
              </pre>
            )}
          </div>
        ))}
        {!errors.length && <div className="text-zinc-600">Keine Fehler im Puffer — sauber.</div>}
      </div>
    </PanelShell>
  );
}

export const PANEL_REGISTRY: Record<string, React.ComponentType> = {
  VirtualBotDeck,
  PineStudio,
  MarketChart,
  LLMConsole,
  AcademyBadgeMatrix,
  RiskGauges,
  SelfOptimizingMLPanel,
  TelegramOperatorPanel,
  DeadmanSwitchPanel,
  RewardXPMatrixPanel,
  MemoryWatchdogPanel,
  TvJobsPanel,
  MarketRadarPanel,
  OrderbookConfluencePanel,
  SchedulerTelemetryPanel,
  OrderReceiptsPanel,
  RateLimiterPanel,
  ContagionRadarPanel,
  FlywheelBudgetPanel,
  PaperLabPanel,
  DiagnosticsErrorPanel,
};

export const PANEL_TITLES: Record<string, string> = {
  VirtualBotDeck: 'Bot Deck',
  PineStudio: 'Pine Studio',
  MarketChart: 'Market',
  LLMConsole: 'LLM Console',
  AcademyBadgeMatrix: 'Academy Badges',
  RiskGauges: 'Risk Gauges',
  SelfOptimizingMLPanel: 'Self-Opt ML',
  TelegramOperatorPanel: 'Telegram',
  DeadmanSwitchPanel: 'Deadman',
  RewardXPMatrixPanel: 'Reward XP',
  MemoryWatchdogPanel: 'Memory',
  TvJobsPanel: 'TV Jobs',
  MarketRadarPanel: 'Market Radar',
  OrderbookConfluencePanel: 'OB Confluence',
  SchedulerTelemetryPanel: 'Scheduler',
  OrderReceiptsPanel: 'Order Receipts',
  RateLimiterPanel: 'Rate Limiter',
  ContagionRadarPanel: 'Contagion',
  FlywheelBudgetPanel: 'Flywheel',
  PaperLabPanel: 'Paper Lab',
  DiagnosticsErrorPanel: 'Diagnostics',
};
