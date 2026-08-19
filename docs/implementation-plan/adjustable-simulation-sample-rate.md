# Adjustable simulation sample rate

## Status

**Not built.** Everything below is a plan, with the measurements marked as such.

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

- [ ] **Phase 1 - everything runs at the step rate**
  - [ ] **1.1** `ego_route`: `time_step_s` through `ego_track` and `_sample_in_time`,
        defaulting to `TIME_STEP_S`. Nothing else - not `plan_route`, not
        `speed_profile`, and **not `PROFILE_SAMPLE_M`**.
        *Done when* a 100 Hz track decimated `[::10]` matches the 10 Hz track to 1e-9
        on position and velocity, and `plan_route`'s `duration_s` does not move.
  - [ ] **1.2** `convert_scenario(step_hz=)` and `--step-hz` on `cli.convert`, threaded
        to `light_states`, `plan_metadata` and `ts`. No new metadata key -
        `metadata.ts` spacing already *is* the rate, and `metadata.dt` would move every
        unflagged scenario's bytes. Refuse `<= 0` and refuse a reciprocal that is not a
        whole number of microseconds.
        *Done when* `sha256sum -c` passes on an unflagged re-convert and a `--step-hz 100`
        run reports the same distance, duration and speed.
  - [ ] **1.3** `check_dataset.py`: the 0.1 s window gate, and the same change to
        `test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, so converter and
        checker apply one rule. **Blocking** - without it a 100 Hz dataset cannot be
        verified at all.
        *Done when* the 10 Hz output is textually identical to today's and the 100 Hz
        dataset reports the same worst swing with `snaps == 0`.
  - [ ] **1.4** `drive.py`: `step_config` / `sim_step_seconds` / `data_step_seconds`, and
        `--step-hz` folded into the env dict conditionally. `:838-839`'s `steps % 10`
        becomes `% max(1, round(0.1 / sim_dt))`.
        *Done when* an unflagged `--render none` run is byte-identical and `--step-hz 100`
        prints the resolved pair.
  - [ ] **1.5** Budget in env steps rather than frames; refuse `--agent-policy replay`,
        baked lights and non-ego tracks at a mismatched rate; warn on `idm`, `manual` and
        `--render 3D` above 10 Hz.
        *Done when* the three refusals fire with both rates named, and a matched-rate
        replay completes with `steps` about equal to `budget`.
  - [ ] **1.6** `signal_control` takes its clock from the engine, not the plan. A
        correctness fix - say why in the docstring, or the next reader will fix it back.
  - [ ] **1.7** The remaining hard-coded 0.1s: `sensor_survey.py:314,563`,
        `policy_client.py:303`, and `--step-hz` on the survey and the example.
        Everything here must parse on **3.8**.
  - [ ] **1.8** `STEP_HZ` in `.env.example`, appended by `drive.sh` and
        `sensor-survey.sh` - and **bump their `-h` `sed` ranges** or the new help lines
        are silently cut off. **Deliberately not wired into `run-stages-4-6.sh`**: a
        dataset's rate is baked into bytes the Stage 3 review never re-checks, so
        picking it up from a machine-local file is how two workspaces end up at
        different rates with nobody having decided.
  - [ ] **1.9** Measure, then document. Wall-clock per `env.step` at 100 Hz headless /
        offscreen / 3D; whether 3D is usable at a 100 fps `ForceFPS` target; pickle size
        **with `--signals`**, since `light_states` builds a Python list of colour
        *strings* per lane per step. Then `CLAUDE.md` (the `PROFILE_SAMPLE_M` rejection,
        the two-clocks rule, the 0.1 s window), `README.md`, and
        `docs/scenario-datapoints.md:222-224`, which currently says this cannot be done.

- [ ] **Phase 2 - cameras at 20 Hz**
  - [ ] **2.1** `--camera-hz` and the read gate in `SensorPack`, and nowhere else -
        `drive.py`'s loop keeps its shape. Refuse a non-integer ratio.
        *Done when* a stub engine counting `perceive` calls gives exactly 4 in 20 steps
        at stride 5, with `imu`/`gps` on all twenty.
  - [ ] **2.2** The wire contract: **omit the camera key on a skipped step, never resend
        the previous frame** - a model cannot tell a stale frame from a fresh one, and
        resending costs the exact bandwidth the decimation buys. `/spec` gains
        `sensor_rates`, `/act` gains `sensors_fresh`, so absence is a stated fact rather
        than a silence.
  - [ ] **2.3** `camera_rig.tick_rate` validated against the interval the rig is
        actually read at, rather than against 0.1. Still a refusal, never a resample,
        and validation only - the rate has one source, the global flag.
  - [ ] **2.4** `--camera-skip-draw`, its own flag, after measuring. The read gate alone
        saves little: the mounted read is 2.2 ms of a 20.4 ms step, so four skips in five
        save about 1.8 ms and the remaining 18 ms is the draw. Refused together with
        `--render offscreen` until measured, and `image_on_cuda` outright.
  - [ ] **2.5** `CAMERA_HZ` in `.env.example`, both scripts, README.

## Known limits, stated rather than hidden

**Phase 1 makes 100 Hz *available*, which is narrower than it sounds.** The only things
that *record* numeric sensors at that rate are `sensor_survey.py`'s per-step CSV and
`policy_client`'s wire. `drive.py --record` writes observations and actions only. 100 Hz
IMU and GPS on disk from `drive.py` is separate work, named here so it is a decision
rather than an omission.

**`--agent-policy idm` will drive differently at 100 Hz**, for the reasons in the facts
above. It is warned about and left alone: patching a reference checkout is out of bounds,
and the difference is real rather than a data fault.

**Three things are unmeasured and must not be guessed at**: the wall-clock cost of a
100 Hz drive headless and in 3D; the pickle size of a 100 Hz dataset carrying signals;
and whether `buffer.set_active` is safe mid-episode, given that `image_observation` calls
`perceive()` unconditionally every step (`image_obs.py:85` from `base_env.py:620`) and a
dormant buffer would fill the 3-deep `np.roll` stack with whatever it holds. That last one
is why 2.4 is its own flag rather than part of 2.1.

