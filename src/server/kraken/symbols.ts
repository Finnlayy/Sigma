/**
 * =========================================================
 * Datei:      src/server/kraken/symbols.ts
 * Zweck:      Kraken AssetPairs -> KrakenAssetPairsResponse (K-1).
 *             Rein funktional: keine I/O, mit synthetischen Payloads testbar.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import type { KrakenAssetPairsResponse, KrakenSymbolInfo } from '../../types';
import { ExchangeSymbolNormalizer } from '../../lib/symbolNormalizer';
import type { RawAssetPair, RawAssetPairs } from './client';

/**
 * Kraken-Konvention: `Z`-Praefix = Fiat, `X`-Praefix = Krypto, jeweils 4 Zeichen
 * (ZUSD, XBT, XXBT). Erst die explizite Map, dann die Praefix-Regel.
 */
export function krakenDisplayCode(code: string): string {
  const upper = (code ?? '').trim().toUpperCase();
  if (!upper) return '';
  const mapped = ExchangeSymbolNormalizer.REVERSE_KRAKEN_BASE_MAP[upper];
  if (mapped) return mapped;
  if (upper.length === 4 && (upper.startsWith('Z') || upper.startsWith('X'))) {
    const stripped = upper.slice(1);
    // Einbuchstaben-Reste (z. B. "Z" aus "ZZZ1") sind kein Asset-Code.
    if (stripped.length === 3) return stripped;
  }
  return upper;
}

/** Kraken liefert Leverage als Zahl (5) oder als Verhaeltnis-String ("5:1"). */
export function parseLeverageList(raw: (number | string)[] | undefined): number[] {
  if (!Array.isArray(raw)) return [];
  const out: number[] = [];
  for (const entry of raw) {
    const value = typeof entry === 'number'
      ? entry
      : Number.parseFloat(String(entry).split(':')[0].trim());
    if (Number.isFinite(value) && value > 0 && !out.includes(value)) out.push(value);
  }
  return out;
}

/** Anzeige-Name eines Pairs: wsname ("XBT/USD") > altname ("XBTUSD") > Pair-Key. */
export function pairDisplayName(pairKey: string, spec: RawAssetPair): string {
  const wsname = typeof spec.wsname === 'string' ? spec.wsname.trim() : '';
  if (wsname) return wsname;
  const altname = typeof spec.altname === 'string' ? spec.altname.trim() : '';
  if (altname) return altname;
  return pairKey;
}

export function mapAssetPair(pairKey: string, spec: RawAssetPair): KrakenSymbolInfo {
  const leverageBuy = parseLeverageList(spec.leverage_buy);
  const leverageSell = parseLeverageList(spec.leverage_sell);
  const symbol = pairDisplayName(pairKey, spec);
  const [basePart, quotePart] = symbol.includes('/') ? symbol.split('/') : ['', ''];
  const base = krakenDisplayCode(basePart || spec.base || '');
  const quote = krakenDisplayCode(quotePart || spec.quote || '');

  return {
    symbol,
    wsname: typeof spec.wsname === 'string' ? spec.wsname : symbol,
    altname: typeof spec.altname === 'string' ? spec.altname : '',
    base,
    quote,
    status: typeof spec.status === 'string' ? spec.status : 'unknown',
    lotDecimals: Number.isFinite(spec.lot_decimals) ? Number(spec.lot_decimals) : 8,
    pairDecimals: Number.isFinite(spec.pair_decimals) ? Number(spec.pair_decimals) : 2,
    costDecimals: Number.isFinite(spec.cost_decimals) ? Number(spec.cost_decimals) : undefined,
    ordermin: typeof spec.ordermin === 'string' ? spec.ordermin : undefined,
    costmin: typeof spec.costmin === 'string' ? spec.costmin : undefined,
    hasLeverage: leverageBuy.length > 0 || leverageSell.length > 0,
    leverageBuy,
    leverageSell,
  };
}

/** Quote-Reihenfolge der Filterleiste — deckungsgleich mit KrakenSymbolModal. */
export const POPULAR_QUOTES = ['USD', 'EUR', 'USDT', 'USDC', 'BTC', 'ETH', 'GBP', 'CAD', 'AUD'];

/** Pairs, die Kraken fuer das Listing gesperrt hat, werden nicht angeboten. */
export const LISTABLE_STATUS = new Set(['online', 'post_only', 'limit_only', 'reduce_only', 'unknown']);

export function mapAssetPairs(result: RawAssetPairs | null): KrakenAssetPairsResponse {
  const empty: KrakenAssetPairsResponse = { total: 0, symbols: [], quotes: [], popularSymbols: [] };
  if (!result || typeof result !== 'object') return empty;

  const symbols: KrakenSymbolInfo[] = [];
  for (const [pairKey, spec] of Object.entries(result)) {
    if (!spec || typeof spec !== 'object') continue;
    const status = typeof spec.status === 'string' ? spec.status : 'unknown';
    if (!LISTABLE_STATUS.has(status)) continue;
    symbols.push(mapAssetPair(pairKey, spec));
  }
  symbols.sort((a, b) => a.symbol.localeCompare(b.symbol));

  const quoteSet = new Set<string>();
  const popularSet = new Set<string>();
  for (const s of symbols) {
    if (s.quote) quoteSet.add(s.quote);
    if (s.quote && POPULAR_QUOTES.includes(s.quote)) popularSet.add(s.symbol);
  }

  // Beliebte Quotes zuerst (in POPULAR_QUOTES-Reihenfolge), Rest alphabetisch.
  const quotes = [
    ...POPULAR_QUOTES.filter((q) => quoteSet.has(q)),
    ...Array.from(quoteSet).filter((q) => !POPULAR_QUOTES.includes(q)).sort(),
  ];

  return {
    total: symbols.length,
    symbols,
    quotes,
    popularSymbols: symbols.filter((s) => popularSet.has(s.symbol)).map((s) => s.symbol),
  };
}
