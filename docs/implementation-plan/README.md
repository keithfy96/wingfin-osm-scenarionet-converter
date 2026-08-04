# Direct OSM-to-ScenarioNet Pipeline

## Summary

Continue from the completed Stage 1 workspace and remove Lanelet2 entirely.
Generate a preliminary lane-level map directly, review its assumptions through
an expanded audit HTML, materialize OSM-compatible corrections into a reviewed
OSM copy, retain non-OSM decisions in JSON, validate the reviewed map, and
convert it to a map-only ScenarioNet dataset.

```text
Completed Stage 1
  -> Stage 2: automatic preliminary lane-model generation
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

### Stage 2 - Generate the Preliminary Lane Model

- Add `osm-scenario generate-map --workspace ...`.
- Verify the Stage 1 manifest and checksums, then read the immutable source OSM
  and projected directed graph.
- Generate deterministic string IDs, individual lane centerlines, polygons,
  boundaries, neighbors, intersection connectors, entry/exit links, speed
  limits, turn permissions, restriction effects, signal associations, and
  stop-line candidates.
- Prefer explicit OSM tags. Every fallback or ambiguous result becomes a review
  finding containing source IDs, proposed value, confidence, reason, and
  affected generated features.
- Build connectors from graph topology, lane ordering, turn tags, geometry, and
  valid restrictions. Never silently select an ambiguous movement.
- Write:
  - `lane-model/preliminary.json`
  - `reports/lane-model-generation.json`
  - `inspection/stage-2-map-review.html`
- Keep traffic signals as static lane associations only. Do not generate
  timing, actors, or traffic-light state sequences.

### Stage 3 - Manual Review Through the Audit HTML

Extend the existing Stage 1 audit instead of building a general geometry
editor.

This is the manual decision stage, not a manual geometry-editing stage. The
reviewer selects or overrides interpretations through structured audit
controls; the reviewer does not draw lanes, polygons, connectors, or stop-line
geometry.

- Preserve existing source layers, OSM Way/Node search, restriction evidence,
  crossings, signals, missing tags, and direction warnings.
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
- Support loading a prior review JSON to resume work and exporting a
  checksum-bound submission. No browser-local state is authoritative.
- Blocking findings must end as `accepted`, `overridden`, or
  `not_applicable`; `unresolved` prevents promotion.

### Stage 4 - Materialize Decisions and Regenerate

Add `osm-scenario apply-review --workspace ... --submission ...`.

This stage is automatic. It consumes the `review.json` exported by Stage 3,
applies the review decisions, regenerates all affected geometry, and produces
`review/reviewed.osm` and the reviewed lane model without requiring manual
geometry edits.

- Validate the submission schema, string identifiers, source checksums,
  allowed decision types, and referenced OSM/generated features.
- Preserve `source/map.osm` unchanged.
- Write OSM-native decisions into `review/reviewed.osm`, including applicable
  changes to lane counts, widths, speeds, direction, turn tags, signal tags,
  and restriction relations.
- Write `review/review.json` as the complete audit record. Non-OSM
  requirements--connector selection, signal-to-lane association, inferred
  stop-line placement, and review justification--remain active overrides in
  this file.
- Rebuild the directed graph when reviewed OSM changes affect direction or
  topology, using the locked Stage 1 selection policy, projection origin, and
  CRS.
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
- Use stable string IDs throughout, including browser JSON, to avoid large OSM
  ID rounding.
- Treat these as the final source-of-truth hierarchy:
  1. `source/map.osm` - immutable acquisition evidence.
  2. `review/reviewed.osm` - reviewed OSM-native corrections.
  3. `review/review.json` - non-OSM requirements, dispositions, provenance,
     and approval.
  4. `lane-model/reviewed.json` - generated derivative.
  5. ScenarioNet pickle files - serialized output.
- Remove Lanelet2 commands, dependencies, artifacts, validators, and
  terminology from the active pipeline.
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
- Missing source facts may use configured preliminary defaults, but every
  material inference remains visible and reviewable.
- Source restrictions are never automatically deleted. Only deterministic
  topology may mark a restriction already satisfied; incomplete or
  contradictory evidence remains review-required.
- V1 produces a valid static map only. Vehicles, actors, traffic-light cycles,
  and scenario timelines are intentionally deferred.
