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
  __GLX_VENDOR_LIBRARY_NAME=nvidia`, which is what `scripts/drive.sh` sets and why
  the switch lives in the shell rather than in `drive.py`. Nothing needs installing;
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
| `3D` | `camera,imu,gps` | 901.5 KB | 14.98 ms |
| `offscreen` | — | **3600.4 KB** | 29.35 ms |
| `offscreen` | everything | 5001.2 KB | 49.03 ms |

**`--render offscreen` costs 3.6 MB a step with no sensors asked for**, because it forces
`image_observation` and the observation becomes a 3-frame camera stack (320×240 by default, hence
3600 KB; a 320×180 camera gives 2700 KB). `none` and `3D` both keep it at 161 floats — which is
why the 3D row *with* a camera is cheaper than the offscreen row without one. `drive.py` prints
KB/step so this is a number rather than a mystery.

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
