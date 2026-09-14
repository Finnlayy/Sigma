1. The issue describes optimizing CSS transitions for better rendering performance and layout thrashing (GPU rendering). Specifically:
    - Restrict all CSS transitions strictly to `transform` and `opacity`.
    - Do not animate properties that invalidate layout (width, height, margin, top, left).
    - Use `transform: scaleX(...)` for progress bars instead of animating `width`.

2. I will search for all components that animate `width` and change them to use `transform: scaleX(...)` with `transform-origin: left`.
    - `src/components/StrategyCard.tsx`
    - `src/components/GeneticOptimizerPanel.tsx`
    - `src/components/sigma/panels.tsx`
    - `src/components/quant/SystemHealthPanel.tsx`
    - `src/components/KrakenLedgersPanel.tsx`
    - `src/components/sigma/mp17Panels.tsx`

3. Let's make sure I'm doing the replacement correctly in each file:
    - Remove `style={{ width: \`\${pct}%\` }}`
    - Add `style={{ transform: \`scaleX(\${pct / 100})\`, transformOrigin: 'left' }}`
    - Make sure `transition-transform` or `transition-all` is applied. (Use `transition-transform` for better performance as it only targets transform).

4. Pre-commit check
