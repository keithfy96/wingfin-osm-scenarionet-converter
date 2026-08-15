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

### Running the whole pipeline

Two scripts cover the six stages, split where Stage 3 stops for a human. The workspace
is set once in `.env` (gitignored; copy `.env.example`) and can be overridden per run by
a positional argument.

```bash
./scripts/run-stages-1-3.sh               # fetch -> generate-map -> inspect --view review
./scripts/run-stages-1-3.sh --skip-fetch  # when source/map.osm has not changed
./scripts/run-stages-4-6.sh mosque        # apply-review -> validate-map -> convert
```

Stage 6 runs **without** `--routes` and `--signals`, so the dataset is map-only; routes
and signal timing are drawn by hand afterwards in the pages that run writes.

**`--skip-fetch` is not only a time saver.** Stage 1 rebuilds
`normalized/road-network-local.graphml`, osmnx stamps a build timestamp into GraphML, and
that file's checksum is an input to the Stage 2 `generation_fingerprint` — so a full
1→3 run mints a **new fingerprint even when `source/map.osm` is byte-identical**, and the
existing `review.json` stops applying. Measured: two Stage 1 runs over one unchanged
`map.osm` produced two different graphml checksums. Skip Stage 1 unless the OSM moved.

A code change in `src/osm_scenario/` does **not** move the fingerprint on its own — only
`GENERATOR_VERSION`, `configuration_checksum`, the source OSM and that graphml do. So a
generator fix plus `--skip-fetch` keeps a review usable; bumping `GENERATOR_VERSION` does
not.

The scripts refuse a workspace whose `source.type` is not `local_file`: re-running `fetch`
on a `place` or `bbox` workspace re-downloads and **overwrites `source/map.osm`**, taking
hand edits with it. For a local file already sitting in `<ws>/source/`, `fetch` uses it in
place (`acquisition.py:79`) and writes nothing to it.

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

Two things about markings are MetaDrive's and not ours, so do not go looking for them in
`conversion.py`. **The 3D markings are a raster, not geometry**: `_construct_lane_line_segment`
builds only a collision ghost, and what the eye sees is `BaseMap.get_semantic_map` painting
every line with `cv2.polylines` at a hard-coded `white_line_thickness=2` **pixels**, so a line
is `2 / pixels_per_meter` metres wide — 0.091 m at MetaDrive's own 22 px/m, 0.125 m at the 16
`tools/drive.py` can fit on a 1024 m region. And **the white hairline round every road edge is
drawn by nothing**: `terrain.frag.glsl` paints by value band (ground 0, lines 10, road 20) and
`semantic_tex` is created with no filter, so the linear blend from road to grass passes through
5–16 and the shader calls it white. Keith looked at both and chose to leave them.

### A junction is not painted, and the lanes inside it are why

`_map_features` writes boundary features for `model.lanes` only, so a `ConnectorFeature` — a
junction turn — is a `LANE_SURFACE_STREET` polygon with no lines. A junction should therefore
be bare road, and mostly is.

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
prints it.

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

### A restriction has to be known before the lanes are dealt out, not after (v21)

Node-via restrictions used to be read only at the end, over a candidate list that was already
final — and **both** rules that decide where a lane lands had by then counted a destination
that was about to be deleted. On mosque `859423756`, where rel 18555950 forbids the straight-on
and every vehicle must therefore turn right, that cost two of three lanes their only exit:

- `_balanced_approach_assignment` counted 3 lanes arriving against **6** lanes of destination,
  did not close, and stood aside. Discount the forbidden destination and it is 3 against 3.
- `_side_filtered_candidates` struck the right turn from idx1 and idx2 as offside-only, and its
  no-stranding catch did not fire because `kept` was not empty — each lane still held the
  straight-on, **which was there only because the filter deliberately keeps a movement a
  restriction forbids so the restriction has something to act on**. The lane was judged to have
  somewhere to go on the strength of a movement that existed in order to be deleted.

`_restricted_groups` now hides those destinations from the two balanced rules, and the catch no
longer counts a restriction-forbidden candidate as an exit. Four things not to re-derive:

- **Only the allocation is blinded.** The movements are still generated, still forbidden, and
  keep their ids. A restriction that deletes nothing leaves nothing on the map explaining why
  the turn is missing.
- **Nothing is hidden where that leaves the approach no destination at all.** With no survivors
  there is no split to protect, and blinding the allocation only collapses the forbidden
  movements onto one lane and moves their ids. junction-1 rel 16740674 is that case.
- **`blocks_by_group`, the feeder list `_merge_side` compares, is deliberately not filtered.**
  Tried and measured: no active connector changed, and the forbidden ids of **seven** relations
  moved across both workspaces.
- **`non_reverse_groups` / `_is_decision_node` must never be filtered either.** A forbidden
  movement is still a movement geometrically; dropping it from that count can take a node below
  the decision-node threshold, at which point the movement becomes a *continuation* — and a
  restriction cannot act on a continuation.

See `docs/mapping-algo-changes/2026-08-13-04:42:58-lanes-were-dealt-across-a-destination-a-restriction-forbids.md`.

### An off-ramp before the junction means the junction does not carry that turn (v22)

Nothing in the generator ever *asserts* a turn. At a decision node every non-reverse outgoing
group is reachable from the approach and only evidence removes a movement — so a turn nobody
may make, with no `turn:lanes` and no restriction naming it, is generated and never
questioned. Neither extract holds a single `left` in any `turn:lanes` value: **every left turn
on both maps exists purely because two ways share an OSM node.**

The evidence that had never been read is the slip road. `_link_bypass_way` names the `_link`
way that already carries a movement, and the status becomes `forbidden` with a **warning**,
`movement_served_by_link_bypass`, recording what went and what took it. Keith:
*"these two are wrong because there is an offramp before it."*

Both ends have to match — the ramp leaves the node the approach's own **edge starts at**, and
comes out at the node the destination's **edge ends at** — and the movement must carry a side.
All three guards were measured, and each is a reading that was tried and is wrong:

- match the ramp's end against **any node of the destination way** and mosque reads **22**
  connectors bypassed against the tight test's 5, six of them carriageways carrying straight on
  at +2.45° and +5.07°. A ramp replaces a turn, never a road going ahead — hence the side test.
- keep only the **chain's final node** and the Kenanga case vanishes. `182502392` comes out at
  `1928630157` and a *different* ramp, `182502409`, starts there; walking through reads
  `1928630009`. Nothing distinguishes one ramp mapped as two ways from two ramps in series, so
  **every way boundary along the chain is recorded**.
- a ramp says a turn is taken elsewhere, **never that a lane has no exit**, so a movement that
  is the lane's last one stays. Read after the restrictions resolve — a restriction may have
  taken the exit that would otherwise have counted.

Read **before** the lanes are dealt out, in `blocked_groups` beside `_restricted_groups`, with
the same carve-outs and for the same reasons as v21.

Three ramps in the two extracts, three duplicated turns, and the third — `191861354` at node
`474922037` — was **already forbidden by a surveyed restriction**, which is the corroboration
for reading the shape as evidence rather than a guess. Two consequences worth not
re-discovering: a Perdana car no longer drives the short block **between** the junction and
where the ramp merges (that is what a slip means — it joins beyond it), and Kenanga
`5fe50f735e40d7c2` is now starved because its only other feed, `7046b111f705c203`, is an open
`review_required` blocker. See
`docs/mapping-algo-changes/2026-08-13-16:32:06-a-turn-an-off-ramp-already-carries-was-offered-twice.md`.

### A merging road must not cross the lane it is joining (v23)

**A joining way's last edges aim at the junction node, and that node sits inside the
carriageway** — on the other way's centreline, which on a three-lane road *is* the middle lane.
So the road converges on the lane it merges into, **overshoots it**, and the merge taper hauls
the last lane back out. That is the turning *in* before turning *out* Keith reported, and why a
ramp's ribbon lands on the lane beside the one it enters. Measured: 1.21 m, 1.40 m and 1.52 m of
overshoot on the three merges he named.

**The overshoot is usually not in the lane the merge owns.** On the `182502409` ramp it is
`15438e6fd90cf39e`, an ordinary lane, which is why three separate attempts confined to
`72fdbea2a86f51e8` could not fix it. `_uncrossed_lanes` walks back through single continuations
and pulls every vertex on the wrong side **perpendicular onto the line**, keeping its distance
along it. Five things not to re-derive:

- **The correction is a sideways pull, never a bend.** Drawing it as a cubic tangent to the road
  behind and to the lane ahead bowed every lane it touched in `junction-1` — all dead straight
  before — by up to **2.31 m**, and pushed the ramp's ribbon on the middle lane from 14.7 to
  **22.1 m²**, making the reported defect worse. Keith reverted it on sight.
- **Stopping the lane short and letting the junction band cover the gap was also tried and
  reverted**: it opens a hole at **26** `mosque` merges that were seamless. A merge may never
  part a join.
- **A road past the line further back than `merge_taper_length_m` is left alone entirely.** That
  is two carriageways of different widths mapped as separate ways: `mosque` way `935525163` runs
  1.75 m off — half a lane — for **115.6 m** across four merges. Pulling a 70 m lane sideways is
  not a merge correction, and the four are excluded by name in the workspace-backed test.
- **Only a single continuation counts.** `entry_lanes` / `exit_lanes` name a lane for a direct
  continuation and a connector for a junction movement; a fork has no one road behind it, and a
  junction movement is another lane's traffic rather than this road carrying on.
- **The pull runs before `_tapered_line`**, so the taper's move is along the lane rather than
  back out across it. `_tapered_line` itself is unchanged, and `topology.py` is not involved.

10 lanes move on `mosque` and 8 on `junction-1`; three on each are lanes the merge code did not
previously own. See
`docs/mapping-algo-changes/2026-08-15-19:15:13-a-merging-road-crossed-the-lane-it-was-joining.md`.

### Starved middle lanes: mostly fixed, one left

Two allocation rules now run before the proportional mapping, and between them they
cover both shapes where the lane arithmetic closes:

- `_balanced_approach_assignment` — **one** approach across **several** destinations
  (a lane peeling off cannot also be the straight-on lane). Added in v10.
- `_balanced_merge_assignment` — **several** approaches into **one** destination
  (a merging link must not land on a lane the main road already feeds). Added in v11.

Both count only the destinations a node-via restriction *leaves* (v21) — see "A restriction has
to be known before the lanes are dealt out" above. Counting a road nobody may take turns a clean
three-into-three into an ambiguous three-into-six and neither rule fires.

Where the counts do **not** close, `_merge_side` (v20) still says which *side* of the
destination an approach lands on: a road joins another from one side and has to land on
that side, or its traffic crosses the traffic it is merging with. It ranks the approaches
with `_kerb_first_key` rather than reading an angle, because **which side a road joins
from is a comparison between the roads meeting at the node, not a property of one of
them** — and it has an answer at any angle, including zero.
`side_movement_min_degrees` (10°) asks "is this a turn?", which is the wrong question for
a merge: mosque `935525161` joins at **−0.01°** and was called sideless, so
`_mapped_lane_index`'s `min(lane_index, count−1)` sent a kerbside single lane to index 0
and across three lanes of link traffic. See
`docs/mapping-algo-changes/2026-08-13-01:52:14-a-road-that-merges-dead-straight-is-given-no-side.md`.

The order of authority where a side is decided is **`turn:lanes` → `_merge_side` →
`movement_side`**. The tag stays on top; a surveyed value is still evidence and the
ranking is still an inference. Where the merge side decides, the block dealt inward by
`_side_block_offset` is the *whole* approach — nothing tagged it, so all of it merges
together.

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
