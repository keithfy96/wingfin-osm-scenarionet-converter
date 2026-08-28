# A shallow box that steps sideways was chorded across the oncoming carriageway

## Symptom

Keith regenerated `traffic.json` on the junction-box branch and the crash scene reproduced:
*"the vehicle is STILL driving into oncoming traffic, and running on the grass."* Measured on
the same 60 traffic pairs, the branch was **worse** than the code before the first junction-box
fix, not merely unfinished: metres driven against a lane's direction 279 → 578, metres off the
sealed road surface 87 → 264, longest single wrong-way run 10 m → 30 m. Every new 20–30 m
wrong-way run lay dead on a `_junction_box` curve (nearest distance 0.02–0.09 m) at a staggered
crossing of dual-carriageway way 334662874.

## Fundamental cause

`f0fc307` crosses a run of 2 m stub lanes as one manoeuvre, but only *guides* the crossing by
the stubs when the net turn is ≥ `BOX_GUIDE_MIN_DEG` (120°). A staggered junction turns almost
nothing — 1.7° to 10.8° — while stepping up to 15 m sideways around the divider, so it fell in
the unguided branch, where the crossing is one cubic that ignores the stubs. Two faults then
compound:

1. Below `_SMOOTHING_MAX_DEG`, `_wanted_trim` sizes the trim from the sideways step:
   `_smoothing_span(14.95 m)` = √(6·14.95·110)/2 ≈ 49.7 m **off each lane**. The trim cap
   added for guided boxes did not apply to this branch.
2. The cubic joined two points 104.62 m apart across a box the road itself crosses in
   19.37 m, as a near-straight chord (max bulge 1.87 m) that cut the dogleg the road takes —
   30 contiguous metres of it down the oncoming carriageway.

The pre-`f0fc307` per-stub construction had hugged the dogleg here, which is why the first
fix's own sweep (folding share, undrivable-bend share) improved in aggregate while these
crossings regressed: the metrics swept measured curvature, and a chord is perfectly smooth.
Wrongness of *position* — a smooth line down the wrong carriageway — was not being measured.

## Fix

Two changes in `_junction_box` (`src/osm_scenario/ego_route.py`):

- **`BOX_SIDEWAYS_MAX_M` (6.0):** a crossing is guided when it turns ≥ 120° *or steps more
  than 6 m sideways*, however little it turns. Guiding steers through the stub midpoints with
  the stubs' own headings — the road's diagonal — and caps the trim at
  `BOX_TRIM_SPANS · _box_path`, which is what stops the 49.7 m trims. Swept at 2/4/6/8 m;
  everything caught steps over 10 m, everything correctly left alone steps under 2 m.
- **`BOX_GUIDE_SPACING_M` (4.0):** guide waypoints closer than 4 m to their neighbour or to
  the exit collapse into one. Without it, guiding the −96.2° box (`fold-demo`'s junction,
  sideways 12–19 m, newly over the gate) pinned two waypoints 3.9 m apart with an 89° heading
  flip — a corner no cubic can round — and bent six of the sixty traffic routes to 1.06 m of
  radius against the car's 2.94 m. With the spacing those six read 2.76–3.59 m. Swept at
  0/3/4/5 m; 3 leaves the kinks, 5 costs `mosque` 3 m of road.

Alternatives measured and rejected: gating on the stub path's deviation from the chord (the
worst boxes' single stub *is* the diagonal — deviation ≈ 0, catches nothing); bounding the
sideways gate at < 20° or < 90° (loses `mosque`'s 90–96° improvers, 44 m/71 m stuck);
`wanted > box_path` as the gate (misses the same 90–96° boxes).

## Verification

On the same 60 traffic pairs per extract, before the first fix / branch / after this fix:

- `junction-1`: wrong-way 279 / 578 / **285 m**; off-surface 87 / 264 / **84 m**; longest
  wrong-way run 10 / 30 / **10 m**; routes bent tighter than the car 42 / 18 / **18**.
- `mosque`: wrong-way 30 / 44 / **30 m**; off-surface 41 / 71 / **41 m**; tighter than the
  car 42 / 27 / **28**.
- Blast radius: rebuilt every pair plus both ego routes against the branch code — 34 of 61
  junction-1 and 31 of 61 mosque lines moved, **every one carrying a sideways-guided box**;
  zero moved without one. The ego `test` route's own 3 m wrong-way run disappears.
- Map untouched: 974 map features, 0 differing against the committed pickle; fingerprint
  `57dcd345d17e5a86` unchanged. `tools/check_dataset.py` under MetaDrive's 3.8/numpy 1.24:
  `sanity_check PASS`, `result OK`, tightest radius 3.2 m.
- `uv run pytest`: 707 passed, including the 396-route gate sweep at 0 refused / 0 over 30°.
- `workspaces/*/traffic/traffic.json` regenerated with `--seed 1` (the pool the crash video
  used; the CLI default is seed 0 and draws 60 different pairs) and re-measured to exactly
  the figures above. `junction-1` reconverted at 10 Hz.
