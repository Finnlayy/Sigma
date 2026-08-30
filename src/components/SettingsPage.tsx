import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KeyRound, RefreshCw, Save, Shield } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type SettingRow = {
  key: string;
  label: string;
  group: "secrets" | "runtime" | "risk" | string;
  value: string;
  isMasked: boolean;
  setInEnv: boolean;
  kind?: string;
  format?: string;
  hint?: string;
  allowed?: string[];
  min?: number | null;
  max?: number | null;
};

type SaveTone = "ok" | "err" | "bad";
type SaveFlash = { tone: SaveTone; hint: string; format?: string; allowed?: string[] };

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

const FLASH_MS = 5000;

function unwrapDetail(data: any): { code?: string; detail: string; hint?: string; format?: string; allowed?: string[] } {
  const raw = data?.detail ?? data;
  if (typeof raw === "string") return { detail: raw };
  if (raw && typeof raw === "object") {
    const detail = typeof raw.detail === "string" ? raw.detail : JSON.stringify(raw);
    return {
      code: raw.code,
      detail,
      hint: raw.hint,
      format: raw.format,
      allowed: Array.isArray(raw.allowed) ? raw.allowed : undefined,
    };
  }
  return { detail: "Save failed" };
}

function validateDraft(value: string, row: SettingRow): string | null {
  const raw = value.trim();
  const hint = row.hint || `Erwartet: ${row.format || "gültiger Wert"}`;
  if (!raw) return `Feld ist leer. ${hint}`;
  const allowed = row.allowed ?? [];
  if (row.kind === "enum" && allowed.length) {
    if (!allowed.some((a) => a.toLowerCase() === raw.toLowerCase())) return hint;
  }
  if (row.kind === "flag" && raw !== "0" && raw !== "1") return hint;
  if (row.kind === "int") {
    if (!/^-?\d+$/.test(raw)) return hint;
    const n = Number(raw);
    if (row.min != null && n < row.min) return hint;
    if (row.max != null && n > row.max) return hint;
  }
  if (row.kind === "float") {
    const n = Number(raw);
    if (!Number.isFinite(n) || raw === "") return hint;
    if (row.min != null && n < row.min) return hint;
    if (row.max != null && n > row.max) return hint;
  }
  if (row.kind === "url") {
    const aliases = new Set(allowed.map((a) => a.toLowerCase()));
    if (aliases.has(raw.toLowerCase())) return null;
    try {
      const u = new URL(raw);
      if (u.protocol !== "http:" && u.protocol !== "https:") return hint;
    } catch {
      return hint;
    }
  }
  return null;
}

function flashClass(tone?: SaveTone): string {
  if (tone === "ok") {
    return "border-emerald-500 text-emerald-300 bg-emerald-950/50 shadow-[0_0_12px_rgba(16,185,129,0.55)]";
  }
  if (tone === "err") {
    return "border-red-500 text-red-300 bg-red-950/50 shadow-[0_0_12px_rgba(239,68,68,0.55)]";
  }
  if (tone === "bad") {
    return "border-amber-400 text-amber-200 bg-amber-950/50 shadow-[0_0_12px_rgba(251,191,36,0.55)]";
  }
  return "border-zinc-600 text-zinc-200 bg-zinc-800 hover:bg-zinc-700";
}

function iconClass(tone?: SaveTone): string {
  if (tone === "ok") return "text-emerald-400 drop-shadow-[0_0_8px_rgba(16,185,129,0.95)]";
  if (tone === "err") return "text-red-400 drop-shadow-[0_0_8px_rgba(239,68,68,0.95)]";
  if (tone === "bad") return "text-amber-300 drop-shadow-[0_0_8px_rgba(251,191,36,0.95)]";
  return "";
}

export default function SettingsPage({ onCredentialsChanged }: { onCredentialsChanged?: () => void }) {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [flash, setFlash] = useState<Record<string, SaveFlash>>({});
  const timers = useRef<Record<string, number>>({});

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

  useEffect(() => () => {
    Object.values(timers.current).forEach((id) => window.clearTimeout(id));
  }, []);

  const grouped = useMemo(() => {
    const order = ["secrets", "runtime", "risk"];
    const map: Record<string, SettingRow[]> = {};
    for (const row of rows) {
      (map[row.group] ??= []).push(row);
    }
    return order.filter((g) => map[g]?.length).map((g) => [g, map[g]] as const);
  }, [rows]);

  const pulse = (key: string, next: SaveFlash) => {
    if (timers.current[key]) window.clearTimeout(timers.current[key]);
    setFlash((prev) => ({ ...prev, [key]: next }));
    timers.current[key] = window.setTimeout(() => {
      setFlash((prev) => {
        const copy = { ...prev };
        delete copy[key];
        return copy;
      });
    }, FLASH_MS);
  };

  const save = async (row: SettingRow) => {
    const key = row.key;
    const value = drafts[key];
    if (value === undefined || value.trim() === "") {
      pulse(key, {
        tone: "bad",
        hint: `Feld ist leer. ${row.hint || "Bitte einen Wert eintragen."}`,
        format: row.format,
        allowed: row.allowed,
      });
      setNotice(null);
      return;
    }
    const localErr = validateDraft(value, row);
    if (localErr) {
      pulse(key, {
        tone: "bad",
        hint: localErr,
        format: row.format,
        allowed: row.allowed,
      });
      setNotice(null);
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
      const parsed = unwrapDetail(data);
      if (!res.ok) {
        const invalid = res.status === 400 && parsed.code !== "rejected";
        pulse(key, {
          tone: invalid ? "bad" : "err",
          hint: parsed.hint || parsed.detail,
          format: parsed.format || row.format,
          allowed: parsed.allowed ?? row.allowed,
        });
        if (!invalid) setNotice(parsed.detail);
        return;
      }
      if (data.applied === false) {
        pulse(key, { tone: "err", hint: "System hat den Wert abgelehnt.", format: row.format, allowed: row.allowed });
        return;
      }
      setDrafts((d) => ({ ...d, [key]: "" }));
      pulse(key, { tone: "ok", hint: `${key} übernommen.`, format: row.format, allowed: row.allowed });
      await load();
      onCredentialsChanged?.();
    } catch {
      pulse(key, { tone: "err", hint: "Save failed — Core offline?", format: row.format, allowed: row.allowed });
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
              const state = flash[row.key];
              const saveBtn = (
                <button
                  type="button"
                  onClick={() => void save(row)}
                  disabled={busy === row.key}
                  aria-label={`Save ${row.key}`}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 disabled:opacity-50 transition-shadow ${flashClass(state?.tone)}`}
                >
                  <Save className={`w-3.5 h-3.5 ${iconClass(state?.tone)}`} />
                  {busy === row.key ? "…" : "Save"}
                </button>
              );
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
                      onChange={(e) => {
                        const v = e.target.value;
                        setDrafts((d) => ({ ...d, [row.key]: v }));
                        if (flash[row.key]) {
                          setFlash((prev) => {
                            const copy = { ...prev };
                            delete copy[row.key];
                            return copy;
                          });
                        }
                      }}
                      onKeyDown={(e) => e.key === "Enter" && void save(row)}
                      className="w-full bg-zinc-950 border border-zinc-700 text-xs text-zinc-200 px-3 py-1.5 rounded-lg font-mono focus:border-emerald-500 focus:outline-none"
                    />
                  </label>
                  {state?.tone === "bad" ? (
                    <Tooltip open>
                      <TooltipTrigger asChild>{saveBtn}</TooltipTrigger>
                      <TooltipContent
                        side="left"
                        className="max-w-xs bg-amber-950 text-amber-50 border border-amber-600/70"
                      >
                        <div className="space-y-1 text-left">
                          <p>{state.hint}</p>
                          {state.format && (
                            <p className="font-mono text-[10px] text-amber-200/90">Format: {state.format}</p>
                          )}
                          {state.allowed && state.allowed.length > 0 && (
                            <p className="font-mono text-[10px] text-amber-200/90">
                              Erlaubt: {state.allowed.join(" · ")}
                            </p>
                          )}
                        </div>
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    saveBtn
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
