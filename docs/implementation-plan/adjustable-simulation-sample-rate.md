# Adjustable simulation sample rate

## Status

**Phases 1 and 2 are built and verified.** The simulator, the converter and every tool
take `--step-hz`, defaulting to MetaDrive's 10; `drive.py` and `step_timing.py` take
`--decision-hz`, defaulting to the step rate, which is how a 100 Hz world runs 20 Hz
decisions and 20 Hz sensor reads. Cameras are still **drawn** every step and that was
measured rather than assumed - see 2.4. Figures below are measured on `junction-1`
unless they say otherwise.

The invariant held: with no flag anywhere, `sha256sum -c` passes on a re-converted
`junction-1` dataset, and `tools/drive.py` and `tools/check_dataset.py` print every
number they printed before. The only textual changes are the three lines that now state
the rate, which is the point of them.

This is not a stage. It is an edit that cuts across the ones already built: a
flag, defaulting to today's numbers, that changes how fast the simulator
advances and how densely the dataset is written. Stages 1-6 do not move, and
Stage 7 (`stage-7-an-agent-at-the-wheel.md`) does not gain or lose anything -
it only gets faster once this lands.

## Why

`env.step` advances 0.1 s, so 10 Hz is the control rate, the sensor rate and the
recording rate all at once. `docs/scenario-datapoints.md:222-224` already states
the consequence - the recorded IMU is a 10 Hz signal differenced over 0.1 s where
a real one runs 100-1000 Hz, and *"the only route is running the simulator faster,
via `physics_world_step_size` and `decision_repeat`"*. Nothing in the repo sets
either key.

Keith asked for the sim at **100 Hz** with all the numeric sensors at 100 Hz, the
converted dataset written at 100 Hz too, and the **cameras at 20 Hz** - every rate
a flag defaulting to today's numbers, with the defaults in `.env` beside
`LINE_WIDTH_M`.

## Nothing upstream changes

The rate is a **convert-time argument**, never a config field.
`configuration_checksum` feeds `generation_fingerprint` (`generation.py:4006-4016`),
so a field on `ConverterConfig` would invalidate the Stage 3 lane-model review and
move the scenario id. `--speed-kph` is the precedent and the same paragraph in
`convert_scenario`'s docstring covers both. Stages 1-5 are untouched; nothing in
`config/`, nothing in either reference checkout.

**With no flag passed anywhere, every byte and every line of stdout is what it is
today.** That is the invariant each step below re-proves, by `sha256sum -c` over
the converted pickles and by diffing an unflagged `drive.py` run.

## Facts the design rests on

- `env.step()` = `physics_world_step_size` x `decision_repeat`
  (`base_env.py:190-191`, defaults 0.02 and 5), via `base_env.py:435,462-466` ->
  `base_engine.py:431-441` -> `engine_core.py:385-387`, where `doPhysics(dt, 1, dt)`
  takes one fixed substep - so the key is genuinely honoured.
- The mapping keeps the physics tick from ever getting coarser than MetaDrive's own:
  `repeat = max(1, ceil(dt / 0.02))`, `physics = dt / repeat`. **10 Hz returns exactly
  (0.02, 5)**, so `--step-hz 10` and no flag are the same run; 100 Hz gives (0.01, 1).
  `decision_repeat = 1` also matters onscreen - `base_engine.py:455` calls
  `taskMgr.step()` once per substep, and each of those redraws every camera buffer.
- **MetaDrive has no per-sensor rate**, anywhere. The sensor config is
  `name=(cls, *args)` with no slot for one, so 20 Hz cameras are ours to build.
- `perceive()` is a **read**, not a render, when `new_parent_node is None`
  (`base_camera.py:187-192`). Passing it makes `perceive` call `taskMgr.step()`
  itself - the expensive form, and the one `policy_client.py:209` uses.
- **The draw is not gated by the read.** Buffers are drawn on every `taskMgr.step()`
  (`base_engine.py:455,458`) and never deactivated, so gating only the read gives
  20 Hz frames at 100 Hz cost. `buffer.set_active(False/True)` is the supported way
  to skip a draw; MetaDrive does exactly that for its dashboard
  (`dashboard.py:129-135`).
- `waypoint_policy.py:61-65` is MetaDrive's own correct derivation of the step from
  the two keys, and the pattern to copy.
- `_sample_in_time` (`ego_route.py:1231`) is the **only** seconds-to-steps conversion
  in `src/`. `signal_plan` is already parameterised; every call site just passes
  `TIME_STEP_S`.
- **Two clocks, not one.** The *sim* step comes from the engine; the *data* step is
  the recorded tape's own spacing, `metadata.ts[1] - ts[0]`. Every hard-coded 0.1 in
  `tools/` belongs to exactly one of them, and two of them are currently reading the
  wrong one - `signal_control.py:93,103` converts an **engine** step count to seconds
  using the **plan's** rate, and `drive.py:170`'s `_longest_red` divides seconds by the
  **data** rate to produce a budget counted in **env** steps. Both are right only by
  today's coincidence.

**Rate-coupled inside MetaDrive, and not ours to fix** - reported, never patched:
`IDMPolicy.LANE_CHANGE_FREQ = 50  # [step]`, `IDM_ACT_BATCH_SIZE = 5`, and
`PIDController` (`PID_controller.py:1-22`), which has **no dt at all** - it sums raw
error and takes a raw difference, so both gains scale with the rate. So
`--agent-policy idm` will not drive identically at 100 Hz. `parse_object_state`'s
`sim_time_interval=0.1` (`parse_object_state.py:28`) is hard-defaulted and passed by
no caller, which is why a mismatched replay is refused rather than warned about.

## Two measurements that changed the plan

Taken on `junction-1`'s `test` route (403.7 m) before any code was written:

| profile spacing | step | samples | duration | mean | worst turn/step | peak `turn/dt.v` |
|---|---|---|---|---|---|---|
| 0.1 m | 0.1 s (today) | 370 | 36.9 s | 39.3 km/h | 24.70 deg | 10.5 m/s^2 |
| 0.1 m | **0.01 s** | 3695 | 36.9 s | 39.3 km/h | 12.31 deg | 43.7 m/s^2 |
| 0.01 m | 0.1 s | 556 | 55.6 s | 26.2 km/h | 16.78 deg | 3.2 m/s^2 |
| 0.01 m | 0.01 s | 5558 | 55.6 s | 26.2 km/h | 12.36 deg | 23.0 m/s^2 |

**`PROFILE_SAMPLE_M` must not scale with the rate**, though the obvious reading of
`CLAUDE.md`'s invariant says it should. Left at 0.1 m it gives exactly what was asked
for - identical duration and speed, ten times the samples. Scaled, it costs a third of
the pace and does not fix the artefact it exists for. The invariant was derived at
10 Hz, where "as fine as the track" and "fine enough to read the road's own 0.25 m
junction arcs" happened to coincide at 0.1 m; at 100 Hz they stop coinciding, and
densifying below the source geometry makes the curvature estimator read
`radius = span/turn` over a shorter span than the road really turns through.

**Per-step heading metrics stop meaning anything at 100 Hz.** The identical drive reads
24.70 deg/step at 10 Hz and 12.31 deg/step at 100 Hz - 247 deg/s against 1231 deg/s.
Turning is concentrated at vertices, so once a step is shorter than the vertex spacing
one step absorbs a whole vertex's turn whatever `dt` is. `tools/check_dataset.py`'s
30-degrees-per-step check is the only drivability gate in the repo, and
`LATERAL_ACCEL_MPS2 = 8.5` was tuned directly against it. Neither the fixed threshold
nor a rescaled one survives: rescaling would **fail a 100 Hz dataset of a drive that
passes at 10 Hz**. It has to be measured over a fixed 0.1 s window, which is
rate-invariant by construction and returns today's number at `window == 1`.

## Progress, outputs, and verification

Each step is commit-sized, leaves the tree green, and is safe to stop at. Three are
worth stopping at deliberately: **after 1.2** a 100 Hz dataset exists and the 10 Hz one
is provably unchanged; **after 1.3** it can be verified; **after 1.5** it can be driven.

- [x] **Phase 1 - everything runs at the step rate**
  - [x] **1.1** `ego_route`: `time_step_s` through `ego_track` and `_sample_in_time`,
        defaulting to `TIME_STEP_S`. Nothing else - not `plan_route`, not
        `speed_profile`, and **not `PROFILE_SAMPLE_M`**.
        *Done when* a 100 Hz track decimated `[::10]` matches the 10 Hz track to 1e-9
        on position and velocity, and `plan_route`'s `duration_s` does not move.
  - [x] **1.2** `convert_scenario(step_hz=)` and `--step-hz` on `cli.convert`, threaded
        to `light_states`, `plan_metadata` and `ts`. No new metadata key -
        `metadata.ts` spacing already *is* the rate, and `metadata.dt` would move every
        unflagged scenario's bytes. Refuse `<= 0` and refuse a reciprocal that is not a
        whole number of microseconds.
        *Done when* `sha256sum -c` passes on an unflagged re-convert and a `--step-hz 100`
        run reports the same distance, duration and speed.
  - [x] **1.3** `check_dataset.py`: the 0.1 s window gate, and the same change to
        `test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, so converter and
        checker apply one rule. **Blocking** - without it a 100 Hz dataset cannot be
        verified at all.
        *Done when* the 10 Hz output is textually identical to today's and the 100 Hz
        dataset reports the same worst swing with `snaps == 0`.
  - [x] **1.4** `drive.py`: `step_config` / `sim_step_seconds` / `data_step_seconds`, and
        `--step-hz` folded into the env dict conditionally. `:838-839`'s `steps % 10`
        becomes `% max(1, round(0.1 / sim_dt))`.
        *Done when* an unflagged `--render none` run is byte-identical and `--step-hz 100`
        prints the resolved pair.
  - [x] **1.5** Budget in env steps rather than frames; refuse `--agent-policy replay`,
        baked lights and non-ego tracks at a mismatched rate; warn on `idm`, `manual` and
        `--render 3D` above 10 Hz.
        *Done when* the three refusals fire with both rates named, and a matched-rate
        replay completes with `steps` about equal to `budget`.
  - [x] **1.6** `signal_control` takes its clock from the engine, not the plan. A
        correctness fix - say why in the docstring, or the next reader will fix it back.
  - [x] **1.7** The remaining hard-coded 0.1s: `sensor_survey.py:314,563`,
        `policy_client.py:303`, and `--step-hz` on the survey and the example.
        Everything here must parse on **3.8**.
  - [x] **1.8** `STEP_HZ` in `.env.example`, appended by `drive.sh` and
        `sensor-survey.sh` - and **bump their `-h` `sed` ranges** or the new help lines
        are silently cut off. **Deliberately not wired into `run-stages-4-6.sh`**: a
        dataset's rate is baked into bytes the Stage 3 review never re-checks, so
        picking it up from a machine-local file is how two workspaces end up at
        different rates with nobody having decided.
  - [x] **1.9** Measure, then document. Wall-clock per `env.step` at 100 Hz headless /
        offscreen / 3D; whether 3D is usable at a 100 fps `ForceFPS` target; pickle size
        **with `--signals`**, since `light_states` builds a Python list of colour
        *strings* per lane per step. Then `CLAUDE.md` (the `PROFILE_SAMPLE_M` rejection,
        the two-clocks rule, the 0.1 s window), `README.md`, and
        `docs/scenario-datapoints.md:222-224`, which currently says this cannot be done.

- [x] **Phase 2 - decisions and cameras below the world tick** (2026-08-22)
  - [x] **2.1** The flag is **`--decision-hz`, not `--camera-hz`**, and it gates the
        policy call as well as the sensor read. A camera-only flag would have left
        `RemotePolicy(step_seconds=...)` at the env.step interval - 0.01 s at 100 Hz -
        so openpilot's bridge, whose `_DT_MDL` is 0.05 s, would still have had its lag
        compensation and curvature-rate limit mis-scaled by 5x. One flag, because
        `world tick / decision + camera / physics` has one middle column. A camera rate
        that differs from the control rate can split off it later; nothing needs that.
        `drive.decision_stride` is the one place the ratio is worked out, refused rather
        than rounded, and `decides_on` is the one place the schedule is decided - both
        loops call it, so the benchmark cannot measure a schedule the tool does not run.
        *Done*: `decides_on` gives exactly 4 decisions in 20 steps at stride 5
        (`test_step_timing.py`), and `rig_ms_median` falls from 3.12 ms to a skipped
        step's 0.0001 ms on the seven-camera rig.
  - [x] **2.2** No wire change was needed, and that is the flag's doing rather than an
        omission. With the decision gated there is no `/act` on a skipped step, so every
        call already carries fresh sensors and there is no stale frame to omit. What the
        server does need is the interval between two *calls*: `step_seconds` is now
        `sim_step_seconds x stride`, and `/spec`'s existing `extra` carries a `rates`
        block naming all three clocks. `policy_client.py` is untouched.
  - [x] **2.3** `camera_rig.tick_rate` is checked against the interval the cameras are
        really read at - `load_rig(path, read_interval_s=...)` - rather than against a
        hard-coded 0.1 s, and `CameraRig.tick_rate_s` carries the declared value for a
        caller that cannot know the interval yet. Both `sensor-survey.sh` and
        `step-timing.sh` note rather than refuse - the sweep because it drives every rate a
        workspace holds, the survey because it has no `--decision-hz` to answer a refusal
        with and `--step-hz 100 --camera-rig rigs/cams.txt` worked before. Still validation
        only, never a resample.
  - [x] **2.4** **`--camera-skip-draw` was built, measured and removed.** The premise -
        that the draw is the expensive half - is wrong on this machine.
        `buffer.set_active(False)` is MetaDrive's own mechanism (`dashboard.py:129-135`)
        and it does not move the number: all seven of `rigs/cams.txt`'s buffers
        deactivated for a whole `mosque` drive at 100 Hz gave **26.42 ms/step against
        26.57 active - 0.15 ms, 1%** - with `is_active()` confirmed `False` on every one.
        Through the flag it read 26.37 against 26.19 ungated. What a lower decide rate
        really saves is the **read**: the same rig goes from 0.341x to 0.371x real time.
        `camera_hz` and `camera_draw_hz` now report the two rates separately so nothing
        reads as though a decimated camera were cheap. Do not rebuild this without
        measuring first.
  - [x] **2.5** `DECISION_HZ` in `.env.example` and `drive.sh` (not `CAMERA_HZ`, per 2.1),
        `--decision-hz` on `step-timing.sh`, and `docs/step-timing-rows.md`, which is
        where the columns are documented and the only place they are.

## What Phase 1 measured

Everything here was taken after the code was written, on `junction-1`'s 403.7 m `test`
route. `env.step` timings exclude construction and the first 20 steps.

| | 10 Hz | 100 Hz |
|---|---|---|
| `env.step`, headless | 1.094 ms | **0.848 ms** |
| `env.step`, `--render offscreen` | 10.9 ms | 20.2 ms |
| `env.step`, `--render 3D` (RTX 4050) | 83.4 ms | 16.6 ms |
| 3D speed against wall-clock | 1.20x | **0.60x** |
| a whole headless drive | 352 steps, 1.55 s | 3516 steps, 4.85 s |
| scenario pickle, map + route | 791,940 B | 1,121,208 B (+41.6%) |
| the same, + a 3-lane light plan | +6,666 B | +56,559 B (+5.0%) |
| `convert` wall-clock, `junction-1` | 1.53 s | 1.54 s |
| `convert` wall-clock, `mosque` | 2.1 s | 2.1 s |

Four things worth not re-deriving:

- **One `env.step` is cheaper at 100 Hz, not dearer.** `decision_repeat` is 1 rather
  than 5, so it is one physics substep instead of five. A whole drive still costs about
  **7.8x**, because there are ten times as many.
- **3D tops out at 60 fps at either rate** - 5 frames per 83.4 ms, 1 per 16.6 ms, which
  is the display's vsync. So `ForceFPS` asking for 100 is exactly why a 100 Hz drive runs
  at 0.60x real time rather than 1.20x. Usable, and slower than the clock on the wall.
- **The light tape grows linearly**, at about **5.1 B per lane per step** - it is a Python
  list of colour *strings*. `junction-1`'s 3-lane plan is nothing; a 20-lane plan at
  100 Hz would add roughly 370 KB a scenario.
- **`convert` does not get slower.** The extra samples are an `np.interp` over a profile
  that was already computed; the cost of a conversion is the map.

Both extracts drive at 100 Hz: `junction-1` 3516 of 3695 steps at completion 0.950
(against 352 of 370 and 0.953 at 10 Hz), `mosque` 1543 of 1605 at 0.951.

## One departure from the plan, and why

Step 1.3 said to make the same window change to
`test_no_route_on_the_real_map_turns_more_than_the_gate_allows`. **It was left alone.**
That test measures the turn at each vertex of the route *polyline*, and a polyline has no
rate - `route_polyline` never sees one - so the test is already rate-invariant and there
is nothing to make invariant. Rewriting its rule would only have moved a **known failure**
that is Keith's to judge: 3 of 396 swept routes still turn more than 30 deg at a vertex,
worst 50.92 deg, on two `MIN_TRIMMED_LANE_M`-clamped 2 m lanes (see `CLAUDE.md`). Making a
standing finding stop being raised is the one thing that rule exists to prevent.

What replaced it is `test_the_drivability_gate_reads_the_same_at_either_rate` in
`tests/unit/test_ego_route.py`, which spells out the checker's window rule against a track
built at both rates and asserts they agree - so converter and checker can be shown to apply
one rule without MetaDrive being installed, and without touching the failing sweep.

## Known limits, stated rather than hidden

**Phase 1 makes 100 Hz *available*, which is narrower than it sounds.** The only things
that *record* numeric sensors at that rate are `sensor_survey.py`'s per-step CSV and
`policy_client`'s wire. `drive.py --record` writes observations and actions only. 100 Hz
IMU and GPS on disk from `drive.py` is separate work, named here so it is a decision
rather than an omission.

**`junction-1/signals/signals.json` no longer matches its lane model**, so the light-tape
figures above were taken against a copy of it re-stamped with the current fingerprint, in
the scratchpad. Keith's file was not touched. The three refusal branches were exercised
directly against `drive.py._refuse_mismatch` under MetaDrive's own interpreter rather than
through a dataset, for the same reason.

**`--agent-policy idm` will drive differently at 100 Hz**, for the reasons in the facts
above. It is warned about and left alone: patching a reference checkout is out of bounds,
and the difference is real rather than a data fault.

**Three things are unmeasured and must not be guessed at**: the wall-clock cost of a
100 Hz drive headless and in 3D; the pickle size of a 100 Hz dataset carrying signals;
and whether `buffer.set_active` is safe mid-episode, given that `image_observation` calls
`perceive()` unconditionally every step (`image_obs.py:85` from `base_env.py:620`) and a
dormant buffer would fill the 3-deep `np.roll` stack with whatever it holds. That last one
is why 2.4 is its own flag rather than part of 2.1.

