# OSM to MetaDrive-Compatible Scenario Dataset Implementation Plan

## Summary

Build a direct, reviewable pipeline:

```text
OSM file/place/bbox
    -> OSMnx and GeoPandas
    -> local East-North geometry
    -> preliminary Lanelet2
    -> JOSM correction
    -> Lanelet2 validation
    -> standalone ScenarioDescription-compatible dataset
    -> optional external MetaDrive consumer check
```

The output contains the static road layout and traffic-light locations. It contains no background traffic. A clearly marked synthetic ego route is added only because MetaDrive's scenario environment requires an SDC track for loading and navigation. This repository produces the complete dataset without importing, installing, cloning, or path-referencing MetaDrive, ScenarioNet, or `wingfin-metadrive`.

## Interfaces and Artifacts

- Provide a top-level `osm-scenario` CLI with `fetch`, `generate-lanelet2`, `inspect`, `validate-lanelet2`, `convert`, and `validate-scenario` commands.
- Accept exactly one OSM source per run: local `.osm`, place query, or bounding box.
- Store each map in an isolated workspace containing raw input, source manifest, preliminary and edited Lanelet2 maps, validation reports, and the final standalone scenario dataset.
- Use versioned YAML configuration for driving side, coordinate origin, lane-width defaults, tag inference, scenario route, and output duration.
- Emit machine-readable JSON reports plus concise Markdown summaries for inference, geometry, validation, and conversion results.
- Reserve interactive `play` and an owned MetaDrive integration container for a future phase. V1 produces a consumer handoff manifest and supports only an opt-in external compatibility check.

## Stage 0: Project Foundation

- [x] Create one locked Python 3.10 `uv` environment for the complete standalone converter.
- [x] Install only this project's direct runtime dependencies, including OSMnx, GeoPandas, Shapely, pyproj, Lanelet2, NumPy, YAML/configuration, and CLI libraries.
- [x] Add typed configuration models, structured logging, deterministic IDs, CLI error handling, pytest, Ruff, and fixture directories.
- [x] Add a locally owned, versioned scenario compatibility contract; do not import, install, clone, or path-reference MetaDrive, ScenarioNet, or `wingfin-metadrive`.
- [x] Start the required timestamped AI action log when code implementation begins and update it throughout implementation.

### Completion gate

- [x] The standalone environment installs from its lock file on a clean checkout.
- [x] Every CLI command exposes `--help` and exits with a useful error when required input is missing.
- [x] Dependency inspection confirms that no MetaDrive, ScenarioNet, or sibling-project package is installed transitively.

### Manual verification

1. From a clean checkout, run the documented `uv sync --locked` command.
   Confirm that it does not change the lock file.
2. Run `osm-scenario --help`, followed by `<command> --help` for every command
   listed under **Interfaces and Artifacts**. Confirm that each command explains
   its required arguments and exits with status `0`.
3. Run each command once without its required source or workspace argument.
   Confirm that it exits nonzero, names the missing argument, and does not emit
   a Python traceback.
4. Inspect the resolved dependency tree and search the project configuration for
   `metadrive`, `scenarionet`, and sibling paths. Confirm that none is a runtime,
   development, editable, Git, or path dependency.

## Stage 1A: Acquire and Preserve the OSM Network

- [x] For place or bounding-box input, retrieve an unsimplified driving graph with OSMnx.
- [x] Preserve lane, direction, turn, width, speed, junction, access, crossing, and signal tags.
- [x] For local input, load the `.osm` XML through OSMnx's supported XML path and retain the original file unchanged.
- [x] Save the unsimplified graph, GeoPackage feature layers, `.osm`, and a source manifest.
- [x] Record checksums, query or graph bounds, acquisition time, OpenStreetMap attribution, and tool versions in the manifest.
- [x] Require explicit `--driving-side left|right` input and record its provenance.

Stage 1A does not generate Lanelet2 geometry. It creates a durable,
inspectable source network that later stages can reload without downloading or
reinterpreting the original input.

### Outputs

```text
<workspace>/source/map.osm                       Preserved local source
<workspace>/source/manifest.json                 Generated provenance
<workspace>/normalized/road-network.graphml      Reloadable OSMnx graph
<workspace>/normalized/road-network.gpkg         Inspectable nodes and edges
```

For an online source, `source/map.osm` is an OSMnx export of the acquired graph,
not the original Overpass payload. The manifest labels this
`osmnx_graph_export`. A local file already inside `source/` is authoritative,
used in place, and not renamed, replaced, or modified.

### Completion gate

- [ ] A local source has the same SHA-256 checksum before and after Stage 1A.
- [ ] GraphML reloads through OSMnx with at least one node and one directed edge.
- [ ] GeoPackage node and edge layers reload through GeoPandas and are nonempty.
- [ ] The manifest is valid JSON and identifies the source, source type, driving side, checksums, bounds, tool versions, and generated artifact paths.
- [ ] Required OSM evidence remains on graph features or in the unchanged raw OSM, including relation data that OSMnx does not place on graph edges.
- [ ] All Stage 1A artifacts can be read again with network access disabled.

### Automated verification

Add focused `pytest` coverage using a small checked-in `.osm` fixture. The test
suite must:

1. Calculate the fixture checksum, run Stage 1A, and confirm the source checksum
   is unchanged.
2. Assert that `road-network.graphml`, `road-network.gpkg`, and `manifest.json`
   exist at the documented paths.
3. Reload GraphML with OSMnx and assert nonzero node and directed-edge counts.
4. Reload the `nodes` and `edges` GeoPackage layers with GeoPandas and assert
   that both are nonempty and have a declared CRS.
5. Validate the manifest schema, recompute every recorded checksum, and confirm
   every recorded relative path resolves inside the workspace.
6. Use fixture roads containing representative lane, direction, turn, width,
   speed, junction, access, crossing, signal, and relation evidence; assert
   that Stage 1A has not silently discarded that evidence.
7. Disable or mock network clients during read-back and fail if Nominatim,
   Overpass, or another HTTP endpoint is contacted.

### Manual verification

These commands are runnable against the Stage 1A implementation.

1. Record the checksum, run Stage 1A, then calculate the checksum again:

   ```bash
   sha256sum workspaces/mosque/source/map.osm
   uv run osm-scenario fetch \
     --osm-file workspaces/mosque/source/map.osm \
     --workspace workspaces/mosque \
     --driving-side left
   sha256sum workspaces/mosque/source/map.osm
   ```

   The two checksum values must match exactly.

2. Confirm that each generated artifact exists:

   ```bash
   test -f workspaces/mosque/normalized/road-network.graphml
   test -f workspaces/mosque/normalized/road-network.gpkg
   test -f workspaces/mosque/source/manifest.json
   ```

3. Reload the graph and inspect its size:

   ```bash
   uv run python -c 'import osmnx as ox; g = ox.load_graphml("workspaces/mosque/normalized/road-network.graphml"); print({"nodes": len(g.nodes), "edges": len(g.edges)})'
   ```

   Both numbers must be greater than zero.

4. Reload the inspectable feature layers:

   ```bash
   uv run python -c 'import geopandas as gpd; p = "workspaces/mosque/normalized/road-network.gpkg"; print({"nodes": len(gpd.read_file(p, layer="nodes")), "edges": len(gpd.read_file(p, layer="edges"))})'
   ```

   Both numbers must be greater than zero.

5. Pretty-print and inspect the generated manifest:

   ```bash
   uv run python -m json.tool workspaces/mosque/source/manifest.json
   ```

   Confirm that it describes the source, explicit driving side, generated
   files, checksums, bounds, and tool versions. The operator does not create
   this manifest manually.

6. Disable network access and repeat steps 3 through 5. Reading saved Stage 1A
   output must not require Nominatim, Overpass, or another service.

## Stage 1B: Project and Preflight the OSM Network

- [ ] Use `pyproj` to define a local azimuthal-equidistant East-North frame centered on the map centroid or an explicit origin.
- [ ] Record the full CRS definition and forward/inverse transforms so every downstream coordinate can be traced back to WGS84.
- [ ] Run preflight checks for empty networks, invalid geometries, disconnected components, conflicting direction tags, and missing lane-count data.

### Completion gate

- [ ] The source can be loaded again without network access.
- [ ] WGS84-to-local-to-WGS84 round trips remain within the configured tolerance.
- [ ] Every discarded or inferred OSM feature is listed in the acquisition report.

### Manual verification

1. Run Stage 1B against the saved Stage 1A workspace. Confirm that it reads the
   saved graph and raw OSM rather than downloading the network again.
2. Open the acquisition Markdown summary and its JSON counterpart. Confirm that
   the origin, CRS, bounds, feature counts, inferred tags, discarded features,
   and round-trip projection error agree, and that the error is within the
   configured tolerance.
3. Inspect several projected node coordinates. Confirm that they are local
   metre values with East as `x` and North as `y`, not longitude and latitude.
4. Review every preflight warning and discarded-feature entry. Confirm that it
   includes the affected OSM identifier and an explicit reason.

## Stage 2: Generate Preliminary Lanelet2

- [ ] Normalize OSM tags into directed road segments.
- [ ] Honor explicit `lanes`, directional lane counts, `oneway`, `turn:lanes`, `maxspeed`, and restriction relations before applying defaults.
- [ ] Generate directed lane centerlines with Shapely offsets.
- [ ] Infer one lane per permitted direction only when OSM lacks lane counts, and report every inference.
- [ ] Generate left and right boundaries from configured or tagged widths.
- [ ] Reuse shared boundary objects, maintain consistent orientation, and avoid hand-written geometry or XML operations.
- [ ] Split approaches near junctions and build intersection connectors from graph topology, permitted turns, lane ordering, and restriction relations.
- [ ] Flag ambiguous connectors for manual review instead of silently selecting questionable geometry.
- [ ] Handle divided roads, merges, forks, T-junctions, four-way junctions, and roundabouts as explicit geometry cases.
- [ ] Associate OSM traffic-signal nodes with incoming lanelets and stop lines.
- [ ] Generate a marked inferred stop line only when no mapped stop line exists.
- [ ] Write positive, stable Lanelet2 IDs using Lanelet2 primitives and its writer rather than constructing XML manually.
- [ ] Produce `preliminary.osm`, an inference report, and a confidence-ranked correction queue.

### Completion gate

- [ ] `preliminary.osm` loads through the Lanelet2 parser.
- [ ] All generated lanelets have two boundaries, a direction, and traceable source IDs.
- [ ] Every low-confidence lane, connector, and traffic-light association appears in the correction queue.

### Manual verification

1. Run `osm-scenario generate-lanelet2 --workspace workspaces/<map-id>` and
   confirm that `lanelet2/preliminary.osm` is created while `source/raw.osm`
   remains byte-for-byte unchanged.
2. Open `preliminary.osm` in JOSM with the Lanelet2 style enabled. Select several
   straight roads, curves, intersection connectors, and signalized approaches;
   confirm that every lanelet has left and right bounds oriented in its driving
   direction.
3. Trace the source identifiers for several generated lanelets back to the
   corresponding OSM ways in the inference report.
4. Inspect every high-priority item in the correction queue. Confirm that each
   highlighted lane, connector, stop line, or signal association is visible in
   the overlay and has a stated reason and confidence.

## Stage 3: Visual Inspection and Manual Correction

- [ ] Generate before/after overlays showing OSM centerlines, lanelet boundaries, connectors, signal positions, inferred tags, and validation hotspots.
- [ ] Configure JOSM with the official Lanelet2 map styles and presets.
- [ ] Keep `preliminary.osm` immutable and save manual work as `edited.osm`.
- [ ] Review lane direction, lane count, shared boundaries, connector turns, roundabouts, stop lines, signal associations, overlaps, and disconnected roads.
- [ ] Record resolved correction-queue items and any intentional deviations in a review checklist.
- [ ] Block final dataset conversion until `edited.osm` exists or the operator explicitly approves the preliminary map.

### Completion gate

- [ ] The operator signs off the visual review checklist.
- [ ] All high-confidence automated findings are either corrected or explicitly waived.

### Manual verification

1. Run `osm-scenario inspect --workspace workspaces/<map-id>` and open the
   generated overlay and `preliminary.osm` in JOSM.
2. Compare the lanelet map against the OSM source or imagery at every flagged
   intersection. Check lane direction, lane count, shared boundaries, permitted
   turns, roundabouts, stop lines, signal positions, overlaps, and disconnected
   roads.
3. Save corrections only to `lanelet2/edited.osm`. Confirm that the checksum of
   `preliminary.osm` has not changed.
4. Mark every correction-queue item as corrected or waived with a reason, then
   sign and date the review checklist. Confirm that conversion remains blocked
   while an unreviewed blocking item exists.

## Stage 4: Validate Lanelet2

- [ ] Load the edited map with the recorded origin and Lanelet2 projector; fail on parser or projection errors.
- [ ] Run `lanelet2_validate` in a pinned Lanelet2 1.2.2 Docker tool image, including the map origin.
- [ ] Build a vehicle routing graph and verify successor, predecessor, lane-change, and reachable-route relationships.
- [ ] Check boundary orientation, width bounds, self-intersections, duplicate IDs, connector curvature, overlapping lanelets, dangling approaches, and unassociated signals.
- [ ] Treat validator errors as blocking.
- [ ] Require each retained warning to have an explicit waiver and reason in the validation report.

### Completion gate

- [ ] Native Lanelet2 validation has no unwaived errors.
- [ ] At least one valid vehicle route exists in every accepted drivable component.
- [ ] The final validation report identifies the exact map checksum it covers.

### Manual verification

1. Run `osm-scenario validate-lanelet2 --workspace workspaces/<map-id>` and
   confirm that the command identifies whether it validated `edited.osm` or the
   explicitly approved `preliminary.osm`.
2. Run the native `lanelet2_validate` command printed in the report. Confirm that
   its error count and warning count match the JSON and Markdown summaries.
3. In JOSM, inspect every reported location and every waiver. Confirm that each
   waiver has an operator, reason, and matching feature identifier.
4. Use the route check reported by the CLI to inspect one start-to-finish vehicle
   path in each accepted drivable component. Confirm that the route follows lane
   direction and permitted connections.
5. Calculate the validated map's checksum using the documented checksum command
   and confirm that it matches the checksum embedded in the validation report.

## Stage 5: Convert Lanelet2 to a Standalone Scenario Dataset

- [ ] Convert lane centerlines, boundaries, crossings, speed limits, entry/exit links, and left/right neighbors into MetaDrive `map_features`.
- [ ] Preserve traffic-light stop positions and controlled-lane IDs in `dynamic_map_states`.
- [ ] Fill each traffic-light timeline with `TRAFFIC_LIGHT_UNKNOWN`, leaving phase generation to later MetaDrive code.
- [ ] Add one synthetic SDC navigation track sampled along a valid route.
- [ ] Allow explicit start and goal lanelets; otherwise select a deterministic long route in the largest drivable component.
- [ ] Mark the track as synthetic layout scaffolding in metadata and emit no other tracks.
- [ ] Define a locally owned, typed compatibility model for the seven required sections: `id`, `version`, `length`, `metadata`, `tracks`, `dynamic_map_states`, and `map_features`.
- [ ] Define local constants for supported map-feature, road-line, track, and traffic-light values instead of importing consumer constants.
- [ ] Record the local-metric coordinate convention, source checksums, Lanelet2 IDs, origin, CRS, inference counts, and converter version.
- [ ] Implement local structural and semantic validation for required keys, allowed value types, array shapes, timeline lengths, finite coordinates, map geometry, route continuity, and traffic-light associations.
- [ ] Generate object and number summaries locally from the final validated scenario rather than copying upstream implementation code.
- [ ] Serialize native dictionaries and NumPy arrays into the scenario pickle, `dataset_summary.pkl`, and `dataset_mapping.pkl` using a documented pickle protocol.
- [ ] Write a handoff manifest containing the local contract version, artifact checksums, Python and NumPy versions, coordinate convention, and consumer compatibility status.

### Completion gate

- [ ] The standalone reader loads the scenario and summary/mapping files back successfully and the local validator accepts them.
- [ ] The dataset contains one synthetic SDC track, no background actors, and the expected number of traffic-light positions.
- [ ] All scenario coordinates are finite, local metric coordinates.
- [ ] The output contains no pickled project-defined class instances; only native supported values and NumPy arrays are serialized.

### Manual verification

1. Run `osm-scenario convert --workspace workspaces/<map-id>` and inspect the
   conversion summary. Confirm that it names the validated Lanelet2 checksum and
   records counts for lanelets, map features, stop lines, traffic lights, skipped
   elements, and conversion warnings.
2. Run `osm-scenario validate-scenario --workspace workspaces/<map-id>` and
   confirm that the local compatibility checks succeed after reading all three
   serialized dataset files back from disk.
3. Inspect the scenario summary and confirm that it contains exactly one track,
   that the track is marked as the synthetic SDC, and that no background vehicle,
   pedestrian, or cyclist tracks exist.
4. Compare a sample of Lanelet2 feature identifiers and traffic-light positions
   against the conversion mapping report. Confirm that coordinates are finite,
   measured in metres, and located near the local origin rather than expressed
   as longitude and latitude.
5. Inspect the handoff manifest and confirm that its checksums match the three
   dataset files and that the compatibility status is `locally_validated` unless
   an optional external consumer check has also passed.

## Stage 6: Optional External Consumer Verification

- [ ] Keep ordinary builds, tests, conversion, and acceptance independent of any external MetaDrive or ScenarioNet installation.
- [ ] Provide an opt-in compatibility procedure that accepts an operator-supplied external consumer command, image, or project path without downloading or modifying it.
- [ ] Mount or copy only the completed dataset into the external consumer; do not expose converter source or use sibling source as a package dependency.
- [ ] Verify external read-back, IDs, summary entries, coordinate ranges, feature counts, topology, traffic-light associations, and route availability.
- [ ] When an external MetaDrive runner is supplied, run a headless scenario smoke test with background traffic disabled and traffic lights enabled.
- [ ] Record the external consumer identity, version or image digest, command, result, and dataset checksums in a separate compatibility report.
- [ ] Defer an owned integration container, interactive `play` command, screenshots, and mandatory MetaDrive execution to a future phase.

### Completion gate

- [ ] V1 acceptance does not require an external consumer to be installed.
- [ ] When the optional check is run, it operates only on the completed dataset and records a reproducible pass or failure without changing the dataset.

### Manual verification

1. Complete Stage 5 with no MetaDrive, ScenarioNet, or `wingfin-metadrive`
   checkout available and confirm that conversion and local validation pass.
2. If an external consumer is available, invoke the documented opt-in check with
   its explicit command, image, or path and the completed workspace.
3. Confirm that the external process receives only the dataset directory, that
   it does not modify any dataset artifact, and that its reported checksums match
   the handoff manifest.
4. Inspect the compatibility report. Confirm that it identifies the external
   consumer precisely and distinguishes `not_run`, `passed`, and `failed` from
   the independent local validation result.

## Stage 7: Tests, Documentation, and Acceptance

- [ ] Add unit fixtures for tag parsing, left/right-hand traffic, projection round trips, deterministic IDs, lane offsets, and signal association.
- [ ] Add geometry fixtures for straight roads, curves, divided roads, merges, T-junctions, four-way junctions, roundabouts, crossings, and incomplete OSM tags.
- [ ] Add cached integration fixtures for local-file and place/bbox acquisition so ordinary tests do not depend on Overpass availability.
- [ ] Add golden Lanelet2 and standalone scenario-dataset fixtures, native validator tests, local compatibility-contract checks, and pickle round-trip tests.
- [ ] Add an opt-in external-consumer test that is skipped when no explicit consumer command, image, or path is supplied.
- [ ] Document exact commands for every stage, JOSM correction steps, artifact meanings, retry behavior, and known OSM inference limitations.
- [ ] Finalize the AI action log with generated-code details and validation results.

### V1 acceptance criteria

- [ ] One local-file map and one downloaded map pass all blocking checks.
- [ ] Both maps survive a Lanelet2 and standalone scenario-dataset round trip.
- [ ] Both produce complete handoff manifests and pass every local compatibility check without external project dependencies.
- [ ] Traffic-light positions and lane associations are preserved, with their state explicitly marked unknown.
- [ ] Re-running the pipeline with the same source and configuration produces stable IDs and equivalent outputs.

### Manual acceptance run

1. Choose one checked-in local `.osm` fixture and one small place or bounding-box
   download. Create a new, empty workspace for each source.
2. Run every CLI stage in order: `fetch`, `generate-lanelet2`, `inspect`,
   `validate-lanelet2`, `convert`, and `validate-scenario`. Retain the
   signed inspection checklist and all JSON and Markdown reports.
3. For both maps, confirm that the final Lanelet2 file reopens in JOSM, the
   standalone dataset passes local read-back validation, and its handoff manifest
   covers all generated dataset artifacts.
4. Compare the traffic-light count, positions, stop points, and controlled-lane
   associations across OSM, Lanelet2, the final dataset, and the conversion
   mapping report. Confirm that state remains explicitly unknown.
5. Delete only the generated output workspace, recreate it from the same raw
   source and versioned configuration, and rerun the pipeline. Confirm that IDs,
   feature counts, checksums for deterministic artifacts, and normalized JSON
   report contents match the first run.

## Assumptions and Defaults

- V1 supports bounded 2D road maps; elevation defaults to zero unless usable elevation data is present.
- OSM is the provenance source, Lanelet2 is the editable map contract, and this project's versioned compatibility model owns the serialized dataset contract.
- Traffic-light geometry and lane association are preserved, but signal timing remains unknown.
- Driving side is inferred when possible and otherwise must be supplied explicitly.
- CommonRoad is not a runtime dependency; the direct OSMnx, GeoPandas, Shapely, and Lanelet2 pipeline matches the intended workflow.
- MetaDrive, ScenarioNet, and `wingfin-metadrive` are not runtime, build, development, Git, editable, or path dependencies.
- External consumer verification is optional in V1 and never changes whether local validation passed.
- An owned MetaDrive integration container and interactive playback are deferred to a future phase.
- Manual JOSM review is a required quality gate because ordinary OSM does not consistently contain lane-level geometry.
- Background vehicles, pedestrians, cyclists, and realistic traffic-light phase generation are outside V1 scope.

## Reference Documentation

- [OSMnx documentation](https://osmnx.readthedocs.io/en/stable/)
- [Lanelet2 repository and architecture](https://github.com/fzi-forschungszentrum-informatik/lanelet2)
- [Lanelet2 JOSM map-editing guide](https://docs.ros.org/en/humble/p/lanelet2_maps/__README.html#editing-lanelet2-maps)
- [Lanelet2 validation](https://docs.ros.org/en/ros2_packages/rolling/api/lanelet2_validation/)
- [Python pickle protocol](https://docs.python.org/3/library/pickle.html)
- [NumPy array serialization compatibility](https://numpy.org/doc/stable/reference/generated/numpy.ndarray.html)
