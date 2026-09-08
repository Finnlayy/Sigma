## 2024-03-24 - [Graphics / Rendering Insight]
**Learning:** [GPU / Framerate bottleneck root cause] Layout thrashing caused by animating width on budget progress bar in StrategyCard.
**Action:** [Hardware-acceleration rule applied] Replaced transition-all and width animation with transform: scaleX and transform-origin: left for GPU-accelerated transition, and added contain: layout paint to isolate layout operations.
