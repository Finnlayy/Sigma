import { motion } from "motion/react";
import {
  Zap, Pause, Ban, Skull, ArrowUpCircle, Wallet, Activity,
} from "lucide-react";

export interface M8InstanceState {
  strategy_id: string;
  status: "ACTIVE" | "THROTTLED" | "QUARANTINED" | "RETIRED" | string;
  base_budget_usd: number;
  current_budget_usd: number;
  budget_multiplier: number;
  consecutive_losses: number;
  consecutive_low_pf_days: number;
  shadow_trades_count: number;
  shadow_wins: number;
  last_ga_recalibration_ts?: number | null;
}

export interface StrategyCardProps {
  state: M8InstanceState;
  name?: string;
  symbol?: string;
  onPromote?: (id: string) => void;
  onQuarantine?: (id: string) => void;
}

const STATUS_META: Record<string, { label: string; cls: string; icon: any; desc: string }> = {
  ACTIVE: {
    label: "ACTIVE",
    cls: "bg-emerald-950/70 text-emerald-300 border-emerald-500/40",
    icon: Zap,
    desc: "Voll budget · 1.0x Multiplikator",
  },
  THROTTLED: {
    label: "THROTTLED",
    cls: "bg-amber-950/70 text-amber-300 border-amber-500/40",
    icon: Pause,
    desc: "Budget ≤50% Base · 0.5x Multiplikator",
  },
  QUARANTINED: {
    label: "QUARANTINED",
    cls: "bg-red-950/70 text-red-300 border-red-500/40",
    icon: Ban,
    desc: "Budget $0 oder 7× EOD PF<1 · nur Shadow",
  },
  RETIRED: {
    label: "RETIRED",
    cls: "bg-zinc-800/70 text-zinc-400 border-zinc-600/40",
    icon: Skull,
    desc: "Terminal · 4 Wochen Shadow ohne GA-Rekalibrierung",
  },
};

/**
 * StrategyCard — Blueprint v1.2.0 "Still Missing" UI (M8-Instanz-Karte).
 * Zeigt Live-Status, Budget-HWM-Fortschritt & State-Transitions-Steuerung.
 */
export function StrategyCard({ state, name, symbol, onPromote, onQuarantine }: StrategyCardProps) {
  const meta = STATUS_META[state.status] || STATUS_META.ACTIVE;
  const Icon = meta.icon;
  const pct = Math.min(100, Math.max(0, (state.current_budget_usd / Math.max(1e-9, state.base_budget_usd)) * 100));
  const barColor =
    state.status === "ACTIVE" ? "bg-emerald-500" :
    state.status === "THROTTLED" ? "bg-amber-500" : "bg-red-500";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`bg-slate-950/60 border rounded-xl p-3.5 space-y-2.5 ${
        state.status === "QUARANTINED" ? "border-red-800/70" : "border-slate-800"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-bold text-slate-100 truncate max-w-[170px]">
              {name || state.strategy_id.split("__")[0]}
            </span>
            <span className="text-[10px] font-mono text-slate-500">{symbol}</span>
          </div>
          <div className="text-[10px] font-mono text-slate-500 truncate max-w-[220px]">
            {state.strategy_id}
          </div>
        </div>
        <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold border flex items-center gap-1 shrink-0 ${meta.cls}`}>
          <Icon className="w-3 h-3" />
          {meta.label}
        </span>
      </div>

      {/* Budget vs. Base (High-Water-Mark) */}
      <div>
        <div className="flex justify-between text-[10px] font-mono text-slate-400 mb-1">
          <span className="flex items-center gap-1">
            <Wallet className="w-3 h-3" />
            Budget
          </span>
          <span>
            <span className="text-slate-100 font-bold">${state.current_budget_usd.toFixed(2)}</span>
            <span className="text-slate-500"> / ${state.base_budget_usd.toFixed(2)} Base</span>
            <span className={`ml-1.5 ${state.budget_multiplier >= 1 ? "text-emerald-400" : state.budget_multiplier > 0 ? "text-amber-400" : "text-red-400"}`}>
              ×{state.budget_multiplier}
            </span>
          </span>
        </div>
        <div className="w-full bg-slate-800/80 h-1.5 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${barColor} transition-all`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="flex justify-between mt-1 text-[9px] font-mono text-slate-500">
          <span>50% THROTTLE-Gate</span>
          <span>100% Base</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1.5 text-center">
        <div className="bg-slate-900/80 rounded border border-slate-800 py-1">
          <div className="text-[9px] font-mono text-slate-500 uppercase">Losses</div>
          <div className={`text-[11px] font-mono font-bold ${state.consecutive_losses >= 3 ? "text-red-400" : "text-slate-200"}`}>
            {state.consecutive_losses}
          </div>
        </div>
        <div className="bg-slate-900/80 rounded border border-slate-800 py-1">
          <div className="text-[9px] font-mono text-slate-500 uppercase">Low-PF EOD</div>
          <div className={`text-[11px] font-mono font-bold ${state.consecutive_low_pf_days >= 3 ? "text-amber-400" : "text-slate-200"}`}>
            {state.consecutive_low_pf_days}/7
          </div>
        </div>
        <div className="bg-slate-900/80 rounded border border-slate-800 py-1">
          <div className="text-[9px] font-mono text-slate-500 uppercase">Shadow W/T</div>
          <div className="text-[11px] font-mono font-bold text-slate-200">
            {state.shadow_wins}/{state.shadow_trades_count}
          </div>
        </div>
      </div>

      {meta.desc && (
        <div className="flex items-center gap-1.5 text-[10px] font-mono text-slate-500">
          <Activity className="w-3 h-3" />
          {meta.desc}
        </div>
      )}

      {(onPromote || onQuarantine) && state.status !== "RETIRED" && (
        <div className="flex gap-1.5 pt-1 border-t border-slate-800/70">
          {onPromote && (
            <button
              onClick={() => onPromote(state.strategy_id)}
              className="flex-1 py-1 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded text-[10px] font-mono font-bold flex items-center justify-center gap-1 transition-colors"
            >
              <ArrowUpCircle className="w-3 h-3" /> RE-PROMOTE
            </button>
          )}
          {onQuarantine && (
            <button
              onClick={() => onQuarantine(state.strategy_id)}
              className="flex-1 py-1 bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-500/30 rounded text-[10px] font-mono font-bold flex items-center justify-center gap-1 transition-colors"
            >
              <Ban className="w-3 h-3" /> QUARANTINE
            </button>
          )}
        </div>
      )}
    </motion.div>
  );
}
