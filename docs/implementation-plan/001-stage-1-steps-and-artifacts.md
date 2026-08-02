# Stage 1 Steps and Artifacts: What They Do and Why

This document explains Stage 1 as a data flow. For each step, it identifies the
input, the work performed, the output, and why that output is needed.

Stage 1 does not generate Lanelet2 lanes. Its job is to turn one OSM source into
a preserved, reloadable, inspectable, and geometrically safe input for Stage 2.

```text
OSM source
    |
    v
Stage 1A: acquire and preserve
    |
    +-- source/map.osm
    +-- source/manifest.json
    +-- normalized/road-network.graphml
    `-- normalized/road-network.gpkg
    |
    v
Stage 1B: project and preflight
    |
    +-- projected metre-based road geometry
    +-- coordinate-system metadata
    `-- preflight reports
    |
    v
Stage 2: generate Lanelet2 lanes and boundaries
```

The projected geometry is stored as
`normalized/road-network-local.graphml` and
`normalized/road-network-local.gpkg`. The reports are stored as
`reports/acquisition.json` and `reports/acquisition.md`.

## Stage 1A: Acquire and Preserve the OSM Network

### Step 1: Accept Exactly One OSM Source

**What it does:** The `fetch` command accepts one local `.osm` file, one place
query, or one bounding box. It rejects runs that provide no source or more than
one source.

**Why it is needed:** Every workspace must have one unambiguous origin. Mixing a
local file with an online query would make it unclear which roads belong to the
map and which input should be reproduced later.

For a local file, the converter reads the existing XML. For a place or bounding
box, OSMnx obtains the drivable road network from OpenStreetMap.

### Step 2: Build an Unsimplified Directed Road Graph

**What it does:** OSMnx interprets OSM nodes and ways as a directed graph:

- graph nodes represent road geometry points or junctions;
- graph edges represent travel along a road segment; and
- edge direction represents permitted travel direction.

The graph remains unsimplified, so intermediate OSM geometry points are not
discarded.

**Why it is needed:** Raw OSM XML is a general geographic format, not a
lane-generation data structure. Stage 2 needs explicit road connectivity and
travel direction to determine which road segments connect, which approaches
enter an intersection, and which directions require lanes.

Retaining intermediate nodes also preserves road curvature. If a curved road
were reduced to only its endpoints, later lane boundaries could cut across the
curve.

### Step 3: Preserve Conversion-Relevant OSM Evidence

**What it does:** The graph retains source tags for lane counts, travel
direction, turns, widths, speed limits, junctions, access rules, crossings, and
traffic signals. The original tag values are preserved even when a parsed value
is also produced.

**Why it is needed:** Stage 2 uses these tags as evidence when constructing
lanes. For example, `lanes=3`, `oneway=yes`, and `turn:lanes=left|through|right`
affect different parts of the generated geometry. Stage 1 must not silently
discard evidence and then force Stage 2 to guess.

This step preserves information; it does not invent missing lane data.

### Step 4: Preserve `source/map.osm`

**What it does:** A local source already stored at `source/map.osm` is read in
place and remains byte-for-byte unchanged. An external local file is copied
into the workspace. For online acquisition, the workspace receives an OSM
export and the manifest identifies it as an OSMnx graph export rather than the
original Overpass response.

**Why it is needed:** This is the closest available source record. It contains
the original OSM identifiers, tags, nodes, ways, and relations that may not all
fit naturally in an OSMnx graph. It is also the evidence used when investigating
whether normalization lost or reinterpreted something.

The checksum identifies the exact file contents. A path alone is insufficient
because a file can be edited while retaining the same name.

### Step 5: Create `normalized/road-network.graphml`

**What it does:** This file serializes the OSMnx directed graph, including its
nodes, edges, connectivity, geometry, and retained attributes.

**Why it is needed:** Stage 2 should not parse the raw OSM again or contact an
online service. Repeating the parse could produce different results after a
library upgrade or configuration change. GraphML freezes the normalized graph
that Stage 1 produced and lets later commands reload it directly.

This file is primarily for the converter and OSMnx. It answers topology
questions such as:

- Which road segments connect to this junction?
- Which direction can each segment be travelled?
- Which OSM tags belong to this graph edge?
- Which nodes and edges should Stage 2 turn into lane geometry?

`road-network.graphml` does not contain generated lanes or Lanelet2 boundaries.
It contains normalized road-level topology.

### Step 6: Create `normalized/road-network.gpkg`

**What it does:** This GeoPackage stores the normalized graph as GIS feature
layers. The `nodes` layer contains point features, and the `edges` layer
contains road line features with inspectable columns.

**Why it is needed:** GraphML is convenient for OSMnx but awkward to inspect in
GIS software. GeoPackage can be opened with GeoPandas, QGIS, or another GIS
tool, making it possible to view the geometry, filter attributes, and identify
problem roads before lane generation.

The GraphML and GeoPackage are therefore two views of the same normalized
network:

| Artifact | Main user | Main purpose |
| --- | --- | --- |
| `road-network.graphml` | Converter and OSMnx | Reload directed topology and attributes |
| `road-network.gpkg` | Operator, GeoPandas, and GIS tools | Inspect nodes, edges, geometry, and columns |

The GeoPackage is not an additional conversion stage and is not the source of
truth for lane generation. It is an inspectable representation of the graph.

### Step 7: Create `source/manifest.json`

**What it does:** The manifest records where the source came from, its checksum
and bounds, the explicit driving side, generated artifact paths and checksums,
acquisition time, attribution, and tool versions.

**Why it is needed:** Generated files do not explain their own history. The
manifest lets a later command or operator answer:

- Which source produced this workspace?
- Was the source local, a place query, or a bounding box?
- Was left-hand or right-hand driving selected?
- Have any inputs or Stage 1A artifacts changed?
- Which tool versions produced the normalized network?

The manifest is generated automatically by `fetch`; the operator does not need
to create it manually.

### Step 8: Record the Explicit Driving Side

**What it does:** The operator supplies `--driving-side left` or
`--driving-side right`, and Stage 1 records both the value and that it came from
the CLI.

**Why it is needed:** OSM road tags do not provide a dependable country-wide
driving-side value for every map. Stage 2 needs this decision when ordering
lanes and interpreting bidirectional roads, so it must be explicit and
reproducible rather than guessed.

## Why Stage 1A Does Not Generate Lanes Yet

At the end of Stage 1A, the project has road centerline topology and OSM
evidence. It does not yet have enough checked, metre-based geometry to produce
reliable lane boundaries.

Separating acquisition from lane generation provides a review point:

1. The source can be preserved once.
2. The normalized road graph can be inspected and tested independently.
3. Stage 2 can be rerun without downloading or reinterpreting OSM.
4. A Lanelet2 defect can be traced either to the source, normalization, or lane
   generation rather than treating the entire pipeline as one opaque action.

## Stage 1B: Project and Preflight the OSM Network

### Step 9: Define a Local East-North Coordinate Frame

**What it does:** `pyproj` converts WGS84 longitude and latitude into local
coordinates where `x` is east in metres and `y` is north in metres. The map uses
an explicit configured origin when provided; otherwise it uses the retained
network centroid.

**Why it is needed:** Longitude and latitude are angular degrees. Lane widths,
offsets, stop-line distances, and intersection geometry must be calculated in
metres. Shapely can then offset a centerline by an actual lane width such as
3.5 metres.

The preserved OSM file remains in WGS84. Projection changes the working
geometry, not the original source.

### Step 10: Record a Reversible Coordinate Transformation

**What it does:** Stage 1B records the source CRS, complete local CRS
definition, origin, axis order, units, and relevant tool version. From that
metadata, `pyproj` can reconstruct both WGS84-to-local and local-to-WGS84
transformers.

Representative points are projected and transformed back. The maximum
round-trip error is reported.

**Why it is needed:** A local point such as `(125.4, -83.7)` has no geographic
meaning unless its origin and projection are known. Reversibility also allows
metre-based geometry created by Stage 2 to be written back as geographic
Lanelet2 `.osm` nodes and traced to its original map location.

Python transformer objects do not need to be serialized. Recording the exact
CRS metadata is sufficient to recreate them.

### Step 11: Run Preflight Checks

**What it does:** Stage 1B checks for an empty network, unusable geometry,
disconnected components, conflicting direction tags, and missing lane counts.
It separates blocking errors from warnings and records affected OSM identifiers
and reasons.

**Why it is needed:** Generating lane boundaries from invalid or contradictory
roads would produce a more complicated failure later. Preflight stops inputs
that cannot be converted safely and exposes incomplete but usable inputs for
manual review or Stage 2 inference.

Missing lane counts are normally warnings because Stage 2 has controlled
inference rules. An empty road network or failed coordinate transformation is a
blocking error.

## Which Artifact Is Authoritative?

The artifacts have different responsibilities rather than replacing one
another:

1. `source/map.osm` is the preserved source evidence.
2. `source/manifest.json` is the provenance and integrity record.
3. `normalized/road-network.graphml` is the reloadable normalized topology used
   by later converter stages.
4. `normalized/road-network.gpkg` is the human- and GIS-inspectable view of that
   topology.
5. Stage 1B projected layers are the metre-based working geometry used for lane
   calculations.
6. The reports explain warnings, exclusions, inferences, and coordinate checks.

None of the Stage 1 files is the final playable scenario. They make the later
Lanelet2 and scenario outputs reproducible, inspectable, and easier to debug.
