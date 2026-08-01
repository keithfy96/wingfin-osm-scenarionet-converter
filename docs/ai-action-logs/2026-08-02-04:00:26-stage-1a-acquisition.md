# Stage 1A Acquisition

## User Goal

Implement Stage 1A of the reviewed OSM conversion plan.

## Run Timestamp

2026-08-02 04:00:26 +08:00 (Asia/Singapore)

## Actions Taken

- Implemented local file, place-query, and bounding-box OSM acquisition.
- Added unsimplified OSMnx graph, GraphML, GeoPackage, and JSON manifest output.
- Preserved conversion-relevant OSM node and way tags.
- Required an explicit CLI driving side and recorded its provenance.
- Added a small OSM fixture and Stage 1A round-trip tests.
- Updated Stage 1A documentation to describe implemented behavior.

## Files And Directories Modified

- `src/osm_scenario/acquisition.py`
- `src/osm_scenario/cli.py`
- `tests/fixtures/osm/tiny.osm`
- `tests/unit/test_cli.py`
- `README.md`
- `docs/implementation-plan/README.md`
- `docs/implementation-plan/01-normalize-osm-explanation.md`
- `docs/ai-action-logs/2026-08-02-04:00:26-stage-1a-acquisition.md`

## Commands And Tools Run

- `uv run ruff check src tests`
- `uv run pytest`
- `uv run osm-scenario fetch` against `workspaces/mosque/source/map.osm`
- Offline GraphML, GeoPackage, manifest, and checksum read-back commands

## Generated Code Details

### What Was Created Or Changed

The `fetch` command now calls a dedicated acquisition module that creates the
Stage 1A workspace artifacts and manifest. Tests cover local-source immutability,
tag and relation evidence, checksums, path containment, CRS, and read-back.

### Why It Was Created Or Changed

Stage 1A needs a durable, inspectable OSM network that later stages can consume
without reparsing or downloading the source again.

### How It Works

OSMnx builds an unsimplified directed graph from exactly one source. The graph
is stored as GraphML and as GeoPackage node and edge layers. A generated JSON
manifest records source provenance, bounds, checksums, counts, versions, and
artifact paths. Pre-staged local OSM files are parsed in place and not modified.

### How It Was Validated

Ruff passed and all eight tests passed. The real mosque map reloaded with 6,029
nodes and 12,683 edges in both GraphML and GeoPackage; its source SHA-256 stayed
`6e3fe2068b856a9a661da258d898ac37c10e1bd2e0232e92586027ca541d154e`.

## What Worked

- Local OSM parsing and artifact generation completed without network access.
- Generated GraphML and GeoPackage layers reload with matching feature counts.
- The pre-staged source checksum remained unchanged.

## What Went Wrong

The first `uv` verification attempt could not write to the global cache. Using
`UV_CACHE_DIR=/tmp/uv-cache` resolved the environment restriction.

## Current State

Stage 1A is implemented. Stage 1B projection and preflight are not implemented.
Online acquisition code is present but was not exercised because that requires
Nominatim and Overpass network access.

## Recommended Next Step

Review the generated mosque workspace and manifest, then review Stage 1B before
implementation begins.
