# A road that simply carries on re-centred on its own line

- **Date:** 2026-08-16 01:58:09
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_aligned_blocks`, `_join_turn_degrees`, `_lane_block`)
- **Generator version:** direct-osm-stage2-v24 → direct-osm-stage2-v25

## Symptom

The v24 change taught `_lane_offset` to read OSM's `placement` tag, which fixed the kink Keith
showed at mosque way 776079597. He came back with the other one — way **776021087**
(`8caffc7049c80776`, `e2e4ac908f46a5ab`) — still sloping, and asked why the fix had not reached
it. Because **776021087 carries no `placement` tag**, and v24 read the survey and nothing else.
The lane is in both extracts with identical geometry.

```
 mosque · node 13946726031 · Persiaran Perdana        travel is LEFT → RIGHT on this page
 way 776021090 (3 lanes) ──► way 776021087 (2 lanes)      OSM lines are collinear: −0.009°
 lane indices run centre-out: idx0 hugs the centreline (offside), idx(n−1) is kerbside
 + = left turn · − = right turn · left-hand traffic
 numbers are metres from the OSM way line, + measured to the LEFT of travel (the kerb side)
 (junction-1 is the same drawing; its approach is way 776021089 instead of 776021090)

 ═══════════════════════════════════════ ┊ ══════════════════════════════════════ KERB ══
   kerbside edge  +5.25 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ┊ ┄┐
                                         ┊  └┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  +3.50  kerbside edge

   f97d680e  idx2/3  +3.50  ─────────────►─╮      connector 71102c6652  through  −0.01°
                                         ┊ ╰────────────►  e2e4ac90  idx1/2  +3.50 → +1.75
                                         ┊       ✗ slides 1.75 m sideways over all 26.6 m
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   c51b4904  idx1/3  +0.00  ─────────────►─╮      connector 84bae9c1e5  through  −0.01°
                                         ┊ ╰────────────►  8caffc70  idx0/2  −0.00 → −1.75
                                         ┊       ✗ slides 1.75 m sideways over all 26.6 m
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   9e6aa3ea  idx0/3  −3.50  ──╮          ┊
                              ╰──► LEAVES at −17.79°: way 1530245743 · Kenanga · 1 lane
   offside edge   −5.25 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ┊ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  −3.50  offside edge
 ═══════════════════════════════════════ ┊ ═════════════════════════════════ CENTRELINE ══

   776021087 is the SAME ROAD carrying on, fed by 776021090 and nothing else, and its two
   lanes are that approach's idx1 and idx2.  Nothing on the ground moved — only the block
   re-centred, because a 2-lane block balances about the line 1.75 m from where a 3-lane one
   does.  The road heads west, so the pull is northward: Keith's "kinks upwards".
```

## Fundamental cause

Where a block sits across its way line is decided **per way, from that way's own tags**, and
centring is the answer when nothing says otherwise. It is a local decision about a thing that is
not local: a road is a chain of ways, and where its tarmac lies is a property of the road.

So two ways drawn on the same line with different lane counts get blocks of different widths
balanced about the same point, and every lane that continues between them must step half a lane
sideways on a road that never moved. `_merge_taper_plan` then reads that step as a gap to close
and `_tapered_line` spends it — over the *whole* lane where the taper (30 m) is longer than the
lane (26.6 m), which is why it reads as a slope rather than a bend.

`placement` fixed the four ways OSM had tagged. It could not fix the rest, because the missing
information is not in the tags at all: it is in **which road the road carries on from**, which
the generator already knows by the time the connectors are final.

## Fix

`_aligned_blocks` runs immediately before `_merge_taper_plan`. It groups lanes into blocks —
`(source_edge, direction)`, the unit `_lane_offset` positions — works out which block feeds
which, and where a block's position is determined by the road behind it, translates it there.
The existing `redrawn` set and the single `_lane_surface` rebuild below already re-derive
polygons and boundaries, so it moves centrelines and nothing else.

It has to run there and not while the lanes are built: it reads the feeder graph, and that is
not settled until every movement has been filtered, restored, side-resolved and either kept or
forbidden. It has to run before the taper, so the taper finds nothing left to close.

Three guards decide whether a step is a defect, and **each one is a road this moved wrongly
before the guard existed**:

- **The destination must have exactly one feeder.** More than one is a merge: the joining way
  has its own line, is its own road, and closing the gap is the taper's job. A source that
  *also* goes elsewhere is fine — a lane peeling off is the usual reason the counts differ.
- **Both sides must be one-way carriageways centred on their line.** A two-way way puts each
  direction's block wholly on its own side, so it sits half a carriageway off its line *by
  design*, and comparing that to a centred block reads the straddle as an error. Without this,
  junction-1's `106667716` — a seven-edge one-way residential road — was shifted 1.75 m off its
  own line to suit two-way `1016771782`, and the gap between them grew from 5.254 m to 5.303 m.
- **The join must be straight.** A continuation link carries no turn angle, so `_join_turn_degrees`
  measures one: `1016771782` into `106667716` bends **22.24°** across a 5.25 m gap and is still a
  direct continuation, because "carries straight on at a node that is not a decision node" is a
  much looser test than "the same block position applies to both".

A join whose lane pairs **disagree** is skipped: mosque `777816410` (2 lanes) into `777816409`
(3 lanes) pairs idx0→idx0 and idx1→idx2, a new lane appearing *between* them. That is a road
genuinely widening and no single translation satisfies both.

Within a component, a block on a way whose `placement` was honoured **never moves** — the survey
outranks the inference — and otherwise **the widest block stays put**, which is the rule
`_merge_taper_plan` already applies so the through carriageway is never bent. Anchoring on the
majority instead moved way 935525164, a three-lane carriageway, to suit the two-lane stretch
between it and the next: backwards.

`ALIGNMENT_MAX_TURN_DEG` is **10.0**, measured rather than picked. `classify_movement` calls
anything under **35°** `through`, far too loose here — the lane peeling off at node 13946726031
leaves at −17.79° and is a `through` movement. Swept over both extracts: 5° and 10° give the
identical answer; 15° pulls in way 859429322; **20° pulls in the slip roads themselves**
(182502392, 1530245743, 182502406, 182502423, 191861354), which must never be dragged onto the
road they join. It is `side_movement_min_degrees`' number and deliberately not that constant —
there the question is *which side*, which has an answer at any angle; here it is "is this a
turn?".

## Verification

Both `source/map.osm` still matched their manifests. Both workspaces regenerated and diffed
against the v24 models.

**The reported defect is gone.** Bend at the join:

| join | before | after |
| --- | --- | --- |
| mosque 776021090 idx1/idx2 → 776021087 idx0/idx1 (Keith's) | 3.78° | 0.01° |
| junction-1 776021089 idx1/idx2 → 776021087 idx0/idx1 (Keith's) | 3.78° | 0.01° |
| 776021087 idx0/idx1 → 776370584 idx1/idx2 | 3.70° | 0.02° |
| mosque 935525164 → 935525163, and 935525163's own two edges | 6.26° | 0.00° |
| mosque 935525163 → 935525162 | 2.30° | 0.00° |

**The blast radius is 2 ways on mosque and 1 on junction-1.** 10 of 405 mosque lanes
(776021087, 935525163) and 4 of 285 junction-1 lanes (776021087), every one by exactly 1.75 m.
`aligned_lanes` in `feature_counts` reports it. Lane, connector and finding counts and ids are
identical, **no connector changed status**, and **no lane acquired an interior bend**.

**The v23 `width_mismatch` workaround was this defect, and is gone.**
`test_no_merging_road_on_the_real_maps_ends_past_the_lane_it_joins` excluded mosque 935525163's
four lanes (`009a07ebe027c644`, `4aebb94cfa4ed5b1`, `6fd8e4f18611c914`, `7ca159f8f067657a`) as
"two carriageways of different widths mapped as separate ways… 1.75 m off the line for 115.6 m".
935525163 is a two-lane stretch of Persiaran Perdana between three-lane stretches of it, so the
1.75 m was the parity artefact. With the exclusion set deleted the test passes on both extracts.

**Two joins got slightly worse, and both are the same unavoidable geometry.** Way 776021087's
own two edges meet at a 3.09° bend; moving the block 1.75 m further from the way line opens that
mitre, so the bend reads 4.36° and the endpoint gap 0.133 → 0.265 m. Same for 935525163
(0.183 → 0.367 m). Measured against the population they join: the median gap at a direct
continuation is **0.213 m** on mosque with 127 of 270 already over 0.265 m, and the median bend
is **4.92°** with 150 of 270 already over 4.36°. Both land at the middle of the distribution.

`uv run ruff check` clean. `uv run pytest`: **383 passed, 1 failed** —
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, which is **not this change**.
It reads `workspaces/junction-1/lane-model/reviewed.json`, which Keith rebuilt at 01:38 on
2026-08-16, replacing a **v17** model dating from before this work. Swept over the same 1,500
seeded pairs, v23, v24 and v25 all give the identical result — 273 built, 0 refused, **3 over
30°, worst 50.92°** — so the model version is not what moved. All three routes run through
777160375 idx0/3 → 777159293 idx0/1 → 777160374 idx0/1 → 777159294 idx0/2, where a −88.97° right
turn is taken across two lanes that `MIN_TRIMMED_LANE_M` clamped to **2.0 m** with 2.6 m and
10.6 m gaps either side. That is an `ego_route` defect on short clamped lanes, it is Keith's to
judge, and it was not folded into this change.
