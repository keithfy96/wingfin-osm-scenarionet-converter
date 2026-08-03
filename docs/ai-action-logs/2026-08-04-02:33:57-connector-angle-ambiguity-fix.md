# Connector Angle Ambiguity Fix

## User Goal

Correct the junction-connector borderline-angle check and regenerate the preliminary Lanelet2 map and Stage 3A audit.

## Run Timestamp

2026-08-04 02:33:57 +08 (Asia/Singapore)

## Actions Taken

- Replaced the float-versus-`range` comparison with an inclusive numeric interval.
- Extracted the ambiguity decision into a directly tested helper.
- Added fractional positive and negative boundary-angle regression cases.
- Regenerated the mosque Stage 2 preliminary map and Stage 3A inspection.
- Compared the old and new connector warning totals.

## Files And Directories Created Or Modified

- `src/osm_scenario/lanelet_generation.py`
- `tests/unit/test_lanelet_generation.py`
- `workspaces/mosque/lanelet2/preliminary.osm`
- `workspaces/mosque/reports/lanelet2-generation.json`
- `workspaces/mosque/reports/lanelet2-generation.md`
- `workspaces/mosque/inspection/stage-3a-preliminary-audit.html`
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.json`
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.md`

## Commands And Tools Run

- `uv run osm-scenario generate-lanelet2 --workspace workspaces/mosque`
- `uv run osm-scenario inspect --workspace workspaces/mosque --view lanelet2 --checkpoint preliminary`
- `uv run pytest -q`
- `uv run ruff check .`
- `git diff --check`

## Generated Code Details

### What Was Created Or Changed

Connector ambiguity now treats every floating-point absolute turn angle from 30 through 40 degrees as borderline, rather than matching only exact integer values contained in a Python `range`.

### Why It Was Created Or Changed

The old expression did not implement the documented numeric interval and missed fractional turn angles.

### How It Works

`_connector_is_ambiguous()` returns true when an incoming lane has multiple permitted outgoing candidates or when `30 <= abs(angle) <= 40`.

### How It Was Validated

- Full test suite: 31 passed.
- Ruff: all checks passed.
- `git diff --check`: passed.
- Mosque regeneration and Stage 3A inspection completed successfully.
- Ambiguous connector count changed from 941 to 952; 44 connectors occupy the intended angle interval, with 33 already flagged by the multiple-candidate rule.

## What Worked

Fractional positive and negative angles are now classified consistently at both interval boundaries.

## What Went Wrong

Nothing failed during implementation. The corrected rule increases warnings because it detects previously missed cases; it does not resolve missing lane-to-lane connectivity evidence.

## Current State

The angle-classification defect is fixed and all mosque Stage 2/3A artifacts have been recreated with the new logic.

## Recommended Next Step

Reduce manual review volume by designing a separate, evidence-based lane-to-lane connector inference policy. Do not suppress multiple-candidate warnings without a deterministic rule that preserves legal movements.
