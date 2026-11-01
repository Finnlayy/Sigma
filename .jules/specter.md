## 2024-05-18 - [UI Aesthetic Insight]
**Learning:** Raw inline styles were being used for dynamic colors in queue matrices, and critical numerical fields lacked monospace/tabular styling, causing jitter.
**Action:** Enforce `font-mono tabular-nums` on all volume, rate, and PnL fields, and map dynamic palette colors to semantic Tailwind background tokens.
