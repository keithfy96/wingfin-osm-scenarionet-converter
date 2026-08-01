# Interface and Artifact Explanation

The **Interfaces and Artifacts** section of the implementation plan defines the
pipeline's overall user-facing contract. The numbered stages in the main plan
describe how that contract will be implemented.

## 1. Top-Level CLI

The project will provide the following commands:

```text
osm-scenario fetch
osm-scenario generate-lanelet2
osm-scenario inspect
osm-scenario validate-lanelet2
osm-scenario convert
osm-scenario validate-scenario
osm-scenario play
```

Each command represents a pipeline checkpoint:

- `fetch` imports a local `.osm` file or downloads OSM data using a place or
  bounding box. It preserves the raw input and creates the source manifest.
- `generate-lanelet2` converts normalized OSM roads into directed lanes,
  boundaries, intersections, stop lines, and traffic-light associations. It
  produces `preliminary.osm`.
- `inspect` produces visual overlays and prepares the map for review in JOSM.
  Manual corrections are saved separately as `edited.osm`.
- `validate-lanelet2` runs structural and geometric Lanelet2 checks, such as
  missing boundaries, incorrect orientation, overlaps, disconnected lanes, and
  invalid references.
- `convert` translates the approved Lanelet2 map into ScenarioNet's
  `ScenarioDescription` representation.
- `validate-scenario` runs ScenarioNet schema checks and project-specific checks
  for road geometry, traffic lights, route validity, and coordinate consistency.
- `play` loads the resulting scenario in MetaDrive for a visual smoke test.

The commands operate on the same map workspace. This keeps every intermediate
artifact inspectable instead of hiding the complete process behind one opaque
conversion command.

## 2. Exactly One OSM Source

An acquisition run must specify one, and only one, of the following sources:

```bash
osm-scenario fetch --osm-file inputs/town.osm
osm-scenario fetch --place "Queenstown, Singapore"
osm-scenario fetch --bbox 1.305,103.795,1.285,103.820
```

The source options are mutually exclusive:

- **Local `.osm`:** Reproducible and usable without network access.
- **Place query:** Convenient, but the returned boundary depends on OpenStreetMap
  geocoding.
- **Bounding box:** A precise geographic extraction using north, south, east,
  and west coordinates.

After acquisition, later commands identify the map workspace instead of
specifying the OSM source again.

## 3. Isolated Map Workspace

Every map gets its own directory so artifacts from different locations or
conversion attempts cannot become mixed:

```text
workspaces/queenstown/
|-- config.yaml
|-- source/
|   |-- raw.osm
|   `-- manifest.json
|-- normalized/
|   |-- road-network.gpkg
|   `-- acquisition-report.json
|-- lanelet2/
|   |-- preliminary.osm
|   `-- edited.osm
|-- reports/
|   |-- inference.json
|   |-- inference.md
|   |-- geometry.json
|   |-- geometry.md
|   |-- lanelet2-validation.json
|   `-- lanelet2-validation.md
`-- scenarionet/
    |-- scenario.pkl
    |-- conversion.json
    `-- conversion.md
```

The workspace follows these rules:

- `raw.osm` remains unchanged.
- `preliminary.osm` remains unchanged after generation.
- Human corrections are stored in `edited.osm`.
- Conversion uses `edited.osm` when present. Using the preliminary map requires
  explicit operator approval.
- Reports and final ScenarioNet output remain tied to the exact input and
  configuration used to produce them.

## 4. Versioned YAML Configuration

Configuration contains conversion decisions that should not be hidden inside
the implementation:

```yaml
schema_version: 1

driving_side: left

coordinate_origin:
  mode: centroid

lane_defaults:
  width_m: 3.5

tag_inference:
  missing_lane_count: one_per_direction
  infer_stop_lines: true

scenario_route:
  mode: longest_connected_route

output:
  duration_seconds: 30
```

The fields control:

- `driving_side` determines lane placement, travel direction, and intersection
  behavior.
- `coordinate_origin` defines where the local East-North coordinate frame
  begins.
- `lane_defaults` supplies lane widths only when OSM has no usable width
  information.
- `tag_inference` defines permitted fallback behavior for incomplete OSM tags.
- `scenario_route` selects the synthetic ego route required to load and
  navigate the static layout in ScenarioNet and MetaDrive.
- `output.duration_seconds` sets the ScenarioNet timeline length even though the
  map itself is static.

The `schema_version` field allows future configuration changes to be detected
and migrated instead of silently interpreting an older configuration
incorrectly.

## 5. JSON Reports and Markdown Summaries

Each major operation produces two representations of its results:

- JSON supports automated checks, tests, CI, and downstream tooling.
- Markdown provides a concise report for human review.

The report categories are:

- `inference`: Missing lane counts, inferred widths, assumed directions,
  generated stop lines, and confidence levels.
- `geometry`: Invalid shapes, offsets, overlaps, disconnected components, tight
  curves, and questionable junction connectors.
- `validation`: Lanelet2 parser and validator results, unresolved references,
  warnings, and blocking errors.
- `conversion`: Counts of roads, lanelets, signals, stop lines, skipped
  elements, ScenarioNet identifiers, and source-to-output traceability.

The Markdown summaries stay concise. Detailed coordinates, feature identifiers,
and diagnostic data remain in the corresponding JSON reports.

## Relationship to the Implementation Plan

These interface requirements establish:

- how an operator runs the pipeline;
- how OSM inputs are controlled;
- where source, intermediate, and output artifacts are stored;
- where conversion assumptions and defaults are recorded; and
- how results are reviewed and audited.

The stages in the main implementation plan break down the engineering work
needed to deliver these behaviors, from project setup and OSM acquisition
through Lanelet2 editing, ScenarioNet conversion, and MetaDrive verification.
