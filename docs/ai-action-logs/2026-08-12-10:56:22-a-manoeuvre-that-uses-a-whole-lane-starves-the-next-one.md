# A manoeuvre that uses a whole lane starves the next one, and the recorded car now stops at reds

- **Date:** 2026-08-12 10:56:22
- **Asked by:** Keith — "my concern is that you made a superficial change, and there is no
  change to the underlying connectors that are causing this problem, could you confirm that it
  was the connectors allowing the vehicle to make the wild swings in the first place?", then
  "well i checked the earlier fix, it still drove right through the traffic light", then "why
  does it time out at 94s? Can't it wait longer?"
- **Files changed:** `src/osm_scenario/ego_route.py`, `src/osm_scenario/conversion.py`,
  `src/osm_scenario/signal_plan.py`, `src/osm_scenario/cli.py`, `web/src/route/geometry.ts`,
  `tools/drive.py`, `tools/check_dataset.py`, `tests/unit/test_ego_route.py`,
  `tests/unit/test_signal_plan.py`, `web/test/route/geometry.test.ts`

Corrects and finishes
`2026-08-12-02:05:13-the-ego-spun-at-every-junction-because-a-marker-was-driven-as-a-road.md`,
whose verification section reported figures from a route that had already been replaced.

**No change to `generation.py`, `topology.py`, `ConverterConfig`, the lane model schema or any
connector**, so no `docs/mapping-algo-changes/` entry and the generation fingerprint does not
move.

## Symptom

Three things, and only the first was a question about the previous fix.

**Keith suspected the connectors were letting the car turn round.** They were not, and nothing
about them needed to change. Measured from `lane-model/reviewed.json`:

| | count |
| --- | ---: |
| connectors | 116 |
| `active` | 83 |
| `forbidden` | 33 |
| movements classed `reverse` (declared 158.3°–180.0°) | 25, **every one forbidden** |
| widest turn any *active* connector permits | **98.8°** |

The model has never permitted a U-turn, which is why the route builder will not offer a lane
back down the way he came: the page reads the same 83. On his own route the four junction
movements are declared `through +2.2°`, `right −89.0°`, `through −6.4°`, `through +3.9°`, and
the old build still turned 180° at two of them. The reversal was the connector's *marker*
geometry spliced in as a driving line, which is what the previous entry fixed.

**The previous fix shipped incomplete, and the shipped dataset failed its own gate.** The
sweep behind it counted only routes `route_polyline` *refused* and reported "832 built, 0
refused" as though that meant none were bad. Re-swept against the criterion
`tools/check_dataset.py` actually applies - no vertex turning more than 30° - **440 of 813
built routes failed**, and Keith's own dataset reported `worst turn 97.7 deg, 2 steps over 30
deg, peak lateral 35.1 m/s², result FAILED`.

**The recorded car drove through red lights**, and under `--agent-policy idm --lights tape` it
stopped correctly but then ended `arrive_dest=False, ran out of recorded steps`.

## Fundamental cause

### One defect, three places: a manoeuvre takes all of a lane and leaves the next one nothing

`_turn` and `_lane_change` each sized themselves against the lane in front of them and never
against what came after. Traced on Keith's route at the time:

```
 turn   ->a1817878   head 55.72 m   curve   8 pts  1.30 m   tail 6.33 m   worst  0.3°
 change ->c21d5460   head  0.32 m   curve  60 pts  9.60 m   tail 0.32 m   worst  4.6°
 turn   ->a6ab945d   head  0.19 m   curve  24 pts  4.20 m   tail 9.53 m   worst 82.4°  ← cusp
```

The grouped lane change swept 9.60 m across a 7.11 m lane and left a **0.32 m** tail. The
−89.0° turn after it was then bounded to `_spare(0.51) = 0.20 m` of trim while the handle is
sized off the **4.20 m** chord — 1.63 m. The control polygon folds and the cubic doubles back.
Off the untouched lane the same turn builds over 7.51 m with a worst vertex of 3.9°.

The same starvation happens between two *turns*: a 14.58 m lane between two junctions had
9.7 m taken by the first, leaving the second a 1 m approach for a 90° turn.

And the two trims were tied to each other — `min(wanted, spare(arriving), spare(leaving))` —
so the side with 48 m to spare was cut to match the side with none.

### A segment too short to have a direction

`_drop_repeats` collapsed points closer than **1e-6 m**. Trimming a lane at a length computed
from its own vertices lands **0.000078 m** and **0.000157 m** off the endpoint, which survived,
and `atan2` over that distance returns noise that reads as exactly 90.0°. This was the worst
vertex in **390 of the 440**.

### The profile under-read its own geometry

`PROFILE_SAMPLE_M` was 0.25 m while the recorded track samples at `speed × 0.1 s` — 0.1 m at
the speed floor. Curvature measured over 0.25 m spreads a bend the car meets over 0.1 m, so the
drive exceeded the very lateral limit the profile was computed to respect. And `MIN_SPEED_MPS`
was 2.0 on the reasoning that nothing `_turn` builds is tight enough to need less; a *lane
change* across a 7.11 m lane is an S-curve of about 2 m of radius, and 2 m/s through 2 m of
radius is 2.0 m/s² against a 1.8 cap. The floor, not the geometry, broke the comfort limit.

### A red light cannot stop a car whose positions are already written

`ReplayEgoCarPolicy` sets position directly each step; MetaDrive's light is a collision wall
and a teleported car goes through walls. Nothing was wrong: the stop simply was not in the
data. Under `--agent-policy idm` it *did* stop — 186 steps, 18.6 s — and then ran out of
recording, because the scenario is exactly as long as a drive that never stopped and
`tools/drive.py` bounded the episode by that length. MetaDrive did not impose that: `horizon`
is 100000 and `ScenarioEnv`'s `allowed_more_steps` defaults to `None`.

## Fix

Both implementations kept in step: `src/osm_scenario/ego_route.py` and
`web/src/route/geometry.ts`.

**Every manoeuvre reserves what the next one needs.** `_turn_reserve` estimates the trim the
following turn will ask for from the two untouched centrelines — exact for the direction,
because a change ends running parallel to the lane it joined. `route_polyline` looks one step
ahead and passes it to `_lane_change` (which moves its crossing earlier before shortening it)
or to `_turn` (which stops trimming the exit lane's start once the reserve would be eaten). A
lane change after a manoeuvre needs no reserve: it places itself in the window the two lanes
share, not at the far end.

**The reserve yields before a route is refused.** A short lane with a change and a junction on
it is a road the map genuinely has. An earlier attempt raised `RouteError` there and silently
cost **50 of 813** routes, which arrived as "no drive exists" because `plan_route` builds the
geometry itself.

**The two trims are independent.** The Bezier is tangent to both lanes whatever the cut lengths
are, so equalising them only discards room on the side that has it.

**A curve this module builds is checked where it is built.** `_turn_curve` halves the handle
while the curve's own worst vertex exceeds `MAX_CURVE_TURN_DEG` (20°, against a measured 4.1°
worst across all 83 connectors built off untouched lanes), ending at the chord, which has no
turn in it at all. `MAX_VERTEX_TURN_DEG` (150°) stays for lane-to-lane joins, whose shape is
not ours to choose.

**`COINCIDENT_M` is 1e-3 m** — below anything an OSM-derived map means, above the residue a
trim leaves.

**`PROFILE_SAMPLE_M` is 0.1 m and `MIN_SPEED_MPS` is 1.0 m/s**, so the profile is at least as
fine as the track it decides the speed for, and the floor no longer overrides the comfort cap.

**The wait at a red is written into the recorded positions.** `resolve_waits` projects each
signalled lane's stop point onto the drive, sets the car back `STOP_LINE_SETBACK_M` (5 m, where
MetaDrive's 0.25 m wall stands), and resolves the lights front to back — each wait moves every
arrival after it. Each light is read **twice**: once from the arrival the car would have
without stopping, which is what a driver sees and what decides whether to brake; then, if it
was red, again from the braked arrival, because slowing takes time and the light may have
changed during it. Collapsing that into one reading oscillates — the stop delays the car into a
green, the green removes the stop, and the arrival moves back into the red. `speed_profile`
pins the speed to zero there *after* the `MIN_SPEED_MPS` clip, so the existing forward and
backward passes brake into the stop and pull away from it. `_sample_in_time` writes the wait as
a vertex repeated at arrival and at departure; adding the wait to every later time instead
would spread standing still over the next tenth of a metre, which is a crawl.

**A stationary car keeps facing the way it was going.** `_headings` carries the last real
heading across every step too short to have a direction — `atan2(0, 0)` is due east, so a car
waiting at a red would have swung east and back, which is the same spin under a different
cause.

**The clock stays in one place.** `seconds_until_green` sits beside `colour_at` in
`signal_plan`, and `signal_plan` stopped importing `TIME_STEP_S` from `ego_route` — it takes it
as an argument — so the route builder can depend on the clock without a cycle.

**`tools/drive.py` no longer ends the episode at the recording's length** for a policy that
drives itself: the budget is the recording plus the longest red in the plan. It also warns when
a track with baked stops is replayed under `--lights live`, which redraws the offset per
episode and would have the car wait at the wrong moment.

**`tools/check_dataset.py` reads the baked stop against the tape** MetaDrive will actually play,
and fails if the car waits when the light is not red or moves off before it is green — two
derivations of one clock can drift, and that drift would look like a simulator fault. It also
reports the tightest radius the drive line turns through, which the heading rule hides: at
walking pace a car can turn through anything.

**The sweep is now a test.** `test_no_route_on_the_real_map_turns_more_than_the_gate_allows`
asserts no vertex over 30° over 1500 sampled pairs on the real model, *and* that the built count
stays above 300 with zero refusals — a fix that reached zero by refusing routes would not be
one. Reverting `COINCIDENT_M` alone makes it fail 172 of 395.

## Verification

`uv run pytest` **312 passed** (was 300). `uv run ruff check` clean. In `web/`:
`npm run typecheck` clean, `npm run test` **133 passed** (was 130), `npm run build` regenerates
all three bundles.

**The geometry.** Sweeping 3,000 random lane pairs on `junction-1` (seed 7):

| | before | after |
| --- | ---: | ---: |
| routes built | 813 | **813** |
| refused | 0 | **0** |
| built routes with a vertex over 30° | **440** | **0** |
| worst vertex over all built routes | 98.8° | **17.6°** |

**Keith's own dataset**, from MetaDrive's venv:

| | before | after |
| --- | ---: | ---: |
| worst turn per 0.1 s step | **97.7°** | **5.9°** |
| steps over 30° | **2** | **0** |
| peak lateral acceleration | **35.1 m/s²** | **1.6 m/s²** |
| `result` | **FAILED** | **OK** |

**The drive.** All four combinations now reach `arrive_dest=True` and `result OK`:

| run | before | after |
| --- | --- | --- |
| `replay` (the default) | drove through the red | **stops 58 steps, min 0.00 m/s** |
| `idm --lights tape` | `FAILED`, ran out of recorded steps | **arrives**, stops 182 steps |
| `idm --lights live --light-seed 0` | arrived; light was green | arrives; light still green |
| `replay --lights live` | — | arrives, with the baked-stop warning printed |

The baked stop is checked against the tape rather than asserted: `stops 54 s at
9f83d567b07605dc for 5.5 s; the tape reads red on arrival and green on moving off`.

**The page and Python still agree.** Over **400 real routes, 207.4 km**, the worst
disagreement in reported distance is **4.21 m on a 1298 m route** (0.32%), and the browser
refuses none of them.

## Known, measured, and not fixed here

The drive still contains bends no car could take: **tightest radius 1.3 m, 22 steps under 5 m**
on Keith's route. These are lane changes across `junction-1`'s 7.11 m lanes — crossing 3.5 m
sideways inside 7 m of road is a violent S whatever speed it is taken at, and `reserve` is not
the cause: with `reserve=0` the same crossing measures 2.56 m of radius against 1.98 m with it.
The manoeuvre is confined to the joining lane's own window, and a real driver would begin it on
the lane before. `tools/check_dataset.py` now reports the figure so it stays visible; fixing it
means letting a change span more than one lane, which is its own change.
