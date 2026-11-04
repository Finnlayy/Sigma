
## 2026-09-12 - Terminal Panel Accessibility Constraints
**Learning:** Custom toolbar and terminal interface elements lack accessible names and keyboard focus states, making keyboard navigation difficult.
**Action:** Always verify `aria-label` for icon buttons, `aria-pressed` for toggles, and `focus-visible` styling for all custom interactive controls.

## 2024-09-10 - Add aria-label to reusable IconBtn

**Learning:** Reusable components like IconBtn that contain icon-only visual information need explicit aria-labels so screen readers announce their function. When components accept a `title` prop for a native tooltip, it serves as a natural and intuitive fallback for `aria-label`.

**Action:** Ensure all generic, icon-only buttons include an explicit aria-label pointing to a meaningful descriptive text.

## 2024-05-14 - [Accessible Icon Buttons]
**Learning:** React elements with `title` attributes but without text content (such as icon-only buttons) are not automatically read by screen readers on many platforms. A matching `aria-label` should be synced to the `title` attribute to ensure keyboard accessibility and full screen reader compatibility for interactive icon-only elements across the interface.
**Action:** Always include an `aria-label` describing the action when creating an interactive element that relies entirely on icons, even if a `title` tooltip exists.
## 2025-03-08 - Accessible Icon-Only Buttons in StrategyEditor
**Learning:** The `StrategyEditor` component had icon-only buttons for critical actions (Delete Strategy, Move to Archives, Close Manifest Modal) lacking `aria-label`s, rendering them inaccessible to screen readers and difficult to identify for keyboard navigators. Although `title` was present for some, `aria-label` provides a more robust standard for screen readers.
**Action:** Always verify that every interactive element using an icon-only approach includes a descriptive `aria-label` attribute describing its function, ensuring accessibility for all users.

## 2024-11-03 - Custom Tab Selection Accessibility
**Learning:** Custom tab interfaces built using standard `div` and `button` elements lack correct semantics. They are not identified as tabs by screen readers, and toggling them doesn't convey selected states. Keyboard focus visibility may also be obscured or absent.
**Action:** Always wrap custom tab toggles in a `role="tablist"` container with an `aria-label`. Use `role="tab"` and `aria-selected` on the buttons themselves. Add `focus-visible:ring-2` to guarantee clear keyboard focus indication.
