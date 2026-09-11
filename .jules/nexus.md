## 2025-03-09 - [IPC/API Integration Insight]
**Learning:** Silent payload parsing failures on raw WebSocket listeners can mask corrupted or desynced event payloads. Missing robust reconnect loops with exponential backoff on active subscriptions causes silent stream deaths.
**Action:** Always wrap WebSocket `.onmessage` handlers in strict JSON schema validation, explicitly logging failures to `console.error`. Implement a reconnect loop with exponential backoff for stream resilience before falling back to HTTP polling.
