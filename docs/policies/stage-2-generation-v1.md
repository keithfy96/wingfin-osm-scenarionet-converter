# Stage 2 Lane-Generation Policy v1

`stage-2-generation-v1` documents how Stage 2 turns the immutable Stage 1 OSM
snapshot and projected directed graph into the preliminary lane model. The
current executable implementation is `direct-osm-stage2-v10` in
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
- Lanes are offset laterally from the OSM way centreline. A two-way way splits
  its lanes between two directed edges, so each edge lays its block wholly on its
  own side and the two together straddle the centreline. A way that carries its
  whole carriageway on one directed edge — `oneway` or `junction=roundabout` —
  has no opposite block to balance against, so its lane block is centred on the
  centreline instead. Without this a one-way carriageway is drawn half its total
  width off the road, and a single-lane ramp can come to rest exactly on top of
  a neighbouring lane and read as a continuation into it.
- Centring changes position only. Lane index 0 remains the lane against the road
  centre, the offside lane, and index `count - 1` remains the kerbside one.
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

Grouping outgoing lanes by directed edge does not mean every approach lane is
linked to every group. A movement that carries a side is emitted only from that
side's lane of the approach, as described under lane-to-lane mapping below.

## Lane-to-lane mapping

- Stage 2 creates one deterministic target per source lane and outgoing edge
  group, rather than a full incoming-lane by outgoing-lane cross product.
- **An approach is allocated as a whole when its arithmetic closes.** A lane that
  peels off cannot also be the straight-on lane. Where the destinations of one
  approach hold exactly as many lanes as the approach brings — a three-lane road
  reaching a two-lane continuation and a one-lane link, say — every lane has
  exactly one destination and there is nothing left to infer. Those lanes are
  dealt kerb first: destinations are ordered by how far each turns toward the
  kerb, and the approach's lanes are handed out from the kerbside inward, so the
  link leaving toward the kerb is fed by the kerbside lane and the rest carry on
  in order. Reverse destinations do not consume capacity and take no part.
  Asked one destination at a time instead, both the link and the continuation
  claim the kerbside lane, one lane is left serving two through movements, and
  two others collapse onto a single target — the road silently loses a lane.
- When the counts do **not** close the approach is oversubscribed, a lane really
  does serve more than one movement — a single lane that may go left or straight
  — and the proportional order mapping below still decides. Sharing is real
  there, and the ambiguity it creates is reported rather than resolved.
- A balanced approach emits no `lane_transition_count_mismatch`. Its lanes are
  apportioned across destinations, not lost, so the count difference across any
  one destination is not a mismatch.
- Generated lane indices run centre-out. Index `0` is the lane against the road
  centreline, the **offside** lane, and index `count - 1` is the lane against the
  kerb, the **nearside** lane. With left-hand traffic the nearside lane is the
  leftmost from the driver's seat; with right-hand traffic it is the rightmost.
  This is the driver's frame, not screen orientation.
- The side rule governs **turns**, not continuations. A movement that leaves toward
  the kerb enters the target group's nearside lane; one that leaves toward the
  centreline enters its offside lane. A straight-ahead movement carries no side.
- A continuation never carries a side, whatever its angle. A carriageway that bends
  past the side threshold at an ordinary shape node or way split is still the same
  road, so its lanes keep their order; snapping them to the kerb would shuffle the
  whole block sideways.
- The same side governs which lane a movement may be emitted **from**. A nearside
  movement is generated only from the approach's nearside lane and an offside
  movement only from its offside lane, so an exit is not duplicated out of every
  lane of the approach. Three cases are exempt: a `reverse` candidate, which the
  U-turn policy governs; a lane whose `turn:lanes` names `left` or `right`, which
  is explicit evidence; and a candidate a node-via restriction forbids, which is
  kept so the restriction still has something to act on and stays visible for
  audit. If the filter would leave an approach lane with no movement at all, its
  straightest candidate is kept rather than stranding the lane — but a lane that
  carries straight on is not stranded, so a direct continuation disables that
  fallback. Otherwise every lane of an approach feeds the exit, which is exactly
  what the side rule exists to prevent.
- Source filtering runs before the ambiguity pass, so a movement left alone in its
  family after filtering is active rather than `review_required`.
- Which side a movement takes is decided by `movement_side()` in
  [`topology.py`](../../src/osm_scenario/topology.py). An explicit `turn:lanes`
  value naming one direction is used first. Otherwise the sign of the signed turn
  angle decides, once the angle reaches
  `lane_selection.side_movement_min_degrees`. That threshold exists because
  `classify_movement` treats everything within 35 degrees as `through`, so a slip
  road leaving at 20 degrees is not a `left` movement even though it plainly
  departs to the left.
- Equal lane counts preserve lane order.
- Different lane counts use proportional centre-out lane-index mapping when the
  movement carries no side.
- A lane-count change emits `lane_transition_count_mismatch` because the
  proportional mapping is an inference that may require Stage 3 review.
- Explicit `turn:lanes` permissions then remove incompatible movement
  candidates.

## Junction geometry

An OSM way ends on the centreline of the road it joins, because two ways must
share a node to be connected. A lane must not be drawn stopping there: it would
end in the middle of the opposing carriageway, pointing at through traffic,
instead of reaching the lane it enters.

- Where an **active** connector carries a `through` movement — a merge or a
  diverge — and the two lanes are further apart than
  `lane_geometry.merge_taper_min_gap_m`, the lane on the side with fewer lanes is
  bent so its end meets its counterpart. The displacement is blended in over
  `lane_geometry.merge_taper_length_m`, clamped to the lane's own length, so the
  lane bends rather than steps sideways.
- The through carriageway is never bent: only the minor side yields. An even split
  is left alone, because there is nothing to choose between the two, and a real
  turn is left alone because it ends at a stop line and its connector curve is
  already the right shape.
- Where several lanes merge into one, that lane can only begin in one place. The
  remaining approaches keep their gap and their connectors span it.
- A taper is geometry only. Movement classification, side selection and connector
  status are all decided from the untapered OSM geometry first: letting a taper
  change an angle would let it change the classification that selected it. The
  generation report records `merge_tapers` so the adjustment is auditable.
- A connector between two lanes that already meet has no gap to span and is drawn
  as a short marker of fixed length back along the incoming lane, not to that
  lane's previous vertex — a straight lane has only two, so the marker would
  otherwise retrace the whole lane.

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

A non-reverse candidate also becomes `review_required` when it turns at least
`lane_selection.sharp_movement_review_degrees`, 130 degrees by default, and the
source lane's `turn:lanes` names no permission matching its direction. A
movement that sharp sends a driver back the way they came and deserves the same
positive evidence the U-turn policy demands, but `reverse` starts only at 145
degrees, so a turn a few degrees short of that would otherwise be asserted
outright. The classification is untouched: the movement is still reported as
`left` or `right`, and only its status moves. An explicit matching `turn:lanes`
permission settles it and keeps the movement active.

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
- `inspection/stage-2-review-audit.html` — the single read-only inspection
  artifact. It draws generated lanes, connector statuses, restrictions, and
  inferred stop lines on an OSM basemap, marks each lane with a chevron pointing
  along its direction of travel, and underlays the source OSM ways and nodes the
  generation came from. Each connector is drawn as a lane-width band as well as a
  centreline, so a movement stays legible where two lane polygons abut. A lane's
  popup lists every link it has with the movement and status of each, so a
  `review_required` candidate cannot be mistaken for an asserted connection.
  A searchable, filterable finding queue focuses each
  finding on both the generated geometry it affects and the source way or node it
  was raised against; a finding sourced to a relation resolves to its member ways
  and nodes. Pasting a bare ID into the search box resolves it against the way,
  node, and generated-feature namespaces in that order, then highlights and zooms
  to whatever it matched, so an ID can be located without going through a finding.
- The Stage 2 record in `source/manifest.json`.

The audit is a read-only check of what Stage 2 generated. It makes textual
findings spatially understandable, but it is not the Stage 3 decision
application and does not export `review.json`.

## Current mosque interpretation

With `direct-osm-stage2-v10`, the current mosque output intentionally contains
review-required U-turn candidates at genuine decision nodes where OSM provides
neither permission nor prohibition. Those findings are not generation errors;
they expose legal uncertainty for Stage 3. An unexpectedly large number of
ordinary continuation connectors, active reverse connectors without explicit
evidence, or reverse candidates at shape nodes would violate this policy.

See the [Stage 2 finding reference](stage-2-finding-reference.md) for the
current mosque snapshot, exact trigger conditions, and visual-review guidance
for every finding category.
