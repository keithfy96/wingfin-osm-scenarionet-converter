# A lane block was centred on the way line where the survey had placed it

- **Date:** 2026-08-15 22:21:37
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_lane_offset`, `_placement_edge_distance`)
- **Generator version:** direct-osm-stage2-v23 → direct-osm-stage2-v24

## Symptom

Keith pointed at two pairs of lanes that slope sideways instead of running straight down the
road, and asked why the straight-through lanes do this when even the turning lanes follow the
line: `77c247b634f545e1` / `b8d3ab5cd5456a83` on **mosque**, and `8caffc7049c80776` /
`e2e4ac908f46a5ab` on **junction-1**. On junction-1 he described the kink as pointing north.

Both are the same shape: a 3-lane way continues dead straight into a 2-lane way after the
offside lane peels off, and every surviving lane is shifted 1.75 m sideways across the join.

```
 mosque · node 7241032487 · Persiaran Perdana        travel is LEFT → RIGHT on this page
 way 777816409 (3 lanes) ──► way 776079597 (2 lanes)      OSM lines are collinear: +0.118°
 lane indices run centre-out: idx0 hugs the centreline (offside), idx(n−1) is kerbside
 + = left turn · − = right turn · left-hand traffic
 numbers are metres from the OSM way line, + measured to the LEFT of travel (the kerb side)

 ═══════════════════════════════════════ ┊ ══════════════════════════════════════ KERB ══
   kerbside edge  +5.25 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ┊ ┄┐
                                         ┊  └┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  +3.50  kerbside edge

   670f5907  idx2/3  +3.50  ─────────────►─╮      connector 75ba45ba79  through  +0.12°
                                         ┊ ╰────────────►  b8d3ab5c  idx1/2  +3.51 → +1.75
                                         ┊       ✗ slides 1.75 m sideways over all 24.4 m
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   f7013922  idx1/3  +0.00  ─────────────►─╮      connector 6e64edf435  through  +0.12°
                                         ┊ ╰────────────►  77c247b6  idx0/2  +0.01 → −1.75
                                         ┊       ✗ slides 1.75 m sideways over all 24.4 m
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   08aafd41  idx0/3  −3.50  ──╮          ┊
                              ╰──► LEAVES: way 39620642 · Persiaran Kenanga · 1 lane
   offside edge   −5.25 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ┊ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  −3.50  offside edge
 ═══════════════════════════════════════ ┊ ═════════════════════════════════ CENTRELINE ══

   the offside lane departs, so the road narrows on the OFFSIDE only and the kerb should not
   move.  Centring the 2-lane block instead narrowed it symmetrically: BOTH edges came in by
   1.75 m and both surviving lanes were dragged with them.

   way 776079597 is tagged  placement=middle_of:2 , which puts the OSM line down the middle of
   the offside lane → idx0 at +0.00, idx1 at +3.50 — exactly where the approach lanes already
   are.  The generator never read the tag.
```

The junction-1 case is the same drawing with different IDs and no tag: way 776021089 (3 lanes,
idx0 leaving for the `turn:lanes:forward=right` slip 1530245743) into way 776021087 (2 lanes),
OSM lines collinear to −0.009°, both surviving lanes pulled 1.75 m to the offside — which on a
westbound road is north, the direction Keith reported.

## Fundamental cause

`_lane_offset` centred a one-way carriageway's lane block on the OSM way line:
`offset = side_sign · (idx + 0.5 − 0.5·count) · width`. That is an **inference** about where the
tarmac sits relative to the drawn line, and it is applied to every way independently.

Two ways drawn on the same line with different lane counts therefore get blocks of different
widths balanced about the same point, so every lane that continues between them must step half a
lane-width sideways — 1.75 m at a 3.5 m lane — even though the road is straight and no lane has
moved on the ground. `_merge_taper_plan` then reads that step as a gap to close and
`_tapered_line` spends it linearly. `merge_taper_length_m` is 30 m and these lanes are 24.4 m and
26.6 m long, so the taper is longer than the lane: **the whole lane becomes the taper**, which is
why it is a slope end to end rather than a straight line with a bend in it.

OSM has a tag for exactly this — `placement`, which names where the way line crosses the
carriageway — and the generator read it nowhere. Centring is the inference; `placement` is the
survey. This is the same standing rule as "surveyed tags outrank inferred angles": the tag must
win where it exists.

## Fix

`_lane_offset` takes a `placement` argument, and `_placement_edge_distance` parses it. OSM
numbers placement lanes 1..n **from the left in the way's direction**, so lane *k* spans
`[(k−1)·w, k·w]` from the block's left edge and the tag names an edge or the middle of one:
`middle_of:k` → `(k−0.5)·w`, `left_of:k` → `(k−1)·w`, `right_of:k` → `k·w`. With
`driving_side=left` the offside is the right of travel, so our `idx0` is OSM lane `count − idx`;
with right-hand traffic it is `idx + 1`. Driving side renames the lanes rather than moving the
tarmac, so the two orders **reverse** rather than negate — unlike the centred layout, where the
block is symmetric and the two are the same thing.

Three things not to re-derive:

- **Honoured on a one-way carriageway only.** On a two-way way the tag numbers lanes across
  *both* directions and the `direction=backward` block runs the other way, which flips what
  "left" means. Neither extract has a two-way way carrying `placement`, and a block put on the
  wrong side of the road is worse than one centred on the line.
- **`transition`, an out-of-range lane number and anything that does not parse fall back to
  centring.** Acting on a value that was not understood throws the whole block sideways with
  nothing on the map to say why.
- **Nothing downstream changed.** `_lane_surface`, `_merge_taper_plan`, `_tapered_line` and
  `_uncrossed_lanes` all work off the centreline they are handed. The taper simply finds nothing
  left to close at these joins.

Only four ways carry the tag across both extracts, all Persiaran Perdana: mosque 776079597 and
1250683199 (`middle_of:2`), and 776022253 (`right_of:2`), which is in both.

## Verification

Both `source/map.osm` still matched their manifests (mosque `00e30460…`, junction-1
`4607fd469e…`). Both workspaces regenerated and diffed against the v23 models.

**The reported defect is gone.** Bend at the join, measured across the six straight runs where a
placement-tagged way meets what feeds it:

| join | before | after |
| --- | --- | --- |
| mosque 777816409 idx1 → 776079597 idx0 (Keith's) | 4.02° | 0.07° |
| mosque 777816409 idx2 → 776079597 idx1 (Keith's) | 4.02° | 0.09° |
| mosque 1250683199 idx0/idx1 → 184015392 | 5.29° | 0.00° |
| mosque 776022253 idx0/idx1 → 776022254 | 2.60° | 0.03° |
| junction-1 776022253 idx0/idx1 → 776022254 | 2.80° | 0.17° |

`77c247b634f545e1`'s offset from its own way line runs `+0.014 → −0.005 m`, against
`+0.014 → −1.750` before. junction-1's `8caffc7049c80776` still runs `−0.001 → −1.745`: neither
way there carries `placement`, and Keith's decision was to read the tag and only the tag rather
than infer an answer where the survey is silent.

**Nothing regressed.**

- lanes 405 / 285, connectors 200 / 116, findings 228 / 144 — all identical before and after,
  with no id lost or gained
- **no connector changed status**, and the finding rule/severity multiset is unchanged
- **no endpoint gap across any of the 75 / 35 shallow `through` joins got wider**
- 17 mosque lanes and 13 junction-1 lanes moved, all by ≤ 1.75 m and all on the tagged ways or
  on what tapers onto them
- `uv run pytest` 380 passed; `uv run ruff check` clean

**Two consequences worth recording rather than re-discovering.**

- Way 776022254 (untagged, 2 lanes) stopped being tapered, because 776022253 now sits where it
  does. Its lanes are straight along their own way, and the 2.6° that the taper used to spend on
  them has moved to their downstream join with way 776021086, where the road genuinely turns
  +5.07°. A bend moved off a straight run onto a junction.
- Ramp 182502392's `_uncrossed_lanes` correction now aims at where the road it merges into
  actually is, so its interior bends redistributed: 22.53° → 13.05° at one vertex and 19.50° →
  29.92° at the next. The **worst bend on that ramp is unchanged at 35.08°**, and
  `test_no_merging_road_on_the_real_maps_ends_past_the_lane_it_joins` still passes.

**19 straight joins on mosque and 9 on junction-1 still carry a 1.75 m step** — including
junction-1's, the second of the two Keith showed. Those ways carry no `placement`, and aligning
them would be an inference: enforcing continuity everywhere moves 79 of 405 mosque lanes and 88
of 285 junction-1 lanes, by up to 3.50 m, including lanes that are correct today.

Tests added in `tests/unit/test_generation.py`:
`test_a_placement_tag_moves_the_block_off_the_centre_of_the_line` (the parse and both driving
sides, with every unusable value falling back to centring) and
`test_placement_tagged_ways_on_the_real_maps_line_up_with_what_feeds_them` (the six joins above,
asserted on whichever workspaces are present).
