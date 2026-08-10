# Direct OSM-to-ScenarioNet Pipeline

## Summary

Continue from the completed Stage 1-only workspace and keep Lanelet2 out of the
active pipeline. Generate a preliminary lane-level map directly, review its
assumptions through a dedicated browser review application, materialize
OSM-compatible corrections into a reviewed OSM copy, retain non-OSM decisions
in JSON, validate the reviewed map, and convert it to a map-only ScenarioNet
dataset.

```text
Completed Stage 1
  -> Stage 2: automatic lane-geometry and connectivity generation
  -> Stage 3: manual decisions in the audit HTML
  -> Stage 4: automatic decision application and geometry regeneration
  -> Stage 5: map validation
  -> Stage 6: ScenarioNet conversion and isolated MetaDrive validation
```

The primary manual stage belongs after preliminary generation because the audit
can then show both source evidence and the resulting lanes, polygons,
connectors, and associations.

Stage 3 does not require the reviewer to draw or directly edit geometry. The
reviewer inspects the generated result and records structured decisions in the
audit HTML. The audit exports those decisions as `review.json`; Stage 4 then
applies them automatically, regenerates the geometry, and produces
`review/reviewed.osm` plus the reviewed lane model.

## Progress, Outputs, and Verification

Checkboxes represent the current repository state. A checked stage means its
stage-level exit criteria are complete, not merely that implementation has
started. Partially implemented stages keep their main checkbox unchecked and
show completed increments beneath it.

- [x] **Stage 1 - Acquire, normalize, and audit the source OSM**
  - Outputs:
    - `source/map.osm` - immutable OSM acquisition evidence.
    - `source/manifest.json` - source and artifact checksums, selection policy,
      projection metadata, and Stage 1 status.
    - `normalized/road-network.graphml` and
      `normalized/road-network-local.graphml` - selected directed road graph in
      geographic and projected coordinates.
    - `normalized/road-network.gpkg` and
      `normalized/road-network-local.gpkg` - inspection derivatives.
    - `reports/acquisition.json`, `reports/acquisition.md`,
      `reports/stage-1b-data-audit.json`, and
      `reports/stage-1b-data-audit.md` - machine-readable and human-readable
      preflight/audit results.
    - Stage 1 HTML views under `inspection/`, generated on request.
  - Verify:
    1. Run `osm-scenario fetch --osm-file INPUT.osm --workspace WORKSPACE
       --driving-side left` (or use `right`; `--place` and `--bbox` are also
       supported acquisition sources).
    2. Confirm `source/manifest.json` contains `stage_1b.status: "passed"` and
       that `reports/acquisition.json` contains `status: "passed"`.
    3. Run `osm-scenario inspect --workspace WORKSPACE --view stage-1` and open
       the reported HTML path.
    4. Run `uv run pytest -q tests/unit/test_cli.py
       tests/unit/test_normalization.py tests/unit/test_inspection.py` from the
       repository when verifying the implementation itself.

- [x] **Stage 2 - Generate the preliminary lane model**
  - [x] Versioned, JSON-safe lane-model schemas and stable string identifiers.
  - [x] `generate-map` CLI command and Stage 1 checksum/status gates.
  - [x] Segment lane centerlines, polygons, boundaries, neighbors, speeds,
    turn-lane values, and preliminary entry/exit references.
  - [x] Generation fingerprints, evidence checksums, manifest records, findings,
    a JSON report, and a read-only preliminary review map.
  - [x] Static signal candidates and restriction review findings.
  - [x] Topology-aware intersection connector geometry and lane mapping.
  - [x] Deterministic node-via and proof-gated via-way restriction enforcement.
  - [x] Signal-to-approaching-lane association and inferred stop-line candidates.
  - [x] Stage 2 topology and traffic-control fixture coverage.
  - Outputs:
    - `lane-model/preliminary.json` - deterministic projected lane model and
      review findings.
    - `reports/lane-model-generation.json` - generation status, versions,
      checksums, fingerprint, and feature counts.
    - `inspection/stage-2-review-audit.html` - the single read-only inspection
      artifact: lane, connector, restriction-status, and stop-line preview on an
      OSM basemap, plus a searchable and filterable finding-to-geometry audit
      where clicking a finding focuses and highlights its affected generated
      features. Stage 3 will provide authoritative decision capture.
    - `source/manifest.json` updated with the `stage_2` generation record and
      output checksums.
  - Verify:
    1. Run `osm-scenario generate-map --workspace WORKSPACE` after Stage 1
       passes. Supply `--config config/default.yaml` when an explicit checked-in
       configuration is desired.
    2. Confirm the command reports `Stage 2 complete` and
       `reports/lane-model-generation.json` contains `status: "passed"`.
    3. Confirm the report fingerprint matches
       `lane-model/preliminary.json.metadata.generation_fingerprint` and
       `source/manifest.json.stage_2.generation_fingerprint`.
    4. Open `inspection/stage-2-review-audit.html` and visually check lane
       centerlines, polygons, connector colors/statuses, and stop lines. This
       preview does not record decisions; manual decisions start in Stage 3.
    5. Run `uv run pytest -q tests/unit/test_generation.py
       tests/unit/test_topology.py`; run
       `uv run pytest -q` and `uv run ruff check .` for the full regression and
       lint gates.
  - Completed: ambiguous movements are emitted as `review_required`, forbidden
    movements are retained as inspectable evidence but excluded from active
    lane links, and the topology/restriction/signal fixtures pass.

- [ ] **Stage 3 - Record manual review decisions**
  - Outputs:
    - `inspection/stage-3-review.html` generated as the stateful review
      application through `inspect --view review`.
    - Browser-local autosave draft keyed by source checksum and generation
      fingerprint; this is recoverable working state, not authoritative output.
    - An explicitly exported `review.json` submission containing every decision,
      evidence checksum, provenance field, and readiness state.
  - Verify after implementation:
    1. Run `osm-scenario inspect --workspace WORKSPACE --view review` and open
       the reported single-file HTML.
    2. Make a decision, reload the page, and confirm the non-authoritative draft
       restores; then reset it and confirm the draft is removed.
    3. Export `review.json`, reload it, and confirm the decisions round-trip
       without changing finding IDs or evidence checksums.
    4. Confirm export/promotion readiness is blocked while any blocking finding
       is `unresolved`.
    5. Change the Stage 2 generation fingerprint and confirm stale review data
       is rejected or explicitly migrated only where both the finding ID and
       evidence checksum still match.

- [ ] **Stage 4 - Apply decisions and regenerate automatically**
  - Implemented so far: the spine, plus two of the tag writes. Decisions that
    resolve a movement are applied and the model is regenerated from them;
    `lane_count_inference` writes `lanes`/`lanes:<direction>` and
    `turn_permission_geometry_conflict` writes `turn:lanes`. Every **other**
    decision whose effect is an OSM tag is **refused by name**, not half-applied
    - see `_OSM_NATIVE_RULES` in `src/osm_scenario/apply_review.py`. A review
    that moves no tag still leaves `review/reviewed.osm` a byte copy of the
    source.
  - Outputs:
    - `review/reviewed.osm` - OSM-native reviewed corrections, leaving
      `source/map.osm` unchanged. The source is checksummed before and after
      every run and is never written.
    - `review/applied-decisions.json` - authoritative decisions and non-OSM
      overrides. **Deliberately not** `review/review.json`, which this document
      previously named: that is one path segment away from the hand-made Stage 3
      export that is its input, and two files a directory apart with the same
      name is a mistake waiting to be made.
    - `lane-model/reviewed.json` - fully regenerated reviewed lane model, built
      by the same `build_lane_model` core Stage 2 uses, over the reviewed OSM and
      in Stage 1B's pinned coordinate frame.
    - `reports/reviewed-comparison.json` / `.md` and
      `inspection/stage-4-comparison.html` - preliminary versus reviewed, plus a
      `stage_4` record in `source/manifest.json`.
  - Two properties the implementation must keep:
    - The reviewed graph goes through **public-driving-v1 road selection**, as
      Stage 1A does. Rebuilding from OSM without it readmits every excluded way.
    - The reviewed model is keyed on `sha256(review/reviewed.osm)`, not on a
      rebuilt GraphML - osmnx stamps a build timestamp into GraphML, which would
      mint a new fingerprint for a byte-identical model on every run.
  - Verify:
    1. Record the checksum of `source/map.osm`.
    2. Run `osm-scenario apply-review --workspace WORKSPACE --submission
       EXPORTED_REVIEW.json`.
    3. Confirm all four output groups above exist and validate against their
       versioned schemas.
    4. Confirm the original source checksum is unchanged and reviewed geometry
       was regenerated rather than patched directly in `preliminary.json`.
    5. Confirm stale fingerprints, invalid references, and unresolved blockers
       cause a non-zero command exit.
    6. Run it a second time and confirm every output is byte-identical apart
       from `applied_at`.

- [ ] **Stage 5 - Validate the reviewed map**
  - Outputs:
    - JSON and Markdown map-validation reports under `reports/`, containing
      direct OSM and generated-feature identifiers for every issue.
    - A validation status/checksum recorded for the exact reviewed lane model.
  - Verify after implementation:
    1. Run `osm-scenario validate-map --workspace WORKSPACE`.
    2. Confirm the validation report status is `passed`, references the checksum
       of `lane-model/reviewed.json`, and has no unresolved errors or warnings.
    3. Open the reviewed comparison audit and inspect representative ordinary
       roads, merges, forks, roundabouts, and intersections.
    4. Run the negative fixtures and confirm invalid polygons, dangling links,
       forbidden movements, stale reviews, and unassociated signals fail.

- [ ] **Stage 6 - Convert and validate the ScenarioNet dataset**
  - Conversion is implemented; isolated ScenarioNet/MetaDrive validation is not.
    See the Stage 6 implementation section for what has landed.
  - Outputs:
    - `scenario.pkl` - map-only ScenarioNet scenario.
    - `dataset_summary.pkl` and `dataset_mapping.pkl` - ScenarioNet dataset
      indices.
    - Conversion/isolated-validation provenance identifying the reviewed model
      checksum.
  - Verify after implementation:
    1. Run `osm-scenario convert --workspace WORKSPACE` only after Stage 5
       passes.
    2. Load the three pickle files in the lockfile-pinned isolated ScenarioNet
       environment and run `ScenarioDescription.sanity_check()`.
    3. Confirm `tracks == {}` and `dynamic_map_states == {}` and that map
       features preserve lane geometry, topology, speed, boundaries, source OSM
       IDs, and review provenance.
    4. Load the dataset in MetaDrive, render the map, and verify routing crosses
       representative reviewed intersections.

## Implementation Changes

### Stage 2 - Automatic Lane-Geometry and Connectivity Generation

- [x] **Stage 2 exit criteria complete.** See the Stage 2 progress checklist and
  verification procedure above.

The implemented decision rules, evidence hierarchy, connector/continuation
boundary, lane mapping, U-turn handling, restrictions, signals, and Stage 2/3/4
ownership are documented in
[`stage-2-generation-v1`](../policies/stage-2-generation-v1.md). These rules run
during Stage 2; Stage 3 records manual decisions and Stage 4 applies them during
automatic regeneration.

- Add `osm-scenario generate-map --workspace ...`.
- Verify the Stage 1 manifest and checksums, then read the immutable source OSM
  and projected directed graph.
- Generate individual lane centerlines, polygons, boundaries, neighbors,
  intersection connectors, entry/exit links, speed limits, turn permissions,
  restriction effects, signal associations, and stop-line candidates.
- Reuse `ids.deterministic_id()` with semantic namespaces for every generated
  feature and finding ID. All IDs remain strings through Python, JSON, and the
  browser.
- Prefer explicit OSM tags. Every fallback or ambiguous result becomes a review
  finding containing source IDs, proposed value, confidence, reason, and
  affected generated features.
- Build connectors from graph topology, lane ordering, turn tags, geometry, and
  valid restrictions. Never silently select an ambiguous movement.
- Use `legacy-stage2-implementation` only as an algorithmic reference for
  connector-angle and restriction-handling behavior. Do not copy its Lanelet2
  representation, dependencies, or artifact formats into the active pipeline.
- Extend the Stage 1 manifest/checksum convention with a Stage 2 generation
  record containing the generator version, lane-model schema version, input and
  configuration checksums, output checksums, and a derived generation
  fingerprint. Increment the generator version whenever geometry or topology
  semantics change.
- Give every review finding an evidence checksum covering its source evidence
  and generated proposal so review decisions can be invalidated or migrated
  safely when generation changes.
- Write:
  - `lane-model/preliminary.json`
  - `reports/lane-model-generation.json`
  - `inspection/stage-2-review-audit.html`
- Keep traffic signals as static lane associations only. Do not generate
  timing, actors, or traffic-light state sequences.

### Stage 3 - Stateful Manual Review Application

- [ ] **Stage 3 exit criteria complete.**

Build a stateful review application using the existing Stage 1 audit viewer as
its visual foundation. This is the largest engineering stage in the plan, not
an incremental extension of the current static, read-only HTML.

This is the manual decision stage, not a manual geometry-editing stage. The
reviewer selects or overrides interpretations through structured audit
controls; the reviewer does not draw lanes, polygons, connectors, or stop-line
geometry.

Deliver Stage 3 in three internal milestones:

1. Review UI foundation and generated-map overlays.
2. Per-finding state, structured controls, bulk actions, and readiness rules.
3. Draft persistence, import/export, checksum binding, and stale-review
   migration.

- Preserve existing source layers, OSM Way/Node search, restriction evidence,
  crossings, signals, missing tags, and direction warnings.
- Extend the existing CLI as
  `osm-scenario inspect --workspace ... --view review`. Keep the Stage 1
  `audit` view read-only and do not add a parallel top-level review command.
- Implement the client with framework-free TypeScript and esbuild. Move the
  HTML, CSS, application state, and rendering logic out of Python f-strings;
  package the compiled bundle and embed it into the generated single-file HTML.
  Node tooling is build-time only and is not required by the installed CLI.
- Add overlays for generated centerlines, polygons, connector lanes,
  allowed/forbidden movements, stop lines, and signal associations.
- Clicking a finding or lane opens its source evidence, generated result,
  proposed interpretation, and structured controls.
- Supported decisions include:
  - Lane and directional lane counts.
  - Width and speed limit.
  - `oneway` and turn-lane values.
  - Accepting or overriding proposed turns.
  - Keeping, correcting, or removing restriction relations.
  - Accepting or rejecting connector candidates.
  - Selecting a signal-to-lane association.
  - Accepting an inferred stop line.
  - Marking a finding not applicable, with a required reason.
- Allow bulk decisions only for findings sharing the same rule and road class;
  still serialize each affected source feature explicitly.
- Autosave drafts after decision changes to `localStorage`, keyed by workspace
  identity, source checksum, and generation fingerprint. Show save/restore
  status and provide an explicit draft-reset action so an accidental tab close
  does not lose in-progress work.
- Treat browser-local drafts as non-authoritative. Only an explicitly exported
  `review.json` can be passed to Stage 4.
- Support loading a prior review JSON and exporting a checksum-bound
  submission. When the generation fingerprint changes, never restore or accept
  the old review silently. Offer explicit migration only for decisions whose
  finding ID and evidence checksum are unchanged; changed and new findings
  return to `unresolved`, and the UI shows a migration summary before export.
- Blocking findings must end as `accepted`, `overridden`, or
  `not_applicable`; `unresolved` prevents promotion. The UI reports readiness,
  while Stage 4 remains the authoritative promotion gate.
- A warning may also end as `ignored`: set aside unjudged, with the generated
  proposal standing. It is deliberately not `accepted` — an accepted finding
  records a judgement, an ignored one records that none was made, and the two
  must stay distinguishable for anything scored against a reviewer's decisions.
  **`ignored` is permitted on warnings only, and Stage 4 must reject a
  submission carrying it on a blocking finding**, exactly as it rejects
  `unresolved` there. Submissions containing it carry `submission_version: 3`.

### Stage 4 - Materialize Decisions and Regenerate

- [ ] **Stage 4 exit criteria complete.**

Add `osm-scenario apply-review --workspace ... --submission ...`.

This stage is automatic. It consumes the `review.json` exported by Stage 3,
applies the review decisions, regenerates all affected geometry, and produces
`review/reviewed.osm` and the reviewed lane model without requiring manual
geometry edits.

- Validate the submission schema, string identifiers, source checksums,
  generation fingerprint, finding evidence checksums, allowed decision types,
  referenced OSM/generated features, and absence of unresolved blockers.
- Preserve `source/map.osm` unchanged.
- Write OSM-native decisions into `review/reviewed.osm`, including applicable
  changes to lane counts, widths, speeds, direction, turn tags, signal tags,
  and restriction relations.
- Write `review/review.json` as the complete audit record. Non-OSM
  requirements--connector selection, signal-to-lane association, inferred
  stop-line placement, and review justification--remain active overrides in
  this file.
- Rebuild the directed graph when reviewed OSM changes affect direction or
  topology by reusing or extracting the graph-building and projection routines
  in `normalization.py`. Use `review/reviewed.osm` with the locked Stage 1
  selection policy, projection origin, and CRS; do not create a second graph
  normalization implementation or overwrite Stage 1 normalized artifacts.
- Regenerate `lane-model/reviewed.json` from `reviewed.osm` plus the non-OSM
  overrides; never directly patch preliminary geometry.
- Produce a comparison audit showing preliminary versus reviewed output. Final
  approval must reference the reviewed model checksum, and conversion remains
  blocked until it is approved.

### Stage 5 - Validate the Reviewed Map

- [ ] **Stage 5 exit criteria complete.**

Add `osm-scenario validate-map --workspace ...`.

Validation must reject:

- Invalid, empty, self-intersecting, or non-finite lane geometry.
- Centerlines outside their lane polygons.
- Dangling or non-reciprocal entry/exit and neighbor references.
- Connector endpoints that do not meet their incoming and outgoing lanes.
- Movements that violate reviewed restrictions or turn permissions.
- Unresolved blocking findings or stale review checksums.
- Unassociated reviewed signals or invalid stop-line associations.
- Drivable lanes without a positive resolved width and speed policy.
- Unexpected isolated lanes or routing components.

Write JSON and Markdown validation reports with direct source and
generated-feature identifiers. Warnings require an explicit review disposition
before approval.

### Stage 6 - Convert to a Map-Only ScenarioNet Dataset

- [ ] **Stage 6 exit criteria complete.**
  - [x] **`osm-scenario convert --workspace ...` implemented**
    (`src/osm_scenario/conversion.py`). Gated on `stage_5.status == "passed"`
    and the reviewed model's checksum. Writes `sd_<dataset>_<version>_<id>.pkl`,
    `dataset_summary.pkl` and `dataset_mapping.pkl` under
    `<workspace>/scenarionet/`, plus `reports/scenario-conversion.json` and a
    `stage_6` manifest record.
  - [x] **`ScenarioDescription.sanity_check()` passes**, and the dataset satisfies
    every assertion `read_dataset_summary` makes. Both are run against MetaDrive
    0.4.3's own source, not a reading of it — see the schema note below.
  - [x] **`inspection/stage-6-reachability.html`** — pick any lane and see every
    lane a car can reach from it, coloured by how many lanes it has to cross,
    and the same search run backwards. Written by `convert` from the same
    resolved graph the dataset is built from, so the page cannot show a network
    the pickle does not contain. It is what turns `metadata.routing`'s one
    number into a route you can pick before spending GPU time on it.
  - [ ] **Not yet loaded in MetaDrive itself.** Nothing has been rendered, and no
    route has been driven. That needs panda3d and a GPU, so it belongs in the
    lockfile-pinned isolated environment rather than here.

- Convert the reviewed model directly into `ScenarioDescription.map_features`.
- Include centerline, polygon, type, speed, boundaries, neighbors, and
  entry/exit relationships.
- Export map-only data with no fabricated vehicles or traffic-light timing:
  `tracks={}`, `dynamic_map_states={}`, and a minimal one-step scenario
  envelope.
- Preserve source OSM IDs and review provenance in metadata.
- Write `scenario.pkl`, `dataset_summary.pkl`, and `dataset_mapping.pkl`.
- Keep MetaDrive/ScenarioNet out of the converter's core dependencies. Use a
  lockfile-pinned isolated validation environment to run
  `ScenarioDescription.sanity_check()`, reload the serialized dataset,
  construct the MetaDrive map, and verify a route across representative
  junctions.

Two things the implemented conversion decided that the list above does not say:

- **`entry_lanes` / `exit_lanes` are resolved to lane ids.** The lane model
  records a junction movement as a *connector* id and a continuation as a lane
  id, and ScenarioNet accepts only the latter. Non-`active` connectors are
  dropped rather than followed, so a movement the review forbade cannot
  reappear as a drivable edge.
- **The dataset carries reachability, not just `routing_components`.** Stage 5's
  component sizes use *weakly* connected components, which ignore one-way
  direction: `junction-1` is 6 pieces weakly and 274 strongly, and only 8% of
  lane-to-lane journeys exist. `metadata.routing` names the best starting lane
  and how far it reaches, which is the number step 4 above actually needs. The
  Stage 6 page draws it, because the number alone still misleads: 285 lanes are
  joined by 294 edges, so the best lane's 79 is a thread twelve steps long
  before it branches at all, not a network.

**The schema is pinned against MetaDrive's own source, without depending on it.**
`test_the_scenario_passes_metadrives_own_sanity_check` loads
`metadrive/scenario/scenario_description.py` by file path from a checkout and runs
the real `ScenarioDescription.sanity_check()`; it skips where no checkout exists.
MetaDrive's `__init__` needs panda3d, so the test registers bare package modules
and supplies `metadrive.utils.math.norm` directly - no install, and MetaDrive
stays out of this converter's dependencies as required above.

Three things measurement caught that a reading of the format would not:

- `metadata` must carry `metadrive_processed` (`METADATA_KEYS`), and `ts` must be
  an array whose shape equals `length` - `sanity_check` reads `.shape` on it.
- The **filename** is validated. `read_dataset_summary` asserts `is_scenario_file`
  on every summary entry, which accepts only `sd_*` or an all-digits name.
  `conversion.scenario_file_name` builds it the way MetaDrive's
  `get_export_file_name` does, and both index files key on that one string.
- Neighbours are lists, not bare ids, matching every ScenarioNet converter.
  MetaDrive 0.4.3 stores and never reads them, so this is convention only.

Equally worth recording, because they look wrong beside Waymo's converted data and
are not: two-point lane polylines load (MetaDrive skips a lane only at
`len(polyline) <= 1`), `ROAD_EDGE_BOUNDARY` is exactly
`MetaDriveType.BOUNDARY_LINE`, and entry/exit ids that resolve against
`map_features` are stricter than Waymo's own, whose ids do not resolve at all.

## Interfaces and Artifacts

- Version the preliminary/reviewed lane-model schema and review-submission
  schema with Pydantic.
- Reuse `ids.deterministic_id()` and stable string IDs throughout, including
  browser JSON, to avoid large OSM ID rounding.
- Record the source checksum, generation fingerprint, preliminary-model
  checksum, finding/evidence checksums, decisions, migration provenance, and
  approval state in `review.json`.
- Keep the CLI surface small: add `review` as an `inspect --view` value while
  retaining `generate-map`, `apply-review`, `validate-map`, and `convert` as
  the planned pipeline commands.
- Treat these as the final source-of-truth hierarchy:
  1. `source/map.osm` - immutable acquisition evidence.
  2. `review/reviewed.osm` - reviewed OSM-native corrections.
  3. `review/review.json` - non-OSM requirements, dispositions, provenance,
     and approval.
  4. `lane-model/reviewed.json` - generated derivative.
  5. ScenarioNet pickle files - serialized output.
- Do not reintroduce Lanelet2 commands, dependencies, artifacts, validators, or
  representation types into the active pipeline.
- Use TypeScript and esbuild for the packaged review bundle, Vitest for client
  state and serialization tests, and a browser smoke test for the generated
  review HTML.
- During implementation, create the required timestamped AI action log with
  its Generated Code Details section.

## Test and Acceptance Plan

- Unit-test OSM tag precedence, directional lane counts, widths, speeds, turns,
  stable IDs, geometry offsets, and polygon construction.
- Cover one-way roads, bidirectional roads, merges, forks, T-junctions,
  four-way intersections, divided roads, and roundabouts.
- Test node-via and via-way restrictions, incomplete relations, contradictory
  tags, ambiguous connectors, signals, and mapped/inferred stop lines.
- Test audit JSON export/import, OSM materialization, non-OSM overrides, stale
  checksums, invalid references, required reasons, and identifiers larger than
  JavaScript's safe integer range.
- Verify generated feature and finding IDs use `deterministic_id()` and remain
  stable for identical semantic inputs.
- Verify source, configuration, generator-version, or generated-output changes
  produce a different generation fingerprint and stale submissions are
  rejected by `apply-review`.
- Verify draft autosave/restore, checksum-key isolation, explicit reset,
  export/import round trips, bulk-decision expansion, required reasons, and the
  unresolved-blocker promotion gate.
- Verify stale-review migration retains only decisions with matching finding
  IDs and evidence checksums; changed and new findings become unresolved.
- Verify `inspect --view review` produces a working single-file page and does
  not change the existing inspection views.
- Verify Stage 4 uses the shared normalization path and leaves the Stage 1 OSM,
  graph, and inspection artifacts unchanged.
- Verify identical inputs and reviews generate byte-equivalent logical models
  and stable IDs.
- Verify negative validation cases fail before conversion.
- Run the isolated ScenarioNet/MetaDrive check and confirm the map loads,
  polygons render, and routing crosses reviewed intersections.
- Perform a real-workspace acceptance review using the audit HTML; confirm
  every blocker is resolved, the reviewed comparison is approved, and the
  original Stage 1 source checksum remains unchanged.

## Assumptions

- Review is manual; geometry generation and correction are deterministic and
  automatic.
- The HTML is a structured evidence-and-decision interface, not a vertex
  editor.
- The review application is generated rather than hosted. Its compiled client
  bundle ships with the Python package, so reviewers do not need Node.
- `localStorage` protects in-progress work but is never authoritative;
  `review.json` is the only review artifact accepted by Stage 4.
- Missing source facts may use configured preliminary defaults, but every
  material inference remains visible and reviewable.
- Source restrictions are never automatically deleted. Only deterministic
  topology may mark a restriction already satisfied; incomplete or
  contradictory evidence remains review-required.
- V1 produces a valid static map only. Vehicles, actors, traffic-light cycles,
  and scenario timelines are intentionally deferred.
