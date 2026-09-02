# Stage 11 - a complete ROS 2 bag

## Status

**Phases 0 to 4 are built; 5 is not.** Stage 10 produces a bag that real ROS 2 reads
and rviz2 renders; this stage is about how much of the vehicle rig's bag it actually covers,
which was **8 of 45** when this was written and is **30 of 45** now that phase 4 has landed.

Written 2026-09-02, after the question *"so it should generate all 45 topics already?"* could not
be answered by any command in the repo. Every count below was derived by script against
`ros_schema.TOPICS` rather than read off `docs/rosbag.md`, and the two disagree - see *Two
defects*, below.

The reference throughout is `bag_audit.html` at the repo root, the audit of the rig's own
`ros2_mig_phase_5_p1` bag: **55 topics**, 1,466,940 messages.

## Summary

```text
Stage 10: the drive written out as a ROS 2 bag  (8 of the rig's 45 topics)  <- where this began
  -> Stage 11: the rest of the bag, in six phases, each one testable on its own
       phase 0  the ledger and the definition extractor      8 / 45   done
       phase 1  camera_info_latched + tf_static             14 / 45   done
       phase 2  the SBG GNSS family                         23 / 45   done
       phase 3  /sensing/lidar/points                       24 / 45   done
       phase 4  image_raw/ffmpeg - the encoder              30 / 45   done
       phase 5  the fifteen rig-typed topics                45 / 45
```

Phases 1-4 are independent of one another and of phase 5. **Only phase 0 must come first**,
because its coverage count is how every later phase is judged done.

### Nothing upstream changes

| | change |
|---|---|
| `src/osm_scenario/` | none |
| `generation_fingerprint` | unmoved. Every flag here is drive-time, like `--ros-bag` itself |
| `workspaces/` | read only |
| `tools/drive.py` | optional extras on one existing call, `drive.py:2478` |

## Where the numbers come from

### 55 topics, 45 producible

`docs/rosbag.md` verdicts the rig's 55: **24 direct, 21 approximate, 10 not honestly
producible.** The ten are CAN (`can_rx`, `can_tx` - there is no bus), the cabin camera and its
`camera_info`, the two cabin audio topics, the GNSS receiver's own temperature and status, and
`/diagnostics` + `/rosout`, which would be the simulator's logs rather than the vehicle's.

**55 - 10 = 45**, and that is the target.

### What Stage 10 covered: 8 declared, 7 on the wire

The state this plan was written against. **Phase 1 has since made it 14 and 7** - it added the
six `camera_info_latched` topics, which are declared always and written only on a
`--camera-rig` drive, exactly like `/tf_static`. The table below is left as it stood so the
starting point stays legible; `tools/ros_probe.py --coverage` is the live figure.

| | count |
|---|---|
| rig topics `ros_schema.TOPICS` declares | **8** |
| ...written on a drive with no `--camera-rig` | **7** - `/tf_static` is guarded by `if mounts:` (`ros_bag.py:251`) |
| topics that are ours, absent from the rig's 55 | 4 |
| **producible, not declared at all** | **37** |

The eight: `/tf`, `/tf_static`, `/localization/odometry`, `/sensing/gnss/pose`,
`/sensing/gnss/imu/data`, `/sensing/gnss/imu/velocity`, `/sensing/gnss/imu/nav_sat_fix`,
`/sensing/lidar/imu`.

The four that are ours: `/perception/objects`, `/perception/traffic_lights`, `/planning/route`,
`/clock`. **These are the point of the exercise and must never be counted against the 45** - the
rig's bag has no ground truth in it, and ours does. A coverage report that treated them as
credit would be flattering itself.

### Two defects, found by script, invisible to every existing check

Both existed because nothing cross-referenced the ledger in `docs/rosbag.md` against the code.
**Both were fixed in phase 0b, and are now unrepresentable** - `MISSING_DEFINITIONS` is computed
from `RIG_TOPICS`, and no `IMPOSSIBLE` row may carry a definition. Kept here because they are
what the phase was for.

- **`/sensing/gnss/imu_data` is missing from `MISSING_DEFINITIONS`.** It is producible, it needs
  an `sbg_driver/SbgImuData` definition, and the table whose entire job is to list exactly that
  omits it. Not to be confused with `/sensing/gnss/imu/data`, which we do publish, as
  `sensor_msgs/Imu` - two topics one character apart, one covered and one forgotten.
- **Two of the 15 `MISSING_DEFINITIONS` entries are not definition problems at all.**
  `/sensing/gnss/imu/temp` and `/sensing/gnss/status` are ❌ rows - physical sensor health. The
  table conflates *"we lack a `.msg`"* with *"a simulator can never produce this"*, and
  `imu/temp`'s own note already says the latter: *"nothing in a simulator produces it"*.

## The fact this stage rests on: the definitions are inside the rig's bag

**`bag_audit.html` records rates, not types.** Every message type named anywhere in that file
amounts to exactly one, `geometry_msgs/TwistStamped`. That is why four `/vehicle` and `/control`
topics have sat in `MISSING_DEFINITIONS` reading *"wingfin message; type not in the audit"*, and
why the five model topics are not listed at all.

**But the audit is not the bag.** rosbag2 writes the full `.msg` text of every type into the bag
itself, dependencies concatenated, and `bags/j1-lights` demonstrates it - measured 2026-09-02
through `Reader.connections[i].msgdef`:

| topic | type | definition |
|---|---|---|
| `/perception/traffic_lights` | `wingfin_msgs/msg/TrafficLightArray` | 654 b |
| `/perception/objects` | `vision_msgs/msg/Detection3DArray` | 1837 b |
| `/localization/odometry` | `nav_msgs/msg/Odometry` | 1502 b |

`wingfin_msgs` exists nowhere but this repo, and its definition still came back out of the file.
So **the rig's bag already contains the exact definition of all fifteen otherwise-unknown
types.** Nothing needs to be guessed, nobody needs to hand over a source package, and the rig
does not need to be running. What is needed is **one `.mcap` file** and forty lines of Python -
and no ROS, because `rosbags` exposes the schemas directly.

This is what turns phase 5 from a research task into a paste.

### So the 37 split by work, not by permission

| | n | needs |
|---|---|---|
| **reachable with nothing from anyone** | **22** | 6 `camera_info_latched` (`sensor_msgs/CameraInfo`, core) · 6 `image_raw/ffmpeg` (`FFMPEGPacket`, *believed* already in `EXTRA_DEFINITIONS` - phase 4 found the vendored text wrong in four ways) · 7 `sbg_driver` (public, copy verbatim) · `imu/pos_ecef` · `imu/utc_ref` · `/sensing/lidar/points` |
| **needs one `.mcap`, then a verbatim paste** | **15** | 6 camera `meta` · `/vehicle/state`, `/vehicle/actuators_output`, `/vehicle/engagement`, `/control/actuators` · the 5 model topics |

### One call site feeds all of it

`drive.py:2478` already holds `action`, `rig` and the model's `prediction` in scope at the moment
it calls:

```python
ros_bag.write(ros_frame.read(env, steps, steps * sim_dt, ros_projection))
```

Every phase below is an optional extra argument threaded through that one line. No new hook, no
second per-frame consumer, and the frame keeps one stamp taken once - which is the fault Stage 10
exists to have avoided.

## Progress, outputs, and verification

- [x] **Phase 0a - `tools/ros_defs.py`, the definition extractor.** Done 2026-09-02.
  Reads `Reader.connections[i].msgdef` and prints each type's `.msg` text in a form that pastes
  straight into `ros_schema.EXTRA_DEFINITIONS`. Needs no ROS and no rig.

  ```bash
  uv run python tools/ros_defs.py bags/j1-lights          # the unknown types only
  uv run python tools/ros_defs.py bags/j1-lights --all    # the core ones too
  ```

  On `bags/j1-lights` it finds **27 definitions across 11 connections, 7 outside the humble
  typestore, all 7 already in `EXTRA_DEFINITIONS`** - so it prints nothing new, which is the
  right answer for a bag written from that table. `--all` renders all 27 as pasteable entries.

  Three things it does that a plain dump would not, each for a failure that is otherwise silent:

  * **It splits the concatenated block.** One connection's `msgdef` is the type *and every type
    its fields reach*, separated by a rule and a `MSG:` line. `wingfin_msgs/TrafficLightArray`
    alone carries four dependencies with it; a tool that emitted the blob whole would give you
    something no `EXTRA_DEFINITIONS` entry can hold.
  * **It normalises `MSG: geometry_msgs/Point` to `geometry_msgs/msg/Point`.** The headers inside
    a definition use the two-part spelling and everything else uses the three-part one. Key a
    dict on both and a recovered definition sits in the file under a name nothing looks up, so
    the type reads as still missing while being right there.
  * **It parses every definition before printing it.** A field in the wrong order serialises
    silently and deserialises into nonsense - the exact fault this whole stage exists to avoid -
    so nothing reaches the clipboard that `get_types_from_msg` will not accept.

  *Verified,* in `tests/unit/test_ros_schema.py::TestReadingDefinitionsBackOutOfABag`, seven
  tests. The round trip is the real one: the test **writes its own bag** from `EXTRA_DEFINITIONS`
  into `tmp_path` and reads it back with nothing but the file, so the gate runs everywhere rather
  than skipping wherever `bags/` happens to be empty - `bags/` is gitignored, and a `skipif` on a
  missing directory is how a check quietly stops running. `TrafficLightArray` and `TrafficLight`
  come back **byte-identical**, their dependencies come back with them, every recovered
  definition parses, and `render`'s output `eval`s back to the text it came from inside 100
  columns. **If our own invented message survives that, the rig's will** - same writer, same
  reader, same file format.

  One thing it cannot do, stated because it is the first thing to check on a rig bag: a
  definition is written by the *recorder*, so a bag whose writer supplied none carries none.
  `MessageDefinition.format` is then `NONE` and the tool lists those topics under **"no
  definition recorded"** rather than inventing anything.

- [x] **Phase 0b - the coverage ledger.** Done 2026-09-02.
  The 55 moved out of `docs/rosbag.md` and into `ros_schema.RIG_TOPICS` as data - topic, the
  rate the real bag ran it at, the verdict, what stands in the way, and the phase that lands it.
  `MISSING_DEFINITIONS` is now **computed from those rows** rather than kept beside them, which
  is what makes both defects unrepresentable rather than merely fixed.

  ```bash
  uv run python tools/ros_probe.py --coverage                    # the code alone, no bag needed
  uv run python tools/ros_probe.py bags/j1-lights --coverage     # what reached the wire
  ```

  As it printed the day 0b landed, with phase 1 still ahead of it:

  ```text
    rig topics produced      8 / 45

    absent, by the phase that lands it
      phase 1  camera_info_latched x6, /tf_static exercised   6
      phase 2  the SBG GNSS family                            9
      phase 3  /sensing/lidar/points                          1
      phase 4  image_raw/ffmpeg - the encoder                 6
      phase 5  the fifteen rig-typed topics                  15
                                                            37

    waiting on a .msg         24   or on which type the rig used; tools/ros_defs.py recovers them
    not producible            10   excluded by design, each with its reason
    simulator extras           4   never counted against the 45 - the rig's bag has no ground truth
  ```

  **The numbers moved from the sketch in this plan, and the sketch was wrong.** It read
  13 / 12 / 5 / 1, which sums to 31 against 37 absent, because it was counting `MISSING_DEFINITIONS`
  *rows* (15, with the six `.../meta` channels behind one wildcard) rather than topics. The
  ledger partitions by phase instead, and that partition reproduces this plan's own ladder
  exactly - 8 -> 14 -> 23 -> 24 -> 30 -> 45 - so "phase 2 is done" now means the same thing in
  the code and in the prose. `waiting on a .msg` is 24 rather than 15 for the same reason: the
  wildcard became six real rows and the five model topics, listed nowhere before, are wingfin
  types too.

  **Both defects fixed, and both now impossible.** `/sensing/gnss/imu_data` is a row carrying
  `sbg_driver/SbgImuData`; `/sensing/gnss/imu/temp` and `/sensing/gnss/status` are `IMPOSSIBLE`
  rows whose reason is the physical one, and a test asserts no `IMPOSSIBLE` row may carry a
  definition or a phase at all.

  *Verified,* in `tests/unit/test_ros_schema.py::TestTheRigCoverageLedger`, 16 tests: the 55 rows
  are distinct and split 24 / 21 / 10; producible is 45; every `TOPICS` key is a rig topic or a
  declared extra; the producible rows partition into written-plus-one-phase with nothing owned
  twice; the phase counts are the ladder above; `SIMULATOR_EXTRAS` never appears in `produced`;
  `MISSING_DEFINITIONS` names only producible rig topics; a row says what it needs exactly when
  we do not write it; a bag missing `/tf_static` reports 7 against 8 declared; and the two rates
  above the simulator tick are recorded as such. `uv run pytest`: **862 passed, 2 skipped**.
  `uv run ruff check .` clean.

  `docs/rosbag.md` now cites the command instead of carrying the figures, and
  `docs/reference/ros-bags.md`, `docs/testing-ros.md` and stage 10's own limits list were
  corrected from "15" to 24.

  *Acceptance criterion for everything after:* a phase is done when this count moves by the
  number it claimed, and `uv run pytest` still passes.

- [x] **Phase 1 - `camera_info_latched` ×6, and `/tf_static` finally exercised.** `+6`
  **7 / 45 -> 14 / 45 declared. Done 2026-09-02.**

  ```bash
  METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- \
      --out bags/phase1-cams --camera-rig rigs/cams.txt --render offscreen
  uv run python tools/ros_probe.py bags/phase1-cams --workspace workspaces/junction-1
  uv run python tools/ros_probe.py bags/phase1-cams --coverage
  ```

  `sensor_msgs/CameraInfo` is core, so unlike everything still absent these needed **no message
  definition at all** - only a rig. Every intrinsic was already in `camera_rig.Camera`:
  `focal_length_px` is `(width/2) / tan(fov/2)`, because `camera_rig.mount` sets the lens with
  panda3d's one-argument `setFov`, which takes the **horizontal** angle. `fy == fx`, the
  principal point is the image centre, `p` is `k` with a zero translation column, and `d` is
  **empty against an empty `distortion_model`** - `plumb_bob` with five zeros would claim a
  calibration was done and came out perfect, which is the same untruth `UNKNOWN_COVARIANCE`
  exists to avoid.

  The same drive lights up `/tf_static`, which had never once been written into a bag.

  Measured on `bags/phase1-cams`, a 364-frame replay of junction-1:

  ```
  3641 messages, 11 channels   ->   3648 messages, 18 channels
  +6 camera_info_latched (one each, latched) +1 tf_static (7 transforms)
  ```

  **The name map, and the one place it is contested.** `rigs/av3.txt` was generated from the
  checkpoint's own `camera_order`, so its six names are already the rig's and the map is the
  identity. `rigs/cams.txt` names its cameras after the file and needs a translation - and
  **contradicts itself about which way two of them face.** `camera_rig.Camera.aim` had already
  measured it: that file reads `+yaw` as right on its front pair and as left on its back pair, so
  two of its four side cameras are named backwards under either reading, and `cam_back_left` is
  mounted aiming rear-**right**.

  Three sources, none allowed to overrule another in silence: `RIG_CAMERA_NAMES` follows the
  spec's labels, `/tf_static` carries the geometry, and `camera_side_disagreements` names every
  camera where the two part company - printed by `drive.py` as the recording starts and by
  `ros_probe.py` off the bag afterwards. It is **printed and never checked**, because the
  disagreement is a property of the input file and failing a bag for faithfully carrying it would
  be blaming the writer for the spec. `rigs/cams.txt` yields exactly two rows; `rigs/av3.txt`
  yields none, which is its own header's claim, now tested.

  `cam_front_wide` gets a `/tf_static` transform and **no rig topic**: it is a seventh buffer with
  no channel on the vehicle, and inventing a seventh `cam_sync_rig` topic would put something in
  our bag the rig's bag cannot have. Six lenses, seven transforms - and the probe checks each
  `camera_info`'s `frame_id` against a real transform, since a camera in one and not the other is
  a half-converted rig that deserialises perfectly on both topics.

  *Verified.* `ros_probe` on the bag: **all 16 checks passed**, two of them new - every
  `camera_info` latched at exactly one message, and every one joining a `tf_static` transform -
  plus the intrinsics check (`fx == fy`, principal point centred, `p` agreeing with `k`) and the
  recovered angle printed per camera, `f=365.6 px, fov 70.0° h / 43.0° v` on all six, which is
  the spec's own 70 back out of `k`. In `tests/unit/test_ros_schema.py`, 19 new tests across
  `TestTheCameraIntrinsics`, `TestTheRigCameraNames` and `TestTheCameraTopicsInAWrittenBag` - the
  last writing a real MCAP from `rigs/cams.txt` itself, so a change to that file cannot quietly
  invalidate them, and asserting the six topics are offered **transient-local** like the route.
  `uv run pytest`: **881 passed, 2 skipped** (from 862). `uv run ruff check .` clean.

  **One defect found on the way, and it is the one this phase made worth fixing.**
  `--ros-topics` says "the subset of the `--ros-bag` topics to write", and
  `ros_bag.start_episode` never consulted it: the route and `/tf_static` went in unconditionally,
  so `--ros-topics /tf` produced a bag of three channels. That was two surprises and would have
  become eight. `_wanted` now gates all three kinds of latched topic, pinned by a test that
  selects `/tf` plus one camera and asserts the bag holds exactly those two. The flag is what
  makes per-phase testing cheap, so a flag that quietly keeps eight other channels is worth more
  than a cosmetic fix.

  *The count moved by exactly the 6 it claimed*, and the two numbers stay apart: 14 declared,
  and 7 on the wire for a drive with no rig - `tools/ros_probe.py bags/j1-lights --coverage` now
  lists all seven declared-but-unwritten topics by name.

- [x] **Phase 2 - the SBG GNSS family.** `+9`   **14 / 45 -> 23 / 45**
  `sbg_ros2_driver` is public; copy the `.msg` text **verbatim** into `EXTRA_DEFINITIONS`. Seven
  topics: `ekf_nav`, `ekf_quat`, `ekf_euler`, `imu_data`, `gps_pos`, `gps_vel`, `utc_time`. Plus
  `imu/pos_ecef` (`geometry_msgs/PointStamped` in most SBG drivers, unconfirmed) and
  `imu/utc_ref` (`sensor_msgs/TimeReference` - **already in the humble typestore, so it was never
  definition-blocked at all**, only semantically unknown).

  Every value is a re-shaping of the pose and velocity already published. `tools/geodesy.py`
  does metres → lat/lon (`aeqd_inverse`, exact to 0.000000 m against PROJ) and ECEF is one more
  standard conversion off the same fix.

  *Verify:* round-trip each new type through CDR before it is written down - a field out of order
  serialises silently and deserialises into nonsense. Then a probe check that `ekf_nav` and
  `nav_sat_fix` agree to 0 m on every frame, since both derive from one position; two channels
  disagreeing is the only way this can be wrong without raising. `MISSING_DEFINITIONS` shrinks by
  exactly nine and phase 0's counter proves it.

  *Done.* `tools/sbg_msgs/` holds the twelve `.msg` files - seven message types and the five
  nested status submessages they name - copied byte for byte from `SBG-Systems/sbg_ros2_driver`
  at tag **3.4.0**, commit `3efaf29`, and loaded at import by `ros_schema._sbg_definitions`.
  Files rather than string literals, because "copied verbatim" is a claim somebody has to be able
  to diff against upstream, and rewrapping a `.msg` into a 99-column Python literal is precisely
  where a field changes order.

  **Two of the nine were never definition-blocked at all.** `imu/pos_ecef` is a
  `geometry_msgs/PointStamped` and `imu/utc_ref` a `sensor_msgs/TimeReference`, both in the
  humble typestore since before this repo existed; they sat in `MISSING_DEFINITIONS` because
  nobody had decided what the rig meant by them, which is a different problem wearing the same
  label. `geodesy.geodetic_to_ecef` is the one piece of arithmetic that was actually missing.

  **A `sbg_driver` message is version-dependent and nothing catches a mismatch.** Measured
  against 3.1.0: `SbgGpsPosStatus` went from 7 fields to 22, `SbgEkfStatus` from 16 to 23 with
  two *removed*, `SbgUtcTime` 11 to 14. CDR carries no field names, so a subscriber built against
  3.1.0 reading our `SbgEkfStatus` reads `dvl_bt_used` as `gps1_course_used` and carries on. Two
  things guard it: rosbag2 writes the definitions into every bag, and the version is recorded as
  `sbg_driver_version` in each bag's own `wingfin` metadata. Which version the rig recorded is
  still unknown - `bag_audit.html` carries rates, not types - so the day a rig bag arrives,
  `tools/ros_defs.py` compares them instead of anyone assuming.

  **Absence had to be encoded, in message types with no way to state it.** `sensor_msgs/Imu` has
  its `-1` and `NavSatFix` its covariance type; `SbgImuData.temp` is a bare float32 and every
  value one can hold is a temperature, `0.0` included - which reads as a sensor at freezing. Four
  quantities are therefore **NaN** (`ABSENT_SCALAR`, verified round-tripping through CDR):
  `accel` (the same refusal `imu_message` already makes), `temp` (whose topic-level twin
  `/sensing/gnss/imu/temp` is one of the ten excluded from the 45 for exactly this reason),
  `delta_vel` / `delta_angle` (a strapdown integrator's intermediates; there is no integrator),
  and `undulation`. An *accuracy* of zero is the opposite case and stays a number - for ground
  truth "1-sigma is zero" is true - and where the type provides its own N/A it is used instead,
  so `num_sv_tracked` is `0xFF` as `SbgGpsPos.msg` documents.

  **The drive declares the GPS epoch as its `t = 0`.** `SbgUtcTime` wants a calendar date and
  `gps_tow` a time of week; the simulator has neither. A wall clock read at conversion time would
  claim the drive happened then and make two runs of one drive differ, and a recent-looking date
  would be worse because it would be believed. So `t = 0` is 1980-01-06T00:00:00Z: elapsed time
  inside the drive is exact, the date is a sentinel, and `clock_utc_status` is `0` - the message's
  own "the UTC time is not known" - so the caveat is in-band. `imu/utc_ref` publishes the sim
  clock and that declared UTC as two values differing by exactly `315964800 s`; equal values would
  have said "our clock is UTC".

  *Measured*, `bags/phase2-sbg`, a 364-frame `junction-1` drive with `rigs/cams.txt`:
  **3,648 messages across 18 topics -> 6,924 across 27**. `MISSING_DEFINITIONS` 24 -> 15. The
  probe went 16 checks to **24, all passing**, and the eight new ones are each one channel
  against another rather than against a constant, because every value here is a re-shaping of one
  position and one velocity: `ekf_nav` against `nav_sat_fix` (worst `0.000e+00 m` over 364
  frames), `gps_pos` against `ekf_nav`, `pos_ecef` converted back through `geodesy` (`0.000e+00
  m`, frame `earth`), `ekf_quat` / `ekf_euler` / `imu/data` as one rotation (worst yaw
  `5.09e-14 deg`), `gps_vel`'s course against its own velocity measured from **east** (worst
  `7.52e-06 deg`, the tolerance being float32), the four NaN fields still NaN, `imu_data`'s gyro
  bit-identical to `imu/data`'s angular velocity, and the two clocks offset by the declared epoch.

  *Verified* in 23 new tests across `TestTheSbgFamily` and `TestTheSbgDefinitionsInAWrittenBag`,
  the second of which writes a real MCAP and checks every field of all twelve types against the
  upstream files - the only place anything is checked against upstream rather than against our own
  code. **`uv run pytest`: 904 passed, 2 skipped** (from 881); `ruff check` clean.

  *One earlier claim corrected on the way.* This work first recorded that the `.msg` comments
  travel into the bag "since rosbag2 stores the definition text in the bag itself". Measured on
  `bags/phase2-sbg`, they do not: `rosbags` **regenerates** the definition from its parsed
  typestore when it writes a connection, so the bag carries field lines only. The field lists
  round-trip exactly, which is the half that matters for decoding; `tools/sbg_msgs/README.md` and
  `_sbg_definitions` now say so, and a test asserts it so a future `rosbags` that starts carrying
  them is a notification rather than a surprise.

  *One pre-existing test had to be loosened rather than satisfied.*
  `test_the_vendored_definitions_are_parseable_message_text` asserted two whitespace-separated
  tokens per line, which held while every entry was a compact hand-written string and rejects an
  upstream file with comments in it. It now runs each definition through `get_types_from_msg` -
  the parser the code actually depends on - which is a stronger check than the shape heuristic it
  replaced and covers both families.

- [x] **Phase 3 - `/sensing/lidar/points`.** `+1`  **23 / 45 -> 24 / 45.** *Done.*
  Verdicted *"possible - you dropped it"*. `sensor_msgs/PointCloud2` is core.
  **Read `docs/reference/sensors-and-observations.md` first:** the point-cloud sensor inherits a
  `_format` that *converts* to uint8 rather than reformats, and a cloud running −18476.9 to
  +11030.2 m does not survive that. The fault is on the policy socket and reading the sensor
  in-process should avoid it - **should**, which is a thing to measure rather than assume.

  *Verify:* a probe check that the points land inside the map's extent - the same containment
  test that already catches a skipped `old_origin_in_current_coordinate`, which is 93.8 m on
  junction-1 and looks entirely plausible when wrong.

  **Measured, and the assumption above was half right.** `DepthCamera._format`, which
  `PointCloudLidar` inherits, overrides the uint8 conversion: `to_float=True` returns the array
  **untouched** and only `to_float=False` converts. So `perceive(to_float=True)` is bit-identical
  to `get_rgb_array_cpu()` - ratio exactly 1.0 on every element - and reading in-process does
  avoid the fault, provided that flag stays as it is. `tools/ros_frame.lidar_cloud` is the line
  that has to keep it.

  **The cloud is published in a `lidar` frame, not in `map`, and that was the design decision.**
  MetaDrive hands the sweep over on world axes with its origin at the sensor, so a `map`-frame
  cloud would need no arithmetic from us at all - and for that exact reason nothing about it
  could ever be checked. Turning it into the sensor's own frame is one rotation by minus the
  car's heading, and the wrong sign produces the same points rigidly rotated: a plausible road,
  a plausible density, a plausible extent, every point behind the car, nothing raising. What
  catches it is that a forward-facing sensor cannot return a point behind itself, so in the
  correct frame every point lies inside the sensor's own FOV. Measured over a full drive:
  **2,320,220 of 2,320,220 points ahead, worst bearing 32.49 deg against a 32.50 deg
  half-angle** - and 0.00% with the sign flipped.

  **A miss keeps its slot and is written NaN**, so the sweep stays organised - 64 beams of 200
  rays - which is `is_dense: false`'s whole purpose and the one structure a lidar payload has.
  The range that decides a miss is **declared, not enforced by MetaDrive**: the depth buffer's
  far plane is 100 km, so an unhit ray comes back as a point up to 18 km out rather than as
  nothing. Default 200 m, `--ros-lidar-range` to move it, and roughly half of every sweep on
  junction-1 is sky.

  **A new failure was found on the way, and it is silent.** A 7-camera rig plus the cloud is
  8 image buffers, and above 7 the depth buffer comes back at its far plane: the env does not
  crash, `perceive` returns the right shape, and every sweep differs from the last, so nothing
  raises or repeats - but **99.8% of the cloud is NaN**. Measured on junction-1 over 12 sweeps
  at each size: 1, 4 and 7 buffers all give 48.52..57.70% of rays within range; 8 gives
  0.10..0.27%. `camera_rig.MAX_IMAGE_BUFFERS` did not cover this - its 5/5 counts runs that
  *survive*, and this one survives. So there is a second, lower ceiling
  (`MAX_BUFFERS_WITH_POINT_CLOUD = 7`), `drive.py` refuses the pairing by name, and the probe
  checks the share of rays that hit. `rigs/cams.txt` is exactly one camera over, and the one
  over is `cam_front_wide`, the spare with no rig topic - a six-camera rig plus the cloud
  records and passes all 30 checks.

  *Verified:* a full offscreen drive on junction-1, `364 frames, 7288 messages across 28
  topics`, the cloud on the wire at **10.00 Hz** - the rig's own rate, because a sweep is
  written at the decision rate and not every step. `ros_probe` **24 checks -> 30**, all passing,
  the six new ones being the payload's shape against its header, the FOV, NaN-in-place, the
  hit share, that no two sweeps are the same buffer republished, and that the cloud lands on
  the OSM extent within its own range. Coverage `24 / 45`. `uv run pytest` and
  `uv run ruff check .` clean. The bag grows from 672 KB to 24 MB - 74.83 MB of payload at
  3.14x - which is what a real sensor costs and the reason the flag is opt-in.

- [x] **Phase 4 - `tools/ros_encode.py`, the encoder.** `+6`  → **30 / 45**
  `--ros-camera` on a drive that already has `--camera-rig`. Six H.264 streams out of the rig's
  own buffers, `libx264` at crf 23, one keyframe a second, at the **decision** rate - because
  `frame_gate` re-uses the last drawn picture on a held step and encoding it again under a new
  stamp tells a reader the world froze. Off by default: it is the one channel in the bag that
  costs real time per frame.

  **`FFMPEGPacket` was already defined and the definition was wrong**, which is the finding of
  this phase. It had the fields in a different order, two of them the wrong width, one invented
  (`frame_id`) and one missing (`is_bigendian`) - and none of it could raise, because no topic
  used the type. A bag written against it opens perfectly: rosbag2 stores the definition beside
  the data, so our own reader agrees with itself, and only a consumer with the real package
  installed finds it is reading the encoding string out of the width field. It is exactly the
  fault `ros_schema.py`'s own comment warns about for `vision_msgs`, sitting unexercised in the
  file. Now verbatim from `ffmpeg_image_transport_msgs` 1.1.2, humble branch, commit
  `5395eac`, pinned character for character by a test. **The general lesson - a `.msg` no topic
  writes is a `.msg` nothing checks - is in CLAUDE.md.**

  Three more things that are silent when wrong, all measured and all pinned:
  **the pictures are BGR** (MetaDrive's `get_rgb_array_cpu` returns panda3d's buffer channels
  untouched; `mode="rgb"` is the branch that reverses), **a vertical flip** is described by no
  field in the message, and **`tune=zerolatency` is load-bearing** rather than a performance
  setting - it is what makes libx264 one-in-one-out, which is what lets a packet carry the stamp
  of the `env.step` that drew it. With a lookahead the packet coming out belongs to an earlier
  step, and a delayed packet is a perfectly valid packet.

  `veryfast` rather than the reflex `ultrafast`: measured on 512×288, `veryfast` is both smaller
  (1,277 vs 1,412 bytes a frame) and quicker (0.96 vs 1.59 ms), and `medium` is worse on both.

  **The read is not shared with `model.observe`, deliberately.** That one happens *before*
  `env.step` and pulls buffers drawn on the previous decision - av3_base's own ordering, and
  right for a model acting on what it has already seen. A bag frame claims that these pixels and
  this pose are one instant, so its picture is the one drawn by the step whose pose it carries.

  *Verified:* a full offscreen drive on junction-1 with `rigs/cams.txt`, `364 frames, 9108
  messages across 33 topics`, **6,075 bytes a frame** against the rig's own measured 7,159 and
  **73× smaller** than the 442 KB an uncompressed `sensor_msgs/Image` would be. `ros_probe`
  **24 checks → 31**, all passing; the same drive with six cameras and `--ros-lidar` gives
  **30 / 45 on the wire, 34 topics, 37 checks**. The seven new probe checks include the one that
  matters - the packets are decoded back and looked at, because a bag full of well-formed
  packets carrying the wrong pixels opens, plays and renders. Coverage `30 / 45`. `uv run
  pytest` 947 passed and `uv run ruff check .` clean. The bag grows from 672 KB to 14 MB.

  **One check had to be rewritten after it was measured.** "No two consecutive decoded frames are
  identical" is the obvious held-buffer test and **it does not work**: re-encoding one identical
  source frame ten times produces zero exact repeats, because a keyframe and the P-frames after
  it quantise differently. The measured separation is 47-61 dB for a held stream against 24.8 dB
  for a moving one, so the probe uses a 40 dB ceiling on the *median* - the median because a car
  stopped at a red really does draw the same picture twice - backed by the structural check that
  actually settles it: the packets arrive at the decision rate the bag's own metadata declares,
  10.00 Hz measured against 10.00 Hz, where a stream written every step would be `stride` times
  too fast.

  `av>=15,<18` joins `rosbags` in the `ros` group. The wheel carries its own ffmpeg statically,
  libx264 included, so nothing is added to `docker/Dockerfile` but a rebuild - and
  `refuse_if_unsupported` checks `av.codecs_available` by name, because a PyAV built against a
  distro ffmpeg without `--enable-libx264` imports perfectly and has no encoder.

- [ ] **Phase 5 - the fifteen rig-typed topics.** `+15`
  Isolated deliberately, so nothing above waits on it. The whole input is one `.mcap` from
  `ros2_mig_phase_5_p1` - not the rig running, not the wingfin source package, and not
  `bag_audit.html`, which carries rates and no types.

  ```bash
  uv run python tools/ros_defs.py /path/to/rig-bag      # every type's .msg text, verbatim
  ```

  Paste the fifteen into `EXTRA_DEFINITIONS`, then fill them from what `drive.py:2478` already
  has in scope: `action` is the commanded `[steering, throttle_brake]` behind `/control/actuators`
  and `/vehicle/actuators_output`; the ego's speed and heading are `/vehicle/state`; `prediction`
  is the five model topics, on an `--agent-policy remote` drive only.

  **Nothing here is guessed**, and that is the whole design. Copying is what `ros_schema.py`
  already does for `vision_msgs` and `FFMPEGPacket`, for the reason its own comment gives: a
  field out of order serialises silently.

  Until that file exists these stay in `MISSING_DEFINITIONS`, with the reason corrected from
  *"type not in the audit"* to **"the audit carries rates, not types - recoverable from any rig
  bag with `tools/ros_defs.py`"**. That tells the next reader what to do, rather than only what
  is wrong.

## Testing one phase at a time

`--ros-bag-topics` already exists (`drive.py:1385`) and takes a comma-separated subset, so a
phase can be exercised without re-recording everything. The ladder in `docs/testing-ros.md` is
unchanged and each phase adds one rung to tier 2:

```bash
uv run python tools/ros_probe.py bags/j1-lights --coverage
uv run pytest && uv run ruff check .
```

`ruff format --check` fails on eight pre-existing files and is not a gate.

## Known limits, stated rather than hidden

- **A topic present is not a topic correct.** Coverage counts names on the wire. Every phase
  above therefore carries a *relationship* check - two independently produced quantities that
  must agree - because that is the only kind of check that catches the faults this stage can
  introduce.
- **Every synthesised channel is still truth, not measurement**, and adding nine GNSS topics
  multiplies that rather than fixing it. The bag says so in its own metadata
  (`source: simulated`, `noise_model: none`); the rig's receiver has noise, lag, multipath and
  dropouts, and its own `/localization/odometry` skips 12.2% of its cycles. A `--gnss-noise` is
  the obvious follow-on and inventing a noise model now would be inventing a fact.
- **`/sensing/gnss/imu/utc_ref`'s semantics remain unknown** even after phase 2. The type is
  standard; what the rig puts in it is not, and a plausible-looking time reference is exactly the
  kind of thing nothing downstream would question.
- **45 of 45 is not parity.** The ten ❌ topics stay absent on purpose, and `/vehicle/can_tx` was
  empty in the rig's bag anyway. A bag that claimed 55 would be claiming a CAN bus.
- **The camera streams are a rendered scene, not a camera.** No lens distortion, no rolling
  shutter, no exposure, no noise, no motion blur - `camera_info` says so with an empty `d` and
  an empty `distortion_model`, which is ROS's way of stating that no distortion is modelled
  rather than that one was measured and came out zero. `rigs/av3.txt` records the same thing
  about its four fisheye corners, which are rendered unwarped.
- **The camera rate is the drive's decision rate, not the rig's 20 Hz.** `env.step` is the world
  tick, so every rate in this bag is a decimation of `--step-hz`; a default drive gives 10 Hz
  and `--step-hz 100 --decision-hz 20` gives the rig's. Resampling to 20 in the writer would be
  inventing frames.
- **`rigs/cams.txt` contradicts itself about which way two of its cameras face**, and phase 1
  reports that rather than resolving it. Its front pair reads `+yaw` as right and its back pair
  reads it as left, so `cam_back_left` publishes as `rear_left` and is mounted aiming rear-right.
  The transform carries the geometry and a consumer that follows `frame_id` gets the truth; one
  that trusts the topic name alone does not. Nothing here can fix that - **the spec file is what
  disagrees** - and the only two honest moves are to say so on every run, which `drive.py` and
  `ros_probe.py` now do, or to correct `cams.txt`, which would reprice every step-timing figure
  in the repo. `rigs/av3.txt` has both columns agreeing by construction and yields no rows.

## Related

- `docs/rosbag.md` - the 55, one row each, with the rate the real bag ran them at
- `docs/implementation-plan/stage-10-ros-bags-out-of-a-drive.md` - what is built
- `docs/testing-ros.md` - the tier ladder, cheapest check first
- `docs/reference/ros-bags.md` - measurements
- `docs/fixes/2026-09-01-20:19:21-phase-b-was-marked-done-on-log-silence.md` - the two items
  still needing a person
