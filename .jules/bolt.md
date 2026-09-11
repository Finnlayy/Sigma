## 2024-09-11 - [Optimize RegExp/Set within Array Filter loops]
**Learning:** Avoid compiling `new RegExp()` or instantiating a `new Set()` inside a tight iteration loop such as `array.filter()` during React render phases, as it reallocates and recompiles for each item, and on every render cycle.
**Action:** Memoize loop-invariant operations like building a `Set` or compiling a `RegExp` using `useMemo` outside of the `.filter()` / `.map()` blocks to prevent unnecessary reallocations and O(N) penalties.
