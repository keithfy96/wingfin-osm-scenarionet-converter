# Testing the ROS 2 bags

How to check Stage 10 works, cheapest first. Every tier is a real check rather than a smoke
test — **stop at whichever one answers your question.**

The thing being checked is that a bag written by a simulator is indistinguishable, to whatever
reads it, from one recorded off the vehicle — same container, same topic names, same message
types, same rates — while also carrying the labels a real drive cannot have. **Almost nothing
in that list raises when it is wrong.** A heading 180° out still plots a car on a road. A twist
published in the world frame is exactly correct while the car drives east. A GNSS reading that
skipped MetaDrive's re-centring shift is 93.8 m along a road that really exists. That is why
this ladder exists and why the cheap tiers are worth running before the expensive ones.

**This ladder checks that the bag is right, not that it is complete.** It writes 11 topics, of
which 8 are the rig's — against 45 the rig's bag has and a simulator could honestly produce. The
gap and the plan for closing it are
`docs/implementation-plan/stage-11-a-complete-ros-bag.md`.

Background and measurements: `docs/implementation-plan/stage-10-ros-bags-out-of-a-drive.md`
and `docs/reference/ros-bags.md`. What the simulator can and cannot put on the wire at all is
`docs/rosbag.md`.

Run everything from the repo root.

## What each tier needs

| tier | needs, beyond this repo |
|---|---|
| **0** | nothing — `uv run` and the existing `.venv` |
| **1–3** | a converted dataset (the repo has `junction-1`) and a GPU for the offscreen render |
| **4** | `docker`, and the `ros:jazzy-ros-base` image — 889 MB, and **nothing to build** |
| **5** | `docker compose build` for `wingfin-sim` — cached except one 39 MB layer |
| **6** | `wingfin-ros-viewer` (built by the script), an X display, and a few GB of disk |

**The one thing that catches people out is the interpreter.** `scripts/ros-bag.sh` runs
`drive.py` on `METADRIVE_PYTHON`, which defaults to the MetaDrive checkout's **Python 3.8**,
and `rosbags` has no 3.8 wheel. This repo's own `.venv` is 3.10 and already carries both
`metadrive` and `rosbags`, so every command below names it. Two other ways round it, if you
prefer: the container (tier 5, which carries the `ros` group as of 2026-09-01), or exporting
`METADRIVE_PYTHON` in `.env`.

> **Do not run a bare `uv sync`.** It removes the `sim` and `ros` groups that are already in
> place — naming one group alone removes the others. Nothing here needs installing.

---

## 0. The translation tests (~10 s)

```bash
uv run pytest tests/unit/test_ros_schema.py && uv run ruff check .
```

32 tests over `tools/ros_schema.py`, which imports neither MetaDrive nor `rosbags` and is
therefore the one part of Stage 10 that is plain testable Python: signs, stamps, frames,
units, the topic table, and the camera-mount conversion checked against `rigs/cams.txt`
parsed for real.

**Expect** `32 passed`, then `All checks passed!`.
**Writes** nothing.

**Proves** the rules are right. **Does not prove** anything ever reached a file.

---

## 1. A real bag off a real drive (~2 min)

```bash
METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- --out bags/j1-lights
```

**One line, and it is long on purpose.** It wraps in the terminal, and a wrapped line pastes as
one command; a `\` continuation does not, if a single space follows the backslash or the copied
region caught a right-hand prompt. That is not hypothetical - it is why the first run of this
runbook produced nothing at all.

**Within a second you should see five lines**, the last an absolute path. If you see none, and
the exit status is non-zero, something died before the first `note` — run it again under
`bash -x` and read the last line:

```
  workspace  workspaces/junction-1
  dataset    scenarionet-10hz  (1 scenario(s))
  tracks     CYCLIST=25 PEDESTRIAN=101 TRAFFIC_BARRIER=24 VEHICLE=1
  lights     8 in the dataset
  out        /.../wingfin-osm-scenarionet-converter/bags/j1-lights
```

`out` is absolute on purpose: `--out` resolves against the current directory and nothing in the
script changes directory, so a relative name is unambiguous to the script and not to whoever
reads the scroll-back an hour later.

Before a frame is written, the preflight reads the dataset and says what is actually in it —
because a bag whose traffic-light topic is empty for want of a `convert --signals` is, months
later, indistinguishable from a junction that genuinely had none.

**Expect** on `junction-1`: `PEDESTRIAN=101 CYCLIST=25 TRAFFIC_BARRIER=24`, **`lights 8`**,
`gnss  real lat/lon available`, and at the end
`ros bag  ~364 frames, ~3641 messages across 11 topics`.

**Writes** `bags/j1-lights/` and nothing else. A drive writes no reports unless you pass
`--record` or `--export-drive`, and neither is used here.

**Proves** `tools/ros_frame.py` — the one module `uv run pytest` cannot cover, because it
needs a live engine.

---

## 2. Read it back, two independent ways (~20 s)

**Tier 1 must have run first.** Both commands *read* a bag; neither creates one.

```bash
./scripts/ros-bag.sh --audit bags/j1-lights
uv run python tools/ros_probe.py bags/j1-lights --workspace workspaces/junction-1
```

`ros_audit.py` is a deliberate re-implementation of the method `bag_audit.html` uses on the
rig's own bag: parse the MCAP summary, read every `MessageIndex` record, and derive per-topic
rates **without decompressing a byte of payload**. It imports no mcap library and no ROS. If
it runs on a simulated bag and produces the same shape of report, the container is *provably*
the rig's, not merely readable by the library that wrote it.

`ros_probe.py` checks **relationships between two independently produced quantities**, never a
value against a constant — the heading against the direction the car actually moved, the twist
against the change in position, `/tf` against the odometry it was derived beside. Any one of
those can be wrong in isolation and look fine; they cannot all agree while any is wrong.

**Expect** from the audit: `32 chunks`, `compression=['zstd']`, every channel at
`10.00 Hz / 100.00 ms` median. From the probe: `all 13 checks passed`, with GNSS spanning lat
`3.1842..3.1864`, lon `101.6110..101.6124` — Kuala Lumpur, on the junction.

**Writes** nothing; both print to the terminal.

**Check those figures against `docs/reference/ros-bags.md`.** That file records what was
measured on 2026-08-31, and a disagreement is the finding — it exists to be contradicted.

---

## 3. The tape bound (~4 min)

```bash
METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- --out bags/j1-tape --agent-policy idm --extra-seconds 40

METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- --out bags/j1-past-tape --agent-policy idm --extra-seconds 40 --ros-bag-past-tape
```

A self-driven car outruns the recording it is driving over. Past the last recorded frame
MetaDrive removes **every** replayed pedestrian and cyclist, including ones mid-crossing,
while deliberately keeping cones and barriers.

**Expect** the first to run ~824 steps but stop the bag at ~379, and say why. The second
records all ~824.

The difference — **445 frames, 54%** — is a busy junction rendering deserted. Those frames are
not *mislabelled*: the boxes still match the pixels. They are **unrepresentative**, which is
worse, because nothing downstream can detect it. Training on them teaches a model this
junction has no people in it.

**Writes** `bags/j1-tape/` and `bags/j1-past-tape/`.

---

## 4. ROS's own reader (~10 s, no build)

```bash
docker run --rm -v "$PWD/bags:/bags:ro" ros:jazzy-ros-base ros2 bag info /bags/j1-lights
```

The strongest check available, and it costs nothing: `ros:jazzy-ros-base` needs no build and
nothing added to our own images. This is ROS's own rosbag2 reading a file written by an
unrelated third-party library — **the only check here that depends on neither `rosbags` nor
our code being correct.**

**`jazzy` is load-bearing, not an arbitrary tag.** `rosbags` writes rosbag2 **format v9**, and
humble's rosbag2 cannot parse a v9 `metadata.yaml` at all — it fails on the manifest before it
reaches a single message. Swapping the tag turns this tier from the strongest check into a
confusing failure that says nothing about our data.

Two things it establishes rather than assumes: our `Writer(version=9)` metadata against what
jazzy expects, and whether `info` lists topics whose message packages (`vision_msgs`,
`wingfin_msgs`) are absent from `ros-base`. MCAP embeds the schema text, so it should — but
that is a claim to test, not to state.

**Writes** nothing; `/bags` is mounted read-only.

---

## 5. The same ladder, in the container (~3 min)

```bash
./scripts/sim.sh --no-model ./scripts/ros-bag.sh junction-1 -- --out bags/j1-container
./scripts/sim.sh --no-model ./scripts/ros-bag.sh --audit bags/j1-container
./scripts/sim.sh --no-model uv run python tools/ros_probe.py bags/j1-container --workspace workspaces/junction-1
```

This is the environment a rig would use, and the only one where MetaDrive and `rosbags` share a
single interpreter rather than being two venvs that have to agree. Tiers 1 and 2 on the host
prove the code; this proves the image.

**`--no-model` is required and its absence is not obvious.** `compose.yaml` always sets
`MODEL_CHECKPOINT` to the mounted `/models` path, `drive.py` takes it as the default for
`--model-checkpoint`, and a checkpoint implies `--agent-policy remote` — so without it a replay
drive refuses with *"needs --agent-policy remote, not replay"* before it opens a bag. Exporting
an empty `MODEL_CHECKPOINT` does not help: compose's `${MODEL_CHECKPOINT:-...}` substitutes its
default for an empty value too. `sim.sh --no-model` passes `-e MODEL_CHECKPOINT=` into the
container, which is the only thing that clears it.

**Expect the host's figures, exactly**: 364 frames, 3,641 messages, 11 topics, 32 chunks,
`compression=['zstd']`, all 13 probe checks. The `.mcap` files differ by about 0.1% in size —
physics is not bit-reproducible across the two environments — but every count, rate and check
matches, and a difference in any of *those* is the finding.

**If it refuses with `--ros-bag needs the ros dependency group`**, the image predates
2026-09-01. Rebuild with `docker compose build`; only the last 39 MB layer is uncooked, so it
takes about a minute. `./scripts/sim.sh` warns about this on every run by comparing the image's
`wingfin.groups` label against the Dockerfile.

**Writes** `bags/j1-container/`, owned by your own uid — compose runs the container as
`DOCKER_UID`, so nothing lands root-owned.

---

## 6. Look at it (~1 min)

```bash
./scripts/ros-view.sh bags/j1-lights
```

**Every other tier is numeric. This is the only one that is not**, and on its first run it found
two faults that all 13 probe checks pass straight over.

> **Partly completed, 2026-09-02.** Keith ran the viewer and confirmed the route draws where the
> car goes. Every display subscribes — `ros2 topic info` reports 1 subscriber each on `/tf`,
> `/localization/odometry`, `/planning/route`, `/perception/objects`,
> `/perception/traffic_lights` and `/perception/traffic_lights/markers` — with no QoS warnings
> and no rviz warnings on a single pass of `bags/j1-lights`.
>
> **Still not done: the boxes, the lights, and a screenshot.** Nobody has watched a box track a
> pedestrian or watched the eight signals cycle, and there is no artefact anyone who was not
> there can check. See
> `docs/fixes/2026-09-01-20:19:21-phase-b-was-marked-done-on-log-silence.md`.

rviz2 plays the bag **once**, 36 seconds, with `use_sim_time` and a 4-second head start; add
`--loop` to repeat. What to look for — each is a claim no number in tier 2 makes, and each fails
with a completely clean log:

- **the car crawls along the road rather than teleporting** between frames
- **the boxes sit *on* the road and move *with* the people**, not hovering, not a frame behind —
  up to 132 of them on `junction-1`, pedestrians, cyclists and barriers
- **the route runs where the car actually went**
- **`base_link` stays under `map`** and the axes point the way the car is going
- **no two conflicting lights are green at once.** Eight spheres cycle at the junction, six
  together and two opposite. **No numeric check anywhere can catch this**, because the bag does
  not carry which movements conflict — this tier is the only thing between a broken signal plan
  and a training set

Set the view's **Target Frame** to `base_link` so the camera rides with the car: a constant lag
or a z-offset is obvious from on board and invisible from a fixed viewpoint.

**The lights are markers, not the typed topic.** rviz2 has no plugin for `wingfin_msgs`, so the
viewer image runs `light_markers.py`, which republishes `/perception/traffic_lights` as
`visualization_msgs/MarkerArray` — a sphere per light coloured by its state, with the word above
it. Display only; the bag keeps the real typed topic.

The container is jazzy and builds `wingfin_msgs` from `ros_schema.EXTRA_DEFINITIONS`; it installs
`vision_msgs` **and** `vision-msgs-rviz-plugins`, because with the types but no plugin rviz2
subscribes to `/perception/objects` and silently draws nothing.

**What it caught, first run:**

- **Every pose claimed to be invalid.** Odometry, twist and every detection carried
  `covariance[0] = -1`. That is `sensor_msgs/Imu`'s "I do not produce this quantity", not
  "I am unsure of it" — so a consumer is being told to discard a pose that is exact ground truth.
  It is also not positive-semidefinite, which is how it surfaced: rviz2 logged
  `Negative eigenvalue found for position` once a frame and drew no ellipse. `NavSatFix` in the
  same file had it right all along, with zeros and `position_covariance_type: 0`.
- **The route never drew, and it took two fixes.** `/planning/route` is one message at t=0. The
  rviz config asked for Transient Local because the topic is latched; the bag recorded it
  volatile, and DDS answers an incompatible request by delivering nothing —
  `No messages will be sent to it`, once, from both ends. `ros_bag._latched_qos` fixed the
  writer, and **that alone was not enough**: `ros2 bag play` does not serve a latched message
  from a durability cache to a late joiner (measured, 3 trials of 3, nothing received — and now
  with no warning either, because the QoS matches). `--delay 4` is what delivers it, by holding
  the first message until every subscriber is up. Compatible QoS and a head start are both
  required.

**Two warnings that are not faults:**

- **`DURABILITY_QOS_POLICY ... No messages will be sent to it`, from both the player and rviz2**
  — you are playing a bag recorded before the latched-QoS fix. The route will not draw; nothing
  else is affected. Play `bags/j1-lights`. This warning is *new diagnostic value*: before the
  fix, a stale bag was silently indistinguishable from a good one.
- **`TF_OLD_DATA`, then `Detected jump back in time. Clearing TF buffer`** — only under
  `--loop`, once per lap, when the bag's clock restarts at zero. On a single pass it should not
  appear at all.

**It does not exit when the bag ends, and that is deliberate.** `ros2 bag play` runs in the
background and `rviz2` is `exec`'d in the foreground, so the container lives exactly as long as
the window — otherwise the view would vanish 36 seconds in, before anyone could pause or ride
along. Close the window, or Ctrl-C. From a terminal the player's keys are live: **SPACE**
pause/resume, **→** step one message, **↑/↓** rate ±10%. For an unattended run, wrap it:
`timeout 60 ./scripts/ros-view.sh bags/j1-lights`.

**Writes** nothing. The bag is mounted read-only.

## Where the outputs are

| path | what | size |
|---|---|---|
| `bags/j1-lights/` | the bag — a directory, not a file | 524 KB |
| `bags/j1-lights/metadata.yaml` | rosbag2 v9 manifest: topics, types, counts, `storage_identifier: mcap` | 4.7 KB |
| `bags/j1-lights/*.mcap` | every message, zstd per chunk | 509 KB |

Measured on `bags/j1-lights`, and **the size follows the dataset, not the recorder.**
`/perception/objects` is 94% of the payload — 17.2 MB uncompressed across 364 frames, at a median
of 98 detections a frame — so a re-convert that changes how many pedestrians, cyclists and
barriers `junction-1` holds moves this figure with it. Bags recorded before the 2026-09-02
re-convert ran to 964 KB on 200 detections a frame. Re-quote from the file rather than trusting
the row.

- **`bags/` does not exist until the first run** — the writer creates it, parents and all.
- **`bags/` is gitignored** (`.gitignore:38`, with `*.mcap` and `*.mcap.zstd`), because a
  camera bag at rig scale is tens of gigabytes and `git add -A` is a habit here.
- **Both `--audit` and `ros_probe.py` take the directory**, not the `.mcap` inside it.
- **Everything except the drives writes nothing at all.**

---

## If a tier fails

- **No output at all, exit 1, and no `bags/` directory** — this was a real bug in
  `scripts/ros-bag.sh`, fixed on 2026-09-01, and it is worth knowing what it looked like because
  the symptom is *nothing*. The argument loop skipped past `--out` with `((i++))`, and a bare
  `((expr))` returns exit status 1 when the expression evaluates to zero; post-increment
  evaluates to the **old** value, so with `--out` first after `--` (`i == 0`) `set -e` killed the
  script before its first `note`. The documented command was the one that could not work. If you
  ever see this shape again — exit non-zero, not one byte of output — reach for `bash -x` first;
  it names the last line executed in about a second.
- **`no bag at <path> - nothing has written one there yet`** — tier 2 reads what tier 1 writes.
  The message names the command that writes one and the absolute path it will write to.
- **`--ros-bag needs Python 3.10 ... and this is 3.8`** — the interpreter. Prefix the command
  with `METADRIVE_PYTHON=.venv/bin/python`, as every command above does.
- **`No module named 'rosbags'` on a 3.10 interpreter** — a bare `uv sync` removed the group.
  `uv sync --group sim --group ros`, naming both.
- **`bags/j1-lights already exists`** — a bag is a recording, not an output file to overwrite.
  Pick another name or remove it deliberately.
- **`you asked for --lights, and this dataset has no traffic lights in it`** — correct, and
  see the next section. It is a refusal rather than an empty channel on purpose.
- **The probe's heading or twist check fails** — a sign convention, not rounding. MetaDrive is
  ENU and left-positive; ROS `base_link` is x forward, y left, `+yaw` left. Everything sideways
  flips together or nothing does.
- **The probe's GNSS containment fails by roughly 90 m** — `old_origin_in_current_coordinate`
  was skipped. It is 93.8 m on junction-1, and it is a numpy array, so
  `metadata.get(...) or (0.0, 0.0)` raises rather than defaulting.
- **`ros2 bag info` cannot open the bag** — check the audit reported `compression=['zstd']`
  and not a `.mcap.zstd` file. `CompressionMode.FILE` compresses the whole file and destroys
  the index; only `STORAGE` matches the rig.

---

## What is not checked here

- **`mosque` still has no lights.** `junction-1` gained 8 on 2026-09-02; `mosque` has a
  `signals/signals.json` that was never converted in, so `--lights` is still refused there:

  ```bash
  uv run osm-scenario convert -w workspaces/mosque --config config/default.yaml --routes workspaces/mosque/routes/routes.json --signals workspaces/mosque/signals/signals.json --actors workspaces/mosque/actors/actors.json
  ```

  Convert-time arguments are deliberately not `ConverterConfig` fields, so this does **not**
  move `generation_fingerprint` and the Stage 3 lane review keeps applying. It does rewrite the
  workspace's dataset, which is tracked in git.

- **No pixels.** `image_raw/ffmpeg` is 6 of the rig's 55 topics and the encoder is not written —
  no picture has ever reached a bag. Everything *around* the pictures now has: as of stage 11
  phase 1 a `--camera-rig` drive writes the six `camera_info_latched` topics and the `/tf_static`
  transform tree, so a consumer has each camera's intrinsics and its mount before a single frame
  exists. What is missing is the encoding, not the geometry.

- **`rigs/cams.txt` disagrees with itself about which way two cameras face**, and the bag reports
  that rather than picking a side. Its front pair reads `+yaw` as right and its back pair reads it
  as left, so `cam_back_left` publishes as `rear_left` and is mounted aiming rear-**right**.
  `drive.py` prints a `NAME/AIM` line as the recording starts and `ros_probe.py` prints one off
  the bag; `/tf_static` carries the geometry, so follow `frame_id` rather than the topic name.
  `rigs/av3.txt` has no such rows.

- **24 of the rig's 55 topics stay omitted** for want of a `.msg` definition
  (`ros_schema.MISSING_DEFINITIONS`; `tools/ros_probe.py --coverage` counts them), omitted rather than published under a substitute type: a
  subscriber deserialising `wingfin_msgs/VehicleState` fails on a `geometry_msgs/TwistStamped`
  wearing that topic name, which is worse than an absent topic. **They are recoverable from one
  `.mcap` off the rig**, because rosbag2 writes each type's `.msg` text into the bag itself:
  `uv run python tools/ros_defs.py <bag>` prints them ready to paste. Run it on `bags/j1-lights`
  and it finds 27 definitions and nothing new, which is the right answer for a bag written from
  our own table - and is the self-test that says the rig's will come out the same way.

- **Every channel is truth, not measurement**, and the bag says so — its `wingfin` metadata
  records `source: simulated, noise_model: none`. The rig's GNSS has noise, lag, multipath and
  dropouts; ours is a perfect number every frame.
