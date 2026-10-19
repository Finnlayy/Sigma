
## 2024-05-24 - [UI Aesthetic Insight]
**Learning:** Layout jitter caused by missing `tabular-nums` in `font-mono` text and the need for unified `FeedBadge` states in dark-glassmorphism panels.
**Action:** Always include `tabular-nums` alongside `font-mono` for dynamic numerical data like prices and percentage changes, and consistently use the `FeedBadge` for connection states in dark-glassmorphism components.
## 2024-10-19 - [UI Aesthetic Insight]
**Learning:** Found inconsistent background tokens and missing backdrop-blur in MarketPanel chart and log containers, as well as missing tabular-nums typography in historical trade lists. Tooltips were missing glassmorphism baseline colors.
**Action:** Enforce bg-[#0a0a0c]/80 border-white/10 backdrop-blur-md on all outer panel shells. Apply font-mono tabular-nums to all price and volume data points, and standard rgba(10,10,12,0.8) background to Recharts tooltips.
