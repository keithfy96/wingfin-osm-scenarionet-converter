# Step timing — what every row and column means

A reference for `tools/step_timing.py` / `scripts/step-timing.sh`. `README.md` says how to
*run* the sweep and shows a worked table; `CLAUDE.md` records the findings that are easy to
get wrong. This says what a single row, column or CSV field actually holds.

The same row table, shorter, is one command away:

```bash
./scripts/step-timing.sh -- --list-rows

 ./step-timing.sh mosque -- --rows 5
 ./step-timing.sh mosque -- --rows 2,6

```

> This describes MetaDrive's behaviour and our measurements of it. It is not a converter
> policy — see `docs/policies/README.md` for what belongs there.

---

## 1. The rows

A **row** is one configuration: a render mode, an ego policy, a set of sensors, and
optionally a pinned physics rate. Each row is driven once per dataset, so a workspace
holding `scenarionet-10hz` and `scenarionet-100hz` gives two measurements per row.
`--rows 1,2` picks them; the default is `1,2`.

**`sensors` is what the drive carries, not what the timing loop reads.** Every offscreen row
draws a 320×180 RGB camera every step and MetaDrive reads it inside `env.step`, so the
camera is on the row whether or not this tool touches it — and it is **about three quarters
of what a step costs**, so a column that left it out was hiding the answer. The loop reads only the
numeric sensors; the camera must not be read there as well, for the reason under
[Where the camera's cost is](#where-the-cameras-cost-is).

| # | `--render` | policy | sensors | physics | isolates | needs |
|---|---|---|---|---|---|---|
| **1** | offscreen | replay | camera,imu,gps | the dataset's | the floor: the same step with nothing deciding | a GL context |
| **2** | offscreen | idm | camera,imu,gps | the dataset's | a training-shaped step, with a controller driving | a GL context |
| 3 | offscreen | remote | camera,imu,gps | the dataset's | your model in the same seat | a GL context, `--policy-url` |
| 4 | offscreen | idm | camera | the dataset's | vision only: a camera and MetaDrive's own state, nothing else read | a GL context |
| 5 | offscreen | idm | camera,imu,gps | **100 Hz** | physics pinned: CARLA-shaped at a 10 Hz dataset | a GL context |
| 6 | none | idm | imu,gps | the dataset's | no graphics at all: what the camera and the render path cost | nothing |
| 7 | 3D | replay | — | the dataset's | what `drive.sh` gives you | a display |

Rows 1 and 2 are the default pair; the rest need `--rows`, and row 3 needs a model listening
as well. A row that cannot run on this machine is **skipped, not fatal** — it still appears
in the table and in the CSV, with `status=skipped` and the reason in `skip_reason`, so a
file is never silently short.

The `sensors` cell of a row that actually ran is **read off the live env** — the camera is
named when `image_observation` is on and one is really registered, and `camera_size` comes
from the frame's own shape. If anything ever stops building one, the table says so rather
than repeating what the row was meant to do.

### Row 1 — offscreen, replay, camera+imu+gps

`ReplayEgoCarPolicy` sets the car's position directly from the recorded track each step. It
**decides nothing**, so this is the simulator's cost with no driver in it: physics, the
managers, the terrain, one camera render, the camera readback, and the imu/gps read.

It is the reference for the one question a policy cannot answer — *does the simulator keep
up with the clock at all* — and it is what row 2 is read beside.

### Row 2 — offscreen, idm, camera+imu+gps

The same, with MetaDrive's own `TrajectoryIDMPolicy` in the driving seat. It computes:
projects the car onto its route, runs a heading PID and a lateral PID for steering, runs the
IDM car-following model for acceleration, and checks the light in front. That is the slot
your model will occupy, which is what makes this "training-shaped".

**It does not drive the same route as row 1.** A replayed car follows the tape exactly; an
IDM car follows its own line and may end early as `out_of_road`. So the two rows have
different `steps` and `sim s`, and that is expected rather than a fault.

### Row 3 — offscreen, remote, camera+imu+gps

Your model, over HTTP, through `policy_client.RemotePolicy` — the same class
`drive.py --agent-policy remote` uses. **It is not in the default pair and needs
`--policy-url`**; without one it is skipped, which is the usual reason it is absent from a
table rather than anything having gone wrong.

One thing differs from row 2: who is holding the wheel. The camera reaches the model
**inside the observation**, which under `image_observation` *is* the image stack, and
`RemotePolicy` encodes and sends the whole observation — so the image is sent once, not
twice. The imu/gps go to the model too rather than being read by this loop, so their cost
sits inside `policy_ms` along with the wire and the model itself. That is a real cost of
hosting a model; it is not the model being slow.

### Row 4 — offscreen, idm, camera only

Row 2 with nothing pulled out for a model: **the shape a camera-only model runs in.**

It is not a blind car with a camera. The observation still carries MetaDrive's own 41-number
state — speed, heading error, steering, last throttle, yaw rate, lateral offset in lane and
the next ten route points — because that is built whether anything asks for it or not. What
is missing is only this loop's own imu/gps read.

**It is not a way to price imu/gps.** Those measure about **0.13 ms**, well under the
run-to-run spread — measured back to back, row 4 has come out *dearer* than row 2 on noise
alone. `sensor_ms_median` answers that question directly and honestly.

### Row 5 — offscreen, idm, camera+imu+gps, physics pinned at 100 Hz

Row 2 with `physics_world_step_size` pinned so the integrator runs at 100 Hz whatever the
decision rate. **At a 10 Hz dataset that is CARLA's own default shape** — 100 Hz physics,
10 Hz decisions — and the only row whose number means anything beside a CARLA figure. At a
100 Hz dataset it collapses to the same pair as row 2, which is the "physics held still,
decision rate varied" half of the comparison.

`--physics-hz` does the same thing to every row.

### Row 6 — none, idm, imu+gps: no graphics at all

`--render none`. MetaDrive deletes every `BaseCamera` from the sensor list at construction
(`base_env.py:342-347`) and builds no terrain, so this is physics, managers, the 161-float
observation, the imu/gps read and the policy — and nothing that touches the GPU.

**It reads the same imu/gps as row 2 so that the subtraction is clean**: row 2 against row 6
is the camera and the render path and nothing else. Measured on the laptop this was written
on (RTX 4050, `junction-1`, `--rows 2,6 --max-steps 300`):

| | ms/step | x real |
|---|---|---|
| 10 Hz, row 2 (camera) | 16.69 | 5.45x |
| 10 Hz, row 6 (none) | **4.06** | **19.82x** |
| 100 Hz, row 2 (camera) | 17.48 | 0.51x |
| 100 Hz, row 6 (none) | **3.57** | **2.30x** |

**12.6 ms of a 16.7 ms step is the camera — about three quarters of it** — and at 100 Hz it
is the whole difference between a drive that runs at 2.3x real time and one that runs at
0.51x. Re-measure on your own machine rather than quoting those; the shape is the point.

The one row that runs anywhere, including a container with no GL context at all.

### Row 7 — 3D, replay, no sensors read

A window, exactly what `./scripts/drive.sh` opens. Needs a display; skipped when neither
`$DISPLAY` nor `$WAYLAND_DISPLAY` is set.

**There is deliberately no unthrottled twin of this row.** `ForceFPS` is built only onscreen
and takes its interval from `physics_world_step_size`, so it looks like the thing to raise —
but it never fires here: `force_render_fps=1000` measured 16.59 ms a frame against 16.67
stock at 100 Hz, and 83.34 against 83.50 at 10 Hz, and loading `sync-video #f` into panda3d
before the window exists does not move it either. The ceiling is the compositor's 60 Hz. The
row records `force_fps` — the engine's own state — rather than claiming a number it has not
got.

---

## 2. The printed columns

```
  #  render     policy  sensors        decide  physics  rpt   steps   sim s  wall s  x real  ms/step  policy    p95
```

| column | what it is |
|---|---|
| `#` | the row number above |
| `render` | `none`, `offscreen` or `3D` |
| `policy` | who drives: `replay`, `idm` or `remote` |
| `sensors` | what the drive **carries**, read off the live env: the camera on every offscreen row, plus whatever this loop reads itself |
| `decide` | how many times a second `env.step` is called: the decision rate, and also the sensor rate |
| `physics` | how many times a second the physics is integrated |
| `rpt` | `decision_repeat`: physics ticks inside one `env.step`, over which the action is held |
| `steps` | steps in the **measured** window — the drive minus `--warmup` |
| `sim s` | simulated seconds those steps cover: `steps × (1 / decide)` |
| `wall s` | wall-clock seconds they took |
| `x real` | `sim s / wall s`. **Above 1 is faster than the clock**; 0.6x means an hour of driving takes over an hour |
| `ms/step` | median `env.step`, milliseconds |
| `policy` | median time in the policy call alone, milliseconds |
| `p95` | 95th percentile of `env.step` — the slow tail |

### Two ways to misread this table

**`ms/step` is not comparable across rates; `x real` is.** `env.step` holds one action
across `decision_repeat` physics ticks, and MetaDrive's 10 Hz default is `(0.02, 5)` — so a
10 Hz run **integrates at 50 Hz, not 10**, and a 100 Hz run at 100 Hz with one tick per
step. One step at 100 Hz is therefore *cheaper* than one at 10 Hz while there are ten times
as many of them. Comparing `ms/step` between two rates says the physics got cheaper, which
is the opposite of what happened.

| run | physics ticks / simulated second | decisions / simulated second |
|---|---|---|
| 10 Hz | **50** | 10 |
| 100 Hz | **100** | 100 |

**`policy` is the driver's cost — not row 2 minus row 1.** The subtraction was the intent
and it does not survive the machine: measured three times over, row 1 came out at
8.90 / 8.99 / 10.07 ms a step and row 2 at 9.35 / 10.35 / 8.99, so the difference read
+0.45, +1.36 and **−1.08** ms while `policy` held 0.37–0.43 ms throughout. About a
millisecond of run-to-run spread swamps it, and the two rows do not drive quite the same
route anyway. Read `policy`.

### Where the camera's cost is

**Inside `ms/step`.** The `sensors` column names the camera, but no column times it on its
own, and there is a reason for that rather than an omission.

MetaDrive does not hand you a camera you then read. When `image_observation` is on — which
`--render offscreen` sets, and which is the only way a camera exists without a window — it
builds the **observation** out of the camera: what `env.step` returns is
`{"image": the last 3 frames, "state": 41 numbers}`, and the code producing that grabs the
frame and rolls the stack (`image_obs.py:85`). So reading the camera is part of making the
step's return value. There is no seam to put a stopwatch in, and **no setting that draws it
without reading it** — turn the read off and the camera stops existing.

Two consequences:

- `sensor_ms_median` only ever covers the **numeric** sensors. It is not hiding the camera;
  the camera is not the kind of thing it can hold.
- **No row reads a camera in the timing loop, and none should.** `SensorPack` reads with a
  parent node, which forces another `taskMgr.step()` (`base_camera.py:188`) — a second render
  of the same frame. That is a real cost when a hosted model needs a raw frame on the wire,
  and it is not a cost a training loop pays, so it must not be baked into the benchmark.

The way to price the camera is to run the same drive with no graphics at all and subtract:
that is what **row 6** is for.

One frame is drawn per `env.step` whatever the rate (`base_engine.py:458`, unconditional),
so the image rate *is* the step rate and a camera costs a full 10x more per simulated second
at 100 Hz — against 2x for the physics.

### Which rows to compare, and for what

| question | read |
|---|---|
| does the simulator keep up with nothing deciding? | row 1's `x real` |
| what does the driver cost? | the `policy` column — **not** row 2 minus row 1 |
| **what does the camera cost?** | **row 2 against row 6** — the render path and nothing else |
| what do imu/gps cost? | the `sensor` column. Around 0.13 ms, so no row subtracts to find it |
| what does my model cost over the wire? | row 3's `policy`, which is the model plus the round trip |
| what would a camera-only model run in? | row 4 |
| how does this compare with CARLA? | row 5, and `physics_ticks_per_sim_second` rather than `ms/step` |

**A difference under about a millisecond is not readable here**, whatever two rows are
subtracted — that is what run-to-run spread measures on this machine, and it is why row 4
stopped claiming to price imu/gps and why `policy` is timed around the call instead.

---

## 3. The CSV

One row per measurement, written to
`<workspace>/reports/step-timing-<label>-<YYYY-MM-DD-HH:MM:SS>.csv`. Every run writes its
own file; nothing is appended to and nothing is overwritten.

### The machine — repeated on every row, so two files concatenate

| field | holds |
|---|---|
| `label` | `--label`, or `$STEP_TIMING_LABEL`, or the hostname. **What tells two machines apart** |
| `host` | hostname. In a container this is a random id, which is why `label` exists |
| `in_docker` | `yes`/`no`, from `/.dockerenv` or `/proc/1/cgroup` |
| `cpu_model` | `model name` from `/proc/cpuinfo` |
| `cpu_threads` | `os.cpu_count()` |
| `gpu_name` | `nvidia-smi --query-gpu=name`, empty when there is no NVIDIA card |
| `gl_max_texture` | the GL ceiling a throwaway context reports. Empty means no GL context, and every row but 6 was skipped |
| `gl_renderer` | the driver string the live window reported. Empty under `--render none` |
| `os`, `python`, `numpy`, `metadrive` | versions the drive ran under |
| `timestamp` | when the sweep started. The same stamp is in the file name |

### The dataset and the rate

| field | holds |
|---|---|
| `dataset` | the dataset directory's name, e.g. `scenarionet-100hz` |
| `scenario_id` | which scenario was driven |
| `step_hz` | decisions a second — the `decide` column |
| `sim_dt_s` | seconds one `env.step` advances: `1 / step_hz` |
| `physics_hz` | physics integrations a second |
| `physics_dt_s` | `physics_world_step_size` as the engine held it |
| `decision_repeat` | ticks per step — the `rpt` column |
| `physics_ticks_per_sim_second` | `decision_repeat / sim_dt_s`. **The number to match against another simulator**, since it is the integration work per second of simulated time rather than per step |

### The configuration

| field | holds |
|---|---|
| `row` | the row number |
| `render` | `none`, `offscreen`, `3D` |
| `policy` | `replay`, `idm`, `remote` |
| `sensors` | what the drive carried, off the live env — `camera` when one was really registered and `image_observation` was on, plus this loop's own reads |
| `camera_read_mode` | `observation` (MetaDrive's own read inside `env.step`, no extra render) or `none` (no camera). No row reads one in the timing loop, which would force a second render |
| `camera_size` | the image the observation actually carried, `320x180`, from the frame's own shape |
| `stack_size` | frames in the image observation, 3, likewise from the frame |
| `norm_pixel` | `True`: float32 in [0, 1] rather than uint8 |
| `observation_kind` | `image+state41` offscreen — a dict of image plus the 41-number state, **with no lidar block** — or `lidarstate161` under `--render none` |
| `force_fps` | the engine's own `ForceFPS` state. `UnlimitedFPS` means nothing was throttling this row |

### The outcome

| field | holds |
|---|---|
| `status` | `ok` or `skipped` |
| `skip_reason` | why, when skipped. Empty otherwise |
| `steps` | steps driven in total, warm-up included |
| `measured_steps` | steps in the distribution — `steps` minus `warmup_steps`. **This is what the table's `steps` column shows**, because it is what the timings describe |
| `warmup_steps` | steps driven before the clock started |
| `sim_seconds`, `wall_seconds` | over the measured window |
| `realtime_factor` | `sim_seconds / wall_seconds` |
| `reset_seconds` | `env.reset` on its own. Carries the terrain build, which is seconds offscreen and would otherwise land on step 1 |
| `arrive_dest` | did the drive reach the end of the route |
| `ended_by` | `arrive_dest`, `out_of_road`, `crash*`, `max_step`, or `ran out of budget` |

### The distribution — all in milliseconds

| field | holds |
|---|---|
| `step_ms_mean`, `step_ms_median`, `step_ms_p95`, `step_ms_p99`, `step_ms_max` | `env.step` alone |
| `policy_ms_median` | the policy call alone. **The driver's cost** |
| `sensor_ms_median` | the numeric sensor read alone — **not** the camera, which is inside `step_ms_*`. About 0.13 ms on this map |

---

## 4. Reading two machines' files together

The machine columns repeat on every row precisely so this works:

```bash
cat step-timing-laptop-*.csv > both.csv
tail -n +2 -q step-timing-rig-*.csv >> both.csv
```

Then pivot on `label` × `row` × `step_hz`. Nothing needs lining up by hand.

Four things to check before believing a comparison:

- **`label` is set on both.** A container's hostname is a random id, so two rig runs a week
  apart otherwise look like two different machines.
- **`status` is `ok`.** A `skipped` row is still a row; `skip_reason` says whether the
  machine could not do it or the flag was missing.
- **`physics_ticks_per_sim_second` matches** if the point is comparing simulators. Two runs
  at "10 Hz" are not the same work if one integrates at 50 Hz and the other at 100.
- **The boxes were quiet.** The same configuration measured 8 ms a step early in a session
  and 17 ms after twenty minutes of back-to-back sweeps, which is thermal rather than
  anything in the code. Ratios within one run compare; absolute figures are worth what the
  machine's state was worth.
