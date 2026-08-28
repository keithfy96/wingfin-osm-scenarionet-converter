# A junction box's turn fell into the gap between its 2 m stubs

## Symptom

Keith drove traffic on `junction-1` and filmed cars leaving the tarmac and ending up
facing against the flow. He then named three places and photographed each one from the
street: lane `2603bce63d3ee855` (Persiaran Kenanga, `turn:lanes:forward=right`), connector
`4de6b16c7d13515b` at node `1927184814`, and lane `a3d3f905fff8867f` on `mosque`. His
point was that these are ordinary turns that real cars make every day, and that the lane
model tracks the road correctly — so the fault had to be elsewhere.

He was right on both counts. The lane chain was legal (0 of 25,832 sampled steps ran a lane
backwards) and every destination lane at those nodes was fed. Two of the three lanes were
not being drawn at all: their declared exit is a 2.00 m stub **5.11 m and 5.22 m away**, and
`_turn` refused the join because `MAX_JOIN_M` is 5.0.

## Fundamental cause

**A big intersection is often mapped as a loop of short ways rather than one node, and the
corner then falls inside a fragment the trim has clamped to nothing.**

`generation._trimmed_edge` cuts every lane back from its junctions and, when a way is
shorter than its two setbacks together, scales them down and stops at `MIN_TRIMMED_LANE_M`
— 2.00 m. 28 of `junction-1`'s 285 lanes and 43 of `mosque`'s 405 end up clamped. Keith's
"junction with a small divider" is **four such nodes in a ~12 m square** — 1927184814,
474928793, 7251588325, 7251588324 — joined by 2.00 m one-way stub ways around the island.

`route_polyline` built one `_turn` per lane, so each 2 m fragment got its own manoeuvre with
`_spare(2.0) = 1.00 m` to cut back into on either side. And **the turn is not spread along
the fragments; it is concentrated in the gaps between them.** At node 474928793 the whole
91.65° right turn is carried by a connector whose centreline is **0.82 m long**, between two
stubs 0.76 m apart. Each cubic laid across such a gap passed `MAX_CURVE_TURN_DEG` on its
own — that gate is per-vertex, 20° at 0.25 m sampling, which permits a 0.72 m radius — so
nothing raised anything. The fold was the concatenation.

Three further causes surfaced once the crossing was built as one manoeuvre:

- **Catmull-Rom is degenerate at the interior of a reversal.** `P[i+1] − P[i−1]` across a
  U-turn is the *net* displacement, which points along the exit rather than through the
  turn: on `junction-1`'s 178.85° crossing the middle waypoint read +100.98° against the
  approach's +94.65°, leaving one cubic to render 172.52° at 0.16 m of radius.
- **`_wanted_trim` diverges at a reversal.** `TURN_RADIUS_M · tan(θ/2)` is the tangent
  length of a fillet between two lines that *intersect*; two antiparallel lanes never do.
  Clamped only at 170°, it asked for **102.87 m**, and `_spare` handed over every metre the
  lanes had — one box turned 166.5 m of a 167.5 m straight approach into a single curve.
- **Two cases were handed back to the starved per-lane path**: a run that ends the route,
  and (as written) one whose exit is reached by changing lane. Those were the worst
  manoeuvres on either map, a median 0.49 m and 1.12 m. The second turns out to be
  impossible — `conversion._lane_change_moves` only allows a neighbour sharing the
  `source_edge`, so a stub's side-neighbour is another stub the gather already swallowed
  (0 of 487 runs and 0 of 428). What does happen, 101 and 149 times, is a change **inside**
  a run, and guiding a line through the lane it left and then the one it joined makes it
  double back — 76.37° at a vertex where the sweep allows 30.

## Fix

All of it in `src/osm_scenario/ego_route.py`, so no map feature and no fingerprint moves.

- `_junction_box` crosses a whole run of clamped stubs as **one** manoeuvre, trimming only
  the two real lanes either side, and checking the gap per span rather than across the run.
- Below `BOX_GUIDE_MIN_DEG` (120°) one cubic spans the box; above it the stubs' **midpoints**
  guide it, because a single cubic across a reversal cuts the corner and strays a median
  18.7 m from the road the stubs trace.
- Interior tangents come from each **stub's own heading**, not from Catmull-Rom.
- The trim is capped at `BOX_TRIM_SPANS × _box_path` — how far the road itself travels
  through the box — wherever the crossing is guided.
- `route_polyline` no longer falls back to the per-lane path, and `guiding` drops every stub
  before the last change in a run from the guide list while still crossing the run whole.

## Verification

300 seeded routes on each extract, against the export-truth surface (`_map_features` →
`_sealed_surfaces` → `_road_union`; raw lane polygons over-report off-road by ~14×).

| | junction-1 | mosque |
|---|---|---|
| built / refused | 239 / 61 → **unchanged** | 300 / 0 → **unchanged** |
| routes folding | 53.1% → 13.8% → **7.9%** | 58.3% → 24.0% → **19.0%** |
| carrying a bend under 2.94 m | 64.9% → 36.4% → **5.4%** | 64.0% → 42.3% → **37.7%** |
| road's own arc ÷ what we draw | 3.5× → **1.37×** | 4.0× → **1.20×** |
| off the drivable surface | 3.15%* → 1.16% → **0.64%** | 2.27%* → 0.23% → **0.20%** |
| longest single run off it | 25.81 m → **21.64 m** | 9.98 m → **9.86 m** |

\* measured before `_sealed_surfaces` was applied; the like-for-like baseline is the middle
column. Depth off the mapped edge, which is what the off-surface figure actually means:
median 0.65 m, p90 1.69 m, max 2.12 m.

Per construction, share of crossings tighter than the car's 2.9424 m turning circle:

| | junction-1 | mosque |
|---|---|---|
| single cubic (< 120°) | 6.4% → **0.0%**, median radius 15.77 m | 0.0%, median 14.72 m |
| guided (≥ 120°) | 88.2% → **0.0%**, median 3.55 m | 100% (1 box, 2.44 m) |
| fell through | 56.7% → **0.0%**, median 4.20 m | 64.6% → **33.3%** |

`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` **now passes**: 3 of 396
before this work, 1 of 396 after the first commit, **0 of 396** now, worst vertex 17.61°.
Full suite 707 passed; `ruff check` clean.

**Blast radius.** Every route that moved contains a guided box or a fall-through — a median
3.50 m and at most 7.02 m. Every route with no stub run, and every route whose boxes all
take the single-cubic path, is byte-identical; `test` and `fold-demo` are unchanged.

**The map did not move**, asserted rather than assumed: converted before and after, 974 map
features, **0 differing**, `generation_fingerprint` `57dcd345d17e5a86` unchanged, both
pickles byte-identical. `tools/check_dataset.py` under MetaDrive's 3.8/numpy-1.24
interpreter: `sanity_check PASS`, `result OK`, 0 windows over 30°, tightest recorded radius
8.9 m and 10.4 m, 0 steps under 5 m.

**Left unfixed, deliberately.** Three crossings of 87 are still tighter than the car turns
and are not worth tuning three samples to: 180° U-turns at 2.89 m (`junction-1`) and 2.12 m
(`mosque`), and one `mosque` route whose *destination* is a 2 m stub, so the crossing must
turn 114.92° into a lane with 1.00 m to spare, at 0.44 m. Lane changes are now the largest
remaining cause on `mosque` and are a separate job — `_lane_change`'s `sideways` measures a
diagonal rather than the cross-track offset, overstating it a median 3.42× and up to 23.94×,
so `_smoothing_span` never binds and the span is set by `_CHANGE_MAX_FRACTION` alone in
100% of 342 and 390 manoeuvres.
