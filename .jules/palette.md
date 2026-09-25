
## 2026-09-12 - Terminal Panel Accessibility Constraints
**Learning:** Custom toolbar and terminal interface elements lack accessible names and keyboard focus states, making keyboard navigation difficult.
**Action:** Always verify `aria-label` for icon buttons, `aria-pressed` for toggles, and `focus-visible` styling for all custom interactive controls.

## 2024-09-10 - Add aria-label to reusable IconBtn

**Learning:** Reusable components like IconBtn that contain icon-only visual information need explicit aria-labels so screen readers announce their function. When components accept a `title` prop for a native tooltip, it serves as a natural and intuitive fallback for `aria-label`.

**Action:** Ensure all generic, icon-only buttons include an explicit aria-label pointing to a meaningful descriptive text.

## 2024-05-14 - [Accessible Icon Buttons]
**Learning:** React elements with `title` attributes but without text content (such as icon-only buttons) are not automatically read by screen readers on many platforms. A matching `aria-label` should be synced to the `title` attribute to ensure keyboard accessibility and full screen reader compatibility for interactive icon-only elements across the interface.
**Action:** Always include an `aria-label` describing the action when creating an interactive element that relies entirely on icons, even if a `title` tooltip exists.
## 2024-05-17 - [Icon-Only Button Accessibility Pattern]
**Learning:** Found multiple instances of icon-only `<button>` elements (e.g., `<X>` for close, `<HelpCircle>` for info) lacking accessible names (like `aria-label`). While tooltips (`title`) existed in some places, they were missing in others, and relying solely on `title` isn't robust for all screen readers.
**Action:** Always ensure icon-only buttons have an explicit `aria-label` that describes their action, keeping them consistent with visual tooltips if present.
