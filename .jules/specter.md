## 2026-09-27 - [UI Aesthetic Insight]
**Learning:** The MP-17 Dark-Glassmorphism standard and visual balance are violated in several components:
1. `FeedBadge` silently fails (returns null) for offline feeds instead of showing an explicit Fail-Closed state error badge.
2. `Stat` component rendering numerical values is missing `tabular-nums`, causing sub-pixel layout jitter during real-time updates.
3. `PanelShell` is missing the required dark-glassmorphism background (`bg-[#0a0a0c]/80 backdrop-blur-md`), and uses incorrect contrast colors instead of WCAG AAA `text-neutral-400`, and lacks the subtle `border-white/10` with `shadow-2xl`.
**Action:**
1. Enforce `DISCONNECTED` explicit empty badge state in `FeedBadge`.
2. Add `tabular-nums` to all numerical fields (like `Stat`).
3. Apply `bg-[#0a0a0c]/80 backdrop-blur-md rounded-xl border border-white/10 shadow-2xl` to container panels and enforce crisp monospace typography (`font-mono text-xs text-neutral-400`) in headers.
