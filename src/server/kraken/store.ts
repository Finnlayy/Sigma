/**
 * =========================================================
 * Datei:      src/server/kraken/store.ts
 * Zweck:      File-backed Paper-Ledger (K-1). Atomares Schreiben,
 *             deterministischer Seed, keine erfundenen Positionen.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import fs from 'fs';
import path from 'path';
import type { KrakenProPosition } from '../../types';
import { PAPER_INITIAL_CASH_USD, type PaperBalance } from './ledger';

export interface PaperLedgerState {
  /** Freies Cash in USD (Referenzwaehrung des Ledgers). */
  cash: number;
  /** Spot-Balances des Paper-Kontos. */
  balances: PaperBalance[];
  /** Durch offene Orders gebundene Mengen je Asset. */
  orders_locked: Record<string, number>;
  /** Offene Pro-Positionen (Futures/Margin) — zu Beginn leer. */
  pro_positions: KrakenProPosition[];
  last_sync: string | null;
  created_at: string;
}

export const LEDGER_DIR = path.resolve(process.cwd(), 'data');
export const LEDGER_FILE = 'kraken-paper-ledger.json';

export function ledgerPath(): string {
  return path.join(LEDGER_DIR, LEDGER_FILE);
}

export function seedLedger(): PaperLedgerState {
  return {
    cash: PAPER_INITIAL_CASH_USD,
    // USD-Cash wird als Spot-Asset ausgewiesen, damit das Panel eine Zeile hat.
    balances: [{ asset: 'USD', amount: PAPER_INITIAL_CASH_USD }],
    orders_locked: {},
    pro_positions: [],
    last_sync: null,
    created_at: new Date().toISOString(),
  };
}

function isValidState(value: unknown): value is PaperLedgerState {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Partial<PaperLedgerState>;
  return typeof v.cash === 'number'
    && Array.isArray(v.balances)
    && typeof v.orders_locked === 'object'
    && v.orders_locked !== null
    && Array.isArray(v.pro_positions);
}

/**
 * Liest das Paper-Ledger. Fehlende/kaputte Datei => frischer Seed
 * (fail-closed: kein Crash, keine erfundenen Zahlen aus dem Nichts).
 */
export function loadLedger(): PaperLedgerState {
  const file = ledgerPath();
  try {
    if (!fs.existsSync(file)) return seedLedger();
    const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf-8'));
    if (!isValidState(parsed)) return seedLedger();
    return parsed;
  } catch {
    return seedLedger();
  }
}

/** Atomares Schreiben: tmp-Datei + rename, damit kein Leser eine halbe Datei sieht. */
export function saveLedger(state: PaperLedgerState): void {
  fs.mkdirSync(LEDGER_DIR, { recursive: true });
  const file = ledgerPath();
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf-8');
  fs.renameSync(tmp, file);
}

/** Schreibt nur, wenn die Bewertung erfolgreich war — kein Stale-Write bei Feed-Ausfall. */
export function touchSync(state: PaperLedgerState, at: Date = new Date()): PaperLedgerState {
  const next: PaperLedgerState = { ...state, last_sync: at.toISOString() };
  saveLedger(next);
  return next;
}
