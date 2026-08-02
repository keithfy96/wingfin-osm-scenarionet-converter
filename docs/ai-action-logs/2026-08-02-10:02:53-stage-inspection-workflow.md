# Stage Inspection Workflow

## User Goal

Provide visual checkpoints after each conversion stage so malformed maps can be traced to their originating transformation.

## Run Timestamp

2026-08-02 10:02:53 Asia/Singapore (UTC+08:00)

## Actions Taken

- Added a versioned public-driving source selector and exhaustive source-to-graph audit.
- Replaced the reserved `inspect` command with Stage 1 source, normalized, and combined browser views.
- Added source/projection parity reporting, visual layers, CLI errors for unavailable Lanelet2 output, tests, and operator documentation.
- Regenerated and inspected the mosque Stage 1 workspace.

## Files And Directories Created Or Modified

- Created `src/osm_scenario/osm_source.py` and `src/osm_scenario/inspection.py`.
- Modified `src/osm_scenario/acquisition.py`, `src/osm_scenario/normalization.py`, and `src/osm_scenario/cli.py`.
- Created `tests/unit/test_inspection.py` and extended the OSM fixture and normalization coverage.
- Updated the root README and implementation plan; created `docs/implementation-plan/002-stage-inspection-and-fault-isolation.md`.

## Commands Or Tools Run

- Inspected the CLI, Stage 1 artifacts, tests, project dependencies, and implementation plan.
- Ran the real mosque `fetch` and all three Stage 1 inspection views.
- Rendered desktop and mobile screenshots with headless Chrome and inspected both images.
- Ran `uv run pytest -q`, `uv run ruff check .`, `uv sync --locked`, and `git diff --check`.

## What Worked

- Confirmed that both Stage 1 GeoPackages contain reloadable `nodes` and `edges` layers.
- The mosque source audit passed with 227 selected public-road ways, 323 excluded highway ways, 764 nodes, and 1,117 directed edges.
- Stage 1A-to-1B topology and source-tag parity passed with no differences; projection round-trip error was `1.421e-14` degrees.
- The combined HTML rendered correctly at desktop and mobile sizes with selectable layers and an OSM basemap.
- The complete test suite passed: 18 tests. Ruff, the locked environment check, and patch whitespace check also passed.

## What Went Wrong

- The existing `inspect` command is a Stage 3 placeholder and provides no visual artifact.
- An initial test assumed OSMnx would reload the JSON tag payload as a string; OSMnx deserialized it to a dictionary. The test now accepts the supported reloaded representation.
- The first desktop render placed the information panel beneath Leaflet's zoom control. The panel was repositioned and mobile behavior was added before final verification.

## Current State

- Stage 1 source selection, source parity, projection parity, and visual inspection are implemented and verified.
- `--view lanelet2` exits clearly because Stage 2 and its geometry inspector are not implemented yet.

## Recommended Next Step

- Have the operator review `workspaces/mosque/inspection/stage-1.html`, then address accepted Stage 1 warnings before implementing Stage 2 lane generation.

## Generated Code Details

### What Was Created Or Changed

- Added the `public-driving-v1` source selector, exact source-tag attachment, source/topology/direction audit, Stage 1A-to-1B parity check, HTML inspector, JSON/Markdown inspection reports, CLI views, fixtures, and tests.

### Why It Was Created Or Changed

- The operator needs to see the first representation where a map defect appears instead of discovering it after ScenarioNet or MetaDrive conversion.

### How It Works

- Parses the preserved XML, applies a deterministic public-road policy, attaches exact tag dictionaries, and audits IDs, coordinates, adjacency, and direction.
- Generates Leaflet HTML with independent selected, excluded, warning, projected, direction, and signal layers; each feature exposes its source OSM evidence.
- Reprojects Stage 1B geometry back to WGS84 only for display and compares non-geometric graph data after GraphML serialization.

### How It Was Validated

- `uv run pytest -q`: 18 passed.
- `uv run ruff check .`: passed.
- `uv sync --locked`: passed without lock-file changes.
- `git diff --check`: passed.
- Real mosque generation and source, normalized, and combined inspection commands passed.
- Headless Chrome desktop and mobile screenshots were visually checked for nonblank content, framing, layer controls, and overlap.

## Follow-up: 2026-08-02 21:13:41 +08

### User Goal

Make the standalone source inspection show only source evidence, make the
standalone normalized inspection show only the Stage 1B projected overlay, and
leave the combined inspection unchanged.

### Actions Taken

- Made the legend and Leaflet layer-control keys conditional on the requested view.
- Removed source, warning, direction, and signal feature data from the normalized view.
- Kept projected data out of the source view and kept every layer available in the combined view.
- Added focused regression coverage and regenerated all three mosque inspection pages.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`

### What Worked

- Source HTML now contains source-layer keys without a Stage 1B projection key.
- Normalized HTML and its report contain only the Stage 1B projected layer.
- Combined HTML retains the source and projected layers.

### What Went Wrong

- The first regression assertions found that hidden layer labels still existed in
  the generated JavaScript. Layer definitions were then generated per view so
  irrelevant keys are absent from the HTML rather than merely hidden.

### Current State And Recommended Next Step

- The standalone views now have unambiguous representation boundaries. Manually
  inspect the regenerated source, normalized, and combined mosque HTML pages.

### Generated Code Details

- **What changed:** View-specific feature payloads, legends, layer controls, map
  bounds, and regression tests.
- **Why:** A normalized-only defect must be attributable to Stage 1B without source
  layers obscuring the comparison.
- **How it works:** `visible_layers` controls which keys are emitted, while
  `enabled_layers` controls which emitted layers start enabled. The normalized
  payload is reduced to projected geometry only.
- **Validation:** `uv run pytest -q` passed 20 tests; focused inspection tests passed
  6 tests; Ruff and `git diff --check` passed; all mosque inspection views regenerated.
