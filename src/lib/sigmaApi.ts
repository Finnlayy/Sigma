/**
 * =========================================================
 * Datei:      src/lib/sigmaApi.ts
 * Zweck:      Typed Client für die Blueprint-L4-Routen (§7).
 *             Alle Pfade relativ — Vite proxied /api -> :8000.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */

export interface LogLine {
  subsystem: string;
  level: string;
  raw_line: string;
  timestamp: number;
}

export interface LogSources {
  stream_route: string;
  view_route: string;
  poll_interval_ms: number;
  ring_buffer_lines: number;
  masked_keys: string[];
  levels: string[];
  sources: { subsystem: string; path: string; exists: boolean; size_bytes: number; offset: number }[];
}

export interface SpecSummary {
  blueprint_version: string;
  masterprompt_version: string;
  autonomy_level: number;
  host: string;
  install_root: string;
  loops: string[];
  ports: Record<string, number>;
  axioms: string[];
  risk_guard: Record<string, number>;
  ga: Record<string, number>;
  panels: string[];
  presets: string[];
}

export interface HealthResponse {
  status: string;
  kill_switch: boolean;
  pause: boolean;
  scraper_ok: boolean;
  tv_worker_ok: boolean;
  live_trading: boolean;
  uptime: number;
  blueprint: SpecSummary;
}

export interface BotCard {
  bot_id: string;
  strategy_id: string;
  symbol: string;
  timeframe: string;
  runner_status: 'RUNNING' | 'PAUSED' | 'QUARANTINED';
  capital_eur: number;
  equity_eur: number;
  bot_pnl: number;
  max_loss: number;
  xp_strikes: { xp: number; strikes: number };
  m8_state: string;
  style: string;
  budget_multiplier: number;
  swept_to_vault: number;
  /** §29 fester Hebel + §31/§32 Herkunft und Modus */
  fixed_leverage?: number;
  leverage_badge?: string;
  execution_mode?: string;
  trigger_path?: string;
}

export interface LifecycleRun {
  run_id: string;
  strategy_id: string;
  symbol: string;
  trigger_path: string;
  execution_mode: string;
  state: 'RUNNING' | 'PAUSED' | 'QUARANTINED';
  fixed_leverage: number;
  badge: string;
  ok: boolean;
  code: string;
  reason: string;
  steps: Array<{ step: string; ok: boolean; detail: string }>;
}

export interface SafetySnapshot {
  kill_switch: boolean;
  pause: boolean;
  daily_pnl_usd: number;
  max_daily_loss_usd: number;
  consecutive_errors: number;
  max_consecutive_errors: number;
  max_open_positions: number;
  halt_action: string;
  live_trading: boolean;
}

export interface DeadmanSnapshot {
  armed: boolean;
  age_s: number;
  timeout_s: number;
  heartbeat_s: number;
  expired: boolean;
  triggered: boolean;
  trigger_count: number;
  last_action: string;
  has_native_stop_loss: boolean;
  auto_pulse?: boolean;
  pulse_source?: string;
  kraken_rtt_ms?: number | null;
  kraken_ok?: boolean;
}

export interface MemorySnapshot {
  percent: number;
  stage: number;
  source?: string;
  rss_bytes?: number;
  budget_bytes?: number;
  host_percent?: number;
  rss_percent?: number;
  stages_pct: number[];
  actions: string[];
  cgroup_memory_max: string;
  idle_only: boolean;
  idle_min_stage?: number;
  chromium_zombies_reaped: number;
  history: Array<{ stage: number; action: string; percent: number; ts: number; detail: string }>;
}

export interface BadgeRow {
  strategy_id: string;
  symbol: string;
  timeframe: string;
  regime: string;
  trade_count: number;
  win_rate: number;
  profit_factor: number;
  rating: string;
  is_allowed: boolean;
  badge: string;
}

export type StrategyLamp = 'gray' | 'yellow' | 'green_solid' | 'green_glow' | 'red_glow';

export interface ScorecardKpis {
  trade_count: number;
  win_rate: number;
  profit_factor: number;
  net_pnl: number;
}

export interface ScorecardSlot {
  strategy_id: string;
  symbol: string;
  timeframe: string;
  regime: string;
  origin: 'user' | 'academy';
  lamp: StrategyLamp;
  locked: boolean;
  favorite: boolean;
  pf_after_fees: number;
  last_job_id: string;
  verified_at: number | null;
}

export interface LibrarySnapshotRow {
  id: string;
  name: string;
  status: string;
  executionMode: string;
  assetPair: string;
  interval: number;
  tv: boolean;
  lamp: StrategyLamp;
  kpis: ScorecardKpis;
  primary_badge: string;
  best_symbol: string;
  best_tf: string;
  stage1_done: boolean;
}

export interface StrategyScorecard {
  ok: boolean;
  strategy: Record<string, unknown>;
  header: {
    strategy_id: string;
    lamp: StrategyLamp;
    stage1_done: boolean;
    pf_after_fees: number;
    last_init_job_id: string;
    last_validate_job_id: string;
  };
  kpis: ScorecardKpis;
  slots: ScorecardSlot[];
  badges: BadgeRow[];
  lamp: StrategyLamp;
}

export interface RewardRow {
  strategy_id: string;
  xp: number;
  strikes: number;
  trades: number;
  avg_reward: number;
  recent_grades: string[];
  budget_multiplier: number;
  quarantined: boolean;
}

export interface MlSnapshot {
  brier: number;
  brier_threshold: number;
  temperature: number;
  samples: number;
  min_samples: number;
  drift: boolean;
  retrain_requested: boolean;
  retrain_count: number;
  model_available: boolean;
}

export interface RegimeVector {
  symbol?: string;
  regime: string;
  ema_delta_pct: number;
  atr_percentile: number;
  volatility_band: string;
  hurst: number;
  hurst_class: string;
  crisis: boolean;
  entry_blocked: boolean;
  sample_size: number;
}

export interface Candle { ts: number; o: number; h: number; l: number; c: number; v: number }

/** Herkunft der Loop-C-Daten: echtes Sidecar, abgelaufener Cache oder Offline-Fallback. */
export interface FeedMeta {
  source: 'tv_scraper' | 'cache_stale' | 'synthetic' | 'unknown';
  degraded: boolean;
  cached: boolean;
  age_s?: number | null;
  upstream_error?: string | null;
}

export interface ScraperHealth {
  ok: boolean;
  base_url: string;
  degraded: boolean;
  error?: string;
  vendor?: { importable?: boolean; import_error?: string | null; calls?: number; failures?: number; offline_mode?: boolean };
  cache?: { entries?: number; hits?: number; misses?: number; hit_ratio?: number };
  rate_limit?: { tokens?: number; capacity?: number; rate_per_min?: number; rejected?: number };
  endpoints?: Record<string, string>;
  market_source_prod?: string;
}

export interface MoverRow {
  name: string;
  close: number;
  change: number;
  volume: number;
  market?: string;
}

export interface TvJob {
  job_id: string;
  kind: string;
  strategy_id: string;
  symbol: string;
  status: string;
  progress: number;
  error: string;
  error_code: string;
  eta_s: number;
  created_at: number;
}

export interface TvLibraryScript {
  tv_script_id: string;
  name: string;
  type: string;
  version?: string;
  symbol?: string;
  interval?: number | string;
  url?: string;
  origin?: string;
  has_source?: boolean;
  already_imported?: boolean;
  library_id?: string;
  library_execution_mode?: string;
}

export interface TvLibraryCatalog {
  scripts: TvLibraryScript[];
  source: string;
  session_present: boolean;
  driver: string;
  reason?: string;
  count: number;
  imported_count?: number;
}

export interface TvLibrarySyncResult {
  ok: boolean;
  source: string;
  session_present: boolean;
  driver: string;
  reason?: string;
  execution_mode: string;
  live_trading: boolean;
  imported: Array<{ tv_script_id: string; name: string; library_id: string; executionMode: string; status: string }>;
  skipped: Array<{ tv_script_id: string; name: string; library_id: string; reason: string }>;
  missing: string[];
  imported_count: number;
  skipped_count: number;
  strategies?: unknown[];
}

export interface AlertRecord {
  strategy_id: string;
  name: string;
  symbol: string;
  interval: string;
  tv_alert_id: string;
  enabled: boolean;
  webhook_url: string;
  status: 'ENABLED' | 'DISABLED';
  last_reason: string;
}

export interface TelegramSnapshot {
  enabled: boolean;
  whitelist: string[];
  fast_path_commands: string[];
  fast_path_budget_ms: number;
  llm_url: string;
  log: Array<{ direction: string; chat_id: string; text: string; latency_ms: number; ts: number }>;
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 6000): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
      signal: controller.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

const operatorPost = <T,>(path: string, settingsToken: string, body?: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Sigma-Settings-Token': settingsToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const operatorPut = <T,>(path: string, settingsToken: string, body?: unknown) =>
  request<T>(path, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      'X-Sigma-Settings-Token': settingsToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const sigmaApi = {
  health: () => request<HealthResponse>('/api/v1/health'),
  blueprint: () => request<any>('/api/v1/blueprint'),

  // Safety / Ops
  safety: () => request<SafetySnapshot>('/api/v1/safety'),
  kill: () => post<any>('/api/v1/safety/kill'),
  pause: () => post<SafetySnapshot>('/api/v1/safety/pause'),
  release: () => post<SafetySnapshot>('/api/v1/safety/release'),
  deadman: () => request<DeadmanSnapshot>('/api/v1/deadman'),
  deadmanBeat: (settingsToken: string, native = true) =>
    operatorPost<DeadmanSnapshot>(
      `/api/v1/deadman/beat?has_native_stop_loss=${native}`,
      settingsToken,
    ),
  memory: () => request<MemorySnapshot>('/api/v1/memory'),
  memoryCheck: (settingsToken: string, force = false) =>
    operatorPost<any>(`/api/v1/memory/check?force=${force}`, settingsToken),

  // Virtual Bots
  bots: () => request<{ bots: BotCard[]; total_equity_eur: number; total_budget_eur: number }>('/api/v1/bots'),
  createBot: (body: { strategy_id: string; symbol: string; budget_eur: number; timeframe?: string; max_loss_eur?: number }) =>
    post<BotCard>('/api/v1/bots', body),
  startBot: (id: string) => post<any>(`/api/v1/bots/${id}/start`),
  pauseBot: (id: string) => post<any>(`/api/v1/bots/${id}/pause`),
  setBotM8: (id: string, state: string) => post<BotCard>(`/api/v1/bots/${id}/m8/${state}`),

  // Alerts
  alerts: () => request<{ alerts: AlertRecord[]; webhook_url: string; secret_configured: boolean; enabled_count: number }>('/api/v1/alerts'),
  syncAlert: (id: string, symbol: string, interval: string | number) =>
    post<AlertRecord>(`/api/strategies/${id}/alerts/sync?symbol=${encodeURIComponent(symbol)}&interval=${interval}`),
  switchAlert: (id: string, action: 'enable' | 'disable') => post<AlertRecord>(`/api/strategies/${id}/alerts/${action}`),

  // TV Jobs / Session
  jobs: (strategyId = '') => request<{ jobs: TvJob[]; queued: number; concurrency: number; cache_entries: number }>(
    `/api/tv/jobs${strategyId ? `?strategyId=${strategyId}` : ''}`),
  submitBacktest: (body: { strategy_id: string; symbol: string; interval: number | string; params?: Record<string, unknown> }) =>
    post<TvJob>('/api/tv/jobs/backtest', body),
  pullParameters: (id: string, symbol: string, interval: number | string) =>
    post<TvJob>(`/api/strategies/${id}/tv/pull-parameters?symbol=${encodeURIComponent(symbol)}&interval=${interval}`),
  pushCode: (id: string, body: { symbol: string; interval: number | string; code: string }) =>
    post<TvJob>(`/api/strategies/${id}/tv/push`, { ...body, strategy_id: id }),
  cancelJob: (jobId: string) => post<any>(`/api/tv/jobs/${jobId}/cancel`),
  tvSession: () => request<any>('/api/tv/session/status'),
  tvLogin: () => request<any>('/api/tv/session/login', { method: 'POST', body: '{}' }, 20000),

  // Market / Regime
  ohlc: (symbol: string, interval: number, count = 300) =>
    request<{ candles: Candle[]; feed?: FeedMeta }>(`/api/v1/market/ohlc?symbol=${encodeURIComponent(symbol)}&interval=${interval}&count=${count}`),
  /** Fallback: TV-CSV/Store-Kerzen aus dem Core, falls der Scraper (:8001) schläft. */
  ohlcFallback: async (symbol: string, interval: number, count = 300): Promise<{ candles: Candle[] } | null> => {
    const raw = await request<{ candles: Array<{ time: number; open: number; high: number; low: number; close: number; volume: number }> }>(
      `/api/backtest/ohlc?pair=${encodeURIComponent(symbol)}&interval=${interval}&count=${count}`);
    if (!raw?.candles?.length) return null;
    return {
      candles: raw.candles.map((c, i) => ({
        ts: c.time > 1_000_000 ? c.time : i * interval * 60,
        o: c.open, h: c.high, l: c.low, c: c.close, v: c.volume,
      })),
    };
  },

  scraperHealth: () => request<ScraperHealth>('/api/v1/scraper/health'),
  movers: (market = 'crypto', category = 'gainers', limit = 20) =>
    request<{ rows: MoverRow[]; feed: FeedMeta; count: number }>(
      `/api/v1/market/movers?market=${market}&category=${category}&limit=${limit}`),
  screener: (market = 'crypto', sortBy = 'volume', limit = 20) =>
    request<{ rows: MoverRow[]; feed: FeedMeta; count: number }>(
      `/api/v1/market/screener?market=${market}&sort_by=${sortBy}&limit=${limit}`),
  marketIndicators: (symbol: string, interval = 1440) =>
    request<{ indicators: Record<string, number>; feed: FeedMeta }>(
      `/api/v1/market/indicators?symbol=${encodeURIComponent(symbol)}&interval=${interval}`),

  regime: (symbol: string, interval: number) =>
    request<RegimeVector>(`/api/v1/regime?symbol=${encodeURIComponent(symbol)}&interval=${interval}`),

  // Academy / Reward / ML / Scout
  badges: (strategyId = '') => request<{ matrix: BadgeRow[]; profiles: number; ratings: Record<string, number> }>(
    `/api/v1/academy/badges${strategyId ? `?strategyId=${strategyId}` : ''}`),
  librarySnapshot: () => request<{ strategies: LibrarySnapshotRow[]; count: number }>(
    '/api/v1/strategies/library-snapshot'),
  strategyScorecard: (id: string) => request<StrategyScorecard>(
    `/api/v1/strategies/${encodeURIComponent(id)}/scorecard`),
  putStrategySlots: (id: string, settingsToken: string, slots: Array<Partial<ScorecardSlot>>) =>
    operatorPut<{ ok: boolean; slots: ScorecardSlot[] }>(
      `/api/v1/strategies/${encodeURIComponent(id)}/slots`, settingsToken, { slots }),
  initializeStrategy: (id: string, settingsToken: string) =>
    operatorPost<any>(`/api/v1/strategies/${encodeURIComponent(id)}/initialize`, settingsToken),
  validateStrategy: (id: string, settingsToken: string) =>
    operatorPost<any>(`/api/v1/strategies/${encodeURIComponent(id)}/validate`, settingsToken),
  trainingDataset: () => request<{ rows: any[]; count: number }>('/api/v1/academy/training-dataset'),
  rewardMatrix: () => request<{ matrix: RewardRow[]; weights: Record<string, number> }>('/api/v1/reward/matrix'),
  mlState: () => request<MlSnapshot>('/api/v1/ml/self-optimizing'),
  scout: () => request<any>('/api/v1/scout'),
  scoutPlan: (ids: string[]) => post<any>('/api/v1/scout/plan', ids),

  // Execution Plane §23-§29 (erweiterte Panels §30)
  clock: () => request<any>('/api/v1/clock'),
  scheduler: () => request<any>('/api/v1/scheduler'),
  confluence: () => request<any>('/api/v1/orderbook/confluence'),
  auditConfluence: (body: { symbol: string; direction: string; bids: Array<{ price: number; volume: number }>; asks: Array<{ price: number; volume: number }> }) =>
    post<any>('/api/v1/orderbook/confluence', body),
  receipts: (limit = 50) => request<any>(`/api/orders/receipts?limit=${limit}`),
  rateLimiter: () => request<any>('/api/v1/rate-limiter'),
  contagion: () => request<any>('/api/v1/contagion'),
  flywheel: () => request<any>('/api/v1/flywheel'),
  flywheelSweep: (settingsToken: string) =>
    operatorPost<any>('/api/v1/flywheel/sweep', settingsToken),
  leverage: (strategyId: string) => request<any>(`/api/v1/leverage/${encodeURIComponent(strategyId)}`),

  // §38 Netron ONNX Inspector
  netronStatus: () => request<any>('/api/v1/models/netron/status'),
  netronStart: (model = '') => post<any>(`/api/v1/models/netron/start${model ? `?model=${encodeURIComponent(model)}` : ''}`, {}),
  inspectModel: (versionTag: string) => post<any>(`/api/v1/models/inspect/${encodeURIComponent(versionTag)}`, {}),

  // §34 LLM Tool-Contracts
  llmTools: () => request<any>('/api/v1/llm/tools'),
  llmToolCall: (toolName: string, args: Record<string, unknown>) =>
    post<any>('/api/v1/llm/tool-call', { tool_name: toolName, arguments: args }),
  llmStreamUrl: () => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/api/v1/llm/stream`;
  },
  fromTemplate: (template: string, name?: string) =>
    post<any>('/api/strategies/from-template', { template, name }),
  tvScripts: () => request<TvLibraryCatalog>('/api/strategies/tv/scripts', undefined, 25000),
  syncTvLibrary: (body?: { script_ids?: string[]; symbol?: string; interval?: number }) =>
    request<TvLibrarySyncResult>('/api/strategies/tv/sync-library', {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    }, 25000),

  // §37 Live Process & AI Log Console
  logSources: () => request<LogSources>('/api/v1/logs/sources'),
  logTail: (filter = '', limit = 200) =>
    request<{ subsystems: string[]; lines: LogLine[] }>(
      `/api/v1/logs/tail?limit=${limit}${filter ? `&filter=${encodeURIComponent(filter)}` : ''}`),
  logPoll: (filter = '') =>
    request<{ subsystems: string[]; lines: LogLine[] }>(
      `/api/v1/logs/poll${filter ? `?filter=${encodeURIComponent(filter)}` : ''}`),
  logStreamUrl: (filter = '') => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const qs = filter ? `?filter=${encodeURIComponent(filter)}` : '';
    return `${proto}//${window.location.host}/api/v1/logs/stream${qs}`;
  },

  // §36 Diagnostics Error Desk
  diagnostics: (limit = 50, severity = '') =>
    request<any>(`/api/v1/diagnostics/errors?limit=${limit}${severity ? `&severity=${severity}` : ''}`),
  diagnosticsSelfTest: () => post<any>('/api/v1/diagnostics/self-test', {}),
  clearDiagnostics: () => post<any>('/api/v1/diagnostics/clear', {}),
  diagnosticsExportUrl: () => '/api/v1/diagnostics/export',

  // §32 Kraken Paper Lab
  paperLab: (limit = 50) => request<any>(`/api/v1/paper-lab?limit=${limit}`),
  paperLabStrategy: (strategyId: string) => request<any>(`/api/v1/paper-lab/${encodeURIComponent(strategyId)}`),
  promotePaperStrategy: (strategyId: string, force = false) =>
    post<any>(`/api/v1/paper-lab/${encodeURIComponent(strategyId)}/promote`, { reason: 'operator', force }),

  // §31 Strategy Lifecycle — 3 Trigger-Pfade
  lifecycle: (limit = 25) => request<{ active: Record<string, string>; runs: LifecycleRun[]; trigger_paths: Record<string, string[]>; steps: string[] }>(
    `/api/v1/lifecycle?limit=${limit}`),
  lifecycleFor: (strategyId: string) => request<LifecycleRun>(`/api/strategies/${encodeURIComponent(strategyId)}/lifecycle`),
  startStrategy: (strategyId: string, body: { symbol: string; budget_eur?: number; trigger_path?: string; execution_mode?: string; fixed_leverage?: number; timeframe?: string }) =>
    post<LifecycleRun>(`/api/strategies/${encodeURIComponent(strategyId)}/start`, body),
  pauseStrategy: (strategyId: string, reason = 'operator') =>
    post<any>(`/api/strategies/${encodeURIComponent(strategyId)}/pause`, { reason }),
  resumeStrategy: (strategyId: string, reason = 'operator') =>
    post<any>(`/api/strategies/${encodeURIComponent(strategyId)}/resume`, { reason }),
  quarantineStrategy: (strategyId: string, reason = 'risk') =>
    post<any>(`/api/strategies/${encodeURIComponent(strategyId)}/quarantine`, { reason }),

  // Telegram / LLM
  telegram: () => request<TelegramSnapshot>('/api/v1/telegram'),
  telegramSend: (chat_id: string, text: string) => post<any>('/api/v1/telegram/message', { chat_id, text }),

  pipeline: () => request<any>('/api/v1/signal/pipeline'),
};

export function ratingColor(rating: string): string {
  switch (rating) {
    case 'S': return 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10';
    case 'A': return 'text-green-400 border-green-500/40 bg-green-500/10';
    case 'B': return 'text-sky-400 border-sky-500/40 bg-sky-500/10';
    case 'C': return 'text-amber-400 border-amber-500/40 bg-amber-500/10';
    case 'F': return 'text-red-400 border-red-500/40 bg-red-500/10';
    default: return 'text-zinc-400 border-zinc-600/40 bg-zinc-700/20';
  }
}

export function m8Color(state: string): string {
  switch (state) {
    case 'ACTIVE': return 'text-emerald-400';
    case 'THROTTLED': return 'text-amber-400';
    case 'QUARANTINED': return 'text-red-400';
    case 'RETIRED': return 'text-zinc-500';
    default: return 'text-zinc-400';
  }
}

// =============================================================================
// MP-17 — Sigma Research-/Panel-Endpunkte (fail-closed Read-Only)
// Zeiten: UNIX-Sekunden (UTC); Prozentangaben als Dezimalen (0.06 = 6 %).
// Ohne Fachmodule liefern die Routen strukturierte Leerantworten
// (ok=false, available=false, leere Arrays, feed.source="unknown").
// =============================================================================

export interface SigmaFeedMeta {
  source: 'tv_scraper' | 'cache_stale' | 'synthetic' | 'unknown';
  available: boolean;
  degraded: boolean;
  cached?: boolean;
  age_s?: number | null;
  upstream_error?: string | null;
  error?: string | null;
}

export interface SigmaPanelBase {
  ok: boolean;
  available: boolean;
  feed: SigmaFeedMeta;
  generated_at?: string | null;
}

export interface SigmaRegimeState extends SigmaPanelBase {
  phase?: string | null;
  minute?: number | null;
  last_scan_ts?: number | null;
  wave_status?: string | null;
  range_high?: number | null;
  range_low?: number | null;
  eq?: number | null;
  ce50?: number | null;
  session_window?: string | null;
  session_quarantine?: boolean | null;
  throttle_state?: string | null;
  throttle_bots?: number | null;
  hurst_htf?: number | null;
  poly_bias?: string | null;
  poly_p_cal?: number | null;
  onnx_action?: string | null;
  onnx_model_available?: boolean | null;
  shadow_plan?: Record<string, unknown> | null;
}

export interface SigmaRiskState extends SigmaPanelBase {
  positions: Array<Record<string, unknown>>;
  rules: Array<{ id: string; label: string; enabled: boolean }>;
}

export interface SigmaPowerState extends SigmaPanelBase {
  cos_phi?: number | null;
  cluster?: string | null;
  s_norm?: number | null;
  p_norm?: number | null;
  q_norm?: number | null;
  q_upper?: number | null;
  q_lower?: number | null;
  q_bias?: number | null;
  cos_path: Array<{ time: number; value: number }>;
  resonance?: number | null;
  resonance_badge?: string | null;
}

export interface SigmaZonesState extends SigmaPanelBase {
  interval_min?: number | null;
  zones: Array<Record<string, unknown>>;
  envelope?: Record<string, unknown> | null;
  events: Array<Record<string, unknown>>;
}

export interface SigmaScoutState extends SigmaPanelBase {
  last_scan_ts?: number | null;
  phase_ok?: boolean | null;
  long_rank: Array<Record<string, unknown>>;
  short_rank: Array<Record<string, unknown>>;
  rejected: Array<Record<string, unknown>>;
  filters: Record<string, unknown>;
  blinded?: boolean | null;
}

export interface SigmaPolymarketState extends SigmaPanelBase {
  bins: Array<Record<string, unknown>>;
  term_structure: Array<Record<string, unknown>>;
  mu?: number | null;
  bias?: string | null;
  platt_a?: number | null;
  platt_b?: number | null;
  brier?: number | null;
  p_cal?: number | null;
  gate_open?: boolean | null;
}

export interface SigmaExhaustionState extends SigmaPanelBase {
  score?: number | null;
  exhausted?: boolean | null;
  components: Record<string, unknown>;
  unwind: Array<Record<string, unknown>>;
  forced?: boolean | null;
  ttl_flat?: boolean | null;
}

export interface SigmaProvisionState extends SigmaPanelBase {
  provisions: Array<Record<string, unknown>>;
  harden_supported: boolean;
}

export interface SigmaLadderPreview extends SigmaPanelBase {
  rungs: Array<Record<string, unknown>>;
  guards: Array<{ id: string; ok: boolean; reason?: string }>;
  deploy_allowed: boolean;
  avg_fill_price?: number | null;
  total_depth_pct?: number | null;
}

export interface SigmaFractalPreview extends SigmaPanelBase {
  side?: string | null;
  leverage?: number | null;
  entry?: number | null;
  tranches: Array<Record<string, unknown>>;
  initial_sl?: number | null;
  sl_basis?: string | null;
  fee_covered_be?: number | null;
  kill_switch: Record<string, unknown>;
}

export interface SigmaOnnxState extends SigmaPanelBase {
  tensor: Array<{ name: string; value: number }>;
  action_probs: { long: number; flat: number; short: number };
  action?: string | null;
  leverage?: number | null;
  entropy?: number | null;
  model_available?: boolean | null;
  bar_lock?: string | null;
  latency_ms?: number | null;
}

export interface SigmaOrderflowState extends SigmaPanelBase {
  reason?: string | null;
}

export interface SigmaWriteResult {
  ok: boolean;
  available: boolean;
  reason: string;
  job_id?: string | null;
  detail?: Record<string, unknown> | null;
}

export interface ResearchJob {
  job_id: string;
  hypothesis: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'unavailable';
  progress: number;
  error?: string | null;
  created_at?: number | null;
}

export interface ResearchJobResult extends ResearchJob {
  result?: Record<string, unknown> | null;
}

export interface ResearchDashboard extends SigmaPanelBase {
  hypotheses: Array<Record<string, unknown>>;
  sweeps: Array<Record<string, unknown>>;
  export_html_path?: string | null;
}

const sigmaGet = <T,>(path: string) => request<T>(path);

export const sigmaResearchApi = {
  regime: () => sigmaGet<SigmaRegimeState>('/api/v1/sigma/regime'),
  risk: () => sigmaGet<SigmaRiskState>('/api/v1/sigma/risk'),
  power: () => sigmaGet<SigmaPowerState>('/api/v1/sigma/power'),
  zones: () => sigmaGet<SigmaZonesState>('/api/v1/sigma/zones'),
  scout: () => sigmaGet<SigmaScoutState>('/api/v1/sigma/scout'),
  polymarket: () => sigmaGet<SigmaPolymarketState>('/api/v1/sigma/polymarket'),
  exhaustion: () => sigmaGet<SigmaExhaustionState>('/api/v1/sigma/exhaustion'),
  provisions: () => sigmaGet<SigmaProvisionState>('/api/v1/sigma/provisions'),
  ladderPreview: () => sigmaGet<SigmaLadderPreview>('/api/v1/sigma/ladder/preview'),
  fractalPreview: () => sigmaGet<SigmaFractalPreview>('/api/v1/sigma/fractal/preview'),
  onnx: () => sigmaGet<SigmaOnnxState>('/api/v1/sigma/onnx'),
  orderflow: () => sigmaGet<SigmaOrderflowState>('/api/v1/sigma/orderflow'),

  // Operator-Schreibzugriffe (Token + Modal im UI)
  scan: (settingsToken: string) =>
    operatorPost<SigmaWriteResult>('/api/v1/sigma/scan', settingsToken),
  provision: (settingsToken: string, body: Record<string, unknown>) =>
    operatorPost<SigmaWriteResult>('/api/v1/sigma/provisions', settingsToken, body),
  deProvision: (settingsToken: string, body: Record<string, unknown>) =>
    operatorPost<SigmaWriteResult>('/api/v1/sigma/provisions/de-provision', settingsToken, body),
  hardenPine: (settingsToken: string, body: Record<string, unknown>) =>
    operatorPost<SigmaWriteResult>('/api/v1/sigma/provisions/harden', settingsToken, body),

  // Research (MP-12/16)
  researchRun: (settingsToken: string, body: { hypothesis: string; params?: Record<string, unknown> }) =>
    operatorPost<SigmaWriteResult>('/api/v1/research/run', settingsToken, body),
  researchJob: (jobId: string) => sigmaGet<ResearchJobResult>(`/api/v1/research/jobs/${encodeURIComponent(jobId)}`),
  researchDashboard: () => sigmaGet<ResearchDashboard>('/api/v1/research/dashboard'),
};

/** MP-17 Blinded-Modus: Ticker nur als ASSET_### anzeigen (Ranker/Tensor). */
export function blindedSymbol(symbol: string | undefined, blinded: boolean): string {
  if (!blinded || !symbol) return symbol ?? '—';
  const m = /(\d+)$/.exec(symbol);
  return m ? `ASSET_${m[1]}` : 'ASSET_???';
}
