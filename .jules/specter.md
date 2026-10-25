
## 2024-05-24 - [UI Aesthetic Insight]
**Learning:** Layout jitter caused by missing `tabular-nums` in `font-mono` text and the need for unified `FeedBadge` states in dark-glassmorphism panels.
**Action:** Always include `tabular-nums` alongside `font-mono` for dynamic numerical data like prices and percentage changes, and consistently use the `FeedBadge` for connection states in dark-glassmorphism components.
## 2024-10-27 - [UI Aesthetic Insight]
**Learning:** Missing `font-mono` and `tabular-nums` on `FeedBadge` in `MarketPanel` causes subtle layout jitter and typography inconsistencies compared to `sigma/panels.tsx`.
**Action:** Enforce `font-mono` and `tabular-nums` on all `FeedBadge` variations across the codebase to ensure rigorous typography alignment within the MP-17 standard.
