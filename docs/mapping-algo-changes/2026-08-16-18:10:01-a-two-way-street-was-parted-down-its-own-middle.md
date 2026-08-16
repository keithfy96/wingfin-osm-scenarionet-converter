# A two-way street was parted down its own middle

## Symptom

v26 opens a 1 m median between carriageways carrying traffic in opposite directions. Keith
looked at the result and said it was generally right, with one defect:

> "for ways that are in two directions, there's also a gap between the lanes that shouldn't be
> there because it's not a separate carriageway — way 182502377, 1351503424 and 107911751"

**22 of mosque's 26 two-way edge pairs had been split open along their own centreline by
1.001 m**, across 8 OSM ways. junction-1 had none.

```
 mosque · way 182502377, edge 1239189255 → 1239242579 · cross-section looking along FORWARD travel
 left-hand traffic · lane indices run centre-out: idx0 hugs the centreline (offside),
 idx(n−1) is kerbside · + = left turn, − = right turn
 numbers are metres across the section; 0.00 is the forward lane's centreline, which never moved
 + is KERBWARD of forward travel (its left) · − is OFFSIDE of it (its right, the way-line side)

 ═══════════════════════════════════════════════════════════ KERB (forward side) ══

   +1.75 ─── edge ────────────────────────────────────────────────────────────────
    0.00 │ 956ba26e70 │ way 182502377 idx0/1 │ forward  ──────────────────────►    1 lane
   −1.75 ─── edge ────────────────────────────────────────────────────────────────
   −1.75 ═══ OSM WAY CENTRELINE, way 182502377 ═══════════════════════════════════
    ┊
    ┊    ✗  v26 OPENED 1.001 m OF BARE NOTHING ALONG THE WAY'S OWN CENTRELINE  ✗
    ┊       ✗  ✗  ✗   there is no median here — it is one street   ✗  ✗  ✗
    ┊
   −2.75 ─── edge, v26 ────────────────────────────────────────────────────────────
   −4.50 │ ca9dfcbfec │ way 182502377 idx0/1 │ backward ◄──────────────────────    1 lane
   −6.25 ─── edge, v26 ────────────────────────────────────────────────────────────

 ══════════════════════════════════════════════════════ KERB (backward side) ══

   backward lane centre: −3.500 m at v25 → −4.501 m at v26 → −3.500 m now.
   Only the backward half moved, so the street tore in two along the line it is drawn on.
```

## Fundamental cause

`_road_components` makes a road out of `(OSM way, direction)`, so **the two directions of a
two-way street are two different roads** — and kerbward points opposite ways for them. Any shift
given to one and not the other parts them along the way line. `_lateral_neighbours` refuses to
*demand* that (it drops any pair sharing a source way), but nothing forbade it happening for
another reason, and that is what did.

The trigger was two readings, both arithmetic dust:

| the pair | clearance read at v25 | what it actually is |
| --- | --- | --- |
| `ee2c3f00ec` way 1351503424 fwd × `fe060d4ef5` way 1351503431 bwd | **−0.001 m** | the two halves of one street, across a way boundary |
| `9921e23a4d` way 760225055 bwd × `faffaa7cc9` way 1213764873 fwd | **−0.004 m** | the same |

Both are the seam down the middle of a street, read where OSM happens to split it into two
ways — so the shared-way exclusion does not catch them, they look like two carriageways
overlapping by a millimetre, and the layout parts them to the full 1.0 m target. Each shift then
travelled the whole chained road: **1 mm of misreading moved a 26-lane road 1.001 m and a
2-lane road 1.006 m**, splitting 8 ways at 22 places.

The deeper point is that the exclusion was written against the wrong unit. A street's seam is a
property of the *street*, not of the OSM way that happens to carry a stretch of it, so testing
for a shared way id can only ever catch it in the middle of a way and never at a join.

## Fix

`src/osm_scenario/generation.py`, three edits, all inside the v26 separation pass.

1. **`_road_components` unions both directions of a way.** A road is now a whole street, not one
   carriageway of it. The two seam readings become same-road pairs, which `_lateral_neighbours`
   already skips — the misreading disappears rather than being special-cased. Road counts:
   mosque 39 → 33, junction-1 32 → 23.
2. **`_two_way_roads` names the roads that carry both directions, and `_separated_roads` pins
   them** to a budget of `(0.0, 0.0)`. Both halves can only shift kerbward and kerbward is
   opposite for them, so the only shift that keeps a street whole is zero. The solver is
   otherwise untouched: the budget already carried a floor and a ceiling per road.
3. **A demand whose yielding road is out of budget passes to the other road.** Fewer lanes still
   yields first; without the second turn a demand reaching a pinned street would be dropped
   rather than met by the road that *can* move. It does not fire on either extract — no
   separation demand there touches a two-way street at all — and the hand-built test is what
   exercises it.

The `set(source_way_ids) & set(other.source_way_ids)` test is kept: the road unit keys on
`source_way_ids[0]`, so two lanes can share a later way id without sharing a road.

**`GENERATOR_VERSION` stays `direct-osm-stage2-v26`** (shipped in `ee81c0e`). This corrects the
v26 pass rather than adding a new one, and holding the version is deliberate: no lane, connector
or finding id moves here, and a generator fix that leaves the version alone is exactly the case
`generation_fingerprint` is designed to survive. Measured — the fingerprint is identical to the
one v26 produced on both extracts (`47021421160c`, `253dda24cd12`), so nothing that was bound to
v26 is unbound by this.

**What pinning costs, measured before it was chosen:** not one genuine separation demand on
either extract touches a two-way street — 56 on mosque and 23 on junction-1, none of them. A
street shields itself, which is why: its two halves occupy each other's offside, and a demand
needs an opposing carriageway *on* the offside. Where one ever does reach a street, edit 3 gives
it to the other road, and where both roads are streets `_opposing_overlap_findings` reports it.
A street parting down the middle is a worse answer than an overlap left standing and said out
loud.

## Verification

Both `source/map.osm` still match their manifests (mosque `00e30460458d`, junction-1
`4607fd469eba`). Both workspaces regenerated; all figures re-measured from the models by script,
against the v26 models saved before the change.

**Every street is whole again.**

| | mosque | junction-1 |
| --- | --- | --- |
| two-way edge pairs | 26 | 50 |
| parted by more than 1 mm — v25 / v26 / now | 0 / **22** / **0** | 0 / 0 / **0** |
| worst gap — v25 / v26 / now | 0.0000 / **+1.0058** / **0.0000** m | 0.0000 / 0.0000 / **0.0000** m |

Way `182502377`'s backward lane is back at −3.500 m, where v25 had it.

**v26's own result survives untouched.** Independent census of every near-parallel lane pair:
0 opposing pairs overlapping and 0 short of 1.00 m on both extracts, worst clearance +1.000 m,
exactly as v26 left it. Keith's junction is unchanged — `859429321` / `859429322` against
Persiaran Perdana's SW carriageway still reads +1.000 m at its tightest.

**Blast radius shrank and nothing else moved.**

| | mosque | junction-1 |
| --- | --- | --- |
| `separated_lanes` | 189 → **161** of 405 | 132 → **132** of 285 |
| `separated_roads` | 12 → **10** | 7 → **7** |
| lane / connector / finding ids and counts | identical (405 / 200 / 228) | identical (285 / 116 / 144) |
| connectors changing status | 0 | 0 |
| lanes whose worst interior bend got worse | 0 | 0 |
| direct continuations whose gap grew | 0 of 270 | 0 of 211 |
| total sideways step at continuations | 42.59 → **42.40 m** | 40.77 → **40.77 m** |
| same-direction overlapping pairs | 25 → **24** | 10 → **10** |

junction-1's model is byte-for-byte what v26 produced — 0 lanes changed. On mosque 29 lanes
changed: the 28 that stop being moved, plus slip `182502392` idx0, whose `_uncrossed_lanes` pull
now aims at the road it joins in its unmoved position.

**Way `107911751`, Keith's third example, never showed this defect** — 0.0000 m between its two
directions at v25, at v26 and now, and neither of its lanes has ever moved. It sits at node
`1927184932`, where the median link moved 4.06 m away from it, which is v26 working as intended.

`uv run ruff check` passes. `uv run pytest`: 401 passed, 1 failed —
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, which fails identically before
and after this change (3 of 396 swept routes over 30°, on two `MIN_TRIMMED_LANE_M` lanes) and is
recorded as open in CLAUDE.md.

Three tests added to `tests/unit/test_generation.py`:

- `test_both_directions_of_a_street_are_one_road` — a street mapped as two ways, each carrying
  both directions: the shape that produced the seam.
- `test_a_two_way_street_is_never_parted_down_its_own_middle` — a two-lane street that would
  yield under the fewer-lanes rule against a three-lane carriageway. The street does not move
  and the carriageway takes the whole 0.75 m.
- `test_no_two_way_street_on_the_real_maps_is_parted_down_its_own_middle` — on every workspace
  present, every way carrying both directions has its two innermost lanes touching. This is the
  test that would have caught v26.
