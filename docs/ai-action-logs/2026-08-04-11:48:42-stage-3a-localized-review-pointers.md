# Stage 3A Localized Review Pointers

## User Goal

Keep the colored Stage 3A review lanes visible and add searchable circle pointers at localized issue geometry, including an explicitly approximate fallback for incomplete via-way restrictions.

## Run Timestamp

2026-08-04T11:48:42+08:00 (Asia/Singapore)

## Actions Taken

- Added localized pointer features for ambiguous connectors, inferred stop lines, traffic-signal associations, and via-way restrictions.
- Kept review lines visible by default and grouped each category's lines and pointers under one layer-control entry.
- Added a high-z-order pointer pane, contrasting marker borders, pointer popups, and identifier indexing.
- Preserved line-only ambiguous lane-count behavior.
- Added exact and approximate pointer tests, regenerated the mosque Stage 3A audit, and updated its legend guide.

## Files And Directories Created Or Modified

- `src/osm_scenario/lanelet_inspection.py`
- `tests/unit/test_inspection.py`
- `guide/legend/3a-junction-connectors.md`
- `workspaces/mosque/inspection/stage-3a-preliminary-audit.html` (generated)
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.json` (generated)
- `workspaces/mosque/reports/inspection-stage-3a-preliminary.md` (generated)
- `docs/ai-action-logs/2026-08-04-11:48:42-stage-3a-localized-review-pointers.md`

## Commands And Tools Run

- Focused and full pytest suites through `uv run pytest`.
- Ruff through `uv run ruff check .`.
- Mosque regeneration through `uv run osm-scenario inspect --workspace workspaces/mosque --view lanelet2 --checkpoint preliminary`.
- Generated-payload checks for category counts, accuracy labels, layer visibility, z-order, marker border, and search indexing.

## What Worked

- All 42 tests pass and Ruff reports no violations.
- The mosque audit contains 942 exact ambiguous-connector pointers, 19 exact inferred-stop-line pointers, 19 exact traffic-signal pointers, and one approximate via-way pointer for relation `15336555`.
- The incomplete relation popup identifies available from way `756118317` as the approximate fallback because its via geometry is unavailable.

## What Went Wrong

- The first focused run exposed that Lanelet2 `AttributeMap` does not implement `.get()`. Converting it to a dictionary fixed the three affected tests.

## Current State

The generated Stage 3A audit shows full affected geometry and localized pointers together. Searches highlight matching lane geometry and pointers. Lane-count ambiguity remains unchanged.

## Recommended Next Step

Open the regenerated mosque HTML and spot-check pointer popups against `lanelet2/preliminary.osm` before beginning Stage 3B edits.

## Generated Code Details

- **What was created or changed:** Point-feature generation, length-weighted line midpoint placement, control-geometry lookup through Lanelet2 regulatory elements, via-relation fallback placement, Leaflet circle rendering, and pointer-aware search indexes.
- **Why it was created or changed:** Full-lane overlays identify affected geometry but do not localize where review should begin.
- **How it works:** Each review item derives a point from its connector, generated control geometry, or OSM restriction members. The HTML renders that point above its category line and exposes accuracy and traceability properties in the popup.
- **How it was validated:** Focused tests, the 42-test full suite, Ruff, mosque regeneration, and direct inspection of the generated JSON payload embedded in the HTML.

## Continuation Note — 2026-08-04T11:54:20+08:00

The initial pointer rendering made the wide review lines intercept clicks and
treated circles as independent popup features. Changed review lines to be
click-through, restored direct lane-click highlighting, and made circle clicks
highlight associated base lanes and open the lane-data popup. Every generated
mosque pointer now has at least one target Lanelet ID; the incomplete via-way
pointer targets nine affected lanes. Re-ran all 42 tests, Ruff, mosque audit
regeneration, and `git diff --check`; all passed.

## Continuation Note — 2026-08-04T12:22:00+08:00

The custom high-z-order pointer pane used Leaflet's Canvas renderer, whose
full-map canvas intercepted events before the lower lane canvas. Changed pointer
markers to an SVG renderer so only circle paths capture clicks, separated review
lines and pointers in the layer control, and left pointers disabled by default.
Colored review lines remain visible and click-through. Updated the guide and
regression assertions. All 42 tests and Ruff passed, the mosque inspection was
regenerated, and `git diff --check` passed. A headless-Chrome smoke test clicked
a road lane through the rendered map with pointers both disabled and enabled;
both clicks produced one yellow selection and opened the lane popup. The pointer
pane rendered as SVG and pointers were confirmed disabled by default.

## Continuation Note — 2026-08-04T12:31:00+08:00

Adjusted the layer-control behavior after user clarification: each review
category is again a single entry containing both its colored review lines and
circle pointers, but the combined category is disabled initially. Selecting
**Ambiguous connectors**, for example, now shows both its lines and points.
The SVG pointer renderer remains in place to preserve click-through to lanes.
All 42 tests, Ruff, mosque regeneration, and `git diff --check` passed. A
headless-Chrome check confirmed the ambiguous-connector group starts hidden and
that enabling its single control entry adds both the line and pointer layers;
selecting the displayed geometry still produced a yellow lane selection and
opened its popup.

## Continuation Note — 2026-08-04T12:42:00+08:00

Made displayed review-line geometry interactive. Clicking a colored review line
now highlights its associated base lane while opening a popup for the review
feature itself, exposing the review code, reason, and identifiers. Circle clicks
retain their prior behavior of focusing the underlying lane-data popup.
All 42 tests, Ruff, mosque regeneration, and `git diff --check` passed. A
headless-Chrome click on a rendered ambiguous-connector review line produced one
yellow lane selection and opened a popup containing `review_code` and
`review_reasons`.

## Continuation Note — 2026-08-04T13:18:00+08:00

Corrected circle-click popup timing. Pointer and review handlers now stop the
original browser event, and underlying lane popups requested by pointer clicks
open on the next animation frame so the completing map click cannot immediately
close them.
All 42 tests, Ruff, mosque regeneration, and `git diff --check` passed. An actual
SVG circle click in headless Chrome produced one yellow selection and left the
underlying lane popup open; its contents included the pointer's target Lanelet
ID and did not contain the review-only `review_code` field.
