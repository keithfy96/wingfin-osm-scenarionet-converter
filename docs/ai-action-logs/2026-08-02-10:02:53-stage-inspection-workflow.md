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

## Follow-up: 2026-08-03 04:58:36 +08

### User Goal

Add source OSM node-ID search to the Stage 1B audit map without changing the
existing way, restriction, or audit-layer behavior.

### Actions Taken

- Added every node from the preserved `source/map.osm` snapshot to a dedicated
  search-only GeoJSON index.
- Changed the audit search form to select either Way or Node before entering an
  OSM ID.
- Added node-specific yellow point highlighting, zoom, popup, source-tag
  display, not-found feedback, report counts, and regression coverage.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`

### What Worked

- The mosque audit indexes 6,029 source nodes and 1,412 drawable source ways.
- Search indexes remain excluded from visible audit-layer counts.

### What Went Wrong

- The first test expected six fixture nodes, while the fixture contains eight;
  the expectation was corrected to match the complete source snapshot.

### Current State And Recommended Next Step

- The regenerated mosque audit supports explicit Way and Node searches from a
  single form. Review representative tagged and untagged nodes in the browser.

### Generated Code Details

- **What changed:** Source-node indexing, element-type selection, point search
  rendering, report metadata, and focused tests.
- **Why:** Operators need to locate exact OSM nodes referenced by restriction
  and topology evidence on the audit map.
- **How it works:** Python serializes all source nodes as search-only point
  features; browser-side maps select the requested element type and render an
  isolated temporary highlight with the standard source-evidence popup.
- **How validated:** The full suite passed 22 tests, Ruff and `git diff --check`
  passed, the mosque audit regenerated, and its embedded JavaScript parsed.

## Follow-up: 2026-08-03 04:24:56 +08

### User Goal

Keep complete turn-restriction member ways colorized while also showing the
specific source location where each restriction is applied.

### Actions Taken

- Added a default-visible restriction via-point layer to the Stage 1 audit map.
- Placed exact markers at node-based `via` members.
- Placed explicitly labeled representative midpoint markers for via-way members.
- Included relation ID, restriction value, via member type, and retention state
  in each marker popup.
- Regenerated the mosque audit artifact.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`
- `workspaces/mosque/inspection/stage-1-audit.html`
- `workspaces/mosque/reports/inspection-audit.json`
- `workspaces/mosque/reports/inspection-audit.md`

### What Worked

- The mosque audit now shows 46 high-contrast via markers for the 47 source
  restriction relations; missing source members are not assigned invented
  coordinates.
- Existing full-way restriction colors and styles remain unchanged.

### What Went Wrong

- The default uv cache was read-only, so validation was rerun with
  `UV_CACHE_DIR=/tmp/uv-cache`.

### Current State And Recommended Next Step

- Open `workspaces/mosque/inspection/stage-1-audit.html`, enable the desired
  restriction line layer, and click a yellow marker to inspect its relation.

### Generated Code Details

- **What changed:** Restriction via-point GeoJSON generation, Leaflet marker
  rendering, layer-control labeling, popup evidence, and regression assertions.
- **Why:** Coloring complete member ways identifies the affected roads but does
  not locate the source `via` member where the prohibited movement is defined.
- **How it works:** Node-via relations use the exact source node coordinate;
  way-via relations use a representative midpoint that is labeled as such.
- **How validated:** Focused tests passed 8 cases; the full suite passed 22 tests;
  Ruff and `git diff --check` passed; the mosque artifact was regenerated with
  46 visible restriction via markers.

## Follow-up: 2026-08-03 04:36:46 +08

### User Goal

Show turn restrictions only after selecting the specific OSM way to which they
refer, instead of displaying all restrictions across the audit map.

### Actions Taken

- Removed global restriction lines and via markers from the layer selector and
  initial map state.
- Added client-side filtering that finds restriction relations referencing the
  searched Way ID.
- Rendered only those relations' complete member ways and via markers in a
  temporary selection layer.
- Cleared the previous restriction selection before every search.
- Updated the search result with the number of referencing restrictions shown.
- Regenerated the mosque audit artifact.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`
- `workspaces/mosque/inspection/stage-1-audit.html`
- `workspaces/mosque/reports/inspection-audit.json`
- `workspaces/mosque/reports/inspection-audit.md`

### What Worked

- Restrictions are absent on initial load and appear only when the searched way
  is a member of one or more restriction relations.
- Existing full/partial line styles and via-point marker styling are preserved
  for the filtered relations.

### What Went Wrong

- The first patch context did not exactly match the compact embedded JavaScript;
  it made no changes and was reapplied against the exact source lines.

### Current State And Recommended Next Step

- Search for Way `756118317` in the regenerated mosque audit to see only the
  restrictions referencing that way; search another way to replace them.

### Generated Code Details

- **What changed:** Search-driven relation filtering, temporary restriction
  rendering, automatic cleanup, result messaging, and regression assertions.
- **Why:** A global restriction overlay obscured the relationship between one
  selected road and its applicable restriction relations.
- **How it works:** The browser derives matching relation IDs from restriction
  features whose member Way ID equals the search value, then filters all member
  and via features by those relation IDs before rendering.
- **How validated:** Embedded JavaScript parsed successfully; focused tests
  passed 8 cases; the full suite passed 22 tests; Ruff and `git diff --check`
  passed; the mosque audit artifact was regenerated.

## Follow-up: 2026-08-03 04:51:38 +08

### User Goal

Clearly distinguish disconnected road components from roads that cross
visually, and provide map legends and locations for reviewing both cases.

### Actions Taken

- Added procedural detection of selected source-way geometry intersections that
  do not share an OSM node.
- Classified crossings as tag-supported grade-separated or ambiguous using
  `layer`, `bridge`, and `tunnel` source evidence.
- Added blue and red crossing-marker layers with evidence popups.
- Added a panel legend listing each graph component color and node count plus
  crossing classifications and counts.
- Clarified that component separation does not prove a missing source join.
- Regenerated and visually checked the mosque audit map.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`
- `workspaces/mosque/inspection/stage-1-audit.html`
- `workspaces/mosque/reports/inspection-audit.json`
- `workspaces/mosque/reports/inspection-audit.md`

### What Worked

- The mosque workspace contains six crossings without shared source nodes; all
  six have bridge or layer evidence and are shown in blue.
- No ambiguous crossings were found, so the red review count is zero.
- The six disconnected components are listed with distinct colors and sizes.

### What Went Wrong

- No implementation errors occurred. The active shell continued to report its
  unrelated ScenarioNet virtual environment; uv correctly selected this
  project's environment.

### Current State And Recommended Next Step

- Use the component layer to inspect disconnected network groups and the
  crossing layers to inspect exact geometry intersections. Only red crossings
  should be treated as unresolved grade-separation reviews.

### Generated Code Details

- **What changed:** Spatial crossing analysis, evidence classification, GeoJSON
  layers, map markers, dynamic legend content, report layer counts, and tests.
- **Why:** Component connectivity and visual crossing checks are different, but
  the map previously visualized only components and could not locate crossings.
- **How it works:** An STRtree finds intersecting selected ways; pairs sharing a
  source node are excluded, and remaining intersections are classified from
  source separation tags without modifying OSM data.
- **How validated:** The real workspace produced six grade-separated and zero
  ambiguous crossings; a 1440x1000 headless Chrome screenshot confirmed the
  legend and controls render cleanly; focused and full tests passed 8 and 22
  cases; Ruff, embedded JavaScript parsing, and `git diff --check` passed.

## Follow-up: 2026-08-03 04:39:12 +08

### User Clarification And Revert

- The search-driven restriction filtering from the preceding follow-up
  misunderstood the request and was reverted.
- Way ID search is again independent of restriction visibility.
- Global fully retained, partial, and restriction via-point layers were
  restored to their prior behavior.
- The mosque audit artifact was regenerated.

### Current State

- Restriction member ways retain their global toggleable overlays.
- Restriction via nodes retain their yellow marker overlay.
- The next requested change concerns node highlighting when selecting a
  restriction, not selecting or searching a road way.

### Validation

- Focused inspection tests passed 8 cases.
- Ruff and `git diff --check` passed.
- Embedded JavaScript parsed successfully.

## Follow-up: 2026-08-03 04:41:24 +08

### User Goal

Keep restriction-way visualization unchanged, but show a restriction's via-node
highlight only after that specific restriction is selected.

### Actions Taken

- Kept the fully retained and partial restriction overlays and their existing
  styles unchanged.
- Removed the global restriction via-point toggle and initial marker display.
- Added a click handler to every restriction member-way feature.
- Added a temporary via-marker layer that is cleared and repopulated for the
  selected relation.
- Regenerated the mosque audit artifact.

### Files Modified

- `src/osm_scenario/inspection.py`
- `tests/unit/test_inspection.py`
- `docs/ai-action-logs/2026-08-02-10:02:53-stage-inspection-workflow.md`
- `workspaces/mosque/inspection/stage-1-audit.html`
- `workspaces/mosque/reports/inspection-audit.json`
- `workspaces/mosque/reports/inspection-audit.md`

### What Worked

- No via markers are drawn on initial load.
- Clicking any member way in a displayed restriction selects its relation and
  draws only the corresponding exact-node or representative via-way marker.
- Selecting another restriction replaces the previous marker.

### What Went Wrong

- No implementation errors occurred. The active shell still reported its
  unrelated ScenarioNet virtual environment; uv correctly used this project's
  environment.

### Current State And Recommended Next Step

- Open the regenerated audit, enable or use a visible restriction layer, and
  click a colored restriction way to reveal its yellow via marker.

### Generated Code Details

- **What changed:** Relation-select click handling, temporary via-marker
  rendering, layer-control behavior, and regression assertions.
- **Why:** Showing all via markers at once obscured which application point
  belonged to the restriction being inspected.
- **How it works:** A restriction feature's relation ID filters the embedded via
  GeoJSON; the selection layer is cleared before the matching marker is drawn.
- **How validated:** Embedded JavaScript parsed successfully; focused tests
  passed 8 cases; the full suite passed 22 tests; Ruff and `git diff --check`
  passed; the mosque audit artifact was regenerated.

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
