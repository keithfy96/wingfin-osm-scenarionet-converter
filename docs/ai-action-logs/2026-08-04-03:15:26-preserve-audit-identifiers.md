# Preserve Stage 3A Audit Identifiers

## User Goal

Prevent JavaScript from rounding large Lanelet2 identifiers in the Stage 3A audit map so displayed and searched IDs remain exact.

## Run Timestamp

2026-08-04 03:15:26 +08 (Asia/Singapore)

## Actions Taken

- Converted identifier-valued GeoJSON properties to strings before HTML JSON serialization.
- Covered singular `_id` fields and list-valued `_ids` fields.
- Added regression assertions for lanelet, from/to lanelet, and line-string IDs.
- Regenerated the mosque Stage 3A audit.

## Files And Directories Modified

- `src/osm_scenario/lanelet_inspection.py`
- `tests/unit/test_inspection.py`
- `workspaces/mosque/inspection/stage-3a-preliminary-audit.html` (generated)
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.json` (generated)
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.md` (generated)

## Commands And Tools Run

- Focused and full pytest suites through `uv run pytest`.
- Ruff through `uv run ruff check`.
- `git diff --check`.
- Stage 3A audit regeneration through `uv run osm-scenario inspect`.
- Exact payload checks for the three identifiers reported by the user.

## Generated Code Details

### What Was Created Or Changed

Added identifier normalization to the Stage 3A GeoJSON property construction and tests that require identifiers to be serialized as JSON strings.

### Why It Was Created Or Changed

Lanelet2 uses integer identifiers larger than JavaScript's exact integer range. Serializing them as JSON numbers caused the browser to round displayed values and broke exact identifier search.

### How It Works

Properties whose names end in `_id` are converted to strings. List properties ending in `_ids` have every element converted to a string. Numeric measurements and counts remain numeric.

### How It Was Validated

- Focused inspection suite: 13 tests passed.
- Full suite: 32 tests passed.
- Ruff and `git diff --check` passed.
- Exact incoming, connector, and outgoing IDs from the reported example occur only as quoted values in the regenerated HTML payload.

## What Worked

The regenerated audit preserves `7329135659818866350`, `3255923044616467153`, and `8551600778732414498` exactly.

## What Went Wrong

No implementation or validation failures occurred.

## Current State

Large Lanelet2 and source identifiers are safe from JavaScript numeric rounding in the Stage 3A audit.

## Recommended Next Step

Use the exact ID shown in a popup with the Lanelet ID search to locate the same feature reliably.
