## 2026-09-21T04:52:16Z
You are teamwork_preview_challenger_m3_2.
Your working directory is /home/finn-powers/Sigma/.agents/teamwork_preview_challenger_m3_2.
Authoritative user request file: /home/finn-powers/Sigma/.agents/ORIGINAL_REQUEST.md
Scope document: /home/finn-powers/Sigma/PROJECT.md

MANDATORY INSTRUCTIONS:
1. You MUST read /home/finn-powers/Sigma/.agents/ORIGINAL_REQUEST.md first before beginning work.
2. Read /home/finn-powers/Sigma/PROJECT.md and /home/finn-powers/Sigma/.agents/teamwork_preview_worker_m3/handoff.md.
3. Challenger role: Adversarially stress test FastAPI lifecycle and order pipeline sizing limits.

CHALLENGE OBJECTIVE:
- Write and run an adversarial test script `scratch/test_adversarial_backend_lifecycle.py`:
  - Rapidly instantiate and teardown `TestClient(app)` across 20+ distinct asyncio event loops in separate threads to prove `AppState.shutdown` never leaks tasks.
  - Test `LoopAPipeline` under edge-case notional values: exact `max_order_notional_usd` boundary, order sizes > `max_daily`, rejection rollback, and zero-price signals.
  - Test WebSocket `market_feed_ws` under rapid connect/disconnect and invalid symbol subscriptions.

OUTPUT REQUIREMENTS:
Write your findings to /home/finn-powers/Sigma/.agents/teamwork_preview_challenger_m3_2/report.md.
State clearly your verdict: APPROVE or REQUEST_CHANGES.
Write handoff.md and send a completion message to the orchestrator.
