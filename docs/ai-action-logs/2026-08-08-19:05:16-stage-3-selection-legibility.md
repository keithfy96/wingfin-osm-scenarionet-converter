# Stage 3 Selection Legibility

- User goal: selecting a finding did not make it clear which lane was being asked
  about; bring back the Stage 2 audit's highlighting and feature popup.
- Run timestamp: 2026-08-08 19:05:16 +08 (Asia/Singapore)

## Actions Taken

- Focus highlighting is now yellow (`#ffd43b`), as in the Stage 2 audit, with source
  OSM geometry at its own weight so a way stays distinguishable from the lanes
  generated from it.
- Findings now focus `geometry_ids` / `source_geometry_ids` instead of
  `affected_feature_ids` / `source_ids`. The latter are raw OSM ids; drawn source
  features are keyed `way:<id>` / `node:<id>`, so every source highlight was silently
  matching nothing.
- Added `details.ts`: a feature index that labels a lane the way the CLAUDE.md
  diagrams do (`<id> · lane 2/3 middle`, sides named centre-out), resolves a typed
  OSM id into whichever namespace was drawn, and lists a lane's links — connector
  movements plus the continuations that carry no connector.
- Bound a popup to every drawn feature, built as DOM rather than an HTML string.
  Connectors show incoming and outgoing lane; lanes show what feeds them and where
  they go; both list the findings that name them, one click from being decided.
- Queue rows now carry a `row-where` line naming the lane and node, so findings of
  one rule stop reading as duplicates of each other.
- Detail pane ids became clickable chips; pasting an id into the search box focuses
  it on the map, which closes part of the Stage 1 search affordance gap.
- Added a legend, and a warning in the detail pane when a finding maps to no drawn
  geometry.

## Files and Directories Created or Modified

- `web/src/details.ts`, `web/src/dom.ts` — new
- `web/src/overlays.ts`, `panel.ts`, `main.ts`, `style.css`, `types.ts`, `types-dom.ts`
- `web/test/details.test.ts` — new; `web/test/fixtures.ts`
- `src/osm_scenario/assets/review-client.js` — rebuilt bundle

## Commands and Tools

- `npx tsc --noEmit`, `npx vitest run`, `node build.mjs` in `web/`.
- `uv run pytest`, `uv run ruff check`,
  `uv run osm-scenario inspect -w workspaces/junction-1 --view review`.

## What Worked

- Keeping the base style per layer in the overlay index. Restoring from the layer's
  own `feature` fails for a lane — a GeoJSON layer group carries no `feature` — and
  repainted it with the default connector style.
- Building popups as nodes with real event listeners, rather than the audit's
  `onclick="focusSource('...')"` strings, which cannot survive a bundled IIFE.

## What Went Wrong

- Decision state was previously the focus colour. Unresolved and `review_required`
  are both orange, so a selected connector looked exactly like its neighbours. State
  now lives in the row edge, the detail pane and the popup; selection is yellow only.

## Current State

- `uv run pytest` 81 passed; `uv run ruff check` clean; `npx vitest run` 26 passed
  (5 new); `npx tsc --noEmit` clean.
- All 541 findings in junction-1 now map to at least one drawn feature, so a
  selection always paints something.
- No `docs/mapping-algo-changes/` entry: no algorithm code changed, which is
  criterion 2 in CLAUDE.md Section B.

## Known Gaps Before Stage 3 Can Be Marked Complete

- Unchanged from the previous run except the search box, which now resolves ids:
  no ambiguous-crossing, missing-tag or direction-warning layers; no browser smoke
  test; no dedicated signal-association overlay; `apply-review` (Stage 4) still does
  not exist, so the export path is unverified end to end.
