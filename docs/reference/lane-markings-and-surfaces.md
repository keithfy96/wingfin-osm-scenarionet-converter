# Lane markings and road surfaces at export

Junction kerbs, sealing holes in the tarmac, and clipping a line that would otherwise be
painted through the road beside it. All export-time; no fingerprint moves.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

### A junction is bare inside and kerbed outside, and both halves are deliberate

`_map_features` writes boundary features for `model.lanes` only, so a `ConnectorFeature` — a
junction turn — is a `LANE_SURFACE_STREET` polygon with no lines of its own. The **inside** of a
junction is therefore bare road, which is right: traffic crosses it, and painting every
connector's two edges would put 82 turns' worth of white line crossing each other through the
middle of every intersection.

**The edge of a junction is a different thing, and it was blank by accident until 2026-08-16.**
Every lane is cut back from its node by `_node_setbacks`, which left a median 9.17 m and up to
14.43 m of road edge with no paint on it — 61 of `junction-1`'s 82 active connectors bridge a gap
that wide; the other 21 are stubs where the lanes already touch and nothing was missing. What a
reader saw there instead was `terrain.frag.glsl:115` painting anything in `5 < value < 16` pure
white: ground is 0, a white line 10 and road surface 20, the semantic texture is filtered, so
**every road-to-grass edge gets a hairline about one texel wide whether a line is there or not** —
0.031 m on `junction-1` at 32 px/m against 0.156 m for a real marking. Keith looked at that and
said the connectors had no lane lines, which was exactly right. That hairline is also what draws
round every road on the map, and is the one Keith earlier chose to leave.

`conversion._junction_kerb_boundaries` paints it. **The rule is continuity, and the first version
of this got that wrong**: it took the outline of the junction surfaces alone, stood every arc
0.15 m off the line it met, and threw away anything under 2 m — so one physical kerb came out as a
chain of unequal lines with holes between them. Keith: *"it breaks the line into larger and smaller
lines on the exact same kerb."* Counted on the shipped datasets, **154 breaks over 276 m on
`mosque` and 186 over 292 m on `junction-1`**, split as 38%/46% the stand-off, 12%/12% the length
filter and 50%/43% gaps on a *lane's* own edge that were never candidates at all.

**And the second version got it wrong the other way**, which is the constant below that matters
most. Traced round the raw union, the ring dives into the notch between two surfaces that fail to
meet and comes back out along its other wall, painting **both**: 238 of `mosque`'s 408 lines and 140
of `junction-1`'s 284 were marks lying on open tarmac, 459 m and 270 m of them, in pairs about
1.93 m long. Keith: *"it's adding the edges between the lanes as well… I just need it on either
side."*

The rule now: **close the seams**, take every ring of the road network, subtract only what is
already painted, push each survivor into the line it meets, and reject the two things that must not
be drawn. **0 breaks and 0 marks on tarmac on both extracts**, 142 lines over 636 m on `mosque` and
115 over 489 m on `junction-1`, strictly additive — +142/−0/~0 and +115/−0/~0, not one existing
feature changes — and export-time, so no fingerprint moves. Seven things not to re-derive:

- **Close the road before tracing it, and judge it against the road that was not closed.**
  `_KERB_GAP_CLOSE_M` is 0.35 m, `buffer(+ε).buffer(−ε)` with **mitre** joins — round joins would
  pull every convex corner of the network out and back by the radius. 0.35 is the smallest that
  reaches zero marks on both extracts (0.30 leaves one on each, 0.40 and 0.45 are also clean, 0.50
  swallows a real island on `mosque`). It settles the islands for free: enclosed holes fall from 693
  to exactly `mosque`'s 20 and from 330 to exactly `junction-1`'s 9, the rest being the same defect
  seen from the inside. The kerb still sits a **median 0.004 m** from the true road edge, reaching
  0.46 m only at the notch mouths it now bridges, which is the point of it.

- **Never stand a kerb off the paint it meets.** `_KERB_PAINT_ALLOWANCE_M` is 0.02 m — enough that
  the kerb is not laid a second time over paint that exists, and no more, because two coincident
  lines are resampled out of phase by MetaDrive and draw as something neither of them is. The join
  is then made by `_KERB_JOIN_OVERLAP_M`, 0.10 m pushed along the arc's own end tangent: the end
  sits on the road edge and a tenth of a metre along that edge is still the road edge.
- **`_MIN_KERB_M = 2.0` was the needle filter, not a proxy for the drivable-road test**, and
  calling it one is exactly how the tarmac marks shipped: a notch wall is 1.93 m. Lowering it to
  numerical dust (0.05 m) was still right — the proxy cost 19 of `mosque`'s breaks and 22 of
  `junction-1`'s — but what had to replace it is `_KERB_GAP_CLOSE_M`, which removes the seam, not
  `_KERB_INSET_M`, which cannot see it.
- **`_KERB_INSET_M` (0.25 m) catches a line that strays *into* the road and nothing else.** A notch
  wall lies exactly *on* the boundary, so it passes cleanly — the stray count read 0 while 238 marks
  sat on the tarmac, telling the truth about the wrong thing. It is measured against the real
  surfaces, never the closed ones.
- **`_road_on_both_sides` is what does see a notch wall**, and it asks the question directly: a kerb
  separates road from not-road, so tarmac at ±`_KERB_SIDE_PROBE_M` (0.8 m) along the arc's **whole**
  length means it is the wall of a slot the closing could not reach. 6 such on `mosque` and 2 on
  `junction-1`, all 0.38–1.00 m. **Whole length, not most of it**: the next score down is 0.750, and
  those are 2–4 m arcs that are seam for part of their length and road edge for the rest — a
  majority rule threw out 3.80 m of kerb round a 118 m² traffic island on `junction-1`. An island
  can never be caught by this however narrow, because an island is a hole in the union and the probe
  lands outside the road on that side; `mosque`'s narrowest is 0.97 m.
- **A road that stops must never be painted across.** `_node_setbacks` leaves the end of every road
  square, so the network's outline runs straight over it, and filling that gap draws a stop line
  where there is none — with a ghost body, on road a car drives along. `_ROAD_END_SQUARENESS`
  (0.35) rejects an arc that runs square to the paint at **both** ends and is under
  `_MAX_ROAD_END_M`; one square end is a kerb turning a corner, which is ordinary. 39 left bare on
  `mosque`, 38 on `junction-1`, reported as `lane_markings.road_ends_unpainted`. It was 100 and 96
  before the closing, because most of that count was notch caps rather than roads that stop.
- **`_kerb_rings` takes exteriors plus islands**, and `_MIN_ISLAND_M` (0.3 m) is now a backstop
  rather than the thing doing the work — the closing has already sealed the slivers. It keeps the
  20 real islands on `mosque` and 9 on `junction-1`, whose inward-facing kerb was getting nothing.
  **The inside of a junction cannot be reached from here at all**: it is covered road, so it is on
  no ring.
- **`_MAX_KERB_TURN_DEG` is 150 and the histogram chose it.** Per-vertex turns over both extracts:
  6740 under 10°, a cluster of 40 at 80–89° where a connector's flat cap meets its side, then
  nothing until 32 sit at 170–179°. Those are seams between overlapping turns drawn as zero-width
  needles. `_uncreased` cuts at them and keeps both sides, because a needle is usually a metre of
  seam on the end of an arc that is otherwise kerb — and it rejects steps under
  `ego_route.COINCIDENT_M` for that constant's own reason: shapely repeats a vertex a fraction of
  a micrometre away, and a bearing over 78 µm is noise that hides the reversal it is looking for.
- **A kerb arc has no `side` and no `lane_id`.** It is merged from however many turns meet there
  and belongs to none of them; nothing in this repo or in MetaDrive reads either field on a
  boundary feature. `lane_markings.junction_kerbs` counts them, kept out of `edges` and `merged`
  because both of those are counted by feature type and a kerb would drive `merged` negative.

**Measure coverage at one texel, not at 0.20 m.** A drawn line is 2 px — `mosque`'s 2048 m terrain
square against this machine's 32768 px ceiling is 16 px/m, so 0.125 m. The first version's
acceptance check asked whether the road outline was within **0.20 m** of paint, three times wider
than the paint itself, and passed 393 m of edge on `mosque` that renders bare. Any check here uses
1/16 m.

### The road has to be whole before the lines on it mean anything (2026-08-16)

**A hole in the tarmac draws itself as a white line.** A lane surface is offset from its own
centreline, so where one edge of a road hands over to the next their square caps leave a wedge —
and the shader's `5 < value < 16` band catches the blend from road (20) across it to ground (0).
`mosque` carried **78 of these wider than a texel, 172 m², the widest 0.687 m**, 13 of them within
3 m of the driven line; `junction-1` 85 and 45.5 m². That is what Keith saw running into his lane,
and it is missing road rather than paint. `conversion._sealed_surfaces` closes them, sharing each
wedge out among the surfaces along it. Details and the four constants:
`docs/mapping-algo-changes/2026-08-16-04:50:16-holes-in-the-tarmac-painted-themselves.md`.

Two of them are worth having here, because both are MetaDrive gates rather than geometry:

- **`sanity_check` measures where a polygon is by averaging its vertices**
  (`scenario_description.py:270`) and refuses the map past 100 m. A ring is 5 points, so a 400 m
  lane that gains a hundred at one end reads as 136.8 m out. `_RING_STEP_M` segmentizes a sealed
  ring at 5 m, which adds points to edges that already exist and changes no shape.
- **`unary_union` silently drops a whole lane** on both extracts — 141.17 m² of `mosque` and
  295.9 m² of `junction-1`, valid in, valid out, that lane not covered. `_road_union` unions on a
  1e-9 grid instead. It had been invisible because nothing asked.

**MetaDrive draws every painted line short, and `tools/drive.py` now puts it back.**
`resample_polyline` (`utils/math.py:269`) steps with `np.arange(0, length, interval)`, which never
includes the endpoint, and `scenario_map.py:74/90` runs it over every line longer than
`interval * 2`. So a line over 4 m loses up to a whole interval off its end — **554.7 m of paint
across 585 of `mosque`'s 690 painted lines**, mean 0.95 m, and 448.2 m across 453 of
`junction-1`'s 548. It takes lane edges, dividers and kerbs alike, so the 0.10 m butt-join the kerb
makes into the line beside it is chopped off at both ends. `_keep_line_ends` rebinds the name in
the two modules that imported it — `scenario_map` for the raster, `scenario_block` for the ghosts —
and it is unconditional, because a line drawn short is a fault and not a preference.

The **interval** is the older half of this, and it is a preference: at 2 m the chords sag inside
every curve while the road polygon is filled at full resolution. `--line-interval-m` (default
**0.25**, `LINE_INTERVAL_M` in `.env`) passes `line_sample_interval` through the wrapper that
already sets the line width — `terrain.py:620` never passes it, so there is nothing to override.
**It moves the broken-line dashes from 2 m/2 m to 3 m/3 m**, because `points_to_skip =
floor(STRIPE_LENGTH * 2 / interval)` floors to 1 at interval 2; 3 m is what MetaDrive's own
`STRIPE_LENGTH = 1.5` asks for and the 2 m is the flooring artefact. `--line-interval-m 2.0` puts
the old dashes back and still keeps the line ends.

Together, road edge carrying no thick line: **`mosque` 324.0 m → 86.3 m** and **`junction-1`
420.8 m → 115.9 m**, split 185.0 m / 52.7 m and 203.5 m / 101.4 m between the two halves. What is
left is the 39 and 38 road ends left bare on purpose **and nothing else** — the same figure as if
the resampling were removed altogether. An earlier version of this section put the whole of the
remaining bare edge down to the chord sag; the truncation is the larger half and had not been
diagnosed.

**But some junctions have real lanes inside them.** A big intersection is often mapped as
several nodes joined by short ways rather than one node: `junction-1`'s node `1927184814` is
four one-way ways in a loop round the box. Those ways are shorter than the setbacks that cut
every lane back from its junctions, so `_trimmed_edge` scales both setbacks down and stops at
`MIN_TRIMMED_LANE_M` — keeping the road, which is right, and leaving a 2 m lane that reaches
further into the junction than any other. `generation.py` counts these as `trim_clamped_edges`.

Painted, that was eighteen 2 m marks pointing four ways across a box cars turn through — and
not only cosmetic, because `ScenarioBlock` gives every line a ghost body and only a solid one
sets `on_white_continuous_line`. `conversion._stub_lanes` now drops those boundaries at export.
Three things not to re-derive:

- **The test is the clamp, not a round number.** A lane measures `MIN_TRIMMED_LANE_M` only when
  the clamp bound; the next ones up (2.07 m, 2.37 m, 3.65 m) kept their setbacks and end
  outside both junctions, so their markings are on open road. At a 4 m threshold `junction-1`
  loses 82 boundaries instead of 56 and `mosque` 108 instead of 86.
- **Only the paint goes.** The lane polygon is still written — deleting the lane would cut the
  network, and MetaDrive builds its surface from lane features alone.
- **`_divider_boundaries` still runs over every lane, stubs included.** It decides broken vs
  solid from a lane's neighbours, so hiding a stub from it restyles a *surviving* neighbour.
  Suppress at write time, after the classification.

`lane_markings.junction_stubs` reports the count, kept out of `merged` because a dropped
duplicate and a deliberately blank junction are different facts. `tools/check_dataset.py`
prints it beside `junction_kerbs`. **The stubs' outward-facing edges do come back as kerb** —
they are in the unpainted union above — while their interior marks stay dropped, which is the
distinction the whole of this section turns on.

### And a line may not lie on tarmac either, not only a kerb (2026-08-16)

The rule above — *nothing here may land on drivable road* — was stated about **kerbs only**,
because a kerb was the last kind of line this converter learned to draw. Every other line is still
offset from its own lane's centreline with no idea what else is on that ground, and two facts meet
there: lanes of one OSM way know about each other (`generation.py` names neighbours within one
edge's lane list, which is what lets `_divider_boundaries` dash the line or drop the second copy)
while **a turning lane, a slip road and a merging ramp are always a different way**, so they are
never neighbours and every one of their lines stays a solid `ROAD_EDGE_BOUNDARY`; and **at a merge
or a diverge the two lanes must share tarmac**, because two 3.50 m lanes need 3.50 m between their
centrelines to stop overlapping. So each one's solid line lands inside the other's driving surface,
with a ghost body that sets `on_white_continuous_line`. Keith: *"the left turn lane has its edge
drawn into the straight road, and the straight road has its edge boundary drawn into the turning
lane… it would make it seem like road boundaries."* 70 lines / **651.1 m** on `mosque`, 19 /
**126.8 m** on `junction-1`; on way 1351503429 the branch's edge reached 1.38 m into the through
lane, 0.37 m **past** that lane's own centreline.

`conversion._uncovered_boundaries` cuts every lane boundary back to what is not inside another
drivable surface. Export-time, so no fingerprint moves. Five things not to re-derive:

- **The same-way exclusion is not optional and cannot be replaced by a threshold.** Two lanes of
  one way meet exactly on their shared edge, but a mitre join on a curve puts a legitimate divider
  up to **0.345 m** inside its neighbour — deeper than some real defects. Three surfaces never
  clip: the line's own lane, any lane sharing an OSM way with it, any junction turn it is an end
  of. The same list `generation._lateral_neighbours` keeps, for the same reason.
- **`_COVERED_PAINT_TOLERANCE_M` (0.05 m) must stay above `_KERB_PAINT_ALLOWANCE_M` (0.02 m)**,
  and that is the whole argument for clipping *before* the kerb is traced: a removed piece is at
  least that far inside the road, so it was never covering a ring and the kerb pass cannot paint
  it back. Verified — `junction_kerbs`, `road_ends_unpainted` and `surfaces_sealed` are unchanged.
- **Judged against the lanes' own polygons, not the sealed ones.** A patch closing a wedge between
  two surfaces is not a lane's tarmac. Hence before `_sealed_surfaces`.
- **`_MIN_PAINT_M` (0.5 m) is a needle filter and nothing more**, the lesson `_MIN_KERB_M` taught:
  *every* surviving piece under 2 m on either extract meets other paint at at least one end, so a
  bigger filter breaks a continuous road edge rather than removing a speck. Used twice — a piece
  shorter than it is not written, and **a hole shorter than it is not opened**, because a break of
  a few centimetres reads as a broken line. The interior holes measure 0.23 m and then nothing
  until 4.78 m. That closing is the only thing leaving paint on tarmac at all, and bounds it: the
  longest run left is **0.47 m** on `mosque` and **0.23 m** on `junction-1`, from 651 m and 127 m.
- **A line surviving in one piece keeps its id**; one cut in two gets `boundary_clipped` ids,
  because one id cannot name two lines. **`merged` had to stop counting line features** — a
  boundary cut in two adds one — and now counts `MapFeatures.boundaries_written`.

Not one divider was clipped on either map, which is the same-way exclusion working: a divider is
by definition the line between two lanes of one way. `tools/check_dataset.py` reports
`covered_paint` and **fails** when a line runs `_MIN_PAINT_M` or further inside a lane that is not
its own. See
`docs/mapping-algo-changes/2026-08-16-20:01:55-a-turning-lanes-edge-was-painted-through-the-road-beside-it.md`.
