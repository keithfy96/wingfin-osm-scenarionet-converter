# Wingfin — OpenStreetMap → ScenarioNet converter

Turns a raw OpenStreetMap extract into a lane-level driving map that MetaDrive can
load and drive, with a human review step in the middle.

OSM describes roads as centrelines with tags. A driving simulator needs individual
lanes, with widths, polygons, and an explicit answer to "which lane can you get to
from this lane". Most of that answer is not in the source data — it has to be
inferred from lane counts, turn tags and junction geometry. This repo does the
inference, **shows you every place it had to guess**, lets you decide those cases in
a browser, and only then writes the dataset.

Six stages, each one refusing to run on a hand-off it cannot verify:

```
  ┌── 1 ──────┐  ┌── 2 ─────────┐  ┌── 3 ──────┐  ┌── 4 ──────┐  ┌── 5 ────────┐  ┌── 6 ──────┐
  │  fetch    │→ │ generate-map │→ │  inspect  │→ │  apply-   │→ │  validate-  │→ │  convert  │
  │           │  │              │  │  --view   │  │  review   │  │  map        │  │           │
  │ acquire + │  │ build lanes  │  │  review   │  │ regenerate│  │ is it self- │  │ ScenarioNet
  │ normalize │  │ + connectors │  │ (browser, │  │ with your │  │ consistent? │  │ pickles   │
  │           │  │ + findings   │  │  manual)  │  │ decisions │  │             │  │           │
  └───────────┘  └──────────────┘  └───────────┘  └───────────┘  └─────────────┘  └───────────┘
    source/        lane-model/       review.json    lane-model/    reports/map-     scenarionet/
    normalized/    preliminary       (you export    reviewed       validation       *.pkl
                   .json             it by hand)    .json
```

Everything lives in a **workspace** — one directory per map extract, holding the
source, every intermediate model, the reports, and the browser views. Workspaces are
gitignored.

---

## Setup

```bash
uv sync --dev
uv run osm-scenario --help
```

`workspaces/junction-1` is the working example throughout. `workspaces/mosque` is an
older snapshot kept for the docs.

---

## How to use

### Stage 1 — acquire and normalize

```bash
uv run osm-scenario fetch \
  --osm-file path/to/map.osm \
  --workspace workspaces/junction-1 \
  --driving-side left
```

Creates the workspace. Give it exactly one source — `--osm-file`, `--place "Name"`,
or `--bbox WEST SOUTH EAST NORTH` — and `--driving-side` is required, there is no
default. It copies the OSM into `source/map.osm`, applies the `public-driving-v1`
road-selection policy, builds a directed graph, reprojects it into local metres, and
audits the source data for missing lane counts, broken connectivity, restrictions and
signals.

### Stage 2 — generate the lane model

```bash
uv run osm-scenario generate-map \
  --workspace workspaces/junction-1 \
  --config config/default.yaml
```

Builds every lane, every junction movement, and a list of **findings** — the places
the generator had to infer something or found two sources of truth disagreeing.
Writes `lane-model/preliminary.json` and the read-only audit view
`inspection/stage-2-review-audit.html`.

`--config` is optional everywhere; without it you get built-in defaults, **not**
`config/default.yaml`.

### Stage 3 — review the findings (browser, manual)

```bash
uv run osm-scenario inspect -w workspaces/junction-1 --view review
```

Open `inspection/stage-3-review.html`. Each finding is a question about a specific
lane or connector, and you answer it one of five ways:

| Decision | Meaning | Allowed on a blocker? |
| --- | --- | --- |
| `unresolved` | Not answered yet | **No** |
| `accepted` | The generated proposal stands | Yes |
| `overridden` | You supply a different value | Yes |
| `not_applicable` | The question doesn't apply here | Yes — and this is the only thing that softens a Stage 5 error |
| `ignored` | Parked to stop crowding the queue | **No** — warnings only |

When you're done, the page downloads `review.json`. A browser can't write to disk, so
this file is the hand-carried exchange between the page and the CLI.

### Stage 4 — apply the review

```bash
uv run osm-scenario apply-review \
  -w workspaces/junction-1 \
  --submission workspaces/junction-1/review.json \
  --config config/default.yaml
```

Checks the review still matches the model it was made against — workspace, source
checksum, generation fingerprint, and a per-finding evidence checksum — then
**regenerates** the map with your decisions folded in. It never patches the old model
in place, because changing a lane count renames every lane, connector and finding
downstream of it.

Writes `review/reviewed.osm` (source plus the tags your decisions materialised),
`review/applied-decisions.json` (the audit record), `lane-model/reviewed.json`, a
before/after comparison, and `inspection/stage-4-comparison.html`.

`source/map.osm` is never written — it's acquisition evidence, and Stage 4
re-checksums it afterwards to prove it didn't move.

### Stage 5 — validate

```bash
uv run osm-scenario validate-map -w workspaces/junction-1 --config config/default.yaml
```

Read-only. Asks one question: is the reviewed map geometrically and topologically
self-consistent? Writes `reports/map-validation.{json,md}` and
`inspection/stage-5-validation.html`, and **exits non-zero if it failed** — so a
pipeline can't read "wrote a report" as "the map is fit to convert".

### Stage 6 — convert

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml
```

Writes the ScenarioNet dataset into `<workspace>/scenarionet/`. Without a route this
is **map-only**: MetaDrive can load it and check it, but not drive it.

Two things are drawn by hand in the browser and passed in as files — a route, and
optionally a set of traffic lights. Both are exchange files between the page and the
CLI, exactly like Stage 3's `review.json`: a browser can't write to disk.

### Stage 6, routes — pick where the car drives (browser, manual)

Open `inspection/stage-6-route-builder.html` from the workspace. Click a **start
lane**, click an **end lane**, give the route a name, press add, and repeat for as
many routes as you want. The page draws the drive it would produce as you go. When
you're done it downloads `routes.json` — save it to `<workspace>/routes/`.

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json
```

Each named route becomes one scenario with a synthetic ego car driving it.

A route is needed because `ScenarioEnv` has no start-and-end setting. It navigates by
replaying a recorded car's positions, so the route has to be *in the file* —
`tracks["ego"]["state"]["position"]`. MetaDrive never reads `routes.json` itself.

`routes.json` records which lane model it was drawn on, and `convert` refuses it if
that model has moved since. That's not a fault: re-running Stage 4, or any change that
moves the generation fingerprint, means the lane IDs the file names may no longer mean
the same thing. Re-open the page and pick the routes again.

### Stage 6, lights — place the traffic signals (browser, manual)

Optional. Without it the dataset has no traffic lights at all.

Open `inspection/stage-6-signal-builder.html`. Set the **cycle** — one length shared by
the whole plan — then add a **phase group**, click the lanes it stops, and give it a
`green`, a `yellow` and an `offset` (when its green starts within the cycle). Add a
second group for the crossing arm with an offset that keeps the two apart. A slider
steps the preview through the cycle and recolours every lane at once, and the page
tells you outright which groups end up green together and for how long. It downloads
`signals.json` — save it to `<workspace>/signals/`.

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json \
  --signals workspaces/junction-1/signals/signals.json
```

**Every number in there is chosen by you, because OSM has none.**
`highway=traffic_signals` records that a junction is signalled and nothing else — no
cycle, no split, no offset — so the dataset marks the plan `synthesised` in
`metadata.signals` rather than implying it was surveyed. Timing is deliberately a
`convert`-time file and not a config field: the config checksum feeds the generation
fingerprint, so a phase plan in `config/default.yaml` would invalidate the lane-model
review the next time the map was generated.

A light in a MetaDrive dataset is a **tape** — a colour spelled out for every 0.1 s
step — because MetaDrive has no light controller of its own. The recorded car's stops
are baked into its positions to match, since a replayed car drives through a red
however correct the tape is. `tools/drive.py --lights live` re-drives the same lights
from `metadata.signals` with a fresh offset each episode, so an agent can't learn the
step number instead of the colour; `--lights tape` is the portable default.

One consequence worth knowing before you watch it. A replayed car has no dynamics to
interrupt — it is placed on its recorded positions every step — so it only stops at a
red because the wait is *in those positions*, computed against the tape. Under
`--lights live` the offset moves and the baked waits no longer line up; `drive.py`
warns when you ask for that combination. For training, the answer is
`--agent-policy idm` with `--lights live`, which brakes for the light itself rather
than for a recording of it.

### Stage 6, speed — how fast the recorded car drives

By default the car obeys the road's own speed limit, slowing for corners and picking
up again afterwards. That limit is also a ceiling on the whole drive: however hard the
car is allowed to corner, one obeying a 50 km/h road can never average more than 50
over the route. On `junction-1` the default already runs at 41.5 km/h averaged over
120 routes, against a hard ceiling of 50 — so there is very little left to win inside
the limit.

`--speed-kph` overrides the posted limit, and is the only way past that ceiling:

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json --speed-kph 100
```

Measured on `junction-1`'s 808 m `test` route: **64.8 s** at the posted 50 km/h,
**48.1 s** at `--speed-kph 100` — 60.5 km/h average, twice the drive this repo
produced before the profile was retuned — and both still pass the drivability check
in `tools/check_dataset.py`, which fails any track whose car turns more than 30° in a
single 0.1 s step. It is off by default; nothing changes unless you pass it.

The car still slows for corners at any speed. How hard it may corner is
`LATERAL_ACCEL_MPS2` in `src/osm_scenario/ego_route.py`, and that constant is pinned
to the 30°-per-step check rather than to a comfort figure — degrees per step rise with
speed while the road's shape does not, so that check is what really caps the pace.
Raising it without re-running the route sweep will produce datasets that fail.

### Simulate

MetaDrive runs on Python 3.8 / numpy 1.24; this repo runs 3.10 / numpy 2.2. So the
runners are invoked with MetaDrive's own interpreter, and `scripts/drive.sh` is that
plus the two things a drive always needs — the workspace from `.env`, and the GPU:

```bash
./scripts/drive.sh                          # workspace from .env, 3D window
./scripts/drive.sh junction-1               # override the workspace for this run
./scripts/drive.sh -- --render 2D           # everything after -- goes to drive.py
./scripts/drive.sh -- --line-width-m 0.1    # thinner lane lines, this run only
GPU=integrated ./scripts/drive.sh           # force the built-in graphics
```

It refuses early, with the command to fix it, when the dataset is map-only — without
a recorded car MetaDrive dies on `KeyError: None` deep inside itself, which reads
like a broken dataset and is not one.

**The discrete GPU is used automatically when there is one**, via `__NV_PRIME_RENDER_OFFLOAD`
— nothing to install, and `image_on_cuda` from MetaDrive's install docs is unrelated
(it is an RL image pipeline, not a renderer selector). What it buys is road-surface
detail, because the GL texture ceiling is what caps resolution: measured 16384 px on
this machine's Intel iGPU against 32768 on its RTX 4050, so `mosque` renders at 8 px/m
instead of 4. `drive.py` asks the card rather than assuming, and prints which it got.

**Lane lines are `--line-width-m`, in metres, default 0.15** — about a real road
marking. MetaDrive's own thickness is in *pixels*, so its real width moves with the
size of the map: its 2 px is 0.5 m on `mosque`'s 4096 m terrain square and 0.06 m on
`junction-1`'s 1024 m one, wrong in both directions from opposite ends. One pixel is
the floor, so a big map cannot go as thin as a small one — 0.125 m is `mosque`'s
limit, and the tool says so rather than rounding quietly. `--line-width-m 0` restores
MetaDrive's own. Set `LINE_WIDTH_M` in `.env` to stop typing it.

The underlying commands, for anything the script does not cover:

```bash
# load-and-check, no simulator needed
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/check_dataset.py workspaces/junction-1/scenarionet

# drive it by hand, without the script
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
  /home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/drive.py workspaces/junction-1/scenarionet --render 3D

# adding manual line width 0.15 is the default setting
  __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
    /home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
    tools/drive.py workspaces/mosque/scenarionet --render 3D --line-width-m 0.10
```

Use `tools/drive.py`, not `python -m scenarionet.sim`. Both load the dataset
correctly, but in 3D `sim.py` shows a broken map — MetaDrive's terrain defaults are
sized for short Waymo clips, not for a road network — and none of the settings that
fix it are reachable from that entry point. `drive.py` measures each scenario and
picks a terrain size and texture resolution that fit. `--render` also accepts
`none`, `offscreen`, `2D` and `semantic`.

### Drive it yourself

`--agent-policy manual` hands the wheel to the keyboard instead of to the tape. From
inside `scripts/`:

```bash
./drive.sh junction-1 -- --render 3D --agent-policy manual --max-lateral-dist 30
```

`--render 3D` is required and anything else is refused early. Without a window
MetaDrive falls back to reading the keyboard through a blank pygame window, and the
failure would otherwise be a window that never appears.

**Click the window before driving.** panda3d reads the keyboard through whichever window
has focus, so keys pressed anywhere else reach nothing — and because the ego spawns at the
*recorded* speed rather than at a standstill (**50 km/h** on `junction-1`), a car nobody is
steering drives off on its own and looks exactly like a car being steered badly. The
on-screen `steering` and `throttle` are what the car is executing: press `w` and watch
`throttle` move. If they stay at 0, the window is not getting the keys. `p` pauses if you
would rather not start at speed.

**Press `h` in the window for MetaDrive's own key list.** `w` `s` `a` `d` drive —
**the arrow keys are not bound** — `q` is the driving view and `b` the top-down one,
and the keyboard stops steering while the camera is top-down, which is MetaDrive's
behaviour and not a fault. `r` resets the episode, `p` pauses, `f` switches between
real-time and unlimited FPS, `t` hands over to MetaDrive's built-in expert, and `esc`
quits — skipping the end-of-run report, so let the episode finish if you want it.

**`--max-lateral-dist` is what makes the mode usable.** MetaDrive ends the episode
4 m sideways of the *recorded* route, so a deliberate wrong turn ends the run in about
a second. Measured on `junction-1` with nobody steering at all: the coasting car
crossed it at 4.28 m after 189 of 370 steps. It defaults to MetaDrive's own 4 m, so
nothing changes unless you pass it.

The route is still the one in `routes.json`. A recorded track is the navigation
reference line and the destination whatever drives the car, and the lateral limit
above is measured from it — so `convert --routes` is as necessary here as for a
replay. Not arriving does not set the exit status in this mode: the driver is the
variable, so a kerb or a wrong turn is printed but does not make the run `FAILED`.

### Drive it with your own code

The ego is driven by `env.step([steering, throttle_brake])` — two floats in [-1, 1] — and
that is the whole of the connection. It is the same socket the keyboard uses, because
MetaDrive's manual-control policy is a subclass of the one that reads the action argument.

`tools/agent_env.py` builds an environment with the terrain settings an OSM-sized map needs,
and `examples/drive_with_a_policy.py` is the loop. Run it with MetaDrive's interpreter, from
the repo root:

```bash
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  examples/drive_with_a_policy.py workspaces/junction-1/scenarionet
```

It ships driving with MetaDrive's own IDM, and the only line a model replaces is
`policy = IdmDriver(env)`. A policy is anything callable taking the observation and
returning `[steering, throttle]`; nothing registers it and MetaDrive never learns it exists.

The IDM baseline is there to prove the wiring rather than to drive well: `drive.sh -- --agent-policy
idm` runs the same class *inside* the simulator, where the action is ignored, so the two must
produce the same drive — measured, and they agree exactly on both extracts.

**Recording, for imitation learning.** `--record out.npz` on either the example or `drive.sh`
writes `(observation, executed action)` pairs, so a drive you steer yourself and a drive your
model steers come out the same shape:

```bash
./drive.sh junction-1 -- --render 3D --agent-policy manual --max-lateral-dist 30 \
  --record workspaces/junction-1/demos/keith-1.npz
```

The path is relative to the repo root, not to `scripts/` — `drive.sh` changes there before it
runs anything — and the directory is created if it does not exist. Inside the workspace is the
right home for it: `workspaces/` is gitignored, so a demonstration set does not land in a commit.

It reads the action the car *executed*, not the one it was asked for. Under
`--agent-policy replay` there is no action at all — that policy places the car directly — so
every recorded action is `[0, 0]` and the run says so.

### What a model can actually see

Run the survey before choosing what your model takes as input. It drives, then reports every
output MetaDrive can produce — shapes, ranges, and whether each one *moved* over the drive —
and writes samples you can look at:

```bash
./sensor-survey.sh junction-1
./sensor-survey.sh junction-1 -- --policy straight     # a constant action instead of the IDM
```

Samples land in `workspaces/<workspace>/sensor-survey/`: a PNG per camera, the point cloud as
`.npy`, the observation as `.npy`, and `track.csv` with position, latitude/longitude, IMU and
the action, one row per step.

All four modalities are there. Measured on `junction-1`:

| | how | what comes back |
|---|---|---|
| **camera** | `RGBCamera`, `DepthCamera`, `SemanticCamera` | `(180, 320, 3)` and `(180, 320, 1)`, floats in [0, 1] |
| **lidar, 3-D** | `PointCloudLidar` | `(64, 200, 3)` — x, y, z per ray, in the car's own frame |
| **lidar, ray** | the 120-laser ring in the observation | **constant 1.0 today** — see below |
| **IMU** | assembled from the physics body | 3-D velocity and angular velocity, roll, pitch, heading, acceleration |
| **GPS** | the dataset's own projection | latitude and longitude, exact |

Two of those need saying plainly.

**The 120-laser lidar block in the observation is blind, and it is not misconfigured.** That
sensor scans the *dynamic* world, and our scenarios hold exactly one car — the ego. All 120
values sit at 1.0 for the whole drive and will start carrying something when traffic does. What
sees the road today is the 12-laser side detector at `[0:12]`, and what carries the route is the
navigation block at `[19:41]`. Of the 161 numbers, **39 move**.

**GPS is exact rather than approximate.** The dataset carries the projection Stage 1 chose
(`metadata.coordinate_system_wkt`) and MetaDrive records the shift it applied when it re-centred
the scenario, so world metres invert back to WGS 84 with nothing estimated. Checked against
`pyproj` over ±900 m: **0.000000 m** of disagreement, and all 291 points of the drive land inside
the bounds of `source/map.osm`.

Every column and index is written up in `docs/scenario-datapoints.md`.

### A rig of several cameras

The survey above samples **one** forward camera at MetaDrive's default mount. A real vehicle
carries several, and the spec for a rig is a file — CARLA's sensor-spec shape, which is what the
rigs around here are already written in:

```bash
./sensor-survey.sh junction-1 -- --camera-rig ~/Desktop/work/wingfin/data/cams.txt
./sensor-survey.sh junction-1 -- --camera-rig ~/Desktop/work/wingfin/data/cams.txt \
    --rig-record --steps 60         # every step, to sensor-survey/rig/<camera>.npy

python tools/camera_rig.py ~/Desktop/work/wingfin/data/cams.txt   # resolve it, no engine
```

`--camera-rig` writes one PNG per view; `--rig-record` writes `(steps, H, W, 3)` uint8 per
camera, row-aligned with `track.csv` and `observation.npy`. Three things worth knowing before
you point a model at it:

- **The frame is converted, not copied.** CARLA is x-forward with `yaw` positive to the right;
  MetaDrive is y-forward with heading positive to the **left**. So the mount is an x/y swap and
  the aim is a sign flip, and the tool prints where each camera *actually* points rather than
  trusting its name — `cams.txt` disagrees with itself about the sign, and two of its four side
  cameras are named backwards whichever reading you take.
- **The cameras are mounted on the ego, not one camera re-aimed per view.** Six mounted cameras
  cost 20.4 ms a step against 77.3 ms for MetaDrive's own borrow-and-re-aim example.
- **A rig replaces the survey's own four sensors rather than joining them.** Past seven image
  buffers panda3d's reset fails intermittently — measured, the rig alone survives 5 runs of 5
  and the rig plus the point cloud 1 of 5 — so a rig over the limit is refused outright. Nothing
  is lost: `--policy idm` is deterministic, so a plain run gives the other four on the same drive.

`docs/scenario-datapoints.md` §10 has the conversion table, the measurements, and what was ruled
out along the way.

### Look at the point cloud

```bash
./view-point-cloud.sh junction-1
./view-point-cloud.sh mosque -- --colour distance-ahead
./view-point-cloud.sh junction-1 -- --max-range 0    # keep the far-plane misses too
```

Drag to orbit, scroll to zoom, `h` in the window for the rest. Open3D is pulled into a throwaway
overlay by `uv run --with open3d` rather than added to this repo's dependencies — it is a 450 MB
wheel used to look at a file, not to build one — and it must not go into MetaDrive's venv either,
which is a reference checkout. The script also sends the window through XWayland, because this
desktop is Wayland and Open3D's GLFW picks the Wayland backend and then fails to initialise GLEW.

Two things about the file itself, both of which make the naive three-line Open3D snippet show you
nothing useful:

- **It is `(64, 200, 3)`, not `(N, 3)`** — 200 rays over 64 channels, so it needs reshaping.
- **A ray that hits nothing lands on the depth buffer's far plane.** Raw extent runs to
  **18476 m** on `junction-1`; only **9000 of 12800** rays (70.3%) are inside the sensor's own
  200 m. Left in, the viewer autoscales to the sky and the road is a dot. They are dropped by
  default.

And one thing that is a fact about the scenario rather than the viewer: **every return is the
ground.** Both extracts hit on 45 of 64 channels, all within **0.1 m** of z = −2 m, because the
scenarios hold one car and MetaDrive's terrain carries no buildings. So colouring by height —
the usual choice for a lidar — is flat, and the default here is range instead. Like the blind
120-laser ring above, this fills in when there is traffic to hit.

### Host a model and let it drive

Your model does not have to run where MetaDrive runs — MetaDrive's venv is Python 3.8.20 with no
torch. Put the model behind a socket instead. Edit `act()` in `examples/policy_server.py`, which
imports nothing but the standard library, and run it on whatever interpreter your model needs:

```bash
python examples/policy_server.py --port 8642
```

Then drive against it, either headless or watching in 3D:

```bash
./drive.sh junction-1 -- --render 3D --agent-policy remote \
  --policy-url http://127.0.0.1:8642 --sensors imu,gps,camera
```

`--sensors` takes any of `imu, gps, camera, depth, semantic, point-cloud`; the observation is
always sent. In a training loop use the example instead, which needs no window at all:

```bash
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  examples/drive_with_a_policy.py workspaces/junction-1/scenarionet \
  --policy-url http://127.0.0.1:8642 --sensors imu,gps
```

**Prove the wire before you blame a model.** Serve back the actions a local IDM drive recorded,
and the remote drive must reproduce it exactly:

```bash
python examples/policy_server.py --port 8642 --backend replay --replay-from drive.npz
```

Measured: 291 steps and route completion 0.774126 both ways, with the recorded observations and
actions **bit-identical**, and every observation the server received identical to the one the car
had. `--backend constant --steering 1.0` is the other half of the check — the car leaves the road
in 13 steps, and to the opposite side from `--steering -1.0`.

**What it costs per step**, against `env.step`'s own ~1 ms:

| `--render` | `--sensors` | sent per step | round trip |
|---|---|---|---|
| `none` | — | 0.9 KB | **0.88 ms** |
| `none` | `imu,gps` | 1.4 KB | 0.98 ms |
| `3D` | `camera,imu,gps` | 901 KB | 15.0 ms |
| `offscreen` | — | 3600 KB | 29.4 ms |
| `offscreen` | everything | 5001 KB | 49.0 ms |

Two things to know from that table. **`--render offscreen` makes the observation itself a stack
of camera frames**, whether or not you asked for a sensor — that is how MetaDrive keeps a camera
alive at all — so it costs 3.6 MB a step for an image nobody wanted. `--render none` and
`--render 3D` both leave the observation at 161 floats, which is why the 3D row with a camera on
is cheaper than the offscreen row with nothing. And **if you ever see ~40 ms a step, that is not
a slow model**: it is Nagle's algorithm meeting delayed ACK because one end of the socket lost
`TCP_NODELAY`. Both ends set it; a stock `http.server` does not.

The client refuses an action MetaDrive would have swallowed. `action_check` is off by default and
`EnvInputPolicy` simply clips, so an output in [0, 1] silently loses the ability to brake and
`NaN` is not clipped at all. Those are raised instead.

---

## How it works

### The workspace is the unit of state

```
<workspace>/
  source/
    map.osm                 the acquired OSM — written once, never again
    manifest.json           the ledger: stage_1b, stage_2, stage_4, stage_5, stage_6
  normalized/
    road-network.graphml    WGS84 (Stage 1A)
    road-network-local.*    projected into local metres (Stage 1B)
  lane-model/
    preliminary.json        Stage 2 output
    reviewed.json           Stage 4 output
  review/
    reviewed.osm            source + the tags your decisions materialised
    applied-decisions.json  what was applied, and the whole submission
  reports/                  every stage's JSON + Markdown report
  inspection/               one self-contained HTML page per stage
  routes/routes.json        hand-downloaded from the route builder
  scenarionet/              dataset_summary.pkl, dataset_mapping.pkl, sd_*.pkl
```

Each stage records its result and a sha256 into `source/manifest.json`, and the next
stage refuses to start if what it signed has moved since:

```
source/map.osm sha ──► preliminary.json + a generation fingerprint
                            │
                            ├─ Stage 3 binds review.json to that fingerprint,
                            │  and each decision to its own evidence checksum
                            ▼
     Stage 4 refuses a review whose fingerprint or evidence drifted
                            ├─ signs lane-model/reviewed.json into manifest.stage_4
                            ▼
     Stage 5 refuses a model whose sha moved since Stage 4 signed it
                            ├─ records pass/fail into manifest.stage_5
                            ▼
     Stage 6 refuses a model Stage 5 did not pass
```

Nothing downstream re-decides anything upstream. Stage 4 owns "what did the reviewer
conclude"; Stage 5 owns only "is the result self-consistent". Stage 5 cannot answer a
finding, and Stage 4 cannot declare a map valid.

### The two things Stage 2 produces

Everything is defined in `src/osm_scenario/lane_model.py`, all Pydantic models with
`extra="forbid"`.

A **lane** (`LaneFeature`) is one lane of one road segment: its source way IDs, its
index and direction, road class, width, speed limit, a centreline, a polygon, left
and right boundaries, its neighbours, its turn permissions, and its entry and exit
links.

A **connector** (`ConnectorFeature`) is one junction movement: from this lane, to
that lane, through this node, with a signed turn angle, a movement class
(`reverse` / `left` / `slight_left` / `through` / `slight_right` / `right`) and a
status of `active`, `forbidden`, or `review_required`.

The distinction that matters most:

- A road **carrying on through a node** is a *continuation*. No connector is created;
  the lane's `exit_lanes` simply names the next **lane**.
- A road **turning at a junction** is a *connector*. `exit_lanes` names the
  **connector**, and only when it's active.

So `entry_lanes` and `exit_lanes` hold a mix of two kinds of ID. In `junction-1`'s
reviewed model, 257 distinct lane IDs and 83 distinct connector IDs appear in those
lists — the 83 being exactly the active connectors, since forbidden and
review-required ones are never wired in. Any lookup that assumes one kind of ID fails
silently on the other, and Stage 6 has to resolve the connector IDs back to lanes
because ScenarioNet only understands lane IDs.

### Conventions that will bite you

**Lane indices run centre-out.** `idx0` hugs the centreline; `idx(n−1)` is kerbside.
`driving_side` is `left` for junction-1:

```
  way 776370584, 3 lanes, direction of travel ──────────────►

 ═══════════════════════════════════════════════════════ KERB ══
        idx2/3   nearside  (kerbside)
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
        idx1/3   middle
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
        idx0/3   offside   (against the centreline)
 ══════════════════════════════════════════════ CENTRELINE ══
```

- **`signed_turn_angle` is CCW-positive** — `+` is a left turn, `−` is a right turn.
- **`direction: forward|backward`** is relative to OSM way node order, *not* to
  oncoming-ness. A "backward" lane is not necessarily oncoming traffic.
- **OSM connectivity is via shared nodes.** Relations only carry turn restrictions —
  a missing connection is never a missing relation.

### What Stage 2 actually does

`build_lane_model` in `src/osm_scenario/generation.py` is pure — no filesystem — which
is why Stage 4 can re-run the identical function with your decisions applied. Roughly:

1. **Lanes, one graph edge at a time.** Lane count from tags or inference, width and
   speed from tags or config defaults, then geometry: offset the road centreline
   sideways per lane, buffer it to a polygon, derive left/right boundaries. IDs are
   content-addressed hashes, so the same input always gives the same ID.
2. **Way-level findings merged**, so a road split into five segments asks its lane-count
   question once rather than five times.
3. **Junctions.** For each node, group the outgoing lanes by carriageway.
4. **Allocate whole approaches before deciding individual lanes.** Which lane peels off
   at a junction is a question about the approach as a whole, not about each lane
   separately — so a diverge (`_balanced_approach_assignment`) and a merge
   (`_balanced_merge_assignment`) are resolved first. This is what stops a middle lane
   being fed by nothing while two approaches pile onto the same outer lane.
5. **Per-lane movements.** Classify continuation vs turn, pick the target lane, compute
   the turn angle, build a Bezier curve through the junction node, apply `turn:lanes`
   permissions, mark anything genuinely ambiguous.
6. **Turn restrictions** from OSM relations, including via-way chains.
7. **Emit connectors**, wire the active ones into the lanes, and raise a blocker for
   each one still marked `review_required`.
8. **Merge tapers, traffic signals, stop lines**, then package it all up with a
   generation fingerprint.

`src/osm_scenario/topology.py` holds the geometry and classification helpers this
leans on: `signed_turn_angle`, `classify_movement`, `movement_side`,
`side_lane_index`, `connector_curve`, and the turn-restriction resolvers.

### The standing rule: surveyed tags outrank inferred angles

`turn:lanes` is surveyed evidence of which movements are *permitted*. The movement
class is *inferred* by binning a turn angle against threshold constants. Where the two
disagree, the tag must never be the reason a lane loses its only exit — that would cut
the drivable network on the strength of a magic number. The generator keeps the
movement and raises a finding instead.

The corollary, which matters when you're tempted to make a warning go away: **never
fix a tag-versus-geometry conflict by making the finding stop being raised.** Fix the
mapping and keep the question.

### Findings, and what makes one a blocker

Nine rules can raise a finding: `lane_count_inference`, `lane_width_default`,
`speed_default`, `turn_permission_geometry_conflict`,
`lane_transition_count_mismatch`, `ambiguous_connector`, `signal_lane_association`,
`inferred_stop_line`, `restriction_effect_review`. Each is either a `warning` or a
`blocker`; blockers gate Stage 4.

Two of them can be answered by writing an OSM tag back (`lanes` and `turn:lanes`), so
the next generation run reads your decision straight out of the source. The rest stay
live as overrides in `applied-decisions.json`. Four rules that *would* need a tag are
**refused by name** rather than half-applied — a review that appears to have been
applied but wasn't is worse than a run that stops.

One thing that trips people up: **a reappearing finding is not open work.** Accepting
an inference leaves the map unchanged, so the same question comes back on every
regeneration. Only Stage 4's before/after comparison can tell a re-asked question
from a genuinely unresolved one, which is why Stage 5 reads its `findings_still_open`
field rather than re-deriving it.

### What Stage 5 checks

Geometry (non-finite, empty, self-intersecting, centreline outside its polygon),
references (dangling or non-reciprocal entry/exit and neighbour links), connectors
(endpoints that don't actually meet the lanes they join, active-but-unreachable,
inactive-but-drivable), restrictions, signals, and network boundary facts.

Two calibrations worth knowing about, both measured against `junction-1` rather than
picked:

- A lane's centreline lies *on* its own polygon boundary by construction, so the
  containment test needs a 1e-9 m epsilon — otherwise every lane in the map fails.
- Short connectors degenerate to a stub whose far end stays on the incoming lane, so
  the "does this connector meet its lane" threshold is 0.05 m, deliberately coupled to
  the same threshold in `connector_curve`. 32 of junction-1's 83 active connectors are
  stubs; an exact-endpoint assertion would fail every merge in the map.

Issues on a feature you marked `not_applicable` in Stage 3 are reported as **warnings**
naming the finding that dispositioned them, rather than errors. Stage 5 re-derives
conditions from the model, so it will happily re-detect something a human already
ruled out — re-raising it as an error would make the review pointless. There is no
suppression list; the only place to disposition an issue is Stage 3.

A lane that stops dead is usually just the edge of the extract. All 39 of junction-1's
no-entry/no-exit lanes end at a node that terminates every source way containing them.
A lane stopping at a node the road runs *through* is the real defect — reporting the
first as an error would bury the second under 39 false alarms, so extract-edge lanes
are reported as boundary facts.

### What Stage 6 writes

`map_features` is a flat dict: one `LANE_SURFACE_STREET` per lane (centreline
polyline, polygon, speed, width, entry/exit, neighbours) plus one feature per lane
boundary. Connector IDs in the entry/exit lists are resolved to the lane on the other
side.

A boundary is written `ROAD_LINE_BROKEN_SINGLE_WHITE` — a dashed divider — exactly
where the model records a lane change across it, and `ROAD_EDGE_BOUNDARY` everywhere
else, so a kerb and a centreline can never come out dashed. This is not decoration:
MetaDrive names the line's collision body after its type, so crossing a solid line
sets `on_white_continuous_line` and crossing a broken one sets `on_broken_line`, and
`ScenarioEnv._is_out_of_road` reads the first. The style is derived from lane-change
permissions rather than surveyed — OSM carries no marking data — and
`metadata.lane_markings.source` says so. Where two lanes each carry their own copy of
the divider between them and the copies are the same line to within 5 cm, only one is
written; two copies would dash out of phase and render as a solid line. On
`junction-1`: 285 lanes, 93 dividers, 392 edges, 85 second copies merged.

With `--routes`, each route is re-planned in Python (Dijkstra over the lane graph,
splicing connector centrelines for junction hops), resampled at 10 Hz, and written as
a synthetic ego car at `tracks["ego"]["state"]["position"]`. **MetaDrive never reads
`routes.json`** — it reads the pickles, and the route *is* those positions.

The route builder page previews the same geometry in the browser; Python re-derives
it. The two agree to within 3.5 m over 1.1 km across 40 real routes, and both sides
are deliberately covered by the same test cases (`web/test/route/geometry.test.ts`
and `tests/unit/test_ego_route.py`) — if they ever diverge, the page would offer
drives the converter refuses.

Three pickles land in `scenarionet/`: one `sd_*.pkl` per scenario, plus
`dataset_summary.pkl` and `dataset_mapping.pkl`. They're written through a custom
pickler because numpy 2 stamps a reference to `numpy._core` into the stream, which
numpy 1.24 on Python 3.8 — the MetaDrive side — cannot resolve. Anything that changes
how arrays are written must keep the stream free of version-specific module names.
MetaDrive is deliberately not a dependency of this package; the schema is pinned by a
test that loads MetaDrive's real `ScenarioDescription` when the checkout is present.

### The browser pages

Every inspection page is a single self-contained HTML file with its data inlined as a
JSON payload — no server, no build step at view time. The two interactive ones (the
Stage 3 reviewer and the Stage 6 route builder) host TypeScript clients from `web/`,
compiled by esbuild into `src/osm_scenario/assets/*.js` and **committed**, so an
installed CLI never needs Node.

---

## Configuration

`config/default.yaml`, validated by `src/osm_scenario/config.py`:

| Key | Default | Controls |
| --- | --- | --- |
| `driving_side` | `null` | `left` / `right` |
| `coordinate_origin` | `null` | Local projection origin |
| `lane_width_defaults.vehicle` | `3.5` | Lane width when OSM has none |
| `default_speed_kph` | `50.0` | Fallback speed |
| `speed_defaults_kph` | per `highway` tag | motorway 110, service 30, … |
| `tag_inference.infer_missing_lane_count` | `true` | Infer lane counts when untagged |
| `lane_selection.side_movement_min_degrees` | `10.0` | When a turn counts as a side movement |
| `lane_selection.sharp_movement_review_degrees` | `130.0` | When a sharp movement gets flagged |
| `lane_geometry.merge_taper_length_m` | `30.0` | Merge taper geometry |

Unknown keys are rejected. Every command that takes `--config` falls back to built-in
defaults when the flag is absent — `config/default.yaml` is not loaded automatically.

---

## Repo layout

```
src/osm_scenario/
  cli.py              the six commands
  acquisition.py      Stage 1A       normalization.py     Stage 1B
  osm_source.py       raw OSM XML    stage1b_data_audit.py
  generation.py       Stage 2 — the generator
  lane_model.py       the data model
  topology.py         geometry, movement classification, restrictions
  review.py           Stage 3        apply_review.py      Stage 4
  validation.py       Stage 5        conversion.py        Stage 6
  ego_route.py        route planning + the synthetic ego car
  inspection.py, comparison_view.py, validation_view.py,
  reachability_view.py, route_builder_view.py     the HTML views
  assets/             committed compiled browser clients
web/                  TypeScript sources for those clients
tools/                drive.py, check_dataset.py, signal_control.py,
                      agent_env.py, sensor_survey.py, camera_rig.py, policy_client.py,
                      geodesy.py   (all run under MetaDrive's venv)
                      view_point_cloud.py   (this repo's venv + open3d)
examples/             drive_with_a_policy.py — the loop your own policy goes in
                      policy_server.py — the model's side, stdlib only, any interpreter
config/default.yaml
docs/policies/        road selection, Stage 2 algorithms, finding reference
docs/mapping-algo-changes/   a dated record of every corrected mapping mistake
guide/project-guide.md, "stage 3,4,5 guide.md"
```

---

## Development

```bash
uv run pytest
uv run ruff check
cd web && npm test        # the browser clients
```

`ruff format --check` fails on some pre-existing files and is not a gate.

`uv run pytest` tells you the code is correct; it does **not** tell you the dataset
loads. Both reference checkouts run Python 3.8 / numpy 1.24 while this repo runs
3.10 / numpy 2.2, so the interpreter the tests use is exactly the one where a version
fault is invisible. Check it from the other side with `tools/check_dataset.py`.

### Reference checkouts

Neither is a dependency; both are read-only references for "what does MetaDrive
actually do with this field".

- `/home/keith/Desktop/work/wingfin/metadrive/` — MetaDrive 0.4.3, the format this
  targets
- `/home/keith/Desktop/work/wingfin/scenarionet/` — ScenarioNet, plus the Waymo /
  nuPlan / nuScenes / Argoverse converters worth comparing our output against

`tests/unit/test_conversion.py` loads MetaDrive's real `ScenarioDescription` from the
first path and is `skipif`-marked on the directory being absent — so a moved or
renamed checkout **silently drops the schema gate** rather than failing.

See [`guide/project-guide.md`](guide/project-guide.md) for artifact ownership,
[`stage 3,4,5 guide.md`](stage%203,4,5%20guide.md) for the review stages in depth, and
[`docs/policies/`](docs/policies/) for the road-selection policy and the Stage 2
algorithms.
