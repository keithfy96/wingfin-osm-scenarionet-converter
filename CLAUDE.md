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

**`workspaces/` is tracked, not gitignored.** The `.gitignore` line is commented out
(`.gitignore:9`) and **154 files under it are in git**, including the reviewed lane models,
`routes.json`, `signals.json` and the built `.pkl` datasets — so workspace files *do* appear
in `git status`, and `git add -A` will sweep up whatever Keith has open at the time. Only
`workspaces/*/reports/` is still ignored (`.gitignore:14`), and even there some files were
force-added and stay tracked. **Stage the files you touched by name.** Run `ls workspaces/`
rather than assuming which exist; `junction-1` is the working one.
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
  tools/check_dataset.py workspaces/junction-1/scenarionet-10hz
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
  config key for it.** MetaDrive builds it at `map_region_size × 22` px square — but
  **×11 at 4096** (`constants.py:499`), so 22528 at 1024 and **45056 at 4096**, not
  90112. A GL context reports its own ceiling, and **it is asked rather than assumed**,
  because on this machine it doubles between the two GPUs and the whole resolution
  follows from it: measured **16384** on the Intel iGPU and **32768** on the RTX 4050.
  Past it the texture cannot be uploaded, and that is what "the roads stop" looks like.
  `drive.py._max_texture_dimension` asks a throwaway subprocess — the ceiling is only
  knowable once a GL context exists, and by the time `env.engine.win` does, MetaDrive
  has already built the terrain from `get_semantic_map_pixel_per_meter`. The 22 itself
  is hard-coded in that classmethod; `tools/drive.py` replaces it at runtime, riding
  the seam `base_env.py:335` already uses for `map_region_size`. Nothing in the
  MetaDrive checkout is edited.
- **This machine is hybrid graphics, and which card renders is not a flag.** It is
  settled by the GLX loader before python starts, so it can only be two environment
  variables in front of the command — `__NV_PRIME_RENDER_OFFLOAD=1
  __GLX_VENDOR_LIBRARY_NAME=nvidia`, which is what `_common.sh:select_gpu` /
  `exec_with_gpu` set for all three of `drive.sh`, `sensor-survey.sh` and
  `step-timing.sh`, and why the switch lives in the shell rather than in `drive.py`.
  **Both are read by the GLX loader, so neither does anything in the container**,
  which loads panda3d's EGL display first and picks the card from the image's ICD
  manifest — `select_gpu` says so rather than claiming PRIME offload, and they are
  still set because the image keeps `pandagl` as the aux display for the 3D row.
  Nothing needs installing;
  panda3d 1.10.16 in MetaDrive's own venv picks the RTX up as it stands. **`--cuda`
  in MetaDrive's install docs is not this**: it toggles `image_on_cuda`, which keeps
  camera images in GPU memory for an RL pipeline, needs `pip install -e .[cuda]` plus
  Torch and CuPy (none installed here), and does not choose a renderer.
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
every line with `cv2.polylines` at a thickness given in **pixels** — `white_line_thickness=2`,
`yellow_line_thickness=3`, passed as literals at `terrain.py:625` over that function's own
defaults of 1 and 1, with no config key anywhere between. So a line is
`thickness / pixels_per_meter` metres wide, **and its real width moves with the size of the
map**. And **the white hairline round every road edge is drawn by nothing**:
`terrain.frag.glsl` paints by value band (ground 0, lines 10, road 20) and `semantic_tex` is
created with no filter, so the linear blend from road to grass passes through 5–16 and the
shader calls it white. Keith looked at the hairline and chose to leave it.

**The width is asked for in metres, and that is the point of the flag.** A fixed pixel count is
wrong in opposite directions on the two extracts — MetaDrive's 2 px is **0.5 m** on `mosque`'s
4096 m square at 4 px/m and **0.0625 m** on `junction-1`'s 1024 m square at 32, one far wider
than a road marking and the other far thinner. `drive.py --line-width-m` (default **0.15**,
about a real marking) works out the pixels from the resolution in force, and
`drive.py._set_line_width` is the repo's **second** monkeypatch — it must *override* rather than
re-default, because `terrain.py:625` passes both thicknesses explicitly. Three things not to
re-derive:

- **1 px is the floor**, so a big map cannot go as thin as a small one: `mosque` bottoms out at
  **0.125 m** even on the RTX, and the tool prints which happened rather than rounding quietly.
- **White and yellow get the same number.** `conversion.py` writes only `ROAD_EDGE_BOUNDARY` and
  `ROAD_LINE_BROKEN_SINGLE_WHITE`, so the yellow value is unreachable on our data today; giving
  it a different one would be an unexplained difference on the first map that has a yellow line.
- **It is not a config field, and must not become one.** `configuration_checksum` feeds
  `generation_fingerprint` (`generation.py:2212`), so a rendering preference in
  `config/default.yaml` would invalidate the lane-model review — the same reason signal timing
  is a `convert`-time file. It lives on the command line, with `LINE_WIDTH_M` in `.env` for
  anyone who does not want to type it.

### Driving the ego with something other than the tape — the gym contract (2026-08-17)

**The tick is the call.** MetaDrive is not a process ticking away with a queue or a listener
waiting for input. `env.step(action)` advances `physics_world_step_size` × `decision_repeat` —
0.1 s by default, 5 physics ticks of 0.02 s (`base_env.py:190, :462`), and whatever `--step-hz`
asks for otherwise — and returns; between two calls nothing in the simulator moves. So simulated
and wall-clock time are decoupled, and a policy taking 3 s freezes the simulator for 3 s and then
advances the same one step. The only place MetaDrive deliberately spends
wall-clock time is `ForceFPS.real_time_simulation`, which throttles the *display* and is off
headless.

**The action is two floats in [-1, 1]** — `[steering, throttle_brake]`, `[0] × max_steering`
degrees at the wheels, `[1] ≥ 0` engine force and `< 0` brakes (`base_vehicle.py:472-520`). It
reaches the car as `engine.external_actions`, read by `EnvInputPolicy` (`base_engine.py:425`,
`env_input_policy.py:27`), which is `BaseEnv`'s default (`base_env.py:55`) and which
`ScenarioEnv` does **not** override. So **passing an action *is* driving the ego** — there is
nothing to register and nothing to subclass. `tools/agent_env.make_env` therefore never sets
`agent_policy`, and `examples/drive_with_a_policy.py` is four lines.

**A keyboard drive and a model drive are the same code path.** `ManualControlPolicy` subclasses
`EnvInputPolicy` (`manual_control_policy.py:37`) and they differ only in where the two numbers
come from. That is why `--agent-policy manual` is a value on an enum rather than a module, and
why a recording made at the keyboard and one made through `env.step` come out the same shape.

**Start behind the socket with MetaDrive's IDM, not with a model, because it is the only thing
that tests the plumbing.** `agent_env.IdmDriver` builds `TrajectoryIDMPolicy` from *outside* the
agent manager — legal because `BasePolicy.engine` is a `get_engine()` property
(`base_policy.py:78`) — with the same three arguments `agent_manager.py:48-50` passes, and feeds
its `[steering, acc]` to `env.step`. `drive.py --agent-policy idm` runs that same class *inside*
the engine, where the action is ignored, so the two must agree. Measured: `junction-1` 291 steps
/ completion 0.774 and `mosque` 1180 / 0.723 both ways, with the recorded arrays **bit-identical**.
It reads the engine rather than `obs` (`idm_policy.py:239-297`), so its `obs` argument is
accepted and ignored purely to keep the signature a model will use.

Four things bite, and each reads as a driving problem rather than a plumbing one:

- **`action_check` is off by default** (`base_env.py:69`) and `EnvInputPolicy` simply clips
  (`env_input_policy.py:36`). Output in **[0, 1]** and the car **cannot brake at all**, because
  `_apply_throttle_brake` only brakes below zero. Output far outside and every step saturates, so
  steering is a switch at full lock. **`NaN` passes through unclipped** — `min(max(nan, -1), 1)`
  is `nan` in Python — and reaches `setSteeringValue`. Pass `action_check=True` while developing;
  it is a config key, not code.
- **A discrete action space is also a config key** — `discrete_action` gives `Discrete(25)`,
  `use_multi_discrete` gives `MultiDiscrete([5, 5])` (`env_input_policy.py:52-69`).
- **`max_lateral_dist` (4 m) ends the episode** measured from the *recorded* route, so a model
  that has not learned to steer is cut off within a second or two. MetaDrive's rule, not ours.
- **`routes.json` still chooses the goal.** `ScenarioMapManager.reset` calls `get_sdc_track()`
  unconditionally and `TrajectoryNavigation`'s reference line *is* the tape. An agent-driven ego
  makes the tape the goal rather than the drive; it does not remove the need for one.

**`current_action` is stale under any policy whose `act` returns `None`.** `before_step` appends
to `last_current_action` only when it got an action (`base_vehicle.py:225-226`), so a recording
made under `--agent-policy replay` is a column of `[0, 0]` — 352 of them on `junction-1`,
measured — written rather than refused, and reported as such by `ActionRecorder.all_zero`. The
same is true of `WaypointPolicy`. **And the pair is the observation from before the step**, with
the action executed during it; the returned observation is off by one.

**There is a second socket if the model predicts a path rather than pedals.**
`ScenarioWaypointEnv` / `WaypointPolicy` takes `{"position": (horizon, 2)}` in the ego's local
frame (`scenario_env.py:106-113, 442`). It costs the physics — `set_static=True` is asserted, so
the car is placed rather than driven and can go where a car could not — and it costs the
recorder, for the reason above. Reachable through `make_env(**overrides)`; not the default.

### The observation is not sensor data, and the lidar in it is blind (2026-08-18)

**`LidarStateObservation`'s 161 floats are an RL summary, not what a driver sees**, and reading
them as "the sensors" is the mistake this section exists to stop. Measured over `junction-1`'s
291-step `test` route, **39 of the 161 move**:

| indices | what | moves |
|---|---|---|
| `0:12` | side detector — 12 lasers, **static** world: the road edges | 12/12 |
| `12:17` | heading error, speed, steering, last throttle, last steering | 5/5 |
| `17:18` | yaw rate | 1/1 |
| `18:19` | lateral offset in lane | 1/1 |
| `19:41` | navigation — next 10 route points (ahead, sideways), car frame, clipped 30 m | 20/22 |
| `41:161` | ray lidar — 120 lasers, 50 m | **0/120, all exactly 1.0** |

**The lidar block is blind because `Lidar.perceive` scans `physics_world.dynamic_world`** and our
scenarios hold one car. Not a misconfiguration, and it fixes itself at stage 8 — which is why
`tools/sensor_survey.py` measures it every run instead of anyone quoting this table. The road is
seen by the *side detector*; the route is the navigation block. And the observation is **[0, 1]**
while the action is **[-1, 1]**, so a model matching output range to input range cannot brake.

**All four modalities Keith asked for exist.** `RGBCamera` / `DepthCamera` / `SemanticCamera` at
`(180, 320, 3|1)`; `PointCloudLidar(200, 64, ego_centric=True)` at `(64, 200, 3)` — a real 3-D
cloud, unlike the ray ring; IMU assembled from the bullet body (`body.get_linear_velocity()`,
`body.getAngularVelocity()`, `roll`/`pitch`/`heading_theta`, acceleration differenced over the
step — 0.1 s by default, 0.01 s under `--step-hz 100`, which is the whole point of that flag)
because **MetaDrive has no IMU sensor class**; and GPS below. Four traps:

- **A camera cannot be read without `image_observation=True`.** `base_env.py:343` deletes every
  `BaseCamera` from the sensor list when neither `use_render` nor `image_observation` is set, and
  `_render_mode` is then decided by whether any camera survived (`:385-390`). So the two are
  welded together offscreen.
- **And turning it on replaces the observation.** `ImageStateObservation.observe` returns
  `{"image": ..., "state": ...}` where `state` is the **41-number** `StateObservation` with *no
  lidar block at all* (`image_obs.py:40`). A model trained on the 161-vector is handed something
  else. `sensor_survey.py` builds `LidarStateObservation(env.config)` itself and calls
  `.observe(env.agent)` rather than taking `env.step`'s return — legal from outside because
  `BaseObservation` reaches the engine through `get_engine()`, the same seam `IdmDriver` uses.
- **A partial `sensors=` override wipes `rgb_camera` and kills the env at construction** with
  `KeyError: 'rgb_camera' does not exist in existing config` from `image_obs.py:68`, because
  `image_source` defaults to that name. `agent_env.make_env` merges rather than assigns.
- **The point cloud's unhit rays land on the depth buffer's far plane** — measured −18438 m to
  +10991 m raw, with 70.3% of 12800 rays inside 200 m. A raw min/max describes the sky.

**GPS is exact, and needs no dependency.** Two facts meet: the dataset carries
`metadata.coordinate_system_wkt` (azimuthal equidistant on WGS 84, `junction-1` centred
3.185894327145 N, 101.611554629362 E) and MetaDrive re-centres each scenario on the ego's first
position but **records the shift** as `metadata.old_origin_in_current_coordinate` — verified
`[+55.725, −75.469]` against a first position of `[−55.725, +75.469]`. So
`projected = metadrive_xy − that`, then invert. `pyproj` is a dependency of *this repo* on 3.10
and is **not in MetaDrive's 3.8 venv**, so `tools/geodesy.py` solves it directly: PROJ's
ellipsoidal `aeqd` is geodesic, so the inverse *is* Vincenty's direct problem. Checked against
`pyproj` over 25 points spanning ±900 m — **0.000000 m** — and all 291 points of a drive land
inside `source/map.osm`'s bounds. Do not reach for a spherical approximation; it was not needed.

### A hosted model drives through the same socket, and Nagle is the trap (2026-08-18)

`--agent-policy remote --policy-url` on `drive.py`, `--policy-url` on
`examples/drive_with_a_policy.py`, `tools/policy_client.py` on this side and
`examples/policy_server.py` on the model's. **`remote` maps to `EnvInputPolicy` — the same class
as `manual`** — because a keyboard drive and a model drive differ only in where the two numbers
come from; `manual_control` is the only thing separating them. It follows `manual` wherever
`drive.py` special-cases a policy that drives itself: no episode budget, and not counted toward
`failures`, so the exit status keeps meaning "the dataset is drivable" rather than "the model
drove it".

**The socket exists because of the interpreter, not because of taste.** MetaDrive's venv is
Python 3.8.20 with no torch. Anything that *does* run on 3.8 should skip all of this and pass a
plain callable. And because `env.step` is the tick, a slow policy makes a drive slow and never
makes it wrong.

**`TCP_NODELAY` is worth 325×, and missing it reads as a slow simulator.** A localhost round trip
carrying 161 floats out and two back: **41.0 ms** stock, **0.126 ms** with the option set on both
ends — Nagle meeting delayed ACK. `env.step` itself is **0.954 ms** median headless. The client
sets it on its socket; the server sets `disable_nagle_algorithm`. Miss either half and it returns.

Measured per step on `junction-1`, all giving the same 291-step drive:

| `--render` | `--sensors` | sent | round trip |
|---|---|---|---|
| `none` | — | 0.9 KB | 0.880 ms |
| `none` | `imu,gps` | 1.4 KB | 0.977 ms |
| `3D` | `camera,imu,gps` | **226.6 KB** (901.6 before Phase A) | 14.98 ms |
| `offscreen` | — | **3600.4 KB** | 29.35 ms |
| `offscreen` | everything | **3652.4 KB** (5002.4 before) | 49.03 ms |

**The two camera rows fell when Phase A stopped sending pictures as floats.** Both were
re-measured on 2026-08-24 **either side of the change** — same drive, same server, same flags,
with `_UINT8_SENSORS` emptied for the before — so the two figures in each cell are a matched
pair and not a comparison across sessions. The other three rows did not move and were not
re-measured; the round trips are the 2026-08-18 figures throughout, because the wire got
smaller rather than faster and at these sizes it is the render that dominates.

**The re-measured befores are not quite the 2026-08-18 ones, and the gap is the point.** 901.6
against 901.5 is the same row. **5002.4 against 5001.2 is `route`**, which is 1.2 KB and did not
exist when the row was first taken — "everything" is a moving set, so a row labelled that way
has to say when it was measured.

**`--render offscreen` costs 3.6 MB a step with no sensors asked for**, because it forces
`image_observation` and the observation becomes a 3-frame camera stack (320×240 by default, hence
3600 KB; a 320×180 camera gives 2700 KB). That stack is **MetaDrive's own float observation and
Phase A does not touch it** — which is why the offscreen row fell by 1349 KB rather than by 4×,
and why the `offscreen | —` row did not move at all. `none` and `3D` both keep the observation at
161 floats — which is why the 3D row *with* a camera is cheaper than the offscreen row without
one. `drive.py` prints KB/step so this is a number rather than a mystery.

**Four things the plumbing test settled, and none of them needed a model:**

- serving back a local IDM drive's own actions reproduced it exactly — **291 steps, completion
  0.774126, bit-identical observations and actions** — but only when both sides read the *same
  float32*. The recorder saves float32 while `IdmDriver` returns float64, so replaying a recording
  against the float64 original diverges to 1.9e-3 by step 6. That is chaotic amplification of a
  1e-8 action difference, **not the wire**, and the test has to hold the dtype fixed to say
  anything.
- every observation the server received was bit-identical to the one the car had.
- `--backend constant --steering 1.0` leaves the road in 13 steps at −4.59 m lateral and
  `-1.0` in 12 at +4.00 m — opposite sides, so the action reaches the wheels with the right sign.
- the client **refuses** what MetaDrive would swallow: out of [-1, 1], `NaN`, `inf`, wrong length.

**`kill -INT` does not reliably stop a `serve_forever` without a controlling terminal** —
measured: the process kept serving and `--log-observations` never wrote. `policy_server.py`
handles SIGINT and SIGTERM explicitly and calls `shutdown` from another thread, because calling
it from the handler deadlocks against the loop it is stopping.

**`tools/` runs on 3.8 and ruff checks it against this repo's 3.10.** Ruff asked for
`zip(..., strict=)` (B905) in `policy_client.py` and that keyword does not exist on 3.8; the loop
is indexed instead. Parse every new `tools/` or `examples/` file with MetaDrive's own interpreter
before believing ruff.

### A picture crosses the wire as 8 bits, and two of the four heavy sensors are not pictures (2026-08-24)

Stage 9 Phase A, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.

**Nothing renders float32.** `image_buffer.py:106` is
`np.frombuffer(origin_img.getRamImage().getData(), dtype=np.uint8)`, so a camera frame is 8-bit
when it leaves the GPU, and `BaseCamera._format`'s `ret / 255` (`base_camera.py:208-214`) is what
*creates* a float. Measured on numpy 2.2.6, one 512x288x3 frame: **442,368 B** as uint8,
**3,538,944 B** after `perceive(to_float=True)` — `uint8 / 255` promotes to **float64**, not
float32 — and 1,769,472 B after the cast back down for the wire. So the old path inflated 8x on
the CPU and immediately threw half of it away, and there was nothing in it to throw:
`(v / 255 * 255).round().astype(uint8)` returns all 256 values exactly. `tools/policy_client.py`
now reads a camera with `to_float=False` and drops the `dtype=numpy.float32` beside it — **both
halves, or the cast undoes the read**.

**But `to_float` reaches two different `_format`s, and the second one converts rather than
reformats.** This is the whole of the work and the plan had it wrong:

| `--sensors` | class | native | `to_float=False` does |
|---|---|---|---|
| `camera` | `RGBCamera` -> `BaseCamera` | uint8 0-255 | `astype(uint8, copy=False)` — free |
| `semantic` | `SemanticCamera` -> `BaseCamera` | uint8 0-255 | the same — free |
| `depth` | `DepthCamera` | **float32**, 0-1, nonlinear | `(ret * 255).astype(uint8)` — quantises |
| `point-cloud` | `PointCloudLidar` -> `DepthCamera` | **metres** | the same — destroys it |

`depth_camera.py:184-190` is the second one and `point_cloud_lidar.py:33` is the subclass that
inherits it for data that is not an image at all. Measured on the wire over a real drive, which
is what turns "quantises" into a number: **depth occupies 0.705-1.000** of its 0-1 range, so
`* 255` leaves it **76 distinct levels for the whole scene**, worst exactly where the range is
longest; and the point cloud runs **-18476.9 to +11030.2 m**, which a uint8 cannot hold at all.
Neither raises. So `_UINT8_SENSORS` is `("camera", "semantic")` and those two only.

Measured on `junction-1`, **every row either side of the change on the same drive**, with
`_UINT8_SENSORS` emptied for the before. Row 1 is `--render offscreen --step-hz 100
--decision-hz 20`, 3788 steps and completion 0.950 both times; rows 2 and 3 are at 10 Hz under
`--backend constant`, 17 steps both times. KB/step is a per-step payload size, so the drive
length does not enter it — but the sensor set does, which is why each row was taken twice
rather than compared against the older table:

| `--render` | `--sensors` | before | after |
|---|---|---|---|
| `offscreen` | `camera,imu,route` | 3602.0 KB/step | **2927.0** |
| `offscreen` | everything (7) | 5002.4 KB/step | **3652.4** |
| `3D` | `camera,imu,gps` | 901.6 KB/step | **226.6** |

Every one reconciles exactly with the arithmetic, which is how the split was checked: the
observation stack is 3 frames of 320x180x3 float32 = 2700 KB base64, a uint8 camera is 225,
a float32 camera 900, depth 300 and the point cloud 200.

Six things not to re-derive:

- **`RGBCamera.perceive` is not repeatable, so a before/after pixel comparison cannot be the
  check.** Three identical back-to-back float reads of a static scene spread by **1/255** —
  MSAA, and `perceive` steps the task manager itself — while `SemanticCamera` is exact, having
  flat colours and no antialiasing. So the naive test reports "different" and means nothing.
  **Capture one frame and apply both `_format` paths to it instead**: done that way the uint8
  path returns **the raw buffer unchanged** and `(float x 255).round() == uint8` exactly, on
  both cameras. That is the measurement that says Phase A loses nothing, and it is the only
  shape of it that works.
- **The offscreen rows fall by far less than 4x, and that is not a partial fix.** Under
  `--render offscreen` the *observation itself* is a 3-frame camera stack — MetaDrive's own float
  image observation, nothing to do with `--sensors` — which is 2700 KB of the 2927 once a
  320x180 camera is registered, and 3600 KB of the 3600 when none is. Phase A cannot touch it.
  The `3D` row is the clean one because there the observation stays 161 floats.
- **The camera size under `--render offscreen --sensors camera` is 320x180, not 320x240.**
  `drive.py:950` registers `rgb_camera` at 320x240 for the render context and `sensor_config`'s
  default then overrides it, which also shrinks the observation stack. It is why the two
  offscreen rows have different baselines.
- **Nothing downstream needed changing.** `encode_array` has always been self-describing and
  `policy_server.decode_array` reads `encoded["dtype"]`; its numpy-free `struct` fallback already
  had `"uint8": "B"`. `tools/camera_rig.py:220` already defaulted to `to_float=False`, so this
  brings `policy_client` into line rather than inventing a convention.
- **The `/255` is not dropped, it moves — and the model's own code is what settles it.**
  `assets/modifiers/modifiers.py:30` states the input contract as *"(H, W, 3) uint8 BGR from
  the sensor -> (3, INPUT_H, INPUT_W) float32 in [0, 1]"*, and `:72` is
  `np.transpose(image, (2, 0, 1)).astype(np.float32) / 255.0`. So the model **expects uint8 and
  makes the float32 itself**, fused with the channel swap and the transpose it has to do
  anyway; sending float32 meant the divide happened twice, once here in float64 on the CPU.
  "The model needs fp32" is therefore an argument *for* sending uint8, not against it. And
  **uint8, not int8**: 0-255 unsigned, where int8's -128..127 would clip. This is transport,
  not quantisation — weights and activations are untouched.
- **`new_parent_node=agent.origin` stays.** It forces a second scene render
  (`base_camera.py:188`), which is real cost, but the camera `sensor_config` registers is not
  mounted to the car. Avoiding that render is the rig's job — `CameraRig.read` passes no parent
  node — and belongs in Phase C.

`tests/unit/test_policy_client.py` is new and pins the split **against MetaDrive's own source**
rather than against the comment: it walks each sensor class to whichever `_format` it inherits
and asserts the casting ones are exactly `_UINT8_SENSORS`. Read as files, `importlib.util` and
no import, for `test_conversion._metadrive_src`'s reason — importing `metadrive` pulls in
panda3d. If upstream moves `_format`, that fails instead of a point cloud silently arriving as
noise.

### The frame can stay on the card, and the GL context has to be on the same one (2026-08-24)

Stage 9 Phase B, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.
`--image-on-cuda` on `drive.py`, `uv sync --group sim --group gpu`, and the copy back written
out once in `tools/gpu_frames.py`.

```bash
uv sync --group sim --group gpu
# the discrete card is not optional here -- see below
cd scripts && METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
    --render offscreen --agent-policy idm --sensors camera --image-on-cuda
```

`image_on_cuda` is one env-level key that `engine_core.py:615` hands to **every** registered
camera (`sensor = cls(*args, engine=self, cuda=self.global_config["image_on_cuda"])`), and
offscreen it also makes the observation stack itself a CuPy array (`image_obs.py:55-65`).
So the frame is rendered straight into GPU memory and never copied to the host.

**It is worth over half of `env.step`, which is more than the plan expected.** Measured on
`junction-1`, one 512x288 `RGBCamera`, offscreen, 200 steps after 30 warm-up, one env per
process, **three matched pairs**:

| | `env.step` median | observation |
|---|---|---|
| CPU path | 7.09 / 8.04 / 8.28 ms | `numpy.ndarray` |
| `--image-on-cuda` | **3.20 / 3.59 / 3.76 ms** | `cupy.ndarray` |

2.2x every time, and the drift within each column is the machine warming rather than
anything in the code. The saving is the readback: the CPU path's `get_rgb_array_cpu` is
`buffer.getDisplayRegion(1).getScreenshot()`, a synchronous GPU->CPU read, and
`ImageObservation.observe` then rolls the 3-frame stack on the host.

**And it is worth exactly nothing on a socket, which is now measured rather than argued.**
Same drive through `--agent-policy remote`: **2927.0 KB a step either way**, byte for byte,
17 steps and completion 0.061 both times. `encode_array` needs host bytes, so the frame is
copied back and the run has done strictly *more* work than the CPU path. It pays in Phase C,
where the model is in the same process and reads `__cuda_array_interface__`.

Eight things not to re-derive:

- **CUDA-GL interop needs both contexts on the same GPU, and on this machine that means the
  PRIME offload.** Without `__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia` the
  GL context lands on the Intel iGPU while CUDA is on the RTX 4050, and
  `cudaGraphicsGLRegisterImage` fails with **`cudaErrorUnknown(999)`** at env construction.
  Nothing in the message says "wrong card". `scripts/_common.sh:exec_with_gpu` already sets
  both, so `./drive.sh` is fine and a bare `uv run python tools/drive.py` is not. The README
  paragraph saying `image_on_cuda` is "unrelated" to which card renders was right about
  renderer *selection* and wrong about the dependency: it needs that selection to have gone
  the NVIDIA way.
- **`cuda-python` must stay below 13.** `base_camera.py:14` is `from cuda import cudart`, and
  cuda-python **removed** that top-level shim at 13.0 for `cuda.bindings.runtime`. Measured:
  13.3.1 raises `ImportError: cannot import name 'cudart' from 'cuda'`; 12.9.7 imports it with
  a `FutureWarning`. **An unpinned resolve picks 13.3.1** -- which is what the plan's own
  dependency resolve had recorded -- and `_cuda_enable` is then False.
- **CuPy 14 ships no CUDA headers and there is no system toolkit on this machine**, so
  `cupy-cuda12x` alone imports and then dies on the first kernel with *"Failed to find CUDA
  headers"*. The pin is `cupy-cuda12x[ctk]`, whose extra pulls `cuda-toolkit==12.*` (9 wheels,
  ~1.5 GB). Two smaller routes were tried and **both fail**: `cupy-cuda12x<14` bundles headers
  but then wants `libnvrtc.so.12`, and adding `nvidia-cuda-nvrtc-cu12` / `nvidia-cuda-runtime-cu12`
  beside either version does not get found -- neither CuPy 13's plain `dlopen` nor CuPy 14's
  `cuda-pathfinder` looks in that pip layout without `CUDA_PATH`.
- **A kernel is unavoidable, so the headers are not optional.** `BaseCamera._format` is
  `ret.astype(np.uint8, copy=False, order="C")` under `to_float=False` and `ret / 255` under
  `to_float=True`; on a CuPy array both compile a kernel. And `/ 255` promotes to **float64**
  on the card exactly as it does on the host -- 8x the GPU memory, the same Phase A trap one
  bus further along.
- **`perceive` comes back contiguous, so there is no `ascontiguousarray` to pay for.** The
  plan expected the doubly-reversed view (`base_camera.py:196`) to reach the caller with
  negative strides; `_format`'s `order="C"` has already resolved it. Measured on a live drive:
  `c_contiguous True`, strides `(480, 3, 1)`, identical to the CPU path's.
- **The CUDA frame is the same picture and not the same bytes.** Semantic camera, same seed,
  same actions, step 8: **92.65% of pixels identical**, the same four semantic colours and no
  fifth, and **64% of the differing pixels sit on a colour boundary** against a 9.4% base rate.
  It is a sub-pixel resolve difference between the bound render texture and
  `getDisplayRegion(1).getScreenshot()`, not a stale frame (no CPU frame at any offset matches)
  and not a channel order (no permutation or flip matches either). So a bit-exact frame
  comparison is not the check here; **the drive is**, and it is identical -- `--agent-policy
  idm` on `junction-1` gives 291 of 370 steps, completion 0.774, `out_of_road` at -5.44 m both
  ways, with `drive.py`'s whole output byte-identical.
- **`frame_gate` and `--image-on-cuda` cannot both hold a frame, and the refusal moved.**
  `frame_gate.install` raises on `image_on_cuda` outright, and `drive.py` installs the gate on
  *every* offscreen run -- so the first version of this flag died in a traceback on a plain
  drive with no `--decision-hz` at all. The gate is now skipped under CUDA, and refused by
  name only where the stride really would hold a frame (`stride > 1` without
  `--draw-every-step`), before the terrain build rather than after it.
- **`numpy.asarray` on a CuPy array raises rather than copying**, by design, so every place in
  `tools/` that writes bytes has to copy deliberately: the sensor frames and the observation
  in `policy_client`, and the `.npz` in `agent_env.ActionRecorder`. `tools/gpu_frames.to_host`
  is that copy, in one place, and `is_device_array` tests the *interface* rather than
  `isinstance(x, cupy.ndarray)` -- `tools/` has to keep importing in the default environment,
  which has no `gpu` group. `tests/unit/test_gpu_frames.py` pins all three call sites and the
  two version caps by AST and by reading `pyproject.toml`; all six guards were shown to fail
  against the pre-Phase-B code before being kept.
- **There is a fourth such `asarray` and it is deliberately left alone.**
  `camera_rig.CameraRig.read` would raise the moment `image_on_cuda` reached it, and cannot
  today: only `drive.py` sets the key and it does not use a rig, while the rig's callers
  (`sensor_survey.py`, `step_timing.py`) never set it. Wiring `--image-on-cuda` into the sweep
  means wrapping that one line in `gpu_frames.to_host`. Said in both docstrings rather than
  pre-solved, so it is not a `to_host` on a path no test can reach -- which is what the
  `ActionRecorder` call site was until the recorder learned to split an offscreen observation
  (see below), and is the one thing to check before adding a fourth.
- **`--render 3D` is refused, and the drive is not what fails.** Measured on `junction-1`:
  352 of 370 steps, `arrive_dest=True`, completion 0.953, and then `env.close()` raises
  `cudaErrorInvalidGraphicsContext(219)` from `MainCamera.unregister` (`main_camera.py:585`,
  via `base_engine.py:529`) -- a CUDA graphics resource handed back against a GL context that
  has already gone. MetaDrive's bug, so not patched here; refused rather than caught because
  the pairing buys nothing today (the point of a frame on the card is a model reading the
  pointer in this process, and 3D is for a person to watch) and because a successful drive
  exiting non-zero destroys the one thing `drive.py`'s status means. Catching that single
  error around `close()` is the other way, and is what Phase C would want if it ever needs to
  watch a CUDA-fed model in a window.
- **A recording made with the flag is the same picture and not the same bytes, and the control
  is what says so.** Two CPU-path recordings of the same drive are **bit-identical** -- 100%,
  because `ImageObservation.observe` reads the buffer with no parent node and so forces no
  second render, which is what made Phase A's RGB jitter appear here and it does not. Against
  the CUDA path: **35.80% of pixels identical, median difference 1/255, 89.33% within 8/255,
  99th percentile 49, mean level 150.626 against 150.619**. No whole-pixel shift improves the
  match, so it is not misalignment; it is the sub-pixel MSAA resolve already recorded above,
  measured on RGB rather than on the semantic camera, where continuous tone spreads it across
  far more pixels for the same magnitude.
- **The proof line has to read the camera as well as the observation**, because the render mode
  decides which one holds the frame: offscreen the observation *is* the CuPy stack, while under
  `--render 3D` it is 161 floats and the frame exists only inside a registered camera. Reading
  the observation alone printed "no image observation to check" in exactly the mode a reader
  most wants the proof. `drive._cuda_frame_report` falls through to `_first_camera` and
  `perceive(to_float=False)` with no parent node, which copies the buffer the frame pass has
  already filled rather than forcing a second render.

**The 3.8 venv has had all three packages installed the whole time and the gate has been shut
the whole time.** `/home/keith/Desktop/work/wingfin/metadrive/.venv` holds cupy-cuda12x 12.3.0,
cuda-python 12.1.0 and PyOpenGL 3.1.6, and `_cuda_enable` is **False** there because that CuPy
fails to import. Nothing says so at import time. **MetaDrive does raise rather than falling
back** -- but late, minutes into a terrain build, and from one of two places whose hints both
point elsewhere: offscreen it is `ImageObservation.__init__` (`image_obs.py:57`), which gates on
**cupy alone** and so names cupy even when what is missing is PyOpenGL or cuda-python; under
`--render 3D` no image observation is built and it is `BaseCamera.__init__`
(`base_camera.py:56`), hinting "pip install pypiwin32" on a Linux box. That is why `drive.py`
checks `_cuda_enable` itself and refuses first, printing `sys.executable`. The `gpu` group is
installed into **this repo's** 3.10 venv beside `sim`, so `scripts/drive.sh` needs
`METADRIVE_PYTHON` pointed at it.

### A recording carries the pictures now, and the observation is two shapes (2026-08-24)

`--record --render offscreen` had **never worked**. `ActionRecorder.record` did
`numpy.asarray(observation, dtype=float32).ravel()`, and offscreen the observation is not an
array: MetaDrive swaps in `ImageStateObservation`, which returns `{"image", "state"}` -- a
`(H, W, 3, 3)` camera stack and a **41**-number state with no lidar block (`image_obs.py:40`).
It died with `TypeError: float() argument must be a string or a real number, not 'dict'`. That
predates Phase B by a long way, and its cost was not only the crash: the `to_host` Phase B put
in that method sat on a path nothing could reach, **guarded by an AST test that could not
fail**. Counting a green test as coverage is the same mistake as counting refusals as faults.

`record` splits the dict now, `save` writes `images` and `image_scale` beside `observations`
and `actions`, and `--record-no-images` drops the frames for anyone who wants the numbers
alone. Measured on `junction-1`, 352-step replay: `observations (352, 41)`, `images
(352, 180, 320, 3, 3) uint8`, **84.4 MB** on disk, against **29 KB** with the frames dropped.

Six things not to re-derive:

- **uint8 is the inverse of what made the float, not a quantisation.** `norm_pixel` makes the
  stack float32 in [0, 1] (`image_obs.py:75-77`), but the camera renders 8-bit and it is
  `BaseCamera._format`'s `ret / 255` that created the float -- Phase A's finding, one bus
  earlier. `round(x * 255)` returns all 256 values exactly, pinned by a test over the whole
  range. It is a quarter of the size: 518 KB a step against 2.07 MB, ~151 MB raw for a
  291-step drive rather than ~603.
- **`image_scale` goes in the file rather than being inferred from the dtype**, because uint8
  is not proof of a scale: a camera with `norm_pixel` off is uint8 and already unscaled.
- **A `PointCloudLidar` image source must not be scaled**, and `issubclass(cls, BaseCamera)`
  will not catch it -- it subclasses `DepthCamera`. `image_obs.py:73-74` gives it
  `Box(-inf, inf)` whatever `norm_pixel` says, and a real drive runs -18476.9 to +11030.2 m.
  `drive._images_are_normalised` asks for it **by name**, off the env config, and hands the
  answer to the recorder, which cannot see an env.
- **The whole 3-frame stack is kept, and the older two are not redundant.** They look like the
  previous steps' frames and are not recoverable as such: under `--decision-hz` the frame gate
  returns the stack *unrolled* on a held step, so rebuilding from a sequence of newest frames
  would shift frames the real stack never shifted.
- **Freeing the frames before compression is worth 16 MB of 364, and the version that looked
  like a halving was worth nothing.** Filling a pre-allocated `numpy.empty` and dropping each
  frame as it is copied gives **348.1 MB against 348.2** for `numpy.stack` -- the destination
  is allocated whole before the first frame can go. What does help is dropping the list before
  `savez_compressed` runs: 348.1 against 364.4 across the whole span, because `savez_compressed`
  streams in chunks. Two lines, not fifteen. And none of it shows in the process: peak RSS is
  **2.92 GB with the recording against 2.91 without**, the terrain build being the real peak.
- **`save` returns a dict, not the old `(observations, actions)` pair.** There is a third array
  now and a silently-lengthened tuple is how a caller prints the wrong one; both callers
  (`drive.py`, `examples/drive_with_a_policy.py`) were updated. The **`actions` key may not
  move** -- `examples/policy_server.py:134` is `numpy.load(path)["actions"]` and is the only
  reader of these files anywhere.

`tests/unit/test_agent_env.py` is new and is the first thing in this repo to exercise the
recorder rather than read its source. Both of its load-bearing guards were shown to fail
against the old `record` before being kept, and its CuPy stand-in **raises** on
`numpy.asarray` as the real thing does -- without that it is quietly accepted as a 0-d object
array and the guard passes on a shape mismatch instead of on the fault it is written for.

### The rate is `--step-hz`, and there are two clocks, not one (2026-08-19)

**10 Hz was never MetaDrive's rate, only its default.** `env.step` advances
`physics_world_step_size` x `decision_repeat` — 0.02 x 5 — and both keys are ordinary config.
`drive.py`'s `step_config(hz)` derives them: `repeat = max(1, ceil(dt / 0.02))`,
`physics = dt / repeat`, so the physics tick is never *coarser* than MetaDrive's own and
**10 Hz returns exactly (0.02, 5)** — which is what makes `--step-hz 10` and no flag the same
run. 100 Hz gives (0.01, 1). The pair is deliberately not exposed: the rate is their product,
and `decision_repeat` also decides how many `taskMgr.step()`s run per `env.step`, each of which
redraws every camera buffer.

**Two clocks.** `sim_step_seconds(env)` is how far one `env.step` advances the simulator;
`data_step_seconds(scenario)` is how far one recorded frame covers. They are equal only when the
dataset was converted at the rate it is being driven at, and **two places in `tools/` were
reading the wrong one** — right by coincidence rather than by construction:

- `signal_control` converted an **engine** step count to seconds using the **plan's** rate.
  Those lights are live precisely because the tape is not being used. It reads the engine now,
  and the docstring says why so the next reader does not put it back.
- `drive.py`'s `_longest_red` divided seconds by the **data** rate to produce a budget counted
  in **env** steps. It returns seconds; the caller converts, because the caller is the one that
  knows which clock it is counting in.

**The rate is a convert-time argument, never a config field** — `configuration_checksum` feeds
`generation_fingerprint`, so a field on `ConverterConfig` would invalidate the Stage 3 review.
Same reason as `--speed-kph` and the render flags. `STEP_HZ` in `.env` reaches `drive.sh` and
`sensor-survey.sh` and is **deliberately not wired into `run-stages-4-6.sh`**: a dataset's rate
is baked into bytes the review never re-checks, and picking it up from a machine-local file is
how two workspaces end up at different rates with nobody having decided.

**A rate gets its own directory, because the filename cannot carry it (2026-08-20).** The
scenario id is `<workspace>-<fingerprint16>-<route name>`, so the 10 Hz and the 100 Hz build of
one route want the *same* `sd_*.pkl` name — and the stale-pickle sweep under the write loop
deletes whatever the current run did not write, so the second convert took the first one out.
`conversion.dataset_dir_name` names it from the **interval** rather than from the `--step-hz`
argument, so no flag and `--step-hz 10` both land in `scenarionet-10hz`. `routes.json` carries
no timing at all — `{name, start_lane, end_lane}` and the identity block — so the *same* routes
file feeds both builds, and picking a rate is a convert-time decision, never a route.

Three consequences worth not re-deriving:

- **The sweep is now per rate**, which is what it should be: a route dropped from `routes.json`
  is cleaned out of that rate's directory and the other rate's dataset is untouched.
- **`reports/scenario-conversion-<rate>hz.json` follows the same rule**, because the report
  carries each written file's sha256 and size — one report over two live datasets would
  describe the other one's bytes. `check_dataset.py`'s `--png` default is
  `stage-6-map-<rate>hz.png` for the same reason. `manifest["stage_6"]` stays a single object
  and gains `step_hz`, `dataset_dir` and `report`, naming which build it describes.
- **`_common.sh:resolve_dataset` is what `drive.sh` and `sensor-survey.sh` select with**, from
  a `--step-hz` in their own passthrough args first and `STEP_HZ` second — one way to say the
  rate, so `-- --step-hz 100` picks the 100 Hz dataset rather than pointing a 100 Hz simulator
  at the 10 Hz one and being refused. A bare `<ws>/scenarionet` from before the rename still
  drives, but **only while the workspace has no rate-named dataset at all**; once it has one,
  the bare directory is a stale build and not an answer to which rate was asked for.

**And no new metadata key.** `metadata.ts` spacing *is* the rate, exactly — an integer step
index times the interval — so `metadata.dt` would move the bytes of every scenario ever
converted without one, for information already in the file. Everything reads it back off `ts`.

**A dataset can only be *replayed* at the rate it was written at, and `drive.py` refuses the
mismatch rather than warning.** Three things consume the recording one frame per `env.step` with
no interpolation, so at a different rate they run the tape at the ratio of the two clocks:
`ReplayEgoCarPolicy` (`replay_policy.py:41-65`), a baked light tape
(`scenario_light_manager.py:68-75`), and any non-ego track. None *fails* — each simply drives
something other than what the dataset says, which is why it is a refusal.

Three more couplings are **MetaDrive's own and are warned about, never patched** — a reference
checkout is not edited here. `PIDController` (`PID_controller.py:1-22`) has **no dt at all**, so
both its gains scale with the rate and `--agent-policy idm` will not drive identically;
`LANE_CHANGE_FREQ = 50` and `IDM_ACT_BATCH_SIZE = 5` are counted in steps; `STEERING_INCREMENT`
is applied per `env.step`, so the keyboard feels 10x slower at 100 Hz; and `ForceFPS` takes its
interval from `physics_world_step_size`, so 3D asks the display for 100 fps.

**Measured on `junction-1` (403.7 m `test` route), because none of it was guessable:**

| | 10 Hz | 100 Hz |
|---|---|---|
| `env.step`, headless | 1.094 ms | **0.848 ms** |
| `env.step`, `--render offscreen` | 10.9 ms | 20.2 ms |
| `env.step`, `--render 3D` (RTX) | 83.4 ms | 16.6 ms |
| 3D speed against wall-clock | 1.20x | **0.60x** |
| scenario pickle, map + route | 791,940 B | 1,121,208 B (+41.6%) |
| the same, + a 3-lane light plan | +6,666 B | +56,559 B (+5.0%) |
| `convert` wall-clock | 1.53 s | 1.54 s |

**One `env.step` is *cheaper* at 100 Hz, not dearer** — `decision_repeat` is 1 rather than 5, so
it is one physics substep instead of five. A whole drive still costs about 7.8x, because there
are ten times as many. **3D tops out at 60 fps either way** (5 frames per 83.4 ms; 1 per
16.6 ms — the display's vsync), so asking `ForceFPS` for 100 is what makes a 100 Hz drive run at
0.60x real time rather than 1.20x. It is usable, and it is slower than the clock on the wall.
The light tape is a Python list of colour *strings* per lane per step, so it grows linearly:
about 5.1 B per lane per step, which a 20-lane plan at 100 Hz turns into ~370 KB a scenario.

### What a step costs is measured now, not quoted (2026-08-20)

`tools/step_timing.py` / `scripts/step-timing.sh` drives every rate a workspace holds and
reports wall-clock against simulated time. Re-measure with it rather than quoting a number
from this file. **What every row, column and CSV field means is
`docs/step-timing-rows.md`**, and `--list-rows` prints the short version — neither this
section nor the README is the place to look that up, and a row description that lives in
two places drifts.

**And it does not reproduce the four hand-measured `env.step` figures in the table above.**
Same route, same `--render none`, same replay policy: **2.357 ms at 10 Hz against the 1.094
recorded, and 2.181 at 100 Hz against 0.848** — about 2.2x both, with the direction preserved
(100 Hz still cheaper than 10 Hz, because it is one physics tick against five). Not heat: the
cores were at 773 MHz mean and 63 C when this was taken. The likeliest difference is what the
older figure timed - `env.step` includes `_get_step_return`, which builds the observation and
evaluates reward and termination, and a measurement around `_step_simulator` alone would come
out roughly here. Unresolved, and recorded so the older numbers are not trusted as a baseline.

**The default is rows 1–6** — everything but the 3D row, which opens a window and so cannot be
part of an unattended sweep. Rows 1 and 2 differ only in who drives: `replay`, which writes the
car's position from the file and decides nothing, and `idm`, which computes. Row 3 puts a hosted
model in the same seat and **skips itself with `needs --policy-url`** when nothing is listening,
which is a truer thing for the table to say than the row not appearing. One row on its own is
`./step-timing.sh <ws> -- --rows 5`. Every row but 6 is `--render offscreen`, because a camera
cannot exist without one.

**The sweep raises `max_lateral_dist` to 20 m (`SWEEP_MAX_LATERAL_M`), and that is not a
preference.** MetaDrive's 4 m (`scenario_env.py:84`) ends an episode when the car strays from
the *recorded* route, and it is there to judge driving; this tool measures what a step costs,
where a car 6 m off its line costs exactly what one on it does. At 4 m the IDM rows on `mosque`
ended `out_of_road` at step 44 with **24 steps measured** against replay's 380 — and **four of
the six default rows are IDM**, so most of the table would have been a median over two dozen
samples. Applied uniformly, replay included, so no row is measured under different termination
rules from the one it is compared with, and recorded as `max_lateral_m` rather than applied
silently. `drive.py` keeps the 4 m: that tool *is* asking whether a drive is drivable.

**The camera it prices is one the tool invented, until `--camera-rig` names a real one.**
Unflagged, every offscreen row registers a single 320×180 `RGBCamera` — a size chosen in
`step_timing.CAMERA_SIZE`, not by any vehicle — and since the camera is about three quarters of
a step, an unflagged figure is not what a real car costs. `--camera-rig` takes the same
CARLA-shaped spec `sensor-survey.sh` takes, read by `tools/camera_rig.py`, and mounts the
vehicle's own cameras. Measured on `junction-1` at 10 Hz over 200 steps, replay row, same drive:
the seven-camera spec (six 512×288 and one 1280×720 wide, **5.42 MB of image a step** against
0.17) runs at **24.70 ms/step and 3.08x real** against **10.00 ms and 9.28x** for the invented
one, with row 6's no-graphics floor at 3.20 ms / 27.20x. Four things not to re-derive:

- **`rig_ms_median` is allowed in the timing loop and a row's `read` list still is not.** The
  bar is against forcing a *second render pass* — `SensorPack` reads with a parent node, which
  costs another `taskMgr.step()`. `CameraRig.read` passes none and copies the buffers the frame
  pass already filled: **3.90 ms** for the seven. Only the `image_source` camera reaches the
  observation, so the other six are read there or not at all, and a training loop reads all of
  them.
- **`image_source` must name a rig camera**, or MetaDrive registers a dead 320×240 `rgb_camera`
  beside the rig and renders it every step (`image_obs.py:68`). `agent_env.make_env` already
  merges `sensors` unless the caller names its own source.
- **The `sensors` column counts cameras by class, never by the name `rgb_camera`.** A rig's
  cameras are named by the spec, so a name test reports a seven-camera run as having no camera
  at all — the same mislabelling the live-env probe was added to prevent, by a different door.
  It prints `camera x7`.
- **A spec's `tick_rate` is not honoured and the run says so.** Buffers redraw once per
  `env.step` whatever the rate, so a rig declaring 0.1 s draws every 0.01 s on a 100 Hz dataset;
  a line is printed per dataset where the two differ and `camera_hz` records what they really
  drew at. Resampling is Phase 2 of `docs/implementation-plan/adjustable-simulation-sample-rate.md`.

**Read `policy_ms`, not the difference between the rows, and that was the plan being wrong
rather than a preference.** Measured three times over on `junction-1`'s 100 Hz dataset: row 1
at 8.90 / 8.99 / 10.07 ms a step, row 2 at 9.35 / 10.35 / 8.99, so the subtraction read +0.45,
+1.36 and **−1.08** ms while `policy_ms` held 0.37–0.43 ms throughout. About a millisecond of
run-to-run spread swamps it, and the two rows do not drive quite the same route anyway — a
replayed car follows the tape, an IDM car its own line, and it ends early. `policy_ms` is timed
around the policy call and is the answer; the replay row is the reference for whether the
simulator keeps up with nothing deciding at all.

**And the machine's state is worth more than the code's.** The same configuration measured
8 ms a step early in a session and 17 ms after twenty minutes of back-to-back sweeps. Absolute
figures are only as good as the box was quiet; ratios within one run are what compares.

Six things not to re-derive:

- **The camera readback is inside `env.step` and must not be timed twice.** With
  `image_observation=True`, `ImageStateObservation.observe` calls `perceive()` and rolls the
  3-frame stack while building the return value (`image_obs.py:85`) — no parent node, so it is
  the cheap buffer read. `sensor_ms` here is therefore the *numeric* sensors only. An earlier
  version of the plan had a row isolating "the readback" by not reading it, which is not a
  thing that can be arranged.
- **Every offscreen row carries a camera, and the `sensors` column has to say so.** It printed
  the row's *read* list — what the timing loop pulls out for itself — which the camera is
  deliberately not in, so rows 1 and 2 read as `imu,gps` and looked camera-less while drawing a
  320×180 frame every step. Keith read the table exactly that way. `sensors` is now taken off
  the live env (`image_observation` on **and** a camera really registered), and `camera_size`
  off the frame's own shape, so a row that stops building one says so instead of repeating what
  it was meant to do. **The camera must never go in `read`**: `SensorPack` reads with a parent
  node, which forces a second `taskMgr.step()` (`base_camera.py:188`) and would charge the
  benchmark for a frame no training loop draws. Row 3 was doing that *and* sending the image
  twice, the observation it ships already being the image stack - measured over the same
  drive, **3601.0 KB a step against 2700.9** once the camera left its read list.
- **The camera is about three quarters of a step, so it is what the sweep mostly measures.**
  `--rows 2,6` on `junction-1`, same route and policy either side: 16.69 ms a step against
  **4.06** at 10 Hz (5.45x real time against **19.82x**), 17.48 against **3.57** at 100 Hz
  (0.51x against **2.30x**). Row 6 reads imu/gps for exactly this reason — it used to read
  nothing, so the subtraction moved two things at once.
- **imu/gps cost ~0.13 ms, which no row can measure.** That is under the run-to-run spread that
  already defeated row 2 minus row 1; back to back, row 4 has come out dearer than row 2 on
  noise. Row 4 is therefore labelled as what it is — the vision-only shape — and
  `sensor_ms_median` answers the sensor question directly.
- **`--physics-hz` exists because `--step-hz` derives both keys from one number.** 10 Hz gives
  `(0.02, 5)` — **50 Hz of physics, not 10** — and 100 Hz gives `(0.01, 1)`, so one step at
  100 Hz is *cheaper* and `ms/step` is not comparable across rates. `--physics-hz 100
  --step-hz 10` is 100 Hz integration with 10 Hz decisions: CARLA's own default shape
  (`fixed_delta_seconds` 0.1, `max_substep_delta_time` 0.01, `max_substeps` 10), and the only
  pairing whose number means anything beside a CARLA figure. A rate that does not divide the
  step is refused, never rounded.
- **The per-step overhead is what a higher rate multiplies, not the physics.** Measured with
  no graphics on `junction-1`: 2.14 / 2.44 / 2.87 ms a step at 5 / 10 / 20 ticks, so about
  **1.90 ms fixed plus 0.049 ms a tick**. Per simulated second, 10 Hz → 100 Hz is 10x the
  overhead and only 2x the integration.
- **With a camera the camera is the budget** — 16.80 ms a step at 10 Hz against 16.11 at
  100 Hz on one run, identical to within the noise, because one frame is drawn per `env.step`
  whatever the rate (`base_engine.py:458`, unconditional). So the image rate *is* the step
  rate, a camera costs a full 10x more per simulated second at 100 Hz, and that is the whole
  of the difference in real-time factor: **5.79x at 10 Hz against 0.61x at 100 Hz** on the same
  drive. The decision rate is the only one that separates, and it separates in the caller's
  loop rather than in any config key.
- **The first env of a process is dearer than the ones after it**, so `prime` builds and
  throws one away before anything is measured. Without it the first row carried the graphics
  driver's shader compilation and cache filling, and since the rows are meant to be compared
  with each other that is a bias rather than noise. `wall_seconds` also starts *after* the
  warm-up steps rather than before them, for the same reason — an earlier version counted them
  in and reported the floor as the dearer of the two rows.
- **There is no unthrottled 3D row, and that was measured before it was dropped.** `ForceFPS`
  looked like the thing to raise, but `force_render_fps=1000` gives 16.59 ms a frame against
  16.67 stock at 100 Hz and 83.34 against 83.50 at 10 Hz, and loading `sync-video #f` into
  panda3d before the window exists does not move it either. The ceiling is the compositor's
  60 Hz. The row records `force_fps` — the engine's own state — instead of claiming a number
  it does not have.

**Every run writes its own CSV**, `<workspace>/reports/step-timing-<label>-<stamp>.csv`, never
appending and never overwriting, with the machine (host, docker, CPU, GPU, GL ceiling,
versions) repeated on every row so two machines' files concatenate. `STEP_HZ` is deliberately
not read: the sweep drives *every* rate, each at the one it was written at, and picking one
would be the opposite of the comparison. `--label` matters in a container, where the hostname
is a random id.

**Phase 1 makes 100 Hz *available*, which is narrower than it sounds.** The only things that
*record* numeric sensors at that rate are `sensor_survey.py`'s per-step CSV and
`policy_client`'s wire. `drive.py --record` writes observations and actions only. 100 Hz IMU and
GPS on disk from `drive.py` is separate work, named here so it is a decision rather than an
omission — as are the 20 Hz cameras, which are Phase 2 of
`docs/implementation-plan/adjustable-simulation-sample-rate.md`.
### MetaDrive runs on 3.10, and the container is one environment, not two (2026-08-21)

**The two-interpreter split was never a MetaDrive requirement, only how it was installed here.**
`scripts/drive.sh`, `sensor-survey.sh` and `step-timing.sh` shell out through `METADRIVE_PYTHON`
to a 3.8.20 / numpy 1.24.4 venv because that is what the reference checkout has. MetaDrive
0.4.3 has **no `python_requires` cap** (its extras are keyed `:python_version >= '3.8'` with
`numpy>=1.21.6` unbounded), **no `ext_modules`** — the wheel is `py3-none-any`, so no compiler
and no 3.8 ABI tie — **zero numpy-2-removed aliases** in `metadrive/`, no `numpy.core` imports,
and its ten `copy=False` call sites are all `.astype()` / `np.nan_to_num()`, neither of which
numpy 2 changed; the one that did is `np.array(copy=False)`, which does not appear. It resolves
and runs on this repo's 3.10 / numpy 2.2.6 with **no version in `uv.lock` moved** — the lock
gained 543 lines and changed nothing that was already in it.

Installed as an **opt-in `sim` dependency group**, so `uv sync` on the host still installs only
the default and `dev` groups and the checkout-plus-3.8-venv arrangement keeps working until
`uv sync --group sim` is run deliberately.

**The group pins a commit, not `==0.4.3`, and that is not fussiness.** `git describe` on the
checkout says `MetaDrive-0.4.3-32-g85e5dadc` — 32 commits past the tag, on public `origin/main` —
and `metadrive.constants.EDITION` reports `MetaDrive v0.4.3` for both. A version pin would let
two machines run different simulators while every step-timing CSV claimed they were the same,
which is the one thing a cross-machine benchmark must not allow. Nothing in the CSV can tell
them apart, so the lock is what does.

Measured, `mosque`, 200 steps, same rows either side:

| | host, 3.8 / numpy 1.24 | container, 3.10 / numpy 2.2 |
|---|---|---|
| row 1, offscreen replay | 3.78 ms/step, 25.91x | 4.05 ms/step, 24.23x |
| row 6, no graphics | 0.99 ms/step, 86.35x | 1.03 ms/step, 82.26x |

Six things not to re-derive:

- **The NVIDIA container toolkit does not install the glvnd EGL manifest, and without it the
  benchmark silently runs on the CPU.** `NVIDIA_DRIVER_CAPABILITIES=graphics` injects
  `libEGL_nvidia.so.0` and `libGLX_nvidia.so.0`, but leaves `/usr/share/glvnd/egl_vendor.d/`
  holding nothing but Mesa's `50_mesa.json` — so libglvnd never sees the driver. Measured before
  the image wrote its own `10_nvidia.json`: `getDriverRenderer()` reported
  `llvmpipe (LLVM 15.0.7, 256 bits)` at 16384 max texture, against the RTX's 32768. It does not
  error and the table looks ordinary. `gl_renderer` in the CSV is the check.
- **`utility` in that same variable is what injects `nvidia-smi`**, which `step_timing.py:275`
  reads the `gpu_name` column out of. `graphics,compute,utility`, all three.
- **panda3d already declares the fallback.** Its own `Config.prc` ships `load-display pandagl` /
  `aux-display p3headlessgl`, so EGL would be found eventually anyway; the image swaps the two
  lines so it is the first choice rather than the result of a failed GLX attempt, and leaves
  `pandagl` as the aux so an X socket still gets the 3D row. Measured on the host with `DISPLAY`
  unset: GLX lands on the **integrated** card at 16384, EGL on the discrete one at 32768.
- **`UV_PYTHON_INSTALL_DIR` is a permissions fix, not tidiness.** uv puts its managed CPython in
  `$HOME/.local/share/uv/python`, which at build time is `/root` at mode 700, and the venv's
  `python` is a symlink into it. The container runs as the host's uid — so reports written into
  the mounted workspace are not owned by root — and that user cannot read `/root`: the
  interpreter dies before it starts with `sys.executable = ''` and
  `ModuleNotFoundError: No module named 'encodings'`, which reads as a broken image rather than
  as a permissions problem.
- **The venv lives outside `/work`.** The repo is bind-mounted there and the host's own `.venv`
  is in it; `UV_PROJECT_ENVIRONMENT=/opt/venv` plus `UV_NO_SYNC=1` is what stops a `uv run`
  inside the container reaching into — or resyncing — the host's environment. The image is built
  in `/work` for the opposite reason: the editable install of `osm_scenario` then points at
  `/work/src`, which the mount replaces with the live source rather than a stale copy.
- **`network_mode: host` is a measurement decision.** Row 3 times a round trip to `--policy-url`
  at 0.126 ms with `TCP_NODELAY`; a bridge network in front of a number that small would corrupt
  the row it exists to produce.
- **`/etc/localtime` is mounted because the image has no clock of its own.** No `/etc/localtime`
  and no `/usr/share/zoneinfo`, so glibc falls back to UTC — and a step-timing CSV, whose whole
  index is the stamp in its name, came out **8 hours** adrift of a run made outside the container
  on the same machine (host `Asia/Singapore` +0800 against container UTC). A `TZ` variable is not
  a substitute: resolving a zone *name* needs the zoneinfo database the image does not carry,
  while the mounted TZif file is all glibc needs when `TZ` is unset.

**Two tests were `skipif` on paths that do not exist in a container, and a gate that stops
running is worse than one that fails.** `test_conversion._metadrive_src` now falls back from the
checkout to the installed package — it reads MetaDrive's *files* rather than importing them, so
either directory does, and it finds it with `importlib.util.find_spec` rather than an import
because importing `metadrive` pulls in panda3d, which is the whole reason that module reads
files. `test_camera_rig` reads `rigs/cams.txt` out of the repo and is no longer conditional at
all. Both were silently skipping in the container; both run now.

**There is one sweep, not a host one and a container one, and no rebuild between them.**
`step-timing-docker.sh` and `container-check.sh` both end at `scripts/step-timing.sh`, which is the
only caller of `tools/step_timing.py` anywhere in the repo — so a change to either is picked up by
all three entry points. And the image copies in only `pyproject.toml`, `uv.lock`, `README.md` and
`src/` (`docker/Dockerfile:78-79`); `tools/` and `scripts/` exist in the container *solely* through
the `.:/work` bind mount, and the editable install points at `/work/src`, which the mount replaces.
So an edit to `tools/` or `scripts/` is live in there immediately — only a dependency change needs
`docker compose build`.

**Row 7 is the one row the container cannot run** — it opens a window and there is no display.
Everything else works in there, including `run-stages-*.sh` and `pytest`, because there is one
interpreter.

**The camera-rig spec is `rigs/cams.txt`, in the repo, and that is what makes `--camera-rig` the
same string everywhere (2026-08-22).** It used to live at `~/Desktop/work/wingfin/data/cams.txt`,
reached in the container only through a second bind mount (`RIG_DIR` → `/rig`), so one run was
`--camera-rig ~/Desktop/.../cams.txt` outside and `--camera-rig /rig/cams.txt` inside — and Keith
went looking for a `/rig` that exists only in there. `scripts/_common.sh:18` cds to the repo root
before a script does anything and the container works from `/work`, so a repo-relative path is
correct from `scripts/`, from the root and inside. **Not `config/`**, which is where the file
whose checksum feeds `generation_fingerprint` lives — the checksum is over the parsed
`ConverterConfig` (`generation.py:4007`) and a neighbouring file cannot move it, but putting a
vehicle spec there invites the question every time. **Not `docker/rig/`**, whose `.gitignore`
excludes everything but the note, so a spec there never travels to another machine; it stays as
the escape hatch for a spec deliberately kept out of the repo, which is what `RIG_DIR` is for.

**The sweep writes each row as it measures it, and that is not a nicety.** It used to collect
records in a list and open the CSV once at `main`'s last statement, so a twelve-row run with the
seven-camera rig — minutes of GPU time — left **nothing** when it was interrupted at row 11.
`step_timing.RowWriter` opens on the first row (so `--no-csv` and a sweep that measures nothing
still leave no file), flushes every row (microseconds against rows that are tens of seconds), and
the `KeyboardInterrupt` guard round the dataset loop names the file and says how many rows are in
it. `csv_path` took a `dataset_name` argument it never read — the path comes from
`arguments.dataset[0]` — which is what let the whole thing be settled, and the already-exists
refusal moved, **before** `prime` rather than after the sweep. Three things not to re-derive:
`wrote_anything` is a flag rather than `self._handle is not None`, because the line naming the
file is printed *after* the close and reading the handle silently removed it from every run;
the interrupt leaves through `os._exit(130)` after an explicit flush, because panda3d segfaults
tearing an engine down out from under a `KeyboardInterrupt` and the process was exiting 139;
and **`cmd &` in a non-interactive shell sets SIGINT to `SIG_IGN` in the child** (POSIX), so
five attempts to test the interrupt with `kill -INT` measured the shell rather than the code —
`( trap - INT; exec ... ) &` is what puts the default disposition back.

### The decision rate is a third dial, and MetaDrive has no clock for it (2026-08-22)

The question arrives shaped like CARLA's three knobs — **world tick / decision + camera /
physics** — and **MetaDrive has two**. `env.step` advances
`physics_world_step_size × decision_repeat` and returns, so it *is* the world tick, *is* the
policy call and *is* the camera draw; `base_engine.py:458` calls `task_manager.step()` once
per step unconditionally, and the sensor config is `name=(cls, *args)` with **no slot for a
rate anywhere**. So the middle column is not a key that was left unexposed. It is a **stride
counted in the caller's loop**, and `drive.decision_stride` / `drive.decides_on` are the only
places it is worked out and the only places the schedule is decided — both `drive.py` and
`step_timing.py` call them, because a benchmark running a different schedule from the tool it
prices is measuring nothing.

`--decision-hz` on `drive.py` and `step_timing.py`, `DECISION_HZ` in `.env` for `drive.sh`.
Unset it is the step rate, byte for byte the run there always was.

**Two arithmetic constraints, both refused rather than rounded.** Physics must be a whole
multiple of the world tick — a 100 Hz tick with 50 Hz physics is half a substep and does not
exist — and a decision a whole divisor of it, nothing moving between two steps. So of a grid
like `10/20/50`, `100/10/50`, `100/20/50`, `100/10/100`, `100/20/100`, only the last two can
be built; the first three are not configurations anyone is missing, they are arithmetic.

**The flag is `--decision-hz` and not the `--camera-hz` the plan named, and that is the
openpilot half of it.** `RemotePolicy` sends `step_seconds` to the server and
`OpenpilotDriver.spec` checks it against `_DT_MDL = 0.05`; a camera-only flag would have left
it at the env.step interval — 0.01 s at 100 Hz — with the bridge's lag compensation and
curvature-rate limit mis-scaled by 5× and nothing raising anything. It is now
`sim_step_seconds × stride`, **the interval between two calls**. Measured on `mosque` at
`--step-hz 100 --decision-hz 20`: **868 calls over 4337 steps, `arrive_dest=True`, completion
0.950, and no note from `spec`**; the same drive without the flag prints the note and makes
4336 calls. That pairing beats `convert --step-hz 20`, which CLAUDE.md used to recommend for
the bridge — same 0.05 s control interval, ten times the physics under it.

**No wire change was needed, and that is the flag's doing rather than an omission.** With the
decision gated there is no `/act` on a skipped step, so every call already carries fresh
sensors and there is no stale frame to omit. The rates ride in `/spec`'s existing `extra`.

**On a replay row the decision half is vacuous and the tool has to say so.**
`ReplayEgoCarPolicy` runs *in* the engine and MetaDrive calls it every `env.step`; nothing
outside can decimate that. So `--decision-hz` gates the reads and the camera draw alone there,
which is what the sweep's row 1 wants and is printed rather than implied.

**The draw is gated at the render call, not at the buffer — `tools/frame_gate.py` (2026-08-22,
second round).** An earlier version of this section said the draw could not be saved, on a
`buffer.set_active(False)` experiment that measured **1% of a 26 ms step**. That measurement
was right and the conclusion was wrong: **an `RGBCamera` owns two GraphicsOutputs and
`self.buffer` is the cheap one.** `rgb_camera.py:38-52` builds a
`FilterManager(self.buffer, self.cam)` and calls `render_scene_into(...)` with
`set_multisamples(16)`, which creates a **second** buffer
(`direct/filter/FilterManager.py:325-328`) hosted by the first; the scene — terrain, PBR, that
16x MSAA on top of the global 8x at `engine_core.py:96-103` — is drawn into *that*, and
`self.buffer` only draws a fullscreen quad over the result. Seven quads were switched off and
seven scene renders kept running. **Anyone reaching for `set_active` needs that fact first.**

`frame_gate` gates one level up instead, where nothing is left to infer: it rebinds
`engine.task_manager` to a forwarding proxy whose `step()` passes through only on a decision
step. That is exactly the two render calls in `BaseEngine.step` (`base_engine.py:455,458`),
because `base_engine.py:65` is `self.task_manager = self.taskMgr` and **every other render in
MetaDrive reaches the same object through `taskMgr`** — `base_engine.py:394,761`,
`base_env.py:439,534,569`, `main_camera.py:504` and `base_camera.py:188,193`, which is
`perceive(new_parent_node=...)`'s own second pass. So `env.reset`'s frames and `SensorPack`'s
extra render are untouched by construction. One `task_manager.step()` is one
`graphicsEngine.renderFrame()`, so a call that does not happen is a frame that is not drawn.

The read-back is held with it: `ImageObservation.observe` (`image_obs.py:80-88`) returns the
stack unrolled rather than calling `getScreenshot` again and rolling a duplicate frame in.
**Only the image half** — `ImageStateObservation.observe` composes `{"image", "state"}` and the
41-number state stays fresh every step, a vehicle state not being a camera.

Measured, `mosque` 100 Hz, row 1, `rigs/cams.txt`, 200 steps, **three runs of each**:

| | ms/step | x real |
|---|---|---|
| `100/100/100` | 26.11 | 0.34x |
| `100/20/100` | **6.21** | **1.47x** |
| `100/10/100` | **3.66** | **2.54x** |
| `100/20/100 --draw-every-step` | 26.94 | 0.36x |
| `100/10/100 --draw-every-step` | 26.69 | 0.37x |

The last two are the control and are what says where the money was: with the draw put back on
the world tick a lower decide rate is worth **under a millisecond of 26** — the read alone,
which is what the old section measured — and with it gated the same drive runs **4.2x** faster
at 20 Hz and **7.1x** at 10 Hz. `--draw-every-step` exists to keep that comparison available.

Four things not to re-derive:

- **It is verified by counting, not by timing.** Over a real 60-step drive at stride 5,
  `gate.draws` is 12, panda3d's own `globalClock.getFrameCount()` moves by 12 over the same
  window, all 48 held steps return an image bit-identical to the step before, all 12 drawn
  steps return a different one, and the state half moves on all 60.
- **`camera_draw_hz` is counted by the gate, not declared.** It used to be written as
  `step_hz` and was the one camera column a record never re-read off the live run — which is
  precisely the column that must not be taken on trust here.
- **`--render 3D` is never gated**, and that is a decision rather than an omission: the window
  is the point of that mode, `ForceFPS.real_time_simulation` steps the task manager inside the
  substep loop as well (`base_engine.py:454-455`), and `--agent-policy manual` polls the
  keyboard there. `install` returns `None` for anything that is not `_render_mode == offscreen`,
  so `--render none` — which has no cameras at all — is untouched too.
- **`ms/step` is a median over every step, so at a stride of 2 or more it is a *held* step**
  (1.15 ms) while `p95` is a drawn one (26.65). Neither describes the drive; `x real`, or
  `step_ms_mean` in the CSV, does. The two kinds of step are one distribution and the median
  lands in the larger half.
- **`drive.py --record` writes the held frame**, because the held frame is what the car had.
  A recording made at `--decision-hz 20` on a 100 Hz world carries each image five times, and
  that is the recording of a 20 Hz camera rather than a fault in it. The wire is unaffected:
  there is no `/act` on a skipped step, so a hosted model is only ever sent a fresh frame.

**`camera_rig.tick_rate` is now checked against the interval the cameras are really read at**
— `load_rig(path, read_interval_s=...)` — rather than against a hard-coded 0.1 s, so a 20 Hz
spec is correct under `--step-hz 100 --decision-hz 20` and wrong on an unflagged 10 Hz sweep.
`CameraRig.tick_rate_s` carries the declared value for a caller that cannot know the interval
yet: `sensor-survey.sh` drives one rate and refuses, `step-timing.sh` drives every rate a
workspace holds and notes per dataset. Still validation, never a resample.

**A sweep skips the dataset the rate does not divide rather than ending.** `--decision-hz 20`
divides `scenarionet-100hz` and not `scenarionet-10hz`, and the table shows the refusal beside
the row that ran — the same shape as the replay-rate mismatch already there.

**Several configurations go in a file, and they are driven in one process.**
`--rate-sets scripts/rate-sets.csv` is `name,step_hz,decision_hz,physics_hz`, one whole
configuration a row, into **one** CSV with a `rate_set` column. One process is the decision, not
a convenience: `prime` is paid once — the first env of a process is dearer than the ones after
it — and every machine column is identical by construction rather than by two runs happening to
agree. The file lives in `scripts/` and the path is the same inside the container and out, for
`rigs/cams.txt`'s reason. Four things not to re-derive:

- **A set drives only the dataset written at its own `step_hz`**, the one place `--rate-sets`
  behaves differently from the flags. A set is a whole configuration, so driving it against a
  tape at another rate measures something nobody asked for, and every replay row of it would
  skip anyway. Without a set the sweep still drives every rate a workspace holds.
- **A set's `physics_hz` outranks row 5's own 100 Hz pin**, so rows 2 and 5 coincide under any
  set that names one. Said in the footer once rather than per row.
- **`--rate-sets` refuses to sit beside `--step-hz` / `--decision-hz` / `--physics-hz`.** Two
  sources is how a CSV comes to describe a run that did not happen.
- **`drive()` takes the decision rate as an argument and must never read `arguments.decision_hz`
  again.** Under `--rate-sets` the rate lives on the set and the namespace holds `None`, so the
  first version ran every set at stride 1 while the table printed the rate the set asked for —
  a benchmark misreporting its own configuration. `test_the_timing_loop_takes_its_decision_rate_from_the_caller`
  walks the AST of `drive` and fails on that attribute.

**`policy_ms` / `sensor_ms` / `rig_ms` are per call, not per step, and that changed with this.**
They are collected only on the steps they happen on. Charging them to skipped steps too makes
the median *a skipped step* the moment the stride is 2: measured at `--decision-hz 10` on a
100 Hz world, `policy` printed **0.00 ms** against 0.20 at full rate, which is not the model
getting faster. `step_ms_*` is still every step, because `ms/step` is per step by definition.

**The container needs no rebuild for any of this.** The image copies in only `pyproject.toml`,
`uv.lock`, `README.md` and `src/`; `tools/`, `scripts/` and `rigs/` are live through the
`.:/work` bind mount. Verified: `step-timing-docker.sh mosque -- --step-hz 100 --decision-hz 20
--camera-rig rigs/cams.txt` ran on the RTX 4050 at 32768 px with `camera_count 7`,
`decision_hz 20`, `camera_draw_hz 20`, CSV owned by the caller — and the four-set batch there
reproduces the host's figures to within the noise (0.34x / 2.55x / 1.47x against 0.34x /
2.64x / 1.49x).

### openpilot drives through the same socket, and the route is a sensor now (2026-08-22)

`wing-sim/openpilot/bridge/zapeta/server.py` is a **controller, not a driver**: per tick it takes
a predicted path plus `v_ego` / `yaw_rate` / `steering_angle_deg` and returns `steer` / `throttle`
/ `brake`. It never sees an image — the thing that turns cameras into waypoints is a separate
CARLA-shaped AV3 model under `evaluation/src/inference_models/`. So filling stage 7c's empty
`act()` with it needed a path handed to it, and `tools/openpilot_policy.py` +
`examples/openpilot_server.py` are that translation. `--policy-url` and `step-timing.sh --rows 3`
reach it unchanged.

**`route` is a new `--sensors` name and it exists because the observation's route is unusable.**
The `[19:41]` navigation block is normalised and clipped at 30 m, and neither can be undone.
`SensorPack` sends `reference_trajectory` instead — the recorded route as a `PointLane`, the same
object `TrajectoryNavigation` steers by — 25 points at 2 m in **metres**, index 0 at the car's own
projection, ego frame **x ahead / y left**. A drive without it is refused by name rather than
coasting.

**`/spec` is sent before `env.reset()`** (`drive.py:885` against `:899`), so there is no ego and no
scenario when it goes: `SensorPack.describe`'s projection block has always been null, and the car's
steering geometry cannot be read there at all. `SensorPack.episode()` carries both, merged into
`/episode`. A controller that must be told what full lock means in degrees cannot get it any other
way.

Five things not to re-derive, each read off the bridge rather than assumed:

- **`carla_steer_curvature_gain: 0.0` is the whole fit.** It selects `server.py:788`,
  `-road_wheel_deg / max_steer_angle`, and `action[0] × max_steering` *is* the road-wheel angle in
  degrees (`base_vehicle.py:478`) — 40° for the default vehicle. Send MetaDrive's own `max_steering`
  as `max_steer_angle` and nothing is left to convert. The default path inverts an empirical gain
  measured on CARLA Town10HD.
- **Both ends negate**: MetaDrive is left-positive, CARLA right-positive. The waypoints' `y` is
  `-left` and the action's steering is `-steer`. Get one of the two wrong and the car drives
  smoothly into the oncoming carriageway with nothing raising anything.
- **`target_speed` defaults to 0, which is a stop.** `server.py:614` is
  `float(msg.get("target_speed", 0.0))` — an omitted target is not "no opinion". Sent every tick;
  `--target-speed-mps` sets it.
- **`steer_ratio` in `init` is stored and never used.** The bridge divides by its own
  `CP.steerRatio` on ingress (`:646`) and egress (`:788`), so the two cancel when ours matches and a
  mismatch mis-reports the wheel angle to the rate limiter rather than changing the output scale.
  12.0 is what wing-sim's own config sends.
- **The bridge is written for 20 Hz.** `_DT_MDL = 0.05` is what its lag compensation, its
  curvature-rate limit and its per-tick steer window are counted against; `OpenpilotDriver.spec`
  prints the ratio at any other rate. **`--step-hz 100 --decision-hz 20` is what matches it**
  (see the section above) — better than the `convert --step-hz 20` this used to recommend,
  the control interval being the same 0.05 s with ten times the physics under it. And **`accel_map.py` is CARLA pedal calibration** — two 8×11 tables from a
  "Town10HD calibration sweep on Tesla M3 @ 20 Hz sync" — so longitudinal tracking is wrong here
  until re-measured. Steering is not, because that path is geometric. Both halves are now
  measured against the real bridge — see the section below, where the pedal map answers a
  gentle braking request with a fifth of full throttle.

**`--backend stub` is a real socket, not a mock**, and is what proved the frame and the signs
before there was a fork to blame. Measured: `junction-1` **380 steps, arrive_dest=True,
completion 0.951** and `mosque` **435 / 0.951**, 0.5 ms and 2.5 KB a step, against
`--backend constant --steering 1.0` leaving the road in 13.

### The real bridge drives it, and only the steering half of the fit was right (2026-08-23)

The fork is on this machine now — `/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/`,
which is wing-sim's own `openpilot/` tree filled in: `bridge/zapeta/` plus the fork cloned at
`c767ace8` with its seven submodules. `--backend bridge` had been written against
`server.py` and never run; it works, and what it measured is worth not re-deriving.

**That build is now in this repo, fork and all** (2026-08-25) — `docker/openpilot/` carries the
Dockerfile byte-identical below its header, the 1649 lines of `bridge/`, and **`deps/openpilot/`:
309 MB of the fork itself, vendored** with its `.git` and seven submodule gitdir pointers removed,
because git will not track a directory containing one. `deps/openpilot/VENDORED.md` records the
commit and every submodule SHA. `scripts/bridge.sh` drives the build.

**Vendoring it needed `git add -f`, and that is the trap.** The fork ships 75 of its own
`.gitignore` files which exclude the prebuilt binaries the build links against —
`third_party/acados/x86_64/lib/{libacados,libblasfeo,libhpipm}.so` among them, which the lateral
and longitudinal MPC load. A plain `git add` skips them silently and the tree looks complete. The
two `.gitattributes` with `filter=lfs` also had to be commented out, or the 82 MB of
`selfdrive/modeld/models/*.onnx` would be pushed at an LFS server this repo does not have.

**Verified by building it**: `bridge.sh build` from the vendored tree produced a working image,
the acados prebuild linked `-lacados -lhpipm -lblasfeo` against the vendored `third_party/acados`,
and a full `junction-1` drive through it gave **8656 steps, `arrive_dest=True`, completion 0.950,
`result OK`** at 3.527 ms a call. The image is **5.5 GB**, not 6.17 — stripping the fork's 612 MB
`.git` took it out of the layer the Dockerfile `COPY`s.

The two hand-typed docker commands below are what `bridge.sh` runs, kept here because they are
what the measurements were made with:

```bash
cd scripts && ./bridge.sh build && ./bridge.sh start    # what the two lines below now are
# docker build -t wing-sim-openpilot:prod -f docker/openpilot/Dockerfile docker/openpilot
# docker run -d --name openpilot-bridge --network host \
#   -e SIMULATION=1 -e NOBOARD=1 -e SKIP_FW_QUERY=1 -e "FINGERPRINT=TESLA MODEL 3" \
#   -e OPENPILOT_TRAJECTORY_TYPE=0 -e BRIDGE_PORT=5558 \
#   -e PYTHONPATH=/opt/bridge:/opt/openpilot:/opt/project/common \
#   -w /opt/project wing-sim-openpilot:prod python3 -m zapeta.server
uv run python examples/openpilot_server.py --backend bridge --longitudinal accel --port 8642
# then, from inside scripts/
./drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --sensors imu,route --step-hz 100 --decision-hz 20 --render none
```

**The checkout arrives with every symlink missing, and scons is what tells you.** `git status`
inside the fork showed ten deletions — `rednose`, `laika`, `tinygrad`, `selfdrive/hardware` and
six `third_party` entries, all mode 120000 in the index — and the build died on
`Missing SConscript 'rednose/SConscript'`, which reads as a broken Dockerfile rather than a
transport that dropped symlinks. **`docker/openpilot/pull.sh` now repairs this itself** on every
run, re-deriving the ten from `ls-files -s` rather than naming them, so it should not recur.
`git checkout --` on those paths alone is the repair; the
`M` entries beside them are the LFS model files `pull.sh` deliberately does not pull, and must
be left. Docker copies symlinks as symlinks, so nothing else was needed.

**`AV3_MPC_MENU` defaults to `"4 16 20 32"` and `WAYPOINT_OFFSETS_S` is four waypoints**, so the
acados solver this repo needs is in the prebuilt menu — confirmed in the build log
(`[prebuild_lat_menu] done N=4`), not assumed. A count outside the menu is code-generated at
connect time and shows up as a long first tick, not an error.

**The steering fit is exactly right, and that is now measured rather than argued.** A 124.95°
column angle came back as `steer` 0.2603, which is `124.95 / 12 / 40` to four figures — the
geometric branch `carla_steer_curvature_gain: 0.0` selects, with `max_steer_angle: 40.0` and the
bridge's own `CP.steerRatio` cancelling ours. Both negations are right: with the longitudinal made
sign-correct the bridge completes **`junction-1` 0.950 and `mosque` 0.950**, the same completion
the stub reaches.

**The longitudinal fit is not usable, and `--longitudinal` is which of two wrongs to take.**
*(Superseded by `--longitudinal table`, below — the two wrongs both remain, and this is why.)*
`accel_map.accel_to_carla` returns throttle whenever `accel_cmd >= coast_accel(v_ego)`, and
`coast_accel` is the CARLA Tesla M3's *measured zero-throttle deceleration* — **−1.582 m/s² above
10 m/s**, −1.150 at 5, −1.377 at 3.5. MetaDrive's vehicle does not coast down anywhere near that
hard, so every request gentler than the M3's own drag comes back as throttle: `accel_cmd` −1.0
gives **throttle 0.274** at any speed over 10 m/s. Measured over the real drives:

| | decel requests | answered with throttle | v mean | outcome |
|---|---|---|---|---|
| `junction-1` `--longitudinal pedal` | 201 | **137 (68%)** | 16.4 m/s | ran away 13.9 → 20.5 m/s, **out_of_road at 4.08 m**, completion 0.529 |
| `mosque` `--longitudinal pedal` | 2469 | 11 (0%) | 3.5 m/s | arrived, 0.950 |
| `junction-1` `--longitudinal accel` | — | — | 4.4 m/s | arrived, 0.950 |

**It is speed that decides, not the map**, which is why `mosque` survived and reading one run
would have got this backwards: `mosque` sat at 3.5 m/s where `coast_accel` is −1.38 and its
requests averaged −1.42, so they braked. `junction-1` started at 13.9 m/s where coast is −1.58
and asked for −0.2 to −1.5, all of it above the crossover. **Nothing opposes the resulting
throttle**, because `waypoints_from_route` is `route_gt.py`'s constant-speed model by
construction — the trajectory says "I am going as fast as I am going", so in
`blended_except_creep` the e2e planner reads no intent to slow. That is faithful to wing-sim, not
a porting error: `route_gt.py` exists "to isolate whether drift is caused by the model or the
controller", and it is the *model* half that is still missing here.

**`--longitudinal accel` normalises `accel_cmd` by the Tesla envelope the bridge plans within**
(`TESLA_ACCEL_MAX/MIN`, +2.0 / −3.48, each direction by its own end). **It is not a calibration
either** — MetaDrive's `action[1]` is engine and brake *force*, not acceleration — and it
undershoots badly: 4.4 m/s mean against a 10 m/s target, 8726 steps where the stub takes 3788.
What it is, is sign-correct and unit-consistent, which on this simulator the pedal map is not, and
it is what makes the steering claim above measurable at all. `pedal` stays the default because it
is what the bridge emits and what a CARLA consumer gets; `--backend stub` answers in pedals only
and refuses `accel` by name.

Three things not to re-derive:

- **The container has no clock of its own** — its log stamps came out 8 hours off (UTC against
  `Asia/Singapore`). Mount `/etc/localtime` if a stamp from it is ever compared with a host one,
  the same fix `docker/compose` already carries for the step-timing image.
- **The bridge round trip is 3.5–3.8 ms a call** against the stub's 0.8, on the same 2.5 KB. That
  is the real MPC solving, and it is small beside `env.step` at 100 Hz.
- **`v_cruise_kph` arrives correctly** — 36.0 for `--target-speed-mps 10`, so a runaway is never
  the target failing to reach the bridge.

**`step_timing.drive` did not call `policy.start_episode`**, which was invisible only because
`SensorPack` re-reads the projection lazily. It does now. And **`--policy-sensors` overrides row 3's
`read` list rather than ROWS being edited**: what a hosted model is sent is the model's business,
and changing the row definition would make every CSV taken under it mean something else.

### The pedals are measured on MetaDrive's own car now (2026-08-23)

Stage 9 Phase 0, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.

**None of this is a controller, and the bridge already carried the component it replaces.**
The model decides where to go, the bridge decides how hard and which way — `accel_cmd` in
m/s² and a steering angle in degrees — and a pedal map decides only *how far to press a pedal
to get that acceleration on this car*. `server.py:788-792` does exactly two conversions
before replying, side by side: a road-wheel angle into a normalised steer, and
`accel_to_carla(self._last_actuators.accel, v_ego)` into a throttle and a brake. Both are
properties of the car, and `accel_map.coast_accel`'s docstring says which kind of thing it is
— *"Realized accel at zero control (engine braking), from the measured col 0."* **The
steering conversion came out free** because it is geometry: `action[0] × max_steering` *is*
the road-wheel angle in degrees (`base_vehicle.py:478`) and the geometric branch emits
`-road_wheel_deg / max_steer_angle`, both sides 40°. Pedal to acceleration is not geometry,
so it had to be measured — which is the whole of why one half of the fit was right and the
other was not.

`tools/pedal_sweep.py` measures the table, `tools/pedal_map.py` inverts it,
`calibration/metadrive-pedal-map.json` is the file, `--longitudinal table` is the third mode.
The fork is never touched — the reply already carries `accel_cmd` in m/s², so the conversion
is entirely on our side.

```bash
cd scripts && ./pedal-sweep.sh junction-1        # ~9 s, no GPU, no display
uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
```

**MetaDrive has no aerodynamic term at all**, which is the whole reason the CARLA table is
wrong here rather than merely imprecise. `_apply_throttle_brake` (`base_vehicle.py:493-520`)
applies a constant `setBrake(2.0)` to all four wheels *even under throttle* and nothing else
resists, so the car coasts at a **flat −0.364 m/s² at every speed** — a quarter of the −1.582
the bridge assumes. Above `max_speed_km_h` (80, so 22.22 m/s) engine force is cut to zero,
which is the one place the table's speed axis earns its keep; everywhere else the response is
speed-independent to within 6%. And **`max_engine_force` / `max_brake_force` are sampled**,
not constants — `BoxSpace(750, 850)` / `BoxSpace(80, 180)` at `pg_space.py:239-240` — measured
**759.464 / 89.464** identically on both extracts at both rates, because the parameter seed is
the scenario index and each of our datasets holds one scenario. The file records them and
every episode checks the live car against them.

**The sweep visits speeds; it must not let the pedal choose them.** Holding one pedal and
letting the car sweep the range is the obvious shape and the flat coast kills it: near the
pedal that cancels the coast (+0.036) the car would take **440 s and 4.9 km** to cross the
range, and the pedals either side never leave the end they start at. So the car is trimmed
*to* 23 speeds and all 41 pedals are probed at each — 2,829 steps, nine seconds.
`BulletPlaneShape(Vec3(0, 0, 1), 0)` (`terrain.py:179`) is **infinite**, so driving straight
for kilometres is fine and `map_region_size` never bounds it.

Measured against the real bridge, both extracts, `--step-hz 100 --decision-hz 20`. "hard
decels" are requests below the coast (`accel_cmd < −0.5`), where the sign is not in dispute;
"delivers" is the median `|produced − requested|` over every call:

| | calls | hard decels | answered with throttle | delivers | outcome |
|---|---|---|---|---|---|
| `junction-1` `pedal` | 262 | 153 | **89 (58%)** | 1.371 m/s² | out_of_road, 0.529 |
| `junction-1` `accel` | 1746 | 8 | 0 (0%) | 0.308 m/s² | arrived, 0.950 |
| `junction-1` `table` | 1559 | 195 | **0 (0%)** | **0.000 m/s²** | out_of_road, 0.815 |
| `mosque` `pedal` | 2427 | 2387 | **2158 (90%)** | 1.170 m/s² | arrived, 0.950 |
| `mosque` `accel` | 2836 | 1 | 0 (0%) | 0.362 m/s² | arrived, 0.950 |
| `mosque` `table` | 2498 | 85 | **0 (0%)** | **0.000 m/s²** | arrived, 0.950 |

Six things not to re-derive:

- **A pedal table does not fix the speed undershoot, and this was measured rather than
  hoped.** The mean speed barely moves — `junction-1` 4.41 → 4.19 m/s, `mosque` 3.06 → 3.47,
  against a 10 m/s target — because **the bridge is not asking to accelerate**: median
  `accel_cmd` −0.30 m/s², only 159 of 1559 calls positive. The target reaches it correctly
  (`v_cruise_kph` 36.0) and **doubling it makes the bridge brake harder**: at
  `--target-speed-mps 20` the cruise reads 72.0 and the median request falls to **−2.003**,
  with the car nearly stopped. So it is the longitudinal *plan*, upstream of any pedal
  conversion, exactly where the constant-speed `waypoints_from_route` above says it would be.
  That is the model's half.
- **A braking step that ends at zero is not a measurement of the brake**, and it is the one
  fault this sweep has. At 11.2 m/s² a 10 Hz step loses 1.12 m/s, so from 1 m/s the car
  reaches zero *inside* the step and the average reads −3.18 rather than −11.19. Before
  `TRUNCATION_FLOOR_MPS` existed that artefact alone put 60 cells out of order by up to
  0.90 m/s² and made the bottom four rows describe a car that cannot brake. A step is kept
  only when it ends above the floor **or** ends faster than it started — the second being a
  car pulling away from rest, which is the only real measurement the 0 m/s row can hold.
- **The bottom rows are filled, not measured, and the file says which.** 45 of 943 cells,
  none above 1.0 m/s, take the nearest measured speed; `sample_counts` is 0 for exactly
  those. A stationary car cannot be measured braking at all.
- **The crossover is +0.036 pedal, not 0.** That is the throttle that cancels the coast, and
  it is why a request between −0.364 and 0 correctly comes back as a *touch of throttle*. A
  naive "did a deceleration request produce throttle?" count therefore reads 67% against the
  table and means nothing — the honest test is requests **below** the coast, and the direct
  test is whether the chosen pedal delivers what was asked.
- **`--longitudinal` keeps all three.** `pedal` is what the bridge emits and what a CARLA
  consumer gets, so it must stay reproducible; `accel` is the sign-correct fallback where no
  table has been measured; only `table` is a calibration. `--backend stub` answers in pedals
  and carries no `accel_cmd`, so it refuses both of the others by name.
- **`--log-telemetry` now writes `v_ego_mps` and `metadrive_action`** beside the bridge's
  forty reply fields, because none of the above can be answered from the reply alone. The
  reply stays at the top level so an existing grep still works.

`junction-1` `table` ends `out_of_road` at −4.01 m lateral where `accel` arrives at 0.950.
Both steer through identical code, so the difference is where along the route the car is when
the lateral error accumulates. **Not diagnosed.**

### The AV3 checkpoint loads on this card, and takes about a second a pass (2026-08-24)

Stage 9 Phase C.1, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.
`tools/model_probe.py`, `scripts/model-probe.sh`, and an opt-in `model` dependency group.
It answers two questions and nothing else - it does not drive and writes no file.

```bash
uv sync --group sim --group gpu --group model
cd scripts && ./model-probe.sh                                # does it load, what does it cost
cd scripts && ./model-probe.sh junction-1 -- --with-simulator # the same, beside a renderer
```

**The checkpoint answers most of it without torch, a GPU or a download.**
`wingfin-openpilot-temp/assets/models/step_440000_trt_direct_full.ep` is a `pt2` zip, and
`model_probe.read_archive` reads its graph with `zipfile` and `json`: exported by
**`torch 2.8.0+cu128`**, taking `images (1, 5, 6, 3, 288, 512)`, `navigation (1, 20, 7)` and
`ego_state (1, 5, 2)` in **bfloat16**, returning **`(1, 20, 8)`**. Reading it first is also
what makes a failure legible - the probe prints what the file wanted beside what is installed
rather than letting TensorRT complain about a plan file.

That output line is **20 waypoints, not 4, and 8 wide**, and both halves correct the stage-9
plan. `av3_base.N_WAYPOINTS = 4` is a *fallback until `_set_output_shape` runs*, not this
model's count; 20 over `MODEL_HORIZON_S = 2.0` is 0.1 s spacing and is already in the
bridge's prebuilt `AV3_MPC_MENU` (`"4 16 20 32"`), so no code generation and no slow first
tick. 8 is `MODELV2_OUTPUT_WIDTH` - `[x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]` - so the
bridge's `msg["modelv2"]` / `from_predicted` path is reachable rather than the 3-wide
`derive` one `tools/openpilot_policy.py` sends today.

**It deserialises on sm_89, and that was the sharper of the two unknowns.** The archive is
**not weights**: `data/weights/model.pt` is **1,261 bytes** beside a **1,275,435,821-byte
serialized TensorRT engine**, and a TRT engine is built against one SM architecture and one
TensorRT version, so it either opens on this card or does not. It opens - RTX 4050, TensorRT
10.12.0.36, load 9-13 s (mostly reading 1.2 GB off disk).

**And it fits, with 1151 MiB to spare.** Measured on `junction-1` with `rigs/cams.txt`
mounted offscreen - the seven real cameras, 5.42 MB of image a step:

| card, of 6141 MiB | model alone | beside the simulator |
|---|---|---|
| simulator only | - | 2377 MiB |
| + model loaded | 2561 | 4934 |
| + one warm-up pass | 2617 | 4990 |
| **free** | **3524** | **1151** |

**The finding is the forward pass: 947-1002 ms** - medians over 10, 20 and 50 passes across
three runs, best single pass 919. A decision at `--decision-hz 20` has **50 ms**, so this is **20x** over, and a simulated second
will cost about twenty. It does not make a drive *wrong* - `env.step` is the tick, so a slow
policy makes a slow drive and nothing else - but every C.2 timing has to be read against it.

Eleven things not to re-derive:

- **It is not the timing loop.** Ten passes with `cuda.synchronize()` either side average
  **989.9 ms**; ten queued with one synchronize at the end average **999.3 ms**. The sync is
  not being charged for.
- **It is compute-bound and the card is capped at little more than half its rating.** 100%
  utilisation throughout, **34.6-35.1 W against `Current Power Limit` 35 W and
  `Max Power Limit` 60 W**, SM clock **975-1335 MHz against a 3105 MHz maximum**, 87-89 C
  against an 85 C target, and `nvidia-smi -q -d PERFORMANCE` counting 4,339 s of SW power
  capping beside 4,217 s of SW thermal slowdown. Reference point in the same state: a
  4096^3 **bf16 matmul runs at 14.4 TFLOP/s** (fp32 6.4). **The power limit is a machine
  setting and Keith's to change, not this repo's** - and it is not a fix either, since even
  a 2.5x uplift leaves ~400 ms against 50.
- **It opened because it was built portable, deliberately - and that is measured, not
  inferred.** Keith asked what made it open on a card it was not built on, which is the right
  question: an engine at `HardwareCompatibilityLevel.NONE` is locked to one architecture.
  This one is **`AMPERE_PLUS`**, read two independent ways that agree - the torch-tensorrt
  engine state's `HW_COMPATIBLE` field is `'1'`, and
  `trt.Runtime.deserialize_cuda_engine(...).hardware_compatibility_level` says `AMPERE_PLUS`
  - and the `CompilationSettings` pickled into `SERIALIZED_METADATA` show it was **asked
  for** rather than defaulted: `hardware_compatible: True`, beside
  `enabled_precisions: {bf16}`, `immutable_weights: True`, `version_compatible: False` and a
  `workspace_size` of 6 GiB, which is more than this whole card. **So it runs on any
  sm_80-or-newer NVIDIA GPU** - RTX 30/40/50-series and the datacentre Ampere+ parts - and
  **refuses** below that rather than running slowly. Moving this to a bigger machine is
  therefore safe in a way it could not be promised to be beforehand.
- **The portability is documented as costing speed, and that cost is NOT measured here.**
  NVIDIA's own documentation says `AMPERE_PLUS` restricts kernel selection to a portable
  subset. It is a plausible second contributor to the ~1 s pass beside the 35 W cap, and
  quantifying it would need a `NONE`-level rebuild - which cannot happen here, because the
  archive holds a compiled engine and a 1.26 KB weights stub with **no source model in it**.
  `refittable: False` / `immutable_weights: True`, so even the weights cannot be swapped.
  **A native rebuild is the only lever on the forward-pass cost that is not a machine power
  setting, and it belongs to whoever compiled the checkpoint.**
- **The engine's `DEVICE` field is not evidence of the build machine**, and read as such it
  is exactly the kind of wrong that looks like information. It reads
  `0%8%9%0%NVIDIA GeForce RTX 4050 Laptop GPU` - this laptop - because it is re-derived when
  the plan is deserialised. **No build GPU is recorded anywhere in the file.** With
  `AMPERE_PLUS` it stops mattering, which is the point.
- **Read the engine state off the already-loaded module, never by deserialising twice.**
  `_engine_state` takes it from `getattr(module, "<name>_engine").__getstate__()`, which is
  free; `trt.Runtime.deserialize_cuda_engine` is the obvious alternative and would put a
  second ~1.5 GB copy on a card with **1151 MiB spare** under `--with-simulator`. The cheap
  route is trusted because the two were cross-checked once on this engine. What it cannot do
  is separate `AMPERE_PLUS` from `SAME_COMPUTE_CAPABILITY` - the flag alone says only "not
  `NONE`" - so that is all the probe's line claims.
- **The plan is 956,574,460 raw bytes, 777 layers, and asks for 1578 MiB of scratch.** The
  1216 MiB in the archive is base64 (+33%), and `device_memory_size_v2` on top of the weights
  is what the measured ~2.5 GB of card is actually made of.
- **`uv sync --group model` on its own *removes* `sim` and `gpu`.** uv syncs exactly the
  groups named, so that line takes MetaDrive, panda3d and CuPy out and the next `./drive.sh`
  dies on a missing import. The line is `uv sync --group sim --group gpu --group model`, and
  all three coexist in **one** 3.10 environment.
- **numpy did not have to move.** `wing-sim/evaluation/pyproject.toml` pins `numpy==1.26.4`
  beside the identical torch pins; this repo stayed at **2.2.6** and torch 2.8 resolved
  against it. Adopting that pin defensively would have been the one change here able to break
  code that already works.
- **`torch_tensorrt.load` logs two failures before succeeding, and neither is an error.** It
  tries the `.pt2` package loader (*"f must be a buffer or a file ending in .pt2"*), then
  `torch.jit.load` (*"PytorchStreamReader failed locating file constants.pkl"*), then
  `torch.export.load` works. Reading either as the cause of a later problem is a wasted
  afternoon.
- **`torch/_export/serde`'s `ScalarType` is not `torch.ScalarType`.** They disagree from
  index 1: code **13 is `BFLOAT16` in the serde enum and `quint8` in the runtime one**, so
  reading a serialized graph with the runtime table mislabels every tensor in the report and
  raises nothing. `tests/unit/test_model_probe.py` asserts the baked table against torch's
  own copy wherever torch is installed.

**Two things `--group model` is deliberately not.** It is **not** a project dependency -
`uv sync` with no flags stays small, and nothing in `src/osm_scenario/` imports torch. And
the three versions are pinned **exactly**, to what the archive says compiled the engine,
because `>=` lets a resolve pick a stack that cannot open it and the failure then lands
minutes into a run. `test_model_probe` compares the pin against the archive rather than
against this paragraph, and all three of its `pyproject.toml` guards were shown to fail
against a broken file before being kept.

**`tools/model_probe.py` is the one file in `tools/` that is 3.10-only by construction** and
so is **not** parsed with MetaDrive's 3.8 interpreter before being believed: torch 2.8 has no
3.8 wheel, and it does not need one now that MetaDrive itself runs on 3.10. Its absence from
that check is deliberate rather than an oversight. `scripts/model-probe.sh` also skips
`select_gpu` on the plain run - CUDA finds the discrete card by itself, and the PRIME
variables exist for CUDA-GL interop - while `--with-simulator`, which builds a GL context,
goes through `exec_with_gpu` for the reason Phase B recorded.

**One thing left for C.2, stated rather than discovered later.** The 1151 MiB of headroom is
**not** measured against `--image-on-cuda`, which puts a CuPy context and the frame stack on
the same card.

**The camera-order note that used to stand here was wrong, and C.3 replaced the whole
question.** It said the model's `rear_right` is our `cam_back_left` (+125) - reasoned from the
model's camera *names*, which is the thing that note itself warned against. `rigs/cams.txt`
carries `y: 0.0` on all seven cameras, so its yaw column has nothing to be cross-checked
against, and it is self-inconsistent about the sign (`camera_rig.Camera.aim` records this).
`rigs/av3.txt` is built from wing-sim's own spec instead, where the names and the yaws agree
by construction - see the section below. `rigs/cams.txt` is untouched.


### The model is at the wheel now, and six conversions stand between it and the car (2026-08-25)

Stage 9 Phases C.3 and C.2, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.
`rigs/av3.txt`, `tools/av3_model.py`, `tools/av3_probe.py` / `scripts/av3-probe.sh`, and
`--camera-rig` / `--model-checkpoint` / `--waypoints` on `drive.py`.

```bash
uv sync --group sim --group gpu --group model
cd scripts && ./av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20   # nothing steers
# then, in two terminals:
uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
cd scripts && METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
    --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --model-checkpoint <the .ep> --sensors imu,route \
    --step-hz 100 --decision-hz 20 --render offscreen
```

**What this replaces is the *trajectory*, not the controller.** Every waypoint the bridge had
ever been sent came from `waypoints_from_route` - the recorded route resampled at the car's
own current speed, which is wing-sim's `route_gt.py` and a controller test by construction.
Phase 0 measured the cost: median `accel_cmd` **-0.30 m/s^2** with 159 of 1559 calls positive,
because a constant-speed path carries no speed *intent* for the longitudinal planner to read.
`--waypoints derive` keeps the old path, so every pre-C.2 measurement stays reproducible.

**Six conversions, and not one of them raises when it is wrong.** A mirrored route or a
swapped camera pair gives a model that loads, runs and returns twenty plausible waypoints.
That is the whole reason `av3_probe` exists and runs before anything steers.

**`rigs/av3.txt` exists because the mapping onto `rigs/cams.txt` could not be made safe, and
the note in this file that tried was wrong.** wing-sim's
`evaluation/configurations/validation_invariants.yml` states its mounts in the **vehicle**
frame the CAD uses - its own header says so: *origin at the rear-axle centre ON THE GROUND, x
forward, y LEFT, yaw CCW-positive, pitch quoted nose-DOWN* (ISO 8855 / REP-103) - and
`sensors/utils.py:transform_from_config` is the conversion it applies to reach CARLA:

    y -> -y     yaw -> -yaw     pitch -> -pitch     roll -> roll (unchanged, measured)
    x -> rear_axle_x + x        z -> ground_z + z

`rigs/av3.txt` is generated by applying exactly that, with the two datum shifts resolved
against MetaDrive's DefaultVehicle (`-REAR_WHEELBASE` = -1.4166, `-CHASSIS_TO_WHEEL_AXIS` =
-0.2, `base_vehicle.py:687`). Height above the **road** is what is preserved, not height above
the roofline - which is what wing-sim itself does when it resolves this rig onto a body it was
not measured on. `rigs/cams.txt` is untouched, because every step-timing figure in this repo
was priced with it.

Ten things not to re-derive:

- **The names and the aims now agree by construction, which `rigs/cams.txt` cannot manage.**
  That file carries `y: 0.0` on all seven cameras, so its yaw column has nothing to be
  cross-checked against, and it names its back pair the opposite of its own yaws
  (`camera_rig.Camera.aim` records this). wing-sim's spec has two independent columns that
  agree - `front_right` at `y -0.468, yaw -53.7` - which is what makes the frame readable at
  all. `test_av3_model` asserts every non-centre camera's name against its resolved aim.
- **The resize is NOT a no-op, and this file used to say it was.** The modifier squashes
  1440x1080 into 512x288 - a 4:3 frame compressed vertically by 1.33x, which is what the model
  was trained on. Rendering 512x288 natively gives a vertical field of view a third narrower
  with nothing raising, so `rigs/av3.txt` renders **512x384** and the preprocess does a real
  squash. Same picture as wing-sim's, 1/8 the pixels.
- **Pitch is now accepted and its sign was measured, not reasoned.** `camera_rig --check-frame`
  probes a pitched `NodePath`: panda3d's P is nose-up positive, which is CARLA's own
  convention, so it passes through untouched. It has to be read against the **car's own
  attitude** - a car under throttle sits nose-up on its suspension, and read against the world
  the same probe returns 9.89 rather than 10.00. **Roll is still refused**: wing-sim measured
  that it does *not* flip where y and yaw both do, and nothing here has checked that against
  MetaDrive.
- **MetaDrive's camera is BGR**, so conversion 1 is the fork's modifier verbatim rather than an
  adaptation. `BaseCamera.get_image` returns `get_rgb_array_cpu()` unchanged for `mode="bgr"`
  and reverses the last axis for `"rgb"` (`base_camera.py:110-113`), and `image_buffer.py:104`
  reads panda3d's BGRA RAM image. `test_av3_model` executes `modifiers.py` as a file and
  asserts pixel equality at 1440x1080, 512x384 and 512x288.
- **The ring holds uint8 and the ego state rides beside it.** At `--decision-hz 20` the stride
  is 10 and the depth 41, so 41 x 6 x 3 x 288 x 512 is **108.8 MB**; preprocessed float32 would
  be 435 MB for a picture that is 8-bit at the buffer. And the engine takes `(1, T, 2)`, not
  `(1, 2)` - `av3_base` buffers `_ego_buf` alongside `_image_buf` - so tiling the current speed
  T times would tell the model the car has been at this speed for two seconds.
- **Conversions 4 and 5 are one mirror.** MetaDrive is y-left / yaw-CCW; the model's frame is
  y-right / yaw-CW. So `y`, `sin(theta)`, `yaw`, `yaw_rate`, `v_y` and curvature negate
  **together** and `x`, `cos(theta)`, `v_x`, `a_x` do not. Half of it right is a car that
  steers smoothly into the oncoming carriageway.
- **Conversion 6 does NOT negate, and only the model could say so.** `waypoints_from_route`
  flips `y` because it starts from MetaDrive's left-positive route sensor; the model's output
  starts in its own training frame, which is already the bridge's. **A drive cannot settle
  this** - measured on `junction-1`, the drive statistic leans the wrong way (27% sign
  agreement, off-path 0.379 m as given against 0.385 m negated) because the model carries a
  standing **+1.6 m rightward bias** on this map, and a bias reads exactly like a mirror.
  `av3_probe --nav-sweep` settles it by holding every other input fixed and replacing the
  navigation with a 30 m arc: right-hand bend **+2.172 m**, left-hand **+1.062 m**, so +y is
  RIGHT and nothing flips.
- **`waypoints` is sent even under `--waypoints modelv2`.** `server.py:_handle_step` reads
  `msg["waypoints"]` first and returns a hard stop on an empty list, *before* it looks at
  `modelv2` at all. An empty list beside a full modelv2 block is a car that never moves.
- **`n_waypoints` goes in `/episode`, not `/act`.** The bridge builds its lateral MPC once, at
  connect, from `init`. 20 is in the prebuilt `AV3_MPC_MENU` ("4 16 20 32"), so there is no
  code-generation pause on the first tick. `RemotePolicy` gained `episode_extra` beside `extra`
  for exactly this: a per-episode field cannot ride on a per-step payload.
- **`--image-on-cuda` is deliberately not on this path.** It is refused above a stride of 1
  unless `--draw-every-step`, which throws away the 4.2x the frame gate is worth - and against
  a 1 s forward pass, Phase B's 3 ms is noise.

**The trajectory half works and the lateral is what ends the drive**, and the statistic to
read it by is the SPEED rather than the sign of `accel_cmd`. Phase 0 diagnosed a car crawling
at 4 m/s under a 36 km/h cruise because `route_gt`'s constant-speed path carried no intent;
measured against the **real bridge** on `junction-1` with `--longitudinal table`:

| | `route_gt` trajectory | the model |
|---|---|---|
| mean `v_ego` | 4.19 m/s | **8.92** (max 13.89, target 10) |
| median `accel_cmd` | -0.30 m/s^2 | -0.504 |
| completion | 0.815 | 0.163, `out_of_road` |

The pace doubles and the median request goes *more* negative, which is not a contradiction: a
car at its target speed correctly asks to hold, and that reads negative. Looking for the sign
to flip was the wrong criterion. What ends the drive is the lateral.

**And what the model does laterally on this map is a domain-gap reading, not a fault to fix
here.** Measured over 40 spread decisions of `junction-1`'s `test` route with the car
replayed: it predicts **16.5 m of travel in 2 s where the car covers 24.1**, and a lateral of
**0.12 m median** where the route bends 27 m at 38 m ahead - a slow, near-straight path with a
+1.6 m rightward bias. Four of its six cameras are 105.4 deg fisheyes standing in as
rectilinear at wing-sim's own unwarped `default_fov` of 70, and the road is a Kuala Lumpur OSM
extract rather than Town10HD. `av3_probe` reports all of it rather than averaging it away.

**`mosque` confirms every conversion independently, and corroborates the mechanism above.**
Conversions 2, 4 and 5 agree over **460** route points at worst 0.0000 m; the nav sweep gives
right **+1.500 m** against left **+0.582** - same sign, smaller response - and its standing
bias is **+1.041 m** against `junction-1`'s +1.617. On that map, with 14 of 23 sampled
decisions on a bend, the *drive-based* statistic recovers the right answer by itself: 72% sign
agreement, off-path 0.396 m as given against 0.598 m negated. So `junction-1`'s drive
statistic fails because the bias is large relative to the model's own lateral, not because a
drive is the wrong instrument in principle.

**Against `--backend stub` the two `--waypoints` modes are identical, and that is the control
rather than the flag failing**: `StubBridge.control` is pure pursuit over `msg["waypoints"]`
and never reads `modelv2`, so it cannot tell them apart. Only the real bridge branches on it
(`server.py:_handle_step`).

**A drive costs a quarter of an hour.** 947-1002 ms a forward pass (Phase C.1), one per
decision, and a full-length `junction-1` route at `--step-hz 100 --decision-hz 20` is 758 of
them. `env.step` is the tick, so this makes a drive slow and never wrong.

**`scripts/av3-probe.sh` and `--model-checkpoint` both need this repo's interpreter**, not the
3.8 checkout venv: torch 2.8 has no 3.8 wheel and does not need one, MetaDrive running on 3.10.
The probe script runs on it directly; `drive.sh` needs `METADRIVE_PYTHON=../.venv/bin/python`,
for Phase B's reason. `tools/av3_model.py` and `tools/av3_probe.py` join `tools/model_probe.py`
as the files that are 3.10-only by construction and so are **not** parsed with the 3.8
interpreter before being believed.


### Other cars are placed by this repo and driven by MetaDrive (2026-08-22)

Stage 8. `osm-scenario traffic` writes `workspaces/<ws>/traffic/traffic.json`; `drive.py
--traffic live` reads it and puts cars on the road. **The only thing this repo supplies is
placement.** Every route is built by `ego_route.plan_route` and `ego_route.route_polyline`,
unchanged and uncopied - so traffic drives the *same* junction geometry the recorded car does,
cubics laid between the two lanes' own tangents rather than the connector marker - and every
car is a MetaDrive vehicle running `TrajectoryIDMPolicy`, which takes a `PointLane` and
nothing else (`idm_policy.py:442`).

```bash
uv run osm-scenario traffic -w workspaces/junction-1 --count 60 --seed 1
cd scripts && ./drive.sh junction-1 -- --traffic live --traffic-count 25 --render 3D
```

**It is a file because of the interpreter, not because of taste** - the same reason
`signal_control.py:9` gives for the live light manager. The planner is 3.10 and the manager
runs in MetaDrive's 3.8 venv, so `tools/traffic.py` reads the numbers rather than importing
`osm_scenario.traffic_routes`. `traffic.json` carries geometry and **no timing at all**, so
one file serves every rate a workspace holds, exactly as one `routes.json` does.

**`traffic_env` takes a class; `live_signal_env` returns one.** That asymmetry is deliberate
and load-bearing: both are whole `ScenarioEnv` subclasses, so composing them by assignment
would leave only the last one standing, and `--traffic live --lights live` would silently drop
the lights. A red light is a physics wall and the **only** thing separating conflicting
movements, because IDM has no give-way rule. `drive.py` therefore passes whatever `--lights`
chose into `traffic_env`.

Six things not to re-derive:

- **A lane with no feeder is not automatically a place a car may appear.** `junction-1` has 19
  (not the 21 `CLAUDE.md` used to read), and only **11** are roads the extract cut; the other 8
  are starved lanes inside junctions, where a car materialises on tarmac other traffic is
  crossing and nothing raises. The test is on the node - *does any other lane end where this one
  begins* - and it was checked before it was trusted: **all 11** sit at an OSM node **outside**
  `source/map.osm`'s own `<bounds>`, which is what a truncated way looks like, while most of the
  8 are inside it. `mosque` splits 16 into 9 and 7. Exit lanes get **no** node test, deliberately:
  nothing appears there, so a lane that leads nowhere is a fine place to stop however it got that
  way.
- **`POLYLINE_TOLERANCE_M` is 5 mm and the 30-degrees gate pins it, not the file size.**
  `route_polyline` samples finely enough for `speed_profile` to read curvature - 55,842 points
  over 60 `junction-1` routes - and every one becomes a `PointLane` vertex for every car on that
  route. Measured worst vertex turn: **18.3 degrees** undecimated, **18.5 at 5 mm** for 23.4% of
  the points, **34.3 at 2 cm** (over the gate) and **47.9 at 5 cm**. The next tolerance up is not
  a cheaper version of this one; it is a different road. The file goes 3.2 MB -> 761 KB.
- **The manager keeps its own generator, seeded once and advanced per episode.**
  `BaseEngine.seed` reseeds every manager from the scenario index at each reset, and
  `junction-1` holds exactly **one** scenario - so `global_random_seed` is 0 on every reset and a
  manager drawing from `self.np_random` places identical traffic forever. Found by measuring two
  resets, not by reading. Same trap, same fix, as `signal_control.LiveSignalManager`.
- **A replacement enters at the *start* of a route, never partway along.** Every route in the
  pool begins at a lane the extract cut, so the start is the one place a car may arrive from off
  the map; dropped anywhere else it appears in the middle of a road other cars are on. Measured:
  across 24 episodes on both maps the road never once fell below the count asked for, under a
  replayed ego and under a slow `--agent-policy idm` one. **That is the fault that ruled out
  baking traffic into `tracks`** - a recorded track is as long as the episode, so the road empties
  around a slow agent - so it is the row that mattered.
- **Collisions are counted once per car per episode, not once per step.** A crash flag stays up
  while two cars are still touching, so a per-step count reports one collision as thirty and the
  number describes the frame rate.
- **Traffic is not in the dataset**, so a stock ScenarioNet consumer still sees an empty map - the
  same split as `--lights tape` against `--lights live`. And **it is what finally fills the lidar**:
  `Lidar.perceive` scans `physics_world.dynamic_world`, which is why the 120-laser block reads a
  constant 1.0 on a scenario holding one car.

### Three reasons the traffic looked like it was ignoring the road (2026-08-22, second round)

Keith: *"the cars look good, but they seem to be just driving around aimlessly on the grass,
i need them to follow lane and traffic rules and not bump into other vehicles."* Three separate
faults, none of them in the routes: `traffic.json` is unchanged and did not need regenerating,
and nothing in `src/osm_scenario/` moved, so no fingerprint moved either. All three are in
`tools/traffic.py`.

**1. The file's frame is not the simulator's, and everything written beside a pickle inherits
this.** `ScenarioDataManager` loads every scenario with `centralize=True`
(`scenario_data_manager.py:76`), which moves the whole world so the recorded car starts at the
origin, and records the move as `metadata.old_origin_in_current_coordinate`. `traffic.json` is
written in the *file's* frame, so a point handed to MetaDrive unshifted lands exactly that far
from the road it was computed for - **`[55.725, -75.469]` on `junction-1`, 93.8 m**. Measured
against the road surface itself rather than against a nearest-vertex distance, which is
meaningless when a lane feature carries only a polygon: **0 of 10 cars on the tarmac, a median
47.7 m clear of it, and 60 of 65 sampled route points off the road**; after, **10 of 10 and 0 of
65**. `_episode_shift` reads the field every reset, because the shift belongs to the *scenario*
and a dataset may hold several. `tools/geodesy.py:20` and `policy_client.py:160` already read the
same field for the same reason - traffic was the one thing written beside the pickle that did
not.

**2. `arrive_destination` is a circle around the last point, so a car that arrived wide never
arrived.** `TrajectoryIDMPolicy.arrive_destination` (`idm_policy.py:464`) is `DEST_REGION_RADIUS`
2 m from `traj.end` in the plane, and nothing else ends a car's run - `steering_control` asks
`heading_theta_at(long + 1)`, which clamps to the final segment, so a car that misses the circle
drives dead straight for ever through whatever is in front of it. Measured over three episodes of
25 cars: **36 cars ran past their last point and stayed**, against 27 retired by the circle, two
of them reaching **245 m and 131 m** clear of any road. `_past_the_end` measures the same margin
**along the route** instead. It is not a new constant - arriving is still `DEST_REGION_RADIUS`
from the end; it just stops asking the car to arrive laterally as well. Worst distance off the
road, same drive: **244.85 m -> 7.23 m**.

**3. `MIN_GAP_M` spaced cars along one route, and the pool has far more routes than the map has
ways in.** `junction-1`'s 60 routes start at **10 distinct points**, the busiest carrying 8, so
two routes are usually the same tarmac for their first hundred metres - and two cars on different
routes were spaced by nothing at all. Measured at reset: **closest pair 0.97 m**, four pairs under
5 m, and about half of every episode's collisions were the rear-end that followed. The rule is now
between the *cars* (`_free_at` takes a position, and `after_step` rebuilds the list from where the
cars actually are rather than from where their routes project them): **closest pair 15.19-20.11 m
over four resets, still 25 of 25 placed**. A car cannot see which route another car is following;
it can only see where it is.

### Traffic gives way where two routes cross, because IDM cannot see across its own lane

**`get_find_front_back_objs_single_lane` keeps only objects whose bounding box is on the
follower's own lane** (`idm_policy.py:161-164`, `lane.point_on_lane`). That is geometric, not by
lane identity, so a car ahead on the same tarmac *is* seen whatever route object it is following -
which is why rear-end collisions were a placement fault and not a driving one. But a car entering
from the side is on no part of that lane at any distance, so it is not an obstacle at all.
MetaDrive's own traffic manager never meets this: it replays recorded tracks driven by people who
did give way.

`_yielders` runs once per `before_step` and is the only thing in this repo that decides how a car
drives. It looks `YIELD_LOOKAHEAD_M` (40 m, about 3 s at 50 km/h) ahead along each car's own
route, finds the first place two look-aheads pass within `CONFLICT_WIDTH_M` (4 m) **at an angle**,
and holds one of the two back. `--traffic-give-way off` measures what it is worth.

Measured 25 cars, unsignalled, ego replayed. **Sixteen episodes on `junction-1` and twelve on
`mosque`, and the length is not padding**: a single `junction-1` episode ranges from 2 to 10
collisions with the rule off, so a five-episode window moves by more than the rule is worth and
an earlier version of this table read the difference backwards. The runs are exactly repeatable
across separate processes - the same episode list came back from two independent 8- and
16-episode runs of each column - which is the point of the tie-break below.

| | give way off | give way on |
|---|---|---|
| `junction-1`, collisions over 16 episodes | 79 (0.34 /car-min) | **60 (0.26)** |
| ... of which head-on, traffic only | 23 | **4** |
| ... of which crossing, traffic only | 47 | **38** |
| ... of which rear-end, traffic only | 0 | 12 |
| ... of which with the ego | 9 | **6** |
| `mosque`, collisions over 12 episodes | 24 (0.12 /car-min) | **9 (0.04)** |

**The head-on column is where the rule pays**, and it is not the column it was aimed at: a
give-way rule declines to act above `CROSSING_MAX_DEG`, so it never brakes for a head-on. What it
removes is the *crossing* collision upstream that knocks a car into the oncoming carriageway in
the first place. 23 to 4.

**The rear-end count goes up and that is the rule's own doing**, not noise: a car that brakes for
a crossing is a car the one behind it has to brake for, and `do_speed_control` runs a fifth of
the cars per step (`IDM_ACT_BATCH_SIZE`), so a follower can be up to 0.5 s late noticing. It is
still a net 19 fewer, and the twelve it costs are the shunt rather than the T-bone.

Cars retired per episode is unchanged either way, which is the check that matters against a rule
that brakes: nothing is gridlocked, the same traffic completes the same routes.

**It costs 3.1 ms a step at 25 cars** - 11.3 ms against 14.4 on `junction-1` headless - and the
first version cost **6.9**. Two prunings halved it, and neither was the obvious one: `_look_ahead`
was vectorised with `searchsorted` (`_pose_at` walks a ~900-vertex route from the start, and it
was being called 525 times a step) for 1 ms, and `_conflict` gained a bounding-box rejection and
stopped computing the crossing angle over the whole 21x21 grid rather than over the samples that
are actually close, for 2.8 ms. The arrays it reads are built once a reset in `_localised_routes`.

Five things not to re-derive:

- **The angle band is what makes it safe to run at all.** Below `CROSSING_MIN_DEG` (30 deg) two
  paths are running together - the same lane, or a merge - and treating that as a conflict would
  have a follower and its leader each waiting for the other for ever. IDM already owns that case,
  per `point_on_lane` above. Above `CROSSING_MAX_DEG` (150 deg) is head-on, which a give-way rule
  cannot fix and a correct one-way lane model does not produce.
- **The nearer car goes, decided on distance and not on time.** A car that has stopped has an
  infinite time to arrive, so a time-based priority makes it give way to everything for ever -
  including to the car that is waiting for it.
- **Ties break on the spawn ordinal, and `vehicle.name` will not do**, which cost a full round of
  measurement to find. `nameable.py:12` is `self.name = str(uuid.uuid4())` - a fresh random id
  every process - so a tie broken on it sends a different car first on every run, and the physics
  amplifies that from there. Measured: with the rule **off** the same five episodes gave **26
  collisions four times over**, and with it **on** they gave **13, 19, 20 and 22**. It was the
  give-way column being the only unrepeatable one that gave it away. On the ordinal, three runs
  of each now give 26 and 18 exactly. Anything in this repo that breaks a tie between two
  MetaDrive objects has the same trap waiting in it.
- **Giving way can only ever slow a car down.** `before_step` takes `min(idm_acc, brake)`;
  steering, following distance and everything else is still MetaDrive's. `_yield_brake` sizes the
  brake from the room left and the car's own speed, so a stationary car asks for nothing and is
  held by the throttle cap alone.
- **Traffic gives way to the ego as well, and the ego is never the one braked.** The ego is not
  in the plan, so nothing in the look-ahead could see it: 9 of `junction-1`'s 79 collisions were
  with it. `_ego_look_ahead` extrapolates a straight line from where it is going rather than
  reading its recorded track - the tape is the ego's future only under `--agent-policy replay`,
  and `idm`, `manual` and `remote` all drive it somewhere else, so a straight line is right
  enough for all four over the second that decides a give-way and wrong in the same way for all
  four. Worth **67 to 60** over 16 episodes, measured on its own by disabling that one method.
  The ego never receives a brake: under replay it is a tape and cannot yield, and under any other
  policy it brakes for its own reasons.
- **It is measured by counting collisions, not by counting yields.** Over a five-episode run the
  rule holds a car back on a few hundred car-steps out of 8,800; genuine crossings are rare on a
  map this size, and a third of the collisions going for that handful of interventions is the
  result, not a sign it is not firing.
- **Traffic stops at a red without any of this**, and that was checked rather than assumed:
  `TrajectoryIDMPolicy.act` has no light logic, but a MetaDrive light is a physical object on the
  lane, so `get_find_front_back_objs_single_lane` returns it as the front object and
  `acceleration()` brakes for it. It is the same path that already stops the ego 5.7 m short of a
  red under `--agent-policy idm`.

### Nothing steers a traffic car by the road, so it has to be slowed for the corner (2026-08-23)

Keith, after the three fixes above: *"although the vehicles keep within the lanes, there are
still some instances of them going onto the grass, why does this happen?"*

**No part of MetaDrive is keeping a traffic car on the road, and there is nothing to
misconfigure.** `TrajectoryIDMPolicy.act` returns two numbers: an acceleration from IDM's
car-following, and a steering angle from `steering_control` (`idm_policy.py:463`), which is a
heading PID aimed at `heading_theta_at(long + 1)` - a **fixed 1 m preview** - plus a lateral
PID on the projection error. Road edges, lane lines and the drivable surface are not inputs to
it. So "the lane is clear" cannot help: it is tracking a polyline, not reading a road. And
nothing notices when it fails - `out_of_road` termination is the **ego's**, and a traffic car
has no road constraint at all.

Measured, `junction-1`, three episodes of 25 cars, before this change:

- **26 cars left the tarmac and 26 of 26 were touching nothing at the time.** Not collisions.
- Tracking is excellent until it is not: **median lateral error 0.08 m**, p90 2.16 m, worst 16 m.
- **`NORMAL_SPEED` is 40 km/h, flat, everywhere** - while these are the same routes the ego
  drives under `speed_profile`. **29.5% of `junction-1`'s 51.8 km of route distance allows less
  than 40 km/h on curvature alone**; every one of the 60 routes has a point allowing under
  20 km/h and 29 of 60 go under 10 (slowest point: median 10.2 km/h).
- At the moment of departure **17 of 26** were faster than the corner allows - median **29.8
  km/h into a corner allowing 19.5**. The other candidate, a backward jump in
  `PointLane.local_coordinates`, accounted for 2 of 26.

**And the routes are partly at fault, which is worth separating from the controller.** They
are on the road - only **95.1 m of 51,766 m** (0.18%) lies off the drivable surface, worst
1.97 m - but they are not all drivable. Measured over a window the length of the car, against
its own minimum turning radius of **2.94 m** (wheelbase 2.47 m at 40 deg of lock): routes with
a lane change have a tightest radius of **2.00 m median**, 28 of 45 tighter than the car can
physically turn; routes without measure **6.02 m**, 2 of 15. That is `ego_route._lane_change`
fitting a 3.5 m shift inside one pair of 5.8-7 m lanes, already recorded above as why the ego
crawls to 10 km/h on a lane change. It is **not** the main trigger, and that was checked rather
than assumed: 78% of cars drove lane-change routes and 69% of departures were on them, so they
are slightly *under*-represented, and only 10 of 26 departures were within 20 m of a kink
tighter than the car can turn.

`traffic.json` gained a **speed profile** per route (`traffic_version` 2 - a version 1 file is
refused by name, because it would drive every corner at 40 km/h), min-pooled to
`SPEED_STEP_M` 2 m from `ego_route.speed_profile`'s own 0.1 m, and `tools/traffic.py` writes it
to `policy.target_speed` each step. `--traffic-speed flat` measures what it is worth.

| `junction-1`, 25 cars, 5 episodes | before | after |
|---|---|---|
| cars that left the tarmac | 41 | **25** |
| worst distance off it | 9.39 m | **3.80 m** |
| collisions | 20 | **17** |
| routes completed | 48 | 35 |
| mean speed | 29.2 km/h | 17.6 km/h |

| `mosque`, same | before | after |
|---|---|---|
| cars that left the tarmac | 56 | **24** |
| worst distance off it | 9.51 m | **3.08 m** |
| collisions | 5 | **0** |
| routes completed | 44 | 24 |
| mean speed | 33.4 km/h | 18.0 km/h |

Six things not to re-derive:

- **`TRAFFIC_LATERAL_ACCEL_MPS2` is 4.0 against `ego_route`'s 8.5, and the sweep is monotonic
  and steep.** Same five episodes, cars off the tarmac and the worst distance: 8.5 gives **54
  and 45.22 m**, 6.0 gives **38 and 12.36 m**, 4.0 gives **27 and 3.76 m**. 8.5 is not a comfort
  figure - it is pinned to the ego's 30-degrees-per-step gate - and it works for the ego because
  the ego's positions are **replayed**, so nothing has to steer to them.
- **The pace and the throughput are what it costs, and the cost is real**: mean speed roughly
  halves and routes completed per five episodes fall from 48 to 35. That is the trade for
  keeping cars on the road with a controller that has a 1 m preview. It is one constant and a
  `osm-scenario traffic` rebuild away if a future map wants it different.
- **Min-pooled, never sampled.** A sample can land either side of the one tight vertex in a
  junction and report the speed of the straight beside it. The raw profile is 517,750 samples
  over 60 routes and would be most of the file; pooled at 2 m it is 25,887, and the file goes
  761 KB to 1.17 MB.
- **`_allowed_mps` reads the pool the car is *in*, and must not interpolate toward the next
  one** - on the approach to a corner that hands back a speed the corner does not allow.
- **`target_speed` is set before `policy.act` reads it.** `act` computes an acceleration on one
  step in five (`IDM_ACT_BATCH_SIZE`), so a step late is up to half a second late into a corner.
  Pinned by a test that walks the AST.
- **A car more than `LOST_LATERAL_M` (5 m) off its own route is taken off the map**, and
  replaced at a route start like an arrival - but counted as `cars_lost`, never as a completed
  route. With the profile in force the lateral error is 0.11 m at the median and 1.80 m at p90,
  with **7 excursions past 5 m against 11 past 3 m**, so 3 m would pick up cars still going
  round a junction. **It confounds any collision measurement taken with it on**: it culls
  exactly the cars that were about to crash, and the first sweep of the profile read 5
  collisions against 17 purely because the flat column had 33 cars removed and the profile
  column 4. Isolate it before comparing anything.

**Traffic stopping at a red is the one thing not measured**, and it cannot be here:
`workspaces/junction-1/signals/signals.json` is bound to an older lane model and `convert`
refuses it by fingerprint. Re-draw the phases in `inspection/stage-6-signal-builder.html` and
rebuild with `--signals` - choosing signal timing is a person's job because OSM supplies none.

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
- **A road past the line further back than `merge_taper_length_m` is left alone entirely.**
  Pulling a 70 m lane sideways is not a merge correction. This was read as two carriageways of
  different widths mapped as separate ways — `mosque` way `935525163` running 1.75 m off, half a
  lane, for 115.6 m across four merges — and **that reading was wrong**: `935525163` is a
  two-lane stretch of Persiaran Perdana between three-lane stretches of it, so its block sat half
  a lane off both. v25's `_aligned_blocks` puts it where the road it carries on from is, and the
  four names the workspace-backed test used to exclude are gone from it.
- **Only a single continuation counts.** `entry_lanes` / `exit_lanes` name a lane for a direct
  continuation and a connector for a junction movement; a fork has no one road behind it, and a
  junction movement is another lane's traffic rather than this road carrying on.
- **The pull runs before `_tapered_line`**, so the taper's move is along the lane rather than
  back out across it. `_tapered_line` itself is unchanged, and `topology.py` is not involved.

10 lanes move on `mosque` and 8 on `junction-1`; three on each are lanes the merge code did not
previously own. See
`docs/mapping-algo-changes/2026-08-15-19:15:13-a-merging-road-crossed-the-lane-it-was-joining.md`.

### Where a lane block sits across the way line is surveyed, not inferred (v24)

`_lane_offset` **centres** a one-way carriageway's block on the OSM line. That is an inference
about where the tarmac is, applied to each way on its own — so two ways drawn on the same line
with different lane counts get blocks of different widths balanced about the same point, and
every lane that continues between them steps **half a lane-width** sideways on a road that is
dead straight. `_merge_taper_plan` reads that step as a gap and `_tapered_line` spends it; at
`merge_taper_length_m` 30 m against a 24.4 m lane the taper is longer than the lane, so the whole
lane becomes the slope rather than a straight line with a bend in it. Keith reported it as lanes
that kink instead of following the centreline.

**`placement` is the survey of it and was never read.** `placement=middle_of:2` on mosque way
776079597 puts idx0 at +0.00 and idx1 at +3.50 — exactly where the three-lane approach's
surviving lanes already are. Four ways carry the tag across both extracts, all Persiaran Perdana:
mosque 776079597 and 1250683199 (`middle_of:2`) and 776022253 (`right_of:2`, in both). Reading it
took the bend at six straight joins from 4.02°/5.29°/2.60° to ≤ 0.09°, with no connector, finding
or status changed. Four things not to re-derive:

- **OSM numbers placement lanes 1..n from the left in the way's direction.** With
  `driving_side=left` our idx0 is offside, which is the *right* of travel, so idx0 is OSM lane
  `count − idx`; right-hand traffic makes it `idx + 1`. The driving side renames lanes rather
  than moving tarmac, so the two orders **reverse** rather than negate — unlike the centred
  layout, where the block is symmetric and reversing and negating are the same thing.
- **One-way carriageways only.** On a two-way way the tag numbers lanes across both directions
  and the backward block runs the other way, flipping what "left" means. Neither extract has that
  case, and a block on the wrong side of the road is worse than one centred on the line.
- **`transition`, an out-of-range lane number and anything unparseable fall back to centring.**
- **A lane that stops being tapered moves.** Untagged way 776022254 is straight now that 776022253
  sits where the tag says, and the 2.6° its taper used to spend has moved to its junction with way
  776021086, where the road genuinely turns +5.07°. Ramp 182502392's `_uncrossed_lanes` pull aims
  at the new position, redistributing its interior bends (22.53°→13.05°, 19.50°→29.92°) while its
  worst stays 35.08°.

That left **19 straight joins on mosque and 9 on junction-1** still stepping, on ways OSM never
tagged — the second half of what Keith reported, fixed in v25 below. See
`docs/mapping-algo-changes/2026-08-15-22:21:37-a-lane-block-was-centred-where-the-survey-placed-it.md`.

### A road that carries on takes its position from the road behind it (v25)

Where a block sits is decided per way, from that way's own tags — a local decision about
something that is not local. **A road is a chain of ways, and where its tarmac lies is a property
of the road.** `_aligned_blocks` runs immediately before `_merge_taper_plan`, works out which
block feeds which, and where a block's position is settled by the road behind it, translates it
there. It moves centrelines only; the `redrawn` set and the single `_lane_surface` rebuild below
it already re-derive the surfaces.

It cannot run while the lanes are built — it reads the feeder graph, which is not settled until
every movement has been filtered, restored, side-resolved and either kept or forbidden — and it
must run before the taper, so the taper finds nothing left to close. Blast radius: **10 of 405
mosque lanes** (ways 776021087 and 935525163) and **4 of 285 junction-1 lanes** (776021087), all
by exactly 1.75 m, `aligned_lanes` in `feature_counts`.

Three guards, and **each is a road this moved wrongly before the guard existed**:

- **The destination must have exactly one feeder.** More than one is a merge, where the joining
  way has its own line and closing the gap is the taper's job. A source that *also* goes
  elsewhere is fine — a lane peeling off is why the counts differ at all.
- **Both sides must be centred one-way carriageways.** A two-way block sits half a carriageway
  off its line *by design*, so comparing it to a centred one reads the straddle as an error.
  Without this, junction-1's one-way `106667716` was shifted 1.75 m off its own line across seven
  edges to suit two-way `1016771782`.
- **The join must be straight**, measured by `_join_turn_degrees` because a continuation link
  carries no angle. `1016771782` into `106667716` bends **22.24°** and is still a direct
  continuation: "carries straight on at a node that is not a decision node" is a far looser test
  than "the same block position applies to both".

A join whose lane pairs disagree is skipped — mosque `777816410` into `777816409` pairs idx0→idx0
and idx1→idx2, a lane appearing *between* them, and no translation satisfies both. Within a
component a `placement`-tagged block never moves (the survey outranks the inference) and
otherwise **the widest block stays**, the same rule `_merge_taper_plan` applies; anchoring on the
majority instead moved three-lane 935525164 to suit the two-lane stretch beside it.

**`ALIGNMENT_MAX_TURN_DEG` is 10.0 and is not `side_movement_min_degrees`.** `classify_movement`
calls anything under 35° `through`, and the lane peeling off at node 13946726031 leaves at
−17.79° as a `through` movement. Swept: 5° and 10° agree exactly, 15° pulls in way 859429322, and
**20° pulls in the slip roads** (182502392, 1530245743, 182502406, 182502423, 191861354), which
must never be dragged onto the road they join.

Two costs worth not re-deriving. Moving a block further from its way line **opens the mitre at
that way's own interior bends** — 776021087's two edges go from 3.09°/0.133 m to 4.36°/0.265 m —
which is unavoidable for any lateral shift and lands mid-distribution (median gap at a direct
continuation is 0.213 m, median bend 4.92°). And the remaining steps are all at merges, where the
taper is correct. See
`docs/mapping-algo-changes/2026-08-16-01:58:09-a-road-that-carries-on-re-centred-on-its-own-line.md`.

### Two carriageways going opposite ways get a median, and roads shove each other over (v26)

`_lane_offset` decides where a block sits across its way from **that way's own tags alone**, with no
knowledge of what else is already in the corridor, and nothing downstream ever asks. v24 read
`placement`, v25 read the road behind — both about one road against its own line. Neither can see
the road on the other side of the median, so mosque's median right-turn link `859429322` /
`859429321` was laid **3.03 m inside** Persiaran Perdana's SW carriageway. Keith: *"these lanes eat
into the south-west bound lanes going in the opposite direction… they don't have to be touching."*
35 opposing pairs overlapped on mosque and 19 on junction-1; all of them clear **1.00 m** now.

`_separated_roads` runs between `_aligned_blocks` and `_merge_taper_plan` and moves a **whole road
bodily kerbward** — centrelines only, so nothing bends. Kerbward is always the direction: an
opposing carriageway is on a lane's offside, so both roads moving kerbward always opens the gap,
every shift is ≥ 0, and an opposing pair with room needs no constraint at all. Demands (opposing,
offside) must reach `SEPARATION_TARGET_M`; pushes (same direction, kerbward) bound the difference by
`max(clearance, 0)` so a slip already landing on the lane it joins is only stopped from getting
worse; squeezes (opposing, kerbside) bound the sum. A shortfall goes to the road with **fewer
lanes**, the rule `_merge_taper_plan` and `_aligned_blocks` already apply.

Six things not to re-derive, each a version that was built and measured first:

- **The unit that moves is coarser than an `_aligned_blocks` component.** Those chain across
  straight *single-feeder* joins, so a way splits wherever something merges into it partway along;
  different shifts on the halves opened a **4.03 m step inside way `859429321`**, three inside
  `756118317` and one inside `1016771782` — the v24/v25 defect returning. `_road_components` makes
  every block of one way in one direction one road **unconditionally**, then chains continuations
  and shallow `through` connectors on top.
- **A road is a whole street, both directions of it, and getting that wrong tore 22 of mosque's
  two-way ways open along their own centreline.** Kerbward is north for a street's eastbound half
  and south for its westbound one, so a shift given to one and not the other parts them. The
  same-OSM-way exclusion below cannot catch it: a street's seam is a property of the *street*, so
  where OSM splits one into two ways the seam is read **across the boundary** — two readings of
  **1 mm and 4 mm** moved a 26-lane road 1.001 m and a 2-lane road 1.006 m. Both directions of a
  way are now one road (33 roads on mosque, 23 on junction-1), which makes the seam a same-road
  pair, and `_two_way_roads` pins those roads to a budget of **zero**, because the only shift that
  keeps a street whole is none. It costs nothing measurable: **no separation demand on either
  extract touches a two-way street** — a street shields itself, its two halves occupying each
  other's offside. Where one ever does, a demand whose yielding road is out of budget passes to
  the other road; where both are streets, the warning reports it. See
  `docs/mapping-algo-changes/2026-08-16-18:10:01-a-two-way-street-was-parted-down-its-own-middle.md`.
- **A nearest point on the other lane's own end means they are not alongside each other.** Two lanes
  running away from a shared node meet there, and the perpendicular part of a mostly-along distance
  says nothing: junction-1's `776021086` and `1530245742` are **13.35 m apart** and read as 2.98 m
  of overlap without the guard. `tiny.osm`'s ways 10 and 11 are the same shape, and the fixture test
  is what caught it.
- **One reading of the geometry is not enough** — solved once it left 16 overlaps on mosque and 3 on
  junction-1 and made three pairs *worse* (`182502377` × `191861354`, clear → 2.51 m of overlap).
  Roads meeting at an angle lose part of each shift to it, and moving a road changes *where* two
  roads come closest, so it re-reads and re-solves up to `SEPARATION_ROUNDS`.
- **A shift may be negative, or a road drifts.** Add-only rounds left the link **4.68 m** clear of a
  carriageway it was asked to clear by 1.00 and squeezed the other side by 0.98 m. Each round now
  has a floor (how far the road has already moved) as well as a ceiling, and *every* opposing pair
  carries a demand — negative where there is slack — so the layout knows what may be given back.
- **Each round lays every road out from where it started.** `offset_curve` is not its own inverse on
  a curved line.
- **Four kinds of pair abut by design and are excluded**: same road; **same OSM way**, because a
  two-way way puts its two directions either side of its line and they meet *on* it; joined by a
  connector or continuation; and cut back to `MIN_TRIMMED_LANE_M`, the interior of a junction where
  traffic crosses. The last is the clamp, **not** a length threshold — the distinction
  `conversion._stub_lanes` draws — and alone it takes mosque's demand set from 124 pairs to 104.

Blast radius **161 of 405 mosque lanes over 10 roads and 132 of 285 junction-1 lanes over 7**, worst
shift 4.36 m and 2.72 m, reported as `separated_lanes` / `separated_roads`. Perdana's SW carriageway
does not move at all on mosque — the fewer-lanes rule put it on the link and the NE side. Ids,
counts and connector statuses are unchanged, and **continuity improved**: total sideways step at a
direct continuation 47.63 → 42.40 m on mosque and 44.13 → 40.77 m on junction-1. See
`docs/mapping-algo-changes/2026-08-16-14:32:44-a-carriageway-was-laid-over-the-traffic-coming-the-other-way.md`.

### `ego_route` still turns over the gate on two 2 m clamped lanes

`test_no_route_on_the_real_map_turns_more_than_the_gate_allows` fails: **3 of 396 swept routes
turn more than 30° at a vertex, worst 50.92°**. It is not v24 and not v25 — sweeping the same
1,500 seeded pairs on models built at v23, v24 and v25 gives the identical three routes. It
became visible on 2026-08-16 only because `workspaces/junction-1/lane-model/reviewed.json`, which
that test reads, was rebuilt; the model it had been passing against was **v17**.

All three run 777160375 idx0/3 → 777159293 idx0/1 → 777160374 idx0/1 → 777159294 idx0/2, where a
**−88.97°** right turn is taken across two lanes `MIN_TRIMMED_LANE_M` clamped to **2.0 m**, with
2.6 m and 10.6 m gaps either side. Undiagnosed further, and Keith's to judge.

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
