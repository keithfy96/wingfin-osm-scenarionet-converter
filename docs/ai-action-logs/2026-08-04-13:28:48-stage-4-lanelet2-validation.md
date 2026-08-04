# Stage 4 Lanelet2 Validation

## User Goal

Carry out Stage 4 after creating `workspaces/mosque/lanelet2/edited.osm`.

## Run Timestamp

2026-08-04 13:28:48 +08 (Asia/Singapore)

## Actions Taken

- Verified and checksumed the edited map.
- Implemented the Stage 4 CLI validation command.
- Added checksum-bound JSON and Markdown validation reports.
- Added parser, geometry, lane-width, boundary-orientation, routing, waiver, and
  native-validator gates.
- Ran the validator against the mosque edited map.

## Files And Directories Created Or Modified

- `src/osm_scenario/lanelet_validation.py`
- `src/osm_scenario/cli.py`
- `tests/unit/test_lanelet_validation.py`
- `workspaces/mosque/reports/lanelet2-validation.json` (generated)
- `workspaces/mosque/reports/lanelet2-validation.md` (generated)

## Generated Code Details

### What Was Created Or Changed

Implemented `validate-lanelet2` as a real Stage 4 command and added focused unit
tests.

### Why It Was Created Or Changed

The command was a placeholder and could not validate the user's edited map.

### How It Works

The validator selects only `edited.osm`, loads it using the Stage 1B recorded
origin, checks Lanelet2 parsing and geometry, builds a vehicle routing graph,
matches warnings to explicit review waivers, and writes reports tied to the
input SHA-256. Errors and unwaived warnings keep the gate closed.

### How It Was Validated

Focused tests passed. The full suite passed with 44 tests, Ruff passed, and
`git diff --check` produced no output. The final mosque run generated both
reports and correctly exited nonzero because the validation gate remains open.

## Commands Or Tools Run

- `uv run pytest -q tests/unit/test_lanelet_validation.py`
- `uv run ruff check ...`
- `uv run osm-scenario validate-lanelet2 --workspace workspaces/mosque`

## What Worked

- The edited map loaded with zero parser errors.
- Reports were generated against SHA-256
  `3f27be12e2a5e30f0bb1af207c1cbd6295fc3a7274b1ad40175ee260b95ab79e`.

## What Went Wrong

- The first fixture setup omitted Stage 1A acquisition artifacts; the test now
  uses the real acquisition workflow.
- The mosque map has no routing relationships, 63 width warnings, and no signed
  Stage 3B waiver record.
- `lanelet2_validate` is not installed locally, so the required native check
  remains blocking.

## Current State

Implementation is complete. Stage 4 reports are generated, but the mosque map
does not yet pass its validation gate: 2 blocking errors and 64 unwaived
warnings. The errors are zero routable components and the unavailable required
native validator. The warnings are 63 width findings and the absent Stage 3B
review record.

## Continuation — 2026-08-04 13:31 +08

- Tightened route validation so an isolated lanelet is not counted as a valid
  route.
- Made the unavailable pinned native validator a blocking error.
- Final validation: 44 tests passed, Ruff passed, and diff check passed.

## Recommended Next Step

Correct the missing Lanelet2 routing connectivity, review or correct the width
findings, add signed explicit waivers where justified, and run the pinned native
Lanelet2 1.2.2 validator.
