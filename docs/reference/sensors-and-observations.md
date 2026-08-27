# Sensors, observations and the policy socket

What the observation actually contains, the four real sensor modalities, how a frame crosses
the wire, keeping a frame on the GPU, and what a recording holds.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

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
