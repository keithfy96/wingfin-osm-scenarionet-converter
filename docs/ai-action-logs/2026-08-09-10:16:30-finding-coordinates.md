# Finding Coordinates

- User goal: index the review questions by coordinate so they can be lined up against
  drive footage carrying a GPS track, and score a VLM's answers against Keith's own.
- Run timestamp: 2026-08-09 10:16:30 +08 (Asia/Singapore)

## Actions Taken

- `ReviewFinding` gained `location`: a representative WGS84 point, a bbox, and the
  full node coordinates of every OSM way or node the finding came from, copied in.
- New models in `lane_model.py`: `GeoPoint`, `FindingSource`, `FindingLocation`.
- `generation.py` gained `_source_refs` (extracted from the closure inside
  `build_review_payload`, so the payload and the location agree on what a finding
  refers to) and `_finding_location`.
- `LANE_MODEL_SCHEMA_VERSION` 2 to 3; `GENERATOR_VERSION` v13 to v14.
- Exported decisions carry the same `location`, `source_type` and `source_ids`;
  `submission_version` is now 2 and `parseSubmission` accepts 1 as well.
- The detail pane shows the finding's lat/lon.

## Files and Directories Created or Modified

- `src/osm_scenario/lane_model.py`, `src/osm_scenario/generation.py`
- `web/src/types.ts`, `state.ts`, `submission.ts`, `panel.ts`;
  `src/osm_scenario/assets/review-client.js`
- `tests/unit/test_generation.py`, `web/test/{fixtures,state,submission}.ts`

## Commands and Tools

- `uv run pytest`, `uv run ruff check`,
  `uv run osm-scenario generate-map -w workspaces/junction-1 --config config/default.yaml`,
  `uv run osm-scenario inspect -w workspaces/junction-1 --view review`.
- In `web/`: `npx tsc --noEmit`, `npx vitest run`, `node build.mjs`.

## What Worked

- Assigning `location` after `_finding()` computes `evidence_checksum`. Verified
  against a snapshot of the v13 model: all 541 finding ids and all 541 evidence
  checksums are byte-identical, so a review exported before this change imports
  against the new model with every decision intact.
- Coordinates are a lookup, not a projection: OSM node positions are already WGS84.
  Every emitted coordinate was checked against the corresponding `<nd>` / `<node>`
  in `source/map.osm` — 4,932 points, all exact.

## What Went Wrong

- **The size estimate was wrong by 3.6x.** Predicted ~190 KB of growth; actual is
  +687 KB (1011 KB to 1699 KB). The estimate assumed compact pairs; the artifact is
  written with `indent=2, sort_keys=True` like every other artifact here, so a single
  `{"lat": ..., "lon": ...}` costs about 140 bytes rather than 40. The convention was
  kept rather than special-casing this file, but the number should have been measured
  before it was quoted.
- An existing test asserted a decision's exact shape and another used
  `submission_version: 2` as its example of an unsupported future version. Both were
  updated deliberately — the first still compares the whole object so a field added
  to a decision has to be an explicit change.

## Current State

- `uv run pytest` 86 passed (2 new); `uv run ruff check` clean; `npx vitest run`
  35 passed (2 new); `npx tsc --noEmit` clean.
- junction-1: 541 of 541 findings carry a location. 489 are way-sourced and 52
  node-sourced; none are `edge`-sourced, which is the case that yields `None`.
- The representative point is the middle node of the longest source polyline, so it
  always lies on real geometry; the bbox states the true extent of a way-scoped
  finding rather than letting a point imply a precision it does not have.

## Known Gaps

- **The question wording is still not in the data.** The sentence a reviewer reads
  lives in `web/src/controls.ts`; exporting it means moving those specs to a JSON
  shared by the GUI and Python. Deliberately out of scope, and the harness must write
  its own prompts from `rule` and `reason` until then.
- **The answers file has no home in the workspace.** `review.json` is still a browser
  download; `review/` and `apply-review` remain Stage 4 work.
- Way-sourced findings repeat their way's nodes 26x across the findings that share
  the way. That was Keith's explicit choice, for self-contained records.

## Not Written

- No `docs/mapping-algo-changes/` entry: no movement, connector or lane mapping
  changed. The same 541 findings with the same ids, annotated with where they are.
