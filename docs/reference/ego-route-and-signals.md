# Driving a dataset — routes, junction geometry and signals

What has to be in a converted dataset before MetaDrive can drive it: the ego route, the
geometry a route drives through a junction, waiting at a red light, and where traffic-light
timing comes from.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

### Making a dataset MetaDrive can drive

**`ScenarioEnv` has no start-and-end setting.** It is wired to
`TrajectoryNavigation`, whose whole input is a *recorded* car's positions, and
`ScenarioMapManager.reset` calls `get_sdc_track()` unconditionally — with no ego
car that is `KeyError('None')`, and no config skips it. So a route has to be in
the file, and choosing it is Keith's, not a heuristic's:

```bash
# 1. pick routes: open inspection/stage-6-route-builder.html, click a start lane,
#    click an end lane, name it, add it, download routes.json
# 2. build
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json
# 3. drive (MetaDrive's own venv)
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/drive.py workspaces/junction-1/scenarionet-10hz --render 3D
```

**Use `tools/drive.py`, not `python -m scenarionet.sim`.** `sim.py` loads the same
dataset and drives it correctly, but in 3D it shows a broken map, and none of the
settings that fix that can be reached from it. See "Why 3D needs its own runner"
below.

**MetaDrive never reads `routes.json`.** It is an exchange file between the
browser and our converter — a browser cannot write to disk — exactly as Stage 3
downloads `review.json` for `apply-review --submission`. MetaDrive reads the
pickles and nothing else, and the route inside them *is*
`tracks["ego"]["state"]["position"]`.

Without `--routes` the dataset stays map-only: `scenarionet.num`,
`scenarionet.check_existence` (it passes `steps_to_run=0`, so no simulator) and
`tools/check_dataset.py` all work; `scenarionet.sim` and `check_simulation` do
not.

Two more things that bite here:

- **`sim.py` loops to 1,000,000 scenarios** when `--scenario_index` is absent, so
  it ends with `AssertionError: Scenario Index ... out of range` after driving
  everything. That is their script running off the end, not a fault in the data.
  `tools/drive.py` stops at the end of the dataset.
- **The route builder previews the drive; Python re-derives it.** Both build the
  same geometry, and over 400 real routes — 207 km — they agree to within **4.2 m
  on a 1298 m route**, with neither refusing a route the other builds. If the two
  ever disagree the page offers drives the converter refuses, so
  `web/test/route/geometry.test.ts` and `tests/unit/test_ego_route.py` cover the
  same cases deliberately.

### The junction geometry a route drives is built, not read off the connector

**`ConnectorFeature.centerline` is a marker, not a driving line.** It looks like the
path across a junction and is not one — `topology.connector_curve` says so in its own
docstring, and it is drawn as a band on the Stage 2 inspection map. Splicing it into a
drive, as `ego_route` did until 2026-08-12, failed two ways:

- where the two lanes **already meet** (44 of `junction-1`'s 83 active connectors, because
  OSM splits a way whenever a tag changes) the marker is a 3 m stub that *retraces the
  approach*. Glued on after the lane it duplicates, the car drove three metres, jumped
  back, and drove them again — one sample travelling backwards and a heading reversed by
  180°, which `ReplayEgoCarPolicy` plays back exactly as recorded
- where the lanes are genuinely apart, the marker is a quadratic Bezier bent around the
  **OSM node**, which sits on the *way* centreline while the curve's ends sit on the *lane*
  centrelines a lane-width to the side. It is tangent to neither, so a 90° turn came out as
  two 82° corners with 28° of curve between them, over 2.81 m of path — 1.8 m of radius

55 of the 83 turned more than 100° at a vertex. **Only 2 of 83 produced a drive line that
steered no more than the turn required**; the typical junction swung through 272° of
steering for an 87° change of direction.

`ego_route._turn` now builds the join from the two lanes' own tangents: cut back into both,
lay a cubic between the cut points whose end tangents *are* the lane directions. The
connector is still consulted — for **whether** a step crosses a junction, which sets the
gap allowance (`MAX_CROSSING_M` 20 m against `MAX_JOIN_M` 5 m) — and never for its shape.

Three constants took several goes and are worth not re-deriving. The Bezier's handles are a
fraction of the **chord**, computed from the turn angle: off the chord alone (the 0.5523
rule of thumb) the curve pinches to 2.7 m where the geometry calls for 24; off the *trim*
it degenerates the other way on short lanes into a straight line with a 176° hook at each
end. A turn may eat `max(0.4·L, L − 1 m)` of a lane, because `junction-1` has chains of
5.8 m lanes between junctions and a plain fraction starves the third one. And curvature for
the speed profile is measured as **turn per metre**, not as a circumradius — the
circumradius reads a polyline's concentrated bend as if it were spread over the window, and
reported 5.4 m where the path really turned through 2.7 m.

**Every manoeuvre must leave room for the next one, and that is the whole of the second
round of this fix.** `_turn` and `_lane_change` each sized themselves against the lane in
front of them and never against what came after, so:

- a run of lane changes swept 9.60 m across a 7.11 m lane and left the −89° turn after it a
  **0.32 m** approach. The cubic built between a 0.20 m trim and a 4.20 m chord folded back
  on itself — an **82° cusp**
- a 14.58 m lane between two junctions had 9.7 m taken by the first turn, leaving the
  second a 1 m approach for a 90° turn

`route_polyline` now looks one step ahead and passes a `reserve`, estimated by
`_turn_reserve` from the two untouched centrelines. **The reserve yields before a route is
refused** — an earlier attempt raised `RouteError` instead and silently cost 50 of 813
routes, which arrive as "no drive exists" because `plan_route` builds the geometry itself.
The two trims are also **independent** now: tying them to the smaller only discards room on
the side that has it.

**A curve this module builds is checked where it is built.** `_turn_curve` halves the handle
while the curve's own worst vertex exceeds `MAX_CURVE_TURN_DEG` (20°, against a measured
4.1° worst across all 83 connectors off untouched lanes), ending at the chord. That is
separate from `MAX_VERTEX_TURN_DEG` (150°), which is for lane-to-lane joins whose shape is
not ours.

**`COINCIDENT_M` is 1e-3 m, not 1e-6.** Trimming a lane at a length computed from its own
vertices lands 0.000078 m off the endpoint; at 1e-6 those survived as segments of their own,
and `atan2` over 78 µm returns noise that reads as an exact 90° turn. That was the worst
vertex in **390 of 813** swept routes.

**The drive also has a speed profile now.** `speed_profile` caps the speed at every point by
the curvature there (`LATERAL_ACCEL_MPS2`), then bounds how fast it may change
(`ACCEL_MPS2`, `BRAKE_MPS2`), so the car brakes *before* a junction rather than at it.
`Route.duration_s` is therefore not `distance / speed`, and `metadata.sdc_route` reports
`slowest_kph` beside `speed_kph` so a reader who divides one by the other can see why.

**Those three constants are pinned to the 30°-per-step gate, not to a comfort figure**, and
that is the second round of this work: at the original 1.8 / 1.2 / 2.0 the car crawled — 25.0
km/h averaged over 120 real `junction-1` routes on roads posted at 50, a 9 m junction turn
taken at 15 km/h, 11.6 s to reach 50 km/h and braking begun 37 m out. They are **8.5 / 5.0 /
6.0**, which gives 41.5 km/h over the same 120 routes with a worst step of 29.6° and nothing
over the gate. 9.0 puts one step over; 12.0 puts 22 over. The gate is the real ceiling because
degrees per step scale with speed while the geometry underneath does not — so do not raise
these to buy pace without re-running the sweep.

**The posted speed limit is a harder ceiling than the profile**, and it is worth knowing before
promising anyone a faster drive: a car obeying a 50 km/h road cannot average more than 50
however the profile is tuned, so on `junction-1` the whole tuning range is 1.0× to at most
1.68×. `convert --speed-kph` overrides the limit and is the only way past it — a per-dataset
decision, so an argument and not a config field, for the same fingerprint reason as `--routes`.

**What is left of the slowing down is lane changes, not junctions.** Measured across the same
sample: routes with a lane change have a tightest drive-line radius of 1.38 m median (68 of 91
under 2 m) and crawl to 10.0 km/h median; routes without measure 5.61 m and 17.9 km/h.
`_lane_change` fits the crossing inside the *one* pair of lanes it moves between, and this map
has 5.8–7 m lanes, so a 3.5 m shift becomes a sub-2 m S. On the `test` route those two kinks —
0.63 m and 0.61 m of radius — are the only reason it is not at 50 km/h for the whole drive:
78.9% of the distance already is, and the slowing costs 6.6 s of 64.8.

Two more constants are tied to each other and to the track. **`PROFILE_SAMPLE_M` must be at
least as fine as the track it decides the speed for** — at 0.25 m against a track sampling
0.1 m, curvature is measured over a longer window than the car meets, and the drive exceeds
the very lateral limit the profile was computed to keep. And **`MIN_SPEED_MPS` is 1.0, not
2.0** — originally because a lane change across a 7.11 m lane is an S of about 2 m of radius
and 2 m/s through it broke the 1.8 cap of the day; still 1.0 because the sharpest kinks measure
0.36 m of radius, where 8.5 allows only 1.75 m/s and a floor of 2.0 would override it there.

**But `PROFILE_SAMPLE_M` must not follow `--step-hz`, and that was measured before it was
decided.** The rule above was derived at 10 Hz, where "as fine as the track" and "fine enough
to read the road's own 0.25 m junction arcs" happened to coincide at 0.1 m. At 100 Hz they stop
coinciding, and the literal reading is the worse half. On `junction-1`'s 403.7 m `test` route:

| profile spacing | step | samples | duration | mean | peak `turn/dt·v` |
|---|---|---|---|---|---|
| 0.1 m | 0.1 s (today) | 370 | 36.9 s | 39.3 km/h | 10.5 m/s² |
| 0.1 m | **0.01 s** | 3695 | 36.9 s | 39.3 km/h | 43.7 m/s² |
| 0.01 m | 0.1 s | 556 | 55.6 s | 26.2 km/h | 3.2 m/s² |
| 0.01 m | 0.01 s | 5558 | 55.6 s | 26.2 km/h | 23.0 m/s² |

Left at 0.1 m the rate gives exactly what it should — identical duration and speed, ten times
the samples. Scaled with the rate it costs **a third of the pace** and still does not fix the
artefact it exists for (43.7 → 23.0 m/s², still 2.7× the 8.5 cap), because densifying below the
source geometry makes the estimator read `radius = span/turn` over a shorter span than the road
really turns through. The coupling was tried and **rejected**; do not "fix" it later.

`tools/check_dataset.py` reports the worst heading change over a **fixed 0.1 s window** of the
ego track and **fails above 30°**, and reports the tightest radius the drive line turns through,
which the
heading rule hides — at walking pace a car can turn through anything. `sanity_check` checks
shapes and lengths and never asks whether the drive is drivable.

**Counting refusals is not the same as counting faults**, and confusing the two is how a
half-finished version of this shipped. A sweep of 3,000 lane pairs reported "813 built, 0
refused" while **440 of those 813** carried a vertex over 30°.
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` now asserts both: no vertex
over 30°, *and* that the built count holds — a fix that reaches zero by refusing routes the
map permits is not one.

### The recorded car stops at red lights, because nothing else can make it

`ReplayEgoCarPolicy` — the default in `tools/drive.py` — sets position directly every step,
and MetaDrive's light is a collision wall, so a replayed car goes through a red however
correct the tape is. Waiting has to be **in the recorded positions**.

`ego_route.resolve_waits` projects each signalled lane's stop point onto the drive, sets the
car back `STOP_LINE_SETBACK_M` (5 m, where MetaDrive's 0.25 m wall stands), and resolves the
lights **front to back** — each wait moves every arrival after it, so a car held 13 s at the
first light meets the second one 13 s later into the cycle.

**Each light is read twice, and the order cannot be collapsed.** First from the arrival the
car would have *without* stopping — that is what a driver sees on the approach and what
decides whether to brake. Then, if it was red, again from the *braked* arrival, because
slowing takes time and the light may have changed during it. Deciding from the braked
arrival alone oscillates: the stop delays the car into a green, the green removes the stop,
and the arrival moves back into the red.

Four things that bite:

- **A baked wait matches `--lights tape` and is wrong under `--lights live`**, which redraws
  the offset per episode. `tools/drive.py` warns when it replays a track with baked stops
  under live lights, and `metadata.sdc_route.stops` records what the waits were computed
  against. For training, `--agent-policy idm` with `--lights live` is still the answer.
- **`tools/drive.py` must not bound the episode by the recording's length** for a policy
  that drives itself. A car that stops needs more steps than a recording of a drive that
  never stopped. MetaDrive does not impose this — `horizon` is 100000 and `ScenarioEnv`'s
  `allowed_more_steps` defaults to `None` — so the budget is the recording plus the longest
  red in the plan.
- **A stationary car still faces the way it was going.** `atan2(0, 0)` is due east, so a
  naive heading array would swing the car east while it waited and back when it moved off —
  the same spin as the marker bug, from a different cause. `_headings` carries the last real
  heading across any step too short to have a direction.
- **`signal_plan` no longer imports `TIME_STEP_S` from `ego_route`** — it takes the step as
  an argument — because `ego_route` now needs `colour_at` and `seconds_until_green`, and the
  clock stays in one module rather than being written a fourth time.

`route_summary` reports `stops`, `waiting_s` and `driving_duration_s` beside `duration_s`.
`driving_duration_s` is the drive with the standing still removed, **not** the drive with
every light green: the car still brakes for the red and pulls away from it.

### Traffic lights, and why the timing cannot come from OSM

**MetaDrive has no traffic-light controller.** `ScenarioLightManager.after_step` does
one thing — index `state["object_state"]` by `episode_step` and call `set_status` —
and it is the only light manager in 0.4.3; procedurally generated maps carry no
lights at all. A light in a dataset is therefore a **tape**: a colour spelled out for
every step — 0.1 s of them by default, `1 / --step-hz` otherwise, which is why a tape
may only be replayed at the rate it was baked at.

**OSM supplies presence, never timing.** `highway=traffic_signals` carries no cycle,
no split and no offset, so every number is chosen by a person in
`inspection/stage-6-signal-builder.html` and the dataset marks the plan
`synthesised` in `metadata.signals`. `junction-1` has exactly **one** signal node,
`1927184932`, and it is at the edge of the extract — node 0 of way `1173001826`, in
no other way, 0 connectors — so Stage 2 bound it to the three lanes it *releases*.
The page draws it and never selects it.

```bash
# 1. place lights: open inspection/stage-6-signal-builder.html, add a phase group,
#    click the lanes it stops, set green/amber/start, download signals.json
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json \
  --signals workspaces/junction-1/signals/signals.json
```

Four things that bite:

- **Signal timing must never go in `config/default.yaml`.** `configuration_checksum`
  is an input to `generation_fingerprint` (`generation.py:2212`), so a field on
  `ConverterConfig` invalidates the lane-model review at the next `generate-map`.
  Same for the lane model schema. Timing is a `convert`-time file, like `--routes`.
- **`stop_point` sits at the top level of a light entry, not inside `state`.**
  Everything in `state` is length-checked against the scenario length, and
  `_get_episode_light_data` reads an in-`state` position as the old Waymo `[T, 2]`
  format. Getting this wrong fails only on scenarios that are not 3 steps long.
- **A wrong lane id is silent.** `skip_missing_light` defaults to **True**, so a
  light keyed on something that is not a map feature is dropped with a log line and
  no light. `tools/check_dataset.py` checks every key resolves; nothing else does.
- **A baked tape is the same on every episode**, so an agent learns the step number
  rather than the light. `tools/signal_control.py` drives the same lights from
  `metadata.signals` and one offset drawn per episode — one offset for the *whole*
  plan, because the gaps between groups are what keeps crossing arms apart.
  `tools/drive.py --lights live` runs it; `--lights tape` is the portable default a
  stock ScenarioNet consumer sees.

**The clock is written three times** — `signal_plan.colour_at` for the tape,
`web/src/signal/phase.ts` for the page, `tools/signal_control.py` for the live
manager — because they run on three different interpreters. The offset means **when
green starts**. `tests/unit/test_signal_plan.py` and `web/test/signal/phase.test.ts`
assert the same numbers on purpose.

**To see the ego stop at a red light you must leave replay.**
`--agent-policy idm` selects `TrajectoryIDMPolicy`, which MetaDrive supports for the
ego (`agent_manager.py:49` hands it `current_sdc_route`) and which inherits
`IDMPolicy`'s light check. `ReplayEgoCarPolicy` sets position directly and drives
through anything. Measured on `junction-1` with a light on the route: the ego stops
**5.7 m short** of the red and moves off when it goes green.

**It used to end early with `out_of_road`, and that was ours rather than MetaDrive's**
(fixed 2026-08-28). Measured at 4.26 m lateral against `max_lateral_dist=4`, and again
at 4.14 m on a later model, 114 of 960 steps. The cause was not the lateral controller:
`--agent-policy idm` was handed MetaDrive's **stock** `TrajectoryIDMPolicy`, whose
`target_speed` is written once in `__init__` to a flat 40 km/h and never again — while
23% of `junction-1`'s own drive line allows less than that on curvature alone, and every
one of the three fixes that already existed for traffic (the speed profile, the windowed
reference, the zero-integral heading PID) lived inside `tools/traffic.py` where the ego
could not reach them. It now drives `windowed_policy_class()` and is paced by
`drive._EgoPace` off its own route: **`arrive_dest`, 1044 of 1116 steps, completion
0.950** on `junction-1`, and 440 of 474 on `mosque`. The whole account, both maps and
the traffic half of it, is in `docs/reference/live-traffic.md`.

**A car that paces itself needs a bigger step budget than the recording has**, and that
is not slack: the tape is built at `LATERAL_ACCEL_MPS2` 8.5, which works because a
replayed car's positions are set directly and nothing has to steer to them, while
anything that steers gets 4.0. `budget` for `idm` is now `_EgoPace.duration_s /
IDM_TRACKING_RATIO` rather than the recording's length — see `drive.IDM_TRACKING_RATIO`
for why a regulator with no integral sits 9% under its own target.
