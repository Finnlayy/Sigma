## $(date +%Y-%m-%d) - [IPC/API Integration Insight]
**Learning:** Raw WebSocket message handlers inside `MarketChart` and `LLMConsole` were exposed to corrupted payloads and inconsistent validation, failing silently or returning incomplete components due to schema/interface mismatch.
**Action:** Enforced fail-closed constraints by wrapping all WebSocket `.onmessage` handlers in `MarketFeedMsgSchema` and `LlmMsgSchema` using strictly aligned Zod validation (`.parse()`), ensuring robust parsing failures are caught and explicitly logged via `console.error`.
