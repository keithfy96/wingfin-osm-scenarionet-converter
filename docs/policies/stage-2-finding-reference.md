# Stage 2 Finding Reference

This reference explains every finding emitted by
[`stage-2-generation-v1`](stage-2-generation-v1.md). The rule descriptions are
stable Stage 2 policy; the counts are a snapshot of the current mosque
workspace. **Counts change when the source OSM, configuration, or generator
semantics change.** A finding records uncertainty or use of fallback evidence.
It is not, by itself, proof that the source or generated map is wrong.

## Snapshot

- Generator: `direct-osm-stage2-v9`
- Generation fingerprint:
  `0794f9ac9f2f9847b5a44103de8ee1928cc1c7a7a5678dc1f77f6b1fa0417b07`
- Source: `workspaces/mosque/lane-model/preliminary.json`
- Total findings: **3,166**

> **This snapshot is stale.** The counts below were produced by
> `direct-osm-stage2-v9` from the mosque workspace, which is no longer present on
> disk, so they cannot currently be regenerated. The rule descriptions are stable
> policy and remain accurate. The current generator is `direct-osm-stage2-v11`,
> which allocates a whole approach across its destinations, and several approaches
> into the one carriageway they join, whenever the lane arithmetic closes. It
> lowers `ambiguous_connector` and `lane_transition_count_mismatch` wherever that
> happens and leaves every other rule untouched.
>
> For reference, the same generator over the junction-1 workspace produces **546**
> findings: `speed_default` 192, `lane_width_default` 192, `lane_count_inference`
> 105, `ambiguous_connector` 31, `lane_transition_count_mismatch` 22,
> `turn_permission_geometry_conflict` 3, `signal_lane_association` 1. That is a
> much smaller and hand-corrected extract, so its shares are not comparable to the
> mosque table below.

| Finding | Count | Share | Severity |
| --- | ---: | ---: | --- |
| `lane_width_default` | 1,117 | 35.3% | Warning |
| `speed_default` | 974 | 30.8% | Warning |
| `lane_count_inference` | 620 | 19.6% | 550 blockers; 70 warnings |
| `ambiguous_connector` | 292 | 9.2% | Blocker |
| `lane_transition_count_mismatch` | 141 | 4.5% | Warning |
| `inferred_stop_line` | 19 | 0.6% | Warning |
| `signal_lane_association` | 2 | 0.1% | Blocker |
| `restriction_effect_review` | 1 | under 0.1% | Blocker |

Warnings mark deterministic, usable proposals whose real-world correctness
still needs inspection. Blockers mark low-confidence or legally ambiguous
proposals that Stage 3 must resolve before acceptance as validated map facts.

## `turn_permission_geometry_conflict`

Placed first because it sorts first in the reviewer's queue (`REVIEW_PRIORITY`,
`web/src/panel.ts:19-29`, index `0` — hardest judgement first). It is absent
from the mosque table above, which predates the rule.

**Meaning and current scale.** A lane's `turn:lanes` tag permits **no movement
the geometry offers at that node**, so obeying the tag literally would leave the
lane with no exit at all. Rather than cut the drivable network, Stage 2 restores
the straightest rejected movement and raises this blocker. In `junction-1`
(`direct-osm-stage2-v17`, fingerprint `af457dfc…`) there are **3** of these among
140 findings: two at node `1927184814` on way `756118314`, one at node
`13946726034` on way `1530245742`.

**This finding is the tag being obeyed, not ignored.** That is the single most
common misreading, and it changes what the buttons mean.

**Trigger.** All three conditions must hold together
(`_stranded_permission_fallback`, `generation.py:566-583`):

1. no candidate movement out of the lane survived the `turn:lanes` filter;
2. at least one candidate was rejected **by the tag** (not by geometry, a
   restriction, or a side rule);
3. the lane carries no continuation — a lane that runs straight on already has
   somewhere to go, so it is not stranded.

Restored movement is `min(rejected, key=(abs(angle), to_lane_id))` — the
straightest rejected candidate, lane id as a deterministic tie-break.

**What is compared.** `turn_permissions` is the parsed `turn:lanes` value for
that one lane (`generation.py:308-322`; note the kerbside-first inversion under
left-hand traffic, and that `turn:lanes:<direction>` outranks the bare tag). The
movement class is *inferred* by binning `signed_turn_angle` (`classify_movement`,
`topology.py:47-55`: `≤35°` through, `35-70°` slight, `70-145°` left/right,
`≥145°` reverse). The two are joined by `movement_matches`
(`topology.py:58-65`), which treats `right` as covering `right`, `slight_right`,
`sharp_right`. So "the tag and the angle disagree" concretely means: no tag
token matched the angle bin of **any** available movement. This is the standing
principle in `CLAUDE.md` — a surveyed tag outranks an inferred angle, but the tag
must never be the reason a lane loses its only exit. Reverse movements bypass the
permission test entirely, so a dropped U-turn is never this rule.

**What the generator has already done.** The restored connector **exists in the
model** (`candidates_for_lane = [restored]`, `generation.py:1736`). It carries
its **geometric** movement class, deliberately not relabelled to the tagged one:
relabelling would feed a fabricated movement into `movement_side`, ambiguity
counting and restriction matching. The tag is recorded in the finding instead —
see
[`2026-08-07-12:03:07-turn-lanes-must-not-strand-a-lane`](../mapping-algo-changes/2026-08-07-12:03:07-turn-lanes-must-not-strand-a-lane.md).

`proposed_value` carries `turn_permissions` (what the tag said),
`restored_movement` and `restored_angle_degrees` (what was put back), and
`rejected_movements` (every class the tag refused). `affected_feature_ids` is
`[approach lane, restored destination lane]`, so re-targeting the movement
changes the finding's identifier.

### The reviewer's options

Control spec at `web/src/controls.ts:127-134`; question line:
*"The turn tag and the measured angle disagree — which one is right?"*

| Button | Status written | What it does |
| --- | --- | --- |
| `Keep restored movement` | `accepted` | Keeps the connector the generator restored, with its geometric class. The OSM tag is left untouched; the model simply does not obey it at this node. |
| `Set movement` ▾ | `overridden` | A single-select `Movement` dropdown — `through`, `left`, `right`, `slight left`, `slight right`, `reverse / U-turn` — writing `{"movement": "<value>"}`. Stage 4 writes that value into **this lane's slot** of the way's `turn:lanes` in `review/reviewed.osm`, then regenerates, so the movement is reclassified against the corrected tag. |
| `Not applicable` | `not_applicable` | Requires a typed reason. Says the *finding* is wrong, not the value. |
| `Clear decision` | `unresolved` | Returns it to the queue and blocks export. |

`Ignore` is **not offered**: the rule is always emitted `severity="blocker"`
(`generation.py:1739`), and `web/src/panel.ts:706` shows that button for warnings
only.

Two things the screen does not tell you:

- **`Set movement` corrects the tag; it does not filter the lane's exits.** It
  says what `turn:lanes` *should have said* for this lane, and the movements are
  re-derived from that. It cannot take the restored exit away — leaving the lane
  with no exit is the thing the fallback exists to prevent, so no control in
  Stage 3 means "this lane may exit only to the right". The tag already said
  that; this finding is the report that saying it stranded the lane.
- **Naming the movement the tag already carries is refused.** It would write the
  value back unchanged, regenerate an identical model and return the same
  blocker — an override that looks applied and did nothing. Stage 4 stops with
  *"which is what turn:lanes already says there"* rather than let that pass
  (`apply_review.py`, `_overrides_from`).

Stage 4 will also refuse an override that names a movement outside the six the
dropdown offers, one on a way whose `turn:lanes` has a different number of slots
than the model built lanes, one on a way that also carries a lane-count override
(re-laning moves every slot), and a bare `turn:lanes` on a two-way way — there
the value covers both directions at once, and correcting one would restate the
other by guess. Each names what to fix.

**What each choice commits you to.** Overriding says the tag was wrong and states
what it should have said; the corrected value lands on `review/reviewed.osm`,
`source/map.osm` is untouched, and the finding goes away only if the new value
permits something the geometry actually offers. If it does not, the conflict is
reported again and the decision is marked `satisfied: false`, so Stage 5 still
sees the blocker — answering is not the same as resolving. Accepting says the
geometry is right and the tag is describing something else: the map keeps a
movement the tag does not permit, the OSM is left alone, and the same question
returns on every regeneration by design. Marking it not applicable says the
question itself is malformed; the reason is kept in the audit record, and Stage 5
downgrades re-derived issues on that feature to dispositioned warnings rather
than errors. Never resolve this by making the rule stop firing.

### Worked example — `junction-1` node `1927184814`

Way `756118314` (*Persiaran Meranti*, tertiary, oneway, `lanes=2`) is tagged
`turn:lanes=right|right`, so both its lanes carry `turn_permissions=['right']`.
At its end node the geometry offers a `through` at +5.82° and a `left` onto
*Persiaran Perdana*. Neither matches `right`, so both were rejected and the
`through` was restored — twice, once per lane.

```
 node 1927184814             + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline (offside), idx(n−1) is kerbside

   APPROACHES                          ┊  DESTINATION — way 39619063 "Persiaran Meranti"
   (arriving at the node)              ┊  2 lanes · also tagged turn:lanes=right|right
                                       ┊
 ═════════════════════════════════════ ┊ ═══════════════════════════════════ KERB ══

   756118314  idx1/2   +5.82° ────────────────►  idx1/2  c0530c25fd  nearside
     Meranti · turn:lanes=right|right  ┊                               1 feed
     tag permits ......... right       ┊         ✗  0 EXITS — THIS LANE DEAD-ENDS  ✗
     geometry offers ..... through, left
     ► RESTORED as through  ·  blocker 31f7cfe441cbb146
                                       ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   756118314  idx0/2   +5.82° ──┐      ┊
     ► RESTORED as through      │      ┊
       blocker 989b411397bffef2 ├─────────────►  idx0/2  86602054f0  offside
   777160373  idx0/3  −88.62° ──┘      ┊                    2 feeds — SHARED
     Perdana · no turn:lanes           ┊         ✗  0 EXITS — THIS LANE DEAD-ENDS  ✗
     a genuine right turn              ┊
 ═════════════════════════════════════ ┊ ════════════════════════════ CENTRELINE ══

   travel direction ──────────────────────────────────────────────────────────►

   REJECTED BY THE TAG — no channel drawn above, because it has none:
     756118314 idx0/2 and idx1/2  ──►  way 776021089 "Persiaran Perdana"  (left)
     that left is the ONLY other movement the geometry offers at this node.
     There is no right-hand branch here at all — nothing for `right` to select.
```

`turn:lanes` describes what the lanes do at the **end of the way it is tagged
on**. Meranti is split in two at `1927184814` and both halves carry the identical
tag, which is why the upstream half raises blockers:

```
   1928630073            1927184814                        474928793
   ─────●───────────────────●───────────────────────────────────●
        │   756118314       │      39619063                     │
        │   Meranti         │      Meranti                      │
        │   turn:lanes=     │      turn:lanes=                  │
        │   right|right     │      right|right                  │
        │                   │                                   │
        │  at THIS way's end:                 at THIS way's end:
        │  +5.82°, straight on into 39619063   −91.65° RIGHT onto 777160375
        │  no right branch exists here         a genuine right turn — but
        │  → the two blockers above            ✗ forbidden, relation 10421009
        │                                        no_u_turn, 777160373 via 39619063
```

The far-end right turn being `forbidden` is why both destination lanes above
show **0 exits**: the restored `through` gives each lane an exit into a corridor
that currently terminates. The restriction was written to stop
`Perdana → Meranti → Perdana`, and its `reason` is
`prohibited via-way suffix removed`.

So the reviewer's real question at this node is which of two source facts to
correct: the `turn:lanes` on `756118314`, which describes a turn one junction
downstream, or the via-way restriction that removes the turn for everyone.

Note what the buttons can and cannot do about it. `Set movement → right` is
refused, because that is what the tag already says — it would write the same
value back and return the same blocker. `Set movement → through` states that this
half of Meranti carries straight on, which is what the geometry shows, and
resolves both blockers. `Keep restored movement` leaves the tag standing and the
disagreement on the record. The via-way restriction is out of reach of this
finding either way: no `turn:lanes` value can give back a movement a relation
forbids.

## `lane_width_default`

For the complete selection formula, parser behavior, geometry construction,
and a traced edge example, see the
[Stage 2 lane-width algorithm](stage-2-lane-width-algorithm.md).

**Meaning and current scale.** Stage 2 emits this finding when a directed graph
edge's source way has no usable explicit OSM `width`. The snapshot has **1,117
findings (35.3%) across 227 unique source ways**. These are edge-level findings,
not 1,117 distinct roads: a source way split into several directed graph edges
can appear several times. Their road classes are 541 residential, 193
secondary, 152 tertiary, 109 motorway, 74 secondary-link, 25 motorway-link,
and 23 unclassified. The affected-feature union is all **1,863 generated
lanes**, and every lane received the configured **3.5 m** fallback.

**Trigger, precedence, and unit.** A `width` is usable only when it parses as a
finite positive number after the supported metre wording is removed. When it
is usable, Stage 2 treats it as total carriageway width and divides it by the
explicit total `lanes` count, or by the generated directional count if total
lanes are unavailable. Otherwise `lane_width_defaults.vehicle` supplies the
per-lane width and the finding is emitted once for that generated edge. Thus
one finding represents an **edge**, while `source_ids` names its OSM way and
`affected_feature_ids` names every lane created for that edge.

**Why review remains necessary.** A configured width creates deterministic
polygons but cannot establish the physical carriageway width, whether an OSM
number meant total or per-lane width, or how usable roadway is divided around
parking and shoulders. It is a medium-confidence warning rather than a blocker
because generation can proceed consistently with a configured geometric
default.

On the visual audit, inspect total-versus-per-lane interpretation, unusually
narrow streets, motorway and link-road widths, parking or shoulder space, and
visible overlap between generated polygons. Accepting the proposal in Stage 3
means accepting 3.5 m for the affected lanes; rejecting it means supplying or
selecting better width evidence and regenerating or correcting the geometry.
Do not read the finding as “the road is 3.5 m wide,” “the OSM way is defective,”
or “each finding is a different road.” Relevant fields are `rule`, `severity`,
`confidence`, `reason`, `source_type`, `source_ids`, `affected_feature_ids`,
and `proposed_value`; inspect each affected lane's `width_m`, `road_class`,
`source_edge`, `source_way_ids`, `polygon`, and boundaries.

## `speed_default`

**Meaning and current scale.** This finding says that a directed edge had no
parseable explicit OSM `maxspeed`, so configuration supplied its generated
speed. There are **974 findings (30.8%) across 210 unique source ways**, whose
affected-feature union is **1,355 lanes**. The findings comprise 541
residential, 184 secondary, 152 tertiary, 74 secondary-link, and 23
unclassified edges.

**Trigger, precedence, and unit.** Stage 2 first accepts a finite positive
numeric `maxspeed`, supporting km/h and conversion from mph. It then consults
`speed_defaults_kph` by exact OSM `highway` class: living street 20 km/h,
motorway 110 km/h, motorway link 60 km/h, residential 50 km/h, and service
30 km/h. If the class has no configured entry, `default_speed_kph` supplies
**50 km/h**. In the current findings, secondary, tertiary, and unclassified
have no class-specific configured value and therefore use 50 km/h; residential
also resolves to its configured 50 km/h. One finding represents an **edge**;
its affected IDs are the lanes created from that edge.

**Why review remains necessary.** Configuration cannot prove a signed or legal
limit, local exceptions, or whether a textual/non-numeric OSM value has a
meaning the parser does not support. This is a medium-confidence warning,
because a consistent value is available for generation, rather than a blocker.
Inspect signed and legal speed, link-road behavior, school or residential
context, and whether `maxspeed` is absent versus present but non-numeric.
Accepting in Stage 3 approves the proposed configured speed for the affected
lanes; rejecting it requires better source or review evidence. It does not mean
50 km/h was observed, that every affected road legally permits 50 km/h, or
that the OSM data is necessarily erroneous. The finding fields above apply;
also inspect lane `speed_limit_kph`, `road_class`, `source_edge`, and
`source_way_ids`.

## `lane_count_inference`

See the [Stage 2 lane-count inference explanation](stage-2-lane-count-inference.md)
for the plain-language precedence table, examples, and review workflow.

**Meaning and current scale.** This finding records a directional lane count
that was inferred rather than read from high-confidence directional or one-way
evidence. There are **620 findings (19.6%) across 111 unique source ways**:

- 550 missing-count cases defaulted to one lane: low-confidence blockers.
- 70 even-total cases inferred one directional lane: medium-confidence warnings.

That gives **550 blockers and 70 warnings**.

**Trigger and precedence.** For each direction, Stage 2 first uses the positive
integer `lanes:forward` or `lanes:backward`. If the way is one-way (including a
roundabout), it uses a positive total `lanes` unchanged. If a positive total and
the **opposite** direction's count are both present, the directional count is
the remainder, `total - opposite`; this is exact arithmetic on stated evidence,
so it is high confidence and emits no finding. Only if neither direction is
stated does it fall back to `max(1, total // 2)`: an even total is medium
confidence, while an odd total is low confidence because division cannot
explain a shared, reversible, or asymmetrically allocated lane. With no usable
count it falls back to one lane at low confidence. A finding is emitted only
for the inferred-total, single-lane-fallback, or contradictory-tagging paths.
One finding represents a generated **edge/direction of a way**, with its created
lane or lanes in `affected_feature_ids`.

The remainder rule is why the current snapshot has no odd-total blockers. OSM
mappers commonly tag only the minority direction, as on way `334662874`
(`lanes=4`, `lanes:backward=1`, Persiaran Kenanga): halving would generate three
lanes on a four-lane road, whereas subtraction yields the correct three forward
plus one backward with no finding at all.

**Why review remains necessary.** Arithmetic cannot prove directional
allocation, reversible or shared lanes, turn pockets, or the correctness of
one-way tagging. Low-confidence cases block because no exact directional
allocation follows from the evidence; even-total division is a warning because
it is a plausible deterministic allocation. Inspect directional totals,
reversible/shared lanes, turn pockets, one-way status, and agreement with lane
markings. Accepting approves `proposed_value.direction` and
`proposed_value.lane_count`; rejecting requires corrected source evidence or a
Stage 3 decision. Do not equate a finding with a wrong lane count, or 620
findings with 620 roads. Relevant finding fields include `reason`
(`default_single_lane`, `inferred_from_total`, or
`contradictory_directional_total`) and the proposed direction and
count; affected lanes expose `direction`, `lane_count`, `lane_index`,
neighbors, `source_edge`, and `source_way_ids`.

## `ambiguous_connector`

**Meaning and current scale.** This finding represents one generated
intersection **connector** whose movement remains `review_required`. There are
**292 findings (9.2%)**. Movement totals are 257 reverse, 23 through, and 12
left/right/slight-turn connectors. The exact causes overlap: 257 are untagged
reverse/U-turn candidates, 35 have multiple targets in the same movement
family, eight lie in the inclusive 30°–40° through/turn boundary band, and
three double back past 130° without a `turn:lanes` permission. Because a
connector can satisfy more than one condition, these totals do not sum to 292.
The mutually exclusive combinations are 251 reverse-only, 24
duplicate-family-only, six reverse-plus-duplicate, four borderline-only, four
duplicate-plus-borderline, two sharp-only, and one duplicate-plus-sharp.

Duplicate-family findings fell sharply once side-aware source filtering landed:
a nearside exit is no longer emitted from the median lane as well as the
kerbside one, so the surviving candidate is usually alone in its family.

**Trigger and evidence precedence.** Signed heading change classifies movement:
up to 35° is through; above 35° and below 70° is slight left/right; from 70° to
below 145° is left/right; and 145° or more is reverse. Slight and ordinary
left movements share a `left` family, slight and ordinary right movements share
a `right` family, while through and reverse retain their own families. A
candidate is ambiguous when any of these is true: it is reverse and lane
turn-tag evidence neither permits nor excludes a U-turn; its source lane has
more than one target in the same family; its absolute angle is 30°–40°; or it
is not reverse yet turns at least `lane_selection.sharp_movement_review_degrees`
(130° by default) with no `turn:lanes` permission naming its direction. That
last rule exists because `classify_movement` only calls a movement reverse past
145°, so a turn a few degrees short of that doubles a driver back the way they
came while escaping the U-turn evidence requirement entirely.
Explicit `turn:lanes` evidence is applied before geometric fallback: matching
evidence can activate a movement and other explicit turn evidence can exclude
an unlisted reverse movement. OSM turn restrictions have final authority and
can make a connector forbidden.

**Why review remains necessary.** Geometry proves neither physical
connectivity nor legal permission, and similar angles do not prove that two
targets are duplicates. All are blockers because Stage 3 must decide whether
the proposed movement exists and is allowed. “Ambiguous” is not synonymous
with “U-turn”: 35 findings are non-reverse, and reverse is only one overlapping
cause. Nor is a reverse candidate automatically an illegal or erroneous
U-turn.

Inspect physical branches, lane arrows, permitted turns, median openings,
service-road geometry, and whether similarly angled targets are genuinely
distinct. Accepting activates the proposed connector/movement in Stage 3;
rejecting excludes it (subject to restriction evidence). Finding fields identify
the node in `source_ids`, connector in `affected_feature_ids`, and proposed
`movement` and `to_lane_id`. The connector record supplies `from_lane_id`,
`to_lane_id`, `from_way_id`, `to_way_id`, `junction_node_id`,
`turn_angle_degrees`, `movement`, geometry, and `status`.

## `lane_transition_count_mismatch`

> The counts in this section, like every other count in this file, predate
> `direct-osm-stage2-v16`, which narrowed the rule to what is described below.
> Junction-1 emits **one** of these findings under v16, against 19 under v15.

**Meaning.** This warning records a transition at a node where the lane mapping
put **more approach lanes than destination lanes** — two or more streams of
traffic handed the same lane. It is measured from the links that survived, so
every lane it names is an end of a real movement.

**Trigger and unit.** After every movement has been filtered, restored,
side-resolved and either kept or forbidden, links are grouped by
`(node, approach edge, destination edge)`. Both kinds count: connectors whose
status is `active` or `review_required`, and direct continuations. A
`forbidden` connector is excluded — the movement does not exist. Within a
group, `feeders` is the count of distinct approach lanes and `landed` the count
of distinct destination lanes they reach; the finding is emitted when
`feeders > landed`. The reverse cannot occur, because one approach lane yields
at most one target per group. `affected_feature_ids` holds the feeders followed
by the landed lanes, so the highlight and the counts always agree, and
`proposed_value` carries `incoming_lane_count`, `outgoing_lane_count` and
`destination_lane_count`.

**What it does not cover.** The rule says nothing about a destination lane that
no approach reaches. That is lane starvation, tracked separately. Before v16
the rule compared the *ways'* lane counts rather than the movement's, so a
one-lane turn peeling off a two-lane road was reported as a "2 to 1 lane
change" — a statement about two unrelated carriageways. Eighteen of junction-1's
nineteen findings were of that kind.

The mapping is deterministic but is not evidence of real merge or split
behaviour, so this stays a medium-confidence warning rather than a blocker.
Inspect the destination's tagged lane count first: a collapse is usually either
a genuinely narrower carriageway or a `turn:lanes` value that pushed two lanes
onto the same side. Accepting approves the mapping; overriding writes a
corrected outgoing lane count into `reviewed.osm` and the mapping is redone.

## `inferred_stop_line`

**Meaning and current scale.** Each finding maps one generated **stop line and
its approaching lane**. There are **19 findings (0.6%) across six signal
nodes**, with five, three, three, three, three, and two stop-line candidates per
node.

**Trigger, placement, and evidence.** For every generated lane associated with
a traffic-signal node, Stage 2 generates a transverse line spanning that lane
because no explicit stop-line geometry is used. Its centre is exactly two
metres before the lane centreline end (clamped to the start for a lane shorter
than two metres), and its orientation is perpendicular to the local approach.
The finding's affected IDs are the stop-line ID and lane ID;
`proposed_value.distance_upstream_m` is `2.0`.

The deterministic offset cannot prove the painted stop-bar location, how a
signal relates to a crossing, or whether several lanes share one physical bar.
It is a medium-confidence warning because usable review geometry is produced.
Inspect visible stop bars, signal position, approach direction, crossing
geometry, lane coverage, and whether multiple lanes should share one physical
stop line. Accepting approves that inferred line; rejecting requires explicit
or corrected geometry. It is not evidence that a stop bar exists exactly two
metres upstream. Besides standard finding fields, inspect stop-line
`identifier`, `lane_ids`, `points`, `source`, `source_node_id`, and `status`,
plus the affected lane centreline and width.

## `signal_lane_association`

**Meaning and current scale.** This finding means a source traffic-signal
**node** has no generated lane ending at it. There are **two findings (0.1%)**,
for nodes `394550253` and `1903786381`.

**Trigger, evidence, and geometry.** Stage 2 associates a signal with generated
lanes whose directed endpoint is the signal node. If that set is empty, the
association becomes `review_required`, with an empty proposed list and empty
`affected_feature_ids`. Consequently these findings currently have **no
affected geometry**. There is no configured geometric fallback: absence of an
ending lane is the evidence gap.

Stage 2 cannot prove whether the signal is displaced from the junction,
controls a filtered/non-driving way or another transport mode, or exposes a
graph-direction/topology problem. The low-confidence finding is a blocker
because no controlled vehicle lane can be named. Inspect signal placement,
filtered or non-driving roads, graph direction, divided-carriageway topology,
and whether the node controls vehicles or another transport mode. Accepting
requires choosing a valid association (or explicitly accepting none under the
future review semantics); rejecting the proposal means correcting source or
topology rather than pretending a lane was mapped. Do not interpret empty
geometry as proof the signal is irrelevant or erroneous. Inspect finding
`source_ids`, empty affected/proposed lists, and the signal record's
`source_node_id`, `lane_ids`, and `status`.

## `restriction_effect_review`

**Meaning and current scale.** This finding represents one OSM restriction
**relation** whose effect cannot be mapped to generated connectors. The sole
finding (under 0.1%) is relation `15336555`: `no_u_turn`, from way `756118317`,
via way `776369869`, to way `776369868`.

**Trigger, precedence, and geometry.** OSM restriction relations override
geometric and turn-tag proposals when their ordered from/via/to path can be
resolved. Here the expected via-way connector chain is missing, so Stage 2
cannot prove which connector should be forbidden. The restriction record and
finding therefore have no forbidden or affected connector IDs. Missing evidence
must not be interpreted as either permission or successful enforcement.

This is a low-confidence blocker because a legal restriction exists but has no
proved generated effect. Inspect relation membership, missing or filtered ways,
directionality, ordered connectivity, and equivalent node restrictions.
Accepting a Stage 3 resolution means identifying and forbidding the correct
generated movement, or establishing with evidence that the relation is already
satisfied; rejecting the empty proposal must not activate the movement by
default. The finding exposes `source_ids`, empty `affected_feature_ids`, reason,
and the full proposed restriction record. Inspect its `source_relation_id`,
`restriction`, `from_way_ids`, `via_member_ids`, `to_way_ids`, `status`,
`forbidden_connector_ids`, and `reason`.
