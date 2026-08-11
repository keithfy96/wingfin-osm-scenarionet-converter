# A route builder, and a scenario that drives

- **Date:** 2026-08-11 04:38:11
- **Asked by:** Keith — "could you make a route builder for me in that case? make it with a
  GUI where i can select a start and an end point and it'll auto generate the
  TrajectoryNavigation necessary", after establishing that a fixed route still leaves a
  control policy to train, as long as there is more than one of them.
- **Files changed:** `src/osm_scenario/ego_route.py` (new),
  `src/osm_scenario/route_builder_view.py` (new), `src/osm_scenario/lane_payload.py` (new),
  `src/osm_scenario/conversion.py`, `src/osm_scenario/cli.py`,
  `src/osm_scenario/reachability_view.py`, `web/src/route/*` (new), `web/build.mjs`,
  `tools/check_dataset.py`, tests both sides, `CLAUDE.md`

No change to `generation.py` or `topology.py`, so no `docs/mapping-algo-changes/` entry.

## Symptom

`python -m scenarionet.sim` on the converted dataset:

```
scenario_description.py:641, in centralize_to_ego_car_initial_position
KeyError: None
```

The map loaded; there was nothing to drive.

## Fundamental cause

**`ScenarioEnv` has no start-and-end setting, and never did.** It is wired to
`TrajectoryNavigation`, whose entire input is a *recorded* car's positions; MetaDrive shifts
the whole map to that car's first frame before anything else happens. So "drive from here to
there" can only be said by putting a car in the file that already did.

The graph-based module that *does* take a start and a destination,
`EdgeNetworkNavigation`, is attached to no environment in 0.4.3 and its first call reads
`left_n["id"]`, which fits no dataset that exists.

Which route to record is a judgement about the map — which turn, which direction, whether a
lane change is wanted — so it belongs to a person. Hence a builder rather than a heuristic.

## Fix

**`ego_route.py`** turns two lane ids into the car. Shortest path over the same two relations
`_reachability` reports on, then geometry: a junction hop follows
`ConnectorFeature.centerline` rather than cutting the corner, a lane change is spread over
the middle 30% of both lanes so it is a diagonal rather than a sideways teleport, and the
result is resampled at MetaDrive's own 0.1 s step.

**`convert --routes`** reads a `routes.json` and emits **one scenario per route, sharing one
map**. That is the shape every ScenarioNet dataset has, and it is what makes `num_scenarios`
sample a different drive each episode — the answer to Keith's "what is there to train".
Without `--routes` the map-only output is unchanged.

**The page** follows the pattern Stage 3 already established: a compiled TypeScript client
embedded in a generated HTML page, exporting a checksum-bound JSON the CLI consumes. Click a
start, click an end, name it, add it, download. The identity block means a routes file drawn
on a stale map is refused rather than silently routed through lanes that have moved.

`lane_payload.py` is shared by both Stage 6 pages, so the builder cannot offer a move the
reachability page says does not exist.

## Four defects the checks caught, none of which would have raised anything

1. **Lane-change indices were off by one** in the client's path reconstruction — caught by
   `web/test/route/path.test.ts` before it ever ran in a browser.
2. **The lane change was a teleport.** Cutting both lanes at their midpoint put the car 4 m
   sideways at constant longitude. Found by a test asserting that lateral movement is paid
   for with travel.
3. **A guard fired on good data.** The first version refused any step over 100 m in the route
   geometry, reasoning that `parse_full_trajectory` truncates there. But resampling removes
   long steps, and `junction-1`'s lanes are two-point polylines — one is a straight 155 m
   segment. The check now runs on the *joins between pieces*, which is where a real hole
   appears, and where step length cannot be confused with a long road.
4. **The preview disagreed with the build**, twice: first 278 m against 440 m (the estimate
   left out the first lane and every junction), then 1430 m against 1161 m (it counted both
   lanes of a change in full, when together they span one lane's worth of road). Estimating
   was the mistake. `web/src/route/geometry.ts` now ports `route_polyline` and measures the
   line, and the page draws that same line.

A fifth was a stray NUL byte inside one of two hand-written template literals, so a junction
lookup silently missed and every connector measured zero. Both sites now go through one
`crossingKey` helper.

## Verification

`uv run pytest` **250 passed** (was 235). `uv run ruff check` clean. In `web/`:
`npm run typecheck` clean, `npm run test` **84 passed**, `npm run build` regenerates both
committed bundles.

**The two implementations agree.** Over 60 random lane pairs the client and Python return
the same existence answer, the same lane chain and the same lane-change positions; over 40
real routes the worst distance disagreement is **3.5 m on 1,140 m** — the difference between
measuring in WGS84 and in the local projection.

**The page works in a browser**, not merely in tests: clicking a lane sets the start and
reports how many lanes are reachable, clicking an unreachable end says so, a reachable end
draws the drive, and the downloaded file is correctly stamped. Two things had to be fixed to
get there — a `button.primary{width:100%}` rule that crushed the name field, and Leaflet's
canvas hit tolerance, which defaults to half the line weight and made a 3 px target.

**MetaDrive drives it.** Feeding the file the browser actually produced back through
`convert --routes`, then in Keith's Python 3.8 / numpy 1.24 environment:

| | steps | arrive_dest | route completion | reference | max off-route |
| --- | ---: | --- | ---: | ---: | ---: |
| `kenanga-offramp` | 352 | True | 0.950 | 511 m | 0.39 m |
| `kenanga-straight` | 127 | True | 0.955 | 185 m | 0.00 m |

`tools/check_dataset.py` reports both scenarios, both routes and `sanity_check PASS`.
`python -m scenarionet.sim --render none` prints `scenario:0, success` and
`scenario:1, success` before running off the end of its own 1,000,000-iteration loop.

**Regression:** 855 map features and 294 exit edges per scenario, `metadata.routing`
unchanged, fingerprint still equal to `reviewed.json`, and converting with no `--routes`
still produces the map-only dataset.
