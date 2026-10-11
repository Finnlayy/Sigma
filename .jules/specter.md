## 2023-10-01 - [UI Aesthetic Insight]
**Learning:** Hardcoded `bg-zinc-900 border border-zinc-800` backgrounds violate the MP-17 Dark-Glassmorphism baseline (`bg-[#0a0a0c]/80 backdrop-blur-md border-white/10`).
**Action:** Replace all `bg-zinc-900 border border-zinc-800` panel container backgrounds with the standard MP-17 class `rounded-xl border border-white/10 bg-[#0a0a0c]/80 backdrop-blur-md p-4 shadow-2xl` for layout cohesion.
