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

## Implementation Changes

### Stage 2 - Automatic Lane-Geometry and Connectivity Generation

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
  - `inspection/stage-2-map-review.html`
- Keep traffic signals as static lane associations only. Do not generate
  timing, actors, or traffic-light state sequences.

### Stage 3 - Stateful Manual Review Application

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

### Stage 4 - Materialize Decisions and Regenerate

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

Add `osm-scenario convert --workspace ...`.

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
