# Stage 9 - A model at the wheel

## Status

**Phases 0, A and B are built and measured; C is planned, not built.** Every
number here is either measured on this machine and marked as such, or arithmetic over
a measured number and marked as such. Where something is unknown it says so rather
than estimating.

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
| **Phase C** | the model in-process | yes | yes | no |

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

1. `modifiers.py` does exactly this and is usable as-is. **The resize is a no-op for
   us** - CARLA rendered 1440x1080 and `rigs/cams.txt` renders 512x288 already - and
   **MetaDrive's frames are BGR natively** (`base_camera.py:100-113`), the same as
   CARLA's, so no extra swap. What remains is BGR->RGB, transpose, `/255`.
2. **The most dangerous of the eight.** `model_dev.yml` states the order is a contract
   with the weights: reorder it and the model still runs, and is wrong. Map by yaw and
   verify; never pair by position in the list.
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

| 6 | waypoint frame | model emits x forward, **y right** (CARLA) | our frame is **y left** -> negate |

Already the convention in `waypoints_from_route`, which writes `-float(left)`.

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

- [ ] **Phase C - the model in the same process**

  What A and B are for, and the largest piece. Three parts, and the first is the one
  that answers the unknowns:

  1. **Load the checkpoint.** Add the model stack - torch 2.8, torch-tensorrt 2.8,
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

- **The waypoint count is not knowable until the checkpoint loads.**
  `AV3Base._set_output_shape` reads it off the backend's warm-up output shape, which is
  why the bridge's `AV3_MPC_MENU` is `"4 16 20 32"`. Our current four waypoints at
  `(0.5, 1.0, 1.5, 2.0)` s happen to match `av3_base.N_WAYPOINTS = 4` over
  `MODEL_HORIZON_S = 2.0` exactly, and match `OPENPILOT_TRAJECTORY_TYPE=0`. A count
  outside the menu is code-generated at connect time - a slow first tick, not an error.
- **The bridge has an unused richer path.** `msg["modelv2"]` selects `from_predicted`
  instead of `derive` for a model shipping the full 8-wide
  `[x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]` (`WAYPOINT_OUTPUT_WIDTH = 2` against
  `MODELV2_OUTPUT_WIDTH = 8`). We send the 3-wide form. Whether this checkpoint
  produces 8-wide output is part of Phase C.1.
- **Phase B is worth nothing on a socket, and that is now measured.** `image_on_cuda`
  returns a CuPy array and `encode_array` needs host bytes, so the frame is copied back
  and the run has done strictly more work than the CPU path. Measured on `junction-1`
  through `--agent-policy remote`: **2927.0 KB a step either way**, byte for byte. It
  pays on the socket only in Phase C. It pays in the *simulator* today -- see Phase B.
- **The fork checkout arrives with every symlink missing.** Ten of them, mode 120000 in
  the index; scons dies on `Missing SConscript 'rednose/SConscript'`, which reads as a
  broken Dockerfile. `git -C "$FORK" status --porcelain | awk '$1=="D"'` before any
  build. Repaired once already on 2026-08-23.
- **Phase C has not been implemented or measured.** Its figures are either read from
  source or arithmetic, and are marked as such; the measured ones quoted elsewhere
  belong to Phases 0, A and B, which are built.
- **`--image-on-cuda` needs the discrete card, not merely a card.** CUDA-GL interop
  registers a GL texture with the CUDA context, so both have to be on the same GPU. On
  this hybrid machine that means `__NV_PRIME_RENDER_OFFLOAD=1
  __GLX_VENDOR_LIBRARY_NAME=nvidia`, which `scripts/_common.sh:exec_with_gpu` already
  sets; without them it is `cudaErrorUnknown(999)` at env construction, and nothing in
  that message says "wrong card".
