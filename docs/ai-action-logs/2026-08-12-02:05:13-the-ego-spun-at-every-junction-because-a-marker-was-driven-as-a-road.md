# The ego spun at every junction, because a marker was being driven as a road

- **Date:** 2026-08-12 02:05:13
- **Asked by:** Keith — "when the ego vehicle reaches the light, it just makes a u turn and
  proceeds in the opposite direction rather than waiting, is that due to the mode i have it
  set at?", then "at some junctions the turn still feel quite abrupt, i'd expect an arc
  instead of a right angle turn"
- **Files changed:** `src/osm_scenario/ego_route.py`, `web/src/route/geometry.ts`,
  `src/osm_scenario/cli.py`, `tools/check_dataset.py`, `tests/unit/test_ego_route.py`,
  `web/test/route/geometry.test.ts`, `CLAUDE.md`

**No change to `generation.py`, `topology.py`, `ConverterConfig` or the lane model schema**,
so no `docs/mapping-algo-changes/` entry and the generation fingerprint does not move.

## Symptom

Driving a converted scenario, the ego reached the traffic light, spun round and went back the
other way instead of waiting.

Two separate things, and only one of them was the light.

**Not waiting was the mode.** `--agent-policy replay` does not drive the car, it teleports it
along a list of positions; a red light in MetaDrive is an invisible wall and a teleported car
goes through walls. Waiting needs `--agent-policy idm`.

**The spin was in the recorded track.** Measured on the route Keith had built at the time:
**13 steps of 575 turned more than 30° in one 0.1 s tick, four of them a full 180°**, at
steps 168, 211, 272 and 467; step 212→213 travelled 1.32 m *backwards*.
`ReplayEgoCarPolicy` calls `set_heading_theta` from that array every step, so it played back
exactly as recorded.

It happened at the light only because a light's `stop_point` is the signalled lane's
downstream end, and a lane's downstream end is a junction. Every light anyone places lands on
one of these. `--agent-policy idm` hid it rather than fixing it: its steering follows the
nearest point on the whole recorded line and cut across a 3.5 m kink without noticing.

## Fundamental cause

`ego_route.route_polyline` spliced `ConnectorFeature.centerline` in as the path across every
junction. It is not one. `topology.connector_curve` builds a **marker for the inspection
map** and says so in its docstring, and it fails as a driving line in two distinct ways:

| shape | count of 83 active | what it does to the drive |
| --- | ---: | --- |
| **retracing stub** — the marker branch, where the two lanes already meet | 44 | a 3 m stub back up the approach: drive it, jump back, drive it again |
| **untangent Bezier** — bent around the OSM node, which sits on the *way* centreline while the curve's ends sit on the *lane* centrelines a lane-width away | 39 | a 90° turn taken as two corners with a bend between them |

55 of 83 turned more than 100° at a vertex. Over the 27 genuine turns of 30° or more the
median was **82.1° at the entry corner, 28.1° inside the curve, 82.1° at the exit corner**,
across **2.81 m** of path — an implied radius of **1.8 m**, tighter than a car can physically
turn. **Only 2 of 83** produced a drive line that steered no more than the turn required; the
typical junction swung through **272° of steering for an 87° change of direction**. Then the
resample at 1.388 m/step left a median of **2 recorded positions** in a whole turn.

A third, smaller shape had the same effect from a different cause: lane centrelines are
offset sideways off their way, so where the bearing changes at a node the next lane starts
0.26 m to 0.75 m *behind* the last one ended. Concatenated, that is one sample driven
backwards.

`MAX_JOIN_M` already refused a *hole* at a join. Nothing refused a *reversal*, which is why
it shipped.

## Fix

Both implementations, kept in step: `src/osm_scenario/ego_route.py` and
`web/src/route/geometry.ts`.

**`_turn` builds the join from the two lanes.** Take the direction the approach lane is
heading and the direction the exit lane leaves in, cut back into both, and lay a cubic
between the cut points whose end tangents *are* those two directions — so the drive leaves
along the road it is on and arrives along the road it is joining, with no corner at either
end. The connector is still consulted for **whether** a step crosses a junction, which sets
the gap allowance (`MAX_CROSSING_M` 20 m against `MAX_JOIN_M` 5 m, because the two lane lines
of a real crossing stop 1.7–5.4 m apart on `junction-1`). Never for its shape.

**Three constants took several goes**, and each failure is visible in the geometry:

- the Bezier's handles are a fraction of the **chord**, computed from the turn angle. Off the
  chord alone (the usual 0.5523) the curve pinched to 2.7 m of radius where the geometry
  called for 24, because a junction's two ends are offset sideways as well as turned. Off the
  **trim** instead, it degenerated the other way on two short lanes: the trim shrank to a
  fraction of the chord, the handles with it, and the curve became a straight line with a
  **176° hook** at each end.
- a turn may eat `max(0.4·L, L − 1 m)` of a lane. The fraction alone starves chains of short
  ones — `junction-1` has 5.8 m and 6.0 m lanes between junctions, and by the third the arc
  was built over a metre and came out at 1.6 m of radius.
- curvature for the speed profile is **turn per metre**, not a circumradius. The circumradius
  reads a polyline's concentrated bend as if it were spread over the window and reported
  5.4 m where the path really turned through 2.7 m.

**A lane change is positioned by projection, and a run of them is one manoeuvre.** Taking
both lanes' midpoints is only right when the car arrives at the start of the lane it is
leaving, and it usually does not — a junction turn trims the front off, and a change
immediately before leaves only the far end. Taking midpoints then put the far side of the
crossing *behind* the near side and the curve doubled back: **118° of turn in a single 0.1 s
step** on a route with two changes in a row. The crossing is now placed in the window the two
pieces actually share, and consecutive changes are built as a single sweep across all of
them, because a car crossing three lanes crosses them once.

**Every join must go forwards, and nothing may snap round.** A shallow join has any
overlapping head trimmed off the next lane, resumed at the foot of the perpendicular so no
length is lost. Only a shallow one: on a real turn the exit lane legitimately begins behind
where the approach ended, and trimming there refused movements the map permits. After
assembly, `_refuse_reversals` rejects any vertex turning more than 150° — the mirror of the
hole check, which existed from the first day while this did not.

**Speed follows the geometry** (Keith's choice when asked). `speed_profile` caps the speed at
every point by the curvature there — 1.8 m/s² of lateral acceleration, about 0.18 g — then a
forward and a backward pass bound how fast it may change, so the car brakes *before* a
junction rather than at it. The track is sampled in **time** rather than at a fixed spacing,
so a slower stretch simply gets more samples per metre. `Route.duration_s` is therefore not
`distance / speed`, and `metadata.sdc_route` reports `slowest_kph` beside `speed_kph` so a
reader who divides one by the other can see why.

**`tools/check_dataset.py` reports the worst per-step heading change and fails above 30°.**
That is the check that would have caught this, and it runs on the shipped pickle in the
interpreter that drives it. `sanity_check` checks shapes and lengths and never asks whether
the drive is drivable — it passed throughout.

## Verification

`uv run pytest` **300 passed** (was 289). `uv run ruff check` clean. In `web/`:
`npm run typecheck` clean, `npm run test` **130 passed** (was 129), `npm run build`
regenerates all three bundles.

**The spin is gone.** On Keith's own route, from MetaDrive's venv:

| | before | after |
| --- | ---: | ---: |
| worst turn in one 0.1 s step | **180.0°** | **5.1°** |
| steps turning more than 30° | **13** | **0** |
| peak lateral acceleration | 41.2 m/s² | 2.0 m/s² |
| `sanity_check` | PASS | PASS |

The 41.2 m/s² figure is from the *second* symptom Keith found, the double lane change; the
first route's reversals were worse still and unmeasurable as an acceleration because the
heading simply inverted.

**Nothing is refused that used to build.** Over 3,000 random lane pairs on `junction-1`,
2,168 have no drive between them at all and **832 build — none refused by the new checks**.
An earlier version of `_advance_past` refused 4 of them by policing sharp turns as well as
overlaps, which is what confined it to shallow joins.

**The page and Python still agree.** Over **400 real routes, 223 km**, the worst
disagreement in reported distance is **4.50 m on an 1822 m route** (0.25%), and neither side
refuses a route the other builds. Previously recorded as 3.5 m over 1.1 km on 40 routes; the
sample is ten times larger because the geometry now builds routes it used to refuse.

**The drive itself.** `--agent-policy replay`: 499 of 513 steps, `arrive_dest=True`,
completion 0.953, minimum speed 2.01 m/s — the crawl is one genuinely tight corner out of a
6 m lane, and it is 3.2 m of a 386 m route. Offscreen 3D unchanged: ground **+0.0 m, 0% of
it above the road**, so the terrain work is untouched.

**One thing that got worse, and is not this change.** `--agent-policy idm` ends this route
early with `out_of_road` at 4.22 m lateral against a 4 m limit, at step 156 of 513. It did the
same before at 4.26 m on the previous route. `TrajectoryIDMPolicy`'s lateral controller loses
a reference line that steps sideways, and Keith's current route has three lane changes in a
row — 7 m of lateral movement. This is MetaDrive's policy, not the data: the same track
replays cleanly to `arrive_dest=True`.
