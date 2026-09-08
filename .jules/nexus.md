## 2024-05-18 - [IPC/API Integration Insight]
**Learning:** Raw WebSocket dispatch loop crashes on malformed messages. Need robust stream wrapper with reconnect & schema validation.
**Action:** Replace `new WebSocket` with a robust reconnect stream or manual exponential backoff & retry.
