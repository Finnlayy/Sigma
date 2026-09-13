/**
 * =========================================================
 * Datei:      src/components/sigma/mp17Panels.tsx
 * Zweck:      MP-17 — zwölf Sigma-Forschungs-Panels (fail-closed):
 *             Regime, Geometry, Power, Scout, Polymarket, Ladder,
 *             Fractal, Provisioner, ONNX, Risk, Unwind, Research.
 *             Jedes Panel rendert nur /api/v1/sigma/*-Antworten;
 *             fehlende Feeds -> erklärender Leerzustand + FeedBadge,
 *             niemals synthetische Werte. Keine Orders im UI.
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { useState } from 'react';
import {
  Activity, BarChart3, Brain, Eye, EyeOff, FlaskConical,
  Gauge, Layers, Lock, Radar, ShieldCheck, Sparkles, Target, Wand2,
} from 'lucide-react';
import {
  PanelShell, Stat, FeedBadge, usePoll,
} from './panels';
import { sigmaResearchApi, blindedSymbol, type SigmaPanelBase } from '../../lib/sigmaApi';

/* ------------------------------------------------------- shared helpers */

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-[11px] text-zinc-500">{text}</div>;
}

function Bar({ value, max = 1, tone = 'bg-emerald-500/70' }: { value: number; max?: number; tone?: string }) {
  const w = Math.max(0, Math.min(100, (Math.abs(value) / max) * 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-zinc-800">
      <div className={`h-full ${tone}`} style={{ width: `${w}%` }} />
    </div>
  );
}

function PanelHeader({ data, text }: { data?: SigmaPanelBase | null; text: string }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <span className="text-[10px] uppercase tracking-wide text-zinc-500">{text}</span>
      <FeedBadge feed={data?.feed ?? null} />
    </div>
  );
}

/* ------------------------------------------------------------ 1 Regime */

export function QuantumRegimePanel() {
  const [data, refresh] = usePoll(sigmaResearchApi.regime, 6000);
  return (
    <PanelShell title="Quantum Regime" icon={<Radar size={13} />}
      actions={<button onClick={refresh} className="text-[10px] text-zinc-500 hover:text-zinc-300">refresh</button>}>
      <PanelHeader data={data} text="MP-05/07/06/11 · closed 1h bar only" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="waiting for closed 1h bar … kein Feed (fail-closed)" />
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          <Stat label="Phase" value={data?.phase ?? '—'} tone={data?.phase === 'PRE_CLOSE_UNWIND' ? 'text-amber-400' : 'text-emerald-400'} />
          <Stat label="Minute (UTC)" value={data?.minute ?? '—'} />
          <Stat label="Wave" value={data?.wave_status ?? '—'} tone={data?.wave_status === 'INVALIDATED' ? 'text-red-400' : 'text-zinc-100'} />
          <Stat label="Session" value={data?.session_window ?? '—'} tone={data?.session_quarantine ? 'text-red-400' : 'text-emerald-400'} />
          <Stat label="Throttle" value={data?.throttle_state ? `${data.throttle_state} (${data.throttle_bots ?? 0})` : '—'} />
          <Stat label="Hurst HTF" value={data?.hurst_htf?.toFixed(3) ?? '—'} />
          <Stat label="Poly Bias" value={data?.poly_bias ?? '—'} />
          <Stat label="ONNX" value={data?.onnx_action ?? '—'} tone={data?.onnx_model_available ? 'text-emerald-400' : 'text-zinc-400'} />
        </div>
      )}
      {data?.shadow_plan && (
        <div className="mt-2 rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1 text-[10px] text-zinc-400">
          Schattenplan (nicht bindend) · {String(data.shadow_plan.published_at_utc ?? '')}
        </div>
      )}
    </PanelShell>
  );
}

/* ---------------------------------------------------------- 2 Geometry */

export function MarketGeometryPanel() {
  const [data] = usePoll(sigmaResearchApi.zones, 8000);
  return (
    <PanelShell title="Market Geometry" icon={<Layers size={13} />}>
      <PanelHeader data={data} text={`MP-03 · ${data?.interval_min ?? 15}m / 1h`} />
      {!data?.zones?.length ? (
        <EmptyState text="keine geschlossenen Bars / keine Zone — niemals gezeichnete Zonen ohne Feed" />
      ) : (
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-left text-zinc-500">
              <th className="py-0.5">Symbol</th><th>Zone</th><th>CE50</th><th>Alter</th><th>Bias</th>
            </tr>
          </thead>
          <tbody>
            {data.zones.map((z, i) => (
              <tr key={i} className="border-t border-zinc-800/60 font-mono">
                <td className="py-0.5">{String(z.symbol ?? '—')}</td>
                <td>{`${Number(z.low ?? 0).toFixed(4)}–${Number(z.high ?? 0).toFixed(4)}`}</td>
                <td>{Number(z.ce50 ?? 0).toFixed(4)}</td>
                <td>{String(z.age_bars ?? '—')}b</td>
                <td className={z.bias === 'aligned' ? 'text-emerald-400' : 'text-amber-400'}>{String(z.bias ?? '—')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!!data?.envelope && (
        <div className="mt-2 font-mono text-[10px] text-zinc-400">
          00:00-Envelope {String(Number(data.envelope.slope ?? 0) >= 0 ? '↑' : '↓')}
        </div>
      )}
    </PanelShell>
  );
}

/* ------------------------------------------------------------ 3 Power */

export function PowerPhysicsPanel() {
  const [data] = usePoll(sigmaResearchApi.power, 6000);
  const cos = data?.cos_phi ?? 0;
  const cluster = data?.cluster ?? '—';
  return (
    <PanelShell title="Power Physics" icon={<Activity size={13} />}>
      <PanelHeader data={data} text="MP-04 · last closed candle" />
      {!data?.ok ? (
        <EmptyState text="keine geschlossene Kerze / kein Feed (fail-closed)" />
      ) : (
        <div className="space-y-2">
          <Stat label="cos φ" value={cos.toFixed(3)} tone={cos >= 0.85 ? 'text-emerald-400' : cos < 0.3 ? 'text-red-400' : 'text-zinc-100'} />
          <Stat label="Cluster" value={cluster} />
          <div className="grid grid-cols-3 gap-1.5">
            <Stat label="S_norm" value={data?.s_norm?.toFixed(3) ?? '—'} />
            <Stat label="P_norm" value={data?.p_norm?.toFixed(3) ?? '—'} />
            <Stat label="Q_norm" value={data?.q_norm?.toFixed(3) ?? '—'} />
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <Stat label="Q_upper" value={data?.q_upper?.toFixed(3) ?? '—'} />
            <Stat label="Q_lower" value={data?.q_lower?.toFixed(3) ?? '—'} />
            <Stat label="Q_bias" value={data?.q_bias?.toFixed(3) ?? '—'}
              tone={(data?.q_bias ?? 0) > 0 ? 'text-emerald-400' : 'text-zinc-100'} />
            <Stat label="Resonanz" value={data?.resonance_badge ?? '—'} />
          </div>
          <div className="text-[10px] text-zinc-500">
            cos-φ-Pfad: {data?.cos_path?.length ?? 0} Werte · Schwellen ±0,40 Entry / ±0,15 Exit
          </div>
        </div>
      )}
    </PanelShell>
  );
}

/* ------------------------------------------------------------- 4 Scout */

export function SymbolScoutPanel() {
  const [data] = usePoll(sigmaResearchApi.scout, 9000);
  const [blinded, setBlinded] = useState(false);
  const rows = [...(data?.long_rank ?? []), ...(data?.short_rank ?? [])];
  return (
    <PanelShell title="Symbol Scout" icon={<Sparkles size={13} />}
      actions={
        <button onClick={() => setBlinded(!blinded)} title="Blinded-Modus (Ticker ausblenden)"
          className="rounded border border-zinc-700 p-1 text-zinc-400 hover:border-sky-500 hover:text-sky-400">
          {blinded ? <EyeOff size={11} /> : <Eye size={11} />}
        </button>
      }>
      <PanelHeader data={data} text="MP-05 · Stufe-2-Ranker · 1 Scan je geschlossener 1h-Bar" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="Universe wird gescannt … (kein Screening-Feed)" />
      ) : (
        <>
          <div className="mb-1.5 flex items-center gap-2 text-[10px] text-zinc-500">
            <span>last scan: {data?.last_scan_ts ? new Date((data.last_scan_ts ?? 0) * 1000).toISOString() : '—'}</span>
            <span className={data?.phase_ok ? 'text-emerald-400' : 'text-amber-400'}>
              {data?.phase_ok ? 'SCAN&DEPLOY' : 'Scan nur in Phase SCAN&DEPLOY'}
            </span>
          </div>
          {!rows.length ? (
            <EmptyState text="kein Coin erfüllt die Hard-Filter (r≥0,75, β≥1,5, RVOL≥1,5)" />
          ) : (
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-left text-zinc-500">
                  <th>Symbol</th><th>Side</th><th>β</th><th>r</th><th>RVOL</th><th>pos_EQ</th><th>Empfehlung</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => {
                  const rec = String(row.recommendation ?? '—');
                  const isLong = String(row.side ?? '').toUpperCase() === 'LONG';
                  return (
                    <tr key={i} className="border-t border-zinc-800/60 font-mono">
                      <td className="py-0.5">{blindedSymbol(String(row.symbol ?? ''), blinded)}</td>
                      <td className={isLong ? 'text-emerald-400' : 'text-red-400'}>{String(row.side ?? '—')}</td>
                      <td>{Number(row.beta ?? 0).toFixed(2)}</td>
                      <td>{Number(row.r ?? 0).toFixed(2)}</td>
                      <td>{Number(row.rvol ?? 0).toFixed(1)}</td>
                      <td className={(Number(row.pos_eq ?? 0.5) >= 0.9) ? 'text-red-400' : 'text-emerald-400'}>
                        {Number(row.pos_eq ?? 0.5).toFixed(2)}
                      </td>
                      <td className={rec.startsWith('sniper') ? 'text-emerald-400' : 'text-zinc-300'}>{rec}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <button disabled title="Scan-Trigger: Operator-Token + Bestätigungs-Modal (Backend noch nicht verfügbar)"
            className="mt-2 w-full rounded border border-zinc-700 px-2 py-1 text-[10px] text-zinc-500 disabled:cursor-not-allowed">
            Scan anstoßen (Operator + Modal)
          </button>
        </>
      )}
    </PanelShell>
  );
}

/* -------------------------------------------------------- 5 Polymarket */

export function PolymarketPanel() {
  const [data] = usePoll(sigmaResearchApi.polymarket, 9000);
  return (
    <PanelShell title="Polymarket L0" icon={<BarChart3 size={13} />}>
      <PanelHeader data={data} text="MP-06 · Layer 0 Kalibrierung" />
      {!data?.available ? (
        <div className="rounded border border-zinc-800 bg-zinc-900/40 px-3 py-4 text-center text-[11px] text-zinc-500">
          Polymarket feed unavailable — gate inaktiv
          <div className="mt-1 text-[10px] text-zinc-600">niemals geratene Wahrscheinlichkeiten</div>
        </div>
      ) : (
        <div className="space-y-1.5">
          <Stat label="μ (Erwartungswert)" value={data?.mu?.toFixed(4) ?? '—'} />
          <Stat label="Bias" value={data?.bias ?? '—'} />
          <Stat label="P_cal vs Gate 0,60–0,65" value={data?.p_cal?.toFixed(3) ?? '—'}
            tone={(data?.gate_open ?? false) ? 'text-emerald-400' : 'text-red-400'} />
          <Stat label="Brier" value={data?.brier?.toFixed(4) ?? '—'} />
          <Stat label="Platt" value={data?.platt_a != null && data?.platt_b != null ? `a=${data.platt_a.toFixed(3)} b=${data.platt_b.toFixed(3)}` : '—'} />
        </div>
      )}
    </PanelShell>
  );
}

/* ------------------------------------------------------------ 6 Ladder */

export function LadderArchitectPanel() {
  const [data] = usePoll(sigmaResearchApi.ladderPreview, 8000);
  return (
    <PanelShell title="Ladder Architect" icon={<Layers size={13} />}>
      <PanelHeader data={data} text="MP-02 · DCA-Leiter + MP-01 Guards" />
      {!data?.rungs?.length ? (
        <EmptyState text="keine Leiter-Preview ohne Backend (Deploy gesperrt)" />
      ) : (
        <>
          {data.rungs.map((r, i) => (
            <div key={i} className="mb-1 flex items-center justify-between font-mono text-[10px]">
              <span className="text-zinc-400">Stufe {String(r.step ?? i + 1)}</span>
              <span>{Number(r.price ?? 0).toFixed(4)}</span>
              <span className="text-zinc-500">{(Number(r.margin_pct ?? 0) * 100).toFixed(1)}%</span>
            </div>
          ))}
          <div className="mt-2 space-y-1">
            {data.guards.map((g) => (
              <div key={g.id} className={`flex items-center gap-1.5 text-[10px] font-mono ${g.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                {g.ok ? '✓' : '✗'} {g.id}
                {!g.ok && <span className="text-zinc-500">{g.reason ?? ''}</span>}
              </div>
            ))}
          </div>
          <div className={`mt-2 text-[10px] ${data.deploy_allowed ? 'text-emerald-400' : 'text-red-400'}`}>
            Deploy {data.deploy_allowed ? 'freigegeben (Modal + Operator)' : 'gesperrt — Guard rot / kein Backend'}
          </div>
        </>
      )}
    </PanelShell>
  );
}

/* ----------------------------------------------------------- 7 Fractal */

export function FractalTradePanel() {
  const [data] = usePoll(sigmaResearchApi.fractalPreview, 8000);
  const ks = data?.kill_switch ?? {};
  const triggered = ks.exhausted || ks.swept || Number(ks.minute ?? 0) >= 55;
  return (
    <PanelShell title="Fractal Trade" icon={<Target size={13} />}>
      <PanelHeader data={data} text="MP-15 · 40/30/20/10 Staffel" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="kein Fraktal-Plan ohne Backend (Preview read-only)" />
      ) : (
        <>
          <div className="grid grid-cols-3 gap-1.5">
            <Stat label="Side" value={data?.side ?? '—'} tone={data?.side === 'long' ? 'text-emerald-400' : 'text-red-400'} />
            <Stat label="Hebel" value={data?.leverage ?? '—'} />
            <Stat label="Entry" value={data?.entry?.toFixed(4) ?? '—'} />
          </div>
          {data.tranches.map((t, i) => (
            <div key={i} className="mt-1 flex items-center justify-between rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1 font-mono text-[10px]">
              <span>{String(t.label ?? `TP${i + 1}`)}</span>
              <span>{(Number(t.qty_pct ?? 0) * 100).toFixed(0)}% @ {Number(t.price ?? 0).toFixed(4)}</span>
              <span className={t.filled ? 'text-emerald-400' : 'text-zinc-500'}>{t.filled ? 'gefüllt' : 'offen'}</span>
            </div>
          ))}
          <div className="mt-1.5 text-[10px] font-mono text-zinc-400">
            Initial-SL {data?.initial_sl?.toFixed(4) ?? '—'} ({data?.sl_basis ?? '—'})
          </div>
          {data?.fee_covered_be != null && (
            <div className="mt-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-400">
              +0,05 % fee-covered Break-Even nach TP1 — Pflicht-Auto-Move (nicht abschaltbar) @ {data.fee_covered_be.toFixed(4)}
            </div>
          )}
          {triggered && (
            <div className="mt-1.5 rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[10px] text-red-400">
              auto-exit recommended — system beendet, Mensch startet
            </div>
          )}
        </>
      )}
    </PanelShell>
  );
}

/* ------------------------------------------------------- 8 Provisioner */

export function ProvisionerPanel() {
  const [data] = usePoll(sigmaResearchApi.provisions, 8000);
  return (
    <PanelShell title="Provisioner" icon={<Wand2 size={13} />}>
      <PanelHeader data={data} text="MP-09 · ephemere Pine-Agenten" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="keine provisionierten Strategien (fail-closed)" />
      ) : (
        <>
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-left text-zinc-500">
                <th>strategy_id</th><th>Symbol</th><th>Status</th><th>TTL</th>
              </tr>
            </thead>
            <tbody>
              {data.provisions.map((p, i) => (
                <tr key={i} className="border-t border-zinc-800/60 font-mono">
                  <td className="py-0.5">{String(p.strategy_id ?? '—')}</td>
                  <td>{String(p.symbol ?? '—')}</td>
                  <td>{String(p.status ?? '—')}</td>
                  <td>{String(p.ttl_s ?? '—')}s</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 text-[10px] text-zinc-500">
            Wächter: lookahead_off · bar-close-Alert · Schema-A-Payload · initial_capital=10000 ·
            pyramiding=1 · 0,04 % · calc_on_every_tick=false · idempotency_key je Alert
          </div>
          <button disabled title="Externes Pine härten: Operator-Token + Bestätigungs-Modal (Backend noch nicht verfügbar)"
            className="mt-2 w-full rounded border border-zinc-700 px-2 py-1 text-[10px] text-zinc-500 disabled:cursor-not-allowed">
            Externes Pine härten (Operator + Modal)
          </button>
        </>
      )}
    </PanelShell>
  );
}

/* ------------------------------------------------------------- 9 ONNX */

export function OnnxBrainPanel() {
  const [data] = usePoll(sigmaResearchApi.onnx, 6000);
  return (
    <PanelShell title="ONNX Brain" icon={<Brain size={13} />}>
      <PanelHeader data={data} text="MP-11 · BTC-Makro only (Stufe 2 = Scout)" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="kein Tensor ohne Backend — fallback policy aktiv" />
      ) : (
        <>
          {data.model_available ? (
            <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold text-emerald-400">
              ONNX MODEL LIVE
            </span>
          ) : (
            <span className="rounded border border-zinc-600/40 bg-zinc-700/20 px-1.5 py-0.5 text-[9px] font-bold text-zinc-400">
              DETERMINISTIC FALLBACK POLICY
            </span>
          )}
          <div className="mt-2 space-y-1">
            {data.tensor.map((f, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px] font-mono" title={`${f.name} in [-1,1]/[0,1]; fehlende Quelle = 0 (fail-closed)`}>
                <span className="w-24 text-zinc-500">{f.name}</span>
                <div className="flex-1"><Bar value={f.value} tone={f.value >= 0 ? 'bg-emerald-500/70' : 'bg-red-500/70'} /></div>
                <span className="w-10 text-right text-zinc-300">{f.value.toFixed(2)}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            <Stat label="LONG" value={`${((data.action_probs?.long ?? 0) * 100).toFixed(0)}%`} tone="text-emerald-400" />
            <Stat label="FLAT" value={`${((data.action_probs?.flat ?? 0) * 100).toFixed(0)}%`} />
            <Stat label="SHORT" value={`${((data.action_probs?.short ?? 0) * 100).toFixed(0)}%`} tone="text-red-400" />
          </div>
          <div className="mt-1.5 flex items-center gap-2 text-[10px] font-mono text-zinc-400">
            <span>Hebel {data?.leverage ?? '—'}x</span>
            <span>Entropie {data?.entropy?.toFixed(3) ?? '—'}</span>
            <span className={data?.bar_lock === 'BLOCKED_BY_BAR_LOCK' ? 'text-red-400' : ''}>{data?.bar_lock ?? '—'}</span>
            <span>{data?.latency_ms != null ? `${data.latency_ms.toFixed(2)}ms` : ''}</span>
          </div>
          <div className="mt-1.5 text-[10px] text-zinc-600">
            Tensor klassifiziert nur das BTC-Makro — Symbolwahl erfolgt im Scout (Stufe 2)
          </div>
        </>
      )}
    </PanelShell>
  );
}

/* ------------------------------------------------------------ 10 Risk */

export function RiskGuardPanel() {
  const [data] = usePoll(sigmaResearchApi.risk, 6000);
  return (
    <PanelShell title="Risk Guard" icon={<ShieldCheck size={13} />}>
      <PanelHeader data={data} text="MP-01 · Schutzschicht" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="keine Positionen / kein Feed (fail-closed)" />
      ) : (
        <>
          {data.positions.length === 0 && <EmptyState text="keine aktiven Positionen" />}
          {data.positions.map((p, i) => (
            <div key={i} className="mb-1.5 rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1 font-mono text-[10px]">
              <div className="flex justify-between">
                <span>{String(p.symbol ?? '—')}</span>
                <span className={p.liq_distance_pct != null && Number(p.liq_distance_pct) < 0.05 ? 'text-red-400' : 'text-zinc-300'}>
                  Liq {p.liq_distance_pct != null ? `${(Number(p.liq_distance_pct) * 100).toFixed(1)}%` : '—'}
                </span>
              </div>
              <div className="flex justify-between text-zinc-500">
                <span>Hard-Stop {Number(p.hard_stop ?? 0).toFixed(4)}</span>
                <span>Fee-BE {p.fee_covered ? 'ja' : 'nein'}</span>
                <span>Cooldown {String(p.cooldown_s ?? '—')}s</span>
              </div>
            </div>
          ))}
          <div className="mt-2 space-y-1">
            {data.rules.map((r) => (
              <div key={r.id} className="flex items-center gap-1.5 text-[10px] text-zinc-400">
                <Lock size={10} className="text-amber-400" />
                <span>{r.label}</span>
                {r.enabled && <span className="text-[9px] text-zinc-600">(fix aktiv, nicht abschaltbar)</span>}
              </div>
            ))}
          </div>
        </>
      )}
    </PanelShell>
  );
}

/* ----------------------------------------------------------- 11 Unwind */

export function UnwindPanel() {
  const [data] = usePoll(sigmaResearchApi.exhaustion, 6000);
  const score = data?.score ?? 0;
  return (
    <PanelShell title="Unwind" icon={<Gauge size={13} />}>
      <PanelHeader data={data} text="MP-08 · Exhaustion + geordnetes Glattstellen" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="kein Exhaustion-Score ohne Backend (fail-closed)" />
      ) : (
        <>
          <div className="mb-1 flex items-center justify-between text-[10px] font-mono">
            <span>Score</span><span className={data?.exhausted ? 'text-red-400' : 'text-zinc-300'}>{score.toFixed(2)}</span>
          </div>
          <Bar value={score} max={1} tone={data?.exhausted ? 'bg-red-500/70' : 'bg-emerald-500/70'} />
          <div className="mt-2 space-y-0.5 text-[10px] font-mono">
            {['bbw', 'oi', 'cvd'].map((k) => {
              const c = (data?.components?.[k] ?? {}) as Record<string, unknown>;
              return (
                <div key={k} className="flex items-center gap-1.5 text-zinc-400">
                  {c.available ? <span className="text-emerald-400">✓</span> : <span className="text-zinc-600">✗</span>}
                  <span className="uppercase">{k}</span>
                  {c.available ? <span>{Number(c.value ?? 0).toFixed(2)}</span> : <span className="text-zinc-600">kein Feed</span>}
                </div>
              );
            })}
          </div>
          {data.unwind.length > 0 && (
            <div className="mt-2 rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1 text-[10px]">
              {data.unwind.map((u, i) => (
                <div key={i} className="flex justify-between font-mono">
                  <span>{String(u.step ?? i + 1)}. {String(u.action ?? '')} {String(u.side ?? '')}</span>
                  <span className={u.forced ? 'text-red-400' : 'text-zinc-500'}>{u.forced ? 'forced close' : String(u.wait_condition ?? '')}</span>
                </div>
              ))}
              {data.forced && <div className="mt-1 text-[10px] text-red-400">Net-PnL-Guard: Verlust &gt; 50 % des Gewinns → forced close</div>}
            </div>
          )}
        </>
      )}
    </PanelShell>
  );
}

/* --------------------------------------------------------- 12 Research */

export function ResearchLabPanel() {
  const [data] = usePoll(sigmaResearchApi.researchDashboard, 10000);
  return (
    <PanelShell title="Research Lab" icon={<FlaskConical size={13} />}>
      <PanelHeader data={data} text="MP-12/16 · Hypothesen H1–H7 (Walk-Forward)" />
      {!data?.ok && !data?.available ? (
        <EmptyState text="keine Hypothesen-Jobs ohne Backend (fail-closed)" />
      ) : (
        <>
          {data.hypotheses.map((h, i) => (
            <div key={i} className="mb-1 flex items-center justify-between rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1 text-[10px]">
              <span className="font-mono">{String(h.id ?? '—')}</span>
              <span className="text-zinc-400">{String(h.status ?? '—')}</span>
              <span className="font-mono text-zinc-500">
                {h.effect_size != null ? `ES ${Number(h.effect_size).toFixed(3)} · n=${String(h.trades ?? '—')}` : ''}
              </span>
            </div>
          ))}
          {data.sweeps.map((s, i) => {
            const sharpe = Number(s.sharpe ?? 0);
            const dd = Number(s.max_dd ?? 1);
            const flag = sharpe > 3 || dd < 0.05;
            return (
              <div key={i} className="mb-1 flex items-center justify-between font-mono text-[10px]">
                <span>{String(s.name ?? '—')}</span>
                <span className="text-zinc-400">R {Number(s.return_pct ?? 0).toFixed(1)}% · DD {dd.toFixed(1)}%</span>
                {flag && <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1 text-[9px] text-amber-400">Overfitting-Flag</span>}
              </div>
            );
          })}
          {data.export_html_path && (
            <a href={data.export_html_path} className="mt-2 block text-[10px] text-sky-400 hover:underline">
              HTML-Dashboard exportieren (MP-16)
            </a>
          )}
          <button disabled title="Hypothesen-Run: Operator-Token + Bestätigungs-Modal (Backend noch nicht verfügbar)"
            className="mt-2 w-full rounded border border-zinc-700 px-2 py-1 text-[10px] text-zinc-500 disabled:cursor-not-allowed">
            Run (Operator + Modal)
          </button>
        </>
      )}
    </PanelShell>
  );
}
