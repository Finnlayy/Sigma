## 2024-05-20 - [Graphics / Rendering Insight]
**Learning:** Animating `width` property (e.g. for progress bars in StrategyCard components) causes layout-thrashing triggered by live orderbook re-renders, causing heavy CPU reflow on every frame.
**Action:** Hardware-acceleration rule applied: Switched from animating `width` to using `transform: scaleX(...)` on the GPU composite layer along with `transform-origin: left` to achieve equivalent visuals with zero CPU layout thrashing. Also enforced `contain: layout paint;` on isolated trading cards to restrict rendering calculations.

## 2024-03-24 - [Graphics / Rendering Insight]
**Learning:** [GPU / Framerate bottleneck root cause] Layout thrashing caused by animating width on budget progress bar in StrategyCard.
**Action:** [Hardware-acceleration rule applied] Replaced transition-all and width animation with transform: scaleX and transform-origin: left for GPU-accelerated transition, and added contain: layout paint to isolate layout operations.

## 2025-02-23 - [Graphics / Rendering Insight]
**Learning:** Found a layout-thrashing pattern triggered by live strategy list re-renders where animating `width` directly via `transition-all` on progress bars caused synchronous CPU layout recalculations and degraded framerate.
**Action:** Applied hardware-acceleration rule: enforced `contain: layout paint` on isolated UI components (`StrategyCard`) and refactored `width` animations to composite-layer `transform: scaleX(...)` with `transform-origin: left` to ensure pure GPU execution without frame drops.
