# Stage 10 - ROS 2 bags out of a drive

## Status

**Phase A is built and measured**, on 2026-08-31. Every figure below was produced by running it;
nothing here is estimated. Phase B (real ROS 2 in the container, for live viewing) is **not
built** and is not needed for a bag.

The reference throughout is `bag_audit.html` at the repo root - the audit of the vehicle rig's
own `ros2_mig_phase_5_p1` bag, 55 topics, 1,466,940 messages, 7.41 GB - and the goal is a
simulated bag a training stack cannot tell apart, except that ours also carries labels.

**What this stage covers of that goal: 8 of the rig's 45 producible topics**, and 7 on a drive
without `--camera-rig`. The cameras, the SBG GNSS family, the point cloud and the `/vehicle` and
`/control` topics are all absent. That is not a defect in what is built here - it is the
remaining scope, now written down as
`docs/implementation-plan/stage-11-a-complete-ros-bag.md`, which supersedes the **Cameras**
checkbox below.

## Summary

```text
Completed Stage 6 dataset
  -> Stage 7: an agent at the wheel
  -> Stage 8: live traffic
  -> Stage 9: a model at the wheel
  -> Stage 10: the drive written out as a ROS 2 bag, with ground truth attached
```

### Nothing upstream changes

| | change |
|---|---|
| `src/osm_scenario/` | none - no generator, topology or conversion code is touched |
| `generation_fingerprint` | unmoved. `--ros-bag` is drive-time, like `--record` and `--export-drive`; a `ConverterConfig` field would feed `configuration_checksum` and invalidate the Stage 3 review |
| `workspaces/` | read only. Datasets, `actors.json` and `source/manifest.json` are untouched; bags are written where the command says |
| `tools/drive.py` | three flags and one consumer in the existing per-frame slot |

## Facts the design rests on

### The rig's bag has no labels in it, and that is the whole opportunity

None of the 55 topics is a ground-truth object. `/perception/inference_control` and
`/perception/model_info` are the rig's own model's config and output, not answers. That is normal
for a recorded drive - nobody labelled it. The simulator knows where every pedestrian, cyclist,
cone and car is, and which light was red, on every frame, for nothing. **Stage 10 is not an
imitation of the rig; it is the rig's pixels with the answers attached.**

### What of the 55 MetaDrive can produce: 24 direct, 21 synthesisable, 10 not

The ten that cannot exist without fabricating them are CAN (`can_rx`, `can_tx` - there is no
bus), the cabin camera and its `camera_info` (no cabin, no driver), the two audio topics, the
GNSS receiver's own temperature and status, and `/diagnostics` + `/rosout`, which would be the
simulator's logs rather than the vehicle's.

**Topics whose message type we do not have are omitted, not published with a substitute type.**
A subscriber deserialising `wingfin_msgs/VehicleState` fails on a `geometry_msgs/TwistStamped`
wearing that topic name - worse than an absent topic. `ros_schema.MISSING_DEFINITIONS` lists the
fifteen and what each needs; adding the `.msg` text to `EXTRA_DEFINITIONS` lights them up.

### `CompressionMode.STORAGE`, and never `FILE`

The published `rosbags` docs list only `file` and `message`. The installed release also has
`STORAGE`, which is the one that matches the rig. Measured on a 16 MB bag:

| mode | result |
|---|---|
| `NONE` | 12 chunks, `compression=<none>` |
| **`STORAGE`** | **12 chunks, `compression=zstd`** - chunked, indexed, the rig's container |
| `FILE` | one `bag.mcap.zstd`; the index cannot be read without inflating the whole file |
| `MESSAGE` | *larger* than no compression at all |

`FILE` would destroy the rig's own audit method, which reads 1,466,940 timestamps out of 7.41 GB
in 0.3 s without decompressing a payload.

### Two message packages register at runtime, with no build step

`vision_msgs` and `ffmpeg_image_transport_msgs` are not in the humble typestore.
`get_types_from_msg` parses their `.msg` text and `register` adds them; both were verified
round-tripping through CDR. rosbag2 writes the definition text into the bag, so a reader decodes
them without our package - which is also why `wingfin_msgs/TrafficLightArray`, a message that
exists only here, is a safe thing to invent for a topic the rig does not have.

### Python 3.10 is a gate, not a formality

`drive.py` runs on `METADRIVE_PYTHON`, which on the host is the MetaDrive checkout's **3.8**
venv; `rosbags` needs 3.10+. In the container the two are one 3.10 interpreter and it simply
works. `ros_frame.refuse_if_unsupported()` fires before the terrain is built rather than letting
an ImportError land three hundred frames into a recording.

### Four faults that produce a bag which opens, renders, and is wrong

None of them raises. Each is why the probe checks a *relationship* rather than a value.

- **A stamp per stream instead of per frame.** MetaDrive's example bridge stamps each topic with
  the wall clock as it arrives at the far end of a socket, so the camera and the boxes of one
  `env.step` land tens of milliseconds apart - half a metre at 50 km/h, baked into the labels.
- **A twist in the world frame.** Correct exactly while the car drives east.
- **The camera-mount conversion.** `camera_rig.py:130` stores `Camera.position` in *MetaDrive's*
  ego frame (x right, y forward, `+heading` LEFT), not the CARLA spec the file is parsed from.
  Converting as though it were CARLA swaps forward for lateral and mirrors left and right. This
  was written wrong first, and `tests/unit/test_ros_schema.py` now parses `rigs/cams.txt` itself
  to assert `cam_left` aims left.
- **A skipped `old_origin_in_current_coordinate`.** 93.8 m on junction-1, on a road that really
  exists. Also the one line that *raises*: `metadata.get(...) or (0.0, 0.0)` is the natural way
  to write the fallback and a two-element numpy array has no truth value.

### Two different attributes name an object's kind

Traffic participants declare `TYPE_NAME`; static objects declare `CLASS_NAME`. Reading only the
first gives barriers the Python class name `TrafficBarrier` instead of `TRAFFIC_BARRIER` - close
enough to look right in a report, wrong enough to miss a filter.

## Progress, outputs, and verification

- [x] **`ros_schema.py` - one frame as messages.** Imports neither MetaDrive nor `rosbags`, so
  every rule about frames, signs, units and stamps is testable with no simulator.
  *Outputs:* 12 topics, 8 vendored `.msg` definitions.
  *Verify:* `uv run pytest tests/unit/test_ros_schema.py` - **32 tests, all passing.**

- [x] **`ros_bag.py` - the writer.** MCAP, `CompressionMode.STORAGE`, zstd.
  *Verify:* a real drive of `junction-1/scenarionet-10hz` produced **364 frames, 3,641 messages
  across 11 topics, 0.97 MB on disk against 34.98 MB of payload (39.4x)**.

- [x] **`ros_frame.py` - the env into a frame.** Boxes from `BaseEngine.get_objects()`, never
  from the dataset's track list.
  *Verify:* the real bag carries **300 distinct object ids**, kinds `CYCLIST`, `PEDESTRIAN`,
  `TRAFFIC_BARRIER`, between **51 and 257 boxes a frame**.

- [x] **`ros_audit.py` - the rig's own method, re-implemented.** Parses the MCAP summary and
  `MessageIndex` records; imports no mcap library and no ROS.
  *Verify:* on the real bag - `32 chunks · compression=['zstd']`, every channel at **10.00 Hz,
  100.00 ms median interval**. That the container is the rig's is proven by a reader sharing no
  code with the writer.

- [x] **`ros_probe.py` - the relationships.** *Verify:* **all 10 checks pass** on the real bag:
  every per-frame topic at 364 messages; 364 of 364 frames sharing one identical stamp; `/tf` and
  `/localization/odometry` agreeing to `0.00e+00`; **the heading pointing along the direction of
  travel, worst 0.0 deg over 363 moving frames**; the twist matching the motion to 0.30 m/s;
  every box with a real size; every one of 364 GNSS fixes landing on the map, lat
  3.184159..3.186394, lon 101.611005..101.612379 - Kuala Lumpur, on the junction.

- [x] **The tape bound.** *Verify:* `--agent-policy idm --extra-seconds 40` ran **824 steps
  against a 379-frame recording**, and MetaDrive's own note reports **251 recorded pedestrians
  and cyclists removed** past it. The bag stopped at 379 and said so. Without it, **445 of 824
  frames - 54% - would have shown a junction emptier than the scenario has.**

- [x] **`scripts/ros-bag.sh` preflight.** Reports actor counts by kind, light count and whether
  the dataset carries a projection, and **refuses `--lights` against a dataset with no
  `dynamic_map_states`** - because a bag whose lights topic is empty for want of a convert flag
  is, later, indistinguishable from a junction that had none.

- [ ] **Cameras.** `image_raw/ffmpeg` (`ffmpeg_image_transport_msgs/FFMPEGPacket`) to match the
  rig, which writes 7.2 KB a frame where raw `sensor_msgs/Image` at `rigs/cams.txt`'s 512x288
  would be 442 KB - 62x, or ~41 GB against the rig's 0.67 GB for a six-camera 780 s drive. The
  mount conversion and `/tf_static` are built and tested; the encoder is not.

- [ ] **Phase B - rviz2, and an independent `ros2 bag info`.** The viewer is **built and
  connected**; the visual check it exists for **has not been made by anyone**. See
  `docs/fixes/2026-09-01-20:19:21-phase-b-was-marked-done-on-log-silence.md`.
  Built as a **separate** image (`docker/ros-viewer/`, `scripts/ros-view.sh`) rather than ROS
  inside `wingfin-sim`: a bag is a file, and looking at one needs ROS and a display, not
  MetaDrive and 13.3 GB of CUDA. The independent `ros2 bag info` came free from a stock
  `ros:jazzy-ros-base` and **is** done.
  *Two corrections to the original scoping, both measured:* it must be **jazzy, not humble** -
  humble's rosbag2 cannot parse our format-v9 metadata at all - and it needs
  `vision-msgs-rviz-plugins` plus a built `wingfin_msgs`, without which rviz2 subscribes to the
  two most valuable topics and silently draws nothing.
  *Verified:* rviz2 opens through XWayland at `OpenGl 4.6`, plays all 11 topics with no
  `Ignoring a topic`, and **actually subscribes** - `ros2 topic info` against a live viewer
  reports 1 subscriber each on `/tf`, `/localization/odometry`, `/planning/route` and
  `/perception/objects`. That rules out a mistyped topic, an unloaded plugin or a QoS mismatch.
  It says nothing about whether a pixel is in the right place.
  *Not verified, and this is the point of the tier:* whether the boxes sit on the road, move
  with the people, and share the car's instant. **No screenshot exists.**
  *Found on the way, and fixed:* every pose claimed `covariance[0] = -1` -
  `sensor_msgs/Imu`'s "not produced", on exact ground truth - and a QoS durability mismatch made
  the route topic deliver nothing. 21 rviz2 warnings a run before, 0 after.

## Known limits, stated rather than hidden

- **Every synthesised channel is truth, not measurement**, and the bag says so: its `wingfin`
  metadata records `source: simulated, noise_model: none`. The rig's GNSS has noise, lag,
  multipath and dropouts - its own `/localization/odometry` skips 12.2% of its cycles - while
  ours is a perfect number every frame. A model trained on both without being told learns to
  trust GNSS absolutely. A `--gnss-noise` is the obvious follow-on; inventing a noise model now
  would be inventing a fact.
- **Altitude is meaningless.** MetaDrive's z is the car's height over a near-flat world, not
  elevation over the ellipsoid.
- **OSM's own accuracy is the floor.** The projection is exact to 0.000000 m against PROJC, and
  inherits whatever the surveyed road geometry is wrong by - typically a few metres.
- **24 of the rig's 55 topics are omitted for want of a `.msg`** (`MISSING_DEFINITIONS`), not
  because the data is unavailable. `/vehicle/state`, `/control/actuators` and the
  `sbg_driver` GNSS family are all computable the moment their definitions exist.
  **And the definitions are recoverable**: rosbag2 writes every type's `.msg` text into the bag,
  so the rig's own bag carries them - see Stage 11, which also records two errors in this very
  table (`/sensing/gnss/imu_data` is missing from it, and two of its entries are ❌ topics rather
  than definition problems).
- **No linear acceleration.** Differencing velocity across a frame would put simulation noise
  into a field a real IMU measures directly; it is published as zero with the covariance marked
  unknown, which is what REP-145 asks of a publisher that has no such data.
- **`ros_frame.py` is the one module `uv run pytest` cannot cover** - it needs a live engine.
  It is deliberately the thinnest of the three, and `ros_probe.py` is what exercises it.
