import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Legend,
} from "recharts";

export interface AutopsyEvent {
  trade_id: string;
  strategy_name?: string;
  symbol?: string;
  direction?: string;
  exit_reason?: string;
  net_pnl_usd?: number;
  pnl_r?: number;
  mfe_r?: number;
  mae_r?: number;
  capture_ratio?: number;
  stop_slippage_bps?: number;
  autopsy_zone?: string;
  exit_time?: string;
}

const ZONE_COLORS: Record<string, string> = {
  GOOD: "#10b981",
  WATCH: "#f59e0b",
  CLEAN_LOSS: "#38bdf8",
  BAD: "#ef4444",
  NEUTRAL_LOSS: "#a1a1aa",
};

/**
 * MfeMaeScatter — Blueprint v1.2.0 "Still Missing" UI.
 * MFE/MAE-Scatter aller Autopsien: jeder Punkt eine Trade-Section,
 * gefärbt nach v1.2.0-Zone. Referenzlinien: capture 0.55 & MFE 0.5R.
 */
export function MfeMaeScatter({ events }: { events: AutopsyEvent[] }) {
  if (!events || events.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-xs font-mono text-slate-500">
        Noch keine Autopsien — warten auf geschlossene Paper-Trades…
      </div>
    );
  }

  const zones = Array.from(new Set(events.map(e => e.autopsy_zone).filter(Boolean)));
  const byZone: Record<string, any[]> = {};
  for (const z of zones) {
    byZone[z] = events
      .filter(e => e.autopsy_zone === z)
      .map(e => ({
        mfe: e.mfe_r ?? 0,
        mae: e.mae_r ?? 0,
        r: e.pnl_r ?? 0,
        capture: e.capture_ratio ?? 0,
        name: e.strategy_name,
        zone: e.autopsy_zone,
        id: e.trade_id,
      }));
  }

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={260}>
        <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="mae"
            name="MAE (R)"
            domain={[-2.5, 0.3]}
            tick={{ fill: "#64748b", fontSize: 10, fontFamily: "monospace" }}
            stroke="#334155"
          />
          <YAxis
            type="number"
            dataKey="mfe"
            name="MFE (R)"
            domain={[0, 4]}
            tick={{ fill: "#64748b", fontSize: 10, fontFamily: "monospace" }}
            stroke="#334155"
          />
          <ZAxis type="number" dataKey="r" range={[50, 500]} />
          <ReferenceLine x={0} stroke="#475569" />
          <ReferenceLine y={0.5} stroke="#ef4444" strokeDasharray="4 4" label={{ value: "BAD-Gate 0.5R", fill: "#ef4444", fontSize: 9 }} />
          <ReferenceLine y={1.82} stroke="#10b981" strokeDasharray="4 4" label={{ value: "Capture 0.55 @ mfe 3.3R", fill: "#10b981", fontSize: 9 }} />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{
              background: "#020617", border: "1px solid #1e293b",
              borderRadius: 8, fontSize: 11, fontFamily: "monospace",
            }}
            formatter={(value: any, name: any) => [Number(value).toFixed(3), name]}
            labelFormatter={() => ""}
          />
          <Legend
            wrapperStyle={{ fontSize: 10, fontFamily: "monospace" }}
            iconType="circle"
            iconSize={7}
          />
          {zones.map(z => (
            <Scatter
              key={z}
              name={z}
              data={byZone[z]}
              fill={ZONE_COLORS[z] || "#94a3b8"}
              fillOpacity={0.75}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-3 justify-center pt-1">
        {Object.entries(ZONE_COLORS).map(([z, c]) => (
          <span key={z} className="text-[10px] font-mono text-slate-400 flex items-center gap-1">
            <span className="w-2 h-2 rounded-full" style={{ background: c }} />
            {z} ({byZone[z]?.length || 0})
          </span>
        ))}
      </div>
    </div>
  );
}
