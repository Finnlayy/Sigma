import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PasskeyWebAuthnClient } from '../../optimizer/PasskeyWebAuthnClient';
import {
  sigmaApi,
  type LibrarySnapshotRow,
  type ScorecardSlot,
  type StrategyLamp,
  type StrategyScorecard as ScorecardData,
} from '@/lib/sigmaApi';

const LAMP_DOT: Record<StrategyLamp, string> = {
  gray: 'bg-zinc-500',
  yellow: 'bg-amber-400',
  green_solid: 'bg-emerald-600',
  green_glow: 'bg-emerald-400',
  red_glow: 'bg-red-500',
};

export function AmpelDot({ lamp, glow = false }: { lamp?: string; glow?: boolean }) {
  const key = (lamp || 'gray') as StrategyLamp;
  const color = LAMP_DOT[key] || LAMP_DOT.gray;
  const shine = glow && (key === 'green_glow' || key === 'red_glow')
    ? (key === 'red_glow' ? 'shadow-[0_0_8px_#ef4444]' : 'shadow-[0_0_8px_#34d399]')
    : '';
  return <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${color} ${shine}`} />;
}

async function operatorToken(): Promise<string | null> {
  return PasskeyWebAuthnClient.authenticatePasskeyForSettings('master@alpha.local');
}

export function StrategyScorecardTab({
  strategyId,
  snapshot,
  onBusy,
}: {
  strategyId: string;
  snapshot?: LibrarySnapshotRow | null;
  onBusy?: (msg: string) => void;
}) {
  const [card, setCard] = useState<ScorecardData | null>(null);
  const [symbol, setSymbol] = useState('BTC/USD');
  const [timeframe, setTimeframe] = useState('15');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    if (!strategyId) return;
    void sigmaApi.strategyScorecard(strategyId).then((d) => d && setCard(d));
  }, [strategyId]);

  const note = (msg: string) => {
    setBusy(msg);
    onBusy?.(msg);
  };

  const addSlot = async (locked = false) => {
    const token = await operatorToken();
    if (!token || !strategyId) return;
    note('saving slot…');
    const existing = card?.slots ?? [];
    const next = [
      ...existing,
      { symbol, timeframe, regime: '', favorite: !locked, locked, origin: 'user' as const },
    ];
    const out = await sigmaApi.putStrategySlots(strategyId, token, next);
    if (out) {
      const refreshed = await sigmaApi.strategyScorecard(strategyId);
      if (refreshed) setCard(refreshed);
      note('slot saved');
    } else note('slot save failed');
  };

  const toggleLock = async (slot: ScorecardSlot) => {
    const token = await operatorToken();
    if (!token || !strategyId) return;
    const next = (card?.slots ?? []).map((s) => (
      s.symbol === slot.symbol && s.timeframe === slot.timeframe && s.regime === slot.regime
        ? { ...s, locked: !s.locked, favorite: s.locked ? true : s.favorite }
        : s
    ));
    await sigmaApi.putStrategySlots(strategyId, token, next);
    const refreshed = await sigmaApi.strategyScorecard(strategyId);
    if (refreshed) setCard(refreshed);
  };

  const kpis = card?.kpis ?? snapshot?.kpis;
  const lamp = card?.lamp ?? snapshot?.lamp ?? 'gray';
  const slots: ScorecardSlot[] = card?.slots ?? [];
  const badges = card?.badges ?? [];

  return (
    <div className="space-y-3 p-3 text-[11px]">
      <div className="grid grid-cols-4 gap-2">
        <Kpi label="N" value={String(kpis?.trade_count ?? 0)} />
        <Kpi label="Winrate" value={`${((kpis?.win_rate ?? 0) * 100).toFixed(0)}%`} />
        <Kpi label="PF (fees)" value={(kpis?.profit_factor ?? 0).toFixed(2)} />
        <Kpi label="Net PnL" value={(kpis?.net_pnl ?? 0).toFixed(1)} />
      </div>
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-zinc-500">
        <AmpelDot lamp={lamp} glow /> Ampel {lamp.replace('_', ' ')}
        {busy && <span className="ml-auto font-mono normal-case text-zinc-400">{busy}</span>}
      </div>
      <div>
        <div className="mb-1 text-[10px] uppercase text-zinc-500">Badges</div>
        <div className="flex flex-wrap gap-1">
          {badges.map((b) => (
            <span key={`${b.badge}-${b.regime}`} className="rounded border border-zinc-700 px-1.5 py-0.5 font-mono text-[10px]">
              {b.badge}
            </span>
          ))}
          {!badges.length && <span className="text-zinc-600">Noch keine Verhaltensbadges.</span>}
        </div>
      </div>
      <div>
        <div className="mb-1 text-[10px] uppercase text-zinc-500">Symbole / Timeframes</div>
        <div className="space-y-1">
          {slots.map((s) => (
            <div key={`${s.symbol}-${s.timeframe}-${s.regime}`}
              className="flex items-center gap-2 rounded border border-zinc-800 px-2 py-1">
              <AmpelDot lamp={s.lamp} glow={s.lamp === 'green_glow' || s.lamp === 'red_glow'} />
              <span className="font-mono">{s.symbol}</span>
              <span className="text-zinc-500">{s.timeframe}</span>
              {s.regime && <span className="text-zinc-600">{s.regime}</span>}
              <span className="text-[10px] text-zinc-500">{s.origin}</span>
              <button className="ml-auto text-[10px] text-zinc-400 hover:text-sky-400"
                onClick={() => void toggleLock(s)}>
                {s.locked ? 'entsperren' : 'sperren'}
              </button>
            </div>
          ))}
          {!slots.length && <div className="text-zinc-600">Keine Slots — Akademie oder User eintragen.</div>}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Input className="h-7 w-28 text-[11px]" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <Input className="h-7 w-16 text-[11px]" value={timeframe} onChange={(e) => setTimeframe(e.target.value)} />
          <Button size="sm" variant="outline" onClick={() => void addSlot(false)}>Favorit</Button>
          <Button size="sm" variant="outline" onClick={() => void addSlot(true)}>Sperren</Button>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-zinc-800 bg-zinc-900/50 px-2 py-1.5">
      <div className="text-[10px] uppercase text-zinc-500">{label}</div>
      <div className="font-mono text-sm text-zinc-100">{value}</div>
    </div>
  );
}
