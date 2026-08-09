# Lane-transition counts measured ways, not movements

- User goal: understand a `lane_transition_count_mismatch` that said two incoming
  lanes while highlighting one, and depicted a turn that is not valid.
- Run timestamp: 2026-08-09 15:12:08 +08 (Asia/Singapore)

## Actions Taken

- Audited all 19 `lane_transition_count_mismatch` findings against the surviving
  connectors and continuations. 18 described no lane-count change; 10 named a source
  lane with no movement into the named destination; 2 named transitions with no
  movement at all.
- Replaced the inline emit block (`generation.py:1411–1435`) with
  `_lane_collapse_findings`, called after the connector loop. It groups surviving links
  by `(node, approach edge, destination edge)` and emits only where distinct approach
  lanes outnumber the distinct destination lanes they reach.
- Bumped `GENERATOR_VERSION` to `direct-osm-stage2-v16`; schema version unchanged.
- Reworded the rule's question and accept effect in `web/src/controls.ts`; rebuilt the
  bundle.
- Updated `docs/policies/stage-2-finding-reference.md`,
  `docs/policies/stage-2-generation-v1.md` and the still-open defect note in
  `CLAUDE.md`.
- Wrote `docs/mapping-algo-changes/2026-08-09-15:12:08-lane-transition-counts-measured-ways-not-movements.md`.

## Files and Directories Created or Modified

- `src/osm_scenario/generation.py`, `tests/unit/test_generation.py`
- `web/src/controls.ts`, `src/osm_scenario/assets/review-client.js`
- `docs/policies/stage-2-finding-reference.md`,
  `docs/policies/stage-2-generation-v1.md`, `CLAUDE.md`
- `docs/mapping-algo-changes/2026-08-09-15:12:08-lane-transition-counts-measured-ways-not-movements.md`

## Commands and Tools

- `uv run osm-scenario generate-map -w workspaces/junction-1 --config config/default.yaml`
- `uv run osm-scenario inspect -w workspaces/junction-1 --view review`
- `uv run pytest`, `uv run ruff check`; in `web/`: `npx tsc --noEmit`, `npx vitest run`,
  `node build.mjs`.

## What Worked

- Measuring first. The claim "18 of 19 describe no lane-count change" came from counting
  real links per finding, not from reading the code and inferring what it must do. It
  also decided the trigger: `feeders > landed` was what the data showed was left once
  the false shapes were removed.
- Asserting `lanes` and `connectors` byte-identical before and after. A reporting change
  that moves a movement is a different change, and the diff is the only thing that
  proves it did not.

## What Went Wrong

- The plan predicted the surviving finding would be at node 474928793. It is at
  1927184814. The pre-change audit grouped links by each finding's own destination edge,
  so it could only see collapses an existing finding already named — and the v15 finding
  at 1927184814 pointed at a phantom pair, hiding the real one. Corrected in the
  change-log entry rather than quietly restated.

## Current State

- junction-1: 158 → 140 findings; `lane_transition_count_mismatch` 19 → 1; blockers
  unchanged at 57. The survivor is the `turn:lanes=right|right` collision already
  recorded in `CLAUDE.md` as open.
- `uv run pytest` 92 passed (4 new); `npx vitest run` 50 passed; `ruff` and `tsc` clean.

## Known Gaps

- The rule no longer covers a destination lane that no approach reaches. That is lane
  starvation, still open, and junction-1 has 12 such lanes with 11 undiagnosed.
- The version bump changes `generation_fingerprint`, so any Stage 3 draft is keyed
  differently; `findRecoverableDrafts` offers the old one and decisions on the other 139
  findings carry across.
