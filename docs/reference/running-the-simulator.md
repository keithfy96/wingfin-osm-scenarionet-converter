# Running the simulator — rates, cost and the container

The three rate dials and the two clocks, what one step costs and how it is measured, why 3D
needs its own runner, and the one-interpreter container.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

### Why 3D needs its own runner

2D is fine from any entry point. 3D through `scenarionet.sim` shows roads that stop
and an ego that sinks into the ground and floats — **none of it a defect in the
converted data**, all of it MetaDrive terrain defaults meeting a map shaped like a
road network rather than like a Waymo clip. Three separate causes, each measured:

- **`height_scale` (default 50) is the sinking and the flying.** `use_mesh_terrain`
  is false by default, so the car drives on a flat collision plane at z≈0 while the
  *visible* ground is a noise heightfield around it. `tools/drive.py` measures it:
  at 50 the ground within 25 m of the drive reaches **+10.3 m** and 11% of it stands
  above the road; at 1 it reaches **+0.2 m** and 0% does. Only the surroundings move
  — the road is flattened either way. **0 is not allowed**: panda3d builds a singular
  transform and dies with `Tried to invert singular LMatrix4`.
- **The road-surface texture is often larger than the GPU accepts, and there is no
  config key for it.** MetaDrive builds it at `map_region_size × 22` px square — but
  **×11 at 4096** (`constants.py:499`), so 22528 at 1024 and **45056 at 4096**, not
  90112. A GL context reports its own ceiling, and **it is asked rather than assumed**,
  because on this machine it doubles between the two GPUs and the whole resolution
  follows from it: measured **16384** on the Intel iGPU and **32768** on the RTX 4050.
  Past it the texture cannot be uploaded, and that is what "the roads stop" looks like.
  `drive.py._max_texture_dimension` asks a throwaway subprocess — the ceiling is only
  knowable once a GL context exists, and by the time `env.engine.win` does, MetaDrive
  has already built the terrain from `get_semantic_map_pixel_per_meter`. The 22 itself
  is hard-coded in that classmethod; `tools/drive.py` replaces it at runtime, riding
  the seam `base_env.py:335` already uses for `map_region_size`. Nothing in the
  MetaDrive checkout is edited.
- **This machine is hybrid graphics, and which card renders is not a flag.** It is
  settled by the GLX loader before python starts, so it can only be two environment
  variables in front of the command — `__NV_PRIME_RENDER_OFFLOAD=1
  __GLX_VENDOR_LIBRARY_NAME=nvidia`, which is what `_common.sh:select_gpu` /
  `exec_with_gpu` set for all three of `drive.sh`, `sensor-survey.sh` and
  `step-timing.sh`, and why the switch lives in the shell rather than in `drive.py`.
  **Both are read by the GLX loader, so neither does anything in the container**,
  which loads panda3d's EGL display first and picks the card from the image's ICD
  manifest — `select_gpu` says so rather than claiming PRIME offload, and they are
  still set because the image keeps `pandagl` as the aux display for the 3D row.
  Nothing needs installing;
  panda3d 1.10.16 in MetaDrive's own venv picks the RTX up as it stands. **`--cuda`
  in MetaDrive's install docs is not this**: it toggles `image_on_cuda`, which keeps
  camera images in GPU memory for an RL pipeline, needs `pip install -e .[cuda]` plus
  Torch and CuPy (none installed here), and does not choose a renderer.
- **`map_region_size` sizes the terrain square, and 2048 is the wrong blanket
  answer** — an earlier version of this file said to set it, which would demand a
  45056 px texture no GPU can hold. The square is `map_region_size` metres centred
  on the ego's start (`base_engine.py:386` hard-codes `center_p = [0, 0]`; the disk
  loader passes `centralize=True`, `scenario_data_manager.py:76`), and outside it
  there is no ground and no flattened road. So it must be *just* big enough:
  `tools/drive.py` measures each scenario and picks the smallest power of two that
  covers it, and `tools/check_dataset.py` reports the same number. `junction-1`'s
  `main-route` reaches 449 m from its start, so 1024 is enough; another start lane
  will not be.

Run `tools/drive.py --render offscreen` to check any of this without a display —
`--render none` builds no terrain at all (`Terrain.reset` guards the whole path on
`self.render or use_mesh_terrain`), so it checks the drive and not the view.

Two things about markings are MetaDrive's and not ours, so do not go looking for them in
`conversion.py`. **The 3D markings are a raster, not geometry**: `_construct_lane_line_segment`
builds only a collision ghost, and what the eye sees is `BaseMap.get_semantic_map` painting
every line with `cv2.polylines` at a thickness given in **pixels** — `white_line_thickness=2`,
`yellow_line_thickness=3`, passed as literals at `terrain.py:625` over that function's own
defaults of 1 and 1, with no config key anywhere between. So a line is
`thickness / pixels_per_meter` metres wide, **and its real width moves with the size of the
map**. And **the white hairline round every road edge is drawn by nothing**:
`terrain.frag.glsl` paints by value band (ground 0, lines 10, road 20) and `semantic_tex` is
created with no filter, so the linear blend from road to grass passes through 5–16 and the
shader calls it white. Keith looked at the hairline and chose to leave it.

**The width is asked for in metres, and that is the point of the flag.** A fixed pixel count is
wrong in opposite directions on the two extracts — MetaDrive's 2 px is **0.5 m** on `mosque`'s
4096 m square at 4 px/m and **0.0625 m** on `junction-1`'s 1024 m square at 32, one far wider
than a road marking and the other far thinner. `drive.py --line-width-m` (default **0.15**,
about a real marking) works out the pixels from the resolution in force, and
`drive.py._set_line_width` is the repo's **second** monkeypatch — it must *override* rather than
re-default, because `terrain.py:625` passes both thicknesses explicitly. Three things not to
re-derive:

- **1 px is the floor**, so a big map cannot go as thin as a small one: `mosque` bottoms out at
  **0.125 m** even on the RTX, and the tool prints which happened rather than rounding quietly.
- **White and yellow get the same number.** `conversion.py` writes only `ROAD_EDGE_BOUNDARY` and
  `ROAD_LINE_BROKEN_SINGLE_WHITE`, so the yellow value is unreachable on our data today; giving
  it a different one would be an unexplained difference on the first map that has a yellow line.
- **It is not a config field, and must not become one.** `configuration_checksum` feeds
  `generation_fingerprint` (`generation.py:2212`), so a rendering preference in
  `config/default.yaml` would invalidate the lane-model review — the same reason signal timing
  is a `convert`-time file. It lives on the command line, with `LINE_WIDTH_M` in `.env` for
  anyone who does not want to type it.

### The rate is `--step-hz`, and there are two clocks, not one (2026-08-19)

**10 Hz was never MetaDrive's rate, only its default.** `env.step` advances
`physics_world_step_size` x `decision_repeat` — 0.02 x 5 — and both keys are ordinary config.
`drive.py`'s `step_config(hz)` derives them: `repeat = max(1, ceil(dt / 0.02))`,
`physics = dt / repeat`, so the physics tick is never *coarser* than MetaDrive's own and
**10 Hz returns exactly (0.02, 5)** — which is what makes `--step-hz 10` and no flag the same
run. 100 Hz gives (0.01, 1). The pair is deliberately not exposed: the rate is their product,
and `decision_repeat` also decides how many `taskMgr.step()`s run per `env.step`, each of which
redraws every camera buffer.

**Two clocks.** `sim_step_seconds(env)` is how far one `env.step` advances the simulator;
`data_step_seconds(scenario)` is how far one recorded frame covers. They are equal only when the
dataset was converted at the rate it is being driven at, and **two places in `tools/` were
reading the wrong one** — right by coincidence rather than by construction:

- `signal_control` converted an **engine** step count to seconds using the **plan's** rate.
  Those lights are live precisely because the tape is not being used. It reads the engine now,
  and the docstring says why so the next reader does not put it back.
- `drive.py`'s `_longest_red` divided seconds by the **data** rate to produce a budget counted
  in **env** steps. It returns seconds; the caller converts, because the caller is the one that
  knows which clock it is counting in.

**The rate is a convert-time argument, never a config field** — `configuration_checksum` feeds
`generation_fingerprint`, so a field on `ConverterConfig` would invalidate the Stage 3 review.
Same reason as `--speed-kph` and the render flags. `STEP_HZ` in `.env` reaches `drive.sh` and
`sensor-survey.sh` and is **deliberately not wired into `run-stages-4-6.sh`**: a dataset's rate
is baked into bytes the review never re-checks, and picking it up from a machine-local file is
how two workspaces end up at different rates with nobody having decided.

**A rate gets its own directory, because the filename cannot carry it (2026-08-20).** The
scenario id is `<workspace>-<fingerprint16>-<route name>`, so the 10 Hz and the 100 Hz build of
one route want the *same* `sd_*.pkl` name — and the stale-pickle sweep under the write loop
deletes whatever the current run did not write, so the second convert took the first one out.
`conversion.dataset_dir_name` names it from the **interval** rather than from the `--step-hz`
argument, so no flag and `--step-hz 10` both land in `scenarionet-10hz`. `routes.json` carries
no timing at all — `{name, start_lane, end_lane}` and the identity block — so the *same* routes
file feeds both builds, and picking a rate is a convert-time decision, never a route.

Three consequences worth not re-deriving:

- **The sweep is now per rate**, which is what it should be: a route dropped from `routes.json`
  is cleaned out of that rate's directory and the other rate's dataset is untouched.
- **`reports/scenario-conversion-<rate>hz.json` follows the same rule**, because the report
  carries each written file's sha256 and size — one report over two live datasets would
  describe the other one's bytes. `check_dataset.py`'s `--png` default is
  `stage-6-map-<rate>hz.png` for the same reason. `manifest["stage_6"]` stays a single object
  and gains `step_hz`, `dataset_dir` and `report`, naming which build it describes.
- **`_common.sh:resolve_dataset` is what `drive.sh` and `sensor-survey.sh` select with**, from
  a `--step-hz` in their own passthrough args first and `STEP_HZ` second — one way to say the
  rate, so `-- --step-hz 100` picks the 100 Hz dataset rather than pointing a 100 Hz simulator
  at the 10 Hz one and being refused. A bare `<ws>/scenarionet` from before the rename still
  drives, but **only while the workspace has no rate-named dataset at all**; once it has one,
  the bare directory is a stale build and not an answer to which rate was asked for.

**And no new metadata key.** `metadata.ts` spacing *is* the rate, exactly — an integer step
index times the interval — so `metadata.dt` would move the bytes of every scenario ever
converted without one, for information already in the file. Everything reads it back off `ts`.

**A dataset can only be *replayed* at the rate it was written at, and `drive.py` refuses the
mismatch rather than warning.** Three things consume the recording one frame per `env.step` with
no interpolation, so at a different rate they run the tape at the ratio of the two clocks:
`ReplayEgoCarPolicy` (`replay_policy.py:41-65`), a baked light tape
(`scenario_light_manager.py:68-75`), and any non-ego track. None *fails* — each simply drives
something other than what the dataset says, which is why it is a refusal.

Three more couplings are **MetaDrive's own and are warned about, never patched** — a reference
checkout is not edited here. `PIDController` (`PID_controller.py:1-22`) has **no dt at all**, so
both its gains scale with the rate and `--agent-policy idm` will not drive identically;
`LANE_CHANGE_FREQ = 50` and `IDM_ACT_BATCH_SIZE = 5` are counted in steps; `STEERING_INCREMENT`
is applied per `env.step`, so the keyboard feels 10x slower at 100 Hz; and `ForceFPS` takes its
interval from `physics_world_step_size`, so 3D asks the display for 100 fps.

**Measured on `junction-1` (403.7 m `test` route), because none of it was guessable:**

| | 10 Hz | 100 Hz |
|---|---|---|
| `env.step`, headless | 1.094 ms | **0.848 ms** |
| `env.step`, `--render offscreen` | 10.9 ms | 20.2 ms |
| `env.step`, `--render 3D` (RTX) | 83.4 ms | 16.6 ms |
| 3D speed against wall-clock | 1.20x | **0.60x** |
| scenario pickle, map + route | 791,940 B | 1,121,208 B (+41.6%) |
| the same, + a 3-lane light plan | +6,666 B | +56,559 B (+5.0%) |
| `convert` wall-clock | 1.53 s | 1.54 s |

**One `env.step` is *cheaper* at 100 Hz, not dearer** — `decision_repeat` is 1 rather than 5, so
it is one physics substep instead of five. A whole drive still costs about 7.8x, because there
are ten times as many. **3D tops out at 60 fps either way** (5 frames per 83.4 ms; 1 per
16.6 ms — the display's vsync), so asking `ForceFPS` for 100 is what makes a 100 Hz drive run at
0.60x real time rather than 1.20x. It is usable, and it is slower than the clock on the wall.
The light tape is a Python list of colour *strings* per lane per step, so it grows linearly:
about 5.1 B per lane per step, which a 20-lane plan at 100 Hz turns into ~370 KB a scenario.

### What a step costs is measured now, not quoted (2026-08-20)

`tools/step_timing.py` / `scripts/step-timing.sh` drives every rate a workspace holds and
reports wall-clock against simulated time. Re-measure with it rather than quoting a number
from this file. **What every row, column and CSV field means is
`docs/step-timing-rows.md`**, and `--list-rows` prints the short version — neither this
section nor the README is the place to look that up, and a row description that lives in
two places drifts.

**And it does not reproduce the four hand-measured `env.step` figures in the table above.**
Same route, same `--render none`, same replay policy: **2.357 ms at 10 Hz against the 1.094
recorded, and 2.181 at 100 Hz against 0.848** — about 2.2x both, with the direction preserved
(100 Hz still cheaper than 10 Hz, because it is one physics tick against five). Not heat: the
cores were at 773 MHz mean and 63 C when this was taken. The likeliest difference is what the
older figure timed - `env.step` includes `_get_step_return`, which builds the observation and
evaluates reward and termination, and a measurement around `_step_simulator` alone would come
out roughly here. Unresolved, and recorded so the older numbers are not trusted as a baseline.

**The default is rows 1–6** — everything but the 3D row, which opens a window and so cannot be
part of an unattended sweep. Rows 1 and 2 differ only in who drives: `replay`, which writes the
car's position from the file and decides nothing, and `idm`, which computes. Row 3 puts a hosted
model in the same seat and **skips itself with `needs --policy-url`** when nothing is listening,
which is a truer thing for the table to say than the row not appearing. One row on its own is
`./step-timing.sh <ws> -- --rows 5`. Every row but 6 is `--render offscreen`, because a camera
cannot exist without one.

**The sweep raises `max_lateral_dist` to 20 m (`SWEEP_MAX_LATERAL_M`), and that is not a
preference.** MetaDrive's 4 m (`scenario_env.py:84`) ends an episode when the car strays from
the *recorded* route, and it is there to judge driving; this tool measures what a step costs,
where a car 6 m off its line costs exactly what one on it does. At 4 m the IDM rows on `mosque`
ended `out_of_road` at step 44 with **24 steps measured** against replay's 380 — and **four of
the six default rows are IDM**, so most of the table would have been a median over two dozen
samples. Applied uniformly, replay included, so no row is measured under different termination
rules from the one it is compared with, and recorded as `max_lateral_m` rather than applied
silently. `drive.py` keeps the 4 m: that tool *is* asking whether a drive is drivable.

**The camera it prices is one the tool invented, until `--camera-rig` names a real one.**
Unflagged, every offscreen row registers a single 320×180 `RGBCamera` — a size chosen in
`step_timing.CAMERA_SIZE`, not by any vehicle — and since the camera is about three quarters of
a step, an unflagged figure is not what a real car costs. `--camera-rig` takes the same
CARLA-shaped spec `sensor-survey.sh` takes, read by `tools/camera_rig.py`, and mounts the
vehicle's own cameras. Measured on `junction-1` at 10 Hz over 200 steps, replay row, same drive:
the seven-camera spec (six 512×288 and one 1280×720 wide, **5.42 MB of image a step** against
0.17) runs at **24.70 ms/step and 3.08x real** against **10.00 ms and 9.28x** for the invented
one, with row 6's no-graphics floor at 3.20 ms / 27.20x. Four things not to re-derive:

- **`rig_ms_median` is allowed in the timing loop and a row's `read` list still is not.** The
  bar is against forcing a *second render pass* — `SensorPack` reads with a parent node, which
  costs another `taskMgr.step()`. `CameraRig.read` passes none and copies the buffers the frame
  pass already filled: **3.90 ms** for the seven. Only the `image_source` camera reaches the
  observation, so the other six are read there or not at all, and a training loop reads all of
  them.
- **`image_source` must name a rig camera**, or MetaDrive registers a dead 320×240 `rgb_camera`
  beside the rig and renders it every step (`image_obs.py:68`). `agent_env.make_env` already
  merges `sensors` unless the caller names its own source.
- **The `sensors` column counts cameras by class, never by the name `rgb_camera`.** A rig's
  cameras are named by the spec, so a name test reports a seven-camera run as having no camera
  at all — the same mislabelling the live-env probe was added to prevent, by a different door.
  It prints `camera x7`.
- **A spec's `tick_rate` is not honoured and the run says so.** Buffers redraw once per
  `env.step` whatever the rate, so a rig declaring 0.1 s draws every 0.01 s on a 100 Hz dataset;
  a line is printed per dataset where the two differ and `camera_hz` records what they really
  drew at. Resampling is Phase 2 of `docs/implementation-plan/adjustable-simulation-sample-rate.md`.

**Read `policy_ms`, not the difference between the rows, and that was the plan being wrong
rather than a preference.** Measured three times over on `junction-1`'s 100 Hz dataset: row 1
at 8.90 / 8.99 / 10.07 ms a step, row 2 at 9.35 / 10.35 / 8.99, so the subtraction read +0.45,
+1.36 and **−1.08** ms while `policy_ms` held 0.37–0.43 ms throughout. About a millisecond of
run-to-run spread swamps it, and the two rows do not drive quite the same route anyway — a
replayed car follows the tape, an IDM car its own line, and it ends early. `policy_ms` is timed
around the policy call and is the answer; the replay row is the reference for whether the
simulator keeps up with nothing deciding at all.

**And the machine's state is worth more than the code's.** The same configuration measured
8 ms a step early in a session and 17 ms after twenty minutes of back-to-back sweeps. Absolute
figures are only as good as the box was quiet; ratios within one run are what compares.

Six things not to re-derive:

- **The camera readback is inside `env.step` and must not be timed twice.** With
  `image_observation=True`, `ImageStateObservation.observe` calls `perceive()` and rolls the
  3-frame stack while building the return value (`image_obs.py:85`) — no parent node, so it is
  the cheap buffer read. `sensor_ms` here is therefore the *numeric* sensors only. An earlier
  version of the plan had a row isolating "the readback" by not reading it, which is not a
  thing that can be arranged.
- **Every offscreen row carries a camera, and the `sensors` column has to say so.** It printed
  the row's *read* list — what the timing loop pulls out for itself — which the camera is
  deliberately not in, so rows 1 and 2 read as `imu,gps` and looked camera-less while drawing a
  320×180 frame every step. Keith read the table exactly that way. `sensors` is now taken off
  the live env (`image_observation` on **and** a camera really registered), and `camera_size`
  off the frame's own shape, so a row that stops building one says so instead of repeating what
  it was meant to do. **The camera must never go in `read`**: `SensorPack` reads with a parent
  node, which forces a second `taskMgr.step()` (`base_camera.py:188`) and would charge the
  benchmark for a frame no training loop draws. Row 3 was doing that *and* sending the image
  twice, the observation it ships already being the image stack - measured over the same
  drive, **3601.0 KB a step against 2700.9** once the camera left its read list.
- **The camera is about three quarters of a step, so it is what the sweep mostly measures.**
  `--rows 2,6` on `junction-1`, same route and policy either side: 16.69 ms a step against
  **4.06** at 10 Hz (5.45x real time against **19.82x**), 17.48 against **3.57** at 100 Hz
  (0.51x against **2.30x**). Row 6 reads imu/gps for exactly this reason — it used to read
  nothing, so the subtraction moved two things at once.
- **imu/gps cost ~0.13 ms, which no row can measure.** That is under the run-to-run spread that
  already defeated row 2 minus row 1; back to back, row 4 has come out dearer than row 2 on
  noise. Row 4 is therefore labelled as what it is — the vision-only shape — and
  `sensor_ms_median` answers the sensor question directly.
- **`--physics-hz` exists because `--step-hz` derives both keys from one number.** 10 Hz gives
  `(0.02, 5)` — **50 Hz of physics, not 10** — and 100 Hz gives `(0.01, 1)`, so one step at
  100 Hz is *cheaper* and `ms/step` is not comparable across rates. `--physics-hz 100
  --step-hz 10` is 100 Hz integration with 10 Hz decisions: CARLA's own default shape
  (`fixed_delta_seconds` 0.1, `max_substep_delta_time` 0.01, `max_substeps` 10), and the only
  pairing whose number means anything beside a CARLA figure. A rate that does not divide the
  step is refused, never rounded.
- **The per-step overhead is what a higher rate multiplies, not the physics.** Measured with
  no graphics on `junction-1`: 2.14 / 2.44 / 2.87 ms a step at 5 / 10 / 20 ticks, so about
  **1.90 ms fixed plus 0.049 ms a tick**. Per simulated second, 10 Hz → 100 Hz is 10x the
  overhead and only 2x the integration.
- **With a camera the camera is the budget** — 16.80 ms a step at 10 Hz against 16.11 at
  100 Hz on one run, identical to within the noise, because one frame is drawn per `env.step`
  whatever the rate (`base_engine.py:458`, unconditional). So the image rate *is* the step
  rate, a camera costs a full 10x more per simulated second at 100 Hz, and that is the whole
  of the difference in real-time factor: **5.79x at 10 Hz against 0.61x at 100 Hz** on the same
  drive. The decision rate is the only one that separates, and it separates in the caller's
  loop rather than in any config key.
- **The first env of a process is dearer than the ones after it**, so `prime` builds and
  throws one away before anything is measured. Without it the first row carried the graphics
  driver's shader compilation and cache filling, and since the rows are meant to be compared
  with each other that is a bias rather than noise. `wall_seconds` also starts *after* the
  warm-up steps rather than before them, for the same reason — an earlier version counted them
  in and reported the floor as the dearer of the two rows.
- **There is no unthrottled 3D row, and that was measured before it was dropped.** `ForceFPS`
  looked like the thing to raise, but `force_render_fps=1000` gives 16.59 ms a frame against
  16.67 stock at 100 Hz and 83.34 against 83.50 at 10 Hz, and loading `sync-video #f` into
  panda3d before the window exists does not move it either. The ceiling is the compositor's
  60 Hz. The row records `force_fps` — the engine's own state — instead of claiming a number
  it does not have.

**Every run writes its own CSV**, `<workspace>/reports/step-timing-<label>-<stamp>.csv`, never
appending and never overwriting, with the machine (host, docker, CPU, GPU, GL ceiling,
versions) repeated on every row so two machines' files concatenate. `STEP_HZ` is deliberately
not read: the sweep drives *every* rate, each at the one it was written at, and picking one
would be the opposite of the comparison. `--label` matters in a container, where the hostname
is a random id.

**Phase 1 makes 100 Hz *available*, which is narrower than it sounds.** The only things that
*record* numeric sensors at that rate are `sensor_survey.py`'s per-step CSV and
`policy_client`'s wire. `drive.py --record` writes observations and actions only. 100 Hz IMU and
GPS on disk from `drive.py` is separate work, named here so it is a decision rather than an
omission — as are the 20 Hz cameras, which are Phase 2 of
`docs/implementation-plan/adjustable-simulation-sample-rate.md`.
### MetaDrive runs on 3.10, and the container is one environment, not two (2026-08-21)

**The two-interpreter split was never a MetaDrive requirement, only how it was installed here.**
`scripts/drive.sh`, `sensor-survey.sh` and `step-timing.sh` shell out through `METADRIVE_PYTHON`
to a 3.8.20 / numpy 1.24.4 venv because that is what the reference checkout has. MetaDrive
0.4.3 has **no `python_requires` cap** (its extras are keyed `:python_version >= '3.8'` with
`numpy>=1.21.6` unbounded), **no `ext_modules`** — the wheel is `py3-none-any`, so no compiler
and no 3.8 ABI tie — **zero numpy-2-removed aliases** in `metadrive/`, no `numpy.core` imports,
and its ten `copy=False` call sites are all `.astype()` / `np.nan_to_num()`, neither of which
numpy 2 changed; the one that did is `np.array(copy=False)`, which does not appear. It resolves
and runs on this repo's 3.10 / numpy 2.2.6 with **no version in `uv.lock` moved** — the lock
gained 543 lines and changed nothing that was already in it.

Installed as an **opt-in `sim` dependency group**, so `uv sync` on the host still installs only
the default and `dev` groups and the checkout-plus-3.8-venv arrangement keeps working until
`uv sync --group sim` is run deliberately.

**The group pins a commit, not `==0.4.3`, and that is not fussiness.** `git describe` on the
checkout says `MetaDrive-0.4.3-32-g85e5dadc` — 32 commits past the tag, on public `origin/main` —
and `metadrive.constants.EDITION` reports `MetaDrive v0.4.3` for both. A version pin would let
two machines run different simulators while every step-timing CSV claimed they were the same,
which is the one thing a cross-machine benchmark must not allow. Nothing in the CSV can tell
them apart, so the lock is what does.

Measured, `mosque`, 200 steps, same rows either side:

| | host, 3.8 / numpy 1.24 | container, 3.10 / numpy 2.2 |
|---|---|---|
| row 1, offscreen replay | 3.78 ms/step, 25.91x | 4.05 ms/step, 24.23x |
| row 6, no graphics | 0.99 ms/step, 86.35x | 1.03 ms/step, 82.26x |

Six things not to re-derive:

- **The NVIDIA container toolkit does not install the glvnd EGL manifest, and without it the
  benchmark silently runs on the CPU.** `NVIDIA_DRIVER_CAPABILITIES=graphics` injects
  `libEGL_nvidia.so.0` and `libGLX_nvidia.so.0`, but leaves `/usr/share/glvnd/egl_vendor.d/`
  holding nothing but Mesa's `50_mesa.json` — so libglvnd never sees the driver. Measured before
  the image wrote its own `10_nvidia.json`: `getDriverRenderer()` reported
  `llvmpipe (LLVM 15.0.7, 256 bits)` at 16384 max texture, against the RTX's 32768. It does not
  error and the table looks ordinary. `gl_renderer` in the CSV is the check.
- **`utility` in that same variable is what injects `nvidia-smi`**, which `step_timing.py:275`
  reads the `gpu_name` column out of. `graphics,compute,utility`, all three.
- **panda3d already declares the fallback.** Its own `Config.prc` ships `load-display pandagl` /
  `aux-display p3headlessgl`, so EGL would be found eventually anyway; the image swaps the two
  lines so it is the first choice rather than the result of a failed GLX attempt, and leaves
  `pandagl` as the aux so an X socket still gets the 3D row. Measured on the host with `DISPLAY`
  unset: GLX lands on the **integrated** card at 16384, EGL on the discrete one at 32768.
- **`UV_PYTHON_INSTALL_DIR` is a permissions fix, not tidiness.** uv puts its managed CPython in
  `$HOME/.local/share/uv/python`, which at build time is `/root` at mode 700, and the venv's
  `python` is a symlink into it. The container runs as the host's uid — so reports written into
  the mounted workspace are not owned by root — and that user cannot read `/root`: the
  interpreter dies before it starts with `sys.executable = ''` and
  `ModuleNotFoundError: No module named 'encodings'`, which reads as a broken image rather than
  as a permissions problem.
- **The venv lives outside `/work`.** The repo is bind-mounted there and the host's own `.venv`
  is in it; `UV_PROJECT_ENVIRONMENT=/opt/venv` plus `UV_NO_SYNC=1` is what stops a `uv run`
  inside the container reaching into — or resyncing — the host's environment. The image is built
  in `/work` for the opposite reason: the editable install of `osm_scenario` then points at
  `/work/src`, which the mount replaces with the live source rather than a stale copy.
- **`network_mode: host` is a measurement decision.** Row 3 times a round trip to `--policy-url`
  at 0.126 ms with `TCP_NODELAY`; a bridge network in front of a number that small would corrupt
  the row it exists to produce.
- **`/etc/localtime` is mounted because the image has no clock of its own.** No `/etc/localtime`
  and no `/usr/share/zoneinfo`, so glibc falls back to UTC — and a step-timing CSV, whose whole
  index is the stamp in its name, came out **8 hours** adrift of a run made outside the container
  on the same machine (host `Asia/Singapore` +0800 against container UTC). A `TZ` variable is not
  a substitute: resolving a zone *name* needs the zoneinfo database the image does not carry,
  while the mounted TZif file is all glibc needs when `TZ` is unset.

**Two tests were `skipif` on paths that do not exist in a container, and a gate that stops
running is worse than one that fails.** `test_conversion._metadrive_src` now falls back from the
checkout to the installed package — it reads MetaDrive's *files* rather than importing them, so
either directory does, and it finds it with `importlib.util.find_spec` rather than an import
because importing `metadrive` pulls in panda3d, which is the whole reason that module reads
files. `test_camera_rig` reads `rigs/cams.txt` out of the repo and is no longer conditional at
all. Both were silently skipping in the container; both run now.

**There is one sweep, not a host one and a container one, and no rebuild between them.**
`step-timing-docker.sh` and `container-check.sh` both end at `scripts/step-timing.sh`, which is the
only caller of `tools/step_timing.py` anywhere in the repo — so a change to either is picked up by
all three entry points. And the image copies in only `pyproject.toml`, `uv.lock`, `README.md` and
`src/` (`docker/Dockerfile:78-79`); `tools/` and `scripts/` exist in the container *solely* through
the `.:/work` bind mount, and the editable install points at `/work/src`, which the mount replaces.
So an edit to `tools/` or `scripts/` is live in there immediately — only a dependency change needs
`docker compose build`.

**Row 7 is the one row the container cannot run** — it opens a window and there is no display.
Everything else works in there, including `run-stages-*.sh` and `pytest`, because there is one
interpreter.

**The camera-rig spec is `rigs/cams.txt`, in the repo, and that is what makes `--camera-rig` the
same string everywhere (2026-08-22).** It used to live at `~/Desktop/work/wingfin/data/cams.txt`,
reached in the container only through a second bind mount (`RIG_DIR` → `/rig`), so one run was
`--camera-rig ~/Desktop/.../cams.txt` outside and `--camera-rig /rig/cams.txt` inside — and Keith
went looking for a `/rig` that exists only in there. `scripts/_common.sh:18` cds to the repo root
before a script does anything and the container works from `/work`, so a repo-relative path is
correct from `scripts/`, from the root and inside. **Not `config/`**, which is where the file
whose checksum feeds `generation_fingerprint` lives — the checksum is over the parsed
`ConverterConfig` (`generation.py:4007`) and a neighbouring file cannot move it, but putting a
vehicle spec there invites the question every time. **Not `docker/rig/`**, whose `.gitignore`
excludes everything but the note, so a spec there never travels to another machine; it stays as
the escape hatch for a spec deliberately kept out of the repo, which is what `RIG_DIR` is for.

**The sweep writes each row as it measures it, and that is not a nicety.** It used to collect
records in a list and open the CSV once at `main`'s last statement, so a twelve-row run with the
seven-camera rig — minutes of GPU time — left **nothing** when it was interrupted at row 11.
`step_timing.RowWriter` opens on the first row (so `--no-csv` and a sweep that measures nothing
still leave no file), flushes every row (microseconds against rows that are tens of seconds), and
the `KeyboardInterrupt` guard round the dataset loop names the file and says how many rows are in
it. `csv_path` took a `dataset_name` argument it never read — the path comes from
`arguments.dataset[0]` — which is what let the whole thing be settled, and the already-exists
refusal moved, **before** `prime` rather than after the sweep. Three things not to re-derive:
`wrote_anything` is a flag rather than `self._handle is not None`, because the line naming the
file is printed *after* the close and reading the handle silently removed it from every run;
the interrupt leaves through `os._exit(130)` after an explicit flush, because panda3d segfaults
tearing an engine down out from under a `KeyboardInterrupt` and the process was exiting 139;
and **`cmd &` in a non-interactive shell sets SIGINT to `SIG_IGN` in the child** (POSIX), so
five attempts to test the interrupt with `kill -INT` measured the shell rather than the code —
`( trap - INT; exec ... ) &` is what puts the default disposition back.

### The decision rate is a third dial, and MetaDrive has no clock for it (2026-08-22)

The question arrives shaped like CARLA's three knobs — **world tick / decision + camera /
physics** — and **MetaDrive has two**. `env.step` advances
`physics_world_step_size × decision_repeat` and returns, so it *is* the world tick, *is* the
policy call and *is* the camera draw; `base_engine.py:458` calls `task_manager.step()` once
per step unconditionally, and the sensor config is `name=(cls, *args)` with **no slot for a
rate anywhere**. So the middle column is not a key that was left unexposed. It is a **stride
counted in the caller's loop**, and `drive.decision_stride` / `drive.decides_on` are the only
places it is worked out and the only places the schedule is decided — both `drive.py` and
`step_timing.py` call them, because a benchmark running a different schedule from the tool it
prices is measuring nothing.

`--decision-hz` on `drive.py` and `step_timing.py`, `DECISION_HZ` in `.env` for `drive.sh`.
Unset it is the step rate, byte for byte the run there always was.

**Two arithmetic constraints, both refused rather than rounded.** Physics must be a whole
multiple of the world tick — a 100 Hz tick with 50 Hz physics is half a substep and does not
exist — and a decision a whole divisor of it, nothing moving between two steps. So of a grid
like `10/20/50`, `100/10/50`, `100/20/50`, `100/10/100`, `100/20/100`, only the last two can
be built; the first three are not configurations anyone is missing, they are arithmetic.

**The flag is `--decision-hz` and not the `--camera-hz` the plan named, and that is the
openpilot half of it.** `RemotePolicy` sends `step_seconds` to the server and
`OpenpilotDriver.spec` checks it against `_DT_MDL = 0.05`; a camera-only flag would have left
it at the env.step interval — 0.01 s at 100 Hz — with the bridge's lag compensation and
curvature-rate limit mis-scaled by 5× and nothing raising anything. It is now
`sim_step_seconds × stride`, **the interval between two calls**. Measured on `mosque` at
`--step-hz 100 --decision-hz 20`: **868 calls over 4337 steps, `arrive_dest=True`, completion
0.950, and no note from `spec`**; the same drive without the flag prints the note and makes
4336 calls. That pairing beats `convert --step-hz 20`, which this documentation used to
recommend for the bridge — same 0.05 s control interval, ten times the physics under it.

**No wire change was needed, and that is the flag's doing rather than an omission.** With the
decision gated there is no `/act` on a skipped step, so every call already carries fresh
sensors and there is no stale frame to omit. The rates ride in `/spec`'s existing `extra`.

**On a replay row the decision half is vacuous and the tool has to say so.**
`ReplayEgoCarPolicy` runs *in* the engine and MetaDrive calls it every `env.step`; nothing
outside can decimate that. So `--decision-hz` gates the reads and the camera draw alone there,
which is what the sweep's row 1 wants and is printed rather than implied.

**The draw is gated at the render call, not at the buffer — `tools/frame_gate.py` (2026-08-22,
second round).** An earlier version of this section said the draw could not be saved, on a
`buffer.set_active(False)` experiment that measured **1% of a 26 ms step**. That measurement
was right and the conclusion was wrong: **an `RGBCamera` owns two GraphicsOutputs and
`self.buffer` is the cheap one.** `rgb_camera.py:38-52` builds a
`FilterManager(self.buffer, self.cam)` and calls `render_scene_into(...)` with
`set_multisamples(16)`, which creates a **second** buffer
(`direct/filter/FilterManager.py:325-328`) hosted by the first; the scene — terrain, PBR, that
16x MSAA on top of the global 8x at `engine_core.py:96-103` — is drawn into *that*, and
`self.buffer` only draws a fullscreen quad over the result. Seven quads were switched off and
seven scene renders kept running. **Anyone reaching for `set_active` needs that fact first.**

`frame_gate` gates one level up instead, where nothing is left to infer: it rebinds
`engine.task_manager` to a forwarding proxy whose `step()` passes through only on a decision
step. That is exactly the two render calls in `BaseEngine.step` (`base_engine.py:455,458`),
because `base_engine.py:65` is `self.task_manager = self.taskMgr` and **every other render in
MetaDrive reaches the same object through `taskMgr`** — `base_engine.py:394,761`,
`base_env.py:439,534,569`, `main_camera.py:504` and `base_camera.py:188,193`, which is
`perceive(new_parent_node=...)`'s own second pass. So `env.reset`'s frames and `SensorPack`'s
extra render are untouched by construction. One `task_manager.step()` is one
`graphicsEngine.renderFrame()`, so a call that does not happen is a frame that is not drawn.

The read-back is held with it: `ImageObservation.observe` (`image_obs.py:80-88`) returns the
stack unrolled rather than calling `getScreenshot` again and rolling a duplicate frame in.
**Only the image half** — `ImageStateObservation.observe` composes `{"image", "state"}` and the
41-number state stays fresh every step, a vehicle state not being a camera.

Measured, `mosque` 100 Hz, row 1, `rigs/cams.txt`, 200 steps, **three runs of each**:

| | ms/step | x real |
|---|---|---|
| `100/100/100` | 26.11 | 0.34x |
| `100/20/100` | **6.21** | **1.47x** |
| `100/10/100` | **3.66** | **2.54x** |
| `100/20/100 --draw-every-step` | 26.94 | 0.36x |
| `100/10/100 --draw-every-step` | 26.69 | 0.37x |

The last two are the control and are what says where the money was: with the draw put back on
the world tick a lower decide rate is worth **under a millisecond of 26** — the read alone,
which is what the old section measured — and with it gated the same drive runs **4.2x** faster
at 20 Hz and **7.1x** at 10 Hz. `--draw-every-step` exists to keep that comparison available.

Four things not to re-derive:

- **It is verified by counting, not by timing.** Over a real 60-step drive at stride 5,
  `gate.draws` is 12, panda3d's own `globalClock.getFrameCount()` moves by 12 over the same
  window, all 48 held steps return an image bit-identical to the step before, all 12 drawn
  steps return a different one, and the state half moves on all 60.
- **`camera_draw_hz` is counted by the gate, not declared.** It used to be written as
  `step_hz` and was the one camera column a record never re-read off the live run — which is
  precisely the column that must not be taken on trust here.
- **`--render 3D` is never gated**, and that is a decision rather than an omission: the window
  is the point of that mode, `ForceFPS.real_time_simulation` steps the task manager inside the
  substep loop as well (`base_engine.py:454-455`), and `--agent-policy manual` polls the
  keyboard there. `install` returns `None` for anything that is not `_render_mode == offscreen`,
  so `--render none` — which has no cameras at all — is untouched too.
- **`ms/step` is a median over every step, so at a stride of 2 or more it is a *held* step**
  (1.15 ms) while `p95` is a drawn one (26.65). Neither describes the drive; `x real`, or
  `step_ms_mean` in the CSV, does. The two kinds of step are one distribution and the median
  lands in the larger half.
- **`drive.py --record` writes the held frame**, because the held frame is what the car had.
  A recording made at `--decision-hz 20` on a 100 Hz world carries each image five times, and
  that is the recording of a 20 Hz camera rather than a fault in it. The wire is unaffected:
  there is no `/act` on a skipped step, so a hosted model is only ever sent a fresh frame.

**`camera_rig.tick_rate` is now checked against the interval the cameras are really read at**
— `load_rig(path, read_interval_s=...)` — rather than against a hard-coded 0.1 s, so a 20 Hz
spec is correct under `--step-hz 100 --decision-hz 20` and wrong on an unflagged 10 Hz sweep.
`CameraRig.tick_rate_s` carries the declared value for a caller that cannot know the interval
yet: `sensor-survey.sh` drives one rate and refuses, `step-timing.sh` drives every rate a
workspace holds and notes per dataset. Still validation, never a resample.

**A sweep skips the dataset the rate does not divide rather than ending.** `--decision-hz 20`
divides `scenarionet-100hz` and not `scenarionet-10hz`, and the table shows the refusal beside
the row that ran — the same shape as the replay-rate mismatch already there.

**Several configurations go in a file, and they are driven in one process.**
`--rate-sets scripts/rate-sets.csv` is `name,step_hz,decision_hz,physics_hz`, one whole
configuration a row, into **one** CSV with a `rate_set` column. One process is the decision, not
a convenience: `prime` is paid once — the first env of a process is dearer than the ones after
it — and every machine column is identical by construction rather than by two runs happening to
agree. The file lives in `scripts/` and the path is the same inside the container and out, for
`rigs/cams.txt`'s reason. Four things not to re-derive:

- **A set drives only the dataset written at its own `step_hz`**, the one place `--rate-sets`
  behaves differently from the flags. A set is a whole configuration, so driving it against a
  tape at another rate measures something nobody asked for, and every replay row of it would
  skip anyway. Without a set the sweep still drives every rate a workspace holds.
- **A set's `physics_hz` outranks row 5's own 100 Hz pin**, so rows 2 and 5 coincide under any
  set that names one. Said in the footer once rather than per row.
- **`--rate-sets` refuses to sit beside `--step-hz` / `--decision-hz` / `--physics-hz`.** Two
  sources is how a CSV comes to describe a run that did not happen.
- **`drive()` takes the decision rate as an argument and must never read `arguments.decision_hz`
  again.** Under `--rate-sets` the rate lives on the set and the namespace holds `None`, so the
  first version ran every set at stride 1 while the table printed the rate the set asked for —
  a benchmark misreporting its own configuration. `test_the_timing_loop_takes_its_decision_rate_from_the_caller`
  walks the AST of `drive` and fails on that attribute.

**`policy_ms` / `sensor_ms` / `rig_ms` are per call, not per step, and that changed with this.**
They are collected only on the steps they happen on. Charging them to skipped steps too makes
the median *a skipped step* the moment the stride is 2: measured at `--decision-hz 10` on a
100 Hz world, `policy` printed **0.00 ms** against 0.20 at full rate, which is not the model
getting faster. `step_ms_*` is still every step, because `ms/step` is per step by definition.

**The container needs no rebuild for any of this.** The image copies in only `pyproject.toml`,
`uv.lock`, `README.md` and `src/`; `tools/`, `scripts/` and `rigs/` are live through the
`.:/work` bind mount. Verified: `step-timing-docker.sh mosque -- --step-hz 100 --decision-hz 20
--camera-rig rigs/cams.txt` ran on the RTX 4050 at 32768 px with `camera_count 7`,
`decision_hz 20`, `camera_draw_hz 20`, CSV owned by the caller — and the four-set batch there
reproduces the host's figures to within the noise (0.34x / 2.55x / 1.47x against 0.34x /
2.64x / 1.49x).
