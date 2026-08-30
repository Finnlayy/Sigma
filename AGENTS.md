# AGENTS.md — Finnlayy/Sigma

Jules and other coding agents: read this first.

## Environment

- Python 3.12, Node available.
- `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- `npm ci` (or `npm install`) before `npm run lint`.
- Tests: `.venv/bin/python -m pytest tests/ -q`
- Frontend typecheck: `npm run lint`

## Completeness (absolute)

Implement every required formula, constant, dataclass field, and test from
`docs/prompts/JULES-MASTER-EXECUTE.md` and the matching `docs/plans/MP-XX.md` /
`docs/prompts/MP-XX-*.md` **in full**.

Forbidden in production modules (`sigma/`, `app/`, `src/`):

- `TODO`, `FIXME`, `XXX`, `...`, `pass` as the body of a real function
- `NotImplementedError`, `raise NotImplementedError`
- `placeholder`, `coming soon`, `not yet`, `stub`, `dummy implementation`
- functions that only `return 0`, `return None`, `return {}`, `return []`,
  `return False` when a formula or contract exists
- `unittest.mock` / fake adapters used as the product implementation
- random numbers, invented prices, or hardcoded sample answers instead of math
- `# type: ignore` to hide incomplete code
- empty classes whose methods do nothing except satisfy an import

Allowed:

- Tests **must** use constructed/synthetic OHLCV lists (that is the spec).
- Fail-closed on missing feed (`valid=False`, `available=False`, empty schema)
  is real behavior, not a mock — the math on injected real-shaped payloads
  must still be complete.
- MP-17 empty GET responses when a fachmodul is absent: structured schema,
  never invented live numbers, never FeedBadge LIVE.

A phase is **not** DONE because the file exists. DONE only after:

1. Every named function/class from the phase checklist exists and computes
   the specified formula (read the prompt; implement the arithmetic).
2. Every listed pytest case exists and asserts **numeric / boolean contracts**,
   not merely `is not None`.
3. Grep over new production files finds none of the forbidden tokens above.
4. `.venv/bin/python -m pytest tests/ -q` is green (plus `npm run lint` for MP-17).

## Non-negotiable product rules

- Paper only (`kraken_paper`). No live orders, no `add_order`, no exchange credentials, no network in tests.
- `master_orchestrator.py` classifies and gates only. It never places orders or panic-closes.
- Closed bars only. No look-ahead. Fail-closed on missing/synthetic data.
- Do not weaken SessionClock, Dual-Hurst, throttle, or Wave gates.
- Optional phases MP-10, MP-13, MP-14 stay untouched unless a human explicitly asks.
- Constants from the prompts are **named and hardcoded** (0.005, 0.06, 0.0005, 1800, 7200, 0.40/0.15, 40/30/20/10, …). Do not replace them with “configurable later” placeholders.

## Jules execute loop (MANDATORY)

If `docs/plans/EXECUTE-STATE.md` has **Loop: ACTIVE** and **Next** is not `STOP`:

You are in an unattended sequential implement loop. The operator cannot restart you.

1. Read `docs/plans/EXECUTE-STATE.md`. **Next** is the only phase you may start.
2. Read `docs/prompts/JULES-MASTER-EXECUTE.md` **in full** for that phase (every step).
3. Read `docs/plans/MP-XX.md` + matching `docs/prompts/MP-XX-*.md` + cited KB sections. Implement every bullet, not a subset.
4. Implement on branch `jules/mp-execute` (create from `main` if missing). One accumulating PR — not one PR per phase.
5. Completeness gate, then tests green: mark the row DONE, set **Next** to the next CHAIN item, commit, push.
6. **Immediately start the new Next in this same session.** Do not wait for plan approval, review, merge, or a human.
7. Repeat 1–6 until **Next** is `STOP`.
8. If the VM/session is about to die: leave EXECUTE-STATE committed and pushed. A GitHub Action will start a new session from `jules/mp-execute`. That session must continue from **Next**, not from MP-01.

Never stop after a single phase while Loop is ACTIVE. Never ask “should I continue?”. Continue.
Never mark DONE on a stub so you can skip ahead.

First-session plan (approve once if the product requires it):  
“Implement every CHAIN phase fully (no stubs, no mocks, no fake returns) on `jules/mp-execute` until STOP, updating EXECUTE-STATE after every complete phase, without pausing for humans.”
