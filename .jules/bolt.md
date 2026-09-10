## 2026-09-10 - [O(1) Set Search & RegExp Memoization]
**Learning:** During heavy log streaming (e.g. up to 2000 lines matching via WebSocket), using `Array.includes()` for checking if a subsystem is selected or inline-compiling a regex using `new RegExp()` in a tight filtering loop causes main thread blockage and memory spikes.
**Action:** When filtering large arrays or streaming logs inside React, use `new Set()` wrapped in `useMemo` for O(1) membership testing and memoize the regex outside the loop to prevent repeated re-allocation and re-compilation on every render cycle.
