## 2024-05-14 - [Accessible Icon Buttons]
**Learning:** React elements with `title` attributes but without text content (such as icon-only buttons) are not automatically read by screen readers on many platforms. A matching `aria-label` should be synced to the `title` attribute to ensure keyboard accessibility and full screen reader compatibility for interactive icon-only elements across the interface.
**Action:** Always include an `aria-label` describing the action when creating an interactive element that relies entirely on icons, even if a `title` tooltip exists.
