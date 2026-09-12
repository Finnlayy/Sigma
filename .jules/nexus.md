## 2026-09-12 - [WebSocket Reconnection Architecture]
**Learning:** Raw WebSocket implementations without robust error handling or backoff strategies lead to fragile connections, while missing cleanup in unmounts causes memory leaks and duplicate message streams.
**Action:** Always wrap WebSocket logic with an exponential backoff loop, ensure state clearing within event handlers (to avoid concurrent reconnect timers on `error` + `close` sequential triggers), and always validate incoming IPC payloads explicitly.
