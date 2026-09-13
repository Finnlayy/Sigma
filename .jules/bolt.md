## 2024-05-24 - [React.memo in StrategyCard]
**Learning:** In the `ExecutionRiskPanel`, the parent component was passing inline arrow functions (`onPromote={(sid) => m8Action(sid, "promote")}`) and creating a lot of cards. Standard `React.memo` fails here because referential equality of those functions changes on every render.
**Action:** When memoizing React components that receive inline functions, write a custom `areEqual` function that compares the specific data properties rather than just using the default shallow prop comparison.
