# AGENTS.md — Finnlayy/Sigma

Jules and other coding agents: read this first.

## Environment

- Python 3.12, Node available.
- `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- `npm ci` (or `npm install`) before `npm run lint`.
- Tests: `.venv/bin/python -m pytest tests/ -q`
- Frontend typecheck: `npm run lint`

## Non-negotiable product rules

- Paper only (`kraken_paper`). No live orders, no `add_order`, no exchange credentials, no network in tests.
- `master_orchestrator.py` classifies and gates only. It never places orders or panic-closes.
- Closed bars only. No look-ahead. Fail-closed on missing/synthetic data.
- Do not weaken SessionClock, Dual-Hurst, throttle, or Wave gates.
- Optional phases MP-10, MP-13, MP-14 stay untouched unless a human explicitly asks.

## Jules execute loop (MANDATORY)

If `docs/plans/EXECUTE-STATE.md` has **Loop: ACTIVE** and **Next** is not `STOP`:

You are in an unattended sequential implement loop. The operator cannot restart you.

1. Read `docs/plans/EXECUTE-STATE.md`. **Next** is the only phase you may start.
2. Read `docs/prompts/JULES-MASTER-EXECUTE.md` for that phase’s files, tests, and hartregeln.
3. Read `docs/plans/MP-XX.md` + matching `docs/prompts/MP-XX-*.md`.
4. Implement on branch `jules/mp-execute` (create from `main` if missing). One accumulating PR — not one PR per phase.
5. When tests for that phase are green: mark the row DONE, set **Next** to the next CHAIN item, commit, push.
6. **Immediately start the new Next in this same session.** Do not wait for plan approval, review, merge, or a human.
7. Repeat 1–6 until **Next** is `STOP`.
8. If the VM/session is about to die: leave EXECUTE-STATE committed and pushed. A GitHub Action will start a new session from `jules/mp-execute`. That session must continue from **Next**, not from MP-01.

Never stop after a single phase while Loop is ACTIVE. Never ask “should I continue?”. Continue.

First-session plan (approve once if the product requires it):  
“Implement CHAIN in EXECUTE-STATE sequentially on `jules/mp-execute` until STOP, updating EXECUTE-STATE after every phase, without pausing for humans.”
