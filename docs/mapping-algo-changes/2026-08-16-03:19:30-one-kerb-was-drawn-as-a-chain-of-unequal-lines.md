# One kerb was drawn as a chain of unequal lines

- **Date:** 2026-08-16 03:19:30
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/conversion.py (`_junction_kerb_boundaries`, `_kerb_rings`,
  `_end_squareness`, `_extended`, `_map_features`, `_scenario`), tools/check_dataset.py
- **Generator version:** direct-osm-stage2-v25 → unchanged (export-time only; no fingerprint moves)
- **Corrected by:** `2026-08-16-03:42:57-the-kerb-painted-the-seams-between-road-surfaces.md`. The
  fix below is right about continuity and its counts were true when measured, but it also painted
  the seams between road surfaces — 238 of the 408 lines it reports for `mosque` were marks on open
  tarmac. Read the two together; the constants here are superseded there.

## Symptom

Keith, on a `mosque` drive, after the first kerb pass had shipped: *"in connectors that occur
between lanes, because it's not technically a lane polygon, there is no edge line drawn, but that
results in bends that have an edge line on the polygons, and between them spaces where there is no
edge line. That doesn't make sense from a driving point of view, does it? It breaks the line into
larger and smaller lines on the exact same kerb. I need the connectors to paint the edge lines if
there is none, and not paint them in areas like junctions."*

```
 one physical kerb, plan view          ═══ painted   ┄┄┄ nothing (the shader's hairline)

 BEFORE                  lane edge line          kerb arc          lane edge line
   ══════════════════════════════┄┄┄════════┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄══════════════════
                                 ↑        ↑        ↑            ↑
                       0.15 m stand-off       a 1.4 m arc    0.15 m stand-off
                                              dropped for being under 2 m
   ####################  lane  #####|##### connector #####|#####  lane  #############

 AFTER
   ═══════════════════════════════════════════════════════════════════════════════════
   ####################  lane  #####|##### connector #####|#####  lane  #############

 STILL BARE, deliberately:  the inside of a junction, and the bar across the end of a road
```

Counted on the shipped datasets — a stretch of road edge with no paint whose **both** ends run
along the paint they meet, which is one kerb drawn as two lines with a hole between them:

| | breaks in one kerb | gap total |
|---|---|---|
| `mosque` | **154** | 276 m |
| `junction-1` | **186** | 292 m |

| gap length | `mosque` | `junction-1` | what it was |
|---|---|---|---|
| ≤ 0.35 m | 58 (38%) | 85 (46%) | `_KERB_PAINT_CLEARANCE_M = 0.15`, a deliberate stand-off |
| 0.35–2 m | 19 (12%) | 22 (12%) | `_MIN_KERB_M = 2.0`, an arc that bridged two lines thrown away for being short |
| > 2 m | 77 (50%) | 79 (43%) | never generated — the candidate was the junction surfaces only |

## Fundamental cause

Two of them, and the second is the one worth keeping.

**The rule was written about junctions when the thing it has to get right is a road edge.** The
candidate was `union(connectors, bridges, stubs).boundary` — the outline of the surfaces that carry
no paint — so a gap on a *lane's* own edge could not be a candidate however bare it was. That is
half the long breaks. A kerb is not a property of a junction; it is a property of the road, and it
runs across whatever mix of surfaces happens to lie under it.

**And the acceptance check could not see the defect, because it measured at the wrong scale.** It
asked whether the road outline was within **0.20 m** of a painted line. A line MetaDrive draws is
2 px, and `mosque`'s 2048 m terrain square against this machine's 32768 px ceiling is 16 px/m — so
paint is **0.125 m** wide and a texel is 0.0625 m. At three times the width of the paint, an edge
that renders bare passed as painted. Re-measured at one texel, 393 m of `mosque`'s road edge was
bare and the 0.15 m stand-off was, by construction, invisible to the check that let it ship.

The stand-off itself had a reason — do not lay a second line over one that exists, because two
coincident lines are resampled out of phase by MetaDrive (`scenario_map.py:61` resamples at 2 m)
and draw as something neither of them is. That reason justifies *not overlapping much*. It never
justified a **gap**, and 0.15 m was chosen against a 0.156 m drawn width on the belief the join
would read as continuous. It does not.

## Fix

`_junction_kerb_boundaries` now takes the features already written rather than the model, so it
sees every surface and every line that exists:

```
rings     = exterior of every road piece, plus any enclosed island over _MIN_ISLAND_M wide
candidate = rings - existing_paint.buffer(_KERB_PAINT_ALLOWANCE_M)      # 0.02 m, not 0.15
each arc  : reject if square to the paint at BOTH ends and under _MAX_ROAD_END_M   -> a road end
            cut at _MAX_KERB_TURN_DEG                                    (unchanged)
            extend _KERB_JOIN_OVERLAP_M along its own end tangent, both ends
            reject if its midpoint lies inside road.buffer(-_KERB_INSET_M)
```

Three things it deliberately does not do:

- **It cannot paint inside a junction.** The interior of a box is covered road, so it lies on no
  ring. Keith's *"not in areas like junctions"* is structural here rather than a filter.
- **It never paints across the end of a road.** That bar is on the outline and filling it draws a
  stop line — with a ghost body, on road a car drives along. 100 left bare on `mosque`, 96 on
  `junction-1`, now reported as `lane_markings.road_ends_unpainted`.
- **It does not draw a ring whole.** A kerb is cut wherever an existing line already covers it, one
  piece per gap filled, which is why the count rises to 408 and 284.

Constants, and each is a reading that was tried:

- `_KERB_PAINT_ALLOWANCE_M = 0.02` with `_KERB_JOIN_OVERLAP_M = 0.10` replaces
  `_KERB_PAINT_CLEARANCE_M = 0.15`. Butt into the line rather than stop beside it.
- `_MIN_KERB_M` drops from 2.0 to 0.05 and `_KERB_INSET_M = 0.25` does its job directly. The
  length was a proxy for "not on drivable road"; swept, it needed 2.0 m to reach zero strays and
  cost 19 and 22 real bridging arcs to get there. The direct test leaves **0 strays** at 0.05 m.
- `_ROAD_END_SQUARENESS = 0.35`, `_MAX_ROAD_END_M = 6.0`, `_MIN_ISLAND_M = 0.3`, all new.
- `_KERB_LANE_CLEARANCE_M` is **gone**. It grew the painted lane surface before subtracting it, to
  stop a connector's flat end cap standing as a bar across a junction mouth — 233 of 268 pieces
  when it was got the wrong way round. Working from the network's outer rings, an interior end cap
  is not on the boundary at all, so there is nothing left for it to guard.

## Verification

Stage 6 only; **`generate-map` was not re-run**, so no fingerprint moved and the reviews stay bound.

**Breaks at zero**, which is the acceptance number and the one the shipped version failed:

| | kerb lines | length | breaks | strays on drivable road | road ends left bare |
|---|---|---|---|---|---|
| `mosque` before | 103 | 615 m | 154 | 0 | — |
| `mosque` after | 408 | 1298 m | **0** | **0** | 100 |
| `junction-1` before | 91 | 488 m | 186 | 0 | — |
| `junction-1` after | 284 | 945 m | **0** | **0** | 96 |

Lengths after: median 1.93 m, p90 7.3 m, longest 19.8 m, shortest 0.30 m.

**Additive, exactly.** `_map_features` compared feature by feature against the same code with the
kerb pass disabled — type and a hash of every polyline: `mosque` **+408 / −0 / ~0** (1164 → 1572),
`junction-1` **+284 / −0 / ~0** (869 → 1153).

**The drive is unchanged.** `tools/drive.py --render offscreen`: `junction-1` 352 of 370 steps,
`arrive_dest=True`, completion 0.953; `mosque` 245 of 257, `arrive_dest=True`, completion 0.952.
`sanity_check PASS` and `result OK` on both, with `check_dataset` reporting `284 kerb line(s) round
the junctions · 96 road end(s) left bare` and `408 · 100`.

**What a reader sees**, measured by rebuilding MetaDrive's own semantic raster from the written
pickles and counting road pixels touching ground with no paint between — in the 140 m square round
the ego, `mosque` 61.2 m → **42.9 m** and `junction-1` 69.0 m → **55.1 m**. It does not go to zero,
and that is not this change: 72% of what remains is MetaDrive resampling every boundary polyline at
2 m before painting it while filling the road polygon at full resolution, so the chords sag inside
every curve. That is a viewer setting (`line_sample_interval`, which `terrain.py:620` never passes)
and is recorded in `CLAUDE.md` rather than bundled in here.

`uv run pytest` — 390 passed, 1 failed, and the failure is not this change:
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` reports 3 of 396 routes over the 30°
gate, reproduces at HEAD without these edits, and is already recorded as open. `uv run ruff check`
passes. Two new tests carry the fix:
`test_no_kerb_on_the_real_maps_is_broken_where_it_should_run_on` asserts the break count at zero on
every workspace model, and `test_the_end_of_a_road_is_never_painted_across` asserts the bars stay
bare — and that some still exist, so a run that painted over all of them cannot pass by emptying
the set.
