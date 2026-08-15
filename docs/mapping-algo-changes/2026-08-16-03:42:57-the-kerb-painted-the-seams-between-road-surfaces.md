# The kerb painted the seams between road surfaces

- **Date:** 2026-08-16 03:42:57
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/conversion.py (`_junction_kerb_boundaries`, `_kerb_rings`)
- **Generator version:** direct-osm-stage2-v25 → unchanged (export-time only; no fingerprint moves)
- **Follows:** `2026-08-16-03:19:30-one-kerb-was-drawn-as-a-chain-of-unequal-lines.md`, whose fix
  introduced this

## Symptom

Keith, on the `mosque` drive rebuilt an hour earlier: *"that has introduced new problems, because
it's adding the edges between the lanes as well. I understand that it's drawing the edgeline around
the entire connector, but I just need it on either side — it's messing up the lanes."* Short white
marks lying on open tarmac, in pairs.

**238 of `mosque`'s 408 kerb lines** were paint on the carriageway — 459 m of it, median length
1.93 m — and **140 of `junction-1`'s 284**, 270 m. Two of them, which are one segment traversed each
way:

```
483ba7d974d80121   (255.056, 444.976) → (253.127, 445.024)
a2638b8bc00746eb   (253.127, 445.024) → (255.056, 444.976)
```

```
 a junction mouth, plan view, looking along the road      ▓▓▓ tarmac   ═══ paint

     lane surface                        connector surface
   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ ┊ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓═══════╪═══════▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
                                 ═══════╪═══════   ← the ring goes in one wall
   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ ┊ ▓▓▓▓▓▓▓▓    and back out the other,
   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ ┊ ▓▓▓▓▓▓▓▓    so BOTH walls get painted
   ══════════════════════════════════════════════════════════ kerb (correct)

           the notch is 0.10-0.30 m wide - narrower than the 0.125 m line drawn
           on it, so the two marks read as one smudge in the middle of the road
```

## Fundamental cause

Lane and connector surfaces are each buffered from **their own** centreline, so where two of them
meet they do not quite close: a junction mouth is left with a notch 0.10–0.30 m wide. The previous
change told the kerb to trace every ring of the road union, and a ring traced literally goes *into*
that notch along one wall and back out along the other. Both walls are road edge as far as the
geometry is concerned, and both got painted.

**And the guard that used to stop it had been removed on a wrong reading of what it was.**
`_MIN_KERB_M = 2.0` was described in the previous change as a proxy for "do not paint on road a car
drives on", and replaced with the direct test — is the arc's midpoint inside `road.buffer(-0.25)`.
It was never that. It was the **needle filter**, and a notch wall is 1.93 m: just under it. The
replacement cannot see a notch wall at all, because a notch wall lies exactly *on* the boundary
rather than inside it. So the stray count read 0 and was telling the truth about the wrong thing,
and the acceptance measurement that shipped the change never asked the question the defect answers.

## Fix

Close the seams before the ring is traced, rather than filter the marks afterwards. A gap narrower
than the line drawn on it is an artefact of how the surfaces are built, not a road edge, and the
kerb should run straight past it. One step in `_junction_kerb_boundaries`:

```python
sealed = road.buffer(+_KERB_GAP_CLOSE_M, join_style="mitre", mitre_limit=5.0) \
             .buffer(-_KERB_GAP_CLOSE_M, join_style="mitre", mitre_limit=5.0)
candidate = unary_union(_kerb_rings(sealed))
```

`road` itself remains the test for what is drivable, so `_KERB_INSET_M` still measures against real
tarmac. Nothing else in the rule changed — the paint allowance, the 0.10 m join overlap, the
road-end squareness test, `_uncreased` and the id scheme all stand.

Four things not to re-derive:

- **`_KERB_GAP_CLOSE_M = 0.35` and the sweep chose it** — the smallest value that reaches zero on
  both extracts. 0.30 leaves one mark on each; 0.40 and 0.45 are also clean, so it is not a knife
  edge; **0.50 swallows a real island on `mosque`** (20 → 19).
- **Mitre joins, not round.** A round join pulls every convex corner of the network out and back by
  the radius. Mitred, nothing moves except where a gap is filled: measured, the kerb sits a median
  **0.004 m** from the true road edge, p90 0.08 m, reaching 0.46 m only at the notch mouths it now
  bridges — which is the point of it. Added area is 172 m² of 54,590 on `mosque` (0.3%).
- **It settles the islands for free.** Enclosed holes fall from **693 to exactly `mosque`'s 20 real
  islands** and from 330 to exactly `junction-1`'s 9 — the 673 and 321 needle-thin ones were this
  same defect seen from the inside. `_MIN_ISLAND_M` is now a backstop rather than the rule.
- **The count of road ends left bare falls from 100 to 39** on `mosque` and 96 to 38 on
  `junction-1`, and that is a correction rather than a loss: most of the old count were notch caps,
  not roads that stop.

## Verification

Stage 6 only; **`generate-map` was not re-run**, so no fingerprint moved and the reviews stay bound.

| | kerb lines | length | **on tarmac** | breaks | strays | road ends bare |
|---|---|---|---|---|---|---|
| `mosque` before | 408 | 1298 m | **238** (459 m) | 0 | 0 | 100 |
| `mosque` after | 142 | 636 m | **0** | **0** | **0** | 39 |
| `junction-1` before | 284 | 945 m | **140** (270 m) | 0 | 0 | 96 |
| `junction-1` after | 115 | 489 m | **0** | **0** | **0** | 38 |

"On tarmac" is the fraction of `line.buffer(0.15)` lying outside the road union, under 0.15 — a
kerb separates road from not-road, so road on both sides means it is not a kerb. Lengths after:
median 2.85 m, p90 12.5 m, longest 19.9 m.

**Additive, exactly.** `_map_features` compared feature by feature against the same code with the
kerb pass disabled: `mosque` **+142 / −0 / ~0** (1164 → 1306), `junction-1` **+115 / −0 / ~0**
(869 → 984).

**The drive is unchanged.** `tools/drive.py --render offscreen`: `junction-1` 352 of 370 steps,
`arrive_dest=True`, completion 0.953; `mosque` 396 of 416, `arrive_dest=True`, completion 0.952.
`sanity_check PASS` and `result OK` on both; `check_dataset` reports `115 kerb line(s) round the
junctions · 38 road end(s) left bare` and `142 · 39`.

**The road edge is no less covered for it.** Rebuilding MetaDrive's own semantic raster from the
pickles and counting road pixels touching ground with no paint between, in the 140 m square round
the ego: `mosque` 37.6 m → **37.1 m**, `junction-1` 55.1 m → **53.1 m**, while the painted area
falls by 7.5 m² and 5.9 m². Same coverage, that much less wrong paint.

`uv run pytest` — 391 passed, 1 failed, and the failure is not this change:
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` reports 3 of 396 routes over the 30°
gate and is already recorded as open. `uv run ruff check` passes.
`test_no_kerb_line_has_tarmac_on_both_sides_of_it` is new and is the test that would have caught
this — the existing `test_a_junction_kerb_never_lands_on_road_a_car_drives_on` passes a notch wall,
because a notch wall is on the boundary rather than inside it.
