# The Stage 2 lane model — allocation, restrictions and geometry

How lanes are dealt across destinations, how turn restrictions and off-ramps remove
movements, where a lane block sits across its way, and what is still starved.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

### `lanes=1` with no `oneway` is read as one-way, in Stage 1, and can be refused

A mapper who writes `lanes=1` and leaves `oneway` off almost always means a one-way slip,
and `_directional_lane_count` used to fall through to `max(1, total // 2)` and build a lane
**each way** — a road the source says is one lane wide coming out 7 m across, with a U-turn
at each end. `osm_source.single_lane_implies_oneway` now reads it. Every surveyed tag
switches it off: `oneway=no`, either `lanes:<direction>`, an existing `oneway`, a roundabout.

**The reading is applied in Stage 1, not Stage 2, and that placement is the point.**
`_apply_single_lane_oneway` drops the reverse edges inside `select_public_driving_graph`,
the one chokepoint `acquisition.py` and `apply_review.py` both pass through, so Stage 2 and
Stage 4 agree for free. `generation.py` never re-reads the tags to decide it — it asks the
graph, via `_single_direction_ways`, so the two stages cannot disagree.

But the graph is not the tags, and **the tags still say two-way**: Stage 1 must never write
to `source/map.osm`, which is acquisition evidence. So `_carries_whole_carriageway` takes
`one_way_in_graph` and every call site threads it. Skip that and the change is half done —
the surviving lane sits **1.75 m** off the road's centre, balancing against an oncoming block
that no longer exists, and its count is still reported as an inference.

**A refusal is a real outcome, not a failure.** Dropping the reverse direction of the only
route off a spur strands everyone on it, so a way is refused unless every node that could get
out before still can. Getting out means reaching the main network **or driving off the edge of
the map** — the first version of the guard had only the first half and refused a merge slip
whose far end continues out of the extract. Both anchors are pinned before anything is dropped:
the main network as one node of the largest strongly connected component, so it cannot shrink
under its own answer, and the map's edges as the nodes that already had no way on at all. A
cul-de-sac tip is not one of those, because its way out is the reverse direction in question.

Candidates are decided **one at a time against the graph the last one left** — two ways can each
be spare while the other is two-way and be the only way out together.
`manifest["road_selection"]["single_lane_oneway"]` records `applied` and `blocked`, and the
`lane_count_inference` **blocker** Stage 2 already raises on a `lanes=1` way is what carries a
refusal into Stage 3. No new finding rule was needed for it, and none was added.

### A turn restriction names a route; a connector is one step of one

A `no_*` relation forbids the sequence FROM → VIA → TO. A `ConnectorFeature` is
`from-lane → to-lane` at one node and remembers neither the road before it nor the road
after, so enforcing the relation means **deleting one step of the route — and that stops
everyone who uses that step**, not only the drivers on the prohibited route.

There are two candidates and each has its own test, and they look in opposite directions:

- delete the **last** step (VIA → TO) — exact only if **nothing else feeds VIA**
- delete the **first** step (FROM → VIA) — exact only if **VIA leads nowhere else**

`via_way_resolution` deleted the last one unconditionally until 2026-08-12, which on
`10421009` deleted Persiaran Meranti's own right turn — named by `turn:lanes=right|right`,
and not mentioned anywhere in the relation — and left way `39619063` with **no exit at
all**. See
`docs/mapping-algo-changes/2026-08-12-18:33:44-a-turn-restriction-deleted-the-wrong-movement.md`.

Three things not to re-derive:

- **The adjacency the test reads is an upper bound on purpose.** `topology.way_adjacency`
  counts per *way*, not per lane or per direction, so it can over-count what reaches a way
  and never under-count it. Over-counting sends the restriction to review; under-counting
  would delete a movement carrying legal traffic. Do not "tighten" it without moving the
  whole test to lane level.
- **The last step wins when both are exact.** Not taste — every restriction enforced before
  the change removed the last step, and re-deciding a settled one moves a forbidden
  connector id and costs the review decision attached to it.
- **When neither is exact, nothing is deducible.** A gyratory is the usual shape: each
  segment carries traffic from several entries by design. The movements come back as
  `review_required`, which is already excluded from the lane graph, so holding them does
  not make the prohibited route drivable. `RestrictionEffect.forbidden_connector_ids` stays
  empty there — it forbade nothing and must not claim to — and the held ids ride on the
  findings.

**Node-via restrictions are a different thing and are correct as they stand.** A node
restriction names from-way, via-node and to-way, which is exactly the triple a connector
encodes, so it cannot over-forbid. `mosque`'s gyratory movements stay forbidden through
this change because three node-via `no_right_turn` relations name them precisely.

`restriction_enforced_leg` is a **warning** carrying which step was removed and why the
other was rejected. Only blockers gate export, so it asks nothing; it exists because the
generator now chooses between two defensible enforcements.

### A restriction has to be known before the lanes are dealt out, not after (v21)

Node-via restrictions used to be read only at the end, over a candidate list that was already
final — and **both** rules that decide where a lane lands had by then counted a destination
that was about to be deleted. On mosque `859423756`, where rel 18555950 forbids the straight-on
and every vehicle must therefore turn right, that cost two of three lanes their only exit:

- `_balanced_approach_assignment` counted 3 lanes arriving against **6** lanes of destination,
  did not close, and stood aside. Discount the forbidden destination and it is 3 against 3.
- `_side_filtered_candidates` struck the right turn from idx1 and idx2 as offside-only, and its
  no-stranding catch did not fire because `kept` was not empty — each lane still held the
  straight-on, **which was there only because the filter deliberately keeps a movement a
  restriction forbids so the restriction has something to act on**. The lane was judged to have
  somewhere to go on the strength of a movement that existed in order to be deleted.

`_restricted_groups` now hides those destinations from the two balanced rules, and the catch no
longer counts a restriction-forbidden candidate as an exit. Four things not to re-derive:

- **Only the allocation is blinded.** The movements are still generated, still forbidden, and
  keep their ids. A restriction that deletes nothing leaves nothing on the map explaining why
  the turn is missing.
- **Nothing is hidden where that leaves the approach no destination at all.** With no survivors
  there is no split to protect, and blinding the allocation only collapses the forbidden
  movements onto one lane and moves their ids. junction-1 rel 16740674 is that case.
- **`blocks_by_group`, the feeder list `_merge_side` compares, is deliberately not filtered.**
  Tried and measured: no active connector changed, and the forbidden ids of **seven** relations
  moved across both workspaces.
- **`non_reverse_groups` / `_is_decision_node` must never be filtered either.** A forbidden
  movement is still a movement geometrically; dropping it from that count can take a node below
  the decision-node threshold, at which point the movement becomes a *continuation* — and a
  restriction cannot act on a continuation.

See `docs/mapping-algo-changes/2026-08-13-04:42:58-lanes-were-dealt-across-a-destination-a-restriction-forbids.md`.

### An off-ramp before the junction means the junction does not carry that turn (v22)

Nothing in the generator ever *asserts* a turn. At a decision node every non-reverse outgoing
group is reachable from the approach and only evidence removes a movement — so a turn nobody
may make, with no `turn:lanes` and no restriction naming it, is generated and never
questioned. Neither extract holds a single `left` in any `turn:lanes` value: **every left turn
on both maps exists purely because two ways share an OSM node.**

The evidence that had never been read is the slip road. `_link_bypass_way` names the `_link`
way that already carries a movement, and the status becomes `forbidden` with a **warning**,
`movement_served_by_link_bypass`, recording what went and what took it. Keith:
*"these two are wrong because there is an offramp before it."*

Both ends have to match — the ramp leaves the node the approach's own **edge starts at**, and
comes out at the node the destination's **edge ends at** — and the movement must carry a side.
All three guards were measured, and each is a reading that was tried and is wrong:

- match the ramp's end against **any node of the destination way** and mosque reads **22**
  connectors bypassed against the tight test's 5, six of them carriageways carrying straight on
  at +2.45° and +5.07°. A ramp replaces a turn, never a road going ahead — hence the side test.
- keep only the **chain's final node** and the Kenanga case vanishes. `182502392` comes out at
  `1928630157` and a *different* ramp, `182502409`, starts there; walking through reads
  `1928630009`. Nothing distinguishes one ramp mapped as two ways from two ramps in series, so
  **every way boundary along the chain is recorded**.
- a ramp says a turn is taken elsewhere, **never that a lane has no exit**, so a movement that
  is the lane's last one stays. Read after the restrictions resolve — a restriction may have
  taken the exit that would otherwise have counted.

Read **before** the lanes are dealt out, in `blocked_groups` beside `_restricted_groups`, with
the same carve-outs and for the same reasons as v21.

Three ramps in the two extracts, three duplicated turns, and the third — `191861354` at node
`474922037` — was **already forbidden by a surveyed restriction**, which is the corroboration
for reading the shape as evidence rather than a guess. Two consequences worth not
re-discovering: a Perdana car no longer drives the short block **between** the junction and
where the ramp merges (that is what a slip means — it joins beyond it), and Kenanga
`5fe50f735e40d7c2` is now starved because its only other feed, `7046b111f705c203`, is an open
`review_required` blocker. See
`docs/mapping-algo-changes/2026-08-13-16:32:06-a-turn-an-off-ramp-already-carries-was-offered-twice.md`.

### A merging road must not cross the lane it is joining (v23)

**A joining way's last edges aim at the junction node, and that node sits inside the
carriageway** — on the other way's centreline, which on a three-lane road *is* the middle lane.
So the road converges on the lane it merges into, **overshoots it**, and the merge taper hauls
the last lane back out. That is the turning *in* before turning *out* Keith reported, and why a
ramp's ribbon lands on the lane beside the one it enters. Measured: 1.21 m, 1.40 m and 1.52 m of
overshoot on the three merges he named.

**The overshoot is usually not in the lane the merge owns.** On the `182502409` ramp it is
`15438e6fd90cf39e`, an ordinary lane, which is why three separate attempts confined to
`72fdbea2a86f51e8` could not fix it. `_uncrossed_lanes` walks back through single continuations
and pulls every vertex on the wrong side **perpendicular onto the line**, keeping its distance
along it. Five things not to re-derive:

- **The correction is a sideways pull, never a bend.** Drawing it as a cubic tangent to the road
  behind and to the lane ahead bowed every lane it touched in `junction-1` — all dead straight
  before — by up to **2.31 m**, and pushed the ramp's ribbon on the middle lane from 14.7 to
  **22.1 m²**, making the reported defect worse. Keith reverted it on sight.
- **Stopping the lane short and letting the junction band cover the gap was also tried and
  reverted**: it opens a hole at **26** `mosque` merges that were seamless. A merge may never
  part a join.
- **A road past the line further back than `merge_taper_length_m` is left alone entirely.**
  Pulling a 70 m lane sideways is not a merge correction. This was read as two carriageways of
  different widths mapped as separate ways — `mosque` way `935525163` running 1.75 m off, half a
  lane, for 115.6 m across four merges — and **that reading was wrong**: `935525163` is a
  two-lane stretch of Persiaran Perdana between three-lane stretches of it, so its block sat half
  a lane off both. v25's `_aligned_blocks` puts it where the road it carries on from is, and the
  four names the workspace-backed test used to exclude are gone from it.
- **Only a single continuation counts.** `entry_lanes` / `exit_lanes` name a lane for a direct
  continuation and a connector for a junction movement; a fork has no one road behind it, and a
  junction movement is another lane's traffic rather than this road carrying on.
- **The pull runs before `_tapered_line`**, so the taper's move is along the lane rather than
  back out across it. `_tapered_line` itself is unchanged, and `topology.py` is not involved.

10 lanes move on `mosque` and 8 on `junction-1`; three on each are lanes the merge code did not
previously own. See
`docs/mapping-algo-changes/2026-08-15-19:15:13-a-merging-road-crossed-the-lane-it-was-joining.md`.

### Where a lane block sits across the way line is surveyed, not inferred (v24)

`_lane_offset` **centres** a one-way carriageway's block on the OSM line. That is an inference
about where the tarmac is, applied to each way on its own — so two ways drawn on the same line
with different lane counts get blocks of different widths balanced about the same point, and
every lane that continues between them steps **half a lane-width** sideways on a road that is
dead straight. `_merge_taper_plan` reads that step as a gap and `_tapered_line` spends it; at
`merge_taper_length_m` 30 m against a 24.4 m lane the taper is longer than the lane, so the whole
lane becomes the slope rather than a straight line with a bend in it. Keith reported it as lanes
that kink instead of following the centreline.

**`placement` is the survey of it and was never read.** `placement=middle_of:2` on mosque way
776079597 puts idx0 at +0.00 and idx1 at +3.50 — exactly where the three-lane approach's
surviving lanes already are. Four ways carry the tag across both extracts, all Persiaran Perdana:
mosque 776079597 and 1250683199 (`middle_of:2`) and 776022253 (`right_of:2`, in both). Reading it
took the bend at six straight joins from 4.02°/5.29°/2.60° to ≤ 0.09°, with no connector, finding
or status changed. Four things not to re-derive:

- **OSM numbers placement lanes 1..n from the left in the way's direction.** With
  `driving_side=left` our idx0 is offside, which is the *right* of travel, so idx0 is OSM lane
  `count − idx`; right-hand traffic makes it `idx + 1`. The driving side renames lanes rather
  than moving tarmac, so the two orders **reverse** rather than negate — unlike the centred
  layout, where the block is symmetric and reversing and negating are the same thing.
- **One-way carriageways only.** On a two-way way the tag numbers lanes across both directions
  and the backward block runs the other way, flipping what "left" means. Neither extract has that
  case, and a block on the wrong side of the road is worse than one centred on the line.
- **`transition`, an out-of-range lane number and anything unparseable fall back to centring.**
- **A lane that stops being tapered moves.** Untagged way 776022254 is straight now that 776022253
  sits where the tag says, and the 2.6° its taper used to spend has moved to its junction with way
  776021086, where the road genuinely turns +5.07°. Ramp 182502392's `_uncrossed_lanes` pull aims
  at the new position, redistributing its interior bends (22.53°→13.05°, 19.50°→29.92°) while its
  worst stays 35.08°.

That left **19 straight joins on mosque and 9 on junction-1** still stepping, on ways OSM never
tagged — the second half of what Keith reported, fixed in v25 below. See
`docs/mapping-algo-changes/2026-08-15-22:21:37-a-lane-block-was-centred-where-the-survey-placed-it.md`.

### A road that carries on takes its position from the road behind it (v25)

Where a block sits is decided per way, from that way's own tags — a local decision about
something that is not local. **A road is a chain of ways, and where its tarmac lies is a property
of the road.** `_aligned_blocks` runs immediately before `_merge_taper_plan`, works out which
block feeds which, and where a block's position is settled by the road behind it, translates it
there. It moves centrelines only; the `redrawn` set and the single `_lane_surface` rebuild below
it already re-derive the surfaces.

It cannot run while the lanes are built — it reads the feeder graph, which is not settled until
every movement has been filtered, restored, side-resolved and either kept or forbidden — and it
must run before the taper, so the taper finds nothing left to close. Blast radius: **10 of 405
mosque lanes** (ways 776021087 and 935525163) and **4 of 285 junction-1 lanes** (776021087), all
by exactly 1.75 m, `aligned_lanes` in `feature_counts`.

Three guards, and **each is a road this moved wrongly before the guard existed**:

- **The destination must have exactly one feeder.** More than one is a merge, where the joining
  way has its own line and closing the gap is the taper's job. A source that *also* goes
  elsewhere is fine — a lane peeling off is why the counts differ at all.
- **Both sides must be centred one-way carriageways.** A two-way block sits half a carriageway
  off its line *by design*, so comparing it to a centred one reads the straddle as an error.
  Without this, junction-1's one-way `106667716` was shifted 1.75 m off its own line across seven
  edges to suit two-way `1016771782`.
- **The join must be straight**, measured by `_join_turn_degrees` because a continuation link
  carries no angle. `1016771782` into `106667716` bends **22.24°** and is still a direct
  continuation: "carries straight on at a node that is not a decision node" is a far looser test
  than "the same block position applies to both".

A join whose lane pairs disagree is skipped — mosque `777816410` into `777816409` pairs idx0→idx0
and idx1→idx2, a lane appearing *between* them, and no translation satisfies both. Within a
component a `placement`-tagged block never moves (the survey outranks the inference) and
otherwise **the widest block stays**, the same rule `_merge_taper_plan` applies; anchoring on the
majority instead moved three-lane 935525164 to suit the two-lane stretch beside it.

**`ALIGNMENT_MAX_TURN_DEG` is 10.0 and is not `side_movement_min_degrees`.** `classify_movement`
calls anything under 35° `through`, and the lane peeling off at node 13946726031 leaves at
−17.79° as a `through` movement. Swept: 5° and 10° agree exactly, 15° pulls in way 859429322, and
**20° pulls in the slip roads** (182502392, 1530245743, 182502406, 182502423, 191861354), which
must never be dragged onto the road they join.

Two costs worth not re-deriving. Moving a block further from its way line **opens the mitre at
that way's own interior bends** — 776021087's two edges go from 3.09°/0.133 m to 4.36°/0.265 m —
which is unavoidable for any lateral shift and lands mid-distribution (median gap at a direct
continuation is 0.213 m, median bend 4.92°). And the remaining steps are all at merges, where the
taper is correct. See
`docs/mapping-algo-changes/2026-08-16-01:58:09-a-road-that-carries-on-re-centred-on-its-own-line.md`.

### Two carriageways going opposite ways get a median, and roads shove each other over (v26)

`_lane_offset` decides where a block sits across its way from **that way's own tags alone**, with no
knowledge of what else is already in the corridor, and nothing downstream ever asks. v24 read
`placement`, v25 read the road behind — both about one road against its own line. Neither can see
the road on the other side of the median, so mosque's median right-turn link `859429322` /
`859429321` was laid **3.03 m inside** Persiaran Perdana's SW carriageway. Keith: *"these lanes eat
into the south-west bound lanes going in the opposite direction… they don't have to be touching."*
35 opposing pairs overlapped on mosque and 19 on junction-1; all of them clear **1.00 m** now.

`_separated_roads` runs between `_aligned_blocks` and `_merge_taper_plan` and moves a **whole road
bodily kerbward** — centrelines only, so nothing bends. Kerbward is always the direction: an
opposing carriageway is on a lane's offside, so both roads moving kerbward always opens the gap,
every shift is ≥ 0, and an opposing pair with room needs no constraint at all. Demands (opposing,
offside) must reach `SEPARATION_TARGET_M`; pushes (same direction, kerbward) bound the difference by
`max(clearance, 0)` so a slip already landing on the lane it joins is only stopped from getting
worse; squeezes (opposing, kerbside) bound the sum. A shortfall goes to the road with **fewer
lanes**, the rule `_merge_taper_plan` and `_aligned_blocks` already apply.

Six things not to re-derive, each a version that was built and measured first:

- **The unit that moves is coarser than an `_aligned_blocks` component.** Those chain across
  straight *single-feeder* joins, so a way splits wherever something merges into it partway along;
  different shifts on the halves opened a **4.03 m step inside way `859429321`**, three inside
  `756118317` and one inside `1016771782` — the v24/v25 defect returning. `_road_components` makes
  every block of one way in one direction one road **unconditionally**, then chains continuations
  and shallow `through` connectors on top.
- **A road is a whole street, both directions of it, and getting that wrong tore 22 of mosque's
  two-way ways open along their own centreline.** Kerbward is north for a street's eastbound half
  and south for its westbound one, so a shift given to one and not the other parts them. The
  same-OSM-way exclusion below cannot catch it: a street's seam is a property of the *street*, so
  where OSM splits one into two ways the seam is read **across the boundary** — two readings of
  **1 mm and 4 mm** moved a 26-lane road 1.001 m and a 2-lane road 1.006 m. Both directions of a
  way are now one road (33 roads on mosque, 23 on junction-1), which makes the seam a same-road
  pair, and `_two_way_roads` pins those roads to a budget of **zero**, because the only shift that
  keeps a street whole is none. It costs nothing measurable: **no separation demand on either
  extract touches a two-way street** — a street shields itself, its two halves occupying each
  other's offside. Where one ever does, a demand whose yielding road is out of budget passes to
  the other road; where both are streets, the warning reports it. See
  `docs/mapping-algo-changes/2026-08-16-18:10:01-a-two-way-street-was-parted-down-its-own-middle.md`.
- **A nearest point on the other lane's own end means they are not alongside each other.** Two lanes
  running away from a shared node meet there, and the perpendicular part of a mostly-along distance
  says nothing: junction-1's `776021086` and `1530245742` are **13.35 m apart** and read as 2.98 m
  of overlap without the guard. `tiny.osm`'s ways 10 and 11 are the same shape, and the fixture test
  is what caught it.
- **One reading of the geometry is not enough** — solved once it left 16 overlaps on mosque and 3 on
  junction-1 and made three pairs *worse* (`182502377` × `191861354`, clear → 2.51 m of overlap).
  Roads meeting at an angle lose part of each shift to it, and moving a road changes *where* two
  roads come closest, so it re-reads and re-solves up to `SEPARATION_ROUNDS`.
- **A shift may be negative, or a road drifts.** Add-only rounds left the link **4.68 m** clear of a
  carriageway it was asked to clear by 1.00 and squeezed the other side by 0.98 m. Each round now
  has a floor (how far the road has already moved) as well as a ceiling, and *every* opposing pair
  carries a demand — negative where there is slack — so the layout knows what may be given back.
- **Each round lays every road out from where it started.** `offset_curve` is not its own inverse on
  a curved line.
- **Four kinds of pair abut by design and are excluded**: same road; **same OSM way**, because a
  two-way way puts its two directions either side of its line and they meet *on* it; joined by a
  connector or continuation; and cut back to `MIN_TRIMMED_LANE_M`, the interior of a junction where
  traffic crosses. The last is the clamp, **not** a length threshold — the distinction
  `conversion._stub_lanes` draws — and alone it takes mosque's demand set from 124 pairs to 104.

Blast radius **161 of 405 mosque lanes over 10 roads and 132 of 285 junction-1 lanes over 7**, worst
shift 4.36 m and 2.72 m, reported as `separated_lanes` / `separated_roads`. Perdana's SW carriageway
does not move at all on mosque — the fewer-lanes rule put it on the link and the NE side. Ids,
counts and connector statuses are unchanged, and **continuity improved**: total sideways step at a
direct continuation 47.63 → 42.40 m on mosque and 44.13 → 40.77 m on junction-1. See
`docs/mapping-algo-changes/2026-08-16-14:32:44-a-carriageway-was-laid-over-the-traffic-coming-the-other-way.md`.

### `ego_route` still turns over the gate on two 2 m clamped lanes

`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` fails: **3 of 396 swept routes
turn more than 30° at a vertex, worst 50.92°**. It is not v24 and not v25 — sweeping the same
1,500 seeded pairs on models built at v23, v24 and v25 gives the identical three routes. It
became visible on 2026-08-16 only because `workspaces/junction-1/lane-model/reviewed.json`, which
that test reads, was rebuilt; the model it had been passing against was **v17**.

All three run 777160375 idx0/3 → 777159293 idx0/1 → 777160374 idx0/1 → 777159294 idx0/2, where a
**−88.97°** right turn is taken across two lanes `MIN_TRIMMED_LANE_M` clamped to **2.0 m**, with
2.6 m and 10.6 m gaps either side. Undiagnosed further, and Keith's to judge.

### Starved middle lanes: mostly fixed, one left

Two allocation rules now run before the proportional mapping, and between them they
cover both shapes where the lane arithmetic closes:

- `_balanced_approach_assignment` — **one** approach across **several** destinations
  (a lane peeling off cannot also be the straight-on lane). Added in v10.
- `_balanced_merge_assignment` — **several** approaches into **one** destination
  (a merging link must not land on a lane the main road already feeds). Added in v11.

Both count only the destinations a node-via restriction *leaves* (v21) — see "A restriction has
to be known before the lanes are dealt out" above. Counting a road nobody may take turns a clean
three-into-three into an ambiguous three-into-six and neither rule fires.

Where the counts do **not** close, `_merge_side` (v20) still says which *side* of the
destination an approach lands on: a road joins another from one side and has to land on
that side, or its traffic crosses the traffic it is merging with. It ranks the approaches
with `_kerb_first_key` rather than reading an angle, because **which side a road joins
from is a comparison between the roads meeting at the node, not a property of one of
them** — and it has an answer at any angle, including zero.
`side_movement_min_degrees` (10°) asks "is this a turn?", which is the wrong question for
a merge: mosque `935525161` joins at **−0.01°** and was called sideless, so
`_mapped_lane_index`'s `min(lane_index, count−1)` sent a kerbside single lane to index 0
and across three lanes of link traffic. See
`docs/mapping-algo-changes/2026-08-13-01:52:14-a-road-that-merges-dead-straight-is-given-no-side.md`.

The order of authority where a side is decided is **`turn:lanes` → `_merge_side` →
`movement_side`**. The tag stays on top; a surveyed value is still evidence and the
ranking is still an inference. Where the merge side decides, the block dealt inward by
`_side_block_offset` is the *whole* approach — nothing tagged it, so all of it merges
together.

`_mapped_lane_index` (`generation.py`) is unchanged and still **cannot produce a
middle index**: for a 2-lane approach onto a 3-lane destination
`round(idx × (3−1) / (2−1))` gives `idx0→0`, `idx1→2`, and index 1 is unreachable for
*any* input. It now only decides **oversubscribed** approaches — where the counts do
not close, a lane genuinely serves more than one movement, and the ambiguity is
reported rather than resolved. Clean diverges and clean merges no longer go through it.

**Fixed in v17 — the last diagnosed starved lane.** `39619063` idx1/2 `c0530c25fd` at
node 1927184814 is now fed by `027a3ef89e3e7b88`.

Way `756118314` is tagged `turn:lanes=right|right`, so both its lanes carry
`turn_permissions=['right']`. An explicit `turn:lanes` value outranks geometry in
`movement_side()`, so **both** lanes are labelled `offside`, and
`side_lane_index("offside", 2)` returned `0` for both — they collided on one target.
The approach is oversubscribed (2 lanes arriving, 5 lanes of destination capacity at
the node), so neither balanced rule reaches it and `_mapped_lane_index` decides.

`_mapped_lane_index` now takes the block of lanes an explicit tag puts on that side and
deals them from the side inward, so a side says where a block **starts** rather than
where every lane in it goes. A block of one is unchanged, so only a genuine collision
moves. See
`docs/mapping-algo-changes/2026-08-09-16:44:44-a-side-picks-where-a-block-starts.md`.

Two blockers remain at that node, and correctly: `turn:lanes=right|right` names a right
turn that is not available there, and that disagreement is Keith's to judge. **Never fix
a tag-versus-geometry conflict by making the finding stop being raised** — fix the
mapping and keep the review.

Two cautions when re-measuring this. A previous version of this table also listed
`776021087` idx0/2 `8caffc7049` at node 13946726031; under the criterion "no connector
and no continuation names it as a target" that lane was **already fed at v9**, so it
was either counted under a different criterion or listed in error. And `junction-1`
still has **21** lanes fed by nothing; most are network-boundary lanes rather than
defects, and none of the remainder has been diagnosed.
