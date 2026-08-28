import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, RefreshCw, Save, Shield } from "lucide-react";

type SettingRow = {
  key: string;
  label: string;
  group: "secrets" | "runtime" | "risk" | string;
  value: string;
  isMasked: boolean;
  setInEnv: boolean;
};

const GROUP_COPY: Record<string, { title: string; hint: string }> = {
  secrets: {
    title: "Secrets",
    hint: "Werte landen in der gitignored .env und im laufenden Prozess. Bereits gesetzte Keys bleiben maskiert — zum Überschreiben neu einfügen.",
  },
  runtime: {
    title: "Runtime",
    hint: "Live bleibt aus, bis SIGMA_LIVE_TRADING=1. LLM-Tools ändern diese Keys nicht.",
  },
  risk: {
    title: "Risk knobs",
    hint: "Das kann die LLM-Konsole mit update_risk_settings auch anfassen. Secrets nicht.",
  },
};

export default function SettingsPage({ onCredentialsChanged }: { onCredentialsChanged?: () => void }) {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/settings");
      const data = await res.json();
      setRows(data.settings ?? []);
    } catch {
      setNotice("Settings-API nicht erreichbar — Core auf :8000?");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const grouped = useMemo(() => {
    const order = ["secrets", "runtime", "risk"];
    const map: Record<string, SettingRow[]> = {};
    for (const row of rows) {
      (map[row.group] ??= []).push(row);
    }
    return order.filter((g) => map[g]?.length).map((g) => [g, map[g]] as const);
  }, [rows]);

  const save = async (key: string) => {
    const value = drafts[key];
    if (value === undefined || value === "") {
      setNotice(`${key}: nichts zum Speichern — Feld ist leer.`);
      return;
    }
    setBusy(key);
    setNotice(null);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setNotice(typeof data.detail === "string" ? data.detail : `Save failed (${res.status})`);
        return;
      }
      setDrafts((d) => ({ ...d, [key]: "" }));
      setNotice(`${key} gespeichert.`);
      await load();
      onCredentialsChanged?.();
    } catch {
      setNotice("Save failed — Core offline?");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-zinc-900 border border-zinc-700 rounded-lg text-zinc-300">
            <KeyRound className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-white flex items-center space-x-2">
              <span>Settings &amp; Secrets</span>
              <span className="text-[10px] font-mono px-2 py-0.5 bg-zinc-900 text-zinc-400 border border-zinc-700 rounded font-semibold uppercase">
                .env
              </span>
            </h2>
            <p className="text-xs text-zinc-400">
              Lokal, ohne Passkey. Die LLM-Konsole kann nur Risk-Zahlen, keine API-Keys.
            </p>
          </div>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="p-1.5 bg-zinc-800 hover:bg-zinc-750 border border-zinc-700 text-zinc-300 rounded-lg text-xs font-mono flex items-center space-x-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-emerald-400" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {notice && (
        <div className="text-[11px] font-mono text-amber-300 bg-amber-950/40 border border-amber-800/60 rounded-lg px-3 py-2">
          {notice}
        </div>
      )}

      {grouped.map(([group, items]) => (
        <section key={group} className="bg-zinc-950/70 border border-zinc-800 rounded-xl p-4 space-y-3">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300 flex items-center gap-2">
              {group === "secrets" && <Shield className="w-3.5 h-3.5 text-rose-400" />}
              {GROUP_COPY[group]?.title ?? group}
            </h3>
            <p className="text-[11px] text-zinc-500 mt-1">{GROUP_COPY[group]?.hint}</p>
          </div>
          <div className="space-y-2">
            {items.map((row) => {
              const secret = row.group === "secrets";
              return (
                <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 items-end">
                  <label className="block min-w-0">
                    <div className="flex items-baseline justify-between gap-2 mb-1">
                      <span className="text-[11px] text-zinc-300">{row.label}</span>
                      <span className="text-[10px] font-mono text-zinc-600 truncate">{row.key}</span>
                    </div>
                    <input
                      type={secret ? "password" : "text"}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder={row.setInEnv ? (secret ? "gesetzt — neu einfügen zum Überschreiben" : row.value) : ""}
                      value={drafts[row.key] ?? ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [row.key]: e.target.value }))}
                      onKeyDown={(e) => e.key === "Enter" && void save(row.key)}
                      className="w-full bg-zinc-950 border border-zinc-700 text-xs text-zinc-200 px-3 py-1.5 rounded-lg font-mono focus:border-emerald-500 focus:outline-none"
                    />
                  </label>
                  <button
                    onClick={() => void save(row.key)}
                    disabled={busy === row.key}
                    className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-600 text-zinc-200 rounded-lg text-xs font-mono flex items-center gap-1.5 disabled:opacity-50"
                  >
                    <Save className="w-3.5 h-3.5" />
                    {busy === row.key ? "…" : "Save"}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
