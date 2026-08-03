# Stage 3A Review Overlays

## User Goal

Show every correction that requires manual review on the Stage 3A audit map, and display the reason when the affected object is clicked. Do not treat inferred lane counts or inferred widths as visual review items.

## Run Timestamp

2026-08-04 03:07:25 +08 (Asia/Singapore)

## Actions Taken

- Added separate visual overlays for ambiguous connectors, ambiguous lane counts, inferred stop lines, traffic-signal association reviews, and via-way restriction reviews.
- Attached review records to generated lanelets using generated lanelet IDs, exact source edges, or source relation member ways.
- Added review reasons, confidence, priority, relation IDs, and relation member roles to feature popups.
- Added source relation ID search.
- Excluded medium-confidence inferred lane-count and lane-width records from visual review overlays while retaining them in the Stage 2 generation report.
- Regenerated and visually inspected the mosque Stage 3A audit.

## Files And Directories Modified

- `src/osm_scenario/lanelet_inspection.py`
- `tests/unit/test_inspection.py`
- `workspaces/mosque/inspection/stage-3a-preliminary-audit.html` (generated)
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.json` (generated)
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.md` (generated)

## Commands And Tools Run

- Focused and full pytest suites through `uv run pytest`.
- Ruff checks through `uv run ruff check`.
- Stage 3A regeneration through `uv run osm-scenario inspect`.
- Headless Chrome screenshot at 1440 by 900 and visual image inspection.
- `git diff --check`.

## Generated Code Details

### What Was Created Or Changed

Stage 3A inspection now builds five category-specific review layers, enriches affected lanelet properties with review explanations, and indexes restriction relations for search.

### Why It Was Created Or Changed

The previous single correction overlay did not expose all high-priority review categories or explain each correction clearly at the affected geometry.

### How It Works

Review records are filtered to the five high-priority codes. Direct records map by generated lanelet ID, lane-count ambiguity maps by exact normalized edge, and via-way restrictions map by reading the original OSM relation and locating its member ways. The HTML renders each category independently and includes the attached review details in popups.

### How It Was Validated

- Full suite: 32 tests passed.
- Ruff: all checks passed.
- Mosque audit: 1,001 review items mapped and zero unmapped.
- Screenshot confirmed a nonblank, correctly framed audit with usable sidebar and layer control.

## What Worked

All five selected review categories mapped to visible generated geometry. Relation searches and popup explanations are present, and Stage 3A artifact isolation remains covered by tests.

## What Went Wrong

The first focused test retained the obsolete `Correction queue lanelets` label and failed after the layer redesign. It was updated to assert the new category-specific review interface. Ruff also identified an import grouping issue, which was corrected.

## Current State

The mosque Stage 3A audit shows 952 ambiguous connectors, 4 ambiguous lane-count records, 19 inferred stop lines, 19 traffic-signal association reviews, and 7 via-way restriction reviews. All 1,001 review records map to at least one feature.

## Recommended Next Step

Use the category layers and identifier searches to review high-priority geometry before creating `lanelet2/edited.osm` in Stage 3B.
