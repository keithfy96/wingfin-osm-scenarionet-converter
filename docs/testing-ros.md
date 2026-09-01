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

**The one thing that catches people out is the interpreter.** `scripts/ros-bag.sh` runs
`drive.py` on `METADRIVE_PYTHON`, which defaults to the MetaDrive checkout's **Python 3.8**,
and `rosbags` has no 3.8 wheel. This repo's own `.venv` is 3.10 and already carries both
`metadrive` and `rosbags`, so every command below names it. Two other ways round it, if you
prefer: `./scripts/sim.sh` once the container carries the `ros` group, or exporting
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
METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- --out bags/j1-001
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
  tracks     CYCLIST=49 PEDESTRIAN=202 TRAFFIC_BARRIER=49 VEHICLE=1
  lights     0 in the dataset
  out        /.../wingfin-osm-scenarionet-converter/bags/j1-001
```

`out` is absolute on purpose: `--out` resolves against the current directory and nothing in the
script changes directory, so a relative name is unambiguous to the script and not to whoever
reads the scroll-back an hour later.

Before a frame is written, the preflight reads the dataset and says what is actually in it —
because a bag whose traffic-light topic is empty for want of a `convert --signals` is, months
later, indistinguishable from a junction that genuinely had none.

**Expect** on `junction-1`: `PEDESTRIAN=202 CYCLIST=49 TRAFFIC_BARRIER=49`, **`lights 0`**,
`gnss  real lat/lon available`, and at the end
`ros bag  ~364 frames, ~3641 messages across 11 topics`.

**Writes** `bags/j1-001/` and nothing else. A drive writes no reports unless you pass
`--record` or `--export-drive`, and neither is used here.

**Proves** `tools/ros_frame.py` — the one module `uv run pytest` cannot cover, because it
needs a live engine.

---

## 2. Read it back, two independent ways (~20 s)

**Tier 1 must have run first.** Both commands *read* a bag; neither creates one.

```bash
./scripts/ros-bag.sh --audit bags/j1-001
uv run python tools/ros_probe.py bags/j1-001 --workspace workspaces/junction-1
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
`10.00 Hz / 100.00 ms` median. From the probe: `all 10 checks passed`, with GNSS spanning lat
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
docker run --rm -v "$PWD/bags:/bags:ro" ros:jazzy-ros-base ros2 bag info /bags/j1-001
```

The strongest check available, and it costs nothing: `ros:jazzy-ros-base` needs no build and
nothing added to our own images. This is ROS's own rosbag2 reading a file written by an
unrelated third-party library — **the only check here that depends on neither `rosbags` nor
our code being correct.**

Two things it establishes rather than assumes: our `Writer(version=9)` metadata against what
jazzy expects, and whether `info` lists topics whose message packages (`vision_msgs`,
`wingfin_msgs`) are absent from `ros-base`. MCAP embeds the schema text, so it should — but
that is a claim to test, not to state.

**Writes** nothing; `/bags` is mounted read-only.

---

## Where the outputs are

| path | what | size |
|---|---|---|
| `bags/j1-001/` | the bag — a directory, not a file | ~1 MB |
| `bags/j1-001/metadata.yaml` | rosbag2 v9 manifest: topics, types, counts, `storage_identifier: mcap` | ~4 KB |
| `bags/j1-001/*.mcap` | every message, zstd per chunk | ~1 MB |

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
- **`bags/j1-001 already exists`** — a bag is a recording, not an output file to overwrite.
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

- **Traffic lights have never once run.** `/perception/traffic_lights` and
  `wingfin_msgs/TrafficLightArray` are built and unit-tested, but `dynamic_map_states` is 0 in
  both datasets, so no bag has ever carried one — which is why tier 1 reports `lights 0` and
  why `--lights` is refused. `junction-1` has a `signals/signals.json` that was never
  converted in:

  ```bash
  uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml --routes workspaces/junction-1/routes/routes.json --signals workspaces/junction-1/signals/signals.json --actors workspaces/junction-1/actors/actors.json
  ```

  Convert-time arguments are deliberately not `ConverterConfig` fields, so this does **not**
  move `generation_fingerprint` and the Stage 3 lane review keeps applying. It does rewrite
  `workspaces/junction-1/scenarionet-10hz`, which is tracked in git.

- **Nobody has looked at a bag in rviz2.** Every check above is numeric, and `ros-base`
  carries no rviz2.

- **No cameras.** `image_raw/ffmpeg` is 18 of the rig's 55 topics and the encoder is not
  written — no pixel has ever reached a bag. The mount conversion and `/tf_static` are built
  and tested, so what is missing is the encoding, not the geometry.

- **15 of the rig's 55 topics stay omitted** for want of a `.msg` definition
  (`ros_schema.MISSING_DEFINITIONS`), omitted rather than published under a substitute type: a
  subscriber deserialising `wingfin_msgs/VehicleState` fails on a `geometry_msgs/TwistStamped`
  wearing that topic name, which is worse than an absent topic.

- **Every channel is truth, not measurement**, and the bag says so — its `wingfin` metadata
  records `source: simulated, noise_model: none`. The rig's GNSS has noise, lag, multipath and
  dropouts; ours is a perfect number every frame.
