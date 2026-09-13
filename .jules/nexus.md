## 2024-05-18 - [IPC/API Integration Insight]
**Learning:** Raw WebSocket dispatch loop crashes on malformed messages. Need robust stream wrapper with reconnect & schema validation.
**Action:** Replace `new WebSocket` with a robust reconnect stream or manual exponential backoff & retry.
## 2026-09-13 - [IPC/API Integration Insight]
**Learning:** Silent payload parsing failures and lack of exponential backoff in raw WebSocket listeners (e.g. ProcessLogView) cause fragile connections and blind spots during desync.
**Action:** Always implement robust reconnect loops with exponential backoff, manual strict type guards for payloads, and log parsing errors to console.error, gracefully degrading to polling when limits are exceeded.
