## 2023-10-27 - [Graphics / Rendering Insight]
**Learning:** Animating the `width` CSS property on progress bars causes heavy CPU layout reflows (layout thrashing) leading to frame drops during frequent updates.
**Action:** Replaced `width` animations with hardware-accelerated `transform: scaleX(...)` and `transform-origin: left`, maintaining 120 FPS by utilizing the GPU composite layer.
