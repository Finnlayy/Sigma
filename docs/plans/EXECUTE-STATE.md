# Jules Execute State

<!-- jules-loop:ACTIVE -->
<!-- jules-next:MP-01 -->

**Loop:** ACTIVE
**Next:** MP-01
**Branch:** `jules/mp-execute`
**PR:** one accumulating PR from that branch — do not open a new PR per phase
**Human:** not available. Do not wait. Do not ask. Continue until **Next** is `STOP`.

CHAIN (mandatory order, skip optional MP-10/13/14):

`MP-01 → MP-02 → MP-03 → MP-04 → MP-05 → MP-06 → MP-07 → MP-08 → MP-09 → MP-15 → MP-11 → MP-12 → MP-16 → MP-17 → STOP`

| MP | Code | Tests | Notes |
|---|---|---|---|
| MP-01 | PENDING | PENDING | |
| MP-02 | PENDING | PENDING | |
| MP-03 | PENDING | PENDING | |
| MP-04 | PENDING | PENDING | |
| MP-05 | PENDING | PENDING | |
| MP-06 | PENDING | PENDING | |
| MP-07 | PENDING | PENDING | |
| MP-08 | PENDING | PENDING | |
| MP-09 | PENDING | PENDING | |
| MP-15 | PENDING | PENDING | |
| MP-11 | PENDING | PENDING | |
| MP-12 | PENDING | PENDING | |
| MP-16 | PENDING | PENDING | |
| MP-17 | PENDING | PENDING | |
| MP-10 | SKIP | SKIP | optional — do not implement |
| MP-13 | SKIP | SKIP | optional — do not implement |
| MP-14 | SKIP | SKIP | optional — do not implement |

## How to update this file (every finished phase)

Mark `DONE` only if the phase is **complete** per `AGENTS.md` Completeness
and `docs/prompts/JULES-MASTER-EXECUTE.md` (real formulas, all tests, no
stubs/mocks/placeholders). File existence is not enough.

After that:

1. Set that row to `DONE` and write the commit SHA in Notes.
2. Set **Next** to the following item in CHAIN (after MP-09 comes MP-15, not MP-10).
3. Update the HTML comments `jules-loop` / `jules-next` to match.
4. Commit this file in the **same** commit as the phase (or immediately after).
5. If **Next** is `STOP`, set **Loop** to `DONE` and stop invoking new work.

If the NEW files for **Next** already exist **and** pass the completeness
gate (formulas + tests + no forbidden tokens), mark DONE and advance.
If they are stubs, **do not** advance — finish the real implementation first.
