## 2026-10-25 - Enhance Custom Toggle Accessibility and Focus States
**Learning:** Custom UI toggles (like chart view modes and marker filters) were lacking 'aria-pressed' attributes, which is essential for screen readers to convey active states. Furthermore, icon-only utility buttons across panels were missing distinct keyboard focus states ('focus-visible').
**Action:** Add 'aria-pressed' mapped to the boolean active state for all custom interactive toggles. Add explicit 'focus-visible:ring-2 focus-visible:outline-none' Tailwind classes to generic utility/icon buttons to ensure they remain keyboard navigable without relying on default browser styling or pure hover states.
## 2024-05-24 - Enhance Custom Tabs WAI-ARIA Semantics
**Learning:** Custom tab interfaces require strict WAI-ARIA semantics (role="tablist", role="tab", aria-selected) and explicit focus rings (focus-visible) for accessibility to ensure they are screen reader friendly and keyboard navigable.
**Action:** Add proper ARIA roles and focus-visible classes to the StrategyMatrixModal navigation tabs, wrapping them in a tablist container and giving each tab explicit roles and focus rings.
