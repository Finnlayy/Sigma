/**
 * =========================================================
 * Datei:      src/server/kraken/routes.ts
 * Zweck:      /api/kraken/* — die 6 Endpunkte, die das Frontend heute
 *             schon aufruft (K-1). Public-Daten only, paper only,
 *             fail-closed mit strukturierter Leerantwort.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { Router, type Request, type Response } from 'express';
import type { KrakenAccountLedgers, KrakenAssetPairsResponse } from '../../types';
import { ExchangeSymbolNormalizer } from '../../lib/symbolNormalizer';
import {
  krakenAssetPairs,
  krakenTicker,
  krakenTime,
  readStale,
  krakenCacheKey,
  type KrakenErrorCode,
  type RawAssetPairs,
  type RawTicker,
} from './client';
import { mapAssetPair, mapAssetPairs, POPULAR_QUOTES } from './symbols';
import { priceOf, proLedger, spotLedger, type PriceMap, type TickerPrice } from './ledger';
import { loadLedger, touchSync } from './store';

/** USD braucht keinen Ticker — der Kurs ist definitionsgemaess 1 (siehe ledger.USD_QUOTE_PRICE). */
export const QUOTE_ASSET = 'USD';

/** Base-Asset -> echter Kraken-Pair-Key gegen USD, abgeleitet aus AssetPairs. */
export type PairIndex = Record<string, string>;

/**
 * Kraken benennt Pair-Keys nicht einheitlich: XBT/USD = `XXBTZUSD`, SOL/USD = `SOLUSD`.
 * Deshalb wird der Key aus AssetPairs abgeleitet statt geraten.
 */
export function buildPairIndex(result: RawAssetPairs | null): PairIndex {
  const index: PairIndex = {};
  if (!result || typeof result !== 'object') return index;
  for (const [pairKey, spec] of Object.entries(result)) {
    if (!spec || typeof spec !== 'object') continue;
    const info = mapAssetPair(pairKey, spec);
    if (info.quote !== QUOTE_ASSET || !info.base) continue;

    // Alle Schreibweisen desselben Assets muessen denselben Pair-Key treffen:
    // kanonisch (BTC), Kraken-Anzeige (XBT) und Kraken-intern (XXBT).
    const aliases = [info.base, info.symbol.split('/')[0].toUpperCase(), (spec.base ?? '').toUpperCase()];
    for (const alias of aliases) {
      if (alias && !index[alias]) index[alias] = pairKey;
    }
  }
  return index;
}

/** Asset -> Kraken-Pair-Key: AssetPairs-Index zuerst, dann die X/Z-Fallback-Konvention. */
export function krakenPairForAsset(asset: string, index: PairIndex = {}): string {
  const upper = (asset ?? '').trim().toUpperCase();
  const fromIndex = index[upper];
  if (fromIndex) return fromIndex;
  const base = ExchangeSymbolNormalizer.KRAKEN_BASE_MAP[upper] ?? upper;
  return `${base}ZUSD`;
}

/** Kraken antwortet normalerweise mit dem angefragten Pair-Key; Fallback: Gross-/Kleinschreibung. */
export function pickTickerEntry(raw: RawTicker, pairKey: string): RawTicker[string] | null {
  if (!raw || typeof raw !== 'object') return null;
  if (raw[pairKey]) return raw[pairKey];
  const lower = pairKey.toLowerCase();
  for (const [key, value] of Object.entries(raw)) {
    if (key.toLowerCase() === lower) return value;
  }
  return null;
}

/** `c[0]` = letzter Trade, `o[1]` = 24h-Open (`o` kann auch ein String sein). */
export function toTickerPrice(entry: RawTicker[string] | null): TickerPrice | null {
  if (!entry) return null;
  const last = Number.parseFloat(Array.isArray(entry.c) ? entry.c[0] : '');
  const openRaw = Array.isArray(entry.o) ? entry.o[1] : entry.o;
  const open24h = Number.parseFloat(typeof openRaw === 'string' ? openRaw : '');
  if (!Number.isFinite(last)) return null;
  return { last, open24h: Number.isFinite(open24h) ? open24h : last };
}

export interface PricedAssets {
  prices: PriceMap;
  ok: boolean;
  error: KrakenErrorCode | null;
  detail: string | null;
  priced: string[];
  unpriced: string[];
}

/** Bewertet Paper-Assets gegen den echten Kraken-Ticker. Kein Kurs => Preis 0, nie geraten. */
export async function priceAssets(assets: string[]): Promise<PricedAssets> {
  const prices: PriceMap = {};
  const priced: string[] = [];
  const unpriced: string[] = [];
  const wanted = Array.from(new Set(assets.map((a) => (a ?? '').trim().toUpperCase()).filter(Boolean)));

  for (const asset of wanted) {
    if (asset === QUOTE_ASSET) {
      prices[asset] = priceOf(asset, {});
      priced.push(asset);
    }
  }

  const pairs = wanted.filter((a) => a !== QUOTE_ASSET);
  if (pairs.length === 0) {
    return { prices, ok: true, error: null, detail: null, priced, unpriced };
  }

  // Pair-Keys aus AssetPairs (15-min-Cache, Stale-Fallback) — nie geraten.
  const catalog = await krakenAssetPairs();
  const index = buildPairIndex(catalog.data);

  const res = await krakenTicker(pairs.map((asset) => krakenPairForAsset(asset, index)));
  if (!res.ok || !res.data) {
    for (const asset of pairs) {
      prices[asset] = { last: 0, open24h: 0 };
      unpriced.push(asset);
    }
    const error = res.error ?? catalog.error;
    return { prices, ok: false, error, detail: res.detail ?? catalog.detail, priced, unpriced };
  }

  for (const asset of pairs) {
    const price = toTickerPrice(pickTickerEntry(res.data, krakenPairForAsset(asset, index)));
    if (price) {
      prices[asset] = price;
      priced.push(asset);
    } else {
      prices[asset] = { last: 0, open24h: 0 };
      unpriced.push(asset);
    }
  }

  return { prices, ok: unpriced.length === 0, error: res.error, detail: res.detail, priced, unpriced };
}

export const emptySymbolResponse = (): KrakenAssetPairsResponse => ({
  total: 0,
  symbols: [],
  quotes: [],
  popularSymbols: [],
});

function ledgerPayload(
  mode: 'paper' | 'live',
  spot: ReturnType<typeof spotLedger>,
  pro: ReturnType<typeof proLedger>,
  lastSync: string | null,
  extra: { ok: boolean; available: boolean; reason: string | null },
): KrakenAccountLedgers & typeof extra {
  return {
    mode,
    hasCredentials: false,
    lastSync: lastSync ?? new Date(0).toISOString(),
    spot,
    pro,
    ...extra,
  };
}

export function krakenRouter(): Router {
  const router = Router();

  /**
   * GET /api/kraken/status
   * SigmaTerminal liest `latencyMs`, legacyPanels liest `hasCredentials`/`paperTrading`.
   */
  router.get('/status', async (_req: Request, res: Response) => {
    const time = await krakenTime();
    const ledger = loadLedger();
    const serverTime = time.ok && time.data ? time.data.unixtime : null;
    res.json({
      ok: time.ok,
      available: time.ok,
      mode: 'paper',
      paperTrading: true,
      hasCredentials: false,
      latencyMs: time.ok ? time.latencyMs : null,
      serverTime,
      localTimeSkewS: serverTime !== null ? Number((Date.now() / 1000 - serverTime).toFixed(3)) : null,
      lastSync: ledger.last_sync,
      reason: time.ok ? null : time.error,
      detail: time.ok ? null : time.detail,
    });
  });

  /** GET /api/kraken/symbols — MarketPanel + StrategyEditor lesen `{ symbols }`. */
  router.get('/symbols', async (_req: Request, res: Response) => {
    const pairs = await krakenAssetPairs();
    if (!pairs.ok || !pairs.data) {
      res.json({
        ...emptySymbolResponse(),
        ok: false,
        available: false,
        reason: pairs.error,
        detail: pairs.detail,
        quotes: POPULAR_QUOTES,
      });
      return;
    }
    const mapped = mapAssetPairs(pairs.data);
    const stale = readStale(krakenCacheKey('/0/public/AssetPairs'));
    res.json({
      ...mapped,
      ok: true,
      available: true,
      degraded: pairs.error !== null,
      cached: pairs.latencyMs === 0,
      age_s: stale ? stale.age_s : 0,
      reason: pairs.error,
    });
  });

  /** GET /api/kraken/ledgers — KrakenLedgersPanel erwartet KrakenAccountLedgers. */
  router.get('/ledgers', async (_req: Request, res: Response) => {
    const ledger = loadLedger();
    const assets = ledger.balances.map((b) => b.asset);
    const priced = await priceAssets(assets);
    const spot = spotLedger(ledger.balances, ledger.orders_locked, priced.prices);
    const pro = proLedger(ledger.pro_positions, ledger.cash);
    const reason = priced.ok ? null : priced.error ?? 'ERR_KRAKEN_PARTIAL_PRICING';
    res.json(ledgerPayload('paper', spot, pro, ledger.last_sync, {
      ok: priced.ok,
      available: priced.ok,
      reason,
    }));
  });

  /**
   * POST /api/kraken/ledgers/sync — Panel liest `{ timestamp, spot, pro }`.
   * Ohne Kurse wird **nicht** geschrieben (kein Stale-Write).
   */
  router.post('/ledgers/sync', async (_req: Request, res: Response) => {
    const ledger = loadLedger();
    const priced = await priceAssets(ledger.balances.map((b) => b.asset));
    if (!priced.ok) {
      const spot = spotLedger(ledger.balances, ledger.orders_locked, priced.prices);
      const pro = proLedger(ledger.pro_positions, ledger.cash);
      res.json({
        ok: false,
        available: false,
        timestamp: new Date().toISOString(),
        spot,
        pro,
        error: priced.error ?? 'ERR_KRAKEN_PARTIAL_PRICING',
        reason: priced.detail ?? 'Nicht alle Assets konnten bewertet werden.',
      });
      return;
    }
    const saved = touchSync(ledger);
    const spot = spotLedger(saved.balances, saved.orders_locked, priced.prices);
    const pro = proLedger(saved.pro_positions, saved.cash);
    res.json({
      ok: true,
      available: true,
      timestamp: saved.last_sync as string,
      spot,
      pro,
      priced: priced.priced,
    });
  });

  /** GET /api/kraken/positions/pro — MetricsPanel liest Collateral/Margin/PnL + positions. */
  router.get('/positions/pro', (_req: Request, res: Response) => {
    const ledger = loadLedger();
    const pro = proLedger(ledger.pro_positions, ledger.cash);
    res.json({
      ok: true,
      available: true,
      mode: 'paper',
      ...pro,
      reason: pro.positions.length === 0 ? 'NO_OPEN_PRO_POSITIONS' : null,
    });
  });

  /**
   * POST /api/kraken/sync-balance — MetricsPanel prueft `hasCredentials`.
   * Paper only: es gibt keine Live-Balances, und es werden keine erfunden.
   */
  router.post('/sync-balance', (_req: Request, res: Response) => {
    const ledger = loadLedger();
    const paperBalances: Record<string, number> = {};
    for (const balance of ledger.balances) {
      paperBalances[balance.asset] = balance.amount;
    }
    res.json({
      ok: true,
      available: true,
      hasCredentials: false,
      liveKrakenBalances: null,
      paperBalances,
      mode: 'paper',
      lastSync: ledger.last_sync,
      reason: 'PAPER_ONLY_NO_CREDENTIALS',
    });
  });

  return router;
}
