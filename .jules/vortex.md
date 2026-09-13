## 2024-05-20 - [Graphics / Rendering Insight]
**Learning:** Animating `width` property (e.g. for progress bars in StrategyCard components) causes layout-thrashing triggered by live orderbook re-renders, causing heavy CPU reflow on every frame.
**Action:** Hardware-acceleration rule applied: Switched from animating `width` to using `transform: scaleX(...)` on the GPU composite layer along with `transform-origin: left` to achieve equivalent visuals with zero CPU layout thrashing. Also enforced `contain: layout paint;` on isolated trading cards to restrict rendering calculations.
