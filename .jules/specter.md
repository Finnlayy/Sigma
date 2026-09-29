## 2026-09-28 - [UI Aesthetic Insight]
**Learning:** The ubiquitous `Stat` component used arbitrary dark backgrounds and lacked monospace/tabular alignments, causing subtle layout jitter during rapid real-time websocket updates, and poor text contrast.
**Action:** Enforced the MP-17 Dark-Glassmorphism baseline (`bg-[#0a0a0c]/80 backdrop-blur-md border-white/10`) and required `tabular-nums` on numerical readout containers across all widgets to freeze character widths. Upgraded muted labels to `text-zinc-400` for better WCAG compliance.
