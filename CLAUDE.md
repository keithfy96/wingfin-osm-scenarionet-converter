# CLAUDE.md

Instructions for Claude Code working in this repo. Sections A and B are standing
instructions — follow them every run, without being reminded.

---

## A. Junction and lane diagrams must be genuinely visual

Whenever you explain lane topology at a node — in a plan, in an answer, or in a doc
— **draw it as a plan-view diagram** in the format shown below. Do not substitute a
table of IDs, a bullet list of mappings, or prose. The reader must be able to see
what is wrong without holding four lane indices in their head or cross-referencing
anything.

**Every diagram must carry, inline:**

- travel direction, and the kerb and centreline edges drawn and labelled
- the index convention in the header — indices run **centre-out**, `idx0` hugs the
  centreline (offside), `idx(n−1)` is kerbside (nearside) — and the angle sign
  convention, `+` = left turn, `−` = right turn
- **every destination lane as its own channel**, labelled `idxN/M`, its lane ID,
  nearside / middle / offside, and its **feed count**
- **every approach labelled where it is drawn** — way ID, `idxN/M`, turn angle, and
  any `turn:lanes` tag or ramp/link role
- shared feeds drawn as a visible merge into one lane
- **starved lanes called out inside the drawing**, not in prose underneath
- no "see the table above", no bare hex IDs the reader has to look up

Re-derive every ID, index, angle and count from the generated model by script
before drawing. Never copy figures by hand from an earlier message.

Both reference cases below show the **pre-v11 model**. The defect they illustrate was
fixed in `direct-osm-stage2-v11` (`_balanced_merge_assignment`), so neither node looks
like this any more — see
`docs/mapping-algo-changes/2026-08-07-12:34:23-merging-approaches-starve-the-middle-lane.md`
for the after view. They are kept because they remain the worked examples of the
required format.

### Reference case 1 — node 13946726034 (pre-v11)

```
 node 13946726034            + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside

   APPROACHES                       ┊  DESTINATION — way 776370584, 3 lanes
   (arriving at the node)           ┊  (the node's only outgoing group)
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   776021087  idx1/2   −0.03° ───────────────►  idx2/3  e6db35d27f  nearside
                                    ┊                                 1 feed
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                    ┊         idx1/3  37238b17cc  MIDDLE
                                    ┊                              0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021087  idx0/2   −0.03° ──┐   ┊
                                ├─────────────►  idx0/3  ba662c1bbc  offside
   1530245742 idx0/1   −19.4° ──┘   ┊                       2 feeds — SHARED
     link · turn:lanes:forward=right ┊
 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ───────────────────────────────────────────────────────►
```

### Reference case 2 — node 1928630009 (pre-v11)

```
 node 1928630009             + = left turn · − = right turn · left-hand traffic
 same defect, mirrored: the joining road takes the kerbside lane instead

   APPROACHES                       ┊  DESTINATION — way 776021091, 3 lanes
   (arriving at the node)           ┊  (the node's only outgoing group)
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   182502409  idx0/1   +14.8° ──┐   ┊
     ramp, no turn:lanes        ├─────────────►  idx2/3  b63366201b  nearside
   776021086  idx1/2   +0.2°  ──┘   ┊                       2 feeds — SHARED
                                    ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                    ┊         idx1/3  eef18fbc84  MIDDLE
                                    ┊                              0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021086  idx0/2   +0.2°  ───────────────►  idx0/3  a566b487c1  offside
                                    ┊                                 1 feed

 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ───────────────────────────────────────────────────────►
```

Both cases were the **same defect**, mirrored, and both are now fixed.

---

## B. Log every corrected mapping-algorithm mistake — automatically

When a mapping-algorithm mistake is corrected, write an entry in
`docs/mapping-algo-changes/` **without being asked**. The folder is a countable
record of real corrections; it must not fill up with session notes.

Filename: `YYYY-MM-DD-HH:MM:SS-<algo-change-desc>.md` (get the timestamp from
`date +"%Y-%m-%d-%H:%M:%S"`, do not invent one).

**Write an entry only when all three hold:**

1. **Keith identified the mistake** — he pointed at a lane, connector or mapping
   that is wrong. Not something you noticed and decided to change.
2. **The fix changed algorithm code** in `src/osm_scenario/` — generation,
   topology, lane mapping. Not docs, config, or tests.
3. **The fix was verified** — workspace regenerated, before/after counts compared,
   no existing connector regressed, `uv run pytest` passes.

**Do not write an entry for:** investigations that ended without a code fix;
doc-, config- or test-only changes; refactors Keith did not flag; attempts that
were reverted; or anything where you are unsure. When unsure, ask — do not create
the file speculatively.

Required headings, so entries stay comparable: **Symptom**, **Fundamental cause**,
**Fix**, **Verification**. The fundamental cause is the point of the record — say
*why the algorithm produced the wrong result*, not merely which line changed. Full
template in `docs/mapping-algo-changes/README.md`.

---

## C. Repo facts you cannot get from reading the source

`README.md` and `guide/project-guide.md` cover Stage 1 only. The Stage 2 generator
is `src/osm_scenario/generation.py`; geometry and movement classification live in
`src/osm_scenario/topology.py`.

### Commands

```bash
uv run osm-scenario generate-map -w workspaces/junction-1 --config config/default.yaml
uv run pytest
uv run ruff check
```

The `-w` is required and easy to miss. `ruff format --check` fails on 8
pre-existing files and is **not** a gate — do not mass-reformat to satisfy it.

### Workspaces

`workspaces/` is gitignored, so its contents never appear in `git status` — run
`ls workspaces/` rather than assuming. `junction-1` is the working one.
Generation refuses to run when `source/map.osm` drifts from the sha256 in
`source/manifest.json`; Keith hand-edits the OSM mid-session, so re-check rather
than trusting a number from earlier in the conversation.

### Reference checkouts — read MetaDrive, do not guess at it

Both are on this machine. Neither is a dependency of this repo, and nothing in
`git status` will remind you they exist.

- `/home/keith/Desktop/work/wingfin/metadrive/` — MetaDrive **0.4.3**, the format
  this converter targets. When a question is "what does MetaDrive do with this
  field", the answer is in here, not in a recollection of the docs.
- `/home/keith/Desktop/work/wingfin/scenarionet/` — ScenarioNet: the dataset
  tooling, and the Waymo / nuPlan / nuScenes / Argoverse converters worth
  comparing our output against when a field's shape is in doubt.

`tests/unit/test_conversion.py` loads MetaDrive's real `ScenarioDescription`
straight from the first path (`METADRIVE_SRC`, near line 356) and is marked
`skipif` on the directory being absent — so a moved or renamed checkout **silently
drops the schema gate** rather than failing.

### Checking a converted dataset, and what it can and cannot do yet

`uv run pytest` cannot tell you the dataset loads. Both checkouts run **Python 3.8
/ numpy 1.24** and this repo runs 3.10 / numpy 2.2, so the interpreter the tests
use is exactly the one where a version fault is invisible. Run it from the other
side instead:

```bash
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/check_dataset.py workspaces/junction-1/scenarionet
```

`conversion.py` pickles arrays through `_PortablePickler` precisely because of
that gap — numpy 2 writes a reference to `numpy._core`, which numpy 1 does not
have and 3.8 can never get. **Anything that changes how arrays are written must
keep the stream free of version-specific module names**; two tests in
`test_conversion.py` pin it, one on the pickle stream and one on arrays still
arriving as arrays rather than lists.

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
  tools/drive.py workspaces/junction-1/scenarionet --render 3D
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
the curvature there (`LATERAL_ACCEL_MPS2`, 1.8 m/s²), then bounds how fast it may change, so
the car brakes *before* a junction rather than at it. `Route.duration_s` is therefore not
`distance / speed`, and `metadata.sdc_route` reports `slowest_kph` beside `speed_kph` so a
reader who divides one by the other can see why.

Two constants there are tied to each other and to the track. **`PROFILE_SAMPLE_M` must be at
least as fine as the track it decides the speed for** — at 0.25 m against a track sampling
0.1 m, curvature is measured over a longer window than the car meets, and the drive exceeds
the very lateral limit the profile was computed to keep. And **`MIN_SPEED_MPS` is 1.0, not
2.0**: a lane change across a 7.11 m lane is an S of about 2 m of radius, and 2 m/s through
2 m of radius is 2.0 m/s² against a 1.8 cap — the floor, not the geometry, broke the limit.

`tools/check_dataset.py` reports the worst per-step heading change in the ego track and
**fails above 30°**, and reports the tightest radius the drive line turns through, which the
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
every 0.1 s step.

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
**5.7 m short** of the red and moves off when it goes green. It also ends early with
`out_of_road` at 4.26 m lateral against `max_lateral_dist=4` — the IDM's lateral
controller losing the reference line, which says nothing about the data and does not
happen under replay.

### Why 3D needs its own runner

2D is fine from any entry point. 3D through `scenarionet.sim` shows roads that stop
and an ego that sinks into the ground and floats — **none of it a defect in the
converted data**, all of it MetaDrive terrain defaults meeting a map shaped like a
road network rather than like a Waymo clip. Three separate causes, each measured:

- **`height_scale` (default 50) is the sinking and the flying.** `use_mesh_terrain`
  is false by default, so the car drives on a flat collision plane at z≈0 while the
  *visible* ground is a noise heightfield around it. `tools/drive.py` measures it:
  at 50 the ground within 25 m of the drive reaches **+10.3 m** and 11% of it stands
  above the road; at 1 it reaches **+0.2 m** and 0% does. Only the surroundings move
  — the road is flattened either way. **0 is not allowed**: panda3d builds a singular
  transform and dies with `Tried to invert singular LMatrix4`.
- **The road-surface texture is often larger than the GPU accepts, and there is no
  config key for it.** MetaDrive builds it at `map_region_size × 22` px square —
  22528 at 1024, **45056 at 2048**. A GL context reports its own ceiling: 16384 on
  an Intel iGPU, 32768 on a discrete card. Past it the texture cannot be uploaded,
  and that is what "the roads stop" looks like. The 22 is hard-coded in
  `TerrainProperty.get_semantic_map_pixel_per_meter`; `tools/drive.py` replaces that
  classmethod at runtime — the only monkeypatch in the repo, and it rides the seam
  `base_env.py:335` already uses for `map_region_size`. Nothing in the MetaDrive
  checkout is edited.
- **`map_region_size` sizes the terrain square, and 2048 is the wrong blanket
  answer** — an earlier version of this file said to set it, which would demand a
  45056 px texture no GPU can hold. The square is `map_region_size` metres centred
  on the ego's start (`base_engine.py:386` hard-codes `center_p = [0, 0]`; the disk
  loader passes `centralize=True`, `scenario_data_manager.py:76`), and outside it
  there is no ground and no flattened road. So it must be *just* big enough:
  `tools/drive.py` measures each scenario and picks the smallest power of two that
  covers it, and `tools/check_dataset.py` reports the same number. `junction-1`'s
  `main-route` reaches 449 m from its start, so 1024 is enough; another start lane
  will not be.

Run `tools/drive.py --render offscreen` to check any of this without a display —
`--render none` builds no terrain at all (`Terrain.reset` guards the whole path on
`self.render or use_mesh_terrain`), so it checks the drive and not the view.

### Conventions that bite

- **OSM connectivity is via shared nodes.** Relations only carry turn restrictions.
  A missing connection is never a missing relation.
- **`direction: forward|backward`** is relative to OSM way node order, not to
  oncoming-ness. A "backward" lane is not necessarily oncoming traffic.
- **Lane indices run centre-out**: `idx0` = offside (against the centreline),
  `idx(n−1)` = nearside (kerbside). `driving_side` is `left`.
- **`signed_turn_angle` is CCW-positive**: `+` = left turn, `−` = right turn.
- **`entry_lanes` / `exit_lanes` hold a mix of ID kinds** — lane IDs for
  continuations (written at `generation.py:1147`) and connector IDs for junction
  movements (248 vs 74 in the current junction-1 model). Any lookup that assumes
  one kind fails silently on the other.

### `lanes=1` with no `oneway` is read as one-way, in Stage 1, and can be refused

A mapper who writes `lanes=1` and leaves `oneway` off almost always means a one-way slip,
and `_directional_lane_count` used to fall through to `max(1, total // 2)` and build a lane
**each way** — a road the source says is one lane wide coming out 7 m across, with a U-turn
at each end. `osm_source.single_lane_implies_oneway` now reads it. Every surveyed tag
switches it off: `oneway=no`, either `lanes:<direction>`, an existing `oneway`, a roundabout.

**The reading is applied in Stage 1, not Stage 2, and that placement is the point.**
`_apply_single_lane_oneway` drops the reverse edges inside `select_public_driving_graph`,
the one chokepoint `acquisition.py` and `apply_review.py` both pass through, so Stage 2 and
Stage 4 agree for free. `generation.py` never re-reads the tags to decide it — it asks the
graph, via `_single_direction_ways`, so the two stages cannot disagree.

But the graph is not the tags, and **the tags still say two-way**: Stage 1 must never write
to `source/map.osm`, which is acquisition evidence. So `_carries_whole_carriageway` takes
`one_way_in_graph` and every call site threads it. Skip that and the change is half done —
the surviving lane sits **1.75 m** off the road's centre, balancing against an oncoming block
that no longer exists, and its count is still reported as an inference.

**A refusal is a real outcome, not a failure.** Dropping the reverse direction of the only
route off a spur strands everyone on it, so a way is refused unless every node that could get
out before still can. Getting out means reaching the main network **or driving off the edge of
the map** — the first version of the guard had only the first half and refused a merge slip
whose far end continues out of the extract. Both anchors are pinned before anything is dropped:
the main network as one node of the largest strongly connected component, so it cannot shrink
under its own answer, and the map's edges as the nodes that already had no way on at all. A
cul-de-sac tip is not one of those, because its way out is the reverse direction in question.

Candidates are decided **one at a time against the graph the last one left** — two ways can each
be spare while the other is two-way and be the only way out together.
`manifest["road_selection"]["single_lane_oneway"]` records `applied` and `blocked`, and the
`lane_count_inference` **blocker** Stage 2 already raises on a `lanes=1` way is what carries a
refusal into Stage 3. No new finding rule was needed for it, and none was added.

### The standing principle: surveyed tags outrank inferred angles

`turn:lanes` is surveyed evidence of which movements are *permitted*. The movement
class is *inferred* by binning a turn angle against threshold constants. Where the
two disagree, **the tag must never be the reason a lane loses its only exit** —
that cuts the drivable network on the strength of a magic number. Already enforced
in `_side_filtered_candidates` and `_stranded_permission_fallback`; follow the same
rule anywhere else the two sources of truth meet.

### A turn restriction names a route; a connector is one step of one

A `no_*` relation forbids the sequence FROM → VIA → TO. A `ConnectorFeature` is
`from-lane → to-lane` at one node and remembers neither the road before it nor the road
after, so enforcing the relation means **deleting one step of the route — and that stops
everyone who uses that step**, not only the drivers on the prohibited route.

There are two candidates and each has its own test, and they look in opposite directions:

- delete the **last** step (VIA → TO) — exact only if **nothing else feeds VIA**
- delete the **first** step (FROM → VIA) — exact only if **VIA leads nowhere else**

`via_way_resolution` deleted the last one unconditionally until 2026-08-12, which on
`10421009` deleted Persiaran Meranti's own right turn — named by `turn:lanes=right|right`,
and not mentioned anywhere in the relation — and left way `39619063` with **no exit at
all**. See
`docs/mapping-algo-changes/2026-08-12-18:33:44-a-turn-restriction-deleted-the-wrong-movement.md`.

Three things not to re-derive:

- **The adjacency the test reads is an upper bound on purpose.** `topology.way_adjacency`
  counts per *way*, not per lane or per direction, so it can over-count what reaches a way
  and never under-count it. Over-counting sends the restriction to review; under-counting
  would delete a movement carrying legal traffic. Do not "tighten" it without moving the
  whole test to lane level.
- **The last step wins when both are exact.** Not taste — every restriction enforced before
  the change removed the last step, and re-deciding a settled one moves a forbidden
  connector id and costs the review decision attached to it.
- **When neither is exact, nothing is deducible.** A gyratory is the usual shape: each
  segment carries traffic from several entries by design. The movements come back as
  `review_required`, which is already excluded from the lane graph, so holding them does
  not make the prohibited route drivable. `RestrictionEffect.forbidden_connector_ids` stays
  empty there — it forbade nothing and must not claim to — and the held ids ride on the
  findings.

**Node-via restrictions are a different thing and are correct as they stand.** A node
restriction names from-way, via-node and to-way, which is exactly the triple a connector
encodes, so it cannot over-forbid. `mosque`'s gyratory movements stay forbidden through
this change because three node-via `no_right_turn` relations name them precisely.

`restriction_enforced_leg` is a **warning** carrying which step was removed and why the
other was rejected. Only blockers gate export, so it asks nothing; it exists because the
generator now chooses between two defensible enforcements.

### Starved middle lanes: mostly fixed, one left

Two allocation rules now run before the proportional mapping, and between them they
cover both shapes where the lane arithmetic closes:

- `_balanced_approach_assignment` — **one** approach across **several** destinations
  (a lane peeling off cannot also be the straight-on lane). Added in v10.
- `_balanced_merge_assignment` — **several** approaches into **one** destination
  (a merging link must not land on a lane the main road already feeds). Added in v11.

`_mapped_lane_index` (`generation.py`) is unchanged and still **cannot produce a
middle index**: for a 2-lane approach onto a 3-lane destination
`round(idx × (3−1) / (2−1))` gives `idx0→0`, `idx1→2`, and index 1 is unreachable for
*any* input. It now only decides **oversubscribed** approaches — where the counts do
not close, a lane genuinely serves more than one movement, and the ambiguity is
reported rather than resolved. Clean diverges and clean merges no longer go through it.

**Fixed in v17 — the last diagnosed starved lane.** `39619063` idx1/2 `c0530c25fd` at
node 1927184814 is now fed by `027a3ef89e3e7b88`.

Way `756118314` is tagged `turn:lanes=right|right`, so both its lanes carry
`turn_permissions=['right']`. An explicit `turn:lanes` value outranks geometry in
`movement_side()`, so **both** lanes are labelled `offside`, and
`side_lane_index("offside", 2)` returned `0` for both — they collided on one target.
The approach is oversubscribed (2 lanes arriving, 5 lanes of destination capacity at
the node), so neither balanced rule reaches it and `_mapped_lane_index` decides.

`_mapped_lane_index` now takes the block of lanes an explicit tag puts on that side and
deals them from the side inward, so a side says where a block **starts** rather than
where every lane in it goes. A block of one is unchanged, so only a genuine collision
moves. See
`docs/mapping-algo-changes/2026-08-09-16:44:44-a-side-picks-where-a-block-starts.md`.

Two blockers remain at that node, and correctly: `turn:lanes=right|right` names a right
turn that is not available there, and that disagreement is Keith's to judge. **Never fix
a tag-versus-geometry conflict by making the finding stop being raised** — fix the
mapping and keep the review.

Two cautions when re-measuring this. A previous version of this table also listed
`776021087` idx0/2 `8caffc7049` at node 13946726031; under the criterion "no connector
and no continuation names it as a target" that lane was **already fed at v9**, so it
was either counted under a different criterion or listed in error. And `junction-1`
still has **21** lanes fed by nothing; most are network-boundary lanes rather than
defects, and none of the remainder has been diagnosed.
