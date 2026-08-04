# Stage 1 Project Guide

The active implementation stops after producing and inspecting a normalized,
audited OSM road-network workspace.

## Source of truth

- `source/map.osm` is the immutable source snapshot used by the run.
- `source/manifest.json` records source provenance, checksums, driving side,
  selection counts, and generated artifact checksums.
- `normalized/road-network.graphml` is the WGS84 directed graph.
- `normalized/road-network-local.graphml` is the projected metre-based graph.
- GeoPackage files are convenient inspection exports, not replacement inputs.
- `reports/stage-1b-data-audit.json` is the detailed audit result; warnings are
  review findings, not automatically confirmed map defects.

## Stage 1A: acquisition and graph selection

`fetch` accepts a local OSM XML file, a place query, or a bounding box. It
requires an explicit left/right driving side and preserves the complete tag
dictionary for retained ways. The `public-driving-v1` policy excludes clearly
non-driving and non-public ways, then constructs an unsimplified directed
multigraph so source intersections and identifiers remain inspectable.

## Stage 1B: projection and audit

The normalizer selects a local projected CRS, writes WGS84 and local-coordinate
artifacts, checks coordinate round-trip error, and runs the data audit. The
audit covers lane-count evidence, directionality, connectivity, grade
separation, turn restrictions, traffic signals, physical width, and mapped
stop-line candidates. Configuration controls projection tolerance, default
vehicle-lane width, and whether missing lane counts may be inferred.

## Visual inspection

The `inspect` command supports `source`, `normalized`, `audit`, and `stage-1`.
HTML views and their reports are generated inside the workspace. They are
derived artifacts and can be regenerated from the source snapshot and Stage 1
configuration.

## Validation boundary

A successful run proves that acquisition, normalization, projection, artifact
checksums, and report generation completed. Audit warnings and
`review_required` findings must still be examined before the data is used by a
future downstream design.
