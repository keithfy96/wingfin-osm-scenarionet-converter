# openpilot and the AV3 model at the wheel

The openpilot bridge, the pedal map measured on MetaDrive's own car, the AV3 checkpoint, and
the six conversions between the model and the car.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

### openpilot drives through the same socket, and the route is a sensor now (2026-08-22)

**Every `uv run python examples/openpilot_server.py` below is the host form** — a development
machine with the venv synced, which is what these measurements were taken on. It is no longer what
the docs tell a reader to run: `docs/running-a-test.md` tiers 4 and 5 are container-only now, via
`./sim.sh python3 examples/openpilot_server.py ...`, because the rig has no `uv` and should not
need one. The commands are otherwise identical, and the numbers carry across (verified: tier 5
through the container gives the same 3788 / 0.950 / `result OK`).

`wing-sim/openpilot/bridge/zapeta/server.py` is a **controller, not a driver**: per tick it takes
a predicted path plus `v_ego` / `yaw_rate` / `steering_angle_deg` and returns `steer` / `throttle`
/ `brake`. It never sees an image — the thing that turns cameras into waypoints is a separate
CARLA-shaped AV3 model under `evaluation/src/inference_models/`. So filling stage 7c's empty
`act()` with it needed a path handed to it, and `tools/openpilot_policy.py` +
`examples/openpilot_server.py` are that translation. `--policy-url` and `step-timing.sh --rows 3`
reach it unchanged.

**`route` is a new `--sensors` name and it exists because the observation's route is unusable.**
The `[19:41]` navigation block is normalised and clipped at 30 m, and neither can be undone.
`SensorPack` sends `reference_trajectory` instead — the recorded route as a `PointLane`, the same
object `TrajectoryNavigation` steers by — 25 points at 2 m in **metres**, index 0 at the car's own
projection, ego frame **x ahead / y left**. A drive without it is refused by name rather than
coasting.

**`/spec` is sent before `env.reset()`** (`drive.py:885` against `:899`), so there is no ego and no
scenario when it goes: `SensorPack.describe`'s projection block has always been null, and the car's
steering geometry cannot be read there at all. `SensorPack.episode()` carries both, merged into
`/episode`. A controller that must be told what full lock means in degrees cannot get it any other
way.

Five things not to re-derive, each read off the bridge rather than assumed:

- **`carla_steer_curvature_gain: 0.0` is the whole fit.** It selects `server.py:788`,
  `-road_wheel_deg / max_steer_angle`, and `action[0] × max_steering` *is* the road-wheel angle in
  degrees (`base_vehicle.py:478`) — 40° for the default vehicle. Send MetaDrive's own `max_steering`
  as `max_steer_angle` and nothing is left to convert. The default path inverts an empirical gain
  measured on CARLA Town10HD.
- **Both ends negate**: MetaDrive is left-positive, CARLA right-positive. The waypoints' `y` is
  `-left` and the action's steering is `-steer`. Get one of the two wrong and the car drives
  smoothly into the oncoming carriageway with nothing raising anything.
- **`target_speed` defaults to 0, which is a stop.** `server.py:614` is
  `float(msg.get("target_speed", 0.0))` — an omitted target is not "no opinion". Sent every tick;
  `--target-speed-mps` sets it.
- **`steer_ratio` in `init` is stored and never used.** The bridge divides by its own
  `CP.steerRatio` on ingress (`:646`) and egress (`:788`), so the two cancel when ours matches and a
  mismatch mis-reports the wheel angle to the rate limiter rather than changing the output scale.
  12.0 is what wing-sim's own config sends.
- **The bridge is written for 20 Hz.** `_DT_MDL = 0.05` is what its lag compensation, its
  curvature-rate limit and its per-tick steer window are counted against; `OpenpilotDriver.spec`
  prints the ratio at any other rate. **`--step-hz 100 --decision-hz 20` is what matches it**
  (see `docs/reference/running-the-simulator.md`) — better than the `convert --step-hz 20` this
  used to recommend,
  the control interval being the same 0.05 s with ten times the physics under it. And **`accel_map.py` is CARLA pedal calibration** — two 8×11 tables from a
  "Town10HD calibration sweep on Tesla M3 @ 20 Hz sync" — so longitudinal tracking is wrong here
  until re-measured. Steering is not, because that path is geometric. Both halves are now
  measured against the real bridge — see the section below, where the pedal map answers a
  gentle braking request with a fifth of full throttle.

**`--backend stub` is a real socket, not a mock**, and is what proved the frame and the signs
before there was a fork to blame. Measured: `junction-1` **380 steps, arrive_dest=True,
completion 0.951** and `mosque` **435 / 0.951**, 0.5 ms and 2.5 KB a step, against
`--backend constant --steering 1.0` leaving the road in 13.

### The real bridge drives it, and only the steering half of the fit was right (2026-08-23)

The fork is on this machine now — `/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/`,
which is wing-sim's own `openpilot/` tree filled in: `bridge/zapeta/` plus the fork cloned at
`c767ace8` with its seven submodules. `--backend bridge` had been written against
`server.py` and never run; it works, and what it measured is worth not re-deriving.

**That build is now in this repo, fork and all** (2026-08-25) — `docker/openpilot/` carries the
Dockerfile byte-identical below its header, the 1649 lines of `bridge/`, and **`deps/openpilot/`:
309 MB of the fork itself, vendored** with its `.git` and seven submodule gitdir pointers removed,
because git will not track a directory containing one. `deps/openpilot/VENDORED.md` records the
commit and every submodule SHA. `scripts/bridge.sh` drives the build.

**Vendoring it needed `git add -f`, and that is the trap.** The fork ships 75 of its own
`.gitignore` files which exclude the prebuilt binaries the build links against —
`third_party/acados/x86_64/lib/{libacados,libblasfeo,libhpipm}.so` among them, which the lateral
and longitudinal MPC load. A plain `git add` skips them silently and the tree looks complete. The
two `.gitattributes` with `filter=lfs` also had to be commented out, or the 82 MB of
`selfdrive/modeld/models/*.onnx` would be pushed at an LFS server this repo does not have.

**Verified by building it**: `bridge.sh build` from the vendored tree produced a working image,
the acados prebuild linked `-lacados -lhpipm -lblasfeo` against the vendored `third_party/acados`,
and a full `junction-1` drive through it gave **8656 steps, `arrive_dest=True`, completion 0.950,
`result OK`** at 3.527 ms a call. The image is **5.5 GB**, not 6.17 — stripping the fork's 612 MB
`.git` took it out of the layer the Dockerfile `COPY`s.

The two hand-typed docker commands below are what `bridge.sh` runs, kept here because they are
what the measurements were made with:

```bash
cd scripts && ./bridge.sh build && ./bridge.sh start    # what the two lines below now are
# docker build -t metadrive-wingfin-openpilot:prod -f docker/openpilot/Dockerfile docker/openpilot
# docker run -d --name metadrive-wingfin-openpilot-bridge --network host \
#   -e SIMULATION=1 -e NOBOARD=1 -e SKIP_FW_QUERY=1 -e "FINGERPRINT=TESLA MODEL 3" \
#   -e OPENPILOT_TRAJECTORY_TYPE=0 -e BRIDGE_PORT=5558 \
#   -e PYTHONPATH=/opt/bridge:/opt/openpilot:/opt/project/common \
#   -w /opt/project metadrive-wingfin-openpilot:prod python3 -m zapeta.server
uv run python examples/openpilot_server.py --backend bridge --longitudinal accel --port 8642
# then, from inside scripts/
./drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --sensors imu,route --step-hz 100 --decision-hz 20 --render none
```

**The checkout arrives with every symlink missing, and scons is what tells you.** `git status`
inside the fork showed ten deletions — `rednose`, `laika`, `tinygrad`, `selfdrive/hardware` and
six `third_party` entries, all mode 120000 in the index — and the build died on
`Missing SConscript 'rednose/SConscript'`, which reads as a broken Dockerfile rather than a
transport that dropped symlinks. **`docker/openpilot/pull.sh` now repairs this itself** on every
run, re-deriving the ten from `ls-files -s` rather than naming them, so it should not recur.
`git checkout --` on those paths alone is the repair; the
`M` entries beside them are the LFS model files `pull.sh` deliberately does not pull, and must
be left. Docker copies symlinks as symlinks, so nothing else was needed.

**`AV3_MPC_MENU` defaults to `"4 16 20 32"` and `WAYPOINT_OFFSETS_S` is four waypoints**, so the
acados solver this repo needs is in the prebuilt menu — confirmed in the build log
(`[prebuild_lat_menu] done N=4`), not assumed. A count outside the menu is code-generated at
connect time and shows up as a long first tick, not an error.

**The steering fit is exactly right, and that is now measured rather than argued.** A 124.95°
column angle came back as `steer` 0.2603, which is `124.95 / 12 / 40` to four figures — the
geometric branch `carla_steer_curvature_gain: 0.0` selects, with `max_steer_angle: 40.0` and the
bridge's own `CP.steerRatio` cancelling ours. Both negations are right: with the longitudinal made
sign-correct the bridge completes **`junction-1` 0.950 and `mosque` 0.950**, the same completion
the stub reaches.

**The longitudinal fit is not usable, and `--longitudinal` is which of two wrongs to take.**
*(Superseded by `--longitudinal table`, below — the two wrongs both remain, and this is why.)*
`accel_map.accel_to_carla` returns throttle whenever `accel_cmd >= coast_accel(v_ego)`, and
`coast_accel` is the CARLA Tesla M3's *measured zero-throttle deceleration* — **−1.582 m/s² above
10 m/s**, −1.150 at 5, −1.377 at 3.5. MetaDrive's vehicle does not coast down anywhere near that
hard, so every request gentler than the M3's own drag comes back as throttle: `accel_cmd` −1.0
gives **throttle 0.274** at any speed over 10 m/s. Measured over the real drives:

| | decel requests | answered with throttle | v mean | outcome |
|---|---|---|---|---|
| `junction-1` `--longitudinal pedal` | 201 | **137 (68%)** | 16.4 m/s | ran away 13.9 → 20.5 m/s, **out_of_road at 4.08 m**, completion 0.529 |
| `mosque` `--longitudinal pedal` | 2469 | 11 (0%) | 3.5 m/s | arrived, 0.950 |
| `junction-1` `--longitudinal accel` | — | — | 4.4 m/s | arrived, 0.950 |

**It is speed that decides, not the map**, which is why `mosque` survived and reading one run
would have got this backwards: `mosque` sat at 3.5 m/s where `coast_accel` is −1.38 and its
requests averaged −1.42, so they braked. `junction-1` started at 13.9 m/s where coast is −1.58
and asked for −0.2 to −1.5, all of it above the crossover. **Nothing opposes the resulting
throttle**, because `waypoints_from_route` is `route_gt.py`'s constant-speed model by
construction — the trajectory says "I am going as fast as I am going", so in
`blended_except_creep` the e2e planner reads no intent to slow. That is faithful to wing-sim, not
a porting error: `route_gt.py` exists "to isolate whether drift is caused by the model or the
controller", and it is the *model* half that is still missing here.

**`--longitudinal accel` normalises `accel_cmd` by the Tesla envelope the bridge plans within**
(`TESLA_ACCEL_MAX/MIN`, +2.0 / −3.48, each direction by its own end). **It is not a calibration
either** — MetaDrive's `action[1]` is engine and brake *force*, not acceleration — and it
undershoots badly: 4.4 m/s mean against a 10 m/s target, 8726 steps where the stub takes 3788.
What it is, is sign-correct and unit-consistent, which on this simulator the pedal map is not, and
it is what makes the steering claim above measurable at all. `pedal` stays the default because it
is what the bridge emits and what a CARLA consumer gets; `--backend stub` answers in pedals only
and refuses `accel` by name.

Three things not to re-derive:

- **The container has no clock of its own** — its log stamps came out 8 hours off (UTC against
  `Asia/Singapore`). Mount `/etc/localtime` if a stamp from it is ever compared with a host one,
  the same fix `docker/compose` already carries for the step-timing image.
- **The bridge round trip is 3.5–3.8 ms a call** against the stub's 0.8, on the same 2.5 KB. That
  is the real MPC solving, and it is small beside `env.step` at 100 Hz.
- **`v_cruise_kph` arrives correctly** — 36.0 for `--target-speed-mps 10`, so a runaway is never
  the target failing to reach the bridge.

**`step_timing.drive` did not call `policy.start_episode`**, which was invisible only because
`SensorPack` re-reads the projection lazily. It does now. And **`--policy-sensors` overrides row 3's
`read` list rather than ROWS being edited**: what a hosted model is sent is the model's business,
and changing the row definition would make every CSV taken under it mean something else.

### The pedals are measured on MetaDrive's own car now (2026-08-23)

Stage 9 Phase 0, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.

**None of this is a controller, and the bridge already carried the component it replaces.**
The model decides where to go, the bridge decides how hard and which way — `accel_cmd` in
m/s² and a steering angle in degrees — and a pedal map decides only *how far to press a pedal
to get that acceleration on this car*. `server.py:788-792` does exactly two conversions
before replying, side by side: a road-wheel angle into a normalised steer, and
`accel_to_carla(self._last_actuators.accel, v_ego)` into a throttle and a brake. Both are
properties of the car, and `accel_map.coast_accel`'s docstring says which kind of thing it is
— *"Realized accel at zero control (engine braking), from the measured col 0."* **The
steering conversion came out free** because it is geometry: `action[0] × max_steering` *is*
the road-wheel angle in degrees (`base_vehicle.py:478`) and the geometric branch emits
`-road_wheel_deg / max_steer_angle`, both sides 40°. Pedal to acceleration is not geometry,
so it had to be measured — which is the whole of why one half of the fit was right and the
other was not.

`tools/pedal_sweep.py` measures the table, `tools/pedal_map.py` inverts it,
`calibration/metadrive-pedal-map.json` is the file, `--longitudinal table` is the third mode.
The fork is never touched — the reply already carries `accel_cmd` in m/s², so the conversion
is entirely on our side.

```bash
cd scripts && ./pedal-sweep.sh junction-1        # ~9 s, no GPU, no display
uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
```

**MetaDrive has no aerodynamic term at all**, which is the whole reason the CARLA table is
wrong here rather than merely imprecise. `_apply_throttle_brake` (`base_vehicle.py:493-520`)
applies a constant `setBrake(2.0)` to all four wheels *even under throttle* and nothing else
resists, so the car coasts at a **flat −0.364 m/s² at every speed** — a quarter of the −1.582
the bridge assumes. Above `max_speed_km_h` (80, so 22.22 m/s) engine force is cut to zero,
which is the one place the table's speed axis earns its keep; everywhere else the response is
speed-independent to within 6%. And **`max_engine_force` / `max_brake_force` are sampled**,
not constants — `BoxSpace(750, 850)` / `BoxSpace(80, 180)` at `pg_space.py:239-240` — measured
**759.464 / 89.464** identically on both extracts at both rates, because the parameter seed is
the scenario index and each of our datasets holds one scenario. The file records them and
every episode checks the live car against them.

**The sweep visits speeds; it must not let the pedal choose them.** Holding one pedal and
letting the car sweep the range is the obvious shape and the flat coast kills it: near the
pedal that cancels the coast (+0.036) the car would take **440 s and 4.9 km** to cross the
range, and the pedals either side never leave the end they start at. So the car is trimmed
*to* 23 speeds and all 41 pedals are probed at each — 2,829 steps, nine seconds.
`BulletPlaneShape(Vec3(0, 0, 1), 0)` (`terrain.py:179`) is **infinite**, so driving straight
for kilometres is fine and `map_region_size` never bounds it.

Measured against the real bridge, both extracts, `--step-hz 100 --decision-hz 20`. "hard
decels" are requests below the coast (`accel_cmd < −0.5`), where the sign is not in dispute;
"delivers" is the median `|produced − requested|` over every call:

| | calls | hard decels | answered with throttle | delivers | outcome |
|---|---|---|---|---|---|
| `junction-1` `pedal` | 262 | 153 | **89 (58%)** | 1.371 m/s² | out_of_road, 0.529 |
| `junction-1` `accel` | 1746 | 8 | 0 (0%) | 0.308 m/s² | arrived, 0.950 |
| `junction-1` `table` | 1559 | 195 | **0 (0%)** | **0.000 m/s²** | out_of_road, 0.815 |
| `mosque` `pedal` | 2427 | 2387 | **2158 (90%)** | 1.170 m/s² | arrived, 0.950 |
| `mosque` `accel` | 2836 | 1 | 0 (0%) | 0.362 m/s² | arrived, 0.950 |
| `mosque` `table` | 2498 | 85 | **0 (0%)** | **0.000 m/s²** | arrived, 0.950 |

Six things not to re-derive:

- **A pedal table does not fix the speed undershoot, and this was measured rather than
  hoped.** The mean speed barely moves — `junction-1` 4.41 → 4.19 m/s, `mosque` 3.06 → 3.47,
  against a 10 m/s target — because **the bridge is not asking to accelerate**: median
  `accel_cmd` −0.30 m/s², only 159 of 1559 calls positive. The target reaches it correctly
  (`v_cruise_kph` 36.0) and **doubling it makes the bridge brake harder**: at
  `--target-speed-mps 20` the cruise reads 72.0 and the median request falls to **−2.003**,
  with the car nearly stopped. So it is the longitudinal *plan*, upstream of any pedal
  conversion, exactly where the constant-speed `waypoints_from_route` above says it would be.
  That is the model's half.
- **A braking step that ends at zero is not a measurement of the brake**, and it is the one
  fault this sweep has. At 11.2 m/s² a 10 Hz step loses 1.12 m/s, so from 1 m/s the car
  reaches zero *inside* the step and the average reads −3.18 rather than −11.19. Before
  `TRUNCATION_FLOOR_MPS` existed that artefact alone put 60 cells out of order by up to
  0.90 m/s² and made the bottom four rows describe a car that cannot brake. A step is kept
  only when it ends above the floor **or** ends faster than it started — the second being a
  car pulling away from rest, which is the only real measurement the 0 m/s row can hold.
- **The bottom rows are filled, not measured, and the file says which.** 45 of 943 cells,
  none above 1.0 m/s, take the nearest measured speed; `sample_counts` is 0 for exactly
  those. A stationary car cannot be measured braking at all.
- **The crossover is +0.036 pedal, not 0.** That is the throttle that cancels the coast, and
  it is why a request between −0.364 and 0 correctly comes back as a *touch of throttle*. A
  naive "did a deceleration request produce throttle?" count therefore reads 67% against the
  table and means nothing — the honest test is requests **below** the coast, and the direct
  test is whether the chosen pedal delivers what was asked.
- **`--longitudinal` keeps all three.** `pedal` is what the bridge emits and what a CARLA
  consumer gets, so it must stay reproducible; `accel` is the sign-correct fallback where no
  table has been measured; only `table` is a calibration. `--backend stub` answers in pedals
  and carries no `accel_cmd`, so it refuses both of the others by name.
- **`--log-telemetry` now writes `v_ego_mps` and `metadrive_action`** beside the bridge's
  forty reply fields, because none of the above can be answered from the reply alone. The
  reply stays at the top level so an existing grep still works.

`junction-1` `table` ends `out_of_road` at −4.01 m lateral where `accel` arrives at 0.950.
Both steer through identical code, so the difference is where along the route the car is when
the lateral error accumulates. **Not diagnosed.**

### The AV3 checkpoint loads on this card, and takes about a second a pass (2026-08-24)

Stage 9 Phase C.1, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.
`tools/model_probe.py`, `scripts/model-probe.sh`, and an opt-in `model` dependency group.
It answers two questions and nothing else - it does not drive and writes no file.

```bash
uv sync --group sim --group gpu --group model
cd scripts && ./model-probe.sh                                # does it load, what does it cost
cd scripts && ./model-probe.sh junction-1 -- --with-simulator # the same, beside a renderer
```

**The checkpoint answers most of it without torch, a GPU or a download.**
`wingfin-openpilot-temp/assets/models/step_440000_trt_direct_full.ep` is a `pt2` zip, and
`model_probe.read_archive` reads its graph with `zipfile` and `json`: exported by
**`torch 2.8.0+cu128`**, taking `images (1, 5, 6, 3, 288, 512)`, `navigation (1, 20, 7)` and
`ego_state (1, 5, 2)` in **bfloat16**, returning **`(1, 20, 8)`**. Reading it first is also
what makes a failure legible - the probe prints what the file wanted beside what is installed
rather than letting TensorRT complain about a plan file.

That output line is **20 waypoints, not 4, and 8 wide**, and both halves correct the stage-9
plan. `av3_base.N_WAYPOINTS = 4` is a *fallback until `_set_output_shape` runs*, not this
model's count; 20 over `MODEL_HORIZON_S = 2.0` is 0.1 s spacing and is already in the
bridge's prebuilt `AV3_MPC_MENU` (`"4 16 20 32"`), so no code generation and no slow first
tick. 8 is `MODELV2_OUTPUT_WIDTH` - `[x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]` - so the
bridge's `msg["modelv2"]` / `from_predicted` path is reachable rather than the 3-wide
`derive` one `tools/openpilot_policy.py` sends today.

**It deserialises on sm_89, and that was the sharper of the two unknowns.** The archive is
**not weights**: `data/weights/model.pt` is **1,261 bytes** beside a **1,275,435,821-byte
serialized TensorRT engine**, and a TRT engine is built against one SM architecture and one
TensorRT version, so it either opens on this card or does not. It opens - RTX 4050, TensorRT
10.12.0.36, load 9-13 s (mostly reading 1.2 GB off disk).

**And it fits, with 1151 MiB to spare.** Measured on `junction-1` with `rigs/cams.txt`
mounted offscreen - the seven real cameras, 5.42 MB of image a step:

| card, of 6141 MiB | model alone | beside the simulator |
|---|---|---|
| simulator only | - | 2377 MiB |
| + model loaded | 2561 | 4934 |
| + one warm-up pass | 2617 | 4990 |
| **free** | **3524** | **1151** |

**The finding is the forward pass: 947-1002 ms** - medians over 10, 20 and 50 passes across
three runs, best single pass 919. A decision at `--decision-hz 20` has **50 ms**, so this is **20x** over, and a simulated second
will cost about twenty. It does not make a drive *wrong* - `env.step` is the tick, so a slow
policy makes a slow drive and nothing else - but every C.2 timing has to be read against it.

Eleven things not to re-derive:

- **It is not the timing loop.** Ten passes with `cuda.synchronize()` either side average
  **989.9 ms**; ten queued with one synchronize at the end average **999.3 ms**. The sync is
  not being charged for.
- **It is compute-bound and the card is capped at little more than half its rating.** 100%
  utilisation throughout, **34.6-35.1 W against `Current Power Limit` 35 W and
  `Max Power Limit` 60 W**, SM clock **975-1335 MHz against a 3105 MHz maximum**, 87-89 C
  against an 85 C target, and `nvidia-smi -q -d PERFORMANCE` counting 4,339 s of SW power
  capping beside 4,217 s of SW thermal slowdown. Reference point in the same state: a
  4096^3 **bf16 matmul runs at 14.4 TFLOP/s** (fp32 6.4). **The power limit is a machine
  setting and Keith's to change, not this repo's** - and it is not a fix either, since even
  a 2.5x uplift leaves ~400 ms against 50.
- **It opened because it was built portable, deliberately - and that is measured, not
  inferred.** Keith asked what made it open on a card it was not built on, which is the right
  question: an engine at `HardwareCompatibilityLevel.NONE` is locked to one architecture.
  This one is **`AMPERE_PLUS`**, read two independent ways that agree - the torch-tensorrt
  engine state's `HW_COMPATIBLE` field is `'1'`, and
  `trt.Runtime.deserialize_cuda_engine(...).hardware_compatibility_level` says `AMPERE_PLUS`
  - and the `CompilationSettings` pickled into `SERIALIZED_METADATA` show it was **asked
  for** rather than defaulted: `hardware_compatible: True`, beside
  `enabled_precisions: {bf16}`, `immutable_weights: True`, `version_compatible: False` and a
  `workspace_size` of 6 GiB, which is more than this whole card. **So it runs on any
  sm_80-or-newer NVIDIA GPU** - RTX 30/40/50-series and the datacentre Ampere+ parts - and
  **refuses** below that rather than running slowly. Moving this to a bigger machine is
  therefore safe in a way it could not be promised to be beforehand.
- **The portability is documented as costing speed, and that cost is NOT measured here.**
  NVIDIA's own documentation says `AMPERE_PLUS` restricts kernel selection to a portable
  subset. It is a plausible second contributor to the ~1 s pass beside the 35 W cap, and
  quantifying it would need a `NONE`-level rebuild - which cannot happen here, because the
  archive holds a compiled engine and a 1.26 KB weights stub with **no source model in it**.
  `refittable: False` / `immutable_weights: True`, so even the weights cannot be swapped.
  **A native rebuild is the only lever on the forward-pass cost that is not a machine power
  setting, and it belongs to whoever compiled the checkpoint.**
- **The engine's `DEVICE` field is not evidence of the build machine**, and read as such it
  is exactly the kind of wrong that looks like information. It reads
  `0%8%9%0%NVIDIA GeForce RTX 4050 Laptop GPU` - this laptop - because it is re-derived when
  the plan is deserialised. **No build GPU is recorded anywhere in the file.** With
  `AMPERE_PLUS` it stops mattering, which is the point.
- **Read the engine state off the already-loaded module, never by deserialising twice.**
  `_engine_state` takes it from `getattr(module, "<name>_engine").__getstate__()`, which is
  free; `trt.Runtime.deserialize_cuda_engine` is the obvious alternative and would put a
  second ~1.5 GB copy on a card with **1151 MiB spare** under `--with-simulator`. The cheap
  route is trusted because the two were cross-checked once on this engine. What it cannot do
  is separate `AMPERE_PLUS` from `SAME_COMPUTE_CAPABILITY` - the flag alone says only "not
  `NONE`" - so that is all the probe's line claims.
- **The plan is 956,574,460 raw bytes, 777 layers, and asks for 1578 MiB of scratch.** The
  1216 MiB in the archive is base64 (+33%), and `device_memory_size_v2` on top of the weights
  is what the measured ~2.5 GB of card is actually made of.
- **`uv sync --group model` on its own *removes* `sim` and `gpu`.** uv syncs exactly the
  groups named, so that line takes MetaDrive, panda3d and CuPy out and the next `./drive.sh`
  dies on a missing import. The line is `uv sync --group sim --group gpu --group model`, and
  all three coexist in **one** 3.10 environment.
- **numpy did not have to move.** `wing-sim/evaluation/pyproject.toml` pins `numpy==1.26.4`
  beside the identical torch pins; this repo stayed at **2.2.6** and torch 2.8 resolved
  against it. Adopting that pin defensively would have been the one change here able to break
  code that already works.
- **`torch_tensorrt.load` logs two failures before succeeding, and neither is an error.** It
  tries the `.pt2` package loader (*"f must be a buffer or a file ending in .pt2"*), then
  `torch.jit.load` (*"PytorchStreamReader failed locating file constants.pkl"*), then
  `torch.export.load` works. Reading either as the cause of a later problem is a wasted
  afternoon.
- **`torch/_export/serde`'s `ScalarType` is not `torch.ScalarType`.** They disagree from
  index 1: code **13 is `BFLOAT16` in the serde enum and `quint8` in the runtime one**, so
  reading a serialized graph with the runtime table mislabels every tensor in the report and
  raises nothing. `tests/unit/test_model_probe.py` asserts the baked table against torch's
  own copy wherever torch is installed.

**Two things `--group model` is deliberately not.** It is **not** a project dependency -
`uv sync` with no flags stays small, and nothing in `src/osm_scenario/` imports torch. And
the three versions are pinned **exactly**, to what the archive says compiled the engine,
because `>=` lets a resolve pick a stack that cannot open it and the failure then lands
minutes into a run. `test_model_probe` compares the pin against the archive rather than
against this paragraph, and all three of its `pyproject.toml` guards were shown to fail
against a broken file before being kept.

**`tools/model_probe.py` is the one file in `tools/` that is 3.10-only by construction** and
so is **not** parsed with MetaDrive's 3.8 interpreter before being believed: torch 2.8 has no
3.8 wheel, and it does not need one now that MetaDrive itself runs on 3.10. Its absence from
that check is deliberate rather than an oversight. `scripts/model-probe.sh` also skips
`select_gpu` on the plain run - CUDA finds the discrete card by itself, and the PRIME
variables exist for CUDA-GL interop - while `--with-simulator`, which builds a GL context,
goes through `exec_with_gpu` for the reason Phase B recorded.

**One thing left for C.2, stated rather than discovered later.** The 1151 MiB of headroom is
**not** measured against `--image-on-cuda`, which puts a CuPy context and the frame stack on
the same card.

**The camera-order note that used to stand here was wrong, and C.3 replaced the whole
question.** It said the model's `rear_right` is our `cam_back_left` (+125) - reasoned from the
model's camera *names*, which is the thing that note itself warned against. `rigs/cams.txt`
carries `y: 0.0` on all seven cameras, so its yaw column has nothing to be cross-checked
against, and it is self-inconsistent about the sign (`camera_rig.Camera.aim` records this).
`rigs/av3.txt` is built from wing-sim's own spec instead, where the names and the yaws agree
by construction - see the section below. `rigs/cams.txt` is untouched.


### The model is at the wheel now, and six conversions stand between it and the car (2026-08-25)

Stage 9 Phases C.3 and C.2, `docs/implementation-plan/stage-9-a-model-at-the-wheel.md`.
`rigs/av3.txt`, `tools/av3_model.py`, `tools/av3_probe.py` / `scripts/av3-probe.sh`, and
`--camera-rig` / `--model-checkpoint` / `--waypoints` on `drive.py`.

```bash
uv sync --group sim --group gpu --group model
cd scripts && ./av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20   # nothing steers
# then, in two terminals:
uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
cd scripts && METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
    --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --model-checkpoint <the .ep> --sensors imu,route \
    --step-hz 100 --decision-hz 20 --render offscreen
```

**What this replaces is the *trajectory*, not the controller.** Every waypoint the bridge had
ever been sent came from `waypoints_from_route` - the recorded route resampled at the car's
own current speed, which is wing-sim's `route_gt.py` and a controller test by construction.
Phase 0 measured the cost: median `accel_cmd` **-0.30 m/s^2** with 159 of 1559 calls positive,
because a constant-speed path carries no speed *intent* for the longitudinal planner to read.
`--waypoints derive` keeps the old path, so every pre-C.2 measurement stays reproducible.

**Six conversions, and not one of them raises when it is wrong.** A mirrored route or a
swapped camera pair gives a model that loads, runs and returns twenty plausible waypoints.
That is the whole reason `av3_probe` exists and runs before anything steers.

**`rigs/av3.txt` exists because the mapping onto `rigs/cams.txt` could not be made safe, and
the note in this file that tried was wrong.** wing-sim's
`evaluation/configurations/validation_invariants.yml` states its mounts in the **vehicle**
frame the CAD uses - its own header says so: *origin at the rear-axle centre ON THE GROUND, x
forward, y LEFT, yaw CCW-positive, pitch quoted nose-DOWN* (ISO 8855 / REP-103) - and
`sensors/utils.py:transform_from_config` is the conversion it applies to reach CARLA:

    y -> -y     yaw -> -yaw     pitch -> -pitch     roll -> roll (unchanged, measured)
    x -> rear_axle_x + x        z -> ground_z + z

`rigs/av3.txt` is generated by applying exactly that, with the two datum shifts resolved
against MetaDrive's DefaultVehicle (`-REAR_WHEELBASE` = -1.4166, `-CHASSIS_TO_WHEEL_AXIS` =
-0.2, `base_vehicle.py:687`). Height above the **road** is what is preserved, not height above
the roofline - which is what wing-sim itself does when it resolves this rig onto a body it was
not measured on. `rigs/cams.txt` is untouched, because every step-timing figure in this repo
was priced with it.

Ten things not to re-derive:

- **The names and the aims now agree by construction, which `rigs/cams.txt` cannot manage.**
  That file carries `y: 0.0` on all seven cameras, so its yaw column has nothing to be
  cross-checked against, and it names its back pair the opposite of its own yaws
  (`camera_rig.Camera.aim` records this). wing-sim's spec has two independent columns that
  agree - `front_right` at `y -0.468, yaw -53.7` - which is what makes the frame readable at
  all. `test_av3_model` asserts every non-centre camera's name against its resolved aim.
- **The resize is NOT a no-op, and this file used to say it was.** The modifier squashes
  1440x1080 into 512x288 - a 4:3 frame compressed vertically by 1.33x, which is what the model
  was trained on. Rendering 512x288 natively gives a vertical field of view a third narrower
  with nothing raising, so `rigs/av3.txt` renders **512x384** and the preprocess does a real
  squash. Same picture as wing-sim's, 1/8 the pixels.
- **Pitch is now accepted and its sign was measured, not reasoned.** `camera_rig --check-frame`
  probes a pitched `NodePath`: panda3d's P is nose-up positive, which is CARLA's own
  convention, so it passes through untouched. It has to be read against the **car's own
  attitude** - a car under throttle sits nose-up on its suspension, and read against the world
  the same probe returns 9.89 rather than 10.00. **Roll is still refused**: wing-sim measured
  that it does *not* flip where y and yaw both do, and nothing here has checked that against
  MetaDrive.
- **MetaDrive's camera is BGR**, so conversion 1 is the fork's modifier verbatim rather than an
  adaptation. `BaseCamera.get_image` returns `get_rgb_array_cpu()` unchanged for `mode="bgr"`
  and reverses the last axis for `"rgb"` (`base_camera.py:110-113`), and `image_buffer.py:104`
  reads panda3d's BGRA RAM image. `test_av3_model` executes `modifiers.py` as a file and
  asserts pixel equality at 1440x1080, 512x384 and 512x288.
- **The ring holds uint8 and the ego state rides beside it.** At `--decision-hz 20` the stride
  is 10 and the depth 41, so 41 x 6 x 3 x 288 x 512 is **108.8 MB**; preprocessed float32 would
  be 435 MB for a picture that is 8-bit at the buffer. And the engine takes `(1, T, 2)`, not
  `(1, 2)` - `av3_base` buffers `_ego_buf` alongside `_image_buf` - so tiling the current speed
  T times would tell the model the car has been at this speed for two seconds.
- **Conversions 4 and 5 are one mirror.** MetaDrive is y-left / yaw-CCW; the model's frame is
  y-right / yaw-CW. So `y`, `sin(theta)`, `yaw`, `yaw_rate`, `v_y` and curvature negate
  **together** and `x`, `cos(theta)`, `v_x`, `a_x` do not. Half of it right is a car that
  steers smoothly into the oncoming carriageway.
- **Conversion 6 does NOT negate, and only the model could say so.** `waypoints_from_route`
  flips `y` because it starts from MetaDrive's left-positive route sensor; the model's output
  starts in its own training frame, which is already the bridge's. **A drive cannot settle
  this** - measured on `junction-1`, the drive statistic leans the wrong way (27% sign
  agreement, off-path 0.379 m as given against 0.385 m negated) because the model carries a
  standing **+1.6 m rightward bias** on this map, and a bias reads exactly like a mirror.
  `av3_probe --nav-sweep` settles it by holding every other input fixed and replacing the
  navigation with a 30 m arc: right-hand bend **+2.172 m**, left-hand **+1.062 m**, so +y is
  RIGHT and nothing flips.
- **`waypoints` is sent even under `--waypoints modelv2`.** `server.py:_handle_step` reads
  `msg["waypoints"]` first and returns a hard stop on an empty list, *before* it looks at
  `modelv2` at all. An empty list beside a full modelv2 block is a car that never moves.
- **`n_waypoints` goes in `/episode`, not `/act`.** The bridge builds its lateral MPC once, at
  connect, from `init`. 20 is in the prebuilt `AV3_MPC_MENU` ("4 16 20 32"), so there is no
  code-generation pause on the first tick. `RemotePolicy` gained `episode_extra` beside `extra`
  for exactly this: a per-episode field cannot ride on a per-step payload.
- **`--image-on-cuda` is deliberately not on this path.** It is refused above a stride of 1
  unless `--draw-every-step`, which throws away the 4.2x the frame gate is worth - and against
  a 1 s forward pass, Phase B's 3 ms is noise.

**The trajectory half works and the lateral is what ends the drive**, and the statistic to
read it by is the SPEED rather than the sign of `accel_cmd`. Phase 0 diagnosed a car crawling
at 4 m/s under a 36 km/h cruise because `route_gt`'s constant-speed path carried no intent;
measured against the **real bridge** on `junction-1` with `--longitudinal table`:

| | `route_gt` trajectory | the model |
|---|---|---|
| mean `v_ego` | 4.19 m/s | **8.92** (max 13.89, target 10) |
| median `accel_cmd` | -0.30 m/s^2 | -0.504 |
| completion | 0.815 | 0.163, `out_of_road` |

The pace doubles and the median request goes *more* negative, which is not a contradiction: a
car at its target speed correctly asks to hold, and that reads negative. Looking for the sign
to flip was the wrong criterion. What ends the drive is the lateral.

**And what the model does laterally on this map is a domain-gap reading, not a fault to fix
here.** Measured over 40 spread decisions of `junction-1`'s `test` route with the car
replayed: it predicts **16.5 m of travel in 2 s where the car covers 24.1**, and a lateral of
**0.12 m median** where the route bends 27 m at 38 m ahead - a slow, near-straight path with a
+1.6 m rightward bias. Four of its six cameras are 105.4 deg fisheyes standing in as
rectilinear at wing-sim's own unwarped `default_fov` of 70, and the road is a Kuala Lumpur OSM
extract rather than Town10HD. `av3_probe` reports all of it rather than averaging it away.

**`mosque` confirms every conversion independently, and corroborates the mechanism above.**
Conversions 2, 4 and 5 agree over **460** route points at worst 0.0000 m; the nav sweep gives
right **+1.500 m** against left **+0.582** - same sign, smaller response - and its standing
bias is **+1.041 m** against `junction-1`'s +1.617. On that map, with 14 of 23 sampled
decisions on a bend, the *drive-based* statistic recovers the right answer by itself: 72% sign
agreement, off-path 0.396 m as given against 0.598 m negated. So `junction-1`'s drive
statistic fails because the bias is large relative to the model's own lateral, not because a
drive is the wrong instrument in principle.

**Against `--backend stub` the two `--waypoints` modes are identical, and that is the control
rather than the flag failing**: `StubBridge.control` is pure pursuit over `msg["waypoints"]`
and never reads `modelv2`, so it cannot tell them apart. Only the real bridge branches on it
(`server.py:_handle_step`).

**A drive costs a quarter of an hour.** 947-1002 ms a forward pass (Phase C.1), one per
decision, and a full-length `junction-1` route at `--step-hz 100 --decision-hz 20` is 758 of
them. `env.step` is the tick, so this makes a drive slow and never wrong.

**`scripts/av3-probe.sh` and `--model-checkpoint` both need this repo's interpreter**, not the
3.8 checkout venv: torch 2.8 has no 3.8 wheel and does not need one, MetaDrive running on 3.10.
The probe script runs on it directly; `drive.sh` needs `METADRIVE_PYTHON=../.venv/bin/python`,
for Phase B's reason. `tools/av3_model.py` and `tools/av3_probe.py` join `tools/model_probe.py`
as the files that are 3.10-only by construction and so are **not** parsed with the 3.8
interpreter before being believed.

