/**
 * =========================================================
 * Datei:      scripts/check-kraken.ts
 * Zweck:      K-1 Vertragstests — synthetische Kraken-Payloads,
 *             kein Netzwerk (AGENTS.md: no network in tests).
 *             Run: npm run check:kraken
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import assert from 'node:assert/strict';
import { krakenDisplayCode, mapAssetPairs, parseLeverageList, pairDisplayName } from '../src/server/kraken/symbols';
import {
  assetType,
  enrichProPosition,
  liquidationPrice,
  MAINTENANCE_MARGIN_FRACTION,
  PAPER_INITIAL_CASH_USD,
  priceOf,
  proLedger,
  spotLedger,
} from '../src/server/kraken/ledger';
import { buildPairIndex, krakenPairForAsset, pickTickerEntry, toTickerPrice } from '../src/server/kraken/routes';
import { krakenErrorsAreFatal } from '../src/server/kraken/client';
import { loadLedger, seedLedger } from '../src/server/kraken/store';
import type { KrakenProPosition } from '../src/types';

let passed = 0;
const failures: string[] = [];

function check(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (err) {
    failures.push(`${name}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

const close = (actual: number, expected: number, eps = 1e-9): boolean => Math.abs(actual - expected) <= eps;

// ---------------------------------------------------------------- Symbol-Mapping

check('krakenDisplayCode mappt Kraken-Codes auf Anzeige-Codes', () => {
  assert.equal(krakenDisplayCode('XXBT'), 'BTC');
  assert.equal(krakenDisplayCode('ZUSD'), 'USD');
  assert.equal(krakenDisplayCode('ZEUR'), 'EUR');
  assert.equal(krakenDisplayCode('USDT'), 'USDT');   // kein Z-Praefix-Split bei 4 Zeichen ohne Map? doch: 'SDT' falsch
  assert.equal(krakenDisplayCode('XDG'), 'DOGE');
  assert.equal(krakenDisplayCode(''), '');
});

check('parseLeverageList versteht Zahlen und Verhaeltnis-Strings, entdupliziert', () => {
  assert.deepEqual(parseLeverageList([2, '3:1', 3, '5:1']), [2, 3, 5]);
  assert.deepEqual(parseLeverageList(undefined), []);
  assert.deepEqual(parseLeverageList([0, -1, 'abc']), []);
});

check('pairDisplayName bevorzugt wsname, dann altname, dann Pair-Key', () => {
  assert.equal(pairDisplayName('XXBTZUSD', { wsname: 'XBT/USD', altname: 'XBTUSD' }), 'XBT/USD');
  assert.equal(pairDisplayName('XXBTZUSD', { altname: 'XBTUSD' }), 'XBTUSD');
  assert.equal(pairDisplayName('XXBTZUSD', {}), 'XXBTZUSD');
});

const syntheticAssetPairs = {
  XXBTZUSD: {
    altname: 'XBTUSD',
    wsname: 'XBT/USD',
    base: 'XXBT',
    quote: 'ZUSD',
    status: 'online',
    pair_decimals: 1,
    lot_decimals: 8,
    cost_decimals: 5,
    ordermin: '0.0001',
    costmin: '0.5',
    leverage_buy: [2, 3, '5:1'],
    leverage_sell: [2, 3, 5],
  },
  SOLUSD: {
    altname: 'SOLUSD',
    wsname: 'SOL/USD',
    base: 'SOL',
    quote: 'ZUSD',
    status: 'online',
    pair_decimals: 4,
    lot_decimals: 8,
    leverage_buy: [],
    leverage_sell: [],
  },
  XETHZEUR: {
    altname: 'ETHEUR',
    wsname: 'ETH/EUR',
    base: 'XETH',
    quote: 'ZEUR',
    status: 'online',
    pair_decimals: 2,
    lot_decimals: 8,
  },
  OLDDAOUSD: {
    altname: 'OLDDAOUSD',
    wsname: 'OLDDAO/USD',
    base: 'OLDDAO',
    quote: 'ZUSD',
    status: 'delisted',
    pair_decimals: 5,
    lot_decimals: 8,
  },
};

check('mapAssetPairs liefert total/symbols/quotes/popularSymbols und filtert delisted', () => {
  const mapped = mapAssetPairs(syntheticAssetPairs);
  assert.equal(mapped.total, 3);
  assert.deepEqual(mapped.symbols.map((s) => s.symbol), ['ETH/EUR', 'SOL/USD', 'XBT/USD']);

  const btc = mapped.symbols.find((s) => s.symbol === 'XBT/USD');
  assert.ok(btc);
  assert.equal(btc.base, 'BTC');
  assert.equal(btc.quote, 'USD');
  assert.equal(btc.pairDecimals, 1);
  assert.equal(btc.lotDecimals, 8);
  assert.equal(btc.costDecimals, 5);
  assert.equal(btc.ordermin, '0.0001');
  assert.equal(btc.costmin, '0.5');
  assert.equal(btc.hasLeverage, true);
  assert.deepEqual(btc.leverageBuy, [2, 3, 5]);

  const sol = mapped.symbols.find((s) => s.symbol === 'SOL/USD');
  assert.ok(sol);
  assert.equal(sol.hasLeverage, false);
  assert.equal(sol.base, 'SOL');

  // POPULAR_QUOTES-Reihenfolge: USD vor EUR, Rest alphabetisch
  assert.deepEqual(mapped.quotes, ['USD', 'EUR']);
  // popularSymbols = Pairs mit Quote aus POPULAR_QUOTES (USD **und** EUR), symbol-sortiert
  assert.deepEqual(mapped.popularSymbols, ['ETH/EUR', 'SOL/USD', 'XBT/USD']);
});

check('mapAssetPairs ist fail-closed bei null/leer', () => {
  assert.deepEqual(mapAssetPairs(null), { total: 0, symbols: [], quotes: [], popularSymbols: [] });
  assert.deepEqual(mapAssetPairs({}), { total: 0, symbols: [], quotes: [], popularSymbols: [] });
});

check('buildPairIndex nutzt echte Pair-Keys (SOLUSD statt SOLZUSD) mit BTC/XBT-Alias', () => {
  const index = buildPairIndex(syntheticAssetPairs);
  assert.equal(index['BTC'], 'XXBTZUSD');
  assert.equal(index['XBT'], 'XXBTZUSD');
  assert.equal(index['SOL'], 'SOLUSD');
  assert.equal(krakenPairForAsset('BTC', index), 'XXBTZUSD');
  assert.equal(krakenPairForAsset('SOL', index), 'SOLUSD');
  // ohne Index: X/Z-Fallback
  assert.equal(krakenPairForAsset('BTC'), 'XXBTZUSD');
  assert.equal(krakenPairForAsset('ETH'), 'XETHZUSD');
  assert.deepEqual(buildPairIndex(null), {});
});

// ---------------------------------------------------------------- Ticker-Parsing

check('toTickerPrice liest c[0] als Last und o[1] als 24h-Open', () => {
  const price = toTickerPrice({ c: ['60000.0', '0.001'], o: ['59000.0', '58000.0'] });
  assert.ok(price);
  assert.equal(price.last, 60000);
  assert.equal(price.open24h, 58000);
});

check('toTickerPrice faellt bei String-Open auf Last zurueck und bei null auf null', () => {
  const price = toTickerPrice({ c: ['60000.0'], o: '59000.0' });
  assert.ok(price);
  assert.equal(price.last, 60000);
  assert.equal(price.open24h, 59000);
  assert.equal(toTickerPrice(null), null);
  assert.equal(toTickerPrice({ c: ['n/a'] }), null);
});

check('pickTickerEntry findet exakt und case-insensitive', () => {
  const raw = { XXBTZUSD: { c: ['1', '1'] } };
  assert.ok(pickTickerEntry(raw, 'XXBTZUSD'));
  assert.ok(pickTickerEntry(raw, 'xxbtzusd'));
  assert.equal(pickTickerEntry(raw, 'SOLUSD'), null);
  assert.equal(pickTickerEntry({}, 'XXBTZUSD'), null);
});

check('krakenErrorsAreFatal ignoriert leere Strings', () => {
  assert.deepEqual(krakenErrorsAreFatal([]), []);
  assert.deepEqual(krakenErrorsAreFatal(['']), []);
  assert.deepEqual(krakenErrorsAreFatal(['EAPI:Invalid key']), ['EAPI:Invalid key']);
  assert.deepEqual(krakenErrorsAreFatal('nope'), []);
});

// ---------------------------------------------------------------- Ledger-Mathematik

check('priceOf: USD ist 1 per Definition, Unbekanntes bleibt 0', () => {
  assert.deepEqual(priceOf('USD', {}), { last: 1, open24h: 1 });
  assert.deepEqual(priceOf('BTC', {}), { last: 0, open24h: 0 });
  assert.deepEqual(priceOf('BTC', { BTC: { last: 5, open24h: 4 } }), { last: 5, open24h: 4 });
});

check('assetType trennt fiat / stablecoin / crypto', () => {
  assert.equal(assetType('USD'), 'fiat');
  assert.equal(assetType('eur'), 'fiat');
  assert.equal(assetType('USDT'), 'stablecoin');
  assert.equal(assetType('BTC'), 'crypto');
});

check('spotLedger rechnet Werte, Cash, Krypto-Anteil und 24h-Change exakt', () => {
  const prices = { BTC: { last: 60000, open24h: 58000 } };
  const balances = [
    { asset: 'USD', amount: 10000 },
    { asset: 'BTC', amount: 0.5 },
  ];
  const spot = spotLedger(balances, { BTC: 0.1 }, prices);

  assert.ok(close(spot.totalValueUSD, 40000));        // 10000 + 0.5*60000
  assert.ok(close(spot.cryptoValueUSD, 30000));
  assert.ok(close(spot.freeCashUSD, 10000));
  assert.ok(close(spot.change24hUSD, 1000));          // 0.5 * (60000-58000)
  assert.ok(close(spot.change24hPercent, (1000 / 39000) * 100));

  const btc = spot.assets.find((a) => a.asset === 'BTC');
  assert.ok(btc);
  assert.ok(close(btc.totalValueUSD, 30000));
  assert.ok(close(btc.portfolioPercentage, 75));
  assert.ok(close(btc.available, 0.4));               // 0.5 - 0.1 in Orders
  assert.equal(btc.inOrders, 0.1);
  assert.equal(btc.type, 'crypto');
  assert.equal(btc.name, 'Bitcoin');

  const usd = spot.assets.find((a) => a.asset === 'USD');
  assert.ok(usd);
  assert.ok(close(usd.portfolioPercentage, 25));
  assert.ok(close(usd.change24h, 0));
});

check('spotLedger ist fail-closed: keine Kurse => alles 0, keine Division durch 0', () => {
  const spot = spotLedger([{ asset: 'BTC', amount: 1 }], {}, {});
  assert.equal(spot.totalValueUSD, 0);
  assert.equal(spot.change24hPercent, 0);
  assert.equal(spot.assets[0].portfolioPercentage, 0);
  assert.equal(spot.assets[0].unitPriceUSD, 0);
  assert.deepEqual(spotLedger([], {}, {}).assets, []);
});

check('liquidationPrice: long/short Formel, ungehebelt = 0', () => {
  assert.ok(close(liquidationPrice(100, 10, 'long'), 100 * (1 - 0.1 + MAINTENANCE_MARGIN_FRACTION)));
  assert.ok(close(liquidationPrice(100, 10, 'long'), 90.5));
  assert.ok(close(liquidationPrice(100, 10, 'short'), 109.5));
  assert.equal(liquidationPrice(100, 0, 'long'), 0);
  assert.equal(liquidationPrice(0, 10, 'long'), 0);
  assert.equal(liquidationPrice(-5, 10, 'long'), 0);
});

const syntheticPosition: KrakenProPosition = {
  id: 'pos-1',
  pair: 'XBT/USD',
  type: 'long',
  contractType: 'perpetual',
  size: 0.1,
  notionalValueUSD: 6000,
  leverage: 6,
  entryPrice: 60000,
  markPrice: 60500,
  liquidationPrice: 0,
  collateralUSD: 1000,
  marginRequirementUSD: 1000,
  unrealizedPnLUSD: 50,
  unrealizedPnLPercent: 0,
  status: 'open',
};

check('enrichProPosition rechnet PnL% und ergaenzt den Liquidationspreis', () => {
  const enriched = enrichProPosition(syntheticPosition);
  assert.ok(close(enriched.unrealizedPnLPercent, 5));
  assert.ok(close(enriched.liquidationPrice, 60000 * (1 - 1 / 6 + MAINTENANCE_MARGIN_FRACTION)));
  // Mitgelieferter Liquidationspreis wird nicht ueberschrieben
  assert.equal(enrichProPosition({ ...syntheticPosition, liquidationPrice: 12345 }).liquidationPrice, 12345);
  assert.equal(enrichProPosition({ ...syntheticPosition, collateralUSD: 0 }).unrealizedPnLPercent, 0);
});

check('proLedger aggregiert Collateral, Margin-Level, PnL und effektiven Hebel', () => {
  const pro = proLedger([syntheticPosition], 9000);
  assert.ok(close(pro.totalCollateralUSD, 10000));      // 1000 gebunden + 9000 cash
  assert.ok(close(pro.usedMarginUSD, 1000));
  assert.ok(close(pro.freeMarginUSD, 9000));
  assert.ok(close(pro.marginLevelPercent, 1000));       // 10000/1000*100
  assert.ok(close(pro.totalUnrealizedPnL, 50));
  assert.ok(close(pro.unrealizedPnLPercent, 0.5));      // 50/10000*100
  assert.ok(close(pro.effectiveLeverage, 0.6));         // 6000/10000
  assert.equal(pro.positions.length, 1);
});

check('proLedger ohne Positionen liefert Nullen statt erfundener Zahlen', () => {
  const pro = proLedger([], PAPER_INITIAL_CASH_USD);
  assert.ok(close(pro.totalCollateralUSD, PAPER_INITIAL_CASH_USD));
  assert.equal(pro.usedMarginUSD, 0);
  assert.equal(pro.marginLevelPercent, 0);
  assert.equal(pro.effectiveLeverage, 0);
  assert.equal(pro.totalUnrealizedPnL, 0);
  assert.deepEqual(pro.positions, []);
});

// ---------------------------------------------------------------- Store

check('seedLedger startet mit PAPER_INITIAL_CASH_USD, ohne Positionen', () => {
  const seed = seedLedger();
  assert.equal(seed.cash, PAPER_INITIAL_CASH_USD);
  assert.deepEqual(seed.balances, [{ asset: 'USD', amount: PAPER_INITIAL_CASH_USD }]);
  assert.deepEqual(seed.pro_positions, []);
  assert.equal(seed.last_sync, null);
  assert.equal(typeof loadLedger().cash, 'number');
});

// ---------------------------------------------------------------- Report

if (failures.length > 0) {
  console.error(`\n✗ ${failures.length} von ${passed + failures.length} Checks fehlgeschlagen:\n`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`✓ check:kraken — ${passed} Checks grün (synthetische Payloads, kein Netzwerk)`);
