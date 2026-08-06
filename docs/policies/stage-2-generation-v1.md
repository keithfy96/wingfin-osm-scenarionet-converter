# Stage 2 Lane-Generation Policy v1

`stage-2-generation-v1` documents how Stage 2 turns the immutable Stage 1 OSM
snapshot and projected directed graph into the preliminary lane model. The
current executable implementation is `direct-osm-stage2-v5` in
[`generation.py`](../../src/osm_scenario/generation.py), with topology and
restriction helpers in [`topology.py`](../../src/osm_scenario/topology.py).

This is a deterministic generation policy, not a claim that inferred geometry
or legal movements are real-world facts. Explicit OSM evidence takes
precedence. Missing or genuinely ambiguous evidence is preserved for Stage 3
review rather than silently guessed.

## Stage ownership

These rules run only during Stage 2 automatic generation.

| Stage | Responsibility |
| --- | --- |
| Stage 1 | Preserve and normalize source OSM facts and directed road topology. |
| Stage 2 | Apply this policy and generate `preliminary.json`, findings, and the read-only map. |
| Stage 3 | Let a reviewer accept, reject, or replace reviewable proposals in `review.json`; the reviewer does not draw geometry. |
| Stage 4 | Re-run generation with the reviewed decisions and materialize `reviewed.osm` and the reviewed lane model. |
| Stage 5 | Validate geometry and connectivity. |
| Stage 6 | Convert the validated reviewed model to ScenarioNet. |

Stage 3 and Stage 4 must not silently redefine these rules. A semantic change
to Stage 2 increments `GENERATOR_VERSION`, changes the generation fingerprint,
and invalidates or requires migration of review decisions bound to the older
generation.

## Evidence hierarchy

Stage 2 applies evidence in this order:

1. Explicit OSM tags and restriction relations.
2. Directed topology preserved by Stage 1.
3. Deterministic geometry and lane-order inference.
4. Configured defaults.
5. A review finding when the result is inferred, ambiguous, or cannot be
   proven.

An active connector means the automatic evidence is sufficient under this
policy. It does not mean OSM explicitly stated every part of that movement.
A `review_required` connector is deliberately excluded from active lane links
until Stage 3 resolves it. A `forbidden` connector remains visible for audit
but is also excluded from active lane links.

## Lane generation

- Each directed Stage 1 graph edge produces directional lanes.
- Directional `lanes:forward` or `lanes:backward` is preferred.
- On a one-way road, `lanes=*` is the directional count.
- When `lanes=*` and the opposite direction's count are both present, the
  directional count is the remainder, `lanes` minus the opposite count. This is
  exact evidence rather than an inference, so it emits no finding. A total at or
  below the opposite count is contradictory tagging: Stage 2 generates one lane
  and emits a blocker.
- On a two-way road without directional counts, Stage 2 divides `lanes=*` by
  two. Odd totals are low-confidence; absent lane counts default to one lane.
- Explicit total width is divided across the applicable lanes. Otherwise the
  configured width for the road type is used and a finding is emitted.
- Explicit parseable `maxspeed` is preferred. Otherwise the configured
  road-class/default speed is used and a finding is emitted.
- Lane IDs and all other generated IDs use `ids.deterministic_id()` and remain
  strings in Python, JSON, and browser output.

## Lane ordering and `turn:lanes`

Generated lane indices run from the road centre outward. OSM `turn:lanes`
values are written from left to right in the direction of travel.

- For right-hand traffic, the OSM position maps directly to the generated lane
  index.
- For left-hand traffic, Stage 2 reverses the OSM position before assigning it
  to the generated lane index.
- Semicolon-separated permissions on one lane are retained, such as
  `through;reverse`.
- If a movement conflicts with an explicit lane's turn permissions, that
  movement is not generated from that lane.

## Continuations versus intersection connectors

An OSM way boundary alone does not prove an intersection. For every incoming
lane at a node, Stage 2 groups outgoing lanes by directed edge and applies the
following rules:

1. A non-reversing continuation on the same OSM way is linked directly.
2. A single non-reversing continuation at an ordinary degree-two shape or way
   split node is also linked directly, even if the OSM way ID changes.
3. Connector geometry is generated only at a decision node.

A node is a decision node when at least one of these is true:

- More than one non-reversing outgoing edge group is available.
- More than two distinct physical neighbour nodes meet there.
- The node participates as a node-via turn restriction.
- The node carries a supported traffic-control or junction tag.
- The source lane explicitly permits a reverse/U-turn movement.

This rule prevents ordinary shape nodes and harmless way splits from creating
large numbers of false intersection connectors.

## Lane-to-lane mapping

- Stage 2 creates one deterministic target per source lane and outgoing edge
  group, rather than a full incoming-lane by outgoing-lane cross product.
- Equal lane counts preserve lane order.
- Different lane counts use proportional centre-out lane-index mapping.
- A lane-count change emits `lane_transition_count_mismatch` because the
  proportional mapping is an inference that may require Stage 3 review.
- Explicit `turn:lanes` permissions then remove incompatible movement
  candidates.

## Movement geometry and ambiguity

Movement type is classified from the signed heading change:

- Absolute angle up to 35 degrees: `through`.
- More than 35 and less than 70 degrees: `slight_left` or `slight_right`.
- From 70 degrees up to, but not including, 145 degrees: `left` or `right`.
- Absolute angle of at least 145 degrees: `reverse`.

Non-reverse candidates become `review_required` when multiple candidates from
the same source lane belong to the same movement family, or when the angle is
in the 30-to-40-degree through/turn boundary band. Restrictions can override
that status to `forbidden`.

## U-turn policy

Stage 2 does not assume that an untagged U-turn is legal or illegal.

1. No U-turn candidate is generated at an ordinary non-decision shape node.
2. Unless another lane is explicitly tagged, an inferred U-turn is considered
   only from generated lane index `0`, the innermost lane.
3. Explicit `reverse` or `uturn` in the source lane's turn permissions is
   positive evidence and makes the candidate eligible to be active.
4. Other explicit turn permissions without `reverse` or `uturn` are conflicting
   evidence, so a U-turn candidate is not generated from that lane.
5. With plausible decision-node topology but no positive or conflicting turn
   tag evidence, the candidate is generated as `review_required`.
6. A matching `no_u_turn` restriction has priority and marks the candidate
   `forbidden`.
7. An `only_u_turn` restriction forbids the other matching movements from its
   `from` way at the restriction node.

Therefore an active reverse connector must have positive source evidence. An
untagged plausible reverse movement remains a manual decision; absence of an
OSM prohibition is not treated as permission.

## Restriction policy

- Node-via `no_*` restrictions forbid the matching from-way to to-way
  transition.
- Node-via `only_*` restrictions forbid other transitions from the restricted
  from-way at that node.
- Via-way restrictions are enforced only when their ordered connector chain is
  present and topologically unique.
- Missing, incomplete, or non-unique via-way evidence remains
  `review_required`; Stage 2 does not remove a transition using an unproven
  global way-level assumption.
- A prohibited movement that was already absent is recorded as
  `already_satisfied`.

## Signals and stop lines

- A traffic-signal node is associated with generated lanes that approach and
  end at that source node.
- A signal without an approaching generated lane is `review_required`.
- Stage 2 creates an inferred stop-line candidate two metres before the end of
  each associated lane.
- Inferred stop lines remain `review_required`; Stage 2 does not claim that
  their generated position is an observed source fact.
- Stage 2 does not generate signal timing, phases, actors, or traffic-light
  state sequences.

## Stage 2 outputs

Running:

```bash
uv run osm-scenario generate-map \
    --workspace workspaces/mosque \
    --config config/default.yaml
```

recreates:

- `lane-model/preliminary.json` — generated lane model and statuses.
- `reports/lane-model-generation.json` — counts, checksums, and generator
  metadata.
- `inspection/stage-2-map-review.html` — read-only visual inspection map.
- `inspection/stage-2-review-audit.html` — searchable, filterable review audit
  that focuses each finding and its affected generated geometry on an OSM
  basemap.
- The Stage 2 record in `source/manifest.json`.

Both HTML files are read-only checks of what Stage 2 generated. The review
audit makes textual findings spatially understandable, but it is not the Stage
3 decision application and does not export `review.json`.

## Current mosque interpretation

With `direct-osm-stage2-v5`, the current mosque output intentionally contains
review-required U-turn candidates at genuine decision nodes where OSM provides
neither permission nor prohibition. Those findings are not generation errors;
they expose legal uncertainty for Stage 3. An unexpectedly large number of
ordinary continuation connectors, active reverse connectors without explicit
evidence, or reverse candidates at shape nodes would violate this policy.

See the [Stage 2 finding reference](stage-2-finding-reference.md) for the
current mosque snapshot, exact trigger conditions, and visual-review guidance
for every finding category.
