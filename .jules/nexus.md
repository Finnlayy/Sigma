## 2024-05-18 - [IPC/API Integration Insight]
**Learning:** Raw WebSocket dispatch loop crashes on malformed messages. Need robust stream wrapper with reconnect & schema validation.
**Action:** Replace `new WebSocket` with a robust reconnect stream or manual exponential backoff & retry.
## 2026-09-13 - [IPC/API Integration Insight]
**Learning:** Silent payload parsing failures and lack of exponential backoff in raw WebSocket listeners (e.g. ProcessLogView) cause fragile connections and blind spots during desync.
**Action:** Always implement robust reconnect loops with exponential backoff, manual strict type guards for payloads, and log parsing errors to console.error, gracefully degrading to polling when limits are exceeded.

## 2024-03-05 - [IPC/API Integration Insight]
**Learning:** Bare WebSocket listeners for log streaming and non-idempotent LLM actions can cause dropped messages and poor UX on network blips. Retrying non-idempotent operations like LLM streams can cause duplication in the UI.
**Action:** Implemented exponential backoff with fallback to HTTP polling for the continuous log stream, and added strict Zod payload validation to prevent silent JSON parse failures. Left the LLM stream without automatic retry to ensure exactly-once semantics.

## 2025-03-08 - [IPC/API Integration Insight]
**Learning:** Idempotent UI subscriptions (like log feeds) were missing exponential backoff reconnect loops and silently swallowed malformed payloads, leading to permanent WebSocket drop-offs on temporary network instability.
**Action:** Implemented a robust exponential backoff reconnect with schema validation and explicit console error logging for UI subscriptions, gracefully degrading to HTTP polling without infinite loops on non-idempotent streams.
