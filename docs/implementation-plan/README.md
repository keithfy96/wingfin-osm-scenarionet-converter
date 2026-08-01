# OSM to ScenarioNet Implementation Plan

## Summary

Build a direct, reviewable pipeline:

```text
OSM file/place/bbox
    -> OSMnx and GeoPandas
    -> local East-North geometry
    -> preliminary Lanelet2
    -> JOSM correction
    -> Lanelet2 validation
    -> ScenarioNet dataset
    -> MetaDrive smoke test
```

The output contains the static road layout and traffic-light locations. It contains no background traffic. A clearly marked synthetic ego route is added only because ScenarioNet and `ScenarioEnv` require an SDC track for loading and navigation.

## Interfaces and Artifacts

- Provide a top-level `osm-scenario` CLI with `fetch`, `generate-lanelet2`, `inspect`, `validate-lanelet2`, `convert`, `validate-scenario`, and `play` commands.
- Accept exactly one OSM source per run: local `.osm`, place query, or bounding box.
- Store each map in an isolated workspace containing raw input, source manifest, preliminary and edited Lanelet2 maps, validation reports, and ScenarioNet output.
- Use versioned YAML configuration for driving side, coordinate origin, lane-width defaults, tag inference, scenario route, and output duration.
- Emit machine-readable JSON reports plus concise Markdown summaries for inference, geometry, validation, and conversion results.

## Stage 0: Project Foundation

- [ ] Create separate locked `uv` environments for map generation and ScenarioNet conversion. This isolates the local ScenarioNet checkout's `geopandas<1.0` constraint from the current OSMnx geospatial stack.
- [ ] Use Python 3.10 for the ScenarioNet adapter and a supported Python 3.10 or 3.11 environment for OSMnx and Lanelet2.
- [ ] Add typed configuration models, structured logging, deterministic IDs, CLI error handling, pytest, Ruff, and fixture directories.
- [ ] Connect the adapter to the sibling MetaDrive and ScenarioNet checkouts without modifying either repository.
- [ ] Start the required timestamped AI action log when code implementation begins and update it throughout implementation.

### Completion gate

- [ ] Both environments install from lock files on a clean checkout.
- [ ] Every CLI command exposes `--help` and exits with a useful error when required input is missing.

### Manual verification

1. From a clean checkout, run the documented `uv sync --locked` command for
   both environments. Confirm that neither command changes its lock file.
2. Run `osm-scenario --help`, followed by `<command> --help` for every command
   listed under **Interfaces and Artifacts**. Confirm that each command explains
   its required arguments and exits with status `0`.
3. Run each command once without its required source or workspace argument.
   Confirm that it exits nonzero, names the missing argument, and does not emit
   a Python traceback.

## Stage 1: Acquire and Normalize OSM

- [ ] For place or bounding-box input, retrieve an unsimplified driving graph with OSMnx.
- [ ] Preserve lane, direction, turn, width, speed, junction, access, crossing, and signal tags.
- [ ] For local input, load the `.osm` XML through OSMnx's supported XML path and retain the original file unchanged.
- [ ] Save the unsimplified graph, GeoPackage or GeoParquet feature layers, raw `.osm`, and a source manifest.
- [ ] Record checksums, query bounds, acquisition time, OpenStreetMap attribution, and tool versions in the manifest.
- [ ] Resolve driving side from country metadata when possible. Require `--driving-side left|right` when a local file is ambiguous.
- [ ] Use `pyproj` to define a local azimuthal-equidistant East-North frame centered on the map centroid or an explicit origin.
- [ ] Record the full CRS definition and forward/inverse transforms so every downstream coordinate can be traced back to WGS84.
- [ ] Run preflight checks for empty networks, invalid geometries, disconnected components, conflicting direction tags, and missing lane-count data.

### Completion gate

- [ ] The source can be loaded again without network access.
- [ ] WGS84-to-local-to-WGS84 round trips remain within the configured tolerance.
- [ ] Every discarded or inferred OSM feature is listed in the acquisition report.

### Manual verification

1. Run `osm-scenario fetch` with exactly one small local `.osm` fixture and
   inspect `workspaces/<map-id>/source/`. Confirm that `raw.osm` and the source
   manifest exist and that the manifest identifies the local file and checksum.
2. Disconnect from the network or enable the documented offline mode, then
   rerun the normalization step against that workspace. Confirm that it succeeds
   without contacting Overpass or a geocoder.
3. Open the acquisition Markdown summary and its JSON counterpart. Confirm that
   the origin, CRS, bounds, feature counts, inferred tags, discarded features,
   and round-trip projection error agree, and that the error is within the
   configured tolerance.
4. Repeat the source-selection check with `--place` and `--bbox`. Confirm that
   supplying two source options together is rejected before any download starts.

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
- [ ] Block ScenarioNet conversion until `edited.osm` exists or the operator explicitly approves the preliminary map.

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

## Stage 5: Convert Lanelet2 to ScenarioNet

- [ ] Convert lane centerlines, boundaries, crossings, speed limits, entry/exit links, and left/right neighbors into MetaDrive `map_features`.
- [ ] Preserve traffic-light stop positions and controlled-lane IDs in `dynamic_map_states`.
- [ ] Fill each traffic-light timeline with `TRAFFIC_LIGHT_UNKNOWN`, leaving phase generation to later MetaDrive code.
- [ ] Add one synthetic SDC navigation track sampled along a valid route.
- [ ] Allow explicit start and goal lanelets; otherwise select a deterministic long route in the largest drivable component.
- [ ] Mark the track as synthetic layout scaffolding in metadata and emit no other tracks.
- [ ] Populate the seven required `ScenarioDescription` sections: `id`, `version`, `length`, `metadata`, `tracks`, `dynamic_map_states`, and `map_features`.
- [ ] Record the local-metric coordinate convention, source checksums, Lanelet2 IDs, origin, CRS, inference counts, and converter version.
- [ ] Run `ScenarioDescription.sanity_check()`.
- [ ] Use ScenarioNet's dataset-writing utilities to create the scenario pickle, `dataset_summary.pkl`, and `dataset_mapping.pkl`.

### Completion gate

- [ ] ScenarioNet reads the dataset and its summary/mapping files back successfully.
- [ ] The dataset contains one synthetic SDC track, no background actors, and the expected number of traffic-light positions.
- [ ] All scenario coordinates are finite, local metric coordinates.

### Manual verification

1. Run `osm-scenario convert --workspace workspaces/<map-id>` and inspect the
   conversion summary. Confirm that it names the validated Lanelet2 checksum and
   records counts for lanelets, map features, stop lines, traffic lights, skipped
   elements, and conversion warnings.
2. Run `osm-scenario validate-scenario --workspace workspaces/<map-id>` and
   confirm that ScenarioNet's schema check succeeds after reading the serialized
   dataset back from disk.
3. Inspect the scenario summary and confirm that it contains exactly one track,
   that the track is marked as the synthetic SDC, and that no background vehicle,
   pedestrian, or cyclist tracks exist.
4. Compare a sample of Lanelet2 feature identifiers and traffic-light positions
   against the conversion mapping report. Confirm that coordinates are finite,
   measured in metres, and located near the local origin rather than expressed
   as longitude and latitude.

## Stage 6: MetaDrive Verification

- [ ] Reload the generated dataset through ScenarioNet and MetaDrive APIs.
- [ ] Verify IDs, summary entries, coordinate ranges, feature counts, topology, and traffic-light associations.
- [ ] Run a headless `ScenarioEnv` smoke test with background traffic disabled, traffic lights enabled, and a controllable ego agent.
- [ ] Reset successfully, follow the generated navigation route, and step for a fixed horizon.
- [ ] Fail on missing roads, invalid navigation, NaNs, immediate off-road termination, or loader exceptions.
- [ ] Provide `osm-scenario play` for interactive visual inspection before the map is accepted.
- [ ] Record screenshots and a validation summary containing lanelet, intersection, signal, route, and runtime totals.

### Completion gate

- [ ] The headless smoke test completes its configured horizon.
- [ ] The interactive view shows the corrected road layout and traffic-light positions in their expected locations.

### Manual verification

1. Run the documented headless `osm-scenario play` command and confirm that it
   exits successfully only after the configured scenario duration has elapsed.
2. Run `osm-scenario play --interactive --workspace workspaces/<map-id>` and
   compare the rendered map with the approved JOSM map.
3. Drive or advance the synthetic ego route through representative straight
   roads, curves, and intersections. Confirm that the route remains on connected
   lanes and uses the expected driving side.
4. Inspect every signalized intersection. Confirm that traffic lights and stop
   points appear at their expected approaches and that their state is reported
   as unknown rather than showing invented signal timing.

## Stage 7: Tests, Documentation, and Acceptance

- [ ] Add unit fixtures for tag parsing, left/right-hand traffic, projection round trips, deterministic IDs, lane offsets, and signal association.
- [ ] Add geometry fixtures for straight roads, curves, divided roads, merges, T-junctions, four-way junctions, roundabouts, crossings, and incomplete OSM tags.
- [ ] Add cached integration fixtures for local-file and place/bbox acquisition so ordinary tests do not depend on Overpass availability.
- [ ] Add golden Lanelet2 and ScenarioNet fixtures, native validator tests, schema checks, and a headless MetaDrive end-to-end test.
- [ ] Document exact commands for every stage, JOSM correction steps, artifact meanings, retry behavior, and known OSM inference limitations.
- [ ] Finalize the AI action log with generated-code details and validation results.

### V1 acceptance criteria

- [ ] One local-file map and one downloaded map pass all blocking checks.
- [ ] Both maps survive a Lanelet2 and ScenarioNet round trip.
- [ ] Both load as controllable layouts in MetaDrive.
- [ ] Traffic-light positions and lane associations are preserved, with their state explicitly marked unknown.
- [ ] Re-running the pipeline with the same source and configuration produces stable IDs and equivalent outputs.

### Manual acceptance run

1. Choose one checked-in local `.osm` fixture and one small place or bounding-box
   download. Create a new, empty workspace for each source.
2. Run every CLI stage in order: `fetch`, `generate-lanelet2`, `inspect`,
   `validate-lanelet2`, `convert`, `validate-scenario`, and `play`. Retain the
   signed inspection checklist and all JSON and Markdown reports.
3. For both maps, confirm that the final Lanelet2 file reopens in JOSM, the
   ScenarioNet dataset passes read-back validation, and MetaDrive completes both
   the headless and interactive checks.
4. Compare the traffic-light count, positions, stop points, and controlled-lane
   associations across OSM, Lanelet2, the conversion mapping report, and the
   MetaDrive view. Confirm that state remains explicitly unknown.
5. Delete only the generated output workspace, recreate it from the same raw
   source and versioned configuration, and rerun the pipeline. Confirm that IDs,
   feature counts, checksums for deterministic artifacts, and normalized JSON
   report contents match the first run.

## Assumptions and Defaults

- V1 supports bounded 2D road maps; elevation defaults to zero unless usable elevation data is present.
- OSM is the provenance source, Lanelet2 is the editable map contract, and ScenarioNet is the serialized dataset contract.
- Traffic-light geometry and lane association are preserved, but signal timing remains unknown.
- Driving side is inferred when possible and otherwise must be supplied explicitly.
- CommonRoad is not a runtime dependency; the direct OSMnx, GeoPandas, Shapely, and Lanelet2 pipeline matches the intended workflow.
- Manual JOSM review is a required quality gate because ordinary OSM does not consistently contain lane-level geometry.
- Background vehicles, pedestrians, cyclists, and realistic traffic-light phase generation are outside V1 scope.

## Reference Documentation

- [OSMnx documentation](https://osmnx.readthedocs.io/en/stable/)
- [Lanelet2 repository and architecture](https://github.com/fzi-forschungszentrum-informatik/lanelet2)
- [Lanelet2 JOSM map-editing guide](https://docs.ros.org/en/humble/p/lanelet2_maps/__README.html#editing-lanelet2-maps)
- [Lanelet2 validation](https://docs.ros.org/en/ros2_packages/rolling/api/lanelet2_validation/)
- Local ScenarioNet checkout: `/home/keith/Desktop/work/wingfin/scenarionet`
- Local MetaDrive checkout: `/home/keith/Desktop/work/wingfin/metadrive`
