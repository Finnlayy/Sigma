import { useCallback, useEffect, useState } from "react";
import { safeFetchJson } from "@/lib/api";

type DesktopStatus = {
  mode: string; redis: string; market_source: string; backtest_available: boolean;
  kraken_cli_available: boolean; workspace: string; tasks_alive: boolean;
  worker: { running: boolean; counts: Record<string, number> };
};

/** Desktop integration diagnostics, not a replacement for the existing health/risk panels. */
export default function DesktopServicesPanel() {
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const refresh = useCallback(async () => {
    const result = await safeFetchJson<DesktopStatus>("/api/desktop/status");
    setStatus(result);
  }, []);
  useEffect(() => { void refresh(); const timer = setInterval(refresh, 5000); return () => clearInterval(timer); }, [refresh]);
  if (!status) return <div className="p-4 text-sm text-zinc-400">Desktop service diagnostics require the installed SIGMA application. Use System Health for a web deployment.</div>;
  return <section className="h-full overflow-auto space-y-4 p-4 text-sm">
    <h2 className="font-semibold">Ubuntu Desktop · Paper only</h2>
    <dl className="grid grid-cols-2 gap-2">
      <dt>Redis storage</dt><dd>{status.redis}</dd>
      <dt>Core tasks</dt><dd>{status.tasks_alive ? "Running" : "Unhealthy — check logs"}</dd>
      <dt>TradingView job worker</dt><dd>{status.worker.running ? "Running" : "Stopped"}</dd>
      <dt>Kraken CLI</dt><dd>{status.kraken_cli_available ? "Installed" : "Unavailable"}</dd>
      <dt>Market feed</dt><dd>{status.market_source}</dd>
      <dt>MCP backtests</dt><dd>{status.backtest_available ? "Configured (upstream checked per job)" : "Unavailable — set a real SIGMA_TV_MCP_URL"}</dd>
    </dl>
    <p className="text-zinc-400">Paper orders are simulated by the existing paper engine, never live exchange orders. Missing market data, sessions and MCP services remain unavailable; no synthetic feed is substituted.</p>
    <p className="text-zinc-400">Use Pine Studio → TradingView Login for a real session. Configure MCP and Telegram in Settings. After changing connection settings or signing in, use the SIGMA menu → Restart services. Ollama requires a separately installed local model service.</p>
    <p className="break-all font-mono text-xs">Persistent workspace: {status.workspace}</p>
    <p className="text-xs text-zinc-400">The SIGMA menu opens logs/configuration, manages login autostart, and quits all services. Closing the window hides it to the tray when available. On GNOME, tray visibility may require AppIndicator support.</p>
    <button className="rounded border border-white/10 px-3 py-2" onClick={() => void refresh()}>Refresh service status</button>
  </section>;
}
