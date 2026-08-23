# Stage 9 - A model at the wheel

## Status

**Planned, not built.** Nothing in this document has been implemented. Every number
in it is either measured on this machine and marked as such, or arithmetic over a
measured number and marked as such. Where something is unknown it says so rather
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
| **Phase A** | send frames as uint8 | no | no | no |
| **Phase B** | keep frames on the GPU | yes | no | no |
| **Phase C** | the model in-process | yes | yes | no |

Phase 0 is independent of the other three and is **done**: it repaired the half of the
fit that was wrong, and measuring it showed the remaining speed undershoot belongs to
the trajectory rather than to the pedals, which is Phase C.
Phase B is worth nothing without Phase C and is listed separately only because it can
be proven on its own.

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

- [ ] **Phase A - camera frames as uint8 on the wire**

  `tools/policy_client.py:295-296` is:

  ```python
  frame = engine.get_sensor(key).perceive(to_float=True, new_parent_node=agent.origin)
  packed[name] = encode_array(numpy.asarray(frame, dtype=numpy.float32))
  ```

  **Both** lines change - `to_float=False`, and drop the `dtype=numpy.float32`, or the
  cast undoes the first. A picture does not need to be float32 to cross a wire;
  `perceive(to_float=False)` returns uint8 0-255 and MetaDrive's own docstring says so.

  **This removes a conversion rather than adding one, and that is the whole of why it is
  worth doing.** Nothing renders float32: `image_buffer.py:106` is
  `np.frombuffer(origin_img.getRamImage().getData(), dtype=np.uint8)`, so a frame is 8-bit
  when it leaves the GPU, and `_format`'s `ret / 255` (`base_camera.py:215`) is what
  *creates* the float. **Measured on numpy 2.2.6**, one 512x288x3 frame: 442,368 B as
  uint8, **3,538,944 B** after `perceive(to_float=True)` - `uint8 / 255` promotes to
  **float64**, not float32 - and 1,769,472 B after the cast down for the wire. The path
  today therefore inflates 8x on the CPU and immediately discards half of it. Also
  measured: `(x / 255 * 255).round().astype(uint8)` returns the original array exactly for
  all 256 values, so the float32 currently sent is an 8-bit quantity stored in 32 bits and
  there is no precision in it to lose.

  **The `/255` is not dropped, it moves.** `assets/modifiers/modifiers.py` already does
  BGR->RGB, the transpose and the divide as one preprocessing step, because that is the
  contract with the weights - so today it happens twice, once in float64 on the CPU here
  and again there. Sending uint8 leaves it happening once, on the GPU, fused with the two
  reorderings the model needs anyway. **uint8, not int8**: 0-255 unsigned, where int8's
  -128..127 genuinely would lose data. And this is not int8 *quantisation* - weights and
  activations stay fp32/fp16 under TensorRT, and only the transport dtype of an 8-bit
  picture changes.

  Nothing downstream changes: `examples/policy_server.py:91` is already
  `numpy.frombuffer(raw, dtype=encoded["dtype"])`, which is what `encode_array`'s
  self-describing payload exists for. **`tools/camera_rig.py:220` already defaults to
  `to_float=False`** - this brings `policy_client` into line rather than inventing a
  convention.

  **Arithmetic, not measurement:** seven cameras are 5.42 MB of raw uint8 (6 × 512 ×
  288 × 3 plus 1 × 1280 × 720 × 3 = 5,419,008 B). As float32 that is 21.7 MB and after
  base64 **28.9 MB a step**; as uint8, **7.2 MB**.

  `new_parent_node=agent.origin` **stays**. It forces a second scene render
  (`base_camera.py:188`), which is real cost, but the camera `sensor_config` registers
  is not otherwise mounted to the car. Avoiding that render is the rig's job -
  `CameraRig.read` passes no parent node - and belongs in Phase C.

  **Phase C removes the wire, so this buys nothing there** - a frame goes from camera to
  model with no encode at all. Phase A's value is entirely the socket path that exists
  today, which is what `step-timing.sh --rows 3` prices. `to_float=False` still matters in
  Phase C for a different reason: on the CUDA path `ret` is a CuPy array, and CuPy follows
  numpy's own promotion, so `ret / 255` is an **fp64** divide on the GPU - which the 4050
  runs at 1/64 of its fp32 rate. Read, not measured; CuPy is not installed to check.

  **This invalidates two measured tables**, `README.md` ~line 1000 and `CLAUDE.md`
  ~line 743, both "what it costs per step". **Re-measure, do not edit**; `drive.py`
  prints KB/step. Note the `--render offscreen` rows also carry MetaDrive's own float
  observation stack, which is unaffected, so those rows fall by **less** than 4x -
  reporting them as if they had would be wrong.

  **Verify** - `uv run pytest` stays at 565 passed plus the pre-existing `3 of 396`
  gate failure; `ruff` passes; **both** interpreters parse the file, since `tools/`
  runs under MetaDrive's 3.8 as well as this repo's 3.10. A drive with
  `--sensors camera` completes, the printed KB/step has dropped, and the model side
  decodes unchanged. A new `tests/unit/test_policy_client.py` - there is none today -
  pins that a camera frame round-trips as uint8 0-255 and that `encode_array` still
  round-trips a float32 observation unchanged.

- [ ] **Phase B - frames that stay on the GPU**

  Smaller than it looks. `engine_core.py:615` is
  `sensor = cls(*args, engine=self, cuda=self.global_config["image_on_cuda"])`, so
  **one env-level key reaches every sensor**, and `agent_env.make_env(**overrides)`
  already passes MetaDrive config keys straight through. And `base_camera.py:59` sets
  `cuda_dtype = np.uint8`: **the GPU path is already uint8**, so conversion 1's `/255`
  happens on the GPU where it belongs.

  - Add an opt-in group beside `sim`, so `uv sync` keeps working without a CUDA stack:
    `gpu = ["cupy-cuda12x", "PyOpenGL", "cuda-python"]`. **`cupy-cuda12x`, not
    `cupy-cuda13x`**, though the driver (595.84) offers CUDA 13.2 - the model stack is
    pinned at cu128 and CuPy will share a process and a context with torch.
  - **None of the three is installed today.** A dependency resolve - which downloads
    nothing - shows they install cleanly on this repo's Python 3.10:
    `cupy-cuda12x==14.2.0`, `pyopengl==3.1.10`, `cuda-python==13.3.1`, against the
    `numpy==2.2.6` already here.
  - `make_env(..., image_on_cuda=True)` behind a flag, so the CPU path stays default
    and comparable.

  **Verify** - the gate fails silently, so check it directly:
  `_cuda_enable` in `base_camera.py:10-18` is one `try/import` over all three packages.

  ```bash
  uv run python -c "from metadrive.component.sensors.base_camera import _cuda_enable; print(_cuda_enable)"
  ```

  Then prove a frame is really on the card rather than trusting the flag: a
  `cupy.ndarray` carrying `__cuda_array_interface__` is the win condition; a
  `numpy.ndarray` means it silently took the CPU path. And a drive with and without
  `image_on_cuda` must produce the same trajectory, confirming the switch changed only
  where the pixels live.

  **Two things expected to bite.** `perceive` returns a doubly-reversed view
  (`base_camera.py:197`), so negative strides - torch's zero-copy import may need
  `cupy.ascontiguousarray` first, a cheap GPU-side copy that must be measured rather
  than assumed away. And this path is **barely exercised upstream**: the only uses in
  the whole MetaDrive checkout are under `tests/vis_functionality/`. Keep the CPU path
  selectable.

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
- **Phase B is worth nothing on a socket.** `image_on_cuda` returns a CuPy array and
  `encode_array` calls `.tobytes()`, which drags it straight back to main memory. It
  pays only in Phase C.
- **The fork checkout arrives with every symlink missing.** Ten of them, mode 120000 in
  the index; scons dies on `Missing SConscript 'rednose/SConscript'`, which reads as a
  broken Dockerfile. `git -C "$FORK" status --porcelain | awk '$1=="D"'` before any
  build. Repaired once already on 2026-08-23.
- **Nothing here has been implemented or measured.** The measured figures quoted are
  from the bridge bring-up on 2026-08-23 and belong to the *current* state, not to this
  plan's outcome.
