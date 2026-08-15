# A junction had no kerb, and a shader artifact stood in for one

- **Date:** 2026-08-16 02:28:52
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/conversion.py (`_junction_kerb_boundaries`, `_uncreased`,
  `_surface`, `_map_features`, `_scenario`), tools/check_dataset.py
- **Generator version:** direct-osm-stage2-v25 → unchanged (export-time only; no fingerprint moves)

## Symptom

Keith, on two 3D drives: *"for connectors, there aren't really lane lines, they appear as these
thin lines… I need to render proper lane lines here, that would be the same as the rest of the
other lane edges."* On `junction-1` the solid edge line along a road stops well before the
junction, and something far thinner traces the junction in its place.

```
 approaching a junction, plan view       ═══  edge line, 0.156 m of paint
 travel direction ──────────────────►    ···  what a reader sees instead, 0.031 m

     grass                                                    grass
  ══════════════════════════════·······················  ═══════════════
                                ↑                     ↑
                       lane ends here          junction surface ends here
                        (setback cut)           and the next lane starts

  - - - - - - - - - - - - - - -                       - - - - - - - - -
        divider, correctly absent inside the box

  ══════════════════════════════·······················  ═══════════════
     grass                        median 9.17 m,           grass
                                  up to 14.43 m
```

Two facts, both measured:

- **The thin line is not a line.** `terrain.frag.glsl:115` colours the terrain by value band and
  paints anything in `5 < value < 16` pure white. Ground is 0, a white line 10 and road surface 20
  (`metadrive/constants.py:403`), and the semantic texture is filtered — so every road-to-grass
  edge blends 20 → 0 through the white band for about one texel. On `junction-1` at 32 px/m that
  is **0.031 m**, against 5 px = **0.156 m** for a real marking. Nothing in this repo draws it.
- **A junction carried no paint at all.** `_map_features` wrote boundary features for
  `model.lanes` only, so a `ConnectorFeature` was a road polygon with zero lines, and
  `_stub_lanes` dropped 56 more on `junction-1`. **61 of `junction-1`'s 82 active connectors
  bridge a real gap**, median 9.17 m and up to 14.43 m; the other 21 are stubs where the two lanes
  already touch and nothing was missing.

## Fundamental cause

The rule for what gets painted was **"a lane's own boundaries"**, and a junction has no lane whose
boundaries those are. That reads as a deliberate blank — real junctions *are* bare inside — but it
conflates two different things. The **inside** of a junction is bare because traffic crosses it.
The **edge** of a junction is a kerb, and a kerb is painted everywhere else on the map.

The blank was invisible because of the second fact: the shader draws a hairline at every road/grass
transition whether a line is there or not, so the missing kerb rendered as a present-but-wrong one.
Nothing in `lane_markings`, `sanity_check` or `check_dataset` asks whether a road edge is painted —
they count the features that exist — so the gap could not be seen from the numbers either.

There was also a compounding cause in the fix itself, and it is the reason the first two attempts
produced marks *across* the carriageway rather than round it. A connector's surface is
`centerline.buffer(width/2, cap_style="flat")`, so its **end cap lies exactly on the lane's own end
cap**. Cutting the painted lane surface out of the junction outline by *shrinking* the lane left
that cap standing as a bar straight across the road at every junction mouth, reading as a stop
line: **233 of `junction-1`'s 268 pieces**, every one of them a lane width long.

## Fix

`conversion._junction_kerb_boundaries` derives the kerb at export time from geometry that already
exists, and writes it as ordinary `ROAD_EDGE_BOUNDARY` features:

```
junction = union of every surface that carries no paint
           (exported connectors, the straight-through bridges, and the clamped stub lanes)
lanes    = union of every painted lane surface
kerb     = junction.boundary - lanes.buffer(+0.05) - existing_paint.buffer(0.15)
```

then `linemerge`, cut at any vertex turning more than `_MAX_KERB_TURN_DEG`, and keep the pieces
over `_MIN_KERB_M`. What survives is the outside of a turn and the corners of the box; the inside
is where the turns overlap each other and is removed by the first subtraction.

What it deliberately does not do:

- **It does not paint every connector edge.** 82 turns × 2 lines would cross each other through the
  middle of every intersection. A junction's interior stays bare, which is the point.
- **It does not touch a single existing feature.** Strictly additive — verified below.
- **It claims no owner.** A kerb arc is merged from however many turns meet there, so it carries no
  `side` and no `lane_id`; nothing in this repo or in MetaDrive reads those on a boundary feature.
- **It does not move `generation_fingerprint`.** Export-time only, so the Stage 3 review stays bound
  — the same reason signal timing and `--line-width-m` are not config fields.

Three constants, each measured rather than chosen:

- `_KERB_LANE_CLEARANCE_M = 0.05`, and the **sign** is the whole of it — see the end-cap bar above.
  Growing rather than shrinking took `junction-1` from 268 lines over 1109 m to 86 over 521 m.
- `_MIN_KERB_M = 2.0`. Swept: 1.0 leaves 2 stray lines on drivable road on `junction-1` and 1 on
  `mosque`; 1.5 leaves 2 and 1; **2.0 leaves none**; 3.0 buys nothing and costs 45 m and 69 m of
  kerb that is real.
- `_MAX_KERB_TURN_DEG = 150.0`. Over both workspaces the per-vertex turns are 6740 under 10°, a
  cluster of 40 at 80–89° where a connector's flat cap meets its side, then **nothing at all until
  32 sit at 170–179°**. Those are seams where two turns cross, drawn as zero-width needles, and one
  of them landed on drivable road.

`_uncreased` cuts at those creases rather than discarding the whole arc, because a needle is usually
a metre of seam on the end of an arc that is otherwise the kerb. It rejects steps under
`ego_route.COINCIDENT_M` (1e-3 m, imported rather than copied) for the reason that constant exists:
shapely repeats a vertex a fraction of a micrometre away rather than exactly on itself, and `atan2`
over that returns a bearing made of noise — which hid the reversal on either side of it and let the
one needle that reached drivable road survive a cut that was looking straight at it.

## Verification

Stage 6 only; **`generate-map` was not re-run**, so no fingerprint moved and `review.json` stays
bound.

**Additive, exactly.** `_map_features` compared feature by feature against the same code with the
kerb pass disabled — type and a hash of every polyline:

| | before | after | added | removed | changed |
|---|---|---|---|---|---|
| `junction-1` | 869 | 960 | **91** | **0** | **0** |
| `mosque` | 1164 | 1267 | **103** | **0** | **0** |

**Nothing painted on drivable surface**, which is the load-bearing check — every line gets a ghost
body and a solid one sets `on_white_continuous_line`:

| | kerb lines | total | median | longest | midpoint inside `road.buffer(-0.25)` |
|---|---|---|---|---|---|
| `junction-1` | 91 | 488 m | 4.7 m | 19.3 m | **0** |
| `mosque` | 103 | 615 m | 4.4 m | 19.3 m | **0** |

Worst vertex turn across every kerb line: 140.0° on `junction-1` and 144.0° on `mosque`, both under
the 150° cut, against 179.9° before it. Every arc stands exactly 0.15 m clear of the nearest line
already drawn, so the kerb takes over where the lane's edge line stops.

**The drive is unchanged.** `tools/drive.py --render offscreen` on the rebuilt datasets:
`junction-1` 352 of 370 steps, `arrive_dest=True`, completion 0.953; `mosque` 680 of 723,
`arrive_dest=True`, completion 0.950. `sanity_check PASS` and `result OK` on both.

`check_dataset` now reports it: `441 ROAD_EDGE_BOUNDARY … 56 left unpainted on junction stubs · 91
kerb line(s) round the junctions` on `junction-1`, and 485 / 86 / 103 on `mosque`.

`uv run pytest` — 388 passed, 1 failed, and the failure is not this change:
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` reports 3 of 396 routes over the 30°
gate, and reproduces identically at HEAD in a clean worktree with none of these edits. It came in
with the workspace regenerated at 01:38 on 2026-08-16 and is open. `uv run ruff check` passes.
