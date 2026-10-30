## 2024-05-18 - [Graphics / Rendering Insight]
**Learning:** Animating `width` on progress bars (like budget meters) triggers synchronous layout recalculations and heavy CPU reflow on every frame, causing layout thrashing and degrading framerate during frequent UI updates.
**Action:** Replaced `width` animations with `transform: scaleX(...)` and `transformOrigin: 'left'` combined with `transition-transform` for GPU-accelerated rendering. Also enforced `contain: layout paint;` on isolated trading cards (`StrategyCard.tsx`) to prevent rendering calculation cascades.
## 2024-05-17 - [Graphics / Rendering Insight]
**Learning:** Found that using transition: all on elements like .mode-btn, button, .tab-btn, and .toast in vendor/tradingview-scraper/frontend/style.css triggers layout-thrashing by animating non-composite properties, degrading GPU framerates.
**Action:** Replaced transition: all with explicit transitions strictly restricted to transform, opacity, and other composite/paint layer properties to ensure zero-lag GPU execution.
