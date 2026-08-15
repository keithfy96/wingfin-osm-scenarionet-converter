# A merging road crossed the lane it was joining, and the merge hauled it back

- **Date:** 2026-08-15 19:15:13
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py` (`JoinLine`, `_join_line`, `_unit`,
  `_road_behind`, `_uncrossed_lanes`, `TaperTarget`, `_merge_taper_plan`, the geometry block in
  `build_lane_model`, `GENERATOR_VERSION`), `tests/unit/test_generation.py`
- **Generator version:** `direct-osm-stage2-v22` → `direct-osm-stage2-v23`

## Symptom

Keith, on the Stage 2 map, pointing at three merges: *"the lane ends up angling in and eating
into the center lane, i don't want that to be the case … there's absolutely no reason the lane
needs to turn in before turning out"*, and later, with the geometry in front of him: *"the 2nd
last lanelet is already past the angel of the kerbside lane, all you have to do is not make it
point further to the way centreline but instead have to attach to the kerbside lane."*

Both halves of that were literally true and measurable.

## Fundamental cause

**A joining way's last edges aim at the junction node, and that node sits inside the
carriageway.** OSM ends the way on the *other* way's centreline, which on a three-lane road is
the middle lane. So the road converges on the lane it merges into, **overshoots it**, and the
merge taper then hauls the last lane back out. Distances measured **across** the line of the
lane being joined:

```
 node 1928630009 · ramp way 182502409 merging into way 776021091 (3 lanes, +10.99°)
 left-hand traffic · travel direction EAST · idx0 hugs the centreline, idx2 is kerbside
 + = left turn · − = right turn · distances measured ACROSS idx2's own line

 ══════════════════════════════════════════════════════════════════ KERB ══

  +13.98 ●  3fd9dca0a0c74f0b            ✓ well outside, on the kerb side
          ╲
  +10.07  ●
           ╲   3ad37ab301e5a43e  +7.39 → +3.92      ✓ converging on the lane
            ╲
   +1.77    ●  15438e6fd90cf39e starts             ✓ still outside
             ╲                                        (8.96 m — never redrawn by the merge)
    0.00 ─────╳────────●══════════════════════►  idx2  b63366201b38cca1
               ╲      ╱                              kerbside — the TARGET, 1 feed
   −1.21       ●─────╱  ✗ 15438e6fd90cf39e ENDS 1.21 m PAST the lane it joins
                        ✗ 72fdbea2a86f51e8 (15.46 m) is then drawn back OUT to 0.00
                          — the turn IN, then OUT
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   −3.50 ──────────────────────────────────────►  idx1  eef18fbc845691d7  MIDDLE
          ✗ 6.09 m² of 15438e6fd90cf39e's ribbon and 14.71 m² of              1 feed
            72fdbea2a86f51e8's lie on fa74351a73e87a68, the lane feeding this one
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   −7.00 ──────────────────────────────────────►  idx0  a566b487c1bad350   1 feed
 ════════════════════════════════════════════════════════════ CENTRELINE ══

   travel direction ────────────────────────────────────────────────────────►
```

**The overshoot is not in the lane the merge owns.** On this ramp it is `15438e6fd90cf39e`, an
ordinary lane the merge code has never redrawn, which is why no change confined to
`72fdbea2a86f51e8` could ever fix it — and three attempts confined to it did not. All three
merges Keith named have this shape, overshooting by **1.21 m, 1.40 m and 1.52 m**.

## Fix

`_merge_taper_plan` now returns a `TaperTarget` carrying the anchor lane as well as the point,
because only the anchor's **line** — direction as well as position — can say that a road has
crossed it. What the plan *decides* is unchanged: the `contested` test compares `.point`.

`_uncrossed_lanes` runs immediately before the taper. For each merge it walks back from the
subject through **single** continuations — `entry_lanes` / `exit_lanes` naming a *lane*; a fork
has no one road behind it and a connector is another lane's traffic, not this road carrying on
— and moves every vertex on the wrong side of the line **perpendicular onto it**, keeping its
distance along. The subject's own moved end is left to `_tapered_line`, which owns it.

Two guards, both off numbers already on `ConverterConfig`, so `configuration_checksum` and the
schema do not move:

- A road within `merge_taper_min_gap_m` of the line at the far end of the walk never approached
  from a side, so there is no crossing to read.
- **A road past the line further back than `merge_taper_length_m` is left alone entirely.**
  That is two carriageways of different widths mapped as separate ways, running parallel.
  `mosque` way `935525163` is exactly this — 1.75 m off, half a lane, for **115.6 m** of road
  across four merges — and pulling a 70 m lane sideways is not a merge correction.

`_tapered_line` is untouched and still runs afterwards; `topology.py` is untouched.

**Two shapes were tried first and both are recorded because both were wrong.** Stopping the
lane short and letting the junction band cover the difference opens a hole at **26** `mosque`
merges that were seamless. Redrawing each merge as a cubic tangent to the road behind and to the
lane ahead bowed every lane it touched in `junction-1` — all of which had been dead straight —
by up to **2.31 m**, and pushed the ramp's ribbon on the middle lane from 14.7 to **22.1 m²**,
making the reported defect worse. A merge correction is a sideways pull, not a bend.

## Verification

Both workspaces rebuilt from `HEAD` first, because both held output from the reverted attempt.

**Geometry only:**

| | mosque | junction-1 |
|---|---|---|
| lane / connector id sets | identical | identical |
| connector `status`, `movement`, `turn_angle_degrees`, `from_lane_id`, `to_lane_id` | identical | identical |
| `findings`, `restrictions`, `signals`, `stop_lines` | identical | identical |
| lanes with a changed non-geometry field | 0 | 0 |
| `merge_tapers` | 40 → 40 | 21 → 21 |
| **lanes whose centreline moved** | **10 of 405** | **8 of 285** |

Every one moved sideways only: vertex counts unchanged, **bow off the straight line 0.000 m →
0.000 m** on all eighteen, and no lane's length changed by more than 0.33 m. Three on each map
— `15438e6fd90cf39e`, `de780b2c9152ccd8`, `2603bce63d3ee855` — are lanes the merge code did not
previously own. For comparison the taper alone already redraws 40 and 21.

**The complaint itself:**

| | mosque | junction-1 |
|---|---|---|
| ramp ribbon on the **middle** lane `fa74351a73e87a68` | — | **20.79 → 6.88 m²** |
| `15438e6fd90cf39e` ends, across the lane it joins | — | **−1.21 → −0.00 m** |
| `554ef0cc359ab6a0` ribbon on a road it cannot reach | **15.5 → 6.5 m²** | — |
| `dd9a54123dcc879f` | **17.2 → 7.2 m²** | — |
| `72fdbea2a86f51e8` | **14.3 → 5.0 m²** | 14.7 → **5.4 m²** |
| `15438e6fd90cf39e` | 5.7 → **1.2 m²** | 6.1 → **1.5 m²** |
| map-wide ribbon on a road it cannot reach | 818.2 → **775.5 m²** | 245.4 → **223.4 m²** |

**Nothing came apart and nothing was forced together.** Lane-to-lane joins touching **55 → 55**
and **35 → 35**; widest gap between continuing lanes 14.17 → 14.17 m and 17.25 → 17.25 m; widest
junction gap a drive crosses 17.63 → 17.63 m and 14.43 → 14.43 m, against
`ego_route.MAX_CROSSING_M` of 20 m.

**Route sweep, 3,000 random lane pairs per workspace:**

| | mosque | junction-1 |
|---|---|---|
| routes built | 353 → **353**, identical set | 538 → **538**, identical set |
| geometry refusals | 0 → 0 | 0 → 0 |
| worst per-vertex turn | 35.38° → 35.38° | 35.38° → 35.38° |
| routes with a vertex over 30° | 3 → 3 | 8 → 8 |

18 `mosque` and 46 `junction-1` routes changed their worst vertex, from −8.75° to +0.83° and
−0.31° to +0.28°.

`uv run pytest` **378 passed**, seven of them new; `uv run ruff check` clean; `npm run
typecheck` and `npm test` (139) clean in `web/`. Each new rule was proved to bite by breaking
the implementation: not reaching back past the merge lane, scaling the perpendicular pull so it
bends rather than lands, and dropping the parallel-offset guard each fail a different test.

Both Stage 4 models were already stale (`mosque` v18, `junction-1` v17), so the version bump
costs no live review.
