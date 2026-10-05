
## 2024-05-24 - [UI Aesthetic Insight]
**Learning:** Layout jitter caused by missing `tabular-nums` in `font-mono` text and the need for unified `FeedBadge` states in dark-glassmorphism panels.
**Action:** Always include `tabular-nums` alongside `font-mono` for dynamic numerical data like prices and percentage changes, and consistently use the `FeedBadge` for connection states in dark-glassmorphism components.
## 2024-05-24 - [UI Aesthetic Insight]
**Learning:** MarketPanel contained hardcoded zinc colors and lacked tabular-nums, violating the MP-17 dark-glassmorphism standard.
**Action:** Enforce bg-[#0a0a0c]/80, backdrop-blur-md, and tabular-nums for all dynamic numerical data.
