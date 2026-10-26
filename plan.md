1. **Understand the problem**: The issue requires fixing a connection issue for Nexus. Based on the memory:
    - "For Nexus WebSocket, IPC, and Server-Sent Events (EventSource) tasks, never ignore silent payload parsing failures. Always wrap message handlers in strict JSON validation, explicitly log parsing errors (e.g., to console.error), and ensure connections include exponential backoff reconnect loops to avoid masking corrupted payloads and silent stream deaths."
    - "To implement custom exponential backoff for EventSource streams in React useEffect, disable the native, potentially aggressive EventSource reconnect behavior by calling .close() inside the onerror handler, and manage the reconnection logic manually via a custom setTimeout timer. Ensure the useEffect cleanup function reliably clears this timer and closes the connection on component unmount."
    - "Always do: Validate all incoming UDS payloads against Protocol 1.2 schemas" (though SSE is maybe not UDS, it still needs schema validation with zod, if available).
2. **Review `src/components/quant/SystemHealthPanel.tsx`**:
    - The `EventSource` there does not have an exponential backoff reconnect loop. It relies on the native behavior.
    - It does have a `try/catch` around `JSON.parse(event.data)`, but no schema validation. We can add a simple `z.any()` or better schema if there is one. We should use standard error logging `console.error`.
    - "To implement custom exponential backoff for EventSource streams in React useEffect, disable the native, potentially aggressive EventSource reconnect behavior by calling .close() inside the onerror handler, and manage the reconnection logic manually via a custom setTimeout timer. Ensure the useEffect cleanup function reliably clears this timer and closes the connection on component unmount."
3. **Plan steps**:
    - Modify `src/components/quant/SystemHealthPanel.tsx`.
    - Import `z` from `zod`.
    - Create a basic schema for telemetry data (at least `.catchall(z.any())` or just use `z.any()` for now, or something more strict like `const TelemetrySchema = z.object({ state_machine: z.any().optional(), resource_guard: z.any().optional(), storage_tiering: z.any().optional(), watchdog: z.any().optional(), recent_logs: z.array(z.any()).optional() }).catchall(z.any());`)
    - Implement custom exponential backoff logic inside the `useEffect`.
    - Run `pnpm lint` and `pnpm build` to verify changes.
    - Add an entry to `.jules/nexus.md` as per instructions.
