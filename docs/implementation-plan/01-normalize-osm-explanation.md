# Stages 1A and 1B: Acquire and Normalize OSM Explanation

This document explains the Stage 1 tasks from the implementation plan in the
same order and with matching headings. Tasks 1 through 6 are Stage 1A, which
acquires and preserves the network. Tasks 7 through 9 are Stage 1B, which
projects and checks it. Each numbered section below corresponds to exactly one
implementation-plan checkbox.

Stage 1 turns one OSM source into a preserved, offline, projected road-network
workspace. It does not generate Lanelet2 lanes or boundaries; that starts in
Stage 2.

## Current Local File

The current file is:

```text
workspaces/mosque/source/map.osm
```

It can be used for the first Stage 1 run. Because it has already been placed in
the workspace, the implementation must avoid copying the file onto itself or
silently replacing it. The final Stage 1 interface for pre-staged files should
be confirmed before implementation.

For this map, the operator supplies `--driving-side left`. Stage 1A does not
infer a workspace-wide driving side from country metadata.

## Stage 1A: Acquire and Preserve the OSM Network

### Task 1: Retrieve Place or Bounding-Box Input

> For place or bounding-box input, retrieve an unsimplified driving graph with
> OSMnx.

This task applies only when the user supplies `--place` or `--bbox`. It does not
download anything when a local `.osm` file is supplied.

For a place query, OSMnx first resolves the named place and then downloads the
drivable OSM network inside that area. For a bounding box, OSMnx downloads the
network inside the four supplied coordinates.

The graph is deliberately unsimplified. Intermediate OSM nodes along a road are
retained instead of collapsing an entire road section into one edge. Stage 2
needs those points to construct curves and lane boundaries accurately.

Disconnected components are initially retained so Stage 1 can report them
rather than silently dropping roads.

For the current `map.osm` workflow, this task is skipped and the report records
that no network request was made.

### Task 2: Preserve Relevant OSM Tags

> Preserve lane, direction, turn, width, speed, junction, access, crossing, and
> signal tags.

OSMnx does not retain every possible OSM tag by default. Before parsing, the
converter configures OSMnx to retain the tags needed for later lane generation.

The retained categories are:

- **Lane:** `lanes`, `lanes:forward`, `lanes:backward`, and related lane tags.
- **Direction:** `oneway` and directional variants.
- **Turn:** `turn:lanes` and directional turn-lane variants.
- **Width:** `width`, `est_width`, and usable lane-width tags.
- **Speed:** `maxspeed` and directional speed tags.
- **Junction:** junction and roundabout classification.
- **Access:** vehicle and general access restrictions.
- **Crossing:** crossing nodes and crossing classification.
- **Signal:** traffic-signal nodes and available signal-direction information.

The original tag strings remain available. Stage 1 may also create a parsed
value, such as an integer lane count, but it must not erase the source value.

This task is preservation, not inference. Missing lanes, turns, widths, or
signals are not invented here.

### Task 3: Load and Preserve Local `.osm` Input

> For local input, load the `.osm` XML through OSMnx's supported XML path and
> retain the original file unchanged.

This is the task that applies to the current `map.osm` file.

The converter validates that the file is readable OSM XML and then loads it
through OSMnx with graph simplification disabled. It does not manually scrape
road geometry from XML using string operations.

The source file is treated as immutable. Stage 1 calculates its SHA-256
checksum before processing and records that value in the manifest and reports.
Later stages can use the checksum to confirm that their inputs still correspond
to the same source bytes.

The checksum is useful even though the file is already in `source/`:

- it identifies the exact input contents rather than only the path;
- it detects edits made after normalization;
- it links later validation and conversion reports to this source version; and
- it allows repeated runs to determine whether they used identical input.

The existing filename is `map.osm`, while the main plan currently describes the
canonical workspace filename as `raw.osm`. Stage 1 must choose and document one
canonical behavior before implementation. It must never overwrite a pre-staged
file without an explicit operator action.

### Task 4: Save the Offline Source and Normalized Artifacts

> Save the unsimplified graph, GeoPackage or GeoParquet feature layers, raw
> `.osm`, and a source manifest.

This task writes everything required to inspect or reload Stage 1 without
contacting OpenStreetMap again.

The intended artifact groups are:

```text
workspaces/<map-id>/
|-- config.yaml
|-- source/
|   |-- raw.osm
|   `-- manifest.json
|-- normalized/
|   |-- road-network.graphml
|   `-- road-network.gpkg
`-- reports/
    |-- acquisition.json
    `-- acquisition.md
```

- `raw.osm` is the preserved source.
- `road-network.graphml` stores the unsimplified directed graph for offline
  reload.
- `road-network.gpkg` exposes node and edge layers for GeoPandas or GIS tools.
- `manifest.json` describes the source and its provenance.
- The acquisition reports describe normalization results and warnings.

The plan permits GeoPackage or GeoParquet. The implementation should select one
canonical format before code is written instead of producing both by default.

### Task 5: Record Source Provenance

> Record checksums, query bounds, acquisition time, OpenStreetMap attribution,
> and tool versions in the manifest.

The manifest explains exactly where the source came from and which tools read
it. It is separate from the normalized graph so provenance can be inspected
without loading geospatial data.

For a local file, the manifest records:

- acquisition type `local_file`;
- original or operator-supplied path;
- preserved workspace path;
- SHA-256 checksum;
- bounds declared by the OSM XML and bounds calculated from retained roads;
- processing time;
- OpenStreetMap attribution and licence information;
- converter and configuration versions; and
- Python, OSMnx, GeoPandas, Shapely, pyproj, and relevant library versions.

For a place or bounding-box source, it also records the exact query and returned
bounds. This makes later differences caused by changing online OSM data visible.

### Task 6: Resolve the Driving Side

> Require explicit `--driving-side left|right` input and record its provenance.

Driving side is needed later to order lanes and interpret directional road
information.

The operator must provide `--driving-side left` or `--driving-side right` for
every source type. The converter records the value with
`driving_side_source: explicit_cli` and does not silently select a default.
Road-level OSM `driving_side` tags are retained as evidence but do not choose
the workspace-wide value.

For the current Malaysia map, the expected value is `left`.

## Stage 1B: Project and Preflight the OSM Network

### Task 7: Define a Local East-North Coordinate Frame

> Use `pyproj` to define a local azimuthal-equidistant East-North frame centered
> on the map centroid or an explicit origin.

OSM stores positions as longitude and latitude in WGS84 degrees. Lane widths,
offsets, distances, and intersections must be calculated in metres.

Stage 1 therefore defines a local azimuthal-equidistant projection using
`pyproj`. Its origin is:

1. the explicit longitude and latitude in versioned configuration, when
   provided; otherwise
2. the centroid of the retained road network.

The projected coordinate convention is:

```text
x = east, in metres
y = north, in metres
```

The transformer uses `always_xy=True` so longitude is consistently passed
before latitude. Projected node and edge geometry is stored in the normalized
feature layers.

This task changes the working coordinate representation. It does not change the
preserved WGS84 source file.

### Task 8: Record Reversible Coordinate Transforms

> Record the full CRS definition and forward/inverse transforms so every
> downstream coordinate can be traced back to WGS84.

Defining a projection is not enough; later stages must know exactly how a local
coordinate was produced.

The workspace records:

- WGS84 as the source CRS;
- the local origin longitude and latitude;
- the complete local CRS definition, not only a nickname;
- axis order and units;
- the pyproj version;
- the forward transformation from WGS84 to local East-North; and
- the inverse transformation from local East-North back to WGS84.

Stage 1 tests representative points by projecting them into local coordinates
and transforming them back. The maximum observed error is written to both the
JSON and Markdown acquisition reports.

This traceability allows a Lanelet2 or scenario coordinate to be related back
to its original geographic location.

### Task 9: Run Preflight Checks

> Run preflight checks for empty networks, invalid geometries, disconnected
> components, conflicting direction tags, and missing lane-count data.

The checks are performed after parsing and projection but before Stage 1 is
accepted.

- **Empty network:** Block the run if no usable drivable roads remain.
- **Invalid geometry:** Block non-finite coordinates or unusable geometry;
  report repairable geometry separately.
- **Disconnected components:** Count and identify components instead of silently
  retaining only the largest one.
- **Direction conflicts:** Report incompatible one-way or directional tags that
  cannot be interpreted consistently.
- **Missing lane counts:** List affected ways for Stage 2 inference and manual
  review.

The preflight report distinguishes blocking errors from warnings. Missing lane
data is normally a warning because Stage 2 has explicit inference rules. An
empty network or failed coordinate transformation is blocking.

Every feature omitted from the normalized driving graph and every value inferred
during Stage 1 must be identified in the machine-readable report.

## Completion Gate 1: Offline Reload

> The source can be loaded again without network access.

The saved raw OSM and normalized graph must be sufficient to repeat loading and
report generation without Overpass or geocoding. A local-file run must not make
a network request in the first place.

## Completion Gate 2: Projection Round Trip

> WGS84-to-local-to-WGS84 round trips remain within the configured tolerance.

Representative source positions are projected into local metres and back to
longitude and latitude. Stage 1 passes only when the maximum error is below the
versioned configuration tolerance.

## Completion Gate 3: Report Discards and Inferences

> Every discarded or inferred OSM feature is listed in the acquisition report.

The report must state which source identifier was affected, what happened, and
why. Summary counts alone are not sufficient because an operator must be able
to inspect each decision.

## Stage Boundary

After these nine tasks and three completion gates pass, Stage 1 is complete.
It still has not:

- generated individual lanes or Lanelet2 boundaries;
- constructed intersection connectors;
- inferred missing stop lines;
- associated signals with generated Lanelet2 lanes; or
- created a ScenarioDescription-compatible dataset.

Those operations belong to later stages and should not be hidden inside OSM
normalization.

## Decisions to Review Before Implementation

1. Should `fetch` always import a file from outside a workspace, or should it
   support the current pre-staged `workspaces/mosque/source/map.osm` workflow?
2. Should the canonical preserved filename always be `raw.osm`, or should the
   operator's original filename be retained?
3. Should the normalized feature layer use GeoPackage or GeoParquet?
4. What projection round-trip tolerance should be added to the versioned
   configuration?
5. Which vehicle-access profile and road classifications should Stage 1 retain?
