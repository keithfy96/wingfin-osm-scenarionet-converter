# ROS 2 bags out of a drive - what runs, and what it measured

What Stage 10 does today, with the numbers. The plan and the reasoning are in
`docs/implementation-plan/stage-10-ros-bags-out-of-a-drive.md`; this file is the measurements,
so nothing here has to be re-derived to answer "what did it actually do".

Everything below was produced by running it on 2026-08-31, against
`workspaces/junction-1/scenarionet-10hz` and `bag_audit.html`'s audit of the rig's own
`ros2_mig_phase_5_p1`.

The ladder for checking it — five tiers, cheapest first, and what each failure means — is
`docs/testing-ros.md`. This file is the baseline those tiers are checked against.

## Running it

```bash
./scripts/ros-bag.sh junction-1 -- --out bags/junction-1-001
./scripts/ros-bag.sh --audit bags/junction-1-001
uv run python tools/ros_probe.py bags/junction-1-001 --workspace workspaces/junction-1
```

Or straight at `drive.py`, which is what the script wraps:

```bash
uv run python tools/drive.py workspaces/junction-1/scenarionet-10hz \
    --render none --ros-bag bags/junction-1-001
```

The four modules and what each may import:

| | imports MetaDrive | imports rosbags | unit tested |
|---|---|---|---|
| `tools/ros_schema.py` | no | no | yes - 32 tests |
| `tools/ros_frame.py` | yes | no | the pure parts |
| `tools/ros_bag.py` | no | yes | via the probe |
| `tools/ros_audit.py` | no | **no** - reads the bytes | via the probe |

## The bag it wrote

A default replay drive of junction-1, 364 frames:

```
0.97 MB on disk · 32 chunks · compression=['zstd'] · payload 34.98 MB · 39.44x
3641 messages across 11 channels
```

Every channel at **10.00 Hz, 100.00 ms median interval**. 11 rather than 12 because `/tf_static`
is only written when a `--camera-rig` is mounted.

| | this bag | the rig's `ros2_mig_phase_5_p1` |
|---|---|---|
| container | MCAP · zstd · 32 chunks | MCAP · zstd · 2,165 chunks |
| chunk size | ~1.15 MB (the writer's default) | ~3.4 MB |
| messages | 3,641 | 1,466,940 |
| channels | 11 | 55 |
| compression | 39.4x | 1.42x |
| ground-truth objects | 300 ids, 51-257 boxes a frame | none |

**The compression ratios are not comparable and the difference is not a win.** The rig's payload
is 87.3% lidar; ours is small structured messages with a great deal of repetition, which zstd
eats. Once cameras land, the bulk becomes already-compressed ffmpeg packets and the ratio will
fall toward the rig's.

## What the probe measured

All 10 checks, on the bag above:

| check | result |
|---|---|
| every per-frame topic holds the same number of messages | 364 each, 10 topics |
| time strictly increasing, no repeated frame | 364 messages, 364 distinct stamps |
| every frame's topics share one identical stamp | **364 of 364** |
| `/tf` and `/localization/odometry` agree | worst **0.00e+00** |
| heading points along the direction of travel | worst **0.0 deg** over 363 moving frames |
| twist is in the car's frame and matches its motion | worst 0.30 m/s |
| objects were labelled | 300 ids; `CYCLIST`, `PEDESTRIAN`, `TRAFFIC_BARRIER` |
| every box has a real size | pass |
| the fix moves with the car | lat 3.184159..3.186394, lon 101.611005..101.612379 |
| every fix lands on the map | **364 of 364** within 50 m of the OSM extent |

Each is a *relationship between two independently produced quantities*, not a value against a
constant, because every fault in this area produces a bag that opens and renders and is wrong.

## The tape bound

`--agent-policy idm --extra-seconds 40` on junction-1:

```
824 of 824 steps (379 recorded frames at 0.1 s)
note: 824 steps against a 379-frame recording. 251 recorded pedestrian(s) and
      cyclist(s) were removed there (cones and barriers stay).
ros bag      379 frames ... stopped at step 379 of 824
```

**445 of 824 frames - 54% - would have shown a junction emptier than the scenario has.** They
would not have been mislabelled; the boxes still match the pixels. They are unrepresentative,
and training on them teaches a model this junction has no people in it.
`--ros-bag-past-tape` keeps them.

## Compression modes, measured on a 16 MB bag

| mode | chunks | chunk compression | file |
|---|---|---|---|
| `NONE` | 12 | `<none>` | `bag.mcap` |
| **`STORAGE`** | 12 | **`zstd`** | `bag.mcap` |
| `FILE` | - | - | `bag.mcap.zstd`, index unreadable without inflating |
| `MESSAGE` | 12 | per message | **larger than no compression** |

`STORAGE` is not in the published `rosbags` docs, which list only `file` and `message`. It is in
the installed release and it is the only one that matches the rig.

## What the datasets actually hold

Read out of the pickles, not assumed:

| | `mosque/scenarionet-10hz` | `junction-1/scenarionet-10hz` |
|---|---|---|
| pedestrians / cyclists / static | 2 / 1 / 1 cone | **101 / 25 / 24 barriers** (was 202/49/49 before the 2026-09-02 re-convert) |
| traffic lights (`dynamic_map_states`) | **0** | **0** |
| `coordinate_system_wkt` | yes | yes |

junction-1 has a `signals/signals.json` that was never converted in. Until it is,
`--lights` is refused by `scripts/ros-bag.sh` rather than recording an empty channel:

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
    --routes workspaces/junction-1/routes/routes.json \
    --signals workspaces/junction-1/signals/signals.json \
    --actors workspaces/junction-1/actors/actors.json
```

Convert-time arguments are deliberately not `ConverterConfig` fields, so that run does not move
`generation_fingerprint` and the Stage 3 review keeps applying.

## The rig's 55 topics against what MetaDrive can produce

**24 direct, 21 synthesisable, 10 impossible without fabricating.** The full table is in the
plan doc. The 24 omitted for want of a `.msg` definition are listed in code, in
`ros_schema.MISSING_DEFINITIONS` - derived from `ros_schema.RIG_TOPICS`, and printed with the
rest of the ledger by `uv run python tools/ros_probe.py --coverage` - with what each needs - they are omitted rather than published
with a substitute type, because a subscriber expecting `wingfin_msgs/VehicleState` fails on a
`geometry_msgs/TwistStamped` wearing that topic name, which is worse than an absent topic.

**"Type not in the audit" was the wrong place to look, and none of them is actually unobtainable.**
`bag_audit.html` records rates; the only message type named anywhere in it is
`geometry_msgs/TwistStamped`. But rosbag2 writes every type's full `.msg` text into the bag
itself - which is the property that made it safe to invent `wingfin_msgs/TrafficLight` in the
first place - so the rig's own `ros2_mig_phase_5_p1` bag carries the exact bytes for all fifteen.
`tools/ros_defs.py` (stage 11, phase 0a) splits a connection's concatenated definition into one
pasteable `EXTRA_DEFINITIONS` entry per type, normalises `MSG: geometry_msgs/Point` to the
three-part spelling, and parses each one before printing it. Measured on `bags/j1-lights`: 27
definitions across 11 connections, 7 outside the humble typestore, all 7 already known, so it
prints nothing new. The input is **one `.mcap` file**, not the rig running and not the wingfin
source package. Its one honest failure: a definition is written by the recorder, so a bag whose
writer supplied none carries none - those topics come back under "no definition recorded".

## The camera topics, measured 2026-09-02 (stage 11 phase 1)

`sensor_msgs/CameraInfo` is core, so the six `camera_info_latched` topics needed no message
definition - only a rig. They are declared always and written only on a `--camera-rig` drive,
exactly like `/tf_static`, which had never once reached a bag until this.

```bash
METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- \
    --out bags/phase1-cams --camera-rig rigs/cams.txt --render offscreen
```

| | without a rig | with `rigs/cams.txt` |
|---|---|---|
| messages | 3,641 | **3,648** |
| channels | 11 | **18** |
| `camera_info_latched` | 0 | 6, one message each |
| `/tf_static` | absent | 1 message, **7** transforms |
| coverage | 7 / 45 (14 declared) | **14 / 45** |

Seven transforms against six topics, and the difference is deliberate: `cams.txt`'s seventh
camera `cam_front_wide` has no channel on the vehicle, so it is mounted, rendered and in the
transform tree, and given no `cam_sync_rig` topic. Inventing one would put a channel in our bag
that the rig's bag cannot have.

**The intrinsics, recovered from `k` rather than read off the spec:** `f=365.6 px`, `fov 70.0 deg`
horizontal and `43.0 deg` vertical, on all six 512x288 cameras. That is `(width/2) / tan(fov/2)`
back out again, and it is the check worth having - `camera_rig.mount` sets the lens with panda3d's
one-argument `setFov`, which is the **horizontal** angle, and reading it as vertical on a 16:9
frame is a 1.78x error in `fx` that changes no picture and reprojects every box wrong.

`d` is empty against an empty `distortion_model`, not `plumb_bob` with five zeros: five zeros
would claim a calibration was performed and came out perfect, which is the same untruth
`UNKNOWN_COVARIANCE` exists to avoid.

`ros_probe` on that bag: **all 16 checks passed**, two of them new - every `camera_info` latched
at exactly one message, and every one joining a `/tf_static` transform by `frame_id`. A camera
present in one and absent from the other is a half-converted rig, and both topics deserialise
perfectly on their own, so nothing else notices.

### The two cameras whose name and aim disagree

`rigs/cams.txt` reads `+yaw` as **right** on its front pair and as **left** on its back pair, so
two of its four side cameras are named backwards under either reading. MetaDrive's own rig
description says so independently, from the same file:

```
cam_back_left   H -125.0  aims 125 deg to the right, i.e. rear-right
cam_back_right  H +125.0  aims 125 deg to the left,  i.e. rear-left
```

So `cam_back_left` publishes on `.../rear_left/camera_info_latched` and is mounted aiming
rear-right. **This is reported, never corrected.** Renaming the topic would contradict the spec's
own labels; rotating the mount would put a camera somewhere the drive never rendered from. Three
sources, each right about something different: `RIG_CAMERA_NAMES` follows the labels, `/tf_static`
carries the geometry, and `camera_side_disagreements` names every camera where they part company -
printed by `drive.py` as the recording starts and by `ros_probe.py` off the bag. Exactly two rows
on `cams.txt`; **none on `rigs/av3.txt`**, whose header claims both columns agree by construction
and which is now tested for it.

Follow `frame_id` into the transform, not the topic name.

## What a full ladder run measured, 2026-09-01

Every tier of `docs/testing-ros.md`, run end to end on `junction-1` a month after the figures
above were first taken. **They reproduced exactly** - 364 frames, 3,641 messages, 32 chunks,
39.4x, all 10 probe checks (13 since the lights landed), every channel at 10.00 Hz.

| tier | result |
|---|---|
| 0 - unit tests | 32 passed; ruff clean |
| 1 - a real bag | 364 frames, 3,641 messages, 11 topics, 0.97 MB / 34.98 MB payload |
| 2 - audit + probe | 32 chunks, `compression=['zstd']`, **all 10 checks passed** |
| 3 - tape bound | **379 frames written of 824 steps driven**; `--ros-bag-past-tape` wrote 824 |
| 4 - refusals | all three refuse, and now none of them tracebacks |
| 5 - `ros2 bag info` | **jazzy read all 11 topics and every count**, `wingfin_msgs` included |

**`ros2 bag info` is the result worth keeping.** ROS's own rosbag2, in a stock
`ros:jazzy-ros-base`, opened a bag written by `rosbags` and listed
`wingfin_msgs/msg/TrafficLightArray` - a type that exists nowhere but this repo, with no package
installed anywhere in that container. MCAP carries the schema text, so the invented message is
as readable as `nav_msgs/msg/Odometry`. It reports `ROS Distro: rosbags` and
`Storage id: mcap`, and the duration starts at epoch zero because the stamps are simulator time.

Two things the run turned up that reading could not:

- **`scripts/ros-bag.sh` had never worked as documented.** Its argument loop skipped past `--out`
  with `((i++))`; a bare `((expr))` returns status 1 when the expression is zero, and
  post-increment yields the **old** value, so `--out` in first position (`i == 0`) tripped
  `set -e` and the script died silently - exit 1, no stdout, no stderr, no bag. Every example in
  the README, the script's own help and `docs/testing-ros.md` put `--out` first. Fixed to
  `i=$((i + 1))`.
- **`--agent-policy idm` on junction-1 does not move.** Tier 3's drives report
  `completion 0.001` and the probe finds **one** moving frame in 379. The bags are correct and
  the tape bound is exactly as documented; the ego is a separate question, and the probe's
  heading check passes vacuously on a car that never moved.

## The container, measured 2026-09-01

`wingfin-sim` synced `sim gpu model` and **not `ros`** from the day it was built, so `rosbags`
was absent and every bag the container was pointed at was refused - while `ros-bag.sh`'s header,
the interpreter trap below and `refuse_if_unsupported`'s own message all named the container as
the way out of the host's 3.8. Right about the interpreter, wrong about the library.

Fixed by a second `uv sync` layer below the big one, so nothing above it rebuilds:

| | |
|---|---|
| image | 13.2 GB -> **13.3 GB** |
| the `ros` group | rosbags 0.11.5, apsw, lz4, ruamel-yaml, zstandard - **39.3 MB, installed in 0.8 s** |
| interpreter | Python **3.10.21**, numpy 2.2.6, importable as the non-root runtime uid |

The ladder then ran in there and **reproduced the host exactly**: 364 frames, 3,641 messages,
11 topics, 32 chunks, `compression=['zstd']`, every channel 10.00 Hz / 100.00 ms, **all 10 probe
checks**, 300 object ids, 51-257 boxes a frame, 364 of 364 fixes on the map.

The two `.mcap` files are **973,314 bytes on the host against 974,603 in the container**, 0.13%
apart on identical message counts. Physics is not bit-reproducible across the two environments
and nothing here claims it is; what matters is that every count, rate and check matches.

## Looking at one, and the two faults that found, 2026-09-01

`./scripts/ros-view.sh bags/<name>` — rviz2 in `wingfin-ros-viewer`, a **separate** image from
`wingfin-sim` (a bag is a file; looking at one needs ROS and a display, not 13.3 GB of CUDA).
It plays the bag on a loop under `use_sim_time`, with displays for the car, the route, the TF
tree and the object boxes.

**The viewer is built and connected; the picture has not been checked by anyone.** rviz2 opens at
`OpenGl 4.6` and `ros2 topic info` against a live viewer reports 1 subscriber each on `/tf`,
`/localization/odometry`, `/planning/route` and `/perception/objects` — which rules out a
mistyped topic, an unloaded plugin and a QoS mismatch, and says nothing about whether a box is in
the right place. There is no screenshot.
`docs/fixes/2026-09-01-20:19:21-phase-b-was-marked-done-on-log-silence.md` has what is left.

`/perception/traffic_lights` gets **0 subscribers**: no rviz plugin exists for `wingfin_msgs`.
Free today, since `dynamic_map_states` is 0 everywhere, and a hole the moment lights land.

Two things the image must carry, and both are silent when missing:

- **`vision-msgs-rviz-plugins`, not just `vision_msgs`.** With the types and no plugin, rviz2
  subscribes to `/perception/objects` and draws nothing — indistinguishable from a bag with no
  objects in it.
- **`wingfin_msgs` as a built colcon package.** MCAP's embedded schema text is enough for
  `ros2 bag info` to *list* the topic and not enough for anything to subscribe: `ros2 bag play`
  in a stock `ros:jazzy-ros-base` reports `Ignoring a topic '/perception/traffic_lights',
  reason: package 'wingfin_msgs' not found`. `tools/ros_msgs_package.py` generates the package
  from `ros_schema.EXTRA_DEFINITIONS`, so the definition in a bag and the definition a
  subscriber builds against cannot drift.

**The first run found two faults that all ten probe checks pass straight over.**

**Every pose claimed to be invalid.** `UNKNOWN_COVARIANCE` was `[-1.0] + [0.0] * 35`, on
odometry pose, odometry twist and every detection. -1 in element 0 is defined by
`sensor_msgs/Imu` alone and means *this publisher does not produce this quantity* — not "I am
unsure of it". On a pose that is exact ground truth it tells a consumer to discard it, and a -1
on the diagonal is not positive-semidefinite, so rviz2 logged `Negative eigenvalue found for
position` once a frame and drew no ellipse. `gnss_fix_message` in the same file had it right the
whole time: zeros with `position_covariance_type: 0`.

Now zeros everywhere except `linear_acceleration_covariance`, the one quantity genuinely not
synthesised. **21 warnings a run before, 0 after**, on a re-recorded bag whose 10 probe checks
still pass and whose counts are unchanged (364 frames, 3,641 messages).

**The route never drew.** The rviz config asked for Transient Local because the topic is latched;
a bag records `offered_qos_profiles: []` and `ros2 bag play` republishes **volatile**, and DDS
answers an incompatible request by delivering nothing at all. It says so once —
`No messages will be sent to it. Last incompatible policy: DURABILITY_QOS_POLICY` — and then
looks exactly like a topic with no data. Fixed in the config, not the writer; `--loop` is what
makes volatile sufficient for a once-per-episode message.

**A `TF_OLD_DATA` warning once per lap is the loop**, not a fault: the bag's clock restarts at
zero and rviz2 clears its TF buffer.

## Traffic lights, and the latched-topic delivery problem, 2026-09-02

Keith converted `signals.json` into `junction-1`, so `dynamic_map_states` went 0 -> **8** and a
code path that had never once executed finally ran.

**All 8 lights reached the bag** - which was the thing at risk, because
`ScenarioLightManager.after_reset` drops any light whose lane is missing from the road network,
warns, and carries on (`skip_missing_light` defaults True), so 5 lights out of 8 would have
looked perfectly healthy. Two complementary phases, straight out of the bag:

```
 6 lights   GREENx269 -> YELLOWx30 -> REDx65      at (-27.6..-2.8, 75.5..84.9)
 2 lights   REDx299   -> GREENx65                 at (-17.1..-13.7, 67.2..68.0)
```

The handover is clean: the six turn red on the same frame the two turn green, so no two
conflicting approaches are ever green together. **No numeric check can see that** - the bag does
not carry which movements conflict - which is the strongest argument yet for the viewer existing.

`ros_probe.py` gained three checks (10 -> 13), all reachable failures rather than theatre: no
light disappears part-way, every light has a non-empty id and colour (`ros_frame.lights_of` reads
them through `getattr` defaults, so a MetaDrive rename yields empty strings and nothing raises),
and every light changes colour at least once (a frozen light is what a tape that never advanced
looks like). Positions deliberately get **no** extent check: a light's position comes off the live
engine in the same frame as everything else, so there is no separate shift for it to miss.

### A latched topic is not delivered by a delay-free `ros2 bag play`

`/planning/route` is one message at t=0. rviz2 takes ~2 s to start, so it never saw it, and the
route drew only because playback looped and brought it round 36 s later.

Two things were needed, and **the obvious one alone is not enough**:

| | |
|---|---|
| the bag recorded `offered_qos_profiles: []` | fixed - `ros_bag._latched_qos` records transient-local for the topics `TOPICS` already labels `"latched"`, and `metadata.yaml` now reads `durability: transient_local` |
| **`ros2 bag play` still does not deliver it** | measured **3 trials of 3**: a transient-local subscriber joining 5 s in received nothing, with **no warning**, because the QoS now matches. The player does not serve the retained sample to a late joiner |
| `--delay` does | **3 trials of 3**: a subscriber joining 2 s into a 4 s delay received the whole 29,325-byte route |

So `ros-view.sh` plays once after a 4 s head start, and `--loop` is a flag rather than the only
thing that made the route appear. The QoS fix still matters on its own: a consumer *asking* for
transient-local is no longer refused outright, and the bag stops describing a latched topic as
volatile.

## Which ROS distro can read these bags, measured 2026-09-01

**jazzy yes, humble no** - and the reason is the container format, not the messages.

`ros_bag.py` writes rosbag2 **format version 9**. In v9 a topic's `offered_qos_profiles` is a
sequence and every topic carries a `type_description_hash`; humble's rosbag2 expects that field
to be a *string* and refuses the metadata before it ever opens the `.mcap`:

```
Exception on parsing info file: yaml-cpp: error at line 34, column 29: bad conversion
```

Line 34 is `offered_qos_profiles: []`. Humble also does not ship the mcap storage plugin by
default, which is the next wall behind that one.

**This is counter-intuitive and cost a rebuild.** `ros_schema` writes its message definitions
against `Stores.ROS2_HUMBLE`, so matching a viewer to humble looks obviously right - and the
definitions *are* humble's. The container holding them is not. Tier 4's `ros:jazzy-ros-base`
check was never merely a portability nicety: jazzy is the only distro either half of this has
been shown to work on.

Nothing here argues for downgrading the writer. A v9 bag is what current rosbag2 produces and
what the rig's own tooling will move to; it means a consumer on humble needs `rosbags` (which
reads the file directly, distro-free) rather than `ros2 bag`.

## Traps

- **`CompressionMode.FILE` destroys the index-only read** the rig's own audit depends on.
- **`metadata.get("old_origin_in_current_coordinate") or (0.0, 0.0)` raises.** It is a numpy
  array and a two-element array has no truth value. Skipping the shift instead is worse: 93.8 m
  on junction-1, on a road that really exists.
- **`Camera.position` is in MetaDrive's ego frame, not the CARLA spec** the rig file is parsed
  from (`camera_rig.py:130`). x is RIGHT, y is FORWARD, `+heading` is LEFT. ROS `base_link` is x
  forward, y left, `+yaw` left - so forward and lateral swap, the lateral one negates, and the
  yaw sign is already correct and must be left alone.
- **A camera's topic name is not its aim.** `rigs/cams.txt` reads `+yaw` as right at the front
  and as left at the back, so `cam_back_left` publishes as `rear_left` and is mounted aiming
  rear-right. `/tf_static` carries the geometry; follow `frame_id` into the transform rather than
  trusting the topic. Both `drive.py` and `ros_probe.py` print a `NAME/AIM` line for every camera
  where the two disagree - two on `cams.txt`, none on `av3.txt`.
- **Two attributes name a kind:** `TYPE_NAME` on traffic participants, `CLASS_NAME` on static
  objects. Reading only the first gives `TrafficBarrier` where the dataset says
  `TRAFFIC_BARRIER`.
- **`map.osm` may use single quotes.** junction-1's was hand-edited in JOSM, so a `<bounds>`
  regex written for `"` silently skips the check rather than failing it.
- **A GNSS fix can legitimately land outside the OSM extent** - 10 m past `minlat` on junction-1,
  because osmnx clips ways while lane geometry is offset outward. The containment check pads by
  50 m: wide enough for the overhang, far under the 93.8 m error it exists to catch.
- **A bare `((i++))` under `set -e` is a silent exit 1.** It is how the only documented form of
  `ros-bag.sh` failed for a month with no output at all. Nothing else in `scripts/` uses that
  form; `i=$((i + 1))` is an assignment and carries the assignment's status.
- **Both readers refuse a missing bag** rather than tracebacking, and `ros_audit.refuse_if_missing`
  is shared so the two cannot drift. The ladder reads in one tier what it recorded in the one
  before, so "there is no bag there yet" is the likeliest thing to be wrong, and it happened
  twice before the guard existed.
- **A latched topic needs the QoS *and* a head start.** Recording transient-local stops the
  incompatibility; it does not move the message. `ros2 bag play` never serves a retained sample
  to a late joiner, and once the QoS matches it does not warn either - the silence looks like
  success. Play with `--delay`, or subscribe before playback starts.
- **A traffic light whose lane is missing is dropped silently** - `skip_missing_light` defaults
  True, so a bag with 5 lights of 8 looks healthy. Count what came out against the dataset.
- **"Unknown" covariance is all zeros; -1 means "not produced at all".** Only
  `sensor_msgs/Imu` defines the -1, and only `linear_acceleration_covariance` here earns it.
  Putting it on a pose says "discard this", is not positive-semidefinite, and no numeric check
  reads a covariance - the fault is in what the number claims, not in what it is.
- **A humble viewer cannot open these bags at all** - format v9 against a rosbag2 that reads v8.
  Matching a viewer to `Stores.ROS2_HUMBLE` is the natural mistake and it is the wrong axis: the
  messages are humble's, the container is not.
- **`ros2 bag play` needs `--clock`, and rviz2 needs `use_sim_time:=true`, or the screen is
  empty with no error.** Every stamp in a bag is simulator time starting at epoch zero; a viewer
  on the wall clock puts all of it ~56 years in the past and tf silently discards the lot.
  Neither half works without the other.
- **`vision_msgs` in rviz2 needs `vision_msgs-rviz-plugins`, not just `vision_msgs`.** With the
  types but no plugin, rviz2 subscribes to `/perception/objects` and draws nothing - which looks
  exactly like a bag holding no objects, and the objects are the point of ours.
- **`rosbags` needs Python 3.10** and `drive.py` runs on `METADRIVE_PYTHON`, which is 3.8 on the
  host. The container is one 3.10 interpreter and it works there;
  `ros_frame.refuse_if_unsupported()` says so before the terrain is built.
- **The container needs `--no-model`, or a replay drive refuses before it opens a bag.**
  `compose.yaml` always sets `MODEL_CHECKPOINT`, `drive.py` takes it as the default for
  `--model-checkpoint`, and a checkpoint implies `--agent-policy remote`. `sim.sh --no-model`
  passes `-e MODEL_CHECKPOINT=`, which is the only way to clear it - compose substitutes its
  default for an empty value as readily as for an unset one.
- **A `chmod -R` on `/opt/venv` in a later Docker layer writes a second copy of the venv.**
  chmod modifies, and modifying a file from a lower layer copies it up - so it turns a 39 MB
  layer into a 13 GB one. Measured: the `ros` sync installs 5 packages in 0.8 s and the chmod
  was still running two and a half minutes later. It is not needed either; uv writes 644/755
  under the build's umask. The *first* sync layer keeps its chmod, for `/opt/python`, whose
  managed CPython arrives mode 700.
