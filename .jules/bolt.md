## 2024-09-12 - [RegExp and Lookup Arrays in React Render Loops]
**Learning:** Instantiating `new RegExp()` inside a tight iteration loop such as `lines.filter()` results in high overhead, as it reallocates and recompiles the expression for every item on every render cycle. Additionally, performing lookups via `.includes()` on arrays inside filter blocks adds O(N) overhead per item.
**Action:** Extract inline `new RegExp()` logic, as well as lookup arrays, and wrap them in a `useMemo` block. For arrays, convert them into `Set` instances to ensure O(1) `.has()` checks during array iterations, saving significant main-thread block time.
## 2024-05-24 - [React.memo in StrategyCard]
**Learning:** In the `ExecutionRiskPanel`, the parent component was passing inline arrow functions (`onPromote={(sid) => m8Action(sid, "promote")}`) and creating a lot of cards. Standard `React.memo` fails here because referential equality of those functions changes on every render.
**Action:** When memoizing React components that receive inline functions, write a custom `areEqual` function that compares the specific data properties rather than just using the default shallow prop comparison.
