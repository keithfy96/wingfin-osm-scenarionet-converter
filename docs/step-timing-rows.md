# Step timing — what every row and column means

A reference for `tools/step_timing.py` / `scripts/step-timing.sh`. `README.md` says how to
*run* the sweep and shows a worked table; `CLAUDE.md` records the findings that are easy to
get wrong. This says what a single row, column or CSV field actually holds.

The same row table, shorter, is one command away:

```bash
./scripts/step-timing.sh -- --list-rows              # what each row measures
./scripts/step-timing.sh mosque                      # rows 1-6, every rate the workspace holds
./scripts/step-timing.sh mosque -- --rows 5          # one row on its own
./scripts/step-timing.sh mosque -- --rows 2,6        # what the camera costs
./scripts/step-timing.sh mosque -- --camera-rig rigs/cams.txt
```

> This describes MetaDrive's behaviour and our measurements of it. It is not a converter
> policy — see `docs/policies/README.md` for what belongs there.

---

## 1. The rows

A **row** is one configuration: a render mode, an ego policy, a set of sensors, and
optionally a pinned physics rate. Each row is driven once per dataset, so a workspace
holding `scenarionet-10hz` and `scenarionet-100hz` gives two measurements per row.
**The default is every row but 7**, which needs a display and so cannot run unattended.
`--rows 5` picks one, `--rows 2,6` picks two, `--rows 7` adds the display row.

**`sensors` is what the drive carries, not what the timing loop reads.** Every offscreen row
draws an RGB camera every step and MetaDrive reads it inside `env.step`, so the camera is on
the row whether or not this tool touches it — and it is **about three quarters of what a step
costs**, so a column that left it out was hiding the answer. The loop reads only the
numeric sensors; the camera must not be read there as well, for the reason under
[Where the camera's cost is](#where-the-cameras-cost-is).

**Unflagged, that camera is one this tool invented** — a single 320×180 buffer, a size chosen
here rather than by a vehicle. `--camera-rig` mounts the real ones; see
[The rig](#the-rig--camera-rig). Every figure in a sweep without it describes a car with one
small forward camera.

| # | `--render` | policy | sensors | physics | isolates | needs |
|---|---|---|---|---|---|---|
| **1** | offscreen | replay | camera,imu,gps | the dataset's | the floor: the same step with nothing deciding | a GL context |
| **2** | offscreen | idm | camera,imu,gps | the dataset's | a training-shaped step, with a controller driving | a GL context |
| **3** | offscreen | remote | camera,imu,gps <sup>†</sup> | the dataset's | your model in the same seat | a GL context, `--policy-url` |
| **4** | offscreen | idm | camera | the dataset's | vision only: a camera and MetaDrive's own state, nothing else read | a GL context |
| **5** | offscreen | idm | camera,imu,gps | **100 Hz** | physics pinned: CARLA-shaped at a 10 Hz dataset | a GL context |
| **6** | none | idm | imu,gps | the dataset's | no graphics at all: what the camera and the render path cost | nothing |
| 7 | 3D | replay | — | the dataset's | what `drive.sh` gives you | a display |

<sup>†</sup> row 3's sensor list is what a hosted model is sent, and `--policy-sensors`
overrides it for models that need something else — see [Row 3](#row-3--offscreen-remote-cameraimugps).

Rows **1–6 are the default**; 7 needs `--rows 7` because it opens a window. Row 3 is in the
default and **skips itself** with `needs --policy-url` when no model is listening, which is a
truer thing for the table to say than the row not being there at all. A row that cannot run on
this machine is **skipped, not fatal** — it still appears in the table and in the CSV, with
`status=skipped` and the reason in `skip_reason`, so a file is never silently short.

With a rig the `sensors` cell reads `camera x7,imu,gps`; the count is on the word because a
rig and the single invented camera are not the same measurement and must not print the same
thing.

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

**How far it may stray is raised for the whole sweep**, and this row is why. `ScenarioEnv`
ends an episode when the car is `max_lateral_dist` from the *recorded* route — MetaDrive's
default is 4 m (`scenario_env.py:84`) and it exists to judge **driving**. This tool measures
what a step costs, and a car 6 m off its line costs the same per step as one on it. At 4 m
the IDM rows on `mosque` ended `out_of_road` at step 44 with **24 steps measured**, against
replay's 380 — and four of the six default rows are IDM, so most of the table would have
been a median over two dozen samples. The sweep passes **20 m** instead, on every row
including replay so that no row is measured under different termination rules from the one
it is compared with. It is in the CSV as `max_lateral_m` rather than applied silently, and
`ended_by` still says `out_of_road` if a row reaches even that. `--max-lateral-m` changes
it; `drive.py` keeps MetaDrive's 4 m, because that tool *is* asking whether a drive is
drivable.

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

**`--policy-sensors` overrides what this row sends, and only this row.** What a hosted model
is handed is the model's business rather than the row's: `examples/openpilot_server.py`
fronts a *controller*, which wants the route in metres and cannot be driven without it, so
that run is `--policy-sensors imu,route`. The row's own definition — and every CSV taken
under it — keeps meaning `imu,gps`, and the `sensors` column reports whatever really went, so
two tables can still be told apart. A row driven by anything but `remote` ignores the flag.

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
  #  render     policy  sensors          tick  decide  physics  rpt   steps   sim s  wall s  x real  ms/step  policy    p95
```

| column | what it is |
|---|---|
| `#` | the row number above |
| `render` | `none`, `offscreen` or `3D` |
| `policy` | who drives: `replay`, `idm` or `remote` |
| `sensors` | what the drive **carries**, read off the live env: the camera on every offscreen row, plus whatever this loop reads itself |
| `tick` | how many times a second `env.step` is called — the world tick, `--step-hz` |
| `decide` | how many times a second the policy is asked and the sensors are read — `--decision-hz`. Equal to `tick` unless that flag was passed |
| `physics` | how many times a second the physics is integrated |
| `rpt` | `decision_repeat`: physics ticks inside one `env.step`, over which the action is held |
| `steps` | steps in the **measured** window — the drive minus `--warmup` |
| `sim s` | simulated seconds those steps cover: `steps × (1 / tick)` |
| `wall s` | wall-clock seconds they took |
| `x real` | `sim s / wall s`. **Above 1 is faster than the clock**; 0.6x means an hour of driving takes over an hour |
| `ms/step` | median `env.step`, milliseconds |
| `policy` | median time in the policy call alone, milliseconds — **per call, not per step**. Under a `decide` rate below `tick` the call happens once a stride, so multiply by `decide / tick` for its share of a step |
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
step's return value, and there is no seam inside `env.step` to put a stopwatch in.

What there *is* is a rate. `--decision-hz` gates the render pass and the read together, so on
a skipped step nothing is drawn and the observation hands back the stack it already had —
which is what a camera slower than the world tick means. Measured on `mosque` at 100 Hz with
`rigs/cams.txt`, that is most of what a step costs: **26.11 ms/step against 6.21**. So the
camera can be priced by rate as well as by row; see below.

Two consequences:

- `sensor_ms_median` only ever covers the **numeric** sensors. It is not hiding the camera;
  the camera is not the kind of thing it can hold.
- **No row reads a camera in the timing loop, and none should.** `SensorPack` reads with a
  parent node, which forces another `taskMgr.step()` (`base_camera.py:188`) — a second render
  of the same frame. That is a real cost when a hosted model needs a raw frame on the wire,
  and it is not a cost a training loop pays, so it must not be baked into the benchmark.

The way to price the camera is to run the same drive with no graphics at all and subtract:
that is what **row 6** is for.

Unflagged, one frame is drawn per `env.step` (`base_engine.py:458`, unconditional), so the
image rate *is* the step rate and a camera costs a full 10x more per simulated second at
100 Hz — against 2x for the physics. That is exactly what `--decision-hz` is for.

### Three rates, and only two of them are MetaDrive's

The shape the question is usually asked in is **world tick / decision + camera / physics**,
CARLA's three knobs. **MetaDrive has two.** `env.step` advances
`physics_world_step_size × decision_repeat` and returns; there is no separate sensor tick,
and the sensor config is `name=(cls, *args)` with **no slot for a rate anywhere**. So
`env.step` *is* the world tick, *is* the policy call, and *is* the camera draw.

| the rate | the flag | what sets it |
|---|---|---|
| world tick | `--step-hz` | the product of the two MetaDrive keys |
| physics | `--physics-hz` | `physics_world_step_size`; `decision_repeat` falls out |
| decision + camera | `--decision-hz` | **a stride counted in this tool's own loop** — nothing in MetaDrive. It gates the policy call, the sensor read, the camera read **and the camera draw** |

`--draw-every-step` is the one escape from that last item: it leaves the cameras redrawing on
every world tick while the decisions stay decimated, which is what this tool did before the
render pass was gated. Off by default, and kept so the gate's worth stays measurable.

Two constraints, both arithmetic and both refused rather than rounded:

- **physics ≥ world tick, as a whole multiple.** Ticks are substeps *inside* a step, so a
  100 Hz world with 50 Hz physics is half a tick per step and does not exist.
- **decision ≤ world tick, as a whole divisor.** Nothing moves between two steps, so a
  decision cannot be finer than one.

`--step-hz 100 --decision-hz 20` is `100/20/100`: the world and the physics at 100 Hz, the
policy asked, the sensors read and the cameras drawn every fifth step. It is what openpilot's bridge is written
for — `_DT_MDL = 0.05` is what its lag compensation and its curvature-rate limit are counted
against — and it beats converting a 20 Hz dataset for the purpose, because the control
interval is the same 0.05 s with ten times the physics underneath.

**On the replay rows the decision half is vacuous.** `ReplayEgoCarPolicy` runs *in* the
engine and MetaDrive calls it every `env.step`; nothing outside can decimate that. So on
rows 1 and 7 `--decision-hz` gates the reads and the camera draw alone, and the table says
`policy replay` so the reader can see which it is.

**The draw is gated too, and it is most of what a step costs.** `tools/frame_gate.py` rebinds
`engine.task_manager` to a proxy that lets `step()` through only on a decision step, which
gates the two render calls in `BaseEngine.step` (`base_engine.py:455,458`) and **nothing
else** — every other render in MetaDrive reaches the same object through the name `taskMgr`,
including `env.reset`'s own frames and `perceive(new_parent_node=...)`'s second pass. One
`task_manager.step()` is one `graphicsEngine.renderFrame()`, so a call that does not happen is
a frame that is not drawn. The read-back is held in step with it: `ImageObservation.observe`
returns the stack unrolled rather than pulling the same pixels again and rolling a duplicate
in. Only the image half — the 41-number state stays fresh every step, a vehicle state not
being a camera.

Measured on `mosque`, 100 Hz, row 1, `rigs/cams.txt`, 200 measured steps, **three runs of
each** (RTX 4050):

| configuration | ms/step | x real |
|---|---|---|
| `100/100/100` | 26.11 | 0.34x |
| `100/20/100` | **6.21** | **1.47x** |
| `100/10/100` | **3.66** | **2.54x** |
| `100/20/100 --draw-every-step` | 26.94 | 0.36x |
| `100/10/100 --draw-every-step` | 26.69 | 0.37x |

The last two rows are the control, and they are what says which half the money was in: with
the draw put back on the world tick a lower decide rate is worth **under a millisecond of 26**
— the read alone — and with it gated the same drive is **4.2x** faster at 20 Hz and **7.1x**
at 10 Hz.

**Why `frame_gate` and not `buffer.set_active(False)`, and why this reverses what this file
used to say.** That was built here first, as `--camera-skip-draw`, and measured at **1% of a
26 ms step** with `is_active()` confirmed `False` on all seven cameras — so it was removed and
the draw was written up as unsaveable. The measurement was right and the conclusion was wrong:
**an `RGBCamera` owns two GraphicsOutputs and `self.buffer` is the cheap one.**
`rgb_camera.py:38-52` builds a `FilterManager(self.buffer, self.cam)` and calls
`render_scene_into(...)` with `set_multisamples(16)`, which creates a *second* buffer
(`direct/filter/FilterManager.py:325-328`) hosted by the first. The scene — terrain, PBR, that
16x MSAA on top of the global 8x at `engine_core.py:96-103` — is drawn into that one, and
`self.buffer` only draws a fullscreen quad over the result. Deactivating `self.buffer`
switched off seven quads and left seven scene renders running. Anyone reaching for
`set_active` again needs that fact first.

**And it is checked by counting, not by timing.** Over a real 60-step drive at stride 5:
`gate.draws` is 12, panda3d's own `globalClock.getFrameCount()` moves by 12 over the same
window, all 48 held steps return an image bit-identical to the step before, all 12 drawn steps
return a different one, and the state half moves on all 60.

So `camera_hz` is what the cameras were **read** at and `camera_draw_hz` what they were
**drawn** at, counted by the gate rather than declared — equal unless `--draw-every-step` was
passed. `rig_ms_median` is the read-back and is per read, so it does not fall with the rate;
what falls is `ms/step`.

**One reading trap at a stride of 2 or more: `ms/step` is a median over every step, so it is
a held step** — 1.15 ms in the table above — while `p95` is a drawn one at 26.65. Neither is
the drive. `x real`, or `step_ms_mean` in the CSV, is: the two kinds of step are one
distribution and the median simply lands in the larger half.

### The rig — `--camera-rig`

**Unflagged, the camera every offscreen row draws is one this tool invented**: a single
320×180 `RGBCamera`, a size named in `step_timing.CAMERA_SIZE` rather than by any vehicle.
Since the camera is most of what a step costs, that makes an unflagged sweep a measurement
of a car nobody is building.

`--camera-rig <spec>` takes the same CARLA-shaped file `sensor-survey.sh` takes, read by
`tools/camera_rig.py`, and mounts the vehicle's own cameras instead. The spec this was
written against is **7 cameras — six 512×288 and one 1280×720 wide — 5.42 MB of image a
step**, against 0.17 MB for the invented one. Measured on `junction-1` at 10 Hz, 200 steps,
row 1 (replay, so nothing is deciding) each time:

| cameras | ms/step | x real |
|---|---|---|
| the 7-camera rig | **24.70** | **3.08x** |
| one 320×180, unflagged | 10.00 / 9.26 | 9.28x / 10.31x |
| row 6, no graphics at all | 3.20 | 27.20x |

So the rig is about **21.5 ms of a 24.7 ms step** and turns a drive that ran at 9x real time
into one that runs at 3x. That difference is the whole reason the flag exists.

Five things it does, none of which is a preference:

- **The cameras are mounted, not borrowed.** All seven are parented to the ego and filled by
  the *same* render pass. MetaDrive's own multi-view example re-aims one camera per view,
  which costs a `taskMgr.step()` each — measured in `camera_rig.py` at 20.4 ms/step mounted
  against 77.3 ms borrowed.
- **`rig_ms_median` is the read-back, and it is not a second render.** `CameraRig.read`
  calls `perceive` with **no parent node**, so it copies buffers the frame pass already
  filled. That is why it is allowed in the timing loop where a row's `read` list is not:
  the bar is against forcing a second render pass, not against touching a camera. Measured
  at **3.90 ms** for the seven. Only the `image_source` camera reaches the observation; the
  other six are read here or not at all, and a training loop reads all of them.
- **`image_source` is pointed at a rig camera.** `image_observation` builds the observation
  from `config["sensors"][image_source]` (`image_obs.py:68`) and that name defaults to
  `rgb_camera`, which a rig does not have — left alone, MetaDrive registers a dead 320×240
  buffer beside the rig and renders it every step.
- **More than `camera_rig.MAX_IMAGE_BUFFERS` (9) cameras is refused.** Past it `env.reset`
  fails *intermittently* inside panda3d, which looks like a working rig until it does not.
- **The spec's `tick_rate` is checked against the rate the cameras are really read at, and
  never resampled to it.** That rate is `--decision-hz`, so a rig declaring 0.05 s is exactly
  right under `--step-hz 100 --decision-hz 20` and wrong on an unflagged 10 Hz sweep. Where
  the two differ a line is printed per dataset — a note rather than a refusal, because this
  sweep drives every rate a workspace holds and there is no single one to judge against.
  `sensor-survey.sh` prints the same note, for the same reason - it has no `--decision-hz`
  to answer a refusal with. `camera_hz` records what they were read at, `camera_draw_hz`
  what they were drawn at, and under `--decision-hz` those are the same number: a spec
  declaring 0.1 s is exactly satisfied by `--step-hz 100 --decision-hz 10`.

Row 6 registers no cameras at all, so a rig changes nothing about it — which is what keeps
it the reference the rig is priced against.

### Which rows to compare, and for what

| question | read |
|---|---|
| does the simulator keep up with nothing deciding? | row 1's `x real` |
| what does the driver cost? | the `policy` column — **not** row 2 minus row 1 |
| **what does the camera cost?** | **row 2 against row 6** — the render path and nothing else |
| what does *my vehicle's* camera cost? | the same pair under `--camera-rig`. Without it the camera being priced is one 320×180 buffer this tool invented |
| what does reading the rig back out cost? | `rig_ms_median` in the CSV — a buffer copy, not a second render |
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
own file; nothing is appended to and nothing is overwritten. The stamp is when the run
started, in local time — the container mounts the host's `/etc/localtime` so its files sort
next to a host run's rather than eight hours away.

**A row lands on disk as it is measured, not at the end.** A twelve-row sweep with a real rig
is minutes of GPU time, and it used to hold every row in memory until the last one finished —
so an interrupt at row 11 left no file at all. Each row is now written and flushed as it
completes, `Ctrl-C` names the file and says how many rows are in it, and the closing
`csv <path>` line still means the run finished. Read that line for the path; it is the last
thing printed.

An interrupted run exits **130**, and it gets there through `os._exit` rather than a normal
return: panda3d segfaults tearing an engine down out from under a `KeyboardInterrupt`, so the
process used to die with 139 and the status stopped meaning anything. Nothing is lost by
skipping the shutdown — the CSV was flushed row by row and closed before the exit.

A path that already exists is refused **before** anything is measured rather than after, which
only a hand-named `--csv` can reach — the stamp is per-second otherwise.

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
| `gl_renderer` | the driver string the live window reported. Empty under `--render none`. **Check it in a container**: it must name the card, and `llvmpipe` or `Mesa` means the run was on the CPU |
| `os`, `python`, `numpy`, `metadrive` | versions the drive ran under |
| `timestamp` | when the sweep started. The same stamp is in the file name |

### The dataset and the rate

| field | holds |
|---|---|
| `dataset` | the dataset directory's name, e.g. `scenarionet-100hz` |
| `rate_set` | which `--rate-sets` configuration this row belongs to, by name, or empty when the rates came from the flags. **The column to pivot on** when one file holds several configurations |
| `scenario_id` | which scenario was driven |
| `step_hz` | world ticks a second — the `tick` column |
| `sim_dt_s` | seconds one `env.step` advances: `1 / step_hz` |
| `decision_hz` | how often the policy is asked and the sensors read — the `decide` column. `step_hz / steps_per_decision`. Equal to `step_hz` unless `--decision-hz` was passed, which is what every CSV written before this column existed had |
| `steps_per_decision` | the stride it was reached by: `1` normally, `5` for 20 Hz decisions on a 100 Hz world |
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
| `sensors` | what the drive carried, off the live env — `camera`, or `camera x7` under a rig, when cameras were really registered and `image_observation` was on, plus this loop's own reads |
| `camera_read_mode` | `observation` (MetaDrive's own read inside `env.step`, no extra render) or `none` (no camera). No row reads one in the timing loop, which would force a second render |
| `camera_rig` | the `--camera-rig` spec's file name, or empty — **empty means the camera priced was one 320×180 buffer this tool invented, not a vehicle's** |
| `camera_count` | how many cameras the live env really held, counted by class rather than by name. `1` unflagged, `0` under `--render none` |
| `camera_size` | the image the observation actually carried, from the frame's own shape. Under a rig this is the `image_source` camera, which is the spec's first |
| `camera_mb_per_step` | uint8 megabytes the whole rig produces per step — 5.42 for the seven-camera spec. Empty without a rig |
| `camera_hz` | the rate the cameras were **read** at — `decision_hz`, the read being part of the decision |
| `camera_draw_hz` | the rate they were **drawn** at, **counted by the gate over the measured window rather than declared**. Equal to `camera_hz` unless `--draw-every-step` was passed, which puts the draw back on `step_hz`. Two columns because they are two rates and a run can be made to differ; a CSV written before `frame_gate` existed has `camera_draw_hz == step_hz`, which was true when it was written |
| `stack_size` | frames in the image observation, 3, likewise from the frame |
| `norm_pixel` | `True`: float32 in [0, 1] rather than uint8 |
| `observation_kind` | `image+state41` offscreen — a dict of image plus the 41-number state, **with no lidar block** — or `lidarstate161` under `--render none` |
| `force_fps` | the engine's own `ForceFPS` state. `UnlimitedFPS` means nothing was throttling this row |
| `max_lateral_m` | how far off the recorded route the car was allowed before the episode ended. 20 m, not MetaDrive's 4 — see [Row 2](#row-2--offscreen-idm-cameraimugps) |

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
| `policy_ms_median` | the policy call alone. **The driver's cost**, per call |
| `sensor_ms_median` | the numeric sensor read alone, per read — **not** the camera, which is inside `step_ms_*`. About 0.13 ms on this map |
| `rig_ms_median` | reading the mounted rig cameras back out, per read, under `--camera-rig`. A buffer copy and not a second render — 3.90 ms for seven. Effectively zero without a rig |

**Those three are per call; `step_ms_*` is per step.** They are collected only on the steps
they happen on, and that is a correction rather than a preference: charging them to skipped
steps as well makes the median a skipped step the moment the stride is 2 or more. Measured at
`--decision-hz 10` on a 100 Hz world, `policy` printed **0.00 ms** against 0.20 at full rate,
which is not the model getting faster. How often they happen is the `decide` column; what one
costs is these.

---

## 4. Sweeping several configurations — `--rate-sets`

Comparing `10/10/50` against `100/20/100` means three flags per run and as many CSVs to
reconcile afterwards. `--rate-sets` takes a file of whole configurations instead, drives them
one after another **in one process**, and writes them to **one CSV** with a `rate_set` column.

```bash
./scripts/step-timing.sh mosque -- --rate-sets scripts/rate-sets.csv
./scripts/step-timing-docker.sh mosque -- --rate-sets scripts/rate-sets.csv --camera-rig rigs/cams.txt
```

The file is `name,step_hz,decision_hz,physics_hz`, one a row, `#` comments allowed; a blank
`decision_hz` or `physics_hz` means "whatever `step_hz` derives", so a bare `10` is `10/10/50`.
`scripts/rate-sets.csv` is the one in the repo, and the path is the same inside the container
and out — the repo is mounted at `/work`, the same reason `rigs/cams.txt` lives in the repo.

Four things it does that are decisions rather than conveniences:

- **One process, so the sets are comparable.** `prime` is paid once — the first env of a
  process is dearer than the ones after it — and every machine column is identical by
  construction rather than by two runs happening to agree.
- **A set drives only the dataset written at its own `step_hz`.** This is the one place
  `--rate-sets` behaves differently from the flags. A set is a whole configuration, so running
  it against a tape written at another rate measures something nobody asked for, and every
  replay row of it would skip anyway. Without a set the sweep still drives every rate the
  workspace holds, each at its own — that is the comparison it exists to make. A set whose
  dataset does not exist simply contributes no rows; `convert --step-hz <rate>` is what makes
  one.
- **A set's `physics_hz` outranks row 5's own 100 Hz pin**, because a set describes the whole
  configuration and a row overriding half of it would be measuring something the table does not
  name. Rows 2 and 5 then coincide, which the `physics` column shows and the footer says once.
- **It cannot be combined with `--step-hz`, `--decision-hz` or `--physics-hz`.** The file is the
  source of the rates; two sources is how a CSV comes to describe a run that did not happen.

Everything else still applies per set: `--rows` selects which rows each one drives, `--camera-rig`
mounts the vehicle's cameras on all of them, and an arithmetically impossible pair inside a set
shows as a skipped row saying why rather than refusing the file.

## 5. Reading two machines' files together

The machine columns repeat on every row precisely so this works:

```bash
cat step-timing-laptop-*.csv > both.csv
tail -n +2 -q step-timing-rig-*.csv >> both.csv
```

Then pivot on `label` × `rate_set` × `row`. Nothing needs lining up by hand — and `rate_set`
is the column to pivot on when one file holds several configurations.

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
- **`gl_renderer` names a card on both.** A container whose EGL setup is incomplete falls back
  to `llvmpipe` — software rendering, on the CPU — and does not fail while doing it, so the row
  looks ordinary and is roughly four times too slow for reasons that have nothing to do with the
  hardware being compared.

`scripts/step-timing-docker.sh` is what makes the two files comparable in the first place: it
runs this sweep inside an image that pins the interpreter, the locked package set and MetaDrive's
own commit, so the machine columns are the only thing that differs. `python`, `numpy` and
`metadrive` in the CSV are what catch it when they are not — except that `metadrive` holds
`EDITION`, which cannot tell two commits of the same version apart. The lock file is what does.
