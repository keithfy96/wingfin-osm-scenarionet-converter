# Stage 9 - A model at the wheel

## Status

**Every phase is built and measured.** Every
number here is either measured on this machine and marked as such, or arithmetic over
a measured number and marked as such. Where something is unknown it says so rather
than estimating.

**To check it yourself, `docs/running-a-test.md` is the ladder** — the six tiers from a
one-second test run to a fifteen-minute drive, what each one proves, and how to read the
output. This document is the record of *why* each piece is shaped the way it is; that one is
the runbook.

**What already works, and is the floor this builds on** (2026-08-23, measured):
the openpilot bridge drives the ego. The fork is on disk at
`/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/`, the image builds, the
container serves, and `--backend bridge` completes both extracts. The steering half
of the fit is exact; the longitudinal half is not. Full record in `CLAUDE.md`,
section *The real bridge drives it, and only the steering half of the fit was right*.

| `junction-1`, `--step-hz 100 --decision-hz 20` | steps | arrive_dest | completion |
|---|---|---|---|
| `--backend stub` (the reference) | 3788 | True | 0.950 |
| `--backend bridge --longitudinal pedal` | 1308 | **False**, `out_of_road` 4.08 m | 0.529 |
| `--backend bridge --longitudinal accel` | 8726 | True | 0.950 |

**What is missing is the model.** Every waypoint the bridge has ever been sent came
from `waypoints_from_route`, which is wing-sim's `route_gt.py` reproduced - the
recorded route sampled at the car's *current* speed. `route_gt.py`'s own docstring
says what that is for: *"Use this to isolate whether drift is caused by the model or
the controller."* It is a controller test, by construction. Stage 9 puts the driver
in.

Stage 7 (`stage-7-an-agent-at-the-wheel.md`) built the socket. Stage 8
(`stage-8-live-traffic.md`) is unrelated and composes freely.

---

## Summary

```
7 cameras ──5.42 MB/step──> model ──4 waypoints──> bridge ──2 numbers──> car
             the heavy edge         ~100 bytes      python 3.8            
```

**The model is the driver** - it decides where to go, from pictures. **The bridge is
the controller** - it decides how to get there, and never sees an image. That split
is the whole design, because it puts the heavy data and the Python-3.8 constraint on
different edges of the graph.

Four pieces, in the order they are worth doing:

| | what | needs a GPU | needs the model | fixes something broken today |
|---|---|---|---|---|
| **Phase 0** | a MetaDrive pedal table | no | no | **yes - done, 2026-08-23** |
| **Phase A** | send frames as uint8 | no | no | **yes - done, 2026-08-24** |
| **Phase B** | keep frames on the GPU | yes | no | **yes - done, 2026-08-24** |
| **Phase C.1** | load the checkpoint | yes | yes | **yes - done, 2026-08-24** |
| **Phase C.2/3** | the model in-process | yes | yes | **yes - done, 2026-08-25** |

Phase 0 is independent of the other three and is **done**: it repaired the half of the
fit that was wrong, and measuring it showed the remaining speed undershoot belongs to
the trajectory rather than to the pedals, which is Phase C. Phase A is **done** too, and
the column above understates it: it was listed as fixing nothing, and it turned out that
`depth` and `point-cloud` share the flag with the two cameras, so the obvious version of
it would have quantised a depth buffer to 76 levels and destroyed a point cloud in metres.
Phase B is **done** as well, and the column above was wrong about it twice. It does fix
something today -- `env.step` costs 2.2x less with the frame left on the card, measured
over three matched pairs -- and it is *not* worth nothing without Phase C: what is worth
nothing without Phase C is Phase B **on a socket**, which is a narrower claim and now a
measured one (2927.0 KB a step either way, byte for byte).

---

## Facts the design rests on

### MetaDrive is a library, not a server

There is no MetaDrive process to connect to. `import metadrive` puts it in *your*
program, so "the model runs in the same process as MetaDrive" means no more than
*the script that imports `metadrive` also imports `torch`*. `env.step(action)` is the
entire interface - `base_env.py:190,462` - and passing an action **is** driving the
ego, with nothing to register and nothing to subclass. This is `CLAUDE.md`'s "the
tick is the call".

**"Same process" and "TensorRT" are not alternatives.** TensorRT is *how* the model
computes, on the GPU. The process is *where the calling code lives*. The model runs
on the GPU either way; the process decides only whether pixels must be serialised to
reach it.

### The bridge stays in its own container, and that costs nothing

The fork pins `python = "~3.8"` and `casadi==3.5.5`. That pin is real - but the
`opencv-python-headless` cp38 wheel beside it is **not** a blocker, checked rather
than assumed: `cv2` is not importable inside the running bridge container and the
bridge works. Whether the fork could be moved to 3.10 is unresolved and not worth
resolving: the boundary it sits on carries ~100 bytes each way at 3.5 ms a call, and
it is a *control stack* whose pinned SHA exists so two machines run the identical
controller.

### The eight conversions, and that every one fails silently

This is the substance of Phase C. None of them raises.

**Going in - simulator to model**

| # | what | we have | the model wants | where |
|---|---|---|---|---|
| 1 | pixels | `(288,512,3)` uint8 **BGR** 0-255 | `(3,288,512)` float32 **RGB** 0-1 | `assets/modifiers/modifiers.py` |
| 2 | camera order | `cam_front, cam_left, cam_right, cam_back, cam_back_left, cam_back_right` | `front_middle, front_right, rear_right, rear_middle, rear_left, front_left` | `model_dev.yml` |
| 3 | frame history | one frame per step | 5 frames at `[t-2.0, t-1.5, t-1.0, t-0.5, t]` | `t_frames`, `frame_stride_s` |
| 4 | ego speed | m/s, world frame | `[v_fwd / 8.09, v_lat / 0.27]` | `av3_base.py:248` |
| 5 | route | 25 raw points at 2 m | `(20, 7)` navigation features | `routes/route.py:141` |

1. `modifiers.py` does exactly this and is usable as-is, and
   **MetaDrive's frames are BGR natively** (`base_camera.py:100-113`), the same as
   CARLA's, so no extra swap. What remains is BGR->RGB, transpose, `/255`.

   **CORRECTED at C.3: the resize is NOT a no-op, and calling it one is a geometry
   error rather than a cost one.** The modifier's resize is 1440x1080 -> 512x288: a 4:3
   frame squashed vertically by 1.33x into 16:9, which is what the model was trained on.
   `rigs/cams.txt` renders 512x288 natively, so mounting the model on it would give a
   vertical field of view a third narrower than the model has ever seen, with nothing
   raising. `rigs/av3.txt` renders **512x384** - 4:3 at 1/8 the pixels - so the
   preprocess does a real squash.
2. **The most dangerous of the eight.** `model_dev.yml` states the order is a contract
   with the weights: reorder it and the model still runs, and is wrong.

   **RESOLVED at C.3 by replacing the question.** The row above maps
   `rigs/cams.txt`'s names onto the model's, which cannot be done safely: that file
   carries `y: 0.0` on all seven cameras, so its yaw column has nothing to be
   cross-checked against, and it names its back pair the opposite of its own yaws.
   `rigs/av3.txt` is generated from wing-sim's own
   `evaluation/configurations/validation_invariants.yml` instead, so the six names and
   the six aims agree by construction and the map is the identity.
3. A wrong `t_frames` usually fails at load. **A wrong `frame_stride_s` never does** -
   the model runs on history spaced differently from training and still scores. This
   interacts directly with `--decision-hz` and `tools/frame_gate.py`, which holds the
   drawn frame between decisions.
4. `np.array([v_fwd / s_lon, v_lat / s_lat])` with `ego_velocity_scale: [8.09, 0.27]`
   - **two different divisors**, and the velocity is rotated into the ego frame by yaw
   first.
5. **A computation, not a reformat.** Per point
   `[fwd/H, right/H, cos θ, sin θ, curvature·H, s_norm, valid]`, `H = n_route ×
   route_spacing_m` = 40 m, and **all zeros** when the car is more than
   `route_max_offset_m` (20 m) off route.

**Coming out - model to bridge**

| 6 | waypoint frame | model emits x forward, **y right** (CARLA) | the bridge takes y right too -> **no flip** |

**Measured at C.3, not assumed.** `waypoints_from_route` negates because it *starts*
from MetaDrive's left-positive `route` sensor; the model's output starts in its own
training frame, which is already the bridge's. `av3_probe --nav-sweep` settles it
directly: with the pictures and the ego state held fixed and the navigation block
replaced by a 30 m arc, a right-hand bend moved the predicted lateral **+1.109 m**
relative to a left-hand one. This is the one conversion no source file can answer,
because it is a property of the weights.

**Coming back - bridge to car**

| 7 | steering | CARLA right-positive | MetaDrive left-positive -> negate. **Done, verified exactly** |
| 8 | pedals | Tesla-on-Town10HD table | **wrong here. Phase 0** |

7 is settled: a 124.95° column angle came back as `steer` 0.2603, which is
`124.95 / 12 / 40` to four figures.

### A model does not fix the pedals

`accel_to_carla` returns throttle whenever `accel_cmd >= coast_accel(v_ego)`, and
`coast_accel` is the **CARLA Tesla M3's own zero-throttle deceleration** - measured
in the container: **-1.582 m/s² above 10 m/s**, -1.150 at 5, -1.377 at 3.5. MetaDrive's
car does not coast down anywhere near that hard, so a request to brake at -1.0 m/s²
returns **throttle 0.274**.

That crossover belongs to the table, not to the trajectory, so a real model changes
nothing about it. Measured over the drives above:

| | decel requests | answered with throttle | v mean | outcome |
|---|---|---|---|---|
| `junction-1` `pedal` | 201 | **137 (68%)** | 16.4 m/s | ran away 13.9 -> 20.5 m/s, off the road |
| `mosque` `pedal` | 2469 | 11 (0%) | 3.5 m/s | arrived |

**It is speed that decides, and one map alone gets the rule backwards.** `mosque` sat
below the crossover and braked correctly; `junction-1` started at 13.9 m/s and asked
for -0.2 to -1.5, all of it above.

---

## Progress, outputs, and verification

- [x] **Phase 0 - a pedal table measured on MetaDrive** (2026-08-23)

  **This replaces a stage the bridge already had; it does not add one.** The model decides
  where to go, the bridge decides how hard and which way, and a pedal map decides only how far
  to press a pedal to get that acceleration *on this car*. `server.py:788-792` does exactly
  two conversions before replying, side by side - a road-wheel angle into a normalised steer,
  and `accel_to_carla(self._last_actuators.accel, v_ego)` into a throttle and a brake - and
  both are properties of the car. **The steering one came out free** because it is geometry:
  `action[0] x max_steering` *is* the road-wheel angle in degrees (`base_vehicle.py:478`) and
  the geometric branch emits `-road_wheel_deg / max_steer_angle`, both sides at 40°, so it
  cancels. Pedal to acceleration is not geometry - it depends on mass, engine force, brake
  force and drag - which is why only that half needed measuring.

  `tools/pedal_sweep.py` measures it, `tools/pedal_map.py` reads and inverts it,
  `calibration/metadrive-pedal-map.json` is the table, and
  `examples/openpilot_server.py --longitudinal table` is the third mode. `pedal` and
  `accel` both stay - `pedal` is what a CARLA consumer gets and has to stay reproducible,
  `accel` is the sign-correct fallback where no table has been measured. The fork is
  never touched: the reply already carries `accel_cmd` in m/s², so the conversion is
  entirely on our side.

  ```bash
  # from inside scripts/            (~9 s, no GPU, no display)
  ./pedal-sweep.sh junction-1

  uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
  ./drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642 \
      --sensors imu,route --step-hz 100 --decision-hz 20 --render none
  ```

  **What MetaDrive's longitudinal model turned out to be**, read from
  `base_vehicle.py:493-520` and then measured. There is **no aerodynamic term anywhere**:
  the only resistance is a constant `setBrake(2.0)` on all four wheels, applied even under
  throttle, so the car coasts at a flat **-0.364 m/s²** at every speed. That is MetaDrive's
  `coast_accel`, and it is **a quarter of the -1.582 the bridge's CARLA table assumes**.
  Above `max_speed_km_h` (80, so 22.22 m/s) the engine is cut entirely, which is the one
  place the speed axis of the table earns its keep. And `max_engine_force` /
  `max_brake_force` are **sampled** from `BoxSpace(750, 850)` / `BoxSpace(80, 180)`
  (`pg_space.py:239-240`), not constants - measured **759.464 / 89.464** identically on
  both extracts at both rates, because the parameter seed is the scenario index and each
  dataset holds one scenario. Recorded in the file and checked at the start of every
  episode.

  **The sweep visits speeds; it does not let the pedal choose them**, and that shape is
  forced by the flat coast. Hold one pedal and the car does not sweep the speed range:
  near the pedal that cancels the coast it would take **440 s and 4.9 km** to cross it,
  and the pedals either side never leave the end they start at. So the car is trimmed *to*
  each of 23 speeds and all 41 pedals are probed there - 2,829 measured steps, nine
  seconds. `BulletPlaneShape(Vec3(0, 0, 1), 0)` (`terrain.py:179`) is infinite, so driving
  straight for kilometres is fine and `map_region_size` does not bound it.

  **Verified against the real bridge, both extracts**, 100 Hz world / 20 Hz decisions.
  "hard decels" are requests below the coast (`accel_cmd < -0.5`), where the sign is not in
  dispute; "delivers" is `|what the chosen pedal really produces - what was asked for|`,
  median over every call:

  | | calls | hard decels | answered with throttle | delivers | outcome |
  |---|---|---|---|---|---|
  | `junction-1` `pedal` | 262 | 153 | **89 (58%)** | 1.371 m/s² | out_of_road, 0.529 |
  | `junction-1` `accel` | 1746 | 8 | 0 (0%) | 0.308 m/s² | arrived, 0.950 |
  | `junction-1` `table` | 1559 | 195 | **0 (0%)** | **0.000 m/s²** | out_of_road, 0.815 |
  | `mosque` `pedal` | 2427 | 2387 | **2158 (90%)** | 1.170 m/s² | arrived, 0.950 |
  | `mosque` `accel` | 2836 | 1 | 0 (0%) | 0.362 m/s² | arrived, 0.950 |
  | `mosque` `table` | 2498 | 85 | **0 (0%)** | **0.000 m/s²** | arrived, 0.950 |

  The first criterion is met exactly: **no request to slow down comes back as throttle,
  and the pedal chosen produces the acceleration asked for**, to the resolution of the
  lookup, on both maps. `accel` was already sign-correct and is out by 0.31-0.36 m/s²
  typically, which is what "not a calibration either" meant.

  **The second criterion was wrong, and a pedal table cannot meet it.** This plan expected
  the table to bring the mean speed near the 10 m/s target. It does not - `junction-1`
  goes 4.41 → 4.19 m/s, `mosque` 3.06 → 3.47 - because **the bridge is not asking to
  accelerate**. Median `accel_cmd` over the `junction-1` run is **-0.30 m/s²** and only 159
  of 1559 calls asked for a positive one, while the car sat at 4 m/s under a 36 km/h
  cruise. The target does reach it (`v_cruise_kph` 36.0), and **doubling it makes the
  bridge brake harder, not accelerate**: at `--target-speed-mps 20` the cruise reads 72.0
  and the median request falls to **-2.003 m/s²**, with the car nearly stopped (that run
  was cut short by a command timeout, so its completion figure is not reported). So the
  undershoot is in the longitudinal *plan*, upstream of any pedal conversion, and it is
  where `CLAUDE.md` already says it would be: `waypoints_from_route` is `route_gt.py`'s
  constant-speed model, so the trajectory encodes "I am going as fast as I am going" and
  carries no speed intent for the e2e planner to read. That is the **model** half, which
  Phase C puts in - not a calibration fault.

  `junction-1` `table` ends `out_of_road` at -4.01 m lateral where `accel` arrives. Both
  steer through identical code - the steering path did not change - so the difference is
  where along the route the car is when the lateral error accumulates. Not diagnosed.

  Also landed: `--log-telemetry` now writes `v_ego_mps` and the `metadrive_action` beside
  the bridge's reply, because the question above cannot be answered from the reply alone.

- [x] **Phase A - camera frames as uint8 on the wire** (2026-08-24)

  **Two of the four heavy sensors are not pictures, and that is what this phase turned out
  to be about.** The plan said "both lines change" over the loop in
  `tools/policy_client.py`, but that loop reads `camera`, `depth`, `semantic` and
  `point-cloud` through one `perceive(to_float=...)` call which reaches **two different**
  `_format` implementations:

  | `--sensors` | class | native | `to_float=False` does |
  |---|---|---|---|
  | `camera` | `RGBCamera` -> `BaseCamera` | uint8 0-255 | `astype(uint8, copy=False)` - free |
  | `semantic` | `SemanticCamera` -> `BaseCamera` | uint8 0-255 | the same - free |
  | `depth` | `DepthCamera` | **float32**, 0-1, nonlinear | `(ret * 255).astype(uint8)` - quantises |
  | `point-cloud` | `PointCloudLidar` -> `DepthCamera` | **metres** | the same - destroys it |

  `base_camera.py:208-214` against `depth_camera.py:184-190`, with
  `point_cloud_lidar.py:33` inheriting the second for data that is not an image at all.
  Measured on the wire over a real drive rather than argued: **depth occupies 0.705-1.000**
  of its 0-1 range, so `* 255` leaves **76 levels for the whole scene**, worst where the
  range is longest; the point cloud runs **-18476.9 to +11030.2 m**. Neither raises. So
  `_UINT8_SENSORS = ("camera", "semantic")` and those two only, with
  `tests/unit/test_policy_client.py` pinning the split against MetaDrive's own source by
  walking each class to whichever `_format` it inherits.

  **The rest is as planned.** Nothing renders float32 - `image_buffer.py:106` reads uint8 -
  and `ret / 255` is what creates the float. Measured on numpy 2.2.6, one 512x288x3 frame:
  442,368 B as uint8, **3,538,944 B** after `to_float=True` (`uint8 / 255` promotes to
  **float64**), 1,769,472 B after the cast down for the wire; and
  `(v / 255 * 255).round().astype(uint8)` returns all 256 values exactly, so there was no
  precision in the float to lose. `encode_array` was already self-describing and
  `policy_server.decode_array`'s numpy-free fallback already had `"uint8": "B"`, so nothing
  downstream changed. `new_parent_node=agent.origin` stays - avoiding that second render is
  the rig's job and belongs in Phase C.

  **Measured on `junction-1`, every row taken twice** - once with `_UINT8_SENSORS` emptied
  and once as shipped, same server and same flags - because a payload size compared against
  a figure from another session is not a measurement of this change. Row 1 is
  `--render offscreen --step-hz 100 --decision-hz 20`, 3788 steps and completion 0.950 both
  times; rows 2 and 3 are at 10 Hz under `--backend constant`, 17 steps both times. KB/step
  is a per-step payload size, so drive length does not enter it.

  | `--render` | `--sensors` | before | after |
  |---|---|---|---|
  | `offscreen` | `camera,imu,route` | 3602.0 KB/step | **2927.0** |
  | `offscreen` | everything (7) | 5002.4 KB/step | **3652.4** |
  | `3D` | `camera,imu,gps` | 901.6 KB/step | **226.6** |

  The offscreen rows fall by far less than 4x, and correctly so: under `--render offscreen`
  the observation is itself a 3-frame float camera stack - MetaDrive's own, nothing to do
  with `--sensors` - which is 2700 KB of the 2927. The `3D` row is the clean one, and 226.6
  against 901.6 is the 4x. `offscreen | -` and the two `none` rows have no camera on the
  wire, did not move, and were not re-measured.

  **The re-measured befores are not identical to the 2026-08-18 table and the gap is the
  point**: 901.6 against 901.5 is the same row, but **5002.4 against 5001.2 is `route`**,
  1.2 KB, which did not exist when that row was first taken. "Everything" is a moving set.
  Both tables (`README.md`, `CLAUDE.md`) now carry matched pairs rather than a new figure
  beside an old one.

  **And the pixels were checked, because none of the above could.** The stub ignores the
  image, so a silently flipped or reordered frame would have given the same KB and the same
  completion. `RGBCamera.perceive` turns out **not to be repeatable** - three identical
  back-to-back float reads of a static scene spread by **1/255**, from MSAA and the
  `taskMgr.step()` inside `perceive` - while `SemanticCamera` is exact. So the check has to
  hold the render still: capture one frame, apply both `_format` paths to it. Done that way
  the uint8 path returns **the raw buffer unchanged** and `(float x 255).round() == uint8`
  exactly, on both cameras. A second probe read the wire during a drive and confirms what
  arrives: `camera` uint8, `semantic` uint8, `depth` float32, `point-cloud` float32.

  `uv run ruff check` passes, MetaDrive's 3.8 parses both changed files, and the suite is
  607 passed plus the pre-existing `3 of 396` ego-route gate failure - 594 before, plus 13
  new tests in `tests/unit/test_policy_client.py`.

  **Two of those 13 exist because the other 11 could not fail.** They cover `encode_array`,
  `decode_array` and the *contents* of `_UINT8_SENSORS`; none of them touches the loop that
  reads the sensors, which needs a live engine. So the pre-Phase-A loop passed all eleven.
  `test_the_sensor_read_takes_its_dtype_from_the_carve_out` and
  `test_the_float32_cast_is_not_applied_to_the_uint8_branch` walk the AST of
  `SensorPack.__call__`, following `test_step_timing.py:130`, and both were **shown to fail
  against the old loop before being kept**. The second one's first version did not: it asked
  whether the function contained any `if` at all, and `__call__` has several, so it passed
  against exactly the code it was written to reject. It now requires the dtype-forcing
  `asarray` to sit *inside* a branch.

  **Phase C removes the wire, so this buys nothing there** - a frame will go from camera to
  model with no encode at all. Phase A's value is entirely the socket path that exists
  today, which `step-timing.sh --rows 3` prices. `to_float=False` still matters in Phase C
  for a different reason: on the CUDA path `ret` is a CuPy array, and CuPy follows numpy's
  own promotion, so `ret / 255` would be an **fp64** divide on the GPU - which the 4050 runs
  at 1/64 of its fp32 rate. Read, not measured; CuPy is not installed to check.

- [x] **Phase B - frames that stay on the GPU** (done, 2026-08-24)

  `--image-on-cuda` on `drive.py`, an opt-in `gpu` dependency group, and the copy back
  written out once in `tools/gpu_frames.py`. The mechanism was as small as expected -
  `engine_core.py:615` is
  `sensor = cls(*args, engine=self, cuda=self.global_config["image_on_cuda"])`, so **one
  env-level key reaches every sensor**, `make_env(**overrides)` already passes MetaDrive
  config keys through, and offscreen the observation stack itself becomes a CuPy array
  (`image_obs.py:55-65`). What the plan had wrong was everything around it.

  ```bash
  uv sync --group sim --group gpu
  cd scripts && METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
      --render offscreen --agent-policy idm --sensors camera --image-on-cuda
  ```

  **What it is worth.** `junction-1`, one 512x288 `RGBCamera`, offscreen, 200 steps after
  30 warm-up, one env per process, three matched pairs:

  | | `env.step` median | observation |
  |---|---|---|
  | CPU path | 7.09 / 8.04 / 8.28 ms | `numpy.ndarray` |
  | `--image-on-cuda` | **3.20 / 3.59 / 3.76 ms** | `cupy.ndarray` |

  2.2x every time. The saving is the readback - `get_rgb_array_cpu` is
  `buffer.getDisplayRegion(1).getScreenshot()`, a synchronous GPU->CPU read, and the
  3-frame stack is then rolled on the host. **On a socket it is worth nothing**, measured:
  2927.0 KB a step either way, byte for byte, because `encode_array` needs host bytes.

  **The drive is identical**, which is the check that says the switch changed only where the
  pixels live: `--agent-policy idm` on `junction-1` gives 291 of 370 steps, completion
  0.774, `out_of_road` at -5.44 m both ways, with `drive.py`'s whole output byte-identical.

  Five things the plan got wrong, each measured before it was rewritten:

  - **`cuda-python==13.3.1`, which is what the plan's own dependency resolve recorded,
    closes the gate.** `base_camera.py:14` is `from cuda import cudart` and cuda-python
    removed that shim at 13.0 for `cuda.bindings.runtime`; 13.3.1 raises
    `ImportError: cannot import name 'cudart' from 'cuda'`. The group pins `<13`.
  - **`cupy-cuda12x` alone is not enough.** CuPy 14 ships no CUDA headers and there is no
    system toolkit here, so it imports and dies on the first kernel with *"Failed to find
    CUDA headers"*. The pin is `cupy-cuda12x[ctk]`. Two smaller routes were tried and both
    fail: `cupy-cuda12x<14` bundles headers but wants `libnvrtc.so.12`, and the pip
    `nvidia-cuda-nvrtc-cu12` / `nvidia-cuda-runtime-cu12` wheels are found by neither
    CuPy 13's `dlopen` nor CuPy 14's `cuda-pathfinder` without `CUDA_PATH`.
  - **A kernel is unavoidable**, which is why the headers are not optional: `_format` is
    `astype(uint8, copy=False, order="C")` or `ret / 255`, and on a CuPy array both compile
    one. `/ 255` also promotes to **float64** on the card, the Phase A trap one bus along.
  - **The negative strides are already resolved and cost nothing.** `_format`'s `order="C"`
    handles the doubly-reversed view; measured on a live drive, `c_contiguous True` and
    strides `(480, 3, 1)`, identical to the CPU path's. No `ascontiguousarray`.
  - **CUDA-GL interop needs the GL context on the same GPU as the CUDA context**, which the
    plan did not mention at all. Without `__NV_PRIME_RENDER_OFFLOAD=1
    __GLX_VENDOR_LIBRARY_NAME=nvidia` the GL context is the Intel iGPU and
    `cudaGraphicsGLRegisterImage` fails with `cudaErrorUnknown(999)` at env construction.

  Three more that only appear once it runs:

  - **The CUDA frame is the same picture and not the same bytes.** Semantic camera, same
    seed and actions: 92.65% of pixels identical, the same four semantic colours and no
    fifth, and 64% of the differing pixels on a colour boundary against a 9.4% base rate.
    A sub-pixel resolve difference between the bound render texture and
    `getScreenshot()` - not a stale frame (no CPU frame at any offset matches) and not a
    channel order (no permutation or flip matches). So the drive is the check, not the
    pixels.
  - **`frame_gate` refuses `image_on_cuda` and `drive.py` installs it on every offscreen
    run**, so the first version of the flag died in a traceback on a plain drive with no
    `--decision-hz`. The gate is now skipped under CUDA and refused by name only where the
    stride would really hold a frame.
  - **`numpy.asarray` on a CuPy array raises rather than copying.** Three places in
    `tools/` write bytes - the sensor frames and the observation in `policy_client`, and
    the `.npz` in `agent_env.ActionRecorder` - and each needs a deliberate copy.
    `tools/gpu_frames.to_host` is that copy; `tests/unit/test_gpu_frames.py` pins all three
    call sites by AST and both version caps by reading `pyproject.toml`, and all six guards
    were shown to fail against the pre-Phase-B code before being kept.

  And **the reference checkout's 3.8 venv has had all three packages installed the whole
  time with `_cuda_enable` False the whole time** (its cupy 12.3.0 fails to import), which
  is why `drive.py` checks `_cuda_enable` itself and refuses by name printing
  `sys.executable`. MetaDrive does raise rather than falling back, but late and from one of
  two places: offscreen `ImageObservation.__init__` (`image_obs.py:57`), which gates on cupy
  alone and so hints at cupy whichever of the three is missing; under `--render 3D`
  `BaseCamera.__init__` (`base_camera.py:56`), hinting "pip install pypiwin32".

  **A review afterwards found six defects and two of them were Keith's call.** All six are
  closed (2026-08-24):

  - **`--render 3D` is now refused**, and the drive was never what failed: 352 of 370 steps,
    `arrive_dest=True`, completion 0.953, and then `env.close()` raising
    `cudaErrorInvalidGraphicsContext(219)` from `MainCamera.unregister`
    (`main_camera.py:585`). MetaDrive's bug, so not patched here. Refused rather than caught
    because a successful drive exiting non-zero destroys the only thing `drive.py`'s status
    means, and because the pairing buys nothing until Phase C - at which point catching that
    one error around `close()` is the way back, should a CUDA-fed model need a window.
  - **`--record --render offscreen` was broken long before Phase B** and is fixed: the
    observation is `{"image", "state"}` offscreen and was being ravelled. That is what had
    left the `to_host` in `ActionRecorder.record` on a path nothing could reach, guarded by
    an AST test that could not fail. The recorder writes the camera stack now - `images`
    uint8 with `image_scale`, 84.4 MB for a 352-step `junction-1` drive - and
    `tests/unit/test_agent_env.py` exercises the copy for real.
  - Four smaller ones: a false "falls back to the CPU silently" claim (it asserts, from two
    places, see above); an orphaned comment; the `--image-on-cuda` proof line reading only
    the observation, so it said "nothing to check" under `--render 3D` where the frame lives
    in the camera; and the fourth `numpy.asarray` on a frame, in `camera_rig.CameraRig.read`,
    written down rather than pre-solved because nothing can reach it today.

- [x] **Phase C.1 - the checkpoint loads, and what it costs** (2026-08-24)

  **Both unknowns answered yes, and a third thing turned up that matters more than either.**
  `tools/model_probe.py`, `scripts/model-probe.sh`, an opt-in `model` dependency group, and
  `tests/unit/test_model_probe.py`. Nothing drives; nothing is written.

  ```bash
  uv sync --group sim --group gpu --group model
  cd scripts && ./model-probe.sh                                # does it load, and what does it cost
  cd scripts && ./model-probe.sh junction-1 -- --with-simulator # the same, beside a renderer
  ```

  **Three of the questions did not need a GPU, a download or torch.** The `.ep` is a `pt2`
  zip and `model_probe.read_archive` reads its graph with `zipfile` and `json`:
  `torch_version` is **`2.8.0+cu128`**, the inputs are `images (1, 5, 6, 3, 288, 512)`,
  `navigation (1, 20, 7)` and `ego_state (1, 5, 2)` in **bfloat16**, and the output is
  **`(1, 20, 8)`**. That last one is the two corrected Known-limits bullets above. Reading it
  first is also what makes a failure legible: a deserialisation error prints what the file
  wanted beside what is installed, rather than a bare TensorRT message about a plan file.

  **1. Does an engine compiled elsewhere deserialise here? Yes.** `sm_89`, RTX 4050,
  TensorRT 10.12.0.36, load 9-13 s (mostly reading 1.2 GB off disk). This was the sharper
  question because the archive is
  **not weights**: `data/weights/model.pt` is **1,261 bytes** beside a **1,275,435,821-byte
  serialized TensorRT engine**, and a TRT engine is built against one SM architecture and one
  TRT version.

  **2. Does it fit? Yes, with 1151 MiB to spare.** Measured on `junction-1` with
  `rigs/cams.txt` mounted offscreen - the seven real cameras, 5.42 MB of image a step:

  | card, of 6141 MiB | alone | beside the simulator |
  |---|---|---|
  | simulator only | - | 2377 MiB |
  | + model loaded | 2561 | 4934 |
  | + one warm-up pass | 2617 | 4990 |
  | **free** | **3524** | **1151** |

  **3. And it takes about a second a pass, which is the finding.** **947-1002 ms** - medians
  over 10, 20 and 50 passes across three runs, best single pass 919. At `--decision-hz 20` a
  decision has **50 ms**, so this is **20x** over. It does not make a drive wrong - `env.step` is the tick, so a slow policy
  makes a slow drive and nothing else - but a simulated second will cost about twenty.

  Three things separate that from a measurement fault, all measured:

  - **It is not the timing loop.** Ten passes with a `cuda.synchronize()` either side average
    **989.9 ms**; ten queued with one synchronize at the end average **999.3 ms**. Identical,
    so nothing is being charged to the sync.
  - **It is compute-bound and the card is capped.** 100% utilisation throughout, at
    **34.6-35.1 W against a `Current Power Limit` of 35 W and a `Max Power Limit` of 60 W**,
    with the SM clock at **975-1335 MHz against a 3105 MHz maximum** and 87-89 C against an
    85 C target. `nvidia-smi -q -d PERFORMANCE` counts 4,339 s of SW power capping and
    4,217 s of SW thermal slowdown. A reference point on the same card in the same state: a
    4096^3 bf16 matmul runs at **14.4 TFLOP/s** (fp32 6.4). **Raising the limit is a machine
    setting and Keith's to make, not this repo's** - and it is not a fix either: even a 2.5x
    uplift leaves ~400 ms against 50.
  - **It opened because it was built portable, on purpose.** Keith asked what made an engine
    compiled elsewhere open here, and the answer is measured rather than inferred: the
    engine's `HW_COMPATIBLE` field is `'1'` and TensorRT reports
    **`HardwareCompatibilityLevel.AMPERE_PLUS`** - the two read independently and agree - while
    the `CompilationSettings` pickled into `SERIALIZED_METADATA` show it was *asked for*:
    `hardware_compatible: True`, beside `enabled_precisions: {bf16}`,
    `immutable_weights: True`, `version_compatible: False` and a 6 GiB `workspace_size`. **So
    it runs on any sm_80-or-newer NVIDIA GPU** and refuses below rather than degrading. An
    earlier version of this entry said the build flags were "not worth chasing"; chasing them
    is what turned "it works on this laptop" into "it works on any Ampere-or-newer card",
    which is a different and much more useful statement.
  - **The portability costs speed, and that cost is not measured.** NVIDIA documents
    `AMPERE_PLUS` as restricting kernel selection to a portable subset, so it is a plausible
    second contributor beside the 35 W cap. Quantifying it needs a `NONE`-level rebuild, and
    **it cannot be rebuilt from here**: the archive carries a compiled engine and a 1.26 KB
    weights stub, with no source model in it, and `immutable_weights: True` means even the
    weights cannot be swapped.
  - **The `DEVICE` field names this laptop and is not a build record.** It is re-derived on
    deserialisation; no build GPU is recorded anywhere in the file. With `AMPERE_PLUS` the
    build card stops mattering, which is the point.
  - **The plan is 956,574,460 raw bytes across 777 layers** (the 1216 MiB in the archive is
    base64, +33%) and declares **1578 MiB of scratch** on top of its weights, which is what
    the measured ~2.5 GB of card is made of.

  Four things not to re-derive:

  - **`uv sync --group model` on its own *removes* `sim` and `gpu`.** uv syncs exactly the
    groups named, so that line takes MetaDrive, panda3d and CuPy out and the next
    `./drive.sh` dies. The line is `uv sync --group sim --group gpu --group model`, and all
    three do coexist - measured, one environment, one interpreter.
  - **numpy did not have to move.** `wing-sim/evaluation/pyproject.toml` pins
    `numpy==1.26.4` beside the identical torch pins; this repo stayed at **2.2.6** and torch
    2.8 resolved against it without complaint. Adopting that pin defensively would have been
    the one change here able to break code that already works.
  - **`torch_tensorrt.load` logs two failures before succeeding**, and neither is an error:
    it tries the `.pt2` package loader (`f must be a buffer or a file ending in .pt2`), then
    `torch.jit.load` (`PytorchStreamReader failed locating file constants.pkl`), and then
    `torch.export.load` works. Reading either as the cause of a later problem is a wasted
    afternoon.
  - **The probe reads `torch/_export/serde`'s `ScalarType`, which is not `torch.ScalarType`.**
    The two disagree from index 1: code **13 is `BFLOAT16` in the serde enum and `quint8` in
    the runtime one**, and reading the graph with the wrong table mislabels every tensor in
    the report without raising. `test_model_probe` asserts the baked table against torch's own
    copy whenever torch is installed.

  **What this leaves for C.2**, stated rather than discovered later: 1151 MiB of headroom is
  **not** measured against `--image-on-cuda`, which puts a CuPy context and the frame stack on
  the same card; and `tools/openpilot_policy.py` sends four waypoints from
  `WAYPOINT_OFFSETS_S` where this model emits twenty, which is a message shape to decide
  rather than a constant to change.

- [x] **Phase C.2 / C.3 - the model in the same process** (2026-08-25)

  `rigs/av3.txt`, `tools/av3_model.py`, `tools/av3_probe.py` / `scripts/av3-probe.sh`, and
  `--camera-rig` / `--model-checkpoint` / `--waypoints` on `drive.py`. C.3 landed first -
  the loader and the five in-conversions, with a probe that checks every one against a
  recorded drive while nothing steers - and C.2 mounted it.

  **Measured, `junction-1` at `--step-hz 100 --decision-hz 20`:**

  | | |
  |---|---|
  | forward pass | 1088-1114 ms median (C.1's 947-1002 on a quieter box), one per decision |
  | conversions 2, 4, 5 | agree over 320 route points, worst 0.0000 m |
  | conversion 6, nav sweep | right arc **+2.172 m**, left arc **+1.062 m** -> +y is RIGHT |
  | model drive, `--waypoints modelv2` | 1870 steps, 374 decisions, completion 0.464, `out_of_road` |
  | model drive, `--waypoints derive` | 1870 / 374 / 0.464 - **identical, and that is the control** |
  | `--backend stub`, no model | 3788 steps, `arrive_dest=True`, completion **0.950** - unchanged |
  | **real bridge**, `--longitudinal table` | 752 steps, 151 decisions, completion 0.163, `out_of_road` |

  **Against the real bridge the model doubles the pace and loses the route**, and the
  criterion this plan set for it was the wrong statistic:

  | `junction-1`, `--longitudinal table` | `route_gt` trajectory (Phase 0) | the model |
  |---|---|---|
  | mean `v_ego` | 4.19 m/s | **8.92 m/s** (max 13.89, target 10) |
  | median `accel_cmd` | -0.30 m/s^2 | **-0.504 m/s^2** |
  | completion | 0.815 | 0.163 |

  The plan said to look for the median `accel_cmd` to stop being pinned negative. It does not,
  and it should not have been asked to: a car **at** its target speed correctly asks to hold,
  which reads negative. The thing Phase 0 actually diagnosed - a car crawling at 4 m/s under a
  36 km/h cruise because the trajectory carried no speed intent - is gone: the pace **doubles**.
  What ends the drive is the lateral, not the longitudinal, and that is the +1.6 m bias the
  probe measures.

  One pass per decision means a full-length route is 758 of them - a quarter of an hour.

  **`mosque` says the same thing independently, and it corroborates the mechanism.** Over 23
  spread decisions of its 3998-step route: conversions 2, 4 and 5 agree over **460** route
  points at worst 0.0000 m, and the nav sweep gives right **+1.500 m** against left **+0.582**
  - same sign, smaller response. Its standing bias is **+1.041 m** where `junction-1`'s is
  +1.617, and 14 of 23 decisions are on a bend against 10 of 16 - and *there* the drive-based
  statistic recovers the right answer on its own: **72%** sign agreement, off-path 0.396 m as
  given against 0.598 m negated. Which is the mechanism stated rather than assumed: the drive
  statistic fails on `junction-1` because the bias is large relative to the model's own
  lateral, not because it is the wrong statistic in principle.

  The two `--waypoints` modes coming out identical against `--backend stub` is not the flag
  failing to take effect: `StubBridge.control` is pure pursuit over `msg["waypoints"]` and
  never looks at `modelv2` at all, so it *cannot* tell them apart. The difference is only
  visible against the real bridge, where `_handle_step` branches on the key.

  **The wire is unregressed and the model drives; what it drives *like* is the domain gap.**
  Over 40 spread decisions of the `test` route it predicts 16.5 m of travel in 2 s where the
  car covers 24.1, with a 0.12 m median lateral where the route bends 27 m at 38 m ahead, and
  a standing **+1.6 m rightward bias**. Four of its six cameras are 105.4 deg fisheyes
  standing in as rectilinear, and the road is a Kuala Lumpur OSM extract rather than
  Town10HD. `av3_probe` reports all of it rather than averaging it away.

  **The bias is also why the waypoint sign could not be settled from a drive.** The
  drive-based statistic leans the wrong way - 27% sign agreement, off-path 0.379 m as given
  against 0.385 m negated - because a constant lateral bias reads exactly like a mirror.
  `--nav-sweep` holds the pictures and the ego state fixed and replaces the navigation with a
  30 m arc, which is the only test that separates them.

  Three things the build turned up that this document had wrong, all corrected above:
  the resize is **not** a no-op (it is a 4:3 -> 16:9 squash, so `rigs/av3.txt` renders
  512x384); the camera map onto `rigs/cams.txt` cannot be made safe and was replaced by a rig
  generated from wing-sim's own spec; and conversion 6 does **not** negate.

  ~~What A and B are for, and the largest piece. Two parts left; C.1 below answered the
  first:~~

  1. ~~**Load the checkpoint.**~~ **Done - see above.** Add the model stack - torch 2.8, torch-tensorrt 2.8,
     tensorrt 10.12, cu128, matching whatever compiled
     `assets/models/step_440000_trt_direct_full.ep`. **Two unknowns, both cheap to
     settle and neither guessable**: whether a Torch-TensorRT program compiled
     elsewhere loads on this RTX 4050 at all, and whether 1.27 GB plus activations fits
     **6141 MiB**. Do this first; it can invalidate everything after it.
  2. **Mount the rig on the car and feed the model.** `tools/drive.py` has **no rig
     support at all** - `rigs/cams.txt` and `tools/camera_rig.py` reach only
     `sensor_survey.py` and `step_timing.py`. Then conversions 1-5, each written and
     each pinned by a test, because none of them raises.
  3. **Lift the loader out of wing-sim.** `av3_trt.py` and `av3_base.py` import
     `configurations.model_dev_config` and `routes.RouteNavigator`, both built around a
     CARLA route parquet that our `route` sensor replaces. This is editing, not
     installing.

  It lands as one program importing MetaDrive **and** torch, sending only four
  waypoints to the bridge container.

  **Verify** - the drive completes on both extracts with the model producing the
  waypoints, and `--backend stub` still completes, so a regression in the wire is
  distinguishable from a regression in the model. Beyond that, what "good" looks like
  for a model's driving is Keith's to set and is not a number this document can supply.

---

## Known limits, stated rather than hidden

- ~~**The waypoint count is not knowable until the checkpoint loads.**~~ **Settled by
  C.1, and it was knowable without loading anything**: the archive's own graph declares
  the output as `(1, 20, 8)`. It is **20 waypoints, not 4**. This document had read
  `av3_base.N_WAYPOINTS = 4` as this model's count, and it is a *fallback used until
  `_set_output_shape` runs* - the comment above it says so. 20 over `MODEL_HORIZON_S =
  2.0` is 0.1 s spacing, and **20 is already in the bridge's prebuilt `AV3_MPC_MENU`
  (`"4 16 20 32"`)**, so there is no connect-time code generation and no slow first tick.
  What is *not* settled is what `tools/openpilot_policy.py` should send: it emits four
  waypoints at `(0.5, 1.0, 1.5, 2.0)` s from `WAYPOINT_OFFSETS_S`, and a 20-point
  trajectory is a different message. C.2's problem.
- ~~**The bridge has an unused richer path.**~~ **Settled by C.1: the output is 8 wide**,
  which is `av3_base.MODELV2_OUTPUT_WIDTH` -
  `[x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]`. So `msg["modelv2"]` / `from_predicted` is
  reachable rather than the 3-wide `derive` form we send today. Whether to use it is a
  C.2 decision and not a foregone one - `derive` is what every measurement in this
  document was taken through.
- **Phase B is worth nothing on a socket, and that is now measured.** `image_on_cuda`
  returns a CuPy array and `encode_array` needs host bytes, so the frame is copied back
  and the run has done strictly more work than the CPU path. Measured on `junction-1`
  through `--agent-policy remote`: **2927.0 KB a step either way**, byte for byte. It
  pays on the socket only in Phase C. It pays in the *simulator* today -- see Phase B.
- **The fork checkout arrives with every symlink missing.** Ten of them, mode 120000 in
  the index; scons dies on `Missing SConscript 'rednose/SConscript'`, which reads as a
  broken Dockerfile. `git -C "$FORK" status --porcelain | awk '$1=="D"'` before any
  build. Repaired once already on 2026-08-23.
- **C.2 and C.3 have not been implemented or measured.** Their figures are either read
  from source or arithmetic, and are marked as such; the measured ones belong to Phases
  0, A, B and C.1, which are built.
- **The model runs at about 1 Hz on this machine, and the mechanism is measured.**
  947-1002 ms a forward pass at 100% GPU utilisation, with the card capped at 35 W of a
  60 W maximum and clocking 975-1335 MHz against 3105. A drive at `--decision-hz 20` will
  therefore run at roughly a twentieth of the pace `--backend stub` sets. It is slow and
  not wrong - `env.step` is the tick - but any C.2 timing has to be read against this,
  and no figure in this document from before C.1 anticipated it.
- **There is one lever on that cost and it is not ours.** The engine is built
  `HardwareCompatibilityLevel.AMPERE_PLUS`, which NVIDIA documents as trading speed for
  portability, and the archive holds no source model to rebuild from. **What to ask the
  model's author**, so the ask is unambiguous: either an engine rebuilt at
  `HardwareCompatibilityLevel.NONE` on the card it will run on - which drops the portability
  penalty and locks the file to that architecture - or the source model, so it can be
  compiled here for whatever machine it lands on. Everything else about the ~1 s pass is
  this laptop's 35 W power cap, which is a machine setting rather than a code one.
- **`--image-on-cuda` needs the discrete card, not merely a card.** CUDA-GL interop
  registers a GL texture with the CUDA context, so both have to be on the same GPU. On
  this hybrid machine that means `__NV_PRIME_RENDER_OFFLOAD=1
  __GLX_VENDOR_LIBRARY_NAME=nvidia`, which `scripts/_common.sh:exec_with_gpu` already
  sets; without them it is `cudaErrorUnknown(999)` at env construction, and nothing in
  that message says "wrong card".
