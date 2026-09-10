## 2025-03-08 - [IPC/API Integration Insight]
**Learning:** Idempotent UI subscriptions (like log feeds) were missing exponential backoff reconnect loops and silently swallowed malformed payloads, leading to permanent WebSocket drop-offs on temporary network instability.
**Action:** Implemented a robust exponential backoff reconnect with schema validation and explicit console error logging for UI subscriptions, gracefully degrading to HTTP polling without infinite loops on non-idempotent streams.
