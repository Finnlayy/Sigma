## 2024-03-05 - [IPC/API Integration Insight]
**Learning:** Bare WebSocket listeners for log streaming and non-idempotent LLM actions can cause dropped messages and poor UX on network blips. Retrying non-idempotent operations like LLM streams can cause duplication in the UI.
**Action:** Implemented exponential backoff with fallback to HTTP polling for the continuous log stream, and added strict Zod payload validation to prevent silent JSON parse failures. Left the LLM stream without automatic retry to ensure exactly-once semantics.
