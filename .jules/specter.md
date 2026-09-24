## 2025-02-27 - [UI Aesthetic Insight]
**Learning:** Found StrategyCard component using non-standard slate backgrounds (`bg-slate-950/60`, `border-slate-800`) and missing `tabular-nums` on dynamic numerical elements causing sub-pixel layout jitter during real-time state updates.
**Action:** Replaced container styling with the MP-17 standard `bg-[#0a0a0c]/80 backdrop-blur-md border border-white/10 shadow-2xl` and added `tabular-nums` to numerical fields to eliminate sub-pixel jitter.
