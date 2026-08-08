# Stage 3 Review Application

- User goal: Carry out Stage 3 of the direct OSM-to-ScenarioNet plan.
- Run timestamp: 2026-08-08 18:32:20 +08 (Asia/Singapore)

## Actions Taken

- Added a build-time TypeScript/esbuild client under `web/`, compiled to a committed
  bundle so the installed CLI still needs no Node toolchain.
- Extracted `build_review_payload` out of `_render_review_html` in `generation.py` so
  the Stage 2 audit and the Stage 3 review draw from one payload builder rather than
  two drifting copies.
- Added `src/osm_scenario/review.py`: identity binding, road-class scoping for bulk
  actions, and the single-file HTML shell hosting the client.
- Added `inspect --view review`, routed ahead of the Stage 1 artifact checks because
  Stage 3 validates the generated lane model instead.
- Implemented the three planned milestones: overlays, per-finding decision state with
  structured controls and readiness rules, and draft persistence with checksum-bound
  import/export and stale-review migration.

## Files and Directories Created or Modified

- `web/` — `package.json`, `tsconfig.json`, `vitest.config.ts`, `build.mjs`,
  `src/{types,types-dom,state,persistence,submission,controls,overlays,panel,main}.ts`,
  `src/style.css`, `src/css.d.ts`, `test/{fixtures,state,submission}.ts`
- `src/osm_scenario/review.py`, `src/osm_scenario/assets/__init__.py`,
  `src/osm_scenario/assets/review-client.js`
- `src/osm_scenario/generation.py` — payload extraction only, no behaviour change
- `src/osm_scenario/inspection.py`, `src/osm_scenario/cli.py`
- `tests/unit/test_review.py`, `.gitignore`

## Commands and Tools

- `npm install`, `npx tsc --noEmit`, `node build.mjs`, `npx vitest run` in `web/`.
- `uv run pytest`, `uv run ruff check`, and
  `uv run osm-scenario inspect -w workspaces/junction-1 --view review`.

## What Worked

- Extracting the payload builder first meant the review view inherited the projection,
  the source-OSM evidence layers and the finding geometry links for free, and the 71
  pre-existing tests confirmed the extraction changed nothing.
- Keeping decision state, persistence and submission parsing DOM-free made the rules
  that actually matter — evidence-checksum binding, bulk scoping, readiness — testable
  without a browser.

## What Went Wrong

- The first pass scoped bulk actions from lanes and ways only, leaving 30 findings
  without a road class. Connector ids name neither, so connectors now inherit the
  class of the lane they leave. One `signal_lane_association` finding is still
  unscoped; it is a singleton and does not support bulk.
- `node_modules/` was not gitignored and had to be added.

## Current State

- `uv run pytest` 81 passed; `uv run ruff check` clean; `npx vitest run` 21 passed;
  `npx tsc --noEmit` clean.
- `inspect --view review` renders junction-1: 1131 features, 541 findings (138
  blockers), 281 lanes, 111 connectors.
- The Stage 3 checkbox in the implementation plan remains unchecked. See below.

## Generated Code Details

- Decision states are exactly the plan's three terminal values plus `unresolved`;
  a connector rejection is an override carrying `{accepted: false}` rather than a
  fourth state, so readiness has one rule to enforce.
- Every decision stores the `evidence_checksum` of the finding it was made against.
  Loading a prior review restores a decision only when the finding still exists and
  that checksum is unchanged; everything else returns to `unresolved` and is counted
  in a migration summary shown before export.
- Drafts are keyed on workspace, source checksum and generation fingerprint together,
  so a draft from a different generation is never offered against this one.
- Bulk actions are offered only when the visible set is already one rule and one road
  class, and still write one decision record per finding.
- `build_review_payload` is shared with the Stage 2 audit; changing it changes both.

## Known Gaps Before Stage 3 Can Be Marked Complete

- Stage 1 review affordances are not carried over: the dedicated OSM Way/Node search
  box, ambiguous-crossing, missing-tag and direction-warning layers.
- No browser smoke test over the generated HTML; coverage is unit-level only.
- Signal-to-lane association has a control but no dedicated map overlay.
- `apply-review` (Stage 4) does not exist, so an exported `review.json` has no
  consumer and the export path is unverified end to end.

## Recommended Next Step

- Close the gaps above, or build Stage 4 first so an exported review can be proven to
  round-trip before more review surface is added.
