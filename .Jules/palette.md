## 2023-10-09 - Added accessible close labels to Sigma dock tabs
**Learning:** In highly customized, dynamic interfaces (like the dock panel system mapping generic tab IDs to titles), interactive elements (like an `X` icon used as a `role="button"`) often lack context for screen readers and tooltips, which makes standard UX poor for visual users too.
**Action:** Always inject dynamic `aria-label` and `title` properties based on the contextual UI state (e.g. `` aria-label={`Close ${title}`} ``) rather than generic strings.
