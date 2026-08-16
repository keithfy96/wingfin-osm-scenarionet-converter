# A carriageway was laid over the traffic coming the other way

- **Date:** 2026-08-16 14:32:44
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_road_components`, `_lateral_neighbours`,
  `_separation_layout`, `_separated_roads`, `_opposing_overlap_findings`, `_closest_on`,
  `_lane_samples`, `LateralPair`), tests/unit/test_generation.py
- **Generator version:** direct-osm-stage2-v25 → direct-osm-stage2-v26

## Symptom

Keith pointed at two outgoing right-turn routes at mosque node `1927184932` (Persiaran Perdana ×
Jalan Ara SD 7/3) — `a3d3f905fff8867f` → `f22004d38be6976a` → `74cfd14585b4321d` and
`56b8fe7151084e18` → `f95fa900dabece5e` → `80ffe7dbb18147f7` — which *"eat into the south-west
bound lanes going in the opposite direction"*, and asked that lanes not overlap, **especially**
where they carry traffic the opposite way. He then supplied the rule himself: *"since these are
not the same lane, but actually two separate carriageways, does it not make more sense to have a
larger gap between them? they don't have to be touching."* His figure was **1.0 m**, everywhere on
the map.

Both routes run on the median right-turn link `859429322` → `859429321`, which leaves Perdana's
NE carriageway at node `8010982925`, passes the junction and rejoins at `8010982931`.

```
 mosque · Persiaran Perdana at node 1927184932 · cross-section looking along NE travel
 left-hand traffic · idx0 hugs the centreline (offside) · idx(n−1) is kerbside
 + = left turn · − = right turn
 metres across the section, 0 = lane 80ffe7dbb18147f7's centreline BEFORE, + = KERBWARD of it
 every lane 3.50 m wide, so each edge sits 1.75 m either side of its centreline

 ══════════════════════════════════════════════════════════════ KERB (NE side) ══
   +9.55 │ 7ca159f8 │ way 935525163 idx1/2 │ NE ─────────────────────►
   +6.05 │ 6fd8e4f1 │ way 935525163 idx0/2 │ NE ─────────────────────►
                                      2.44 m of median, unused
   +0.00 │ 80ffe7db │ way 859429321 idx0/1 │ NE ──►   ◄── Keith's lane
   −0.81 │ f95fa900 │ way 859429321 idx0/1 │ NE ──►   ◄── Keith's lane, 2.00 m clamped stub
         ╞═════════════════════════════════════════════════════════════╡
         │  ✗  3.03 m OF THIS LANE IS UNDER THE OPPOSING CARRIAGEWAY ✗ │
         ╞═════════════════════════════════════════════════════════════╡
   −0.47 │ 8e353b17 │ way 1173001828 idx0/3 │ ◄───────────────────── SW
   −3.97 │ 51202b6a │ way 1173001828 idx1/3 │ ◄───────────────────── SW
   −7.47 │ 8be0deef │ way 1173001828 idx2/3 │ ◄───────────────────── SW
 ══════════════════════════════════════════════════════════ KERB (SW side) ══

   the two carriageway lines are 9.75 m apart; three 3.50 m lanes each side plus the
   link needs 24.5 m of corridor, and the free median beside the link is 2.44 m wide.

 AFTER: the link and the NE carriageway move +4.06 m kerbward together, the SW carriageway
 does not move, and every lane of the link clears it — 80ffe7db at +1.002 m, cb23e2cc
 +1.148, a3d3f905 +0.977, 0134fba8 +1.033, 952f43be +1.144, cdc75483 +1.289.  The link's
 own median against the NE carriageway is kept: 80ffe7db reads 2.442 → 2.465 m.
```

Measured over the two extracts before the change, counting only pairs that genuinely run
alongside each other: **35 opposing lane pairs overlapping on mosque and 19 on junction-1**, worst
−3.030 m and −1.658 m, with **58 and 23** pairs closer than the 1.0 m Keith asked for.

## Fundamental cause

`_lane_offset` decides where a block of lanes sits across its way from **that way's own tags,
alone**. It is a per-way inference made with no knowledge of what else is already in the corridor,
and nothing downstream ever asks. v24 taught it to read `placement` where the survey says where the
tarmac is, and v25 taught it to take its position from the road it carries on from — but both of
those are about *one road's* position relative to its own line and its own past. Neither can see the
road on the other side of the median.

So a way drawn down the middle of a dual carriageway gets a lane block centred on its line, the
carriageway coming the other way gets a block centred on *its* line, and where the corridor is not
wide enough for both, the generator lays one on top of the other and reports nothing. Every overlap
found is of that kind. It is not a defect of one way's tags: `859429322` and `1173001828` are each
placed correctly *given only themselves*, and only their relationship is wrong.

## Fix

A pass between `_aligned_blocks` and `_merge_taper_plan` that opens a median between carriageways
running in opposite directions, moving each **whole road bodily kerbward**. It moves centrelines
only; the existing `redrawn` set and single `_lane_surface` rebuild re-derive polygons, boundaries
and connector curves.

**Kerbward is always the direction, and that is what makes the layout solvable.** An opposing
carriageway sits on a lane's offside, so moving both roads kerbward in their own frames always opens
the gap between them: every shift is non-negative and an opposing pair that already has room needs
no constraint at all. Three kinds of constraint follow, and `SEPARATION_TARGET_M` is Keith's 1.0 m:

| | when | constraint |
| --- | --- | --- |
| **demand** | opposing, the other lane on the offside | `s(a) + s(b) ≥ target − clearance` |
| **push** | same direction, the other lane kerbward | `s(a) − s(b) ≤ max(clearance, 0)` |
| **squeeze** | opposing, the other lane on the *kerb* side | `s(a) + s(b) ≤ max(clearance, 0)` |

`max(clearance, 0)` is why a slip road already landing on the lane it joins is stopped from getting
worse rather than made to separate. A shortfall goes to the road with **fewer lanes** — the rule
`_merge_taper_plan` and `_aligned_blocks` already apply, so the through carriageway is never the one
to give way — and a shrink pass then lowers every road to the least its own constraints allow.

Six things not to re-derive, and each is a version of this that was built and measured first:

- **The unit that moves is coarser than an alignment component, and has to be.** `_aligned_blocks`
  chains blocks across straight *single-feeder* joins, so a way is cut in two wherever anything
  merges into it partway along. Give the halves different shifts and the way kinks by the
  difference: measured, that opened a **4.03 m step inside way `859429321`**, three more inside
  `756118317` and one inside `1016771782` — the defect v24 and v25 exist to remove.
  `_road_components` therefore makes every block of one OSM way in one direction one road
  unconditionally, and chains across continuations and shallow `through` connectors on top of that.
  39 roads on mosque, 32 on junction-1.
- **A nearest point that lands on the other lane's own end means the two are not alongside each
  other at all.** Two lanes running away from a shared node have their closest approach there, and
  the perpendicular component of a distance that is mostly *along* the road says nothing about the
  gap: junction-1's `776021086` and `1530245742` are **13.35 m apart** and read as 2.98 m of
  overlap without the guard. It is also what the fixture caught — `tiny.osm`'s two-way way 10 and
  the one-way way 11 that carries on from it read as 1.75 m of overlap and were parted, breaking
  `test_generate_lane_model_writes_deterministic_stage_2_artifacts`.
- **One reading of the geometry is not enough.** A layout solved once left **16 overlaps on mosque
  and 3 on junction-1**, and made three pairs worse — way `182502377` against slip `191861354` went
  from clear to 2.51 m of overlap. Both reasons come from treating a pair as two parallel lines:
  roads meeting at up to `SEPARATION_PARALLEL_MAX_DEG` lose part of each shift to the angle between
  them, and moving a road changes *where* two roads come closest. So the geometry is re-read and
  re-solved, up to `SEPARATION_ROUNDS`.
- **A shift may be negative, or a road drifts.** With each round only ever adding, mosque's link
  ended **4.68 m** clear of a carriageway it was asked to clear by 1.00, and squeezed the one on its
  other side by 0.98 m. Each round now gets a floor as well as a ceiling — the floor being how far
  the road has already moved, so it can be handed back as far as its own line and no further — and
  every opposing pair carries a demand, negative where there is room to spare, so the layout knows
  how much may be returned.
- **Every round lays each road out from where it started**, not from where the last round left it.
  `offset_curve` is not its own inverse on a curved line, so moving a road out and part of the way
  back would leave it a slightly different shape each time.
- **Four kinds of pair abut by design and are excluded**: lanes of one road; lanes sharing an OSM
  way, because a two-way way puts its two directions wholly either side of its line and they meet
  *on* it; lanes a connector or continuation joins, because two lanes that meet are meant to touch;
  and lanes cut back to `MIN_TRIMMED_LANE_M`, which are the interior of a junction box where traffic
  crosses on purpose. The last is the clamp, not a length threshold — the same distinction
  `conversion._stub_lanes` draws — and it alone takes mosque's demand set from 124 pairs to 104.

`MAX_ROAD_SHIFT_M` (5.0 m) is a backstop: past it a road is not being nudged out of an overlap but
thrown across the map, and the pair comes back as a **warning** finding,
`opposing_carriageways_overlap`, instead. Neither extract reaches it. All the constants are module
level and deliberately not `ConverterConfig` fields, because `configuration_checksum` feeds
`generation_fingerprint`.

## Verification

Both `source/map.osm` still matched their manifests (mosque `00e30460458d`, junction-1
`4607fd469eba`). Both workspaces regenerated and diffed against the v25 models, measured by a script
written independently of the generator.

**The reported defect is gone, and so is every other one of its kind.**

| | mosque | junction-1 |
| --- | --- | --- |
| opposing lane pairs overlapping | 35 → **0** | 19 → **0** |
| …closer than 1.0 m | 58 → **0** | 23 → **0** |
| worst clearance between opposing carriageways | −3.030 m → **+1.000 m** | −1.658 m → **+1.000 m** |
| `opposing_carriageways_overlap` findings raised | 0 | 0 |

Keith's link, worst clearance against Perdana's SW carriageway: `80ffe7db` −3.029 → **+1.002 m**,
`cb23e2cc` −2.879 → +1.148, `a3d3f905` −2.317 → +0.977, `0134fba8` −2.286 → +1.033, `952f43be`
−2.172 → +1.144, `cdc75483` −1.932 → +1.289. Its two `MIN_TRIMMED_LANE_M` stubs `f22004d3` and
`f95fa900` are excluded by design and end at −0.060 m and +0.570 m, inside the junction box.

**Nothing regressed.**

- lanes 405 / 285 and connectors 200 / 116, **every id identical**; **no connector changed status**;
  findings 228 / 144 with **none gained or lost**
- **no lane's worst interior bend got worse** on junction-1, and one on mosque by 0.01°
  (`969622d7`, way 935525161, 6.42° → 6.43° — the offset's own rounding)
- **continuity improved on both extracts.** Total sideways step across every direct continuation:
  mosque **47.63 → 42.59 m** (worst 3.01 → 2.00 m), junction-1 **44.13 → 40.77 m** (worst unchanged
  at 4.83 m, on way `107911720`, which did not move). Total end-to-end gap 518.34 → 508.21 m and
  458.34 → 456.60 m
- **same-direction overlaps did not rise**: mosque 25 → 25, junction-1 12 → 10
- the v24 `placement` joins and the v25 alignment joins are unchanged to the hundredth of a degree —
  `776021090 → 776021087` 0.01°, `776022253 → 776022254` 0.03° / 0.17°, `777816409 → 776079597`
  0.09°, `935525164 → 935525163` 0.00°, `1250683199 → 184015392` 0.00°

**Blast radius: 189 of 405 mosque lanes across 12 roads and 132 of 285 junction-1 lanes across 7.**
Worst shift 4.36 m (mosque slip `182502392`) and 2.72 m (junction-1 `756118316`/`756118314`).
Persiaran Perdana's SW carriageway does not move at all on mosque and by 0.61 m on junction-1: the
fewer-lanes rule put the movement on the link and the NE side. `separated_lanes` and
`separated_roads` in `feature_counts` report it. 16 joins on mosque and 3 on junction-1 crossed
`conversion._KERB_GAP_CLOSE_M` (0.35 m), all inside a single way, where moving a block further from
its line opens the mitre at that way's own interior bends — the same unavoidable cost v25 recorded.

`uv run ruff check` clean. `uv run pytest`: **398 passed, 1 failed** —
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, which is **not this change**: it
reads `workspaces/junction-1/lane-model/reviewed.json`, which was not regenerated, and it fails
identically before and after (3 of 396 swept routes over 30°, worst 50.92°, on two
`MIN_TRIMMED_LANE_M` lanes). It is already recorded as open in CLAUDE.md.

Tests added in `tests/unit/test_generation.py`:
`test_two_carriageways_going_opposite_ways_are_parted_by_a_median`,
`test_separation_leaves_alone_what_abuts_by_design` (all four exclusions),
`test_a_road_is_every_block_of_one_way_plus_what_carries_straight_on`, and
`test_no_carriageways_on_the_real_maps_are_laid_over_the_traffic_coming_the_other_way`, which
asserts both halves on whichever workspaces are present — no opposing pair short of the target, and
the total sideways step at direct continuations no worse than v25 left it.
