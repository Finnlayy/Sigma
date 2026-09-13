/**
 * =========================================================
 * Datei:      src/server/kraken/client.ts
 * Zweck:      Kraken **public** REST-Client (K-1).
 *             Ausschliesslich /0/public/* — keine Credentials, keine
 *             Private-Endpunkte, keine Orders (AGENTS.md: paper only).
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */

export const KRAKEN_API_BASE = 'https://api.kraken.com';

/** Hartcodierte Timeouts/Cache-TTLs (AGENTS.md: benannte Konstanten, keine "configurable later"). */
export const KRAKEN_TIMEOUT_MS = 8000;
export const ASSET_PAIRS_TTL_MS = 900_000; // 15 min — AssetPairs aendert sich selten
export const TICKER_TTL_MS = 5_000;         // 5 s — Ticker ist der Preis-Feed der Ledger-Bewertung

export type KrakenErrorCode =
  | 'ERR_KRAKEN_UNREACHABLE'
  | 'ERR_KRAKEN_TIMEOUT'
  | 'ERR_KRAKEN_HTTP'
  | 'ERR_KRAKEN_SHAPE'
  | 'ERR_KRAKEN_EAPI';

export interface KrakenResult<T> {
  ok: boolean;
  data: T | null;
  error: KrakenErrorCode | null;
  /** Kraken-Fehlertext (EAPI:/EGeneral:/EQuery:) oder Transport-Fehlermeldung. */
  detail: string | null;
  latencyMs: number | null;
}

interface CacheEntry<T> {
  at: number;
  ttl: number;
  payload: T;
}

const cache = new Map<string, CacheEntry<unknown>>();

/**
 * Kraken meldet Fachfehler im `error[]`-Array bei HTTP 200.
 * Der Fehlertext schlaegt den Statuscode — gleiche Regel wie
 * `blueprint.kraken_output_is_error` (EXECUTION_REPORT.md §P5).
 */
export function krakenErrorsAreFatal(errors: unknown): string[] {
  if (!Array.isArray(errors)) return [];
  return errors
    .filter((e): e is string => typeof e === 'string' && e.trim().length > 0)
    .map((e) => e.trim());
}

function readCache<T>(key: string): T | null {
  const hit = cache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.at > hit.ttl) {
    cache.delete(key);
    return null;
  }
  return hit.payload as T;
}

function writeCache<T>(key: string, payload: T, ttl: number): void {
  cache.set(key, { at: Date.now(), ttl, payload });
}

/** Letzter guter Payload aus dem Cache, auch wenn die TTL abgelaufen ist (Stale-Fallback). */
export function readStale<T>(key: string): { payload: T; age_s: number } | null {
  const hit = cache.get(key);
  if (!hit) return null;
  return { payload: hit.payload as T, age_s: Math.round((Date.now() - hit.at) / 1000) };
}

export function krakenCacheKey(path: string, params: Record<string, string> = {}): string {
  const qs = Object.keys(params).sort().map((k) => `${k}=${params[k]}`).join('&');
  return qs ? `${path}?${qs}` : path;
}

/**
 * Einziger Netzwerk-Zugangspunkt. Wirft nie — jeder Fehler wird als
 * `KrakenResult` mit Fehlercode zurueckgegeben (fail-closed).
 */
export async function krakenGet<T>(
  path: string,
  params: Record<string, string> = {},
  options: { ttl?: number; allowStale?: boolean } = {},
): Promise<KrakenResult<T>> {
  const key = krakenCacheKey(path, params);
  const fresh = readCache<unknown>(key);
  if (fresh) {
    return { ok: true, data: fresh as T, error: null, detail: null, latencyMs: 0 };
  }

  const qs = new URLSearchParams(params).toString();
  const url = `${KRAKEN_API_BASE}${path}${qs ? `?${qs}` : ''}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), KRAKEN_TIMEOUT_MS);
  const started = Date.now();

  try {
    const res = await fetch(url, { signal: controller.signal, headers: { 'User-Agent': 'projekt-sigma/1.0' } });
    const latencyMs = Date.now() - started;
    if (!res.ok) {
      return fail<T>('ERR_KRAKEN_HTTP', `HTTP ${res.status}`, latencyMs, key, options);
    }
    const body = (await res.json()) as { error?: unknown; result?: unknown };
    if (typeof body !== 'object' || body === null || !('result' in body)) {
      return fail<T>('ERR_KRAKEN_SHAPE', 'Antwort ohne result-Feld', latencyMs, key, options);
    }
    const fatal = krakenErrorsAreFatal(body.error);
    if (fatal.length > 0) {
      const code: KrakenErrorCode = fatal.some((e) => e.startsWith('EAPI:') || e.startsWith('EGeneral:'))
        ? 'ERR_KRAKEN_EAPI'
        : 'ERR_KRAKEN_SHAPE';
      return fail<T>(code, fatal.join(' | '), latencyMs, key, options);
    }
    if (options.ttl && options.ttl > 0) writeCache(key, body.result, options.ttl);
    return { ok: true, data: body.result as T, error: null, detail: null, latencyMs };
  } catch (err) {
    const latencyMs = Date.now() - started;
    const timedOut = err instanceof Error && err.name === 'AbortError';
    return fail<T>(
      timedOut ? 'ERR_KRAKEN_TIMEOUT' : 'ERR_KRAKEN_UNREACHABLE',
      err instanceof Error ? err.message : String(err),
      latencyMs,
      key,
      options,
    );
  } finally {
    clearTimeout(timer);
  }
}

function fail<T>(
  code: KrakenErrorCode,
  detail: string,
  latencyMs: number,
  key: string,
  options: { allowStale?: boolean },
): KrakenResult<T> {
  if (options.allowStale) {
    const stale = readStale<T>(key);
    if (stale) {
      // Bewusst als ok:true — der Payload ist real, nur aelter. `detail` bleibt erhalten,
      // damit die Route `degraded`/`age_s` setzen kann.
      return { ok: true, data: stale.payload, error: code, detail: `${detail} (stale ${stale.age_s}s)`, latencyMs };
    }
  }
  return { ok: false, data: null, error: code, detail, latencyMs };
}

/** /0/public/Time — Referenzuhr + RTT-Messung (Deadman nutzt Kraken-Zeit, nicht Host-Zeit). */
export interface KrakenServerTime {
  unixtime: number;
  rfc1123: string;
}

export function krakenTime(): Promise<KrakenResult<KrakenServerTime>> {
  return krakenGet<KrakenServerTime>('/0/public/Time');
}

/** Rohe AssetPairs-Antwort: Pair-Key -> Spezifikation. */
export type RawAssetPairs = Record<string, RawAssetPair>;

export interface RawAssetPair {
  altname?: string;
  wsname?: string;
  base?: string;
  quote?: string;
  status?: string;
  pair_decimals?: number;
  lot_decimals?: number;
  cost_decimals?: number;
  ordermin?: string;
  costmin?: string;
  leverage_buy?: (number | string)[];
  leverage_sell?: (number | string)[];
}

export function krakenAssetPairs(pair?: string): Promise<KrakenResult<RawAssetPairs>> {
  return krakenGet<RawAssetPairs>(
    '/0/public/AssetPairs',
    pair ? { pair } : {},
    { ttl: ASSET_PAIRS_TTL_MS, allowStale: true },
  );
}

/** /0/public/Ticker — `c` = letzter Trade, `o` = [Open heute, Open 24h rollierend]. */
export type RawTicker = Record<string, RawTickerEntry>;

export interface RawTickerEntry {
  /** `[preis, lot]` — wir lesen nur Index 0. */
  c?: string[];
  /** `[open heute, open 24h]` — Kraken liefert je nach Pair auch einen String. */
  o?: string[] | string;
}

export function krakenTicker(pairs: string[]): Promise<KrakenResult<RawTicker>> {
  if (pairs.length === 0) {
    return Promise.resolve({ ok: true, data: {}, error: null, detail: null, latencyMs: 0 });
  }
  return krakenGet<RawTicker>(
    '/0/public/Ticker',
    { pair: pairs.join(',') },
    { ttl: TICKER_TTL_MS, allowStale: true },
  );
}
