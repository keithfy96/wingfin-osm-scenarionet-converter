# Stage 3A Preliminary Inspection

## User Goal

Implement Stage 3A so the generated `lanelet2/preliminary.osm` can be inspected without overwriting later inspection checkpoints.

## Run Timestamp

2026-08-04 01:47:57 +08 (Asia/Singapore)

## Actions Taken

- Added a dedicated Stage 3A Lanelet2 parser and Leaflet audit renderer.
- Added the `--checkpoint preliminary` CLI contract for the `lanelet2` view.
- Added checksum binding against the Stage 2 manifest and parser-error handling.
- Added search by generated lanelet ID, source OSM way ID, and source OSM node ID.
- Added isolated HTML, JSON, and Markdown outputs and regression coverage.
- Generated and visually checked the mosque workspace inspection.

## Files And Directories Created Or Modified

- `src/osm_scenario/lanelet_inspection.py`
- `src/osm_scenario/inspection.py`
- `src/osm_scenario/cli.py`
- `tests/unit/test_inspection.py`
- `docs/implementation-plan/README.md`
- `workspaces/mosque/inspection/stage-3a-preliminary-audit.html`
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.json`
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.md`

## Commands And Tools Run

- `uv run osm-scenario inspect --workspace workspaces/mosque --view lanelet2 --checkpoint preliminary`
- `uv run pytest -q`
- `uv run ruff check .`
- `git diff --check`
- Headless Chrome screenshot at 1440 by 900 pixels

## Generated Code Details

### What Was Created Or Changed

A Stage 3A renderer that loads the preliminary map with Lanelet2, converts local coordinates back to WGS84 for browser display, renders distinct geometry layers, indexes traceability fields for search, and writes checksum-bound reports.

### Why It Was Created Or Changed

The preliminary Lanelet2 output needs a programmatic visual checkpoint before manual JOSM correction, with reproducible artifacts that cannot overwrite Stage 3C inspection files.

### How It Works

The renderer verifies the preliminary checksum against the Stage 2 manifest, reloads the map with the Stage 2 coordinate origin, constructs GeoJSON layers from Lanelet2 primitives, joins Stage 2 lane and correction records by generated lanelet ID, and writes only Stage 3A-specific filenames.

### How It Was Validated

- Full test suite: 30 passed.
- Ruff: all checks passed.
- `git diff --check`: passed.
- Real mosque workspace generated successfully.
- Headless browser screenshot confirmed a nonblank, correctly framed map with readable controls and layers.

## What Worked

- The real preliminary map rendered 1,861 road lanelets and 2,362 connectors.
- Checksum mismatch protection and repeat execution behavior passed tests.
- Stage 3C sentinel artifacts remained unchanged during repeat Stage 3A runs.

## What Went Wrong

- Initial lint found long embedded HTML and JavaScript lines; the renderer module now explicitly follows the existing inspection module's E501 exemption.
- One older missing-Stage-2 test expected the pre-checkpoint error wording and was updated for the explicit Stage 3A checkpoint contract.

## Current State

Stage 3A is implemented and its mosque artifacts are available. Stage 3B manual correction and Stage 3C edited/comparison inspection remain pending.

## Recommended Next Step

Open the Stage 3A HTML alongside `lanelet2/preliminary.osm` in JOSM and review the high-priority correction overlay before saving manual changes to `lanelet2/edited.osm`.
