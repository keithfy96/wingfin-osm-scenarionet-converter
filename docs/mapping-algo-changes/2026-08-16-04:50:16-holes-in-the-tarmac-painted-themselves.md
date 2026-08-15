# Holes in the tarmac painted themselves

- **Date:** 2026-08-16 04:50:16
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/conversion.py (`_sealed_surfaces`, `_road_union`, `_closed`,
  `_road_on_both_sides`, `_junction_kerb_boundaries`, `_map_features`, `_scenario`),
  tools/check_dataset.py
- **Generator version:** direct-osm-stage2-v25 → unchanged (export-time only; no fingerprint moves)
- **Follows:** `2026-08-16-03:42:57-the-kerb-painted-the-seams-between-road-surfaces.md`. That one
  stopped the kerb *drawing* the seams; this one closes the seams themselves.

## Symptom

Keith, on the `mosque` drive rebuilt an hour earlier: *"it still contains some lines that go into
the lanes where the tarmac does not fully cover it."* White marks running from the road edge into
the lane he was driving in - and **not paint at all**.

```
 two lane surfaces meeting where the road bends    ▓▓▓ tarmac   ░░░ nothing (renders white)

   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░
   ▓▓▓▓▓▓▓ lane, edge A ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░▓▓▓▓▓▓▓▓▓ lane, edge B ▓▓
   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
   ═════════════════════ painted edge line ═══════════╪══════════════════════════
   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
                          square cap ──┘ └── square cap, a few degrees apart
                                            the wedge is up to 0.687 m across
```

| | holes wider than a texel | area | enclosed by road on every side | widest |
|---|---|---|---|---|
| `mosque` (texel 1/16 m) | **78** | 172.10 m² | 69 | 0.687 m |
| `junction-1` (texel 1/32 m) | **85** | 45.55 m² | 52 | 0.609 m |

**13 of `mosque`'s were within 3 m of the line the car drives** - 56.8 m² of hole under the route,
one of 4.59 m² at 777 m along and one at 272 m, which is about where the screenshot was taken.

Adjacent lanes are not the cause: three pairs of side-by-side lanes on way `859423754` share their
facing edges to **0.0000 m**. It is only the joins between one edge of a road and the next.

## Fundamental cause

**A lane surface is built by offsetting its own centreline, and a road is a chain of them.** Where
one edge hands over to the next, the two rectangles meet end-to-end with caps square to their own
directions. If the road bends at that node - median 4.92° at a direct continuation, the figure the
v25 change measured - the caps are not parallel and a wedge of nothing is left between them.

Nothing paints a wedge, and nothing needs to: `terrain.frag.glsl:115` whitens everything in
`5 < value < 16`, the semantic texture is created with no filter, and the blend from road surface
(20) across the gap to ground (0) passes straight through that band. **A hole in the road draws
itself as a line.** The same mechanism as the hairline round every road edge, which Keith had
already looked at and chosen to leave - but this one lands in the middle of a carriageway.

The previous change closed the same gaps for the *kerb candidate* only (`_KERB_GAP_CLOSE_M`), so
the kerb stopped tracing their walls. The gaps themselves were still written to the dataset.

**And a second fault sat underneath it.** `unary_union` over the lane polygons **silently drops
one of them** on each extract - `3cc5be39c0caf1c2` on `mosque`, 141.17 m², and `2fe4cf2f93...` on
`junction-1`, 295.9 m². Valid inputs, a valid single polygon out, and that lane simply not covered;
union the same shapes again and it appears. It had been invisible because nothing asked, but a
surface missing from the union is a hole in the road, and a hole in the road is where this module
draws kerb lines.

## Fix

`_sealed_surfaces`, run in `_map_features` before the kerb pass, using the closing that already
existed for it so there is one constant rather than two:

```
road    = _road_union(every LANE_SURFACE_STREET polygon)   # union_all on a 1e-9 grid
wedges  = _closed(road) - road                             # mitred +0.35 / -0.35
each wedge, only if it borders >= 2 distinct surfaces:
    for each bordering surface, longest shared edge first:
        that surface takes the part of the wedge within _KERB_GAP_CLOSE_M of itself
```

and, in `_junction_kerb_boundaries`, `_road_on_both_sides` - a candidate with tarmac on both sides
of it along its **whole** length is the wall of a slot too wide for the closing, and not a kerb.

Six things not to re-derive:

- **A wedge is shared out among the surfaces along it, not given to one of them.** Given whole to
  its longest neighbour, `mosque`'s largest snakes 190 m through a junction and takes a 40 m lane's
  polygon with it - and MetaDrive's own `sanity_check` then **refuses the dataset**, because the
  polygon's centroid stands more than 100 m from its centreline's.
- **The ring is segmentized at `_RING_STEP_M` (5 m) before it is written**, and that is the rest of
  the same story: `scenario_description.py:270` measures where a polygon is by **averaging its
  vertices**. A 400 m lane's ring is 5 points, so a hundred new ones at one end drag the average
  there - 14.9 m → 136.8 m on `ee2c3f00ec3383e0`. Segmentize adds points to edges that already
  exist, so the shape is identical to the last decimal, and the reading comes back to 37.6 m, which
  is where an untouched map already sits.
- **`_road_union` unions on a 1e-9 grid** for the dropped-lane fault above. A nanometre is six
  orders of magnitude below every other tolerance here, so it decides no geometry.
- **The whole-length rule, not a majority.** Scored over both extracts, a wall of a slot reads
  1.000 and is 0.38-1.00 m long, and the next score down is 0.750 - arcs of 2-4 m that are seam for
  part of their length and real road edge for the rest. A majority rule threw out `12b7284b7f` on
  `junction-1`: 3.80 m of kerb round a **118 m² traffic island** that passes within 0.8 m of a lane
  and a bridge which fail to meet.
- **An island cannot be caught by that rule however narrow it is**, which is why it can be blunt:
  an island is a hole in the union, so the probe lands outside the road on the island side.
  `mosque`'s narrowest real island is 0.97 m across.
- **A gap bounded by one surface alone is left alone.** That is a surface's own concavity, not a
  seam between two of them, and filling it would reshape a road on nobody's evidence.

## Verification

Stage 6 only; **`generate-map` was not re-run**, so no fingerprint moved and the reviews stay bound.

| | holes > 1 texel | hole area | widest hole | kerbs | road ends | surfaces grown |
|---|---|---|---|---|---|---|
| `mosque` before | **78** | 172.10 m² | 0.687 m | 142 | 39 | — |
| `mosque` after | **0** | 0.008 m² | 0.011 m | 136 | 39 | 409 |
| `junction-1` before | **85** | 45.55 m² | 0.609 m | 115 | 38 | — |
| `junction-1` after | **1** | 0.058 m² | 0.061 m | 113 | 38 | 283 |

`junction-1`'s one survivor is 0.16 × 1.01 m and **narrower than the line that would be drawn over
it**, which is the threshold the test asserts.

**Only polygons moved, and only outwards.** Every original polygon is contained in the one written;
the road gains 172.11 m² and 45.49 m², which is the hole area to within `_SEAM_CONTACT_M`; **not one
lane or connector centreline changed, and not one lane edge or divider**. The 6 and 2 kerbs that go
are the seam walls.

**The drive is unchanged.** `tools/drive.py --render offscreen`: `junction-1` 352 of 370 steps,
`arrive_dest=True`, completion 0.953; `mosque` 988 of 1036, `arrive_dest=True`, completion 0.951.
`sanity_check PASS` and `result OK` on both - and `sanity_check` is what caught the centroid fault
above, so it is a gate here and not a formality.

**What a reader sees.** Road edge carrying no thick line, measured against the road outline at one
texel and through MetaDrive's own resampling: `mosque` **324.0 m → 86.3 m** and `junction-1`
**420.8 m → 115.9 m**, the remainder being the 39 and 38 road ends left bare on purpose and nothing
else. That is the two halves together - this change and `tools/drive.py`'s `_keep_line_ends`, which
is not in this folder because it is not generator code.

`uv run pytest` - 394 passed, 1 failed, and the failure is not this change:
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` reports 3 of 396 routes over the 30°
gate and is already recorded as open. `uv run ruff check` passes. Three new tests:
`test_no_hole_is_left_in_the_tarmac_wider_than_the_line_that_would_draw_on_it` is the acceptance
number, `test_sealing_a_surface_only_ever_adds_to_it` pins the blast radius from both sides, and
`test_the_road_union_does_not_quietly_lose_a_surface` pins the union.
