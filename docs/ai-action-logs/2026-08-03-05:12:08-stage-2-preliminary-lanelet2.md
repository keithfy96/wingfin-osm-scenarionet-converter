# Stage 2 Preliminary Lanelet2

## User Goal

Start Stage 2 and generate a preliminary Lanelet2 map from the reviewed Stage 1
artifacts.

## Run Timestamp

2026-08-03 05:12:08 +08:00 (Asia/Singapore)

## Actions Taken

- Implemented offline preliminary lane, boundary, connector, restriction,
  traffic-light, and stop-line generation.
- Wired the `generate-lanelet2` CLI command to versioned configuration.
- Added JSON and Markdown generation reports plus Stage 2 manifest artifacts.
- Added fixture coverage and generated the mosque workspace output.
- Updated the Stage 2 checklist without marking the remaining via-way and
  explicit junction-case work complete.

## Files And Directories Created Or Modified

- `src/osm_scenario/lanelet_generation.py`
- `src/osm_scenario/cli.py`
- `tests/unit/test_lanelet_generation.py`
- `README.md`
- `docs/implementation-plan/README.md`
- `docs/ai-action-logs/2026-08-03-05:12:08-stage-2-preliminary-lanelet2.md`
- Generated ignored artifacts under `workspaces/mosque/lanelet2/` and
  `workspaces/mosque/reports/`.

## Commands Or Tools Run

- `uv run osm-scenario generate-lanelet2 --workspace workspaces/mosque`
- `uv run pytest -q`
- `uv run ruff check .`
- `uv run ruff format ...`

## Generated Code Details

### What Was Created Or Changed

A Stage 2 generator now converts the checked local GraphML and preserved OSM XML
into Lanelet2 primitives, writes `preliminary.osm`, reloads it through the parser,
and records provenance and review items.

### Why It Was Created Or Changed

Stage 2 previously existed only as a CLI placeholder. The project needed a
reviewable preliminary Lanelet2 artifact without depending on ScenarioNet or
MetaDrive.

### How It Works

Directed graph edges are matched to source OSM ways. Explicit lane/direction,
turn, width, speed, node-via restriction, signal, and mapped stop-line evidence
is used before configured defaults. Shapely offsets produce centerlines and
boundaries. Lanelet2 primitives and its projector/writer produce geographic OSM
XML with stable positive IDs. Uncertain results enter a ranked correction queue.

### How It Was Validated

The fixture output and mosque output reload through `lanelet2.io.loadRobust`
without parser errors. Tests cover source preservation, traceability, stable IDs,
repeat execution, signal association, inferred stop lines, and mapped stop-line
reuse.

## What Worked

- The mosque workspace produced a reloadable preliminary map.
- Source checksums remained unchanged.
- Repeat generation recreates outputs without ID drift or workspace errors.

## What Went Wrong

- GEOS returned an empty offset for a nearly collinear fixture connector. A
  deterministic straight-normal offset fallback was added and tested.
- The first implementation assumed a signal-ID summary field that the Stage 1B
  report does not contain. It now derives retained IDs from the report details.

## Current State

Stage 2 has a usable preliminary generator and passes its parser/traceability
gate, but remains in progress. Via-way restrictions and explicit geometry
policies/tests for each junction family are still unchecked. The mosque result is
`review_required`, as expected for inferred and ambiguous geometry.

## Recommended Next Step

Implement the Lanelet2 visual inspection view, then review the mosque correction
queue and geometry before completing the remaining Stage 2 connector cases.
