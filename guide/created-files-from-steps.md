# Created Files From Steps

This guide lists the files created or updated by the implemented conversion
stages. Paths are relative to the selected map workspace, such as
`workspaces/mosque/`.

## Stage 1: Acquire and Normalize OSM

Run Stage 1 with one OSM source and an explicit driving side. For example:

```bash
uv run osm-scenario fetch \
  --osm-file workspaces/mosque/source/map.osm \
  --workspace workspaces/mosque \
  --driving-side left
```

Stage 1 creates or updates the following files:

| File | Purpose |
| --- | --- |
| `source/map.osm` | Preserved original OSM XML and source of truth for nodes, ways, tags, signals, and restriction relations. |
| `source/manifest.json` | Records the input source, driving side, checksums, road-selection results, projection, generated artifact paths, and stage statuses. |
| `normalized/road-network.graphml` | Reloadable directed Stage 1A road graph in WGS84 longitude and latitude coordinates. |
| `normalized/road-network.gpkg` | GIS-readable Stage 1A node and road layers for inspection in applications such as QGIS. |
| `normalized/road-network-local.graphml` | Directed Stage 1B road graph projected into the local East-North coordinate frame in metres. This is the principal graph input for Stage 2. |
| `normalized/road-network-local.gpkg` | GIS-readable version of the locally projected Stage 1B graph. |
| `reports/acquisition.json` | Complete machine-readable acquisition, projection, parity, and preflight report. |
| `reports/acquisition.md` | Concise human-readable summary of the acquisition and normalization results. |
| `reports/stage-1b-data-audit.json` | Detailed machine-readable audit of lane counts, widths, connectivity, traffic signals, restrictions, and stop-line evidence. |
| `reports/stage-1b-data-audit.md` | Concise human-readable summary of the Stage 1B data audit. |

The `fetch` command does not generate Lanelet2 lanes or boundaries.

## Stage 2: Generate Preliminary Lanelet2

Run Stage 2 after reviewing the Stage 1B audit:

```bash
uv run osm-scenario generate-lanelet2 \
  --workspace workspaces/mosque
```

Stage 2 creates or updates the following files:

| File | Purpose |
| --- | --- |
| `lanelet2/preliminary.osm` | Preliminary Lanelet2 map containing generated road lanelets, boundaries, intersection connectors, traffic-light associations, and stop lines. Open this file in JOSM for review. |
| `reports/lanelet2-generation.json` | Complete machine-readable generation report containing source OSM references, generated Lanelet2 IDs, inferred values, connector decisions, parser results, and the confidence-ranked correction queue. |
| `reports/lanelet2-generation.md` | Concise human-readable summary of the Stage 2 generation results. |
| `source/manifest.json` | Updated with Stage 2 status, artifact paths, checksums, file sizes, and generated feature counts. |

Stage 2 does not modify `source/map.osm` and does not create
`lanelet2/edited.osm`. Save manual JOSM corrections as `edited.osm` in the later
visual-review stage so that `preliminary.osm` remains the reproducible baseline.

## Inspection Files

Inspection files are created separately with `osm-scenario inspect`; they are
not direct outputs of `fetch` or `generate-lanelet2`.

The implemented Stage 1 inspection views can create:

| File | Purpose |
| --- | --- |
| `inspection/stage-1-source.html` | Displays the preserved source-road selection and source warnings. |
| `inspection/stage-1-normalized.html` | Displays only the locally projected Stage 1B road graph. |
| `inspection/stage-1-audit.html` | Displays the programmatic Stage 1B audit layers and searchable OSM evidence. |
| `inspection/stage-1.html` | Combined Stage 1 source and normalized inspection map. |

The Lanelet2 browser inspection view has not been implemented yet. Its planned
Stage 3 checkpoints deliberately use separate files:

| Checkpoint | Files | Purpose |
| --- | --- | --- |
| Stage 3A preliminary | `inspection/stage-3a-preliminary-audit.html`, `reports/inspection-stage-3a-preliminary.json`, and `reports/inspection-stage-3a-preliminary.md` | Inspect the immutable Stage 2 `preliminary.osm` and record its checksum. |
| Stage 3B manual review | `lanelet2/edited.osm` and `reports/stage-3b-review.yaml` | Preserve manual JOSM corrections, decisions, waivers, operator, and input/output checksums. |
| Stage 3C edited | `inspection/stage-3c-edited-audit.html`, `reports/inspection-stage-3c-edited.json`, and `reports/inspection-stage-3c-edited.md` | Inspect only the manually edited map and record its checksum. |
| Stage 3C comparison | `inspection/stage-3c-comparison.html`, `reports/inspection-stage-3c-comparison.json`, and `reports/inspection-stage-3c-comparison.md` | Compare the preliminary and edited maps and record both checksums. |

The future CLI will select these outputs explicitly with:

```bash
uv run osm-scenario inspect --workspace workspaces/mosque \
  --view lanelet2 --checkpoint preliminary

uv run osm-scenario inspect --workspace workspaces/mosque \
  --view lanelet2 --checkpoint edited

uv run osm-scenario inspect --workspace workspaces/mosque \
  --view lanelet2 --checkpoint comparison
```

Running one checkpoint may recreate only that checkpoint's files. It must not
overwrite the preliminary, edited, or comparison artifacts belonging to another
checkpoint. Until these views are implemented, inspect
`lanelet2/preliminary.osm` directly in JOSM.

## Repeat Execution

Both implemented stage commands may be run again against the same workspace.
They recreate their generated artifacts and refresh the relevant manifest
section without changing the preserved `source/map.osm`. Stage 2 also leaves an
existing `lanelet2/edited.osm` untouched.
