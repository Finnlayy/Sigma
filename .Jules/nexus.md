## 2025-02-28 - [IPC/API Integration Insight]
**Learning:** Default EventSource and WebSocket error handlers can cause aggressive retry loops or masking of silent parsing failures by appending raw strings to UI state.
**Action:** Implement manual exponential backoff in `onerror` by explicitly closing streams and using `setTimeout`. Enforce fail-closed schema validation by dropping frames instead of falling back to raw payload strings.
