## 2025-02-23 - [Graphics / Rendering Insight]
**Learning:** Found a layout-thrashing pattern triggered by live strategy list re-renders where animating `width` directly via `transition-all` on progress bars caused synchronous CPU layout recalculations and degraded framerate.
**Action:** Applied hardware-acceleration rule: enforced `contain: layout paint` on isolated UI components (`StrategyCard`) and refactored `width` animations to composite-layer `transform: scaleX(...)` with `transform-origin: left` to ensure pure GPU execution without frame drops.
