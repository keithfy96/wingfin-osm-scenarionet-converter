# Stage 11 - a complete ROS 2 bag

## Status

**Nothing here is built.** Stage 10 produces a bag that real ROS 2 reads and rviz2 renders; this
stage is about how much of the vehicle rig's bag it actually covers, which is **8 of 45**.

Written 2026-09-02, after the question *"so it should generate all 45 topics already?"* could not
be answered by any command in the repo. Every count below was derived by script against
`ros_schema.TOPICS` rather than read off `docs/rosbag.md`, and the two disagree - see *Two
defects*, below.

The reference throughout is `bag_audit.html` at the repo root, the audit of the rig's own
`ros2_mig_phase_5_p1` bag: **55 topics**, 1,466,940 messages.

## Summary

```text
Stage 10: the drive written out as a ROS 2 bag  (8 of the rig's 45 topics)
  -> Stage 11: the rest of the bag, in six phases, each one testable on its own
       phase 0  the ledger and the definition extractor      8 / 45   done
       phase 1  camera_info_latched + tf_static             14 / 45
       phase 2  the SBG GNSS family                         23 / 45
       phase 3  /sensing/lidar/points                       24 / 45
       phase 4  image_raw/ffmpeg - the encoder              30 / 45
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

### What Stage 10 covers: 8 declared, 7 on the wire

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
| **reachable with nothing from anyone** | **22** | 6 `camera_info_latched` (`sensor_msgs/CameraInfo`, core) · 6 `image_raw/ffmpeg` (`FFMPEGPacket`, **already in `EXTRA_DEFINITIONS`**) · 7 `sbg_driver` (public, copy verbatim) · `imu/pos_ecef` · `imu/utc_ref` · `/sensing/lidar/points` |
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

- [ ] **Phase 1 - `camera_info_latched` ×6, and `/tf_static` finally exercised.** `+6`
  `sensor_msgs/CameraInfo` is core, and every intrinsic it needs - `width`, `height`, `fov` - is
  already parsed into `camera_rig.Camera`. One latched message each. The same drive lights up
  `/tf_static`, whose mount conversion is built and tested (`ros_frame.mounts_from_rig`) and has
  **never once been written into a bag**.

  Needs a name map. `rigs/cams.txt` declares seven cameras -
  `cam_left` · `cam_front` · `cam_right` · `cam_back_left` · `cam_back` · `cam_back_right`, plus
  `cam_front_wide`, a spare with no rig topic - against the rig's six,
  `front_left` · `front_middle` · `front_right` · `rear_left` · `rear_middle` · `rear_right`.

  *Verify:* `--camera-rig rigs/cams.txt` writes six `camera_info_latched` and a `/tf_static`; a
  probe check that each `CameraInfo`'s `K` is consistent with the FOV and size it was built from;
  and **that the left and right cameras have not traded places** - `cam_left` is spec `yaw: -55`,
  stored by `camera_rig.py:414` as `hpr[0] = +55`, and `+55` in ROS is 55° to the left. Every
  part of that is silent when wrong.

- [ ] **Phase 2 - the SBG GNSS family.** `+9`
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

- [ ] **Phase 3 - `/sensing/lidar/points`.** `+1`
  Verdicted *"possible - you dropped it"*. `sensor_msgs/PointCloud2` is core.
  **Read `docs/reference/sensors-and-observations.md` first:** the point-cloud sensor inherits a
  `_format` that *converts* to uint8 rather than reformats, and a cloud running −18476.9 to
  +11030.2 m does not survive that. The fault is on the policy socket and reading the sensor
  in-process should avoid it - **should**, which is a thing to measure rather than assume.

  *Verify:* a probe check that the points land inside the map's extent - the same containment
  test that already catches a skipped `old_origin_in_current_coordinate`, which is 93.8 m on
  junction-1 and looks entirely plausible when wrong.

- [ ] **Phase 4 - `tools/ros_encode.py`, the encoder.** `+6`
  The one genuinely new module. `ffmpeg_image_transport_msgs/FFMPEGPacket` is **already
  defined**; what is missing is the H.264 encode behind it. The rig writes 7.2 KB a frame where
  raw `sensor_msgs/Image` at `cams.txt`'s 512×288 would be 442 KB - **62×**, or ~41 GB against
  the rig's 0.67 GB for a six-camera 780 s drive. Raw is not an option.

  Written at the **decision rate**, not the step rate. `frame_gate.py` re-uses the last drawn
  frame on a held step, and writing it again under a new stamp tells a model the world froze.

  *Verify:* decode the bag's packets back to frames and compare against `rig.read()`. The encoder
  is the one thing in this stage that can be wrong in a way no header check sees - a bag full of
  well-formed packets carrying the wrong pixels opens, plays, and renders.

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

## Related

- `docs/rosbag.md` - the 55, one row each, with the rate the real bag ran them at
- `docs/implementation-plan/stage-10-ros-bags-out-of-a-drive.md` - what is built
- `docs/testing-ros.md` - the tier ladder, cheapest check first
- `docs/reference/ros-bags.md` - measurements
- `docs/fixes/2026-09-01-20:19:21-phase-b-was-marked-done-on-log-silence.md` - the two items
  still needing a person
