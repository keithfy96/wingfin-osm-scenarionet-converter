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

## The SBG GNSS family, measured 2026-09-02 (stage 11 phase 2)

Nine topics, `7 / 45 -> 23 / 45`, and no artefact from anyone needed. Seven are `sbg_driver`
types, which the humble typestore has never heard of; two - `imu/pos_ecef` as a
`geometry_msgs/PointStamped` and `imu/utc_ref` as a `sensor_msgs/TimeReference` - **were core all
along** and had sat in `MISSING_DEFINITIONS` only because nobody had decided what the rig meant
by them.

| | before | after |
|---|---|---|
| messages in a 364-frame `junction-1` drive | 3,648 | **6,924** |
| channels | 18 | **27** |
| `rig topics produced` | 14 / 45 | **23 / 45** |
| entries in `MISSING_DEFINITIONS` | 24 | **15** |
| probe checks | 16 | **24** |

Reproduce:

```bash
rm -rf bags/phase2-sbg
METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- \
    --out bags/phase2-sbg --camera-rig rigs/cams.txt --render offscreen
uv run python tools/ros_probe.py bags/phase2-sbg --workspace workspaces/junction-1
uv run python tools/ros_probe.py bags/phase2-sbg --coverage      # 23 / 45
```

### The definitions are files, and they are version-dependent

`tools/sbg_msgs/` holds twelve `.msg` files copied byte for byte from `SBG-Systems/sbg_ros2_driver`
at tag **3.4.0**, commit `3efaf29`. Seven message types and the five nested status submessages
they name; `ros_schema._sbg_definitions` loads them at import.

They are files rather than strings because "copied verbatim" has to be checkable, and because
rewrapping a `.msg` into a 99-column Python literal is exactly where a field changes order.

**The field lists changed between releases and CDR does not carry field names.** Measured against
3.1.0: `SbgGpsPosStatus` went 7 fields to 22, `SbgEkfStatus` 16 to 23 with two *removed*,
`SbgUtcTime` 11 to 14, `SbgGpsPos` 12 to 13, `SbgImuStatus` 10 to 11. A subscriber built against
3.1.0 reading our 3.4.0 `SbgEkfStatus` does not fail - it reads `dvl_bt_used` as
`gps1_course_used` and carries on. What protects a reader is that rosbag2 writes the definitions
into the bag; what protects a reader using its *own* installed package is knowing the version, so
it is written into every bag as `sbg_driver_version` in the `wingfin` metadata.

**Which version the rig recorded is unknown**, because `bag_audit.html` carries rates and no bag
off the rig has been read. The day one arrives, `tools/ros_defs.py` prints its definitions and
the two are compared rather than assumed.

### What reaches the bag is the field list, not the comments

Measured on `bags/phase2-sbg`, and it corrected a claim written earlier the same day: `rosbags`
**regenerates** the definition from its parsed typestore when it writes a connection, so the
comments are stripped and the bag carries field lines only. The field lists round-trip exactly -
all seven published types and all five nested ones, checked against the upstream files in
`TestTheSbgDefinitionsInAWrittenBag`. The comments stay in `tools/sbg_msgs/` because they are the
only record of what each field means, which is what every value below had to be decided against.

### Absence, in a message type with no way to state it

`sensor_msgs/Imu` has a `-1` for "I do not produce this" and `NavSatFix` a covariance type, so
the two GNSS topics written before this phase could both disclaim in-band. **The `sbg_driver`
types cannot.** `SbgImuData.temp` is a float32 and nothing else, and every value a float32 can
hold is a temperature - `0.0` reads as a sensor sitting at freezing, which a consumer can and
would believe.

So four quantities are written **NaN** (`ros_schema.ABSENT_SCALAR`), verified round-tripping
through CDR intact:

| field | why nothing can fill it |
|---|---|
| `SbgImuData.accel` | the same refusal `imu_message` makes with its `-1`; zeros would claim a car in free fall with none of the gravity a real FLU accelerometer always reads |
| `SbgImuData.temp` | a physical sensor temperature. `/sensing/gnss/imu/temp` is one of the ten excluded from the 45 for exactly this reason, and a number here would contradict that inside one bag |
| `SbgImuData.delta_vel` / `delta_angle` | sculling and coning, a strapdown integrator's own intermediates. There is no strapdown integrator |
| `SbgEkfNav.undulation` / `SbgGpsPos.undulation` | geoid-to-ellipsoid separation. A zero would make `altitude + undulation` look like a real height above the ellipsoid |

**An accuracy of zero is not the same thing and stays a number.** `position_accuracy`,
`velocity_accuracy`, `course_acc` and the three `clk_*` fields are 1-sigma uncertainties, and for
ground truth zero is *true* - the simulator's position is exact. Only a quantity that does not
exist at all gets NaN. And where a type provides its own absence value it is used instead:
`num_sv_tracked` and `num_sv_used` are `0xFF`, which `SbgGpsPos.msg` documents as N/A, so nothing
has to learn one of our conventions to read it.

Every `SbgEkfStatus.*_used` flag is False. They say which aiding sources the Kalman filter fused,
and there is no filter, no receiver, no magnetometer and no odometer - there is one true pose.

### The drive declares the GPS epoch as its `t = 0`

`SbgUtcTime` wants a calendar date and `SbgGpsPos` a GPS time of week; the simulator has neither,
only seconds since the drive began. A wall clock taken at conversion time would make the bag claim
the drive happened then **and make two runs of one drive differ**; a recent-looking date would be
worse, because it would be believed.

So `t = 0` is declared to be 1980-01-06T00:00:00Z, the start of GPS week 0
(`ros_schema.GPS_EPOCH_UNIX_S`). Then `gps_tow` is literally the elapsed time, the calendar fields
advance correctly *within* the drive, and the absolute date is a sentinel. `clock_utc_status` is
`0` - the message's own "the UTC time is not known, we are just propagating internally" - so the
caveat is in-band and not only in a doc.

`imu/utc_ref` is where the two clocks meet: `header.stamp` is the sim clock, `time_ref` the
declared UTC, and they differ by exactly `315964800 s`. **Publishing them equal would have been
the quiet lie** - it says "our clock is UTC", the one thing a simulated drive cannot claim.

### The eight new probe checks, and why each is a relationship

Every value in the nine channels is a re-shaping of one position and one velocity, so a swapped
north and east, or a bearing measured from north instead of east, produces a bag that opens,
decodes and plots a car on a road. Measured on `bags/phase2-sbg`, 364 frames:

```
ekf_nav and nav_sat_fix are the same position on every frame   364 of 364, worst 0.000e+00 m
gps_pos carries the same position as ekf_nav                   worst 0.000e+00 m
pos_ecef is where ekf_nav's own lat/lon put it                 worst 0.000e+00 m, frame earth
ekf_quat, ekf_euler and imu/data all describe one rotation     worst yaw 5.09e-14 deg
gps_vel's course is its own velocity, measured from east       worst 7.52e-06 deg
what a simulator does not have is NaN, never a plausible zero  temp, accel, delta_*, undulation
imu_data's gyro is imu/data's angular velocity                 worst 0.00e+00 rad/s
utc_ref offsets the sim clock by the declared epoch            315964800 s, 1980-01-06
```

The course tolerance is `1e-3` deg because `course` is a float32; everything else is exact
because both sides come from one call. `ekf_nav` and `gps_pos` differ on the rig - one is the
filter's answer at the INS reference point, the other the receiver's at the antenna - and
coincide here because there is neither a lever arm nor a filter.

### ENU, and the 90 degrees that does not raise

The driver publishes NED under `use_enu: false`, where the same two floats in `velocity` mean
north and east rather than east and north, and `course` and `ekf_euler.angle.z` are bearings from
north rather than from east. MetaDrive's world frame is already ENU and REP-103 agrees, so ENU is
what is written throughout - but read one convention as the other and every heading is 90 degrees
out with a car still driving plausibly along a road. `ekf_euler`'s yaw is zero pointing **east**.

## The point cloud, measured 2026-09-02 (stage 11 phase 3)

`/sensing/lidar/points`, the one topic of the 45 whose payload is a shape rather than a
handful of numbers. `--ros-lidar` on a drive, off by default: it costs an image buffer every
step and takes the bag from 672 KB to 24 MB.

| | before | after |
|---|---|---|
| rig topics produced | 23 / 45 | **24 / 45** |
| messages / channels (six-camera rig) | 6,924 / 27 | **7,288 / 28** |
| `ros_probe` checks | 24 | **30** |
| bag on disk | 672 KB | **24 MB** (74.83 MB payload, 3.14x) |

### `perceive(to_float=True)` is exact, and `to_float=False` is the one that destroys it

`docs/reference/sensors-and-observations.md` records that `depth` and `point-cloud` inherit a
`_format` that *converts* rather than reformats. That is true, and the direction matters:
`DepthCamera._format` (`depth_camera.py:184-191`) overrides the base class and returns the array
**untouched** when `to_float=True`, converting only on the `False` branch - `(ret *
255).astype(uint8)`, which a cloud running thousands of metres does not survive.

Measured on junction-1: `perceive(to_float=True)` is bit-identical to `get_rgb_array_cpu()`,
ratio exactly 1.0 on every element; `perceive(to_float=False)` comes back uint8 clipped to
0..255. So reading the sensor in-process does avoid the socket's fault, **provided that flag
stays as it is**. `ros_frame.lidar_cloud` is the line that has to keep it, and the flag's name
argues for the wrong one.

Other measurements from the same run, none of which is guessable:

* the lens is **near 1.0, far 100000.0**, FOV `(65, 23.045131)` - the vertical follows the
  aspect ratio, so it is the 200x64 shape that sets it
* the array is **`(height, width, 3)` float64** - beams by rays by xyz
* one read costs **0.3-0.4 ms**, the buffer having already been filled by the frame pass
* `ego_centric=True` zeroes the *translation* only; the rotation is still built from the
  camera's world hpr, so the sweep arrives on world axes with its origin at the sensor

### Why the cloud is published in `lidar` and not in `map`

`ego_centric=False` hands back true world coordinates. Publishing those in `map` would need no
arithmetic from us at all - and that is the argument against it, not for it: a cloud that took
no transform makes no claim about which way the sensor is pointing, so nothing about it could
ever be checked, and `/sensing/lidar/points` on the rig is not a world-frame topic anyway.

The sensor frame costs one rotation, by minus the car's heading, and the wrong sign is the
quietest possible failure: the same points, rigidly rotated, still a plausible road at a
plausible density over a plausible extent, every one of them behind the car, nothing raising.
What settles it is that **a forward-facing sensor cannot return a point behind itself**.
Measured over a full drive, de-rotating by `-heading`:

```text
  correct sign     100.00% inside the FOV, worst bearing 32.49 deg (half-angle 32.50)
  flipped sign       0.00% inside, 0.00% with x > 0, mean bearing -95.49 deg
```

The rotation therefore lives in `ros_schema.lidar_points_message`, where it is unit-testable
with no simulator, and not in `ros_frame.py`.

### A miss keeps its slot, and the range that decides one is ours

The depth buffer's far plane is 100 km, so an unhit ray does not come back as nothing - it comes
back as a point up to **18 km** away, at a distance that varies because the buffer is non-linear
out there. There is no sentinel to test for, so the range is **declared**: 200 m by default
(`--ros-lidar-range`), matching `sensor_survey.POINT_CLOUD_MAX_RANGE_M`.

Beyond it the point is written **NaN in place** rather than dropped, which is what
`is_dense: false` exists to say, and it keeps the sweep organised - 64 rows of 200 - so a reader
still knows which beam a return came from. Roughly **half of every sweep on junction-1 is sky**
(49.80% of rays hit within 200 m). `is_dense` is computed per sweep rather than hard-coded, so
the flag is a claim about that cloud.

### The buffer ceiling that does not crash: 8 buffers blind the cloud

`camera_rig.MAX_IMAGE_BUFFERS` is 9, measured by counting runs that *survive*; past it the env
asserts inside `renderFrame()` and aborts. There is a **second, lower ceiling** for the point
cloud, and it is silent. Measured on junction-1, 12 sweeps at each size, returns within 200 m as
a share of the buffer:

```text
  1 buffer  (cloud alone)   48.52..57.70%      7 buffers (6 RGB + cloud)  48.52..57.70%
  4 buffers (3 RGB + cloud) 48.52..57.70%      8 buffers (7 RGB + cloud)   0.10.. 0.27%
```

At eight the env does not crash, the buffer is created, `perceive` returns the right shape, and
every sweep in a drive differs from the last - so nothing raises and nothing repeats. The depth
buffer simply comes back at its far plane, every ray becomes a point 18 km out, the range gate
turns all of them into NaN, and the bag holds 364 correctly-shaped sweeps of nothing. Every
other check in `ros_probe` passes on that bag.

So `camera_rig.MAX_BUFFERS_WITH_POINT_CLOUD = 7`, `drive.py` refuses the pairing by name, and
`ros_probe` checks the share of rays that hit against a 5% floor - set between the two measured
regimes rather than near either, because it is separating a working sensor from a dead one.
`rigs/cams.txt` is seven cameras and so is exactly one over; the one over is `cam_front_wide`,
the spare with no rig topic, and a six-camera rig plus the cloud records and passes all 30
checks.

### What this cloud is not

A **65 deg forward cone**, not a sweep. The rig carries a Livox, which sees far more of the world
than any one rendered buffer can, and no amount of resolution here changes that - it is the
honest limit of recovering a cloud from a depth camera, not a setting. It is rendered geometry,
so there are no dual returns, no intensity off a real material and no motion distortion across
the sweep; `intensity` is **omitted from the fields** rather than filled with a plausible
constant. `bag_audit.html` records the rig's rate and not its fields, so what the rig actually
publishes on this topic is still unknown - the bag records the cone, the range and the frame in
its `wingfin` metadata so a reader can compare rather than assume.

`ros_audit.notes(path)` is what reads that metadata back. rosbag2 keeps `set_custom_data` in
`metadata.yaml` rather than in the MCAP and `rosbags`' `Reader` exposes no accessor for it, so
until phase 3 a bag that carefully recorded what it was could not be asked.

## The camera packets, measured 2026-09-03 (stage 11 phase 4)

The six `image_raw/ffmpeg` topics, and **the only payload in this bag that has to be decoded
before anything about it can be checked.** `--ros-camera` on a drive that already has
`--camera-rig`; off by default, because it is the one channel here that costs real time per
frame.

Two bags below, both 364 frames on junction-1: `phase4-cams` is the seven-camera
`rigs/cams.txt` with no cloud, and `phase4-full` is six cameras plus `--ros-lidar`, which is the
widest bag this repo can currently write.

| | before (`phase2-sbg`) | `phase4-cams` | `phase4-full` |
|---|---|---|---|
| rig topics on the wire | 23 / 45 | **29 / 45** | **30 / 45** |
| channels in the bag | 27 | **33** | **34** |
| messages | 6,924 | **9,108** | **9,472** |
| `ros_probe` checks | 24 | **31** | **37** |
| bag on disk | 672 KB | **14 MB** | **36 MB** |

Declared coverage is **30 / 45** in both; `phase4-cams` writes 29 because it carries no cloud.
Unit tests went 917 → **947**.

### `FFMPEGPacket` was wrong, and could not have raised

The definition vendored in `ros_schema.EXTRA_DEFINITIONS` since stage 10 read

```
std_msgs/Header header, string encoding, uint32 width, uint32 height,
uint32 pts, uint8 flags, uint64 frame_id, uint8[] data
```

and `ffmpeg_image_transport_msgs` 1.1.2 - the humble branch, the distro
`Stores.ROS2_HUMBLE` pins everything else to - says

```
std_msgs/Header header, int32 width, int32 height, string encoding,
uint64 pts, uint8 flags, bool is_bigendian, uint8[] data
```

**Four differences: the field order, two widths, one invented field (`frame_id`), one missing
(`is_bigendian`).** None of it could raise while no camera topic was written, and a bag written
against the old text would still open - rosbag2 stores the definition beside the data, so our
own reader agrees with itself. A consumer with the real package installed reads the encoding
string out of the width field. It is exactly the fault `ros_schema.py`'s own comment warns about
for `vision_msgs`, sitting unexercised in the file. Now verbatim from commit
`5395eac7dd830245c29d13c4db9fac1574137014`, and pinned character for character by a test.

Identical on the `humble`, `rolling` and `master` branches; master adds comments and no fields.

### `encoding` is the codec, and `libx264` is the string that works on both decoders

`ffmpeg_image_transport`'s current decoder resolves it with `find_id_for_encoder_or_encoding`,
which accepts an encoder name or a codec name; the humble-era one carried an explicit
`libx264 -> h264` map. `libx264` satisfies both. The newer four-token form -
`codec;av_pix_fmt;cv_bridge_fmt;ros_fmt` - is deliberately **not** written: it is a
`master`-branch feature, and the humble decoder takes the whole string as a codec name and finds
none.

### The three faults that are silent in the bytes

- **The pictures are BGR.** `BaseCamera.get_rgb_array_cpu` returns panda3d's RAM image with the
  rows flipped and the channels untouched; MetaDrive's own `get_image(mode="bgr")` returns it
  unchanged and `mode="rgb"` is the one that reverses (`base_camera.py:110-113`). Declaring the
  source `rgb24` is a one-word change that mirrors red and blue in every frame of every bag.
- **A vertical flip** is described by no field in `FFMPEGPacket` and shows in no header.
- **A delayed packet.** With B-frames or a lookahead the packet coming out of `encode()` belongs
  to an earlier frame than the one going in, so every camera stamp in the bag is a decision
  early - and a delayed packet is a perfectly valid packet. `tune=zerolatency` is what makes
  libx264 one-in-one-out. Measured over three presets, 20 frames each: exactly one packet per
  call, every call, nothing left to flush at close.

### Preset, and why `ultrafast` is the wrong reflex

Measured on 512x288, 20 synthetic frames:

| preset | bytes a frame | ms a frame |
|---|---|---|
| `ultrafast` | 1,412 | 1.59 |
| **`veryfast`** | **1,277** | **0.96** |
| `medium` | 1,486 | 2.03 |

`veryfast` is both smaller and quicker than `ultrafast` here, which is the opposite of what the
names suggest, and it is the default for that reason. On real junction-1 frames the six streams
came to **6,075 bytes a frame** against the rig's own measured 7,159 - the same order, which is
the useful thing to know about it - and **73x** smaller than the 442 KB an uncompressed
`sensor_msgs/Image` would be at that size.

Round-tripped, a frame comes back at 42.0 dB mean PSNR against the source.

### Equality is the wrong test for a held frame, and this is the measurement

`frame_gate` re-uses the last drawn picture on a step between two decisions, so a stream written
every step would carry one re-encode of a held buffer in every gap. The obvious check is whether
two consecutive decoded pictures are equal. **It does not work.** Encoding one identical source
frame ten times gives:

| stream | exact repeats | median PSNR between consecutive pictures |
|---|---|---|
| the same frame ten times | **0** | 47.2 .. 61.4 dB, climbing as the encoder converges |
| actually moving | 0 | 24.8 dB |

A keyframe and the P-frames after it quantise differently, so nothing is ever bit-identical. The
probe therefore uses a **40 dB ceiling on the median** - the median rather than the worst,
because a car stopped at a red really does draw the same picture twice and that is not a fault -
and backs it with the structural check that actually settles it: the packets arrive at the
**decision** rate the bag's own metadata declares, measured 10.00 Hz against 10.00 Hz, and a
stream written every step would be `stride` times too fast.

### What the probe added

Seven checks, six of them relationships between two independently produced quantities:

```
ok  every camera stream holds the same number of packets - front_left=364 ... rear_right=364
ok  each packet says the same size and frame as its own camera_info - 6 streams agree
ok  every stream opens on a keyframe and keeps one at least every 10 frames
ok  the packets arrive at the decision rate the bag declares, not the step rate - 10.00 vs 10.00 Hz
ok  every packet decodes back to a picture of the declared size - 2184 packets across 6 streams
ok  the pictures move - no stream is one held buffer re-encoded
ok  the cameras drew a scene rather than an empty buffer
```

The decode is the point. Every other check in `ros_probe.py` reads a number off the wire; these
run the decoder, because a bag full of well-formed packets carrying the wrong pixels opens,
plays and renders.

### `av`, and what it does and does not add

`av>=15,<18` joins `rosbags` in the `ros` dependency group. **The wheel carries its own ffmpeg,
statically, including libx264** - no apt package, no system `libavcodec`, and nothing added to
`docker/Dockerfile` but a rebuild. The import succeeding is not the same as the encoder being
there: a PyAV built from source against a distro ffmpeg without `--enable-libx264` imports
perfectly and has no encoder, so `ros_encode.refuse_if_unsupported` checks
`av.codecs_available` by name.

## Recovering the rig's own message types, 2026-09-03 (stage 11 phase 5, the ingest)

Phase 5's fifteen topics are blocked on nothing but `.msg` text, and this half of it is built:
**the recovery is a command, and the result registers with no edit to any source file.**

```bash
uv run python tools/ros_defs.py /path/to/ros2_mig_phase_5_p1 \
    --write tools/wingfin_msgs --package wingfin_msgs
```

The other half - fifteen builders that put values on the wire - is not built and cannot honestly
be, because it needs the field *order* those files carry. Coverage stays 30 / 45.

### Files, not a paste, for the reason `tools/sbg_msgs/` gives

`render` already emitted a pasteable Python literal, and that was the wrong destination.
**Verbatim has to be checkable**: a `.msg` file diffs against the bag it came out of in one
command, and the same text rewrapped to fit a 100-column source line does not - rewrapping being
exactly where a field changes order. So `--write` writes `<Type>.msg` beside the loader, the way
the SBG family already sits in `tools/sbg_msgs/`.

`--package` keeps one directory to one package. Without it, `--write` on a bag of ours vendors
`vision_msgs`' five types into the same directory as `wingfin_msgs`' two - measured, first run -
and a directory named after one package holding another's is how a loader keyed on that name
starts registering a type under the wrong package.

### The loader is carried by the existing suite, not waiting on the rig

`wingfin_msgs/TrafficLight` and `TrafficLightArray` - **ours**, invented for
`/perception/traffic_lights`, a topic the rig's bag does not have - were moved out of the
`EXTRA_DEFINITIONS` string literal into `tools/wingfin_msgs/` by running that command against
`bags/j1-lights`. They came back byte for byte identical to the literals they replaced:

```text
bags/j1-lights: 27 message definitions across the bag's connections
  vendoring 2 definition(s) into tools/wingfin_msgs/
      written  wingfin_msgs/msg/TrafficLight
      written  wingfin_msgs/msg/TrafficLightArray
```

That was the point of moving them. `_wingfin_definitions` is now on the path of every bag test in
the suite - 947 of them - rather than being exercised for the first time on the day the rig's
file arrives. Re-running is `unchanged`, twice.

### "Is this file enough?" runs topic-first, because the type names are the blockage

A definition count does not answer the only question worth asking of a rig bag, so `report`
prints the fifteen one by one with whatever type the recorder filed each under. The lookup
**cannot** run type-first: we do not know the fifteen type names - that is the blockage itself,
`bag_audit.html` recording rates and not types. A bag carrying fourteen of the fifteen is worth
having and is not the end of the job, and only a per-topic list says which one is short.

On one of ours it reads `the 15 topics phase 5 waits on: 0 carried by this bag`, and says why.

### The collision that would be silent: ours and the rig's share one namespace

`wingfin_msgs` is the **vehicle's** package, and two of its types are ours. A rig bag carrying
its own `wingfin_msgs/TrafficLightArray` would land on top of ours, and every bag written
afterwards would serialise our traffic lights against a field list nothing in this repo agreed
to. CDR carries no field names, so nothing downstream raises.

`vendor` reports `CONFLICT`, leaves the file alone, and `report` returns non-zero. Resolving one
is a decision for a person - either the rig's definition wins and `/perception/traffic_lights` is
rebuilt against it, or ours is renamed out of the collision. `--force` is for after that, not
instead of it.

### Why the builders are not written ahead of the definitions

The values are not the blockage and are already in scope at `drive.py:2478` - `action` is the
commanded `[steering, throttle_brake]`, the ego's speed and heading are `/vehicle/state`,
`prediction` is the five model topics. Field names and field order are the blockage, and a
builder written against a plausible field list produces a bag that opens, plays and is wrong.
`ros_schema.py`'s opening argument applies to its own package: **a topic that is present and
invented cannot be tested for, where an absent one can.**

## Traps
- **A `sbg_driver` message is version-dependent and CDR will not tell you.** `SbgEkfStatus` went
  from 16 fields to 23 between 3.1.0 and 3.4.0, two of them removed - a mismatched subscriber
  reads the wrong field and carries on. `tools/sbg_msgs/` pins 3.4.0 and every bag records it.
- **`rosbags` does not store your definition text, it regenerates it.** The comments in
  `tools/sbg_msgs/*.msg` never reach a bag; the field list does, exactly.
- **NaN, not zero, for a quantity a simulator does not have** wherever the message type has no
  `-1` and no status flag to say so - `SbgImuData.temp` above all, whose topic-level twin
  `/sensing/gnss/imu/temp` is excluded from the 45 for the very same reason. An *accuracy* of
  zero is the opposite case and must stay a number: for ground truth it is true.
- **The drive's `t = 0` is the GPS epoch, deliberately.** A 1980 date in `utc_time` is the
  sentinel, not a bug; `clock_utc_status` is 0 and `utc_ref` carries the offset. Stamping a real
  wall clock would make two runs of one drive differ.

- **The vendored `FFMPEGPacket` definition was wrong for three weeks and nothing could have
  said so.** A message type nothing writes is a type nothing checks. Any `.msg` in
  `EXTRA_DEFINITIONS` that no topic uses yet is in the same position.
- **`CameraRig.read()` returns BGR**, whatever `get_rgb_array_cpu` is called. `mode="rgb"` is the
  branch that reverses, not the other way round.
- **Two consecutive decoded H.264 frames are never bit-identical, even from one identical source
  frame.** A held-buffer check written with `==` passes on a bag that is entirely held buffers.
- **Eight image buffers blind the point cloud, and nothing raises.** A 7-camera rig plus
  `--ros-lidar` records 364 well-formed sweeps that are 99.8% NaN. `MAX_IMAGE_BUFFERS` does not
  cover it - that ceiling counts runs that survive, and this one survives.
- **`perceive(to_float=False)` is the destructive branch for a cloud, not `True`.**
  `DepthCamera._format` returns the array untouched on the `True` branch; the flag's name argues
  for exactly the wrong one.

- **A recovered `.msg` and an invented one live in the same package namespace.** `wingfin_msgs`
  is the vehicle's; two of its types are ours. `ros_defs.vendor` refuses a differing overwrite
  because CDR carries no field names and the resulting mismatch raises nowhere.
- **`--write` without `--package` mixes packages into one directory.** A bag of ours vendors
  `vision_msgs` alongside `wingfin_msgs` - one directory holds one package, as `tools/sbg_msgs/`
  does.
- **"Which types does this bag have" does not answer "is this bag enough".** The fifteen type
  names are unknown, so the check has to run topic-first.
- **An unhit ray is a point up to 18 km away, not a zero and not a NaN.** The far plane is
  100 km and the buffer is non-linear out there, so there is no sentinel - the range that calls
  a return a miss is declared by us, and without it the cloud describes the sky.
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
