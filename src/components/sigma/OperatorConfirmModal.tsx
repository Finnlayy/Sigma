/**
 * =========================================================
 * Datei:      src/components/sigma/OperatorConfirmModal.tsx
 * Zweck:      MP-17 — Bestätigungs-Modal für Operator-Schreibzugriffe
 *             (Scan / Harden / Run / Provision). Kein Auto-Deploy;
 *             ohne Token bleibt die Aktion blockiert (403-UI).
 * System:     Manas: Ciel Core Matrix — Projekt:Sigma
 * =========================================================
 */
import { Lock, ShieldAlert, X } from 'lucide-react';

export interface OperatorConfirmModalProps {
  open: boolean;
  title: string;
  detail: string;
  confirmLabel?: string;
  busy?: boolean;
  blockedReason?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Bestätigungs-Dialog nach dem StrategyQueueConfirmModal-Muster. */
export function OperatorConfirmModal({
  open,
  title,
  detail,
  confirmLabel = 'Bestätigen (Operator)',
  busy = false,
  blockedReason = null,
  onConfirm,
  onCancel,
}: OperatorConfirmModalProps) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-md space-y-4 rounded-xl border border-amber-600/70 bg-zinc-900 p-5 shadow-2xl ring-1 ring-amber-500/20"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="rounded-lg border border-amber-700 bg-amber-950/80 p-2 text-amber-400">
              <ShieldAlert size={18} />
            </div>
            <div>
              <h3 className="text-sm font-bold uppercase tracking-wider text-white">{title}</h3>
              <p className="mt-0.5 text-[11px] text-zinc-400">
                Operator-Token + Bestätigungs-Modal — kein Auto-Deploy, keine Orders.
              </p>
            </div>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            aria-label="Schließen"
          >
            <X size={16} />
          </button>
        </div>

        <p className="rounded border border-zinc-800 bg-zinc-950 px-3 py-2 font-mono text-[11px] text-zinc-300">
          {detail}
        </p>

        {blockedReason && (
          <div className="flex items-start gap-2 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-[11px] text-red-300">
            <Lock size={12} className="mt-0.5 shrink-0" />
            <span>{blockedReason}</span>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded border border-zinc-700 px-3 py-1.5 text-[11px] text-zinc-300 hover:bg-zinc-800"
          >
            Abbrechen
          </button>
          <button
            type="button"
            disabled={busy || !!blockedReason}
            onClick={onConfirm}
            className="rounded border border-amber-600/60 bg-amber-600/20 px-3 py-1.5 text-[11px] font-semibold text-amber-200 hover:bg-amber-600/30 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? '…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Fokus/ Prefill-Hinweis für Fractal-/Ladder-Panels (Scout → Provisionieren). */
export const SIGMA_FOCUS_PANEL_EVENT = 'sigma:focus-panel';

export type SigmaFocusPanelDetail = {
  panelId: string;
  symbol?: string;
  recommendation?: string;
  side?: string;
};

export function requestFocusPanel(detail: SigmaFocusPanelDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(SIGMA_FOCUS_PANEL_EVENT, { detail }));
  try {
    sessionStorage.setItem('sigma:provision-hint', JSON.stringify(detail));
  } catch {
    /* ignore */
  }
}
