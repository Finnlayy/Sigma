
## 2026-09-12 - Terminal Panel Accessibility Constraints
**Learning:** Custom toolbar and terminal interface elements lack accessible names and keyboard focus states, making keyboard navigation difficult.
**Action:** Always verify `aria-label` for icon buttons, `aria-pressed` for toggles, and `focus-visible` styling for all custom interactive controls.

## 2024-09-10 - Add aria-label to reusable IconBtn

**Learning:** Reusable components like IconBtn that contain icon-only visual information need explicit aria-labels so screen readers announce their function. When components accept a `title` prop for a native tooltip, it serves as a natural and intuitive fallback for `aria-label`.

**Action:** Ensure all generic, icon-only buttons include an explicit aria-label pointing to a meaningful descriptive text.
