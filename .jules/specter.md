## 2024-05-15 - [UI Aesthetic Insight]
**Learning:** Netron Inspector panel uses a mismatched generic dark background (`#0e1117`) and lacks the explicit `FeedBadge` component for empty/offline states, violating the MP-17 standard.
**Action:** Replace `#0e1117` with `bg-[#0a0a0c]/80 backdrop-blur-md`, introduce a `FeedBadge` component to explicitly reflect the `data?.running` state, and ensure tabular numbers for size metrics.
