/**
 * =========================================================
 * Datei:      src/lib/sigmaApi.ts
 * Zweck:      Typed Client für die Blueprint-L4-Routen (§7).
 *             Alle Pfade relativ — Vite proxied /api -> :8000.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */

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
}

export interface MemorySnapshot {
  percent: number;
  stage: number;
  stages_pct: number[];
  actions: string[];
  cgroup_memory_max: string;
  idle_only: boolean;
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

export const sigmaApi = {
  health: () => request<HealthResponse>('/api/v1/health'),
  blueprint: () => request<any>('/api/v1/blueprint'),

  // Safety / Ops
  safety: () => request<SafetySnapshot>('/api/v1/safety'),
  kill: () => post<any>('/api/v1/safety/kill'),
  pause: () => post<SafetySnapshot>('/api/v1/safety/pause'),
  release: () => post<SafetySnapshot>('/api/v1/safety/release'),
  deadman: () => request<DeadmanSnapshot>('/api/v1/deadman'),
  deadmanBeat: (native = true) => post<DeadmanSnapshot>(`/api/v1/deadman/beat?has_native_stop_loss=${native}`),
  memory: () => request<MemorySnapshot>('/api/v1/memory'),
  memoryCheck: (force = false) => post<any>(`/api/v1/memory/check?force=${force}`),

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

  // Market / Regime
  ohlc: (symbol: string, interval: number, count = 300) =>
    request<{ candles: Candle[] }>(`/api/v1/market/ohlc?symbol=${encodeURIComponent(symbol)}&interval=${interval}&count=${count}`),
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

  regime: (symbol: string, interval: number) =>
    request<RegimeVector>(`/api/v1/regime?symbol=${encodeURIComponent(symbol)}&interval=${interval}`),

  // Academy / Reward / ML / Scout
  badges: (strategyId = '') => request<{ matrix: BadgeRow[]; profiles: number; ratings: Record<string, number> }>(
    `/api/v1/academy/badges${strategyId ? `?strategyId=${strategyId}` : ''}`),
  trainingDataset: () => request<{ rows: any[]; count: number }>('/api/v1/academy/training-dataset'),
  rewardMatrix: () => request<{ matrix: RewardRow[]; weights: Record<string, number> }>('/api/v1/reward/matrix'),
  mlState: () => request<MlSnapshot>('/api/v1/ml/self-optimizing'),
  scout: () => request<any>('/api/v1/scout'),
  scoutPlan: (ids: string[]) => post<any>('/api/v1/scout/plan', ids),

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
