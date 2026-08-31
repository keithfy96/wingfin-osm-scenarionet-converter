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
    source/        lane-model/       review.json    lane-model/    reports/map-     scenarionet-
    normalized/    preliminary       (you export    reviewed       validation       10hz/*.pkl
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

Three things are drawn by hand in the browser and passed in as files — a route, and
optionally a set of traffic lights and a set of actors. All three are exchange files
between the page and the CLI, exactly like Stage 3's `review.json`: a browser can't
write to disk.

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

A light in a MetaDrive dataset is a **tape** — a colour spelled out for every step, 0.1 s
of them unless `--step-hz` says otherwise — because MetaDrive has no light controller of
its own. The recorded car's stops
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

### Stage 6, actors — put people on the map (browser, manual)

Optional. Without it the dataset holds exactly one moving object, the recorded car.

Open `inspection/stage-6-actor-builder.html`. Pick a **kind** — pedestrian, cyclist,
cone or barrier — then **click the map** where the actor goes: each click adds a
corner, in the order it walks them, and two points is enough for a straight crossing.
Set its speed, and a start delay if it should meet the traffic later. Tick **paint a
crossing** where it should leave a zebra. Name it, press add, repeat. It downloads
`actors.json` — save it to `<workspace>/actors/`.

You click the map here rather than picking a lane, unlike the other two Stage 6 pages,
for the reason the whole thing is drawn by hand: an actor walks where no lane is.

**Or start from a whole scene.** The **Randomise** panel takes a density per kind and
places them for you:

| per km of road | low | medium | dense |
|---|---|---|---|
| pedestrian | 1 | 4 | 10 |
| cyclist | 1 | 3 | 8 |
| cone | 2 | 8 | 20 |
| barrier | 1 | 3 | 8 |

**Load your `routes.json` into it first.** The route is drawn in blue and every actor
goes on or beside a lane it actually drives, so the car meets all of them — walkers
standing at the kerb and stepping into the road as it arrives, riders on the road ahead
of it, cones and barriers at the kerb. Without a route they are scattered over the whole
map, capped at 150, and most will be nowhere near the drive. That is not hypothetical:
a pedestrian placed by hand on `junction-1` sat 137 m off the route the entire time it
existed, and nothing but a measurement of the pickle said so.

The seed makes a scene repeatable and the timing is an **estimate** — the page works out
when the car arrives from the distance along the route and the average speed in the box,
which is why a walker waits either side of it rather than stepping out on a stopwatch.
Everything it places is an ordinary entry: select it, remove it, or edit the file.
Pressing Generate again replaces what it placed and leaves anything you drew alone.

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json \
  --actors workspaces/junction-1/actors/actors.json
```

**`--actors` needs `--routes`.** Without a route the dataset is one frame long and
holds no tracks, so there would be nowhere for an actor to walk; `convert` refuses the
combination rather than painting the crossings and dropping every walker.

What an entry looks like — the page writes all of this, and it is here so you can read
one back or edit it by hand:

```jsonc
{
  "name": "crossing-mid",        // becomes the track key; anything but "ego"
  "kind": "pedestrian",          // or cyclist, cone, barrier
  "path": [[lat, lon], ...],     // [lat, lon], the order every page here speaks
  "speed_mps": 1.3,              // constant; 1.3 is an unhurried adult, 5.0 a cyclist
  "start_delay_s": 0.0,          // it is not in the scene until then
  "waits": [                     // stand still partway across
    {"at_m": 9.0, "seconds": 20.0}
  ],
  "crossing_width_m": 4.0        // omit for no zebra at all
}
```

A cone or a barrier does not move, so it takes `"position": [lat, lon]` and an
optional `"heading_rad"` instead of a path, and none of the walking fields.

**It is like the lights, not like `--traffic live`.** Actors are baked into the
pickles at convert time, so they are *in the dataset*: a stock ScenarioNet consumer
sees them, every rate you convert gets them, and **no drive-time flag turns them on**.
There is no `--actors` on `drive.py` and there does not need to be — MetaDrive's own
`ScenarioTrafficManager` is already registered in every drive and spawns them straight
out of `tracks`. The other cars from `--traffic live` are the opposite: not in the
dataset, and switched on per run.

**The ego brakes for them, and so does the traffic.** They are solid to the lidar and
to the physics. Measured on `mosque` with a pedestrian standing on the ego's own line
40 m along its route, under `--agent-policy idm`: the ego held it as its front object
for 180 of 200 steps and came down from 11.0 m/s to 0.04 m/s, stopping 10.2 m short.
Hitting one registers as `crash_human`.

**They are a tape, not a crowd.** An actor walks the path you drew at the speed you
set, whatever else is happening — it will not wait for a car, and a car will not wave
it across. `waits` and `start_delay_s` are the only controls over its timing, because
MetaDrive has no pedestrian policy to give it more.

**A crossing is paint.** Ticking the box emits a `CROSSWALK` polygon over the part of
the path that lies on the carriageway: stripes on the road surface and a label for the
semantic camera, and nothing else. Nothing routes a pedestrian onto one and no policy
yields at one. Leave it off for a walker on the pavement, or for a jaywalker. **The
source carries no surveyed crossing anywhere on either map** — zero `highway=crossing`
nodes across `junction-1` and `mosque`, and four footways between them — which is the
same reason the paths themselves are drawn rather than derived. There is nothing to
convert.

`actors.json` records which lane model it was drawn on and `convert` refuses one drawn
on a different map, exactly as `routes.json` and `signals.json` do. It matters more
here: a stale route names lane IDs that can be found missing, but a stale actor names
nothing at all, so it would simply put a pedestrian somewhere else — quite possibly in
a live carriageway — with nothing downstream noticing.

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
0.1 s window. It is off by default; nothing changes unless you pass it.

The car still slows for corners at any speed. How hard it may corner is
`LATERAL_ACCEL_MPS2` in `src/osm_scenario/ego_route.py`, and that constant is pinned
to the 30°-per-window check rather than to a comfort figure — degrees per second rise
with speed while the road's shape does not, so that check is what really caps the pace.
Raising it without re-running the route sweep will produce datasets that fail.

### Stage 6, rate — how often the drive is written down

MetaDrive's `env.step` advances 0.1 s by default, so 10 Hz is the control rate, the
sensor rate and the recording rate all at once. `--step-hz` changes how densely the
drive is written down, and nothing else — the same route, at the same speeds, with ten
times the samples:

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json --step-hz 100
```

Measured on `junction-1`'s `test` route: 370 samples over 36.9 s at the default,
3,695 over the same 36.9 s at `--step-hz 100`, with the distance, the mean speed and
the slowest corner all identical to nine decimal places. The pickle grows from 792 KB
to 1,121 KB; conversion itself does not get slower (1.53 s against 1.54 s), because
the extra samples are an interpolation over a speed profile that was already computed.

`--step-hz 10` and no flag are the same run, byte for byte — `sha256sum -c` passes on
a re-convert. Like `--speed-kph`, it is an argument rather than a config field:
`configuration_checksum` feeds the generation fingerprint, so putting it in
`config/default.yaml` would invalidate the Stage 3 review the next time the map was
generated. And unlike the rendering flags it is **not** read from `.env` at convert
time — a dataset's rate is baked into bytes the review never re-checks, so picking it
up from a machine-local file is how two workspaces end up at different rates with
nobody having decided.

**A dataset can only be driven at the rate it was written at**, and `tools/drive.py`
refuses the mismatch rather than driving the route at 10× speed. That half is under
[Simulate](#simulate), below.
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
— nothing to install, and `image_on_cuda` from MetaDrive's install docs is not a renderer
selector (it is an RL image pipeline; `--image-on-cuda` below). What the discrete card buys
is road-surface detail, because the GL texture ceiling is what caps resolution: measured
16384 px on this machine's Intel iGPU against 32768 on its RTX 4050, so `mosque` renders at
8 px/m instead of 4. `drive.py` asks the card rather than assuming, and prints which it got.

**`--image-on-cuda` keeps rendered frames in GPU memory** instead of copying them to the
host, as CuPy arrays. It needs `--render offscreen`, the opt-in `gpu` dependency group and
this repo's own interpreter:

```bash
uv sync --group sim --group gpu
cd scripts && METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
    --render offscreen --agent-policy idm --sensors camera --image-on-cuda
```

Measured on `junction-1` with one 512×288 camera, three matched pairs of 200 steps:
`env.step` costs **7.09 / 8.04 / 8.28 ms** on the CPU path and **3.20 / 3.59 / 3.76 ms**
with the flag — 2.2× every time, all of it the synchronous `getScreenshot()` readback the
CPU path does every step. The drive itself is unchanged, byte for byte in `drive.py`'s
output. **It is worth nothing over `--policy-url`**, also measured: the wire needs host
bytes, so the frame is copied back and both paths send the same 2927.0 KB a step. It pays
for a model running in this same process, which reads the pointer.

Two things it needs, and both fail in ways that do not name themselves. It needs the
**discrete card** — CUDA-GL interop registers a GL texture with the CUDA context, so on this
hybrid machine the PRIME variables above are required and without them env construction dies
with `cudaErrorUnknown(999)`; `scripts/drive.sh` sets them already. And it needs
`cuda-python` **below 13**, which the group pins: 13.0 removed the `cuda.cudart` shim
MetaDrive imports, and without it MetaDrive's gate is off. `drive.py` checks that gate itself
and refuses by name, printing which interpreter it looked in.

**It is refused with `--render 3D`, and the drive is not what fails.** Measured on
`junction-1`: 352 of 370 steps, `arrive_dest=True`, completion 0.953 — and then
`env.close()` raises `cudaErrorInvalidGraphicsContext(219)` out of MetaDrive's own
`MainCamera.unregister` (`main_camera.py:585`), handing a CUDA graphics resource back against
a GL context that has already gone. So a successful drive exits non-zero, and the exit status
is the one thing `drive.py` is for. Watch with `--render 3D` alone; keep frames on the card
with `--render offscreen --image-on-cuda`. Nothing is lost by the split — the point of holding
a frame on the card is a model reading the pointer in the same process, which does not need a
window.

**Lane lines are `--line-width-m`, in metres, default 0.15** — about a real road
marking. MetaDrive's own thickness is in *pixels*, so its real width moves with the
size of the map: its 2 px is 0.5 m on `mosque`'s 4096 m terrain square and 0.06 m on
`junction-1`'s 1024 m one, wrong in both directions from opposite ends. One pixel is
the floor, so a big map cannot go as thin as a small one — 0.125 m is `mosque`'s
limit, and the tool says so rather than rounding quietly. `--line-width-m 0` restores
MetaDrive's own. Set `LINE_WIDTH_M` in `.env` to stop typing it.

**The step rate is `--step-hz`, default 10** — MetaDrive's own. `env.step` advances
`physics_world_step_size` × `decision_repeat`, and `--step-hz` sets both: 10 gives
exactly MetaDrive's (0.02, 5), so passing it and omitting it are the same run; 100
gives (0.01, 1). It is the rate a policy is called at as much as the rate the drive is
recorded at. Set `STEP_HZ` in `.env` to stop typing it.

```bash
./drive.sh junction-1 -- --step-hz 100                   # 3D window
./drive.sh junction-1 -- --render none --step-hz 100     # headless, just the numbers
./sensor-survey.sh junction-1 -- --step-hz 100           # the IMU at 100 Hz
```

**The decision rate is `--decision-hz`, and it is the one clock MetaDrive does not have.**
The question is usually asked as `world tick / decision + camera / physics` — CARLA's three
knobs — and MetaDrive has two: `env.step` *is* the world tick, *is* the policy call and *is*
the camera draw, and the sensor config (`name=(cls, *args)`) has no slot for a rate anywhere.
So `--decision-hz` is a stride counted in our own loop: it must divide `--step-hz`, and
`--step-hz 100 --decision-hz 20` is `100/20/100` — the world and the physics at 100 Hz, the
policy asked and the `--sensors` read every fifth step, with the action **held** in between.
Unset it is the step rate, which is the run there always was. Set `DECISION_HZ` in `.env`.

```bash
./drive.sh junction-1 -- --step-hz 100 --decision-hz 20  # 20 Hz decisions on a 100 Hz world
./step-timing.sh mosque -- --rows 1 --step-hz 100 --decision-hz 20
```

It is what openpilot's bridge needs — `_DT_MDL = 0.05` — and a better answer than converting
a 20 Hz dataset: the same 0.05 s control interval with ten times the physics under it. On
`--agent-policy replay` it gates the sensor read alone, because `ReplayEgoCarPolicy` runs
*inside* the engine and MetaDrive calls it every step whatever the flag says.

Two constraints, both arithmetic, both refused rather than rounded: physics must be a whole
multiple of the world tick (a 100 Hz tick with 50 Hz physics is half a substep and does not
exist), and a decision a whole divisor of it (nothing moves between two steps).

**A dataset can only be driven at the rate it was written at.** Three things consume the
recording one frame per `env.step` with no interpolation: `--agent-policy replay`, a
baked light tape under `--lights tape`, and any non-ego track. At a different rate none
of them *fails* — each simply drives something other than what the dataset says — so
`drive.py` refuses, naming both rates and both ways to fix it. Convert at the rate you
mean to drive at, or drive at the rate the dataset carries.

Three more couplings are MetaDrive's own and are **warned** about rather than fixed —
patching a reference checkout is out of bounds, and each is a real difference rather
than a data fault. `--agent-policy idm` will not drive identically, because
`PIDController` has no timestep in it at all and both its gains scale with the rate;
`--agent-policy manual` feels ten times slower at 100 Hz, because `STEERING_INCREMENT`
is applied per `env.step`; and `--render 3D` above 10 Hz runs slower than the clock on
the wall, because `ForceFPS` takes its interval from `physics_world_step_size`.

Measured on `junction-1`, so the cost is a number rather than a guess:

| | 10 Hz | 100 Hz |
|---|---|---|
| `env.step`, headless | 1.094 ms | **0.848 ms** |
| `env.step`, `--render offscreen` | 10.9 ms | 20.2 ms |
| `env.step`, `--render 3D` (RTX 4050) | 83.4 ms | 16.6 ms |
| 3D speed against wall-clock | 1.20× | **0.60×** |
| a whole headless drive | 352 steps, 1.55 s | 3,516 steps, 4.85 s |
| scenario pickle | 792 KB | 1,121 KB |

One `env.step` is *cheaper* at 100 Hz, not dearer — `decision_repeat` drops from 5 to 1,
so it is one physics substep instead of five. A whole drive still costs about 7.8×,
because there are ten times as many. And 3D tops out at **60 fps either way** (five
frames per 83.4 ms, one per 16.6 ms — the display's vsync), which is why asking
`ForceFPS` for 100 makes the window run at 0.60× real time rather than 1.20×. It is
usable, and it is slower than real time.

The underlying commands, for anything the script does not cover:

```bash
# load-and-check, no simulator needed
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/check_dataset.py workspaces/junction-1/scenarionet-10hz

# drive it by hand, without the script
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
  /home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/drive.py workspaces/junction-1/scenarionet-10hz --render 3D

# adding manual line width 0.15 is the default setting
  __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
    /home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
    tools/drive.py workspaces/mosque/scenarionet-10hz --render 3D --line-width-m 0.10
```

Use `tools/drive.py`, not `python -m scenarionet.sim`. Both load the dataset
correctly, but in 3D `sim.py` shows a broken map — MetaDrive's terrain defaults are
sized for short Waymo clips, not for a road network — and none of the settings that
fix it are reachable from that entry point. `drive.py` measures each scenario and
picks a terrain size and texture resolution that fit. `--render` also accepts
`none`, `offscreen`, `2D` and `semantic`.

#### Trying the step rate end to end

Runs straight through, and leaves `junction-1` holding both rates. **A rate gets its
own directory** — no flag writes `scenarionet-10hz`, `--step-hz 100` writes
`scenarionet-100hz` — because a dataset can only be replayed at the rate it was written
at, and the scenario filename is the same either way. `drive.sh` and `sensor-survey.sh`
pick the directory that matches the rate they are about to run at, from `--step-hz`
after `--` if there is one and from `STEP_HZ` in `.env` otherwise. `convert` is typed
from the repo root, because it takes workspace paths relative to it; the two scripts are
typed from inside `scripts/`, which they cd out of themselves.

```bash
# 1. no flag changes nothing - the invariant the whole thing rests on
sha256sum workspaces/junction-1/scenarionet-10hz/*.pkl > /tmp/before.txt
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json
sha256sum -c /tmp/before.txt                                   # all three OK

# 2. the same drive at 100 Hz
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json --step-hz 100

# 3. check it. The drivability gate measures a fixed 0.1 s window rather than one
#    step, so it reports the same worst swing it reported at 10 Hz
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/check_dataset.py workspaces/junction-1/scenarionet-100hz

# 4. drive and survey it, from inside scripts/
cd scripts
./drive.sh junction-1 -- --render none --step-hz 100           # 3516 of 3695 steps
./sensor-survey.sh junction-1 -- --step-hz 100                 # IMU differenced over 0.01 s

# 5. the refusal. The scripts cannot reach it any more - they pick the directory that
#    matches - so it takes drive.py by hand, aimed at the 10 Hz dataset at 100 Hz
cd .. && /home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/drive.py workspaces/junction-1/scenarionet-10hz --render none --step-hz 100
#    REFUSED, naming both rates and both fixes

# 6. and asking a script for a rate nobody built names the rates that exist
cd scripts && STEP_HZ=20 ./drive.sh junction-1
```

Step 3 reports `step 0.01 s (100 Hz)` and `worst turn 27.9 deg per 0.1 s window (10
step(s) of 0.01 s), 0 window(s) over 30 deg` — against `24.7 deg per 0.1 s window (1
step(s) of 0.1 s)` at the default. Step 4's drive reaches completion 0.950, against
0.953 for the same route at 10 Hz.

### Put other cars on the road

The roads in a converted dataset hold one moving car, the ego — plus whatever `--actors`
put there. `--traffic live` puts other *cars* on them, generated from the reviewed lane
graph and driven by MetaDrive's own IDM. Two steps — work out the routes once, then drive:

```bash
uv run osm-scenario traffic -w workspaces/junction-1 --count 60 --seed 1
```

then from inside `scripts/`:

```bash
./scripts/drive.sh junction-1 -- --traffic live --traffic-count 25 --render 3D

./scripts/drive.sh junction-1 -- --agent-policy idm --traffic live --traffic-count 25 --render 3D

```

`--count` is a pool of *routes*, not cars; `--traffic-count` is how many cars are on the road
at once, and one route can carry several. `traffic.json` holds geometry and no timing, so the
same file serves every rate the workspace holds — like `routes.json`, and unlike a dataset.
A different seed costs a second rather than a re-conversion, because none of this touches the
dataset.

**The cars are not in the dataset**, so a stock ScenarioNet consumer still sees an empty map.
That is deliberate and it is the same split as `--lights tape` against `--lights live`: a
recorded track has to be as long as the episode, so a slow agent would run off the end of the
tape and the road would empty around it. Live cars have no end — one that reaches the edge of
the map is retired and another enters where a road begins. Measured across 24 episodes on both
extracts, under a replayed ego and a slow `--agent-policy idm` one, the road never fell below
the number of cars asked for.

That is also the difference between this and **Stage 6, actors**, above.
Pedestrians, cyclists and street furniture sit on the *other* side of the same split: they are
recorded tracks written into the pickles at convert time, so they need no flag here and a stock
consumer sees them — and they are a tape, with the end that implies. Cars are live, so they are
not in the file and have no end. If the question is "how do I add a person", the answer is
`convert --actors`, not anything on this page.

**The cars give way where two routes cross.** MetaDrive's IDM brakes only for what is on its
own lane, so a car entering a junction from the side is not an obstacle to it at any distance —
which is why a junction full of it collides. Traffic looks 40 m ahead along its own route, finds
the first place another car's route crosses it at an angle, and holds one of the two back; the
nearer car goes. It only ever slows a car down — steering, following distance and everything
else is still MetaDrive's.

**The cars slow for the corners their route actually turns through.** MetaDrive's IDM aims for
a flat 40 km/h everywhere and steers with a PID looking 1 m ahead — it has no idea where the
road is — so on `junction-1`, where 29.5% of the route distance allows less than 40 km/h on
curvature alone, cars simply ran wide. Each route now carries a speed profile and the cars are
held to it; `--traffic-speed flat` is how the left column below was produced.

| 25 cars, 5 episodes | before | after |
|---|---|---|
| `junction-1`, cars that left the tarmac | 41 | **25** |
| `junction-1`, worst distance off it | 9.39 m | **3.80 m** |
| `mosque`, cars that left the tarmac | 56 | **24** |
| `mosque`, worst distance off it | 9.51 m | **3.08 m** |

It costs pace: traffic averages about 18 km/h rather than 30, which is the trade for keeping it
on the road. A car that strays more than 5 m off its route anyway is taken off the map and
replaced at a route start, and reported separately — it did not complete a route.

**A traffic plan built before this needs rebuilding** — `osm-scenario traffic` — and `drive.py`
says so by name rather than driving it.

**Cars still do collide, and the number is measured rather than claimed.** Unsignalled, 25 cars,
ego replayed, over sixteen episodes on `junction-1` and twelve on `mosque` — one episode varies
too much to compare on:

| | give way off | give way on |
|---|---|---|
| `junction-1` | 79 collisions, 0.34 per car-minute | **60, 0.26** |
| `junction-1`, head-on only | 23 | **4** |
| `mosque` | 24 collisions, 0.12 per car-minute | **9, 0.04** |

`--traffic-give-way off` is how the left column is produced; it is for measuring the rule, not
for driving. The same number of cars complete their routes either way, so nothing is gridlocked,
and the rule costs about 3 ms a step at 25 cars.

What separates conflicting movements properly is still the signal, so a training run wants
`--signals` on the dataset and `--lights live` beside `--traffic live`. The two compose; they
are not alternatives, and traffic stops at a red without anything extra — a MetaDrive light is
a physical object on the lane, which is the same thing IDM brakes for.

`--traffic-seed` repeats a run. Two resets of the same scenario deliberately do **not** produce
the same traffic — an agent that meets identical cars at identical times learns the step number
rather than the traffic.

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
  examples/drive_with_a_policy.py workspaces/junction-1/scenarionet-10hz
```

It ships driving with MetaDrive's own IDM, and the only line a model replaces is
`policy = IdmDriver(env)`. A policy is anything callable taking the observation and
returning `[steering, throttle]`; nothing registers it and MetaDrive never learns it exists.

**`--step-hz` is the control rate here** unless `--decision-hz` lowers it, the policy
being called once per `env.step` — `--step-hz 100` calls it every 0.01 s instead of every
0.1 s, and `--decision-hz 20` alongside it every 0.05 s, holding the action in between. It
is on the example as well as on `drive.sh`, and the dataset has to have been converted at
the `--step-hz` rate, for the reason under [Simulate](#simulate).

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

**With `--render offscreen` the camera goes in too**, which is what makes the file training
data rather than a table of numbers. The observation is two different things by render mode:
with no graphics it is a flat 161-number vector, and offscreen MetaDrive swaps it for
`{"image", "state"}` — a `(H, W, 3, 3)` camera stack and a **41**-number state with no lidar
block. Both halves are written, as `observations` and `images`, beside `actions`,
`episode_starts` and `scenario_ids`:

```bash
./drive.sh junction-1 -- --render offscreen --sensors camera --record /tmp/drive.npz
# recorded  352 steps, observations (352, 41), actions (352, 2) -> /tmp/drive.npz (84.4 MB)
#           images (352, 180, 320, 3, 3) uint8, divide by 255 for the float the car saw
```

**The images are `uint8` and nothing is lost by it.** MetaDrive hands them over as float32 in
[0, 1], but the camera renders 8-bit and it is `BaseCamera._format`'s `ret / 255` that made
the float — so `round(x * 255)` is the inverse, not a quantisation, and the file carries
`image_scale` to undo it. It is a quarter of the size: 518 KB a step instead of 2.07 MB, 84 MB
compressed for the 352-step drive above rather than a third of a gigabyte. `--record-no-images`
keeps the numbers and drops the frames (29 KB for the same drive).

`--record --render offscreen` had never worked before 2026-08-24 — it ravelled the dict and
died with `TypeError: float() argument must be a string or a real number, not 'dict'`.

**Knowing a drive is still running.** Every 30 seconds a running drive prints where it is,
flushed, so a logged run says as much as a watched one:

```
progress     step 4200, 42.0 s driven, completion 0.081, 12 km/h, 205 ms/step (12 ms of it the policy round trip), 14m20s elapsed
```

A model drive needs it: `--agent-policy remote` has **no step budget** — the recording's length
is no bound on a car not following it — so it ends only when the episode does or at MetaDrive's
`horizon` of 100000 steps, and each decision is about a second of forward pass. A route that
completes is 758 of them; one that runs to the horizon is 20 000, five and a half hours, and
until this line existed the two were indistinguishable from a terminal, as was a hung socket.

A step count climbing against a completion that is not is a car going nowhere; `ms/step` is
measured over the last interval only, so a drive that slows says so rather than having it
averaged away; and the round-trip share separates a slow model from a slow socket. A *replayed*
drive gets `of 3695 (49%)` and `~2s left` as well, because there the recording is the bound —
there is deliberately no ETA where there is no budget. `--progress-seconds N` sets the interval,
`0` turns it off, and `--render 3D` ignores it.

**Watching a drive that happened somewhere else.** `--export-drive <dir>` writes the drive
itself out, as a ScenarioNet dataset this same tool can drive. It is for the case the model
makes ordinary: the interesting drives happen on the GPU rig, which has no screen, and the
question — is the car crawling, circling, or oscillating? — is one a picture answers in a
second and no summary line answers at all.

```bash
# on the rig, headless, alongside whatever the drive was already doing
./sim.sh scripts/drive.sh junction-1 -- --render offscreen --step-hz 100 \
  --model-checkpoint "$MODEL_CHECKPOINT" --agent-policy remote --policy-url http://127.0.0.1:8642 \
  --export-drive workspaces/junction-1/drives/rig
# exported  1 scenario(s), 3517 frames -> workspaces/junction-1/drives/rig (1.4 MB)

# then scp the directory here and watch it in 3D
./watch-drive.sh workspaces/junction-1/drives/rig
```

**Ctrl-C stops the drive and exports what it has.** Without that the flag is unusable on the
run that most needs it: a car that *stalls* never terminates — `terminated` and `truncated` stay
false, the loop steps on to its budget, and under the model that budget is the better part of an
hour. Ctrl-C used to raise straight through the export and throw the whole recording away. Now it
sets a flag, the loop leaves at the end of the frame it is in, and the drive is written exactly as
if it had finished:

```bash
^C
stopping     Ctrl-C - finishing this frame, then exporting the drive so far. Ctrl-C again exits at once, keeping nothing.
scenario 0   ...: 842 of 30170 steps (3017 recorded frames at 0.1 s), arrive_dest=False, ...
             did not arrive: stopped early at your request
exported     1 scenario(s), 842 frames -> workspaces/junction-1/drives/rig (352 KB)
```

It exits `0` and does not count as a failure — the exit status means *the dataset is drivable*,
and a run cut short by hand says nothing either way about that. **A second Ctrl-C exits the
process outright** — `os._exit(130)`, no unwinding — so it is immediate and it keeps nothing.
It exits rather than raising because a `KeyboardInterrupt` lands wherever the process happens to
be, and under `--render 3D` that is inside panda3d's render or `env.close()`'s teardown of the
GL context; doing that segfaulted a laptop's GPU driver on 2026-08-28. In a container the signal
needs a tty:
`docker compose run` forwards it, and under `-T` the equivalent is
`docker kill --signal=INT <container>`.

**An export replaces its directory** rather than merging into it, so the same
`drives/<label>` can be re-run into as many times as it takes — which stopping early makes the
ordinary gesture. Only the three shapes a drive export writes are removed (`dataset_summary.pkl`,
`dataset_mapping.pkl`, `sd_*.pkl`); a directory holding anything else is refused by name, because
`--export-drive` takes a *directory* and a directory is the argument that gets mistyped. Merging
is what is ruled out: `dataset_summary.pkl` lists only what the run that wrote it exported, so a
shorter second drive would otherwise leave the first drive's scenarios beside a summary that does
not name them — a dataset that reads as smaller than it is, and drives as one.

**Repo-relative, so it is one string everywhere** — in the container and out, on the rig and
here, from whichever directory it is typed. `scripts/_common.sh` cds to the repo root and the
container's working directory is `/work`, which is the repo; `rigs/cams.txt` is the same string
for the same reason. `/work/workspaces/...` still works inside the container, but names nothing
outside it, so `drive.py` refuses that spelling on a host and prints the one to use.

It records object *states*, not pixels, so nothing extra is drawn and the cost is invisible:
measured on a 3516-step 100 Hz `junction-1` drive, the same steps and the same completion with
the flag as without, 1.4 MB out. `watch-drive.sh` is a sibling of `drive.sh` rather than a flag
on it because `drive.sh` takes a *workspace* and an exported drive is a bare dataset directory.
`workspaces/*/drives/` is **tracked**, so `git pull` is the way a drive gets from the rig to a screen. Commit the ones worth keeping rather than every iteration: a pickle of float arrays does not delta-compress, so a re-run into the same directory costs its full 1.4 MB again, permanently.

**The export carries the rate it was driven at**, and `watch-drive.sh` reads it back off the
file, so neither command above names a `--step-hz`. A dataset can only be replayed at the rate
it was written at, and the wrong rate does not fail — it *draws*. `ReplayTrafficParticipantPolicy`
sets the recorded velocity as well as the recorded position, so a simulator running slower than
the tape coasts the car forward between frames and yanks it back: a car spiking back and forth,
once a frame, over a recorded line that is perfectly smooth. MetaDrive's converter stamps every
export `0.1 * i` regardless of rate, which is exactly that trap; `drive.py` overwrites the
timestamps with the real interval on the way out, and `_refuse_mismatch` then refuses a wrong
`--step-hz` by name instead of drawing it.

**A replayed car at 100 Hz has to be settled onto the road first, and the drive says when it
does.** Every dataset here carries z = 0, and physics is what normally lifts the ego to its
0.537 m ride height. At `--step-hz 100` there is one physics tick per teleport, and Bullet does
not integrate a body on the first tick after its transform is written from outside — so *every*
tick is a first tick, the car never rises, and it is drawn half a ride height under the road.
`drive.py` steps the physics world at reset, with no teleport in between, until the height stops
changing (measured: 0.539 m in 105 ticks, against the 0.5365–0.5384 physics reaches unaided at
every other rate). It fires only for a replayed car at one tick per teleport, so no rate that was
already right moves — a 10 Hz drive still reports `vehicle z 0.014..0.537`.

One thing about it worth knowing before reading numbers off the file:

* **It is not training data**, and `--record` above is. The exported drive is a *scenario*, and
  `ScenarioEnv` scores route completion against the recorded trajectory — which here is the
  model's own drive, so driving it would measure the model against its own mistakes.

### What a model can actually see

Run the survey before choosing what your model takes as input. It drives, then reports every
output MetaDrive can produce — shapes, ranges, and whether each one *moved* over the drive —
and writes samples you can look at:

```bash
./sensor-survey.sh junction-1
./sensor-survey.sh junction-1 -- --policy straight     # a constant action instead of the IDM
./sensor-survey.sh junction-1 -- --step-hz 100         # every numeric sensor at 100 Hz
```

Samples land in `workspaces/<workspace>/sensor-survey/`: a PNG per camera, the point cloud as
`.npy`, the observation as `.npy`, and `track.csv` with position, latitude/longitude, IMU and
the action, one row per step.

**The step rate is what makes the IMU an IMU.** Acceleration is velocity differenced over
one `env.step`, so at the default it is a 10 Hz signal where a real unit runs 100–1000 Hz;
`--step-hz 100` differences it over 0.01 s instead, and the report header says which it
used. Every figure in this section was measured at the default 10 Hz.

**The cameras are read *and drawn* at `--decision-hz`.** MetaDrive redraws every buffer once
per `env.step` (`base_engine.py:458`, unconditional), so `tools/frame_gate.py` gates that call
itself — the frames come at the decision rate rather than at the world tick. It is most of
what a step costs: on `mosque` at 100 Hz with `rigs/cams.txt`, **26.11 ms/step at
`100/100/100` against 6.21 at `100/20/100` and 3.66 at `100/10/100`** — 0.34x real time to
1.47x and 2.54x. `--draw-every-step` puts the draw back on the world tick, and is the control
those figures were taken against: there a lower decide rate is worth under a millisecond of 26.

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
rigs around here are already written in.

From inside `scripts/`:

```bash
# one frame per camera, to workspaces/junction-1/sensor-survey/rig-<camera>.png
./sensor-survey.sh junction-1 -- --camera-rig rigs/cams.txt

# every step as arrays, to workspaces/junction-1/sensor-survey/rig/<camera>.npy
./sensor-survey.sh junction-1 -- --camera-rig rigs/cams.txt \
    --rig-record --steps 60

./sensor-survey.sh mosque -- --camera-rig rigs/cams.txt
```

and from the **repo root**, to resolve a spec without starting a simulator — mounts, aims and
the per-step size, in a second:

```bash
python tools/camera_rig.py rigs/cams.txt
```

`--camera-rig` writes one PNG per view, plus the usual `survey.txt`, `track.csv` and
`observation.npy`. `--rig-record` adds `(steps, H, W, 3)` uint8 per camera, row *n* of each
being row *n* of `track.csv`.

**Keep `--steps` low until you want the whole drive.** The 7-camera spec is 5.42 MB a step, so
a full 291-step `junction-1` run is **1.6 GB**. The tool prints the projected size before it
starts anything.

Four things worth knowing before you point a model at it:

- **The frame is converted, not copied.** CARLA is x-forward with `yaw` positive to the right;
  MetaDrive is y-forward with heading positive to the **left**. So the mount is an x/y swap and
  the aim is a sign flip, and the tool prints where each camera *actually* points rather than
  trusting its name — `cams.txt` disagrees with itself about the sign, and two of its four side
  cameras are named backwards whichever reading you take.
- **The cameras are mounted on the ego, not one camera re-aimed per view.** Six mounted cameras
  cost 20.4 ms a step against 77.3 ms for MetaDrive's own borrow-and-re-aim example.
- **A rig run is the rig alone** — no point cloud, no depth or semantic camera. The seven-camera
  spec costs 53.9 ms a step, 18.6 Hz, 5.42 MB. Nothing is unavailable: `--policy idm` is
  deterministic, so a plain run gives the other four over the same drive, and the report names
  any file the earlier run left behind so an old one is not mistaken for a new one.
- **Nine image buffers is the measured ceiling**, past which MetaDrive's reset fails
  *intermittently* — 9 cameras survive 5 runs of 5, 10 give 3/5, 11 give 1/5 — so a rig over it
  is refused outright rather than warned about. Mixing in more than one non-RGB camera costs
  more than the count suggests: two of them measure 1/5 at nine buffers.

`docs/scenario-datapoints.md` §10 has the conversion table, the measurements, and what was ruled
out along the way.

### Time it: how much real time one simulated second costs

```bash
./step-timing.sh junction-1                          # rows 1-6, every rate the workspace holds
./step-timing.sh junction-1 -- --rows 5              # one row on its own
./step-timing.sh junction-1 -- --rows 2,6            # the pair that prices the camera
./step-timing.sh junction-1 -- --physics-hz 100      # pin the integrator on every row
./step-timing.sh mosque -- --label rig-container     # name the machine in the CSV
./step-timing.sh -- --list-rows                      # what each row measures, and exit

# price the vehicle's own cameras instead of the single one this tool invents
./step-timing.sh mosque -- --camera-rig rigs/cams.txt
```

**`docs/step-timing-rows.md` is the reference** for what every row, printed column and CSV
field means. What follows is how to run it and what it found.

The default is **every row but 7**, which opens a window and so cannot run unattended — it is one
`--rows 7` away. Rows 1 and 2 differ only in who drives: row 1 replays the recorded track and
decides nothing, row 2 puts MetaDrive's IDM in the seat. Row 3 puts *your* model in the same seat
over `--policy-url`, and skips itself with `needs --policy-url` when nothing is listening rather
than disappearing from the table. `--rows 5` runs one row on its own.

**Every offscreen row carries a camera**, drawn and read every step — `--render offscreen` is
the only way one exists without a window, and MetaDrive builds the observation out of it inside
`env.step`. It is **about three quarters of what a step costs**, so it is the thing this sweep is
mostly measuring. `--rows 2,6` prices it: on this laptop the same 10 Hz drive is 16.69 ms a step
with the camera and **4.06 without**, 5.45x real time against **19.82x** — and at 100 Hz it is the
difference between 0.51x and **2.30x**, between slower than the clock and twice as fast as it.

**That camera is one the tool invented, until `--camera-rig` names a real one.** Unflagged it is a
single 320×180 buffer, a size chosen in `step_timing.py` rather than by any vehicle — so an
unflagged figure is not what *your* car costs. `--camera-rig` takes the same CARLA-shaped spec
`sensor-survey.sh` takes and mounts those cameras instead. Measured on `junction-1` at 10 Hz over
200 steps, replay row, same drive each time: the seven-camera spec (six 512×288 and one 1280×720,
**5.42 MB of image a step** against 0.17) runs at **24.70 ms a step and 3.08x real time**, against
**10.00 ms and 9.28x** for the invented camera and 3.20 ms / 27.20x with no graphics at all. So the
rig is about 21.5 ms of a 24.7 ms step, and it is the difference between a sweep that runs at nine
times the clock and one that runs at three. Reading all seven back out is a further 3.90 ms,
reported on its own as `rig_ms_median` — a buffer copy rather than a second render, which is why it
is timed in the loop where a row's own `read` list may not touch a camera.

**Read the `policy` column, not the difference between the rows.** Subtracting one row from the
other was the intent and it does not survive the machine: measured three times over, row 1 came
out at 8.90 / 8.99 / 10.07 ms a step and row 2 at 9.35 / 10.35 / 8.99, so the difference read
+0.45, +1.36 and **−1.08** ms while the driver's own cost sat at 0.37–0.43 ms throughout. About a
millisecond of run-to-run spread swamps it. `policy` is timed directly around the policy call and
is the number that answers "is my model the slow part"; the replay row is the reference for
whether the simulator keeps up at all with nothing deciding.

Every run prints its table and writes its own CSV into `<workspace>/reports/`, stamped with the
moment it started. Nothing is appended to and nothing is overwritten, and every row carries the
machine it was measured on — host, docker, CPU, GPU, GL ceiling, versions — so a file from a
container on another box concatenates with this one and needs nothing lined up by hand.

Measured on this laptop (RTX 4050, `junction-1`, whole drive):

```
  #  render     policy  sensors            decide  physics  rpt   steps   sim s  wall s  x real  ms/step  policy    p95
  1  offscreen  replay  camera,imu,gps       10 Hz    50 Hz  x5     332    33.2     5.7   5.79x   16.80    0.00  21.39
  2  offscreen  idm     camera,imu,gps       10 Hz    50 Hz  x5     271    27.1     4.5   6.08x   15.48    0.66  18.18
  1  offscreen  replay  camera,imu,gps      100 Hz   100 Hz  x1    3496    35.0    57.4   0.61x   16.11    0.00  18.80
  2  offscreen  idm     camera,imu,gps      100 Hz   100 Hz  x1    2219    22.2    38.6   0.57x   16.79    0.68  21.12
```

Under `--camera-rig` the `sensors` cell reads `camera x7,imu,gps` and the CSV gains
`camera_rig`, `camera_count`, `camera_mb_per_step` and `camera_hz`, so a file says which
vehicle it was measuring rather than leaving it to be remembered.

**The answer in one line: with a camera, 10 Hz runs at about 6x real time on this laptop and
100 Hz runs at 0.6x** — slower than the clock, so an hour of simulated driving takes over an
hour. And **run it on a quiet machine**: the same configuration measured 8 ms a step early in a
session and 17 ms after twenty minutes of back-to-back sweeps, which is thermal rather than
anything in the code. The absolute numbers are worth what the machine's state was worth; the
ratios within one run are what compares.

**`ms/step` is not comparable across rates and `x real` is**, which is the one way to misread
this table. `env.step` holds one action across `decision_repeat` physics ticks — `rpt` in the
table — and MetaDrive's 10 Hz default is `(0.02, 5)`, so **it integrates at 50 Hz, not 10**.
Going to 100 Hz makes a step *cheaper* by dropping to one tick, while there are ten times as
many of them.

Three things the numbers above say, none of which were guessable:

- **With a camera in the loop, the camera is the budget.** A 10 Hz step and a 100 Hz step cost
  the same to within the noise — 16.80 against 16.11 ms above — because one frame is drawn per
  `env.step` whatever the rate, so the render dominates and the five-against-one physics repeat
  barely shows. Per *simulated* second that is a full 10x more rendering at 100 Hz against 2x
  more integration, which is the whole of the 10x difference in `x real`.
- **The camera readback is inside `env.step`, not beside it.** With `image_observation=True`,
  `ImageStateObservation.observe` calls `perceive()` and rolls the 3-frame stack as part of
  building the return value, so it cannot be timed separately and must not be timed twice. The
  `sensors` column and `sensor_ms` are the *numeric* sensors only.
- **The per-step overhead is what 100 Hz multiplies, not the physics.** With no graphics at all
  (`--rows 6`), pinning the integrator finer gives 2.14 / 2.44 / 2.87 ms a step at 5 / 10 / 20
  ticks — about **1.90 ms of fixed overhead plus 0.049 ms a tick**. Ten times as many steps
  means ten times that 1.90 ms.

**`--physics-hz` is the second dial, and it exists for the comparison with CARLA.** `--step-hz`
derives both keys from one number, so 10 Hz decisions with 100 Hz physics cannot be asked for —
and that pairing is precisely CARLA's default (`fixed_delta_seconds` 0.1,
`max_substep_delta_time` 0.01, `max_substeps` 10). Benchmarked as shipped, MetaDrive at 10 Hz is
integrating at half of what CARLA does at the same tick rate. `--physics-hz 100 --step-hz 10`
is the matched shape, and it is also the likely configuration for camera-based training: full
integration, a tenth of the rendering.

A rate that does not divide the step is refused rather than rounded, because a decision cannot
be finer than a physics tick.

**`--decision-hz` is the third dial**, and the sweep takes it too: `--rows 1 --step-hz 100
--decision-hz 20` is the `100/20/100` row. Since the sweep drives *every* rate a workspace
holds, a decision rate that divides one dataset and not another skips the row it cannot have
and runs the one it can — `--decision-hz 20` on a 10 Hz dataset says so in the table rather
than ending the run. `decision_hz`, `steps_per_decision`, `camera_hz` and `camera_draw_hz`
are in the CSV — the last of those counted by the gate rather than declared;
`docs/step-timing-rows.md` is what they mean.

**To compare several whole configurations, put them in a file.** `--rate-sets` takes
`name,step_hz,decision_hz,physics_hz`, one a row — `world tick / decision + camera / physics` —
and drives them one after another **in one process, into one CSV** with a `rate_set` column to
pivot on. One process is the point: `prime` is paid once and every machine column is identical
by construction rather than by two runs happening to agree.

```bash
./step-timing.sh mosque -- --rate-sets ../scripts/rate-sets.csv
./step-timing-docker.sh mosque -- --rate-sets scripts/rate-sets.csv --camera-rig rigs/cams.txt
```

`scripts/rate-sets.csv` ships with `10/10/50`, `100/100/100`, `100/10/100` and `100/20/100`.
A blank `decision_hz` or `physics_hz` means "whatever `step_hz` derives", so a bare `10` is
`10/10/50`. **A set drives only the dataset written at its own `step_hz`** — that is the one
place `--rate-sets` differs from the flags, a set being a whole configuration rather than an
override — so a 100 Hz set needs `convert --step-hz 100` to have been run. It cannot be
combined with `--step-hz`, `--decision-hz` or `--physics-hz`: the file is the source.

### Time it on another machine: the container

```bash
./container-check.sh mosque                  # build, gpu check, tests, full sweep
./container-check.sh mosque -- --camera-rig rigs/cams.txt

./step-timing-docker.sh mosque               # just the sweep
./step-timing-docker.sh mosque -- --rows 2,6 # the pair that prices the camera
```

**`docs/container.md` is the reference** for building it, running it and moving it to another
machine. What follows is why it exists and what it measured.

Every row of a step-timing CSV carries the host, CPU, GPU, GL ceiling and the python / numpy /
MetaDrive versions, so two machines' files concatenate into one spreadsheet. That only means
anything if both machines are provably running the same simulator, and reproducing a venv by hand
on each box is not provable. `uv.lock` plus a pinned MetaDrive commit is — which is what the image
is for. The rig needs Docker, the NVIDIA container toolkit and this repo; nothing else — plus, for
the AV3 model, the AV3 checkpoint, which is mounted rather than built in (see 3b of
`docs/container.md`).

**The image carries the model stack too**, so the same argument reaches one machine further on: an
AV3 forward pass is ~1 s on this laptop's power-capped RTX 4050, which is the whole reason to run
it somewhere else, and a figure from somewhere else is only worth having if the stack behind it is
pinned rather than reassembled. That is what takes the image from 2.88 GB to **13.2** — `gpu` and
`model` are 10.5 GB of wheels — and it was chosen over a second, leaner image deliberately, on the
grounds that it is built once and then used.

**There is one interpreter in the container, not two.** MetaDrive has always run here on its own
3.8 venv (numpy 1.24) beside this repo's 3.10 (numpy 2.2), which is why `drive.sh`,
`sensor-survey.sh` and `step-timing.sh` shell out through `METADRIVE_PYTHON`. It turns out to run
on 3.10 — no `python_requires` cap, no `ext_modules`, no numpy-2-removed aliases — so the image
installs it into the same venv as the converter, via an **opt-in `sim` dependency group**:

```bash
uv sync --group sim      # the same arrangement on the host, if you want it there too
```

The group is pinned to a commit, not to `==0.4.3`. The reference checkout is 32 commits past that
tag and `metadrive.constants.EDITION` reports `MetaDrive v0.4.3` either way, so a version pin
would let two machines run different simulators while every CSV claimed they were the same.

There is a second opt-in group, `gpu`, holding the three packages MetaDrive's `image_on_cuda`
path imports — `cupy-cuda12x[ctk]`, `PyOpenGL` and `cuda-python`. It is separate because it is
about 1.5 GB of CUDA toolkit and only `--image-on-cuda` needs it; `uv sync` installs neither
group. A third, `model`, holds torch / torch-tensorrt / tensorrt for the AV3 checkpoint —
see *Load the model checkpoint* below. **`uv sync` takes exactly the groups you name and
removes the rest**, so once you want more than one, name them all:
`uv sync --group sim --group gpu --group model`. `cuda-python` is capped **below 13** on purpose: 13.0 removed the `cuda.cudart` shim
MetaDrive imports, and the cap is what keeps the gate open.

**It renders on the real GPU with no X server, and the trap there is silent.** panda3d ships
`libp3headlessgl.so` (EGL) and lists it as the fallback after GLX; the image makes it the first
choice. But libglvnd picks a driver by reading the manifests in
`/usr/share/glvnd/egl_vendor.d/`, and the NVIDIA container toolkit installs `libEGL_nvidia.so.0`
**without installing the manifest that points at it** — so out of the box every context lands on
`llvmpipe`, which does not fail. It runs the whole benchmark on the CPU and reports plausible
numbers. The image writes that file itself. **Check `gl_renderer` in the CSV**: it must name the
card, never Mesa or llvmpipe.

Measured on this laptop, `mosque`, 200 steps, same rows either side:

| | host, python 3.8 / numpy 1.24 | container, 3.10 / numpy 2.2 |
|---|---|---|
| row 1, offscreen replay | 3.78 ms/step, 25.91x real | 4.05 ms/step, 24.23x real |
| row 6, no graphics | 0.99 ms/step, 86.35x real | 1.03 ms/step, 82.26x real |

Within a few percent — well inside the run-to-run spread — so neither the container nor 3.10
costs anything measurable. The seven-camera rig runs in there too, at 22.74 ms a step and 3.97x
real on the same drive.

`compose.yaml` mounts the repo at `/work` and works from there, so reports land in
`<workspace>/reports/` on the host exactly as they do outside, owned by you rather than by root —
and **`--camera-rig rigs/cams.txt` is the same string inside and out**, because the spec is in the
repo and `scripts/_common.sh` cds to its root before anything runs. Nothing about a run is written
differently in here. A spec deliberately kept *outside* the repo is the other case: set `RIG_DIR`
in `.env` to the directory holding it and it is `/rig/<name>.txt` inside.

Two things it does not do. **Row 7 needs a display** and there is none, so it is the one row the
container cannot run. And `network_mode: host` is deliberate rather than lazy — row 3 times a
round trip to `--policy-url` at 0.126 ms, and a bridge network in front of a number that small
would corrupt the row it exists to produce; it also means `examples/policy_server.py` can run
inside the container or outside it and `127.0.0.1` reaches it either way.

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

`--sensors` takes any of `imu, gps, route, camera, depth, semantic, point-cloud`; the
observation is always sent. `route` is the recorded route ahead of the car in **metres** —
25 points at 2 m, ego frame, x ahead and y to the left — read straight off the object
`TrajectoryNavigation` steers by. The observation carries a route too, but normalised and cut
off at 30 m, which a controller cannot undo. `/spec` tells the server the real `step_seconds` for the run — the two MetaDrive
keys multiplied out, not a literal 0.1 — so a model integrating anything is in the right units
whether or not `--step-hz` was passed. `remote` is the same code path as `manual`, so it has no
step budget and neither rate warning applies to it.

**Read each sensor's `dtype` off the payload rather than assuming one.** `camera` and `semantic`
arrive as **uint8 0-255**, which is what the GPU produced — a model wanting 0-1 floats does that
divide itself, fused with the channel order and transpose its weights expect anyway. `depth` and
`point-cloud` arrive as **float32** and are not pictures: depth is a nonlinear 0-1 buffer
occupying only 0.705-1.000 of its range on a real drive, and the point cloud is in metres,
measured -18477 to +11030. Sending either as 8 bits would leave the first with 76 levels for the
whole scene and destroy the second outright, so they are excluded by name and
`tests/unit/test_policy_client.py` checks that split against MetaDrive's own source.

In a training loop use the example instead, which needs no window at all:

```bash
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  examples/drive_with_a_policy.py workspaces/junction-1/scenarionet-10hz \
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
| `3D` | `camera,imu,gps` | **226.6 KB** (901.6 before) | 15.0 ms |
| `offscreen` | — | 3600 KB | 29.4 ms |
| `offscreen` | everything | **3652.4 KB** (5002.4 before) | 49.0 ms |

The two camera rows fell when camera frames stopped being sent as floats. Each pair was taken
either side of that change on the same drive, so the two numbers in a cell are comparable; the
other three rows have no camera on the wire and did not move. The round trips are the earlier
figures — the wire got smaller, not faster.

Two things to know from that table. **`--render offscreen` makes the observation itself a stack
of camera frames**, whether or not you asked for a sensor — that is how MetaDrive keeps a camera
alive at all — so it costs 3.6 MB a step for an image nobody wanted, and none of that is a
`--sensors` payload, which is why that row did not move. `--render none` and
`--render 3D` both leave the observation at 161 floats, which is why the 3D row with a camera on
is cheaper than the offscreen row with nothing. And **if you ever see ~40 ms a step, that is not
a slow model**: it is Nagle's algorithm meeting delayed ACK because one end of the socket lost
`TCP_NODELAY`. Both ends set it; a stock `http.server` does not.

The client refuses an action MetaDrive would have swallowed. `action_check` is off by default and
`EnvInputPolicy` simply clips, so an output in [0, 1] silently loses the ability to brake and
`NaN` is not clipped at all. Those are raised instead.

### Drive it with openpilot

`examples/openpilot_server.py` puts wing-sim's openpilot bridge behind that same
`--policy-url`. The bridge is a **controller**, not a driver: it takes a predicted path and
three ego scalars and returns pedals, and never sees an image. So the path comes from the
`route` sensor, and `--sensors imu,route` is not optional.

Start with the stand-in bridge, which needs no fork, no Docker and no SSH key:

```bash
python examples/openpilot_server.py --backend stub --port 8642
```

```bash
./drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642 \
  --sensors imu,route --render none
```

Measured: **380 steps, `arrive_dest=True`, route completion 0.951** on `junction-1` and 435 /
0.951 on `mosque`, at 0.5 ms and 2.5 KB a step. That is what proves the plumbing — the frame,
both sign conventions and the round trip — before there is a real bridge to blame.
`--backend constant --steering 1.0` leaves the road in 13 steps, so a drive that reaches the
destination is not an accident.

With the fork built and its bridge listening, point at it instead:

```bash
python examples/openpilot_server.py --backend bridge --bridge 127.0.0.1:5558 --port 8642
```

and for row 3 of the sweep, which is the thing this exists for:

```bash
./step-timing.sh junction-1 -- --rows 1,3 --policy-url http://127.0.0.1:8642 \
  --policy-sensors imu,route
```

Four things worth knowing before tuning anything:

- **`target_speed` is sent every tick because a missing one means stop.** The bridge reads an
  absent target as `0.0`. `--target-speed-mps` sets it; the default is 10 m/s.
- **The bridge is written for 20 Hz.** `_DT_MDL = 0.05` is what its lag compensation and its
  curvature-rate limit are counted against, and `step_seconds` is the interval between two
  `act()` calls rather than between two `env.step`s — so **`--step-hz 100 --decision-hz 20`
  is what matches it**, and beats converting a 20 Hz dataset: the same 0.05 s control
  interval with ten times the physics under it. Measured on `mosque`: 868 calls over 4337
  steps, `arrive_dest=True`, completion 0.950, and no note. The server prints one at any
  other decision rate.
- **Its pedal map is CARLA calibration**, measured on Town10HD with a Tesla M3, and
  `--longitudinal table` is the re-measurement. See below. Steering is unaffected — that path
  is geometric, which is what `carla_steer_curvature_gain: 0` selects.
- **The bridge container is in this repo**, fork and all, and `scripts/bridge.sh` builds, starts
  and checks it. `docker/openpilot/` holds the Dockerfile, the 1649 lines of bridge server, and
  `deps/openpilot/` — 309 MB of `zapetaai/openpilot` at `c767ace8`, **vendored** rather than
  cloned, because cloning it needs SSH access to a private org that nobody can grant a rig
  remotely. `deps/openpilot/VENDORED.md` records the commit and every submodule SHA.
- **So `--backend bridge` needs nothing the stub does not**, beyond about half an hour of build.
  `git clone`, `./bridge.sh build`, `./bridge.sh start`. Tier 4 of `docs/running-a-test.md` has
  the states and what each means.

#### Measure the pedals on MetaDrive's own car

The bridge plans in m/s² and turns that into pedals with two 8×11 tables from a *"Town10HD
calibration sweep on Tesla M3"*. Their zero crossing is the CARLA Tesla's own zero-throttle
drag, **−1.582 m/s²**; MetaDrive's car coasts at **−0.364**, because the only resistance in
`_apply_throttle_brake` is a constant brake on all four wheels and there is no aerodynamic
term at all. So every request to slow down more gently than the CARLA car's drag came back as
*throttle* — 58% of them on `junction-1`, 90% on `mosque` — and on `junction-1` the car
accelerated from 13.9 to 20.5 m/s and left the road.

```bash
./pedal-sweep.sh junction-1        # ~9 s, no GPU and no display
```

writes `calibration/metadrive-pedal-map.json`, and then

```bash
python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
```

Measured against the real bridge, both extracts, at 100 Hz / 20 Hz decisions: **no request
below the coast comes back as throttle, and the chosen pedal delivers what was asked for to
0.000 m/s²** — against 1.371 m/s² of error under `pedal` and 0.308 under `accel`.

Three things worth knowing:

- **`--longitudinal` has three values and only one is a calibration.** `pedal` is what the
  bridge emits and what a CARLA consumer gets, so it stays reproducible; `accel` normalises
  `accel_cmd` by the Tesla envelope, which is sign-correct and nothing more; `table` is the
  measurement. Only `pedal` works against `--backend stub`, which answers in pedals and
  carries no `accel_cmd`.
- **The table does not fix speed tracking, and nothing on this side can.** The car still
  averages about 4 m/s against a 10 m/s target, because the bridge is not asking to
  accelerate — median `accel_cmd` −0.30 m/s², and *doubling* the target takes it to −2.00.
  The trajectory it is given is `route_gt.py`'s constant-speed model, so it carries no speed
  intent; that is the model's half of the job, not the controller's.
- **The table describes one car.** `max_engine_force` is sampled from a `BoxSpace`, so the
  file records what it was measured on and every episode checks the live vehicle against it.

### Load the model checkpoint

Whether this machine can run the AV3 model at all, before anything is built around it.
Stage 9 Phase C.1 — it does not drive and writes no file.

```bash
uv sync --group sim --group gpu --group model

cd scripts
./model-probe.sh                                # does the engine load, and what does it cost
./model-probe.sh junction-1 -- --with-simulator # the same, with MetaDrive already on the card
```

It prints what the checkpoint declares about itself — read straight out of the `.ep` archive
before torch is even imported, so a failure to load still says what the file wanted — then
loads it, runs one pass at those shapes, and reports VRAM and timing.

Measured on this machine (RTX 4050, 6141 MiB):

| | model alone | beside `rigs/cams.txt` offscreen |
|---|---|---|
| card used after a warm-up pass | 2617 MiB | 4990 MiB |
| **free** | 3524 MiB | **1151 MiB** |
| forward pass | 1002 ms median | 987 ms median |

Three runs put the median between 947 and 1002 ms; the best single pass seen was 919.


Four things worth knowing before you read that table:

- **It loads, and it will load elsewhere too.** The `.ep`'s 1.2 GB is a *serialized TensorRT
  engine*, not weights, and a TRT engine is normally built for one GPU architecture — so
  "compiled on another machine" was a real risk rather than a formality. This one was built
  `AMPERE_PLUS`, deliberately: it runs on **any NVIDIA card from Ampere onwards** (RTX 30/40/50
  series and the datacentre parts) and refuses below that rather than misbehaving. The probe
  prints which, so you never have to guess for a different checkpoint.
- **A pass takes about a second, which is 20× what a 20 Hz decision has.** Two causes, and
  neither is fixable from this repo: the card is at 100% utilisation but capped at 35 W of a
  60 W maximum, clocking 975–1335 MHz against 3105; and `AMPERE_PLUS` buys its portability by
  giving up card-specific optimisations, which NVIDIA documents as costing speed. A slow
  policy makes a *slow* drive, never a wrong one — `env.step` is the tick — so this is a
  pacing fact, not a correctness one.
- **The output is 20 waypoints, 8 wide** — `[x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]`. The
  openpilot policy sends four, 3-wide, today.
- **`MODEL_CHECKPOINT` in `.env`** points it somewhere else; unset it uses the openpilot fork
  checkout's copy, which is the only one on this machine.


### Put the model at the wheel

Stage 9 Phases C.3 and C.2. The model decides *where to go*; the openpilot bridge still decides
how hard and which way. Until now the "where" was `route_gt.py` — the recorded route resampled
at the car's own current speed — which is a controller test by construction and is why the
bridge asks to slow down almost every tick.

**Check the conversions first, while nothing is steering.** Six things stand between the
simulator and the model — pixels, camera order, frame history, ego speed, route, and the sign of
the waypoints coming back — and **not one of them raises when it is wrong**. A mirrored route
gives a model that loads, runs, returns twenty plausible waypoints and drives into the oncoming
carriageway.

```bash
uv sync --group sim --group gpu --group model

cd scripts
./av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
./av3-probe.sh junction-1 -- --no-model        # conversions 2, 4 and 5 in seconds, no GPU pass
```

The ego is replayed from the tape, so the drive is the tape whatever the model says. It prints
which rig camera fills which model slot and where each one aims, the ego-state pair beside the
raw speed it was built from, the navigation block beside `policy_client`'s own `route` sensor —
two independent projections of the same route, one mirrored — and the predicted waypoints
against where the recorded car really went.

Then drive it, in two terminals, both through the container:

```bash
cd scripts
./sim.sh python3 examples/openpilot_server.py --backend bridge --longitudinal table --port 8642

./sim.sh scripts/drive.sh junction-1 -- \
    --agent-policy remote --policy-url http://127.0.0.1:8642 \
    --sensors imu,route --step-hz 100 --decision-hz 20 --render offscreen
```

`sim.sh` is the wrapper that runs the container as **you** — `compose.yaml` takes the uid from
`DOCKER_UID` and a shell does not export it, so a bare `docker compose run` is uid 1000 whoever
you are. It also settles the interpreter (the image's `python3` is the venv) and the checkpoint
(`MODEL_CHECKPOINT` comes from `MODEL_DIR` in `.env`), which is why neither `uv run`, nor
`METADRIVE_PYTHON=`, nor `--model-checkpoint` appears above.

The host form was `uv run python examples/openpilot_server.py …` beside
`METADRIVE_PYTHON=../.venv/bin/python ./drive.sh …`; it still works on a development machine with
the venv synced, and is what the measurements below were taken on. It is not what to use on a rig.

`--model-checkpoint` — or `MODEL_DIR` in `.env` — implies `--camera-rig rigs/av3.txt`, which is
the rig the weights were trained on. `--waypoints derive` puts the bridge back on the cubic-fit path every measurement
before this was taken on, so those stay reproducible.

Six things worth knowing:

- **`rigs/av3.txt` is generated, not hand-written**, from wing-sim's own
  `validation_invariants.yml` — the rig the weights were trained on. Its header records the
  conversion and the two datum shifts. `rigs/cams.txt` is untouched: every step-timing figure in
  this repo was priced with it, and it cannot be mapped onto the model safely anyway (it carries
  `y: 0.0` on all seven cameras, so its yaw column has nothing to check against, and it names its
  back pair the opposite of its own yaws).
- **It renders 512×384, which is 4:3 on purpose.** The model eats 512×288, and the preprocessing
  gets there by squashing a 4:3 frame vertically — which is what it was trained on. Render 16:9
  natively and the vertical field of view is a third narrower, silently.
- **Four of its six cameras are 105.4° fisheyes and MetaDrive has no fisheye lens.** They render
  rectilinear at wing-sim's own unwarped fallback of 70°, which is what a CARLA run without
  fisheye gives. It is a domain gap, and it is stated rather than hidden.
- **A drive is minutes, not seconds.** About a second a forward pass, one per decision — a
  full-length `junction-1` route at `--decision-hz 20` is 758 of them, so a quarter of an hour.
  `env.step` is the tick, so a slow policy makes a slow drive and never a wrong one.
- **The trajectory half works: the pace doubles.** Against the real bridge on `junction-1` with
  `--longitudinal table`, mean speed goes **4.19 → 8.92 m/s** against a 10 m/s target. The
  bridge's median `accel_cmd` goes *more* negative (−0.30 → −0.504 m/s²) and that is not a
  contradiction — a car at its target speed correctly asks to hold. Speed is the statistic here,
  not the sign of the request.
- **What ends the drive is the lateral, and that is a reading rather than a bug to chase here.**
  Over 40 decisions of `junction-1`'s `test` route the model predicts 16.5 m of travel in 2 s
  where the car covers 24.1, with a 0.12 m median lateral and a standing **+1.6 m rightward
  bias**; the bridge-driven run leaves the route at completion 0.163. That bias is also why the
  waypoint sign cannot be settled from a drive — a constant bias reads exactly like a mirror —
  and why `av3-probe` asks the model directly instead, feeding it a synthetic left-hand and
  right-hand arc with everything else held fixed.
- **It needs this repo's interpreter.** `av3-probe.sh` uses it already; `drive.sh` needs
  `METADRIVE_PYTHON=../.venv/bin/python`, because torch has no Python 3.8 wheel and MetaDrive
  does not need one.
- **And it runs in the container**, which is how it reaches a machine that is not this laptop —
  the same `sim` image as the sweep, because a forward pass and the environment it reads share a
  process. Set `MODEL_DIR` in `.env` to the directory holding the `.ep` — one file, since
  `model_dev.yml` is tracked at `config/model_dev.yml` — and `compose.yaml` mounts it at
  `/models`:

  ```bash
  cd scripts && ./sim.sh scripts/av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
  ```

  `METADRIVE_PYTHON` is already correct in there and must **not** be passed. `docs/container.md`
  has the rest, including why the openpilot bridge stays a separate container.

**`docs/running-a-test.md` is the ladder for checking all of it**, cheapest first — 1 s of
tests, then the rig, then the conversions with no GPU pass, then the model predicting beside a
replayed drive, then the drive itself, then the `--backend stub` control that keeps a wire
regression distinguishable from a model one. Stop at whichever tier answers the question.


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
  scenarionet-10hz/         dataset_summary.pkl, dataset_mapping.pkl, sd_*.pkl
  scenarionet-100hz/        the same routes converted at another rate, if you made one
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
splicing connector centrelines for junction hops), resampled at 10 Hz — or at whatever
`--step-hz` asks for — and written as a synthetic ego car at
`tracks["ego"]["state"]["position"]`. **MetaDrive never reads
`routes.json`** — it reads the pickles, and the route *is* those positions.

With `--actors`, each entry becomes a track of its own beside `tracks["ego"]`, cut to
that scenario's length — so the same plan produces different tracks per route, and an
actor whose walk begins after a short route has already ended is left out of that
route rather than written with no valid frame. Each actor that asked for a crossing
also contributes a `CROSSWALK` polygon to the shared `map_features`, covering the part
of its path that lies on a lane surface. `metadata.counts` reports both as `actors`
and `crosswalks`.

The route builder page previews the same geometry in the browser; Python re-derives
it. The two agree to within 3.5 m over 1.1 km across 40 real routes, and both sides
are deliberately covered by the same test cases (`web/test/route/geometry.test.ts`
and `tests/unit/test_ego_route.py`) — if they ever diverge, the page would offer
drives the converter refuses.

Three pickles land in `scenarionet-<rate>hz/`: one `sd_*.pkl` per scenario, plus
`dataset_summary.pkl` and `dataset_mapping.pkl`. They're written through a custom
pickler because numpy 2 stamps a reference to `numpy._core` into the stream, which
numpy 1.24 on Python 3.8 — the MetaDrive side — cannot resolve. Anything that changes
how arrays are written must keep the stream free of version-specific module names.
MetaDrive is deliberately not a dependency of this package; the schema is pinned by a
test that loads MetaDrive's real `ScenarioDescription` when the checkout is present.

### The browser pages

Every inspection page is a single self-contained HTML file with its data inlined as a
JSON payload — no server, no build step at view time. The four interactive ones (the
Stage 3 reviewer and the Stage 6 route, signal and actor builders) host TypeScript
clients from `web/`, compiled by esbuild into `src/osm_scenario/assets/*.js` and
**committed**, so an installed CLI never needs Node. Each is its own bundle rather
than one shared build: they share no code, and a page should not carry another's.

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
  signal_plan.py      the phase plan       actors.py            the actor plan
  inspection.py, comparison_view.py, validation_view.py, reachability_view.py,
  route_builder_view.py, signal_builder_view.py, actor_builder_view.py
                      the HTML views
  assets/             committed compiled browser clients
web/                  TypeScript sources for those clients
tools/                drive.py, check_dataset.py, signal_control.py,
                      agent_env.py, sensor_survey.py, camera_rig.py, policy_client.py,
                      step_timing.py, geodesy.py   (all run under MetaDrive's venv)
                      model_probe.py, av3_model.py, av3_probe.py   (3.10 only - torch has
                      no 3.8 wheel, and MetaDrive does not need one)
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
