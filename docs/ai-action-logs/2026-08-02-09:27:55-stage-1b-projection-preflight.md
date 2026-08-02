# Stage 1B Projection and Preflight

## User Goal

Implement Stage 1B after the user reviewed its purpose and artifacts.

## Run Timestamp

2026-08-02 09:27:55 +08 (Asia/Singapore)

## Actions Taken

- Added offline projection of the saved Stage 1A graph into a local
  azimuthal-equidistant East-North CRS.
- Added reversible CRS metadata and coordinate round-trip validation.
- Added preflight checks and machine-readable and Markdown reports.
- Wired Stage 1B into `fetch` without adding another top-level command.
- Added Stage 1B unit, CLI, offline, integrity, and idempotency coverage.
- Updated configuration and documentation for the new outputs.

## Generated Code Details

### What Was Created or Changed

- Created `src/osm_scenario/normalization.py`.
- Extended the CLI, converter configuration, dependency declaration, and tests.
- Added local GraphML/GeoPackage outputs and acquisition JSON/Markdown reports.

### Why It Was Created or Changed

Lane geometry must be calculated in metres, while later output must remain
traceable to WGS84. Preflight prevents unsafe geometry from reaching Stage 2
and reports incomplete OSM evidence for review.

### How It Works

Stage 1B reloads and checksum-verifies the Stage 1A GraphML, selects an explicit
configured origin or the retained road-geometry centroid, projects through
`pyproj` with `always_xy=True`, checks inverse-transform accuracy, writes
separate projected artifacts, and updates the manifest. Blocking errors fail
the command; warnings remain available in the reports.

### How It Was Validated

- `uv run pytest -q`: 13 tests passed.
- `uv run ruff check .`: passed.
- The real mosque workspace produced 6,029 projected nodes and 12,683 edges.
- Maximum observed round-trip error was `2.842170943040401e-14` degrees against
  the configured `1e-9` degree tolerance.
- All Stage 1B artifact checksums matched the manifest and the source OSM
  checksum remained unchanged.

## Files and Directories Created or Modified

- `src/osm_scenario/normalization.py`
- `src/osm_scenario/cli.py`
- `src/osm_scenario/config.py`
- `config/default.yaml`
- `pyproject.toml` and `uv.lock`
- `tests/unit/test_normalization.py`
- `tests/unit/test_cli.py` and `tests/unit/test_config.py`
- Root and implementation documentation

## Commands or Tools Run

- `uv run pytest -q`
- `uv run ruff check .`
- `uv run osm-scenario fetch --osm-file workspaces/mosque/source/map.osm
  --workspace workspaces/mosque --driving-side left`
- GraphML, GeoPackage, report, manifest, checksum, and CRS read-back checks

## What Worked

Projection, offline reload, repeated execution, reports, manifest integrity,
and the real workspace completion gate all passed.

## What Went Wrong

The first Markdown report repeated every graph-edge warning and was not
concise. Warnings were deduplicated by OSM identifier, and the Markdown report
now contains grouped counts and a bounded sample while JSON retains the full
list. Two initial line-length lint violations were also corrected.

## Current State

Stage 1B is implemented and its completion gate passes. The mosque input has no
blocking errors. Its report contains missing-lane-count and disconnected
component warnings for later inference and review.

## Recommended Next Step

Review `workspaces/mosque/reports/acquisition.md` and sample the full warning
records in `acquisition.json` before defining Stage 2 lane inference behavior.
