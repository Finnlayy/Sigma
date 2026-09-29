## 2024-05-24 - [UI Aesthetic Insight]
**Learning:** StrategyCard wrapper missing Dark-Glassmorphism baseline `#0a0a0c` base color, `backdrop-blur-md`, and structural `tabular-nums` typography for rapidly changing data fields.
**Action:** Apply `bg-[#0a0a0c]/80 backdrop-blur-md` baseline to isolated panels. Enforce `tabular-nums` alongside `font-mono` on all high-frequency numeric elements (like prices or metrics) to avoid sub-pixel layout jitter during real-time updates.
