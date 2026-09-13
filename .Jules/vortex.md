## 2024-05-18 - [Graphics / Rendering Insight]
**Learning:** Animating `width` on progress bars (like budget meters) triggers synchronous layout recalculations and heavy CPU reflow on every frame, causing layout thrashing and degrading framerate during frequent UI updates.
**Action:** Replaced `width` animations with `transform: scaleX(...)` and `transformOrigin: 'left'` combined with `transition-transform` for GPU-accelerated rendering. Also enforced `contain: layout paint;` on isolated trading cards (`StrategyCard.tsx`) to prevent rendering calculation cascades.
