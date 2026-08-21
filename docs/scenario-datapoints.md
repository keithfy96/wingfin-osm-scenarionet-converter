# Scenario datapoints — what every number means

A reference for the files `tools/sensor_survey.py` writes, and for the numbers inside
them. `README.md` says how to *run* the survey and which modalities exist; this says
what a single column or index actually holds.

Every figure below was **measured** on `workspaces/mosque` and `workspaces/junction-1`
and is quoted with the workspace it came from. Nothing here is read off the source and
assumed.

> This document describes MetaDrive's outputs, which this repo does not own. It is not
> a converter policy — see `docs/policies/README.md` for what belongs there.

---

## 1. What produces these files, and when

```bash
./scripts/sensor-survey.sh junction-1
```

The survey drives `<workspace>/scenarionet`, scenario 0. Under the default
`--policy idm` it uses MetaDrive's `TrajectoryIDMPolicy`, whose reference line **is
the Stage 6 ego tape** — the route drawn in `inspection/stage-6-route-builder.html`
and baked in by `convert --routes`.

**It follows that route; it does not replay it.** IDM re-derives steering and throttle
from the car's actual state on every step, so what lands in `track.csv` is its own
drive along your route. That is why `junction-1` ends `out of road` after 291 steps
while `mosque` runs to the 400-step cap.

| file | shape | captured |
|---|---|---|
| `survey.txt` | the printed report | — |
| `observation.npy` | `(steps, 161)` float32 | **every step** |
| `track.csv` | 23 columns × steps | **every step** |
| `rgb_camera.png` | `(180, 320, 3)` | **one frame**, step 40 |
| `depth_camera.png` | `(180, 320, 1)` | **one frame**, step 40 |
| `semantic_camera.png` | `(180, 320, 3)` | **one frame**, step 40 |
| `point-cloud.npy` | `(64, 200, 3)` | **one frame**, step 40 |

One step is 0.1 s of simulated time by default, so `mosque`'s 400 steps are 40 s of
driving. Every figure in this document was taken at that default; `--step-hz` changes it,
and every "0.1 s" below should be read as "one step".
The sampled step is `--sample-at`, default 40.

---

## 2. The sensor mount — one place, four sensors

`sensor_survey.py` calls `perceive(new_parent_node=env.agent.origin)` with no
`position` or `hpr`, so MetaDrive falls back to `DEFAULT_SENSOR_OFFSET (0, 0.8, 1.5)`
and `DEFAULT_SENSOR_HPR (0, -5, 0)` (`constants.py:554-555`). All four sampled sensors
therefore share one mount: **0.8 m forward of the car's origin, 1.5 m up, pitched 5°
down.**

```
                    0.8 m fwd · 1.5 m up · 5° nose-down
                             ▼
                          ╔═════╗   RGB + depth + semantic + point cloud
             ┌────────────╫─────╫────────────┐
             │    ▓▓      ║     ║      ▓▓    │   ego origin, z = 0.000 .. 0.539 m
             │            ╚══╤══╝            │   (that span is suspension travel)
          ═══╧═══════════════╧═══════════════╧═══  ground
                             │
                    1.5 m mount + 0.53 m ride height = 2.03 m
                    ↳ which is the z of every point in point-cloud.npy ✓
```

That mount explains four separate numbers at once: the cloud's ground plane sits at
z ≈ −2.03 m; the 5° down-pitch is why 19 of 64 cloud rows see only sky; it is why the
furthest row lands at 136 m rather than at the horizon; and it is the eye height in
all three camera PNGs.

---

## 3. `track.csv` — column by column

23 columns, one row per step.

| columns | what it is | units / frame |
|---|---|---|
| `step` | `env.step` index; × the step (0.1 s by default) = simulated time | — |
| `x, y, z` | `env.agent.origin.getPos()` — MetaDrive world position | m, **scenario-recentred**: row 0 is exactly `0.0, 0.0`. `z` is **ride height** (0.000–0.539 m on both workspaces), not altitude |
| `projected_x, projected_y` | `x, y` minus `metadata.old_origin_in_current_coordinate` — undoes the recentring | m in **Stage 1's projection**. `mosque` shift `+208.698, +397.412`; `junction-1` `−55.725, +75.469` |
| `latitude, longitude` | `geodesy.aeqd_inverse` of the above | WGS 84 degrees, **exact** — 0.000000 m against `pyproj` |
| `speed_mps` | `env.agent.speed` | m/s, and **planar** — exactly `hypot(vx, vy)` to 1.8e-15; `vz` is excluded |
| `vx, vy, vz` | `body.get_linear_velocity()` | m/s, **world axes** — see §5 |
| `wx, wy, wz` | `body.getAngularVelocity()` | rad/s, world axes — but see §4, `wz` is usable as-is |
| `ax, ay, az` | velocity differenced over one step, 0.1 s by default | m/s², **world axes**, **no gravity** — see §5 |
| `roll, pitch` | `env.agent.roll` / `.pitch` | rad. MetaDrive deliberately swaps panda3d's P and R here (`base_object.py:402-417`), so these are already in car convention |
| `heading` | `heading_theta` | rad, wrapped to ±π |
| `steering, throttle` | the action **requested** on that step | nominally [−1, 1], **not enforced** — see §5 |

### The axis convention

Nothing in the repo stated this before. Over the `mosque` drive:

| | moved | which is |
|---|---|---|
| `dx` | **−367.6 m** | `dlon` **−0.003308°** |
| `dy` | **−162.4 m** | `dlat` **−0.001469°** |

So **+x is east and +y is north**, and `heading` follows from that: `0` ≈ due east,
`π/2` ≈ due north, `π` ≈ due west. `mosque`'s drive sits at 177.5°, heading nearly due
west, which is what the lat/long change says independently.

### What each of the less obvious columns is *for*

**`x, y, z` is not GPS.** It is the simulator's own metre grid with the origin at
wherever the ego started *this* scenario. No noise, no drift, no dropout, no
multipath. It is the **truth you score a GNSS model against**, not a GNSS reading. If
you want receiver-like behaviour you have to add the error yourself.

**`projected_x, projected_y` exists because `x, y` is not comparable to anything.**
MetaDrive re-centres every scenario on the ego's first position, so two drives off the
same map get two different origins. It records the shift it applied, and these columns
add it back — putting you in the metre grid Stage 1 chose for the whole map. Three
things that buys: two drives on one map share coordinates; the track lies straight
over the OSM geometry; and it is the input to the lat/long conversion.

**`latitude, longitude` is the real global map** — WGS 84, the numbers you would paste
into a mapping site. Exact rather than fitted: the dataset carries the exact
projection Stage 1 used (`metadata.coordinate_system_wkt`), so this inverts a known
transform.

**`wx, wy, wz` is angular velocity** — how fast the car is *rotating* about each axis.
`wz` is the yaw rate, the one that matters, peaking at 0.475 rad/s on `mosque` and
0.870 rad/s on `junction-1` (≈ 27°/s and 50°/s) at junction turns. `wx` and `wy` are
roll rate and pitch rate — the body rocking on its springs, 0.10–0.18 rad/s.

**`roll` and `pitch` are the car's tilt.** Roll is leaning left/right through a corner,
pitch is nose up/down under braking. Both tiny here because the roads are flat, so
they are pure suspension: roll −0.32°…+0.80° on `mosque` and −0.93°…+1.20° on
`junction-1`; pitch −2.38°…0.00° on both.

**`heading` is which way the nose points** — yaw, in the world frame described above.

---

## 4. Which columns are the IMU

An IMU is accelerometers plus gyroscopes. **Six columns, not twelve:**

| | columns | what it is |
|---|---|---|
| accelerometers | `ax, ay, az` | **real IMU output** |
| gyroscopes | `wx, wy, wz` | **real IMU output** |
| attitude | `roll, pitch, heading` | what an **AHRS** *computes* from an IMU |
| velocity | `vx, vy, vz` | what an **INS** *computes* from an IMU, normally GNSS-fused |

`survey.txt` prints all twelve under one heading that says `IMU`, which is where the
loose usage comes from. The last six are read straight out of the physics engine, so
they arrive exact and driftless — which is precisely what an integrated solution never
is. Treating them as IMU output makes the data look better than it is.

**Where the world-axis problem in §5 actually bites:**

- `ax, ay, az` — **materially wrong** for IMU use. Must be rotated.
- `wx, wy, wz` — technically world-frame too, but the car never tilts past **2.38°**,
  so world-z and body-z differ by `cos = 0.999140`. **`wz` is the body yaw rate to
  within 0.1%** and is usable exactly as it stands. `wx, wy` differ more in principle
  and peak at 0.10–0.18 rad/s — suspension, nothing else.
- `vx, vy, vz` — world-frame and the rotation matters, but not IMU output anyway.
- `roll, pitch, heading` — these *describe* the body relative to the world, so there
  is nothing to rotate. Already what you want.

**Corroboration that `wz` really is the yaw rate.** Against the finite difference of
`heading` it correlates **0.9519** on `junction-1` and **0.7507** on `mosque` (whose
drive is mostly straight, so there is little signal to correlate), with a mean absolute
difference of **0.026 rad/s**. The residual is expected rather than a discrepancy:
`heading` is sampled once per step — 0.1 s by default — while `wz` is the instantaneous rate at that
instant, with five physics substeps in between.

---

## 5. Four traps in `track.csv`, and whether each can be corrected

### 5.1 The velocity and acceleration columns are in world axes

**What it is.** `vx` means "how fast east", not "how fast forward". If the car does a
U-turn at constant speed, `vx` flips sign even though nothing about the car's own
motion changed. A real IMU is bolted to the car and can only report in the car's frame.

Measured on `mosque`: `vx` matches `speed·cos(heading)` to **0.572 m/s** — the residual
being sideslip — against **27.764 m/s** if read as body-frame. On `junction-1`, 0.832
against 12.764.

**Correctable? Exactly, and nothing is lost.** One rotation by the `heading` already
sitting in the same row:

```python
body_vx =  vx * cos(h) + vy * sin(h)     # forward
body_vy = -vx * sin(h) + vy * cos(h)     # sideways (sideslip)
```

Verified on both workspaces: `hypot(body_vx, body_vy)` reproduces `speed_mps` to
**3.6e-15**. Forward speed comes out 10.68–13.89 m/s on `mosque` and 8.80–13.89 on
`junction-1`; sideslip comes out −0.255…+0.686 and −0.851…+1.132 m/s — small, which is
what sideslip should be for a car that is not drifting.

This is a labelling failure, not a data failure. Everything needed is in the file.

### 5.2 `az` has no gravity in it

**What it is.** A real accelerometer does not measure acceleration — it measures the
force holding it up, which is why one lying on a desk reads **+9.81** rather than zero.
These columns are pure kinematics (velocity differenced), so a stationary car reads 0.
Range on both workspaces: **−2.37 to +8.28 m/s²**.

**The gravity part is correctable.** Add 9.81 and you have specific force: the range
becomes **+7.44 to +18.09 m/s²**, the shape a real sensor produces. Strictly you should
rotate gravity by roll and pitch first, but at ≤2.38° of tilt that changes nothing
measurable.

**One thing is not correctable, and one has since been built.**

- **There is no sensor error at all** — no bias, drift, scale factor, noise,
  quantisation or temperature response. That is not a mistake in MetaDrive; it simply
  is not modelled. Making it realistic means *choosing and injecting* an error model,
  which is a modelling decision someone makes, not a correction someone applies.
  Nothing in the data tells you what the numbers should be.
- **It was a 10 Hz signal, and no longer has to be.** Differenced over the 0.1 s step,
  where real IMUs run 100–1000 Hz. Bandwidth cannot be post-processed back into a
  signal that never had it, and the only route was running the simulator faster, via
  `physics_world_step_size` and `decision_repeat`. That is what `--step-hz` now does:
  `tools/sensor_survey.py --step-hz 100` differences the same IMU over 0.01 s, and
  `tools/drive.py --step-hz 100` calls a policy at 100 Hz. The tables in this document
  were all taken at 10 Hz, which is still the default and still what an unflagged run
  produces. See `docs/implementation-plan/adjustable-simulation-sample-rate.md`.

### 5.3 The action columns record what was requested, not what was applied

**What it is.** `IdmDriver` returns whatever acceleration the IDM formula produces, and
that formula does not know the pedal only travels so far. MetaDrive does not object —
`action_check` is off by default, so `EnvInputPolicy` quietly clamps and drives on.

| | `mosque` | `junction-1` |
|---|---|---|
| `throttle` range | −8.313 … +0.330 | −8.313 … +0.896 |
| rows outside [−1, 1] | **2** of 400 | **2** of 291 |
| `steering` range | −0.075 … +0.312 | **−5.977** … +1.158 |
| rows outside [−1, 1] | 0 | **10** of 291 |

Those rows describe an input the car never received.

**Correctable? Exactly.** MetaDrive's clip is `min(max(a, -1), 1)` and nothing else, so
`np.clip(column, -1, 1)` reproduces what actually reached the wheels. Checked: **no
`NaN`s** in either file — which matters, because `NaN` is the one case clipping would
not save you (`min(max(nan, -1), 1)` is `nan`, and it reaches `setSteeringValue`
unfiltered).

**Keep both, though.** The clipped value tells you what the car got; the raw value is
the only evidence the controller was **saturated** — asking for eight times the braking
available. Replace one with the other and a permanently pinned controller looks like a
well-behaved one.

### 5.4 The first three rows of acceleration are a spawn artefact

Step 0 is forced to zero for want of a previous velocity, so step 1 is the first real
difference and catches the car being dropped into the world. Identical on both
workspaces, which is the tell that it is the spawn and not the map:

| step | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `mosque` \|a\| m/s² | 0.00 | **16.46** | **15.67** | 0.91 | 0.75 | 0.60 |
| `junction-1` \|a\| m/s² | 0.00 | **16.47** | **15.67** | 0.92 | 0.76 | 0.61 |

1.68 g for two steps, then it settles. **Discard steps 0–2** before fitting anything.

---

## 6. `observation.npy` — index by index

This is the array `env.step()` returns. It exists to be fed to a neural network:
everything is squashed to `[0, 1]`, and several entries are answers a real car would
have to work out for itself. **It is not sensor data.**

| index | width | what it actually is |
|---|---|---|
| `0:12` | 12 | **Side detector.** 12 raycasts against the *static* world, 50 m — kerbs and road edges. The only thing in here that sees the road. |
| `12` | 1 | Heading difference between the car and its current lane |
| `13` | 1 | Speed, as `(km/h + 1) / (max km/h + 1)` |
| `14` | 1 | Current steering angle |
| `15` | 1 | Last step's throttle |
| `16` | 1 | Last step's steering |
| `17` | 1 | Yaw rate — angle turned in the last step, ÷ that step (0.1 s by default) |
| `18` | 1 | Sideways offset from the lane centre |
| `19:37` | 18 | **The route.** The next **9** waypoints along the Stage 6 route, each as *(how far ahead, how far right)* in the car's own frame, clipped at 30 m |
| `37` | 1 | Sideways distance from the route line, ÷ `max_lateral_dist` (4 m) |
| `38` | 1 | Heading error against the route |
| `39:41` | 2 | **Permanently 0.0.** Space allocated for a 10th waypoint and never written — `trajectory_navigation.py:130-142` fills 9. Confirmed constant zero on both workspaces. |
| `41:161` | 120 | **Ray lidar**, 120 lasers, 50 m — **all exactly 1.0**, because it scans only the dynamic world and our scenarios hold one car |

**39 of 161 numbers carry information** on both workspaces: 12 road edges, 7 about the
car's own state, 20 about the route. The remaining 122 are a blind sensor and two dead
slots.

**The route block is not perception.** A real car has to work out where it is meant to
go. This vector is handed the next nine points of the answer, in its own frame, every
step. A model trained on it is a lane-follower, not a driver — which is what the
cameras and the point cloud are for.

---

## 7. `point-cloud.npy` — what it is and how to read it

`PointCloudLidar` is **not a spinning lidar**. It is `DepthCamera` with the depth
buffer reprojected into coordinates — its own class docstring says so
(`point_cloud_lidar.py:33-36`). So `(64, 200, 3)` is **64 image rows × 200 image
columns**, and each row, landing on flat ground, reprojects to a **straight line**
across the road.

Measured structure, identical on both workspaces because this is sensor geometry
rather than map:

| | `mosque` | `junction-1` |
|---|---|---|
| rows that hit anything | **45 of 64** | 45 of 64 |
| rays within 200 m | **9000 of 12800** (70.3%) | 9000 of 12800 (70.3%) |
| nearest / furthest row | 6.74 m / **136.24 m** | 6.74 m / 131.88 m |
| gap between rows | **0.15 m** near / **39.85 m** far | 0.15 m / 37.41 m |
| swath width | 8.75 m near / **172.98 m** far | 8.75 m / 169.27 m |
| horizontal FOV | **64.8°** | 65.4° |
| vertical FOV | **23.3°** | 23.1° |
| lateral sample spacing | 0.044 m near / 0.87 m far | 0.044 m / 0.85 m |
| z of every return | **−2.10 … −2.03 m** | −2.06 … −1.96 m |
| raw extent | 7.0 … **18603.7 m** | 7.0 … 18476.1 m |

```
 side view — the car at the origin, looking along its own heading   (mosque)

   row 63 ┄┄┄► sky, no return          ┐
      ...                              ├ 19 of 64 rows at or above the horizon;
   row 45 ┄┄┄► sky, no return          ┘  they land on the far plane, ~18.6 km

      sensor
        ▼ z = 0
     ┌──────┐
     │  ██  │ 2.03 m
  ═══╧══════╧══╤═══╤══╤═╤╤╤╤╤═════╤════════════╤═══════════════════╤═══ ground
              6.7  8.2 10 14    22.4         51.0               136.2 m
              r0   r8 r16 r24   r32          r40                 r44
              └── 22 rows inside 13 m ──┘                          │
                  (the dense blob)                                 │
                     gap between rows: 0.15 m near ────────────────┴── 39.85 m far


 plan view — the same rows seen from above
                                                     ← 172.98 m wide →
    ─────  r44                          ······································
    ─────  r40                     ·······················
    ─────  r32                ·············
    ══███  r0..r22      ▓▓▓▓▓▓        ← 8.75 m wide, 0.044 m between samples
           car ▲ heading                horizontal FOV 64.8°
```

Three things follow, and each is a question this document exists to stop being asked
twice:

- **The dark between the lines is the sensor's vertical resolution, not a hole in the
  road.** There are 45 rows and they spread apart by a factor of 265 with distance.
- **The far row is 173 m wide** — many times any road — so what is being returned is
  MetaDrive's terrain, tarmac and grass alike, with no edge and no occlusion, because
  there is nothing out there to occlude.
- **The ground is dead flat**, within 0.1 m of z = −2 m across the whole cloud, so
  colouring the cloud by height shows nothing. Colour it by range instead, which is
  what `tools/view_point_cloud.py` defaults to.

### The frame is the world's, with the origin at the car

`ego_centric=True` zeroes only the **translation**. The rotation matrix is still built
from the camera's **world** hpr (`point_cloud_lidar.py:66-75`), so the axes are the
world's. This is why the two files look completely unrelated:

| | heading at step 40 | where the whole cloud sits |
|---|---|---|
| `mosque` | **177.5°** | x **−140.5 … −6.5** |
| `junction-1` | **−85.4°** | y **−139.2 … −6.4** |

Rotate each by its own recorded heading and they become the same object: **6.7 to
136 m ahead, ±86 m either side.** Anything feeding this array to a model must rotate
it, or the input spins with the compass while the scene ahead of the car does not
move.

> `README.md` and `CLAUDE.md` still describe this cloud as being "in the car's own
> frame", as do two comments in `tools/sensor_survey.py` (lines 174-175 and 337). They
> are wrong, and correcting them is deliberately held for a later pass.

---

## 8. Two unrelated sensors are both called "lidar"

This is MetaDrive's naming, not ours, and it is the single most confusing thing about
the survey's output. Your two `.npy` files hold one each.

| | `point-cloud.npy` | `observation.npy[:, 41:161]` |
|---|---|---|
| class | `PointCloudLidar(DepthCamera)` → `BaseCamera` | `Lidar(DistanceDetector)` → `BaseSensor` |
| how it works | renders a depth buffer, reprojects it | 120 Bullet raycasts, `rayTestClosest` |
| what it scans | whatever is drawn — terrain, road, any car in view | `physics_world.dynamic_world` only (`lidar.py:172`) |
| coverage | 65° frustum, forward only | 360° ring |
| on our data | the flat ground fan of §7 | **constant 1.0** — one car, nothing dynamic to hit |

They are blind to precisely what the other sees. Three consequences, none of them
guessable from the name:

- **The 3-D one costs a render pass.** Being a `BaseCamera`, it is deleted outright
  unless `use_render` or `image_observation` is set (`base_env.py:342-346`), and one
  surviving camera forces `RENDER_MODE_OFFSCREEN` (`:387-390`). That is why
  `sensor_survey.py` refuses `--render none`. The raycast lidar has no such cost.
- **Its samples are even in image space, not in angle** — hence 0.15 m between ground
  rows near and 39.85 m far. A real spinning lidar bunches too, but by elevation angle
  and symmetrically about the head: a genuinely different pattern.
- **A miss is not a miss.** Unhit rays land on the depth buffer's far plane and come
  back as coordinates ~18.6 km away, which is why the raw extent of the file looks
  absurd. A real lidar returns nothing.

---

## 9. Looking at the point cloud

```bash
./view-point-cloud.sh junction-1
./view-point-cloud.sh mosque -- --colour distance-ahead
./view-point-cloud.sh junction-1 -- --max-range 0    # keep the far-plane misses
```

Drag to orbit, scroll to zoom, `h` in the window for the rest. The tool reshapes
`(64, 200, 3)` to a flat list and drops the far-plane misses, both of which the naive
three-line Open3D snippet does not — without them the viewer autoscales to an 18 km
sphere and the road is a dot.

---

## 10. A multi-camera rig — `--camera-rig`

Everything above describes **one** forward camera at MetaDrive's default mount. A real
vehicle carries several, and the spec for a rig is a file rather than an argument:

```bash
./scripts/sensor-survey.sh junction-1 -- \
    --camera-rig rigs/cams.txt
./scripts/sensor-survey.sh junction-1 -- \
    --camera-rig rigs/cams.txt --rig-record --steps 60
```

`tools/camera_rig.py --check-frame <dataset>` re-measures the conversion below against a
live engine, and `python tools/camera_rig.py <spec>` prints the resolved rig without
starting one.

### The spec is CARLA's frame and MetaDrive's is not

`cams.txt` is a CARLA sensor spec: `x` forward, `y` right, `z` up, **`yaw` positive to
the right**. MetaDrive's vehicle-local frame is different, and the difference was
measured rather than reasoned — a `NodePath` parented to `env.agent.origin`, given a
local pose, and read back in world coordinates against the car's own heading:

```
local +y 1 m  ->  ahead +1.000 m, right -0.000 m       MetaDrive: +y forward,
local +x 1 m  ->  ahead +0.000 m, right +1.000 m                  +x right, +z up
H = +55       ->  +55.00 deg from the car's heading    H positive turns LEFT
H = -55       ->  -55.00 deg from the car's heading
```

So the conversion is an **x/y swap** and a **sign flip**, neither of which is a rename:

| spec (CARLA) | MetaDrive |
|---|---|
| `transform.x` (forward) | `position[1]` |
| `transform.y` (right) | `position[0]` |
| `transform.z` (up) | `position[2]` |
| `transform.yaw` (+ right) | `hpr[0] = -yaw` (+ left) |
| `fov` | `lens.setFov(fov)` — **horizontal**; vertical follows the aspect ratio (70 → 43.0°, 120 → 88.5° at 16:9) |
| `tick_rate` | must be `0.1`, MetaDrive's own step — nothing here resamples |

`pitch` and `roll` are `0.0` on every camera in the spec this was built for, so neither
sign has ever been measured against MetaDrive. A non-zero one is **refused**, not guessed.

### `cams.txt` disagrees with itself about the sign of `yaw`

Under **no** single convention are all four side cameras named correctly:

| reading | `cam_left` −55 | `cam_right` +55 | `cam_back_left` +125 | `cam_back_right` −125 |
|---|---|---|---|---|
| `+yaw` = **right** (CARLA) | left ✓ | right ✓ | rear-**right** ✗ | rear-**left** ✗ |
| `+yaw` = **left** (ROS) | **right** ✗ | **left** ✗ | rear-left ✓ | rear-right ✓ |

Exactly two of the four are backwards either way — the front pair and the back pair
disagree. `camera_rig.py` resolves **CARLA's** reading, because the file's whole shape is
CARLA's and the front pair agrees with it, and then **prints where each camera actually
points** so the disagreement is visible on every run rather than baked in:

```
cam_back_left      512x288  fov  70  H -125.0  aims 125 deg to the right, i.e. rear-right
cam_back_right     512x288  fov  70  H +125.0  aims 125 deg to the left, i.e. rear-left
```

Fixing it is a one-line edit to `cams.txt` — which pair is wrong is not ours to decide.

### The cameras are mounted, not borrowed

MetaDrive's own six-view example
(`metadrive/tests/scripts/multiview_generation_with_image_on_cuda.py`) re-aims a *single*
camera per view through `perceive(..., new_parent_node, position, hpr)`, which steps the
task manager twice per call — six serialised render passes. Parenting six cameras to the
ego instead lets one pass fill every buffer. Measured on `junction-1` at 320×180:

| | read | `env.step` | total | rate |
|---|---|---|---|---|
| no cameras | — | 9.8 ms | 9.8 ms | 102 Hz |
| **6 mounted** | **2.2 ms** | 18.2 ms | **20.4 ms** | **49 Hz** |
| 1 borrowed 6× | 67.1 ms | 10.2 ms | 77.3 ms | 13 Hz |

That example has a second flaw worth not copying: it shares one `ImageObservation` across
its six views, so all six dict entries are the same array object and its 3-deep "stack" is
the last three *views* rather than the last three *timesteps*. `camera_rig` does not go
through `ImageObservation` at all.

For the real 7-camera spec at its own resolutions:

| | read | `env.step` | total | rate | bytes/step |
|---|---|---|---|---|---|
| 6 × 512×288 | 8.5 ms | 29.7 ms | 38.2 ms | 26.2 Hz | 2.65 MB |
| all 7 (+ 1280×720) | 13.0 ms | 40.8 ms | 53.9 ms | 18.6 Hz | 5.42 MB |

`cam_front_wide` alone is 2.77 MB of that 5.42 and about 16 ms of the 54.

**These were 67.9 ms and 93.4 ms until `make_env` stopped building a camera nobody reads.**
In offscreen mode it merged a 320×240 `rgb_camera` into `sensors` unconditionally, because
`image_source` defaults to that name and a caller registering only a depth camera would
otherwise die at construction. A rig *does* name an `image_source` — one of its own cameras —
so the fallback was an extra buffer rendered every step and read by nothing. Removing it is
**1.7× on the whole loop**, which is far more than a 320×240 buffer's own pixels are worth;
the mechanism was not chased further than the measurement, repeated three times at 51.1,
57.0 and 53.9 ms.

### A rig run is the rig alone

`--camera-rig` drops `rgb_camera`, `depth_camera`, `semantic_camera` and `point_cloud`.
That is what the rig is *for* — seven RGB cameras, no point cloud — and it is also the
cheaper shape, since every registered sensor is a buffer rendered every step whether
anything reads it or not.

**It is not forced by the buffer ceiling.** `env.reset` does fail *intermittently* past a
certain load, inside `graphicsEngine.renderFrame()` with
`AssertionError: _formats_by_animation.empty() at geomMunger.cxx:350` or
`MutexPosixImpl::~MutexPosixImpl(): Assertion 'result == 0' failed`, and the process then
aborts or segfaults. Measured over 5 runs at each size on `junction-1`, counting the
buffers the engine really holds:

| RGB cameras | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|
| runs surviving | 5/5 | 5/5 | **5/5** | 3/5 | 1/5 | 1/5 |

So a 7-camera rig has room for two more. **Mixing camera types costs more than the count
suggests**, which is the part not to read off that table: adding *one* non-RGB camera to
the rig is free — a `DepthCamera`, a `SemanticCamera` or a `PointCloudLidar` each give 5/5
at 8 buffers — but *two* of them give **1/5 at 9**, where nine RGB cameras give 5/5.

This ceiling was **7** until `make_env` stopped injecting the dead `rgb_camera` described
above. That buffer sat inside every earlier count, so the old figures were describing a rig
one camera larger than the caller had asked for.

Four readings that each looked like the cause and are not:

- **not the GPU** — 1/5 on the RTX 4050 through `__NV_PRIME_RENDER_OFFLOAD`, 2/5 on the
  Intel iGPU, same eleven buffers
- **not `multi_thread_render`** (default `True`, `threading-model Cull`) — `False` gave 0/5
- **not panda3d threading generally** — `loadPrcFileData("", "threading-model")` before
  MetaDrive is imported gave 2/5 against 3/5 with it left alone
- **not `stm-max-views`**, which panda3d complains about past ~6 cameras. Raising it
  changed nothing byte for byte, and **every camera renders pixel-identically alone and in
  the full rig** — 0.00% of pixels differing by more than 10 on all four late cameras. That
  message is inert here because `use_mesh_terrain` is off, so `ShaderTerrainMesh` is not
  what draws this ground.

The intermittency is why `--camera-rig` **refuses** a rig over the limit rather than
warning: a rig one camera over the line looks like it works, then fails on a run somebody
is relying on.

**Nothing is unavailable, only split across two runs.** `track.csv`, `observation.npy` and
the GPS are written either way, and `--policy idm` is deterministic — the same seed gives
the same drive — so a plain run and a rig run describe the same steps and their rows line
up. The report names any file left behind by the earlier run, with its age, so an old point
cloud is not paired with a fresh track by accident.

### What it writes

| flag | file | shape |
|---|---|---|
| `--camera-rig` | `sensor-survey/rig-<camera>.png` | one frame each, at `--sample-at` |
| `--rig-record` | `sensor-survey/rig/<camera>.npy` | `(steps, H, W, 3)` uint8, **every step** |

`--rig-record` is the model input rather than a picture of it, and it is large — 5.42 MB a
step, so a 291-step `junction-1` drive is 1.6 GB. It is off by default and the projected
size is printed before anything runs. Row *n* of every `rig/<camera>.npy` is row *n* of
`track.csv` and row *n* of `observation.npy`: same step, same drive.
