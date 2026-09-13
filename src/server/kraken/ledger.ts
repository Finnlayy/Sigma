/**
 * =========================================================
 * Datei:      src/server/kraken/ledger.ts
 * Zweck:      Paper-Ledger-Mathematik (K-1). Rein funktional:
 *             keine I/O, kein Netzwerk — mit synthetischen Payloads testbar.
 *             Live-Konten existieren nicht (AGENTS.md: paper only).
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import type { KrakenProPosition, KrakenSpotPosition } from '../../types';

/** Startguthaben des Paper-Kontos. Konfiguration, **kein** Marktpreis. */
export const PAPER_INITIAL_CASH_USD = 10_000;

/** Wartungsmarge fuer die Liquidations-Formel (Kraken Pro Futures, 0.5 %). */
export const MAINTENANCE_MARGIN_FRACTION = 0.005;

/** USD ist die Ledger-Referenzwaehrung — der Kurs ist definitionsgemaess 1. */
export const USD_QUOTE_PRICE = 1;

export const FIAT_ASSETS = new Set(['USD', 'EUR', 'GBP', 'CAD', 'JPY', 'CHF', 'AUD', 'KRW', 'SGD', 'SEK', 'NOK', 'DKK', 'PLN', 'AED']);
export const STABLECOIN_ASSETS = new Set(['USDT', 'USDC', 'DAI', 'TUSD', 'BUSD', 'FDUSD', 'PYUSD', 'EURT', 'USDK', 'USDP', 'GUSD']);

const ASSET_NAMES: Record<string, string> = {
  USD: 'US Dollar',
  EUR: 'Euro',
  GBP: 'Pound Sterling',
  BTC: 'Bitcoin',
  XBT: 'Bitcoin',
  ETH: 'Ethereum',
  SOL: 'Solana',
  XRP: 'Ripple',
  USDT: 'Tether',
  USDC: 'USD Coin',
  DOGE: 'Dogecoin',
  ADA: 'Cardano',
  AVAX: 'Avalanche',
  LINK: 'Chainlink',
  DOT: 'Polkadot',
};

export type AssetType = 'fiat' | 'crypto' | 'stablecoin';

export function assetType(asset: string): AssetType {
  const upper = (asset ?? '').trim().toUpperCase();
  if (FIAT_ASSETS.has(upper)) return 'fiat';
  if (STABLECOIN_ASSETS.has(upper)) return 'stablecoin';
  return 'crypto';
}

export function assetName(asset: string): string {
  const upper = (asset ?? '').trim().toUpperCase();
  return ASSET_NAMES[upper] ?? upper;
}

export interface PaperBalance {
  asset: string;
  amount: number;
}

export interface TickerPrice {
  /** Letzter Trade (`c[0]`). */
  last: number;
  /** 24h-Open (`o[1]`). */
  open24h: number;
}

export type PriceMap = Record<string, TickerPrice>;

/** USD -> 1 per Definition; fehlende Kurse bleiben 0 (fail-closed, keine erfundenen Preise). */
export function priceOf(asset: string, prices: PriceMap): TickerPrice {
  const upper = (asset ?? '').trim().toUpperCase();
  const hit = prices[upper];
  if (hit) return hit;
  if (upper === 'USD') return { last: USD_QUOTE_PRICE, open24h: USD_QUOTE_PRICE };
  return { last: 0, open24h: 0 };
}

export interface SpotLedger {
  totalValueUSD: number;
  freeCashUSD: number;
  cryptoValueUSD: number;
  change24hUSD: number;
  change24hPercent: number;
  assets: KrakenSpotPosition[];
}

/**
 * Spot-Ledger aus Paper-Balances + echten Ticker-Kursen.
 *
 *  totalValueUSD(asset)     = amount * last
 *  available(asset)         = amount - inOrders
 *  portfolioPercentage      = totalValueUSD(asset) / totalValueUSD(gesamt) * 100
 *  change24hUSD(asset)      = amount * (last - open24h)
 *  change24hPercent(gesamt) = change24hUSD / (totalValueUSD - change24hUSD) * 100
 *                             (Nenner = Wert zum 24h-Open; 0 => 0)
 */
export function spotLedger(
  balances: PaperBalance[],
  lockedByAsset: Record<string, number>,
  prices: PriceMap,
): SpotLedger {
  const rows: KrakenSpotPosition[] = [];
  let totalValueUSD = 0;
  let cryptoValueUSD = 0;
  let freeCashUSD = 0;
  let change24hUSD = 0;

  for (const balance of balances ?? []) {
    const asset = (balance.asset ?? '').trim().toUpperCase();
    if (!asset) continue;
    const amount = Number.isFinite(balance.amount) ? balance.amount : 0;
    const { last, open24h } = priceOf(asset, prices);
    const inOrders = Number.isFinite(lockedByAsset?.[asset]) ? lockedByAsset[asset] : 0;
    const value = amount * last;
    const change = amount * (last - open24h);
    const type = assetType(asset);

    totalValueUSD += value;
    change24hUSD += change;
    if (type === 'crypto') cryptoValueUSD += value;
    else freeCashUSD += value;

    rows.push({
      asset,
      name: assetName(asset),
      amount,
      available: amount - inOrders,
      inOrders,
      unitPriceUSD: last,
      totalValueUSD: value,
      portfolioPercentage: 0, // zweiter Durchlauf — braucht die Gesamtsumme
      change24h: change,
      type,
    });
  }

  for (const row of rows) {
    row.portfolioPercentage = totalValueUSD > 0 ? (row.totalValueUSD / totalValueUSD) * 100 : 0;
  }

  const valueAtOpen = totalValueUSD - change24hUSD;

  return {
    totalValueUSD,
    freeCashUSD,
    cryptoValueUSD,
    change24hUSD,
    change24hPercent: valueAtOpen > 0 ? (change24hUSD / valueAtOpen) * 100 : 0,
    assets: rows,
  };
}

/**
 * Liquidationspreis aus Einstiegspreis, Hebel und Seite:
 *   long  = entry * (1 - 1/leverage + maintenanceMarginFraction)
 *   short = entry * (1 + 1/leverage - maintenanceMarginFraction)
 * Ungehebelte Positionen (leverage <= 0) haben keinen Liquidationspreis -> 0.
 */
export function liquidationPrice(
  entryPrice: number,
  leverage: number,
  side: KrakenProPosition['type'],
): number {
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) return 0;
  if (!Number.isFinite(leverage) || leverage <= 0) return 0;
  const inverse = 1 / leverage;
  const raw = side === 'long'
    ? entryPrice * (1 - inverse + MAINTENANCE_MARGIN_FRACTION)
    : entryPrice * (1 + inverse - MAINTENANCE_MARGIN_FRACTION);
  return Math.max(0, raw);
}

/**
 * Fuehrt die abgeleiteten Felder einer Pro-Position nach:
 *  unrealizedPnLPercent = unrealizedPnLUSD / collateralUSD * 100
 *  liquidationPrice     = Formel oben, wenn die Position keinen mitbringt
 */
export function enrichProPosition(position: KrakenProPosition): KrakenProPosition {
  const collateral = Number.isFinite(position.collateralUSD) ? position.collateralUSD : 0;
  const unrealized = Number.isFinite(position.unrealizedPnLUSD) ? position.unrealizedPnLUSD : 0;
  return {
    ...position,
    collateralUSD: collateral,
    unrealizedPnLUSD: unrealized,
    unrealizedPnLPercent: collateral > 0 ? (unrealized / collateral) * 100 : 0,
    liquidationPrice: position.liquidationPrice > 0
      ? position.liquidationPrice
      : liquidationPrice(position.entryPrice, position.leverage, position.type),
  };
}

export interface ProLedger {
  totalCollateralUSD: number;
  freeMarginUSD: number;
  usedMarginUSD: number;
  marginLevelPercent: number;
  totalUnrealizedPnL: number;
  unrealizedPnLPercent: number;
  effectiveLeverage: number;
  positions: KrakenProPosition[];
}

/**
 * Pro-Ledger-Aggregation:
 *  usedMarginUSD        = Σ collateralUSD
 *  totalUnrealizedPnL   = Σ unrealizedPnLUSD
 *  effectiveLeverage    = Σ notionalValueUSD / totalCollateralUSD   (0 bei 0 Collateral)
 *  marginLevelPercent   = totalCollateralUSD / usedMarginUSD * 100  (0 bei 0 used)
 *  unrealizedPnLPercent = totalUnrealizedPnL / totalCollateralUSD * 100
 *
 * `cash` ist das freie Paper-Cash; ohne offene Positionen ist die ganze Summe frei.
 */
export function proLedger(rawPositions: KrakenProPosition[], cash: number): ProLedger {
  const positions = (rawPositions ?? []).map(enrichProPosition);

  let totalCollateralUSD = 0;
  let totalNotionalUSD = 0;
  let totalUnrealizedPnL = 0;

  for (const p of positions) {
    totalCollateralUSD += p.collateralUSD;
    totalNotionalUSD += Number.isFinite(p.notionalValueUSD) ? p.notionalValueUSD : 0;
    totalUnrealizedPnL += p.unrealizedPnLUSD;
  }

  const safeCash = Number.isFinite(cash) ? cash : 0;
  // Collateral-Basis des Pro-Kontos: gebundene Marge + freies Cash.
  const accountEquity = totalCollateralUSD + safeCash;
  const usedMarginUSD = totalCollateralUSD;

  return {
    totalCollateralUSD: accountEquity,
    freeMarginUSD: accountEquity - usedMarginUSD,
    usedMarginUSD,
    marginLevelPercent: usedMarginUSD > 0 ? (accountEquity / usedMarginUSD) * 100 : 0,
    totalUnrealizedPnL,
    unrealizedPnLPercent: accountEquity > 0 ? (totalUnrealizedPnL / accountEquity) * 100 : 0,
    effectiveLeverage: accountEquity > 0 ? totalNotionalUSD / accountEquity : 0,
    positions,
  };
}
