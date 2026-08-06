# Stage 2 Lane-Width Algorithm

This reference explains how `stage-2-generation-v1` selects a width for every
generated lane, turns that width into geometry, and reports width uncertainty.
It describes the current implementation in
[`generation.py`](../../src/osm_scenario/generation.py). The algorithm is stable
policy; the mosque counts and identifiers below are a generated snapshot and
change when the source OSM, configuration, or generator semantics change.

## Outcome

Stage 2 never leaves a generated vehicle lane without a width. For each
directed graph edge it either:

1. derives a per-lane width from a usable explicit OSM `width`; or
2. uses `lane_width_defaults.vehicle` as the per-lane fallback and emits one
   `lane_width_default` warning for that edge.

The current configuration sets the fallback to **3.5 m per generated lane**:

```yaml
lane_width_defaults:
  vehicle: 3.5
```

This is a generation default, not a claim that the whole road is 3.5 m wide or
that a 3.5 m lane was measured in the real world.

## Inputs and unit assumptions

For every projected directed edge, the algorithm uses:

- the edge's source OSM way and its tags;
- the generated directional lane count;
- the explicit total `lanes` tag, when it is a positive integer;
- the explicit `width` tag, when it is parseable; and
- `lane_width_defaults.vehicle` from
  [`config/default.yaml`](../../config/default.yaml).

An explicit OSM `width` is treated as **total carriageway width**, while the
value stored on a generated lane as `width_m` is **per-lane width in metres**.
Stage 2 does not currently distinguish a total-width tag from a value that a
mapper may have intended as per-lane width.

## Width parsing

The current parser normalizes whitespace and case, removes the literal words
`meter` or `meters`, and then requires a finite number greater than zero.

| OSM value | Current result |
| --- | --- |
| `7`, `7.0` | Usable |
| `7 meter`, `7 meters` | Usable |
| missing, empty, `unknown` | Not usable |
| `0`, a negative number, infinity | Not usable |
| `7 m`, `7 metres`, composite or range text | Not usable |

This table documents current parser behavior, not the full set of valid OSM
width-tag conventions. An unsupported but meaningful textual value follows the
same fallback path as a missing value and should be distinguished during
review.

## Selection algorithm

For each directed edge, Stage 2 first resolves its source OSM way and its
directional lane count. It then applies this precedence:

```text
usable_width = parse_positive_float(osm_way.width)
explicit_total_lanes = parse_positive_integer(osm_way.lanes)

if usable_width exists:
    divisor = explicit_total_lanes or generated_directional_lane_count
    per_lane_width = usable_width / max(divisor, 1)
    emit no lane_width_default finding
else:
    per_lane_width = lane_width_defaults.vehicle
    emit one lane_width_default finding for this directed edge
```

The explicit total `lanes` tag takes precedence as the divisor even though the
algorithm is processing one directed edge. If no usable total is available,
the generated directional count is used instead. Consequently, an explicit
width combined with missing or uncertain lane-count evidence can still produce
a provisional width that deserves inspection even though it does not emit
`lane_width_default`; lane-count uncertainty is reported separately by
`lane_count_inference`.

### Examples

| OSM evidence | Generated directional count | Per-lane result | Width finding |
| --- | ---: | ---: | --- |
| `width=10.5`, `lanes=3` | 3 | 3.5 m | None |
| `width=7`, `lanes=2` | 1 | 3.5 m | None |
| `width=10.5`, no usable `lanes` | 3 | 3.5 m | None |
| no usable `width`, any lane count | 3 | 3.5 m fallback on all three lanes | One warning for the edge |

The second example demonstrates that explicit `width` is treated as total
carriageway width and divided by the explicit total lane count, not merely by
the one lane generated in that direction.

## Geometry construction

After selecting `per_lane_width`, Stage 2 creates every lane on the edge with
that same value:

1. Lane centre `i` is offset from the directed edge geometry by
   `(i + 0.5) * per_lane_width`, on the side selected by the workspace driving
   side.
2. The lane polygon is the centreline buffered by half the width, with flat
   ends and mitred joins.
3. Left and right boundaries are centreline offsets of half the width.
4. The lane record stores the selected value in `width_m`.

For three fallback-width lanes, each lane is 3.5 m wide and the generated
directional carriageway is approximately 10.5 m across. The width is therefore
already embedded in the preliminary centreline offsets, polygons, and
boundaries; the finding is not merely an unused recommendation.

If a centreline offset cannot produce a usable line, Stage 2 falls back to the
directed edge geometry for that centreline. This geometric fallback is separate
from `lane_width_default` and does not establish that the selected width is
physically correct.

## Finding granularity and traceability

A `lane_width_default` finding is created after all lanes for one directed edge
have been generated. Its fields mean:

| Field | Meaning |
| --- | --- |
| `rule` | `lane_width_default` |
| `severity` | `warning` |
| `confidence` | `medium` |
| `reason` | `no usable explicit OSM width` |
| `source_type` | `way` |
| `source_ids` | Source OSM way ID or IDs carried by the edge |
| `affected_feature_ids` | Every lane generated for that edge |
| `proposed_value` | The per-lane fallback width, currently `3.5` |

The hierarchy is:

```text
OSM way
└── one or more directed graph edges
    └── one finding per edge that used the fallback
        └── one or more affected generated lanes
```

Therefore a finding is not a distinct road and is not emitted once per lane.
One OSM way can be split into several directed edges and can consequently
produce several findings.

For example, source way `859423755` currently produces four width findings
covering 12 lanes. One finding covers edge
`8010943716 -> 8010943715` and identifies its three generated lanes; each lane
has `width_m: 3.5`.

## Current mosque snapshot

In the current
[`preliminary.json`](../../workspaces/mosque/lane-model/preliminary.json):

- 1,117 directed edges emit `lane_width_default` findings;
- those edges trace to 227 unique source ways;
- the affected-feature union contains all 1,863 generated lanes; and
- every affected lane has `width_m: 3.5`.

The road-class breakdown is 541 residential, 193 secondary, 152 tertiary, 109
motorway, 74 secondary-link, 25 motorway-link, and 23 unclassified findings.
These counts describe this fingerprinted output, not a permanent policy
expectation.

## Review and later stages

The fallback is a warning rather than a blocker because it lets Stage 2 create
deterministic, inspectable geometry. It still cannot prove:

- the physical or legally usable carriageway width;
- whether an explicit source value was intended as total or per-lane width;
- how parking, shoulders, medians, or buffers consume visible road space; or
- whether motorway, link-road, or unusually narrow street widths are plausible.

On the read-only Stage 2 visual audit, inspect lane and boundary alignment,
polygon overlap, narrow roads, motorway and link geometry, and space occupied
by parking or shoulders. The audit does not itself alter `preliminary.json` or
record an acceptance decision.

Under the staged policy:

- accepting the proposal in Stage 3 records approval of the fallback for the
  affected lanes;
- replacing or rejecting it records a reviewed alternative or rejection in
  `review.json`;
- Stage 4 re-runs generation with those decisions and materializes reviewed
  output; and
- verified source facts should be corrected in the source OSM and regenerated,
  while systematic parsing or interpretation changes belong in configuration
  or generator policy.

Do not edit `preliminary.json` as a substitute for correcting source evidence,
configuration, generator semantics, or a recorded review decision.

## Related references

- [Stage 2 generation policy](stage-2-generation-v1.md)
- [Stage 2 finding reference](stage-2-finding-reference.md)
- [`lane_width_default` source implementation](../../src/osm_scenario/generation.py)
