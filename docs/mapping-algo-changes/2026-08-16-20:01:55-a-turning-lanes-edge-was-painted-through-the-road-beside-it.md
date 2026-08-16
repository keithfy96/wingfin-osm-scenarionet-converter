# A turning lane's edge was painted through the road beside it

## Symptom

Keith, driving `mosque`, on four scenes:

> "for merging lanes/turning lanes, they still draw their edge boundaries into the lane that goes
> straight … the left turn lane has its edge drawn into the straight road, and the straight road
> has its edge boundary drawn into the turning lane, this makes it seem as if the car can't go
> straight and it can't turn right, because those lanes are solid lanes, I need those lanes to be
> removed … these edge boundaries will cause problem when I'm using an agent to drive the vehicle
> as it would make it seem like road boundaries."

In both directions, at every merge and every diverge on both maps. Measured on the models behind
the datasets: **70 lines carrying 651.1 m of paint inside another lane's driving surface on
`mosque`** (2.8% of its 23 139 m of paint) and **19 lines carrying 126.8 m on `junction-1`**
(0.8%). None of it between opposing carriageways — v26 already keeps a metre there — so all of it
is a lane's line lying on tarmac another lane covers.

```
 mosque · way 1351503429 leaving way 1351503423 · PLAN VIEW, travel left to right
 left-hand traffic · lane indices run centre-out: idx0 hugs the centreline (offside),
 idx(n−1) is kerbside · + = left turn, − = right turn
 ▬▬▬ = solid ROAD_EDGE_BOUNDARY — MetaDrive gives it a ghost body and sets
       on_white_continuous_line · numbers are metres, all measured

 ══════════════════════════════════════════════════════════════════ KERB ══

  BRANCH        ▬▬▬▬▬▬▬▬▬▬ left edge, 5.41 m, on open ground ▬▬▬▬▬▬▬▬▬▬▬▬
  way 1351503429
  idx0/1  fwd   · · · · · · · · · centreline · · · · · · · · · · · · · · ►
  6c649365bb…   ▬▬▬▬▬▬▬ right edge 060bd4efd33a0ca8, 5.41 m ▬▬▬▬▬▬▬▬▬▬▬▬▬
  3.50 m wide    ╰──── 2.38 m of it lies INSIDE the lane below ────╯
                       reaching 1.38 m in — 0.37 m PAST that lane's
                       own centreline

     ✗  A CAR GOING STRAIGHT HAS A SOLID WHITE LINE UNDER ITS WHEELS  ✗

  THROUGH       ▬▬▬▬▬ left edge 713e3e965d241347, 19.49 m ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
  way 1351503423 ╰─ 2.83 m of it lies INSIDE the branch, 0.92 m in ─╯
  idx0/1  fwd   · · · · · · · · · centreline · · · · · · · · · · · · · · ►
  b47e1cfc69…   ▬▬▬▬▬▬▬▬▬▬▬▬ right edge, on open ground ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
  3.50 m wide

     ✗  A CAR TURNING OFF HAS A SOLID WHITE LINE UNDER ITS WHEELS  ✗

 ═════════════════════════════════════════════════════════ CENTRELINE ══

   travel direction ──────────────────────────────────────────────────►

   centreline separation runs 1.82 m → 4.85 m. Two 3.50 m lanes need 3.50 m
   to stop overlapping, so the two surfaces genuinely share tarmac over the
   first stretch — which is what a diverge is. The tarmac is right. The paint
   is not.
```

The ramp Keith named is the same picture mirrored: `15438e6fd90cf39e` (way 182502409, a
`secondary_link`) put **3.84 m of its 8.58 m** right edge inside `fa74351a73e87a68` (way
776021086, `secondary`, idx1/2), 0.58 m deep, and that lane's own left edge `5dfb70966c42acc6`
put **3.83 m** inside the ramp, 0.50 m deep. His third id, `bd4d4e77929148d3`, is a **connector**
(`753a9e9e52e7a5bd → 16d8418821834753`, `slight_left` +42.1°); the red-boxed mark beside it is a
lane edge lying inside a junction surface, which is 207.2 m of the `mosque` total.

## Fundamental cause

A lane's two lines are offset from **that lane's own centreline** and nothing ever asks what else
is on that ground. Two facts then meet:

1. **Lanes of one OSM way know about each other; lanes of different ways never do.**
   `generation.py` assigns `left_neighbor` / `right_neighbor` positionally within *one edge's*
   lane list, which is what lets `_divider_boundaries` dash the line between two of them or drop
   the second copy. A turning lane, a slip road and a merging ramp are always a **different way**
   from the road they leave or join, so they are never neighbours and every one of their lines
   stays a solid `ROAD_EDGE_BOUNDARY`.
2. **At a merge or a diverge the two lanes must share tarmac.** That is what merging is; two
   3.50 m lanes need 3.50 m between their centrelines to stop overlapping.

So the geometry is right and the paint is wrong, and the paint is the half that a policy reads:
`ScenarioBlock` gives every line a ghost body and only a solid one sets
`on_white_continuous_line`.

The deeper point is that `_junction_kerb_boundaries` already states the rule — "nothing here may
land on drivable road", enforced by `_KERB_INSET_M` and `_road_on_both_sides` — but it states it
about kerbs only, and a kerb was the last kind of line this converter learned to draw. Every other
line was written from one lane's geometry without the test.

## Fix

`src/osm_scenario/conversion.py`, export-time. **No generator change**, so
`generation_fingerprint` does not move and every review stays bound; both workspaces were rebuilt
with `convert` alone.

> **A painted line marks the edge of the road, or the division between two lanes lying side by
> side. It may never run through another lane.**

`_uncovered_boundaries` cuts every lane boundary back to the parts that are not inside another
drivable surface, and runs between the boundary loop and `_sealed_surfaces`. Five things not to
re-derive:

- **Three kinds of surface never clip a line**, and each abuts by design — the same list
  `generation._lateral_neighbours` already keeps: the line's own lane; **any lane sharing an OSM
  way with it**; and **any junction turn the lane is an end of**. The middle one has to be an
  exclusion rather than a wider tolerance, and that is measured: on a curve a mitre join puts a
  legitimate same-way divider up to **0.345 m** inside its neighbour's polygon on `mosque`, which
  is deeper than some of the real defects, so no threshold alone can tell the two apart.
- **`_COVERED_PAINT_TOLERANCE_M` is 0.05 m and must stay above `_KERB_PAINT_ALLOWANCE_M`
  (0.02 m).** That is the whole argument for clipping before the kerb is traced: every removed
  piece is at least this far inside the road, so none of them was covering a ring within the
  allowance and the kerb pass cannot paint it back. Verified rather than assumed —
  `junction_kerbs` and `road_ends_unpainted` are unchanged on both extracts. The result is not
  balanced on the number either: 0.05 / 0.10 / 0.25 remove 616 / 579 / 475 m on `mosque`.
- **It is judged against the lanes' own polygons, not the sealed ones.** The question is whether
  a line is inside a lane's tarmac, and a patch closing a wedge between two surfaces is not a
  lane's tarmac. Hence before `_sealed_surfaces` rather than after.
- **`_MIN_PAINT_M` is 0.5 m and is a needle filter and nothing more** — the distinction
  `_MIN_KERB_M` had to learn the hard way. **Every** surviving piece under 2 m on either extract
  — 12 on `mosque`, 5 on `junction-1` — meets other paint at at least one end and most at both,
  so a bigger filter would break a continuous road edge rather than remove a speck. It is used
  twice, symmetrically: a piece shorter than it is not written, and **a hole shorter than it is
  not opened**, because a break of a few centimetres reads as a broken line rather than a gap in
  one — the complaint Keith made of the first kerb attempt. The histogram picks it: the interior
  holes this cuts measure **0.23 m and then nothing at all until 4.78 m**.
- **A line that survives in one piece keeps its id**, shortened or not, because it still means
  "this lane's left edge". One cut in two gets `deterministic_id("boundary_clipped", …)` per
  piece, because one id cannot name two lines. Nothing in this repo, in `tools/check_dataset.py`
  or in MetaDrive reads a boundary id.

`metadata.lane_markings` gains `covered_paint` and `covered_paint_m`, beside `junction_stubs` and
`junction_kerbs` and for the same reason: a line removed for lying on tarmac is a different fact
from a duplicate merged out. **`merged` had to be re-derived** — it was `model boundaries − stub
boundaries − line features`, and a boundary cut in two adds a feature, which would have driven it
negative. It now counts against `MapFeatures.boundaries_written`, the model boundaries that
reached the file.

## Verification

Both `source/map.osm` still match their manifests. Both datasets rebuilt with `convert` only —
the fingerprint is untouched, so no review was disturbed.

**The paint is off the tarmac.**

| | mosque | junction-1 |
| --- | --- | --- |
| lines carrying paint inside another lane, before | 70 | 19 |
| how much, before | 651.1 m | 126.8 m |
| **longest single run left** | **0.47 m** | **0.23 m** |
| boundaries cut back / metres removed | 67 / 619.6 m | 18 / 127.7 m |

What is left is bounded by construction and is only the bridged holes: a run can survive only
where a break under `_MIN_PAINT_M` was closed rather than opened, so nothing lies on tarmac for as
far as the shortest piece of line worth drawing. Four such across both extracts.

**Nothing else moved.**

| | mosque before → after | junction-1 before → after |
| --- | --- | --- |
| lane surfaces | 607 → **607** | 432 → **432** |
| dividers (broken lines) | 172 → **172** | 83 → **83** |
| junction kerbs | 160 → **160** | 127 → **127** |
| road ends left bare | 32 → **32** | 37 → **37** |
| surfaces sealed | 427 → **427** | 288 → **288** |
| lane edge lines | 382 → **383** | 350 → **352** |

Not one divider was clipped on either map, which is the same-way exclusion doing its job: a
divider is by definition the line between two lanes of one way.

**No line was broken into a chain.** 8 boundaries on `mosque` and 3 on `junction-1` are cut into
more than one piece, and every interior hole is a real one — 4.78, 5.76, 6.90, 7.93, 10.95, 13.69,
14.06, 17.01 and 63.14 m on `mosque`; 5.07, 10.17 and 18.26 m on `junction-1`. The only hole under
a metre, `1a98ee4e3c236bdb`'s 0.23 m, is closed rather than opened.

Keith's own scenes: `6c649365bb3105fe`'s right edge 5.41 m → **3.03 m**; the ramp
`15438e6fd90cf39e`'s right edge 8.58 m → **4.74 m**; and the two long solid lines running
diagonally across the carriageway at connector `bd4d4e77929148d3` are gone. Rendered before and
after and looked at.

`tools/check_dataset.py` reports `covered_paint` beside `junction_kerbs` and now **fails** when a
painted line runs `_MIN_PAINT_M` or further inside a lane surface that is not its own, judging the
exclusions from the dataset alone (own lane, named neighbours, junction turns it is an end of).
Both datasets: `result OK`, `sanity_check PASS`, `draw_map accepted the features`.

`uv run ruff check` passes. `uv run pytest`: **408 passed, 1 failed** —
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, which fails identically before
and after this change (3 of 396 swept routes over 30°) and is recorded as open in CLAUDE.md.

Six tests added to `tests/unit/test_conversion.py`, including
`test_no_painted_line_on_the_real_maps_runs_through_a_lane`, which is the one that would have
caught this, and `test_two_lanes_of_one_way_keep_the_line_between_them`, which is the one that
stops a future version of this deleting every lane divider on the map.

**Not fixed here, and found on the way:** `workspaces/junction-1/lane-model/reviewed.json` is
still `direct-osm-stage2-v24` while its `preliminary.json` is v26, so anything driven from that
workspace predates the last three fixes — which is why its dataset reports 934.9 m cut back
against the 127.7 m its current model needs.
