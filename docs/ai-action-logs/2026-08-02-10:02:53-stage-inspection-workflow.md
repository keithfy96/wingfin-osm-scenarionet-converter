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

## Follow-up: 2026-08-02 21:24:23 +08

### User Goal

Add a programmatically generated map that makes Stage 1B audit findings
locatable and covers both source-correction cases and checks that cannot exist
until Lanelet2 geometry is generated.

### Actions Taken

- Added the `inspect --view audit` checkpoint.
- Joined audit findings back to preserved OSM way, node, and relation geometry.
- Added separate layers for lane counts, widths, components, signal retention,
  restriction retention, stop-line candidates, and direction-tag conflicts.
- Added a visible readiness summary, inference state, highway-class counts, and
  a complete correction-coverage table.
- Marked semantic/manual checks and post-Stage-2 checks explicitly instead of
  representing them as automatically validated.
- Added JSON and Markdown inspection reports and focused regression tests.

### Files Modified

- `src/osm_scenario/inspection.py`
- `src/osm_scenario/cli.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`

### Commands That Materially Affected The Outcome

- `uv run osm-scenario inspect --workspace workspaces/mosque --view audit`
- `uv run ruff check src/osm_scenario/inspection.py src/osm_scenario/cli.py tests/unit/test_inspection.py`
- `uv run pytest -q`
- `git diff --check`

### What Worked

- The mosque audit HTML and its machine-readable reports were generated.
- All requested source evidence has a dedicated layer when source geometry exists.
- Every Lanelet-only case is visible in the coverage table as a later-stage check.

### What Went Wrong

- The combined validation command initially could not write a temporary uv cache
  lock because that cache was read-only in the sandbox. It succeeded after being
  rerun with the already-approved `uv run` escalation.

### Current State

- `workspaces/mosque/inspection/stage-1-audit.html` is ready for manual review.
- Stage 2 remains unimplemented; the audit does not claim to validate Lanelet2.

### Recommended Next Step

- Review the audit layers and source-correction table before approving Stage 2.

### Generated Code Details

- **What changed:** A dedicated Stage 1B audit renderer, source-to-audit geometry
  joins, CLI view, reports, and regression coverage.
- **Why:** Aggregate report counts were difficult to trace back to map locations,
  while several requested checks cannot truthfully be performed before Stage 2.
- **How it works:** The renderer reads `stage-1b-data-audit.json`, joins recorded
  OSM IDs to `source/map.osm`, builds issue-specific GeoJSON layers, and embeds
  those layers and audit metadata in a standalone Leaflet page.
- **How validated:** The focused inspection suite passed 8 tests; the full suite
  passed 22 tests; Ruff and `git diff --check` passed; the real mosque map was regenerated.

## Follow-up: 2026-08-03 02:37:06 +08

### User Goal

Remove Stage 2 and later correction items from the Stage 1B audit screen so it
shows only work that can be evaluated against the source OSM data.

### Actions Taken

- Removed Lanelet boundary shape, lane junction connector,
  signal-to-lanelet association, and inferred stop-line placement rows.
- Removed the corresponding `post_stage_2` list from the JSON inspection report.
- Kept source signal placement and mapped stop-line checks because those are
  Stage 1 source-data concerns.
- Updated regression coverage and regenerated the mosque audit page and reports.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`

### What Worked

- The audit screen and machine-readable report now contain Stage 1 work only.
- The distinction is pipeline-wide: removed items depend on generated Lanelet2
  objects and were not specific to the mosque source.

### What Went Wrong

- The first validation attempt encountered the read-only uv cache in the
  sandbox; the approved `uv run` escalation completed successfully.

### Current State And Recommended Next Step

- The regenerated mosque audit page is ready for Stage 1 source review without
  later-stage tasks occupying the panel.

### Generated Code Details

- **What changed:** Removed four post-Stage-2 UI rows, their JSON coverage field,
  unused styling, and replaced positive tests with absence assertions.
- **Why:** The Stage 1B audit should focus only on evidence and corrections that
  exist before Lanelet2 generation.
- **How it works:** The coverage table now contains only source OSM checks, and
  `inspection-audit.json` exposes only the `source_review` category.
- **How validated:** Focused tests passed 8 cases; the full suite passed 22 tests;
  Ruff and `git diff --check` passed; the real mosque audit was regenerated.

## Follow-up: 2026-08-03 03:36:34 +08

### User Goal

Add Way ID search to the audit map so reported OSM ways can be located without
manually scanning overlapping layers.

### Actions Taken

- Indexed every source OSM way with drawable geometry, including selected and
  excluded ways.
- Added a numeric Way ID search form to the Stage 1B audit panel.
- Added automatic zoom, a high-contrast black/yellow highlight, an opened source
  popup, and a concise selection-status result.
- Added distinct messages for unknown ways and restriction members referenced
  by relations but missing from `source/map.osm`.
- Kept the search index out of the visible layer counts and reported its size as
  separate machine-readable metadata.
- Added regression coverage and regenerated the mosque audit map.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`

### What Worked

- The mosque map indexes 1,412 source ways and can distinguish an available way
  such as `1250683198` from a missing restriction member such as `776369866`.
- Search highlights remain independent of layer visibility.

### What Went Wrong

- The first focused test exposed a Python invalid-escape warning from the
  embedded JavaScript numeric regex; escaping was corrected and tests rerun.
- The sandbox uv cache was read-only during combined validation; the approved
  `uv run` escalation completed successfully.

### Current State And Recommended Next Step

- Use the search box in `stage-1-audit.html` with any OSM Way ID referenced in
  the audit reports.

### Generated Code Details

- **What changed:** Source-way search index, accessible search UI, lookup and
  missing-member behavior, highlight rendering, report metadata, and tests.
- **Why:** Aggregate findings and overlapping audit layers did not provide a
  practical way to locate a known OSM way.
- **How it works:** Source ways are embedded as an unrendered GeoJSON lookup map;
  submission finds the exact ID, creates a temporary highlight layer, fits the
  viewport, and opens the existing evidence popup.
- **How validated:** Focused tests passed 8 cases; the full suite passed 22 tests;
  Ruff and `git diff --check` passed; the mosque artifact was regenerated with
  1,412 indexed source ways.

## Follow-up: 2026-08-03 02:50:13 +08

### User Goal

Make fully retained and partial turn restrictions visually distinguishable when
their geometry overlaps.

### Actions Taken

- Changed fully retained restrictions to solid purple with higher opacity.
- Changed partial restrictions to a thicker cyan dashed line at full opacity.
- Added color and stroke descriptions to both layer-control labels.
- Added exact style regression assertions and regenerated the mosque audit map.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`

### What Worked

- Restriction state is now encoded by both hue and stroke pattern instead of
  dash pattern alone.

### What Went Wrong

- The sandbox uv cache was read-only on the first validation attempt; the
  approved `uv run` escalation completed successfully.

### Current State And Recommended Next Step

- The regenerated mosque audit uses purple solid lines for fully retained
  restrictions and thicker cyan dashed lines for partial restrictions.

### Generated Code Details

- **What changed:** Restriction colors, weights, opacity, dash spacing, layer
  labels, and style assertions.
- **Why:** Identical colors made overlapping restriction states ambiguous.
- **How it works:** Leaflet renders the two GeoJSON collections with independent
  high-contrast styles, and the control names repeat the visual encoding.
- **How validated:** Focused tests passed 8 cases; the full suite passed 22 tests;
  Ruff and `git diff --check` passed; the real mosque audit was regenerated.
