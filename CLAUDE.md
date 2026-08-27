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

## B2. What belongs in this file, and what belongs in `docs/reference/`

This file is loaded into context **in full** at the start of every session. A fact that is
not needed in *every* session does not belong in it. It reached 223 KB by append — 51
commits over 18 days, +3,350 lines against −222, never once shrinking — because each
session added its findings and no session was ever the one that removed any.

- **A trap goes here.** One to three lines saying what would be got wrong, and the
  reference doc that holds the evidence. No tables, no before/after counts, no
  "N things not to re-derive" lists — those are what grew it.
- **The measurement goes in `docs/reference/<topic>.md`**, appended to the existing file
  for that topic. Do not create a new file per session; there are seven and they cover
  the repo.
- **Read that area's reference doc before changing anything in it.** Section D says which.
- **Budget: this file stays under 30 KB.** If a change would push it over, something moves
  out to `docs/reference/` in the same commit.

The same three tests as Section B decide whether a *correction* is also logged in
`docs/mapping-algo-changes/`. That folder is unaffected by this rule.

---

## C. Repo facts you cannot get from reading the source

`README.md` and `guide/project-guide.md` cover Stage 1 only. The Stage 2 generator
is `src/osm_scenario/generation.py`; geometry and movement classification live in
`src/osm_scenario/topology.py`.

Everything in this section applies to any session in the repo. Everything that applies
only when working on one area is in `docs/reference/` — Section D names the traps and the
file for each.

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

### The standing principle: surveyed tags outrank inferred angles

`turn:lanes` is surveyed evidence of which movements are *permitted*. The movement
class is *inferred* by binning a turn angle against threshold constants. Where the
two disagree, **the tag must never be the reason a lane loses its only exit** —
that cuts the drivable network on the strength of a magic number. Already enforced
in `_side_filtered_candidates` and `_stranded_permission_fallback`; follow the same
rule anywhere else the two sources of truth meet.

---

## D. The traps, and where the evidence lives

Seven reference docs under `docs/reference/`, split out of this file on 2026-08-27. Each
holds the measurements, the sweeps and the before/after counts verbatim. Below is only
what would be got wrong without reading them — **read the doc before working in its area.**

These describe *what runs*, and are deliberately distinct from `docs/implementation-plan/`
(what was planned), `docs/mapping-algo-changes/` (corrections log) and
`docs/ai-action-logs/` (session records).

### Driving a dataset — routes, junction geometry, signals
→ `docs/reference/ego-route-and-signals.md`

- **A route has to be in the file.** `ScenarioEnv` has no start-and-end setting and
  `ScenarioMapManager.reset` calls `get_sdc_track()` unconditionally; with no ego car that
  is `KeyError('None')` and no config skips it. Without `--routes` the dataset is map-only.
- **MetaDrive never reads `routes.json`.** It is an exchange file between the browser and
  our converter. The route *is* `tracks["ego"]["state"]["position"]`.
- **`ConnectorFeature.centerline` is a marker, not a driving line.** Never splice it into a
  drive; `ego_route._turn` builds the join from the two lanes' own tangents. The connector
  is consulted only for *whether* a step crosses a junction.
- **`PROFILE_SAMPLE_M` must not follow `--step-hz`.** The coupling was measured and
  **rejected** — it costs a third of the pace and does not fix the artefact it exists for.
- **Counting refusals is not counting faults.** A sweep once reported "813 built, 0
  refused" while 440 of those 813 carried a vertex over the 30° gate.
- **Signal timing and `--routes` are convert-time arguments, never config fields** —
  `configuration_checksum` feeds `generation_fingerprint`, so a field on `ConverterConfig`
  invalidates the Stage 3 lane-model review. Same for `--speed-kph` and the render flags.
- **A wrong light lane id is silent** (`skip_missing_light` defaults to True), and
  `stop_point` sits at the top level of a light entry, never inside `state`.
- **Waiting at a red has to be in the recorded positions.** `ReplayEgoCarPolicy` sets
  position directly and drives through a red however correct the tape is.

### Running the simulator — rates, what a step costs, the container
→ `docs/reference/running-the-simulator.md`

- **`--step-hz` derives both physics keys from one number**; 10 Hz gives `(0.02, 5)`, which
  is why no flag and `--step-hz 10` are the same run. `--decision-hz` is a **stride counted
  in the caller's loop** — MetaDrive has no key for it.
- **There are two clocks.** `sim_step_seconds(env)` is how far one `env.step` advances the
  simulator; `data_step_seconds(scenario)` is how far one recorded frame covers. They are
  equal only when the dataset was converted at the rate it is being driven at.
- **A dataset may only be replayed at the rate it was written at**, and `drive.py` refuses
  the mismatch rather than warning. Each rate gets its own directory.
- **Do not quote a step-cost figure from a doc — re-measure with `tools/step_timing.py`.**
  The same configuration measured 8 ms a step early in a session and 17 ms after twenty
  minutes of sweeps. What every row and column means is `docs/step-timing-rows.md`, and
  that is the only place it is written down.
- **Use `tools/drive.py`, not `python -m scenarionet.sim`**, for 3D. The broken map is
  MetaDrive terrain defaults, not our data, and none of the fixes are reachable from `sim`.
- **The container is one environment, not two.** MetaDrive runs on this repo's 3.10; only
  the 3D row cannot run in there. `tools/` and `scripts/` are live through the bind mount,
  so only a dependency change needs a rebuild.

### Sensors, observations and the policy socket
→ `docs/reference/sensors-and-observations.md`

- **`env.step(action)` is the tick, and passing an action *is* driving the ego** — nothing
  to register, nothing to subclass. A slow policy makes a drive slow, never wrong.
- **The observation is an RL summary, not sensor data**, and its 120-laser lidar block
  reads a constant 1.0 because our scenarios hold one car. Traffic is what fills it.
- **Turning on `image_observation` replaces the observation** with `{"image", "state"}`,
  where `state` is 41 numbers and has no lidar block at all. A partial `sensors=` override
  wipes `rgb_camera` and kills the env at construction.
- **Only `camera` and `semantic` may cross the wire as uint8.** `depth` and `point-cloud`
  inherit a `_format` that *converts* rather than reformats — a point cloud runs
  −18476.9 to +11030.2 m and a uint8 cannot hold it. Neither raises.
- **`TCP_NODELAY` on both ends is worth 325×.** Miss either half and a 0.126 ms round trip
  becomes 41.0 ms, which reads as a slow simulator.
- **`--image-on-cuda` needs the PRIME offload** (CUDA and GL on the same card), `cuda-python`
  below 13, and `cupy-cuda12x[ctk]`. It is refused with `--render 3D`, and
  `numpy.asarray` on a CuPy array raises — copy through `tools/gpu_frames.to_host`.

### openpilot and the AV3 model at the wheel
→ `docs/reference/openpilot-and-the-model.md`

- **`--step-hz 100 --decision-hz 20` is what matches the bridge**, whose `_DT_MDL` is 0.05 s.
  Better than `convert --step-hz 20`: same control interval, ten times the physics under it.
- **Both ends negate.** MetaDrive is left-positive, CARLA right-positive, so the waypoints'
  `y` and the action's steering both flip. Get one of the two wrong and the car drives
  smoothly into the oncoming carriageway with nothing raising anything.
- **`target_speed` defaults to 0, which is a stop** — an omitted target is not "no opinion".
- **`--longitudinal table` is the only calibration.** `pedal` is what the bridge emits and
  is CARLA's Tesla map, wrong on MetaDrive's car, which has no aerodynamic term at all;
  `accel` is the sign-correct fallback. All three are kept deliberately.
- **The AV3 forward pass is ~1 s**, about 20× a 50 ms decision, so a full drive costs a
  quarter of an hour. It does not make a drive wrong — `env.step` is the tick.
- **Six conversions stand between the model and the car and not one raises when wrong.**
  Run `scripts/av3-probe.sh` before anything steers; a drive statistic cannot settle a sign
  when the model carries a standing lateral bias.
- **`uv sync --group model` alone *removes* `sim` and `gpu`** — name all three. And these
  paths need this repo's 3.10 interpreter: `METADRIVE_PYTHON=../.venv/bin/python`.

### Live traffic
→ `docs/reference/live-traffic.md`

- **This repo supplies placement only.** Every route is `ego_route.plan_route`, every car is
  a MetaDrive vehicle on `TrajectoryIDMPolicy`. `traffic.json` is a file because the planner
  is 3.10 and the manager runs in MetaDrive's venv.
- **`traffic_env` takes a class; `live_signal_env` returns one.** Composing them by
  assignment leaves only the last standing and silently drops the lights — and a red light
  is the only thing separating conflicting movements, because IDM has no give-way rule.
- **The file's frame is not the simulator's.** Everything written beside a pickle must be
  shifted by `metadata.old_origin_in_current_coordinate` — 93.8 m on `junction-1`.
- **A manager must keep its own generator**, seeded once and advanced per episode:
  `BaseEngine.seed` reseeds from the scenario index, and our datasets hold one scenario.
- **Ties between two MetaDrive objects must never break on `vehicle.name`** — it is a fresh
  uuid per process, so the run stops being repeatable. Use the spawn ordinal.
- **`LOST_LATERAL_M` confounds any collision measurement** taken with it on: it culls
  exactly the cars that were about to crash. Isolate it before comparing anything.

### Lane markings and road surfaces at export
→ `docs/reference/lane-markings-and-surfaces.md`

- **All of it is export-time**, in `conversion.py`. No fingerprint moves, so a change here
  never invalidates a review.
- **Nothing may be painted onto drivable road**, kerbs and lane edges alike, and
  `tools/check_dataset.py` fails on it.
- **Measure paint coverage at one texel, not at 0.20 m.** A drawn line is 2 px, so 0.125 m
  on `mosque`; a 0.20 m acceptance check passed 393 m of edge that renders bare.
- **A length filter is a needle filter and never a proxy for "is this on the road".**
  Calling `_MIN_KERB_M` one is exactly how 459 m of marks shipped on open tarmac.
- **MetaDrive draws every painted line short** — `resample_polyline` never includes the
  endpoint, costing 554.7 m across 585 of `mosque`'s lines. `_keep_line_ends` is
  unconditional, because a line drawn short is a fault and not a preference.
- **A road that stops must not be painted across**, or a stop line appears where there is
  none, with a ghost body, on road a car drives along.

### The Stage 2 lane model
→ `docs/reference/lane-model-algorithm.md`

- **Surveyed tags outrank inferred angles** — the standing principle in Section C above,
  and it governs every rule in this doc.
- **A restriction has to be known *before* the lanes are dealt out** (v21), or both balanced
  rules count a destination that is about to be deleted. Only the allocation is blinded;
  the movements are still generated and still forbidden.
- **A turn restriction names a route; a connector is one step of one.** Deleting the wrong
  step stops everyone who uses it, not only the prohibited traffic. When neither step is
  exact, nothing is deducible and the movements go to review.
- **v22–v26 each read a piece of evidence the generator had been ignoring**: an off-ramp
  already carrying a turn, a merging road crossing the lane it joins, `placement`, the road
  behind, and the carriageway coming the other way. Every one is measured in the doc.
- **Blast radius is an acceptance criterion.** A fix that redraws lanes that were not wrong
  gets reverted however good its numbers, and a fix must not bend what was straight.
- **Never fix a tag-versus-geometry conflict by making the finding stop being raised.**
  Fix the mapping and keep the review.
- **One failure is open and is Keith's to judge**: `ego_route` still turns over the 30° gate
  on two 2 m clamped lanes, 3 of 396 swept routes, worst 50.92°. Undiagnosed.
