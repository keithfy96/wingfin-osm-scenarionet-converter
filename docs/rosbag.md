# Rosbag — what a MetaDrive drive can and cannot put on the wire

Scope: turning a drive from this repo into a rosbag shaped like the real vehicle's. The reference
is `bag_audit.html` — a 779.6 s MCAP·zstd bag, **55 topics**, 1,466,940 messages, 10.50 GB. This
file records which of those 55 the simulator can supply, which it can only approximate, and which
it cannot honestly produce at all.

## The headline: the bag has no labels in it

Nothing in the 55 is a ground-truth object. `/perception/inference_control` and
`/perception/model_info` are the model's own configuration and its own output — what the stack
*thought*, not what was there. Nobody labelled the drive, which is normal for a real one.

That is exactly what the simulator supplies for free. A `--export-drive` run writes a recorded
scenario whose `tracks` carry every pedestrian, cyclist, cone, barrier and car with a position,
heading and size **on every frame**, and whose `dynamic_map_states` carry which light was which
colour on every frame.

**So the simulator's job is not to reproduce the 55 topics. It is to produce the pixels with the
answers attached.**

## The verdict, by family

| family | n | MetaDrive |
|---|---|---|
| rig cameras — 6× `image_raw` + 6× `meta` + 6× `camera_info_latched` | 18 | ✅ direct, and closer than you would expect — `rigs/cams.txt` already defines `cam_left` / `cam_front` / `cam_right` + `cam_back_left` / `cam_back` / `cam_back_right` + a forward wide. That is the six `front_*` / `rear_*` views, plus a spare. |
| `/tf`, `/tf_static`, `/localization/odometry` | 3 | ✅ direct |
| `/vehicle/state`, `/vehicle/actuators_output`, `/control/actuators` | 3 | ✅ direct — speed, heading, and the commanded `[steering, throttle_brake]` |
| GNSS/INS — `pose`, `ekf_nav` / `quat` / `euler`, `imu_data`, `imu/data`, `pos_ecef`, `imu/velocity`, `nav_sat_fix`, `gps_pos`, `gps_vel` | 11 | ⚠ synthesisable — `tools/geodesy.py` already does metres → lat/lon and `tools/policy_client.py` already emits a GNSS block. But **noiseless**: no multipath, no dropouts, no EKF lag. |
| `/sensing/lidar/imu` | 1 | ⚠ an IMU, yes; a Livox free-running at 202.9 Hz, no |
| `/sensing/gnss/utc_time`, `imu/utc_ref`, `/vehicle/engagement` | 3 | ⚠ trivial to emit |
| `/sensing/lidar/points` | 1 | ⚠ possible — you dropped it |
| `/control/predicted_trajectory`, `lateral_plan`, `longitudinal_plan`, `/perception/inference_control`, `model_info` | 5 | ⚠ only when a model drives — that is Stage 9, already built |
| `/vehicle/can_rx`, `can_tx` | 2 | ❌ no CAN bus. Synthesising DBC frames would be fabrication (`can_tx` was empty in the bag anyway). |
| `/sensing/cabin/image_raw`, `camera_info_latched` | 2 | ❌ no cabin, no driver |
| `/sensing/cabin/audio_stamped`, `audio_info` | 2 | ❌ no audio |
| `/sensing/gnss/imu/temp`, `/sensing/gnss/status` | 2 | ❌ physical sensor health |
| `/diagnostics`, `/rosout` | 2 | ❌ would be the simulator's logs, not the vehicle's |

**24 direct · 21 approximate · 10 not honestly producible.**

## All 55, one row each, with the rate the real bag ran them at

Measured rates are from `bag_audit.html`; `latched` means a single message at the start.

| topic | Hz | verdict |
|---|---|---|
| `/sensing/camera/cam_sync_rig/front_left/image_raw/ffmpeg` | 20 | ✅ |
| `/sensing/camera/cam_sync_rig/front_middle/image_raw/ffmpeg` | 20 | ✅ |
| `/sensing/camera/cam_sync_rig/front_right/image_raw/ffmpeg` | 20 | ✅ |
| `/sensing/camera/cam_sync_rig/rear_left/image_raw/ffmpeg` | 20 | ✅ |
| `/sensing/camera/cam_sync_rig/rear_middle/image_raw/ffmpeg` | 20 | ✅ |
| `/sensing/camera/cam_sync_rig/rear_right/image_raw/ffmpeg` | 20 | ✅ |
| `…/front_left/meta` … `…/rear_right/meta` (6) | 20 | ✅ |
| `…/front_left/camera_info_latched` … `…/rear_right/camera_info_latched` (6) | latched | ✅ **written** (stage 11 phase 1). Intrinsics come off the rig spec's `width` / `height` / `fov`; needs a `--camera-rig` drive |
| `/tf` | 86.6 | ✅ |
| `/tf_static` | latched | ✅ **written** on a `--camera-rig` drive (stage 11 phase 1) |
| `/localization/odometry` | 43.9 | ✅ |
| `/vehicle/state` | 100 | ✅ |
| `/vehicle/actuators_output` | 100 | ✅ |
| `/control/actuators` | 100 | ✅ |
| `/sensing/gnss/pose` | 50 | ⚠ noiseless |
| `/sensing/gnss/ekf_nav` | 50 | ⚠ noiseless |
| `/sensing/gnss/ekf_quat` | 50 | ⚠ noiseless |
| `/sensing/gnss/ekf_euler` | 50 | ⚠ noiseless |
| `/sensing/gnss/imu_data` | 50 | ⚠ noiseless |
| `/sensing/gnss/imu/data` | 50 | ⚠ noiseless |
| `/sensing/gnss/imu/pos_ecef` | 50 | ⚠ noiseless |
| `/sensing/gnss/imu/velocity` | **200** | ⚠ noiseless, **and above the tick** — see *Rates* |
| `/sensing/gnss/imu/nav_sat_fix` | 5 | ⚠ noiseless |
| `/sensing/gnss/gps_pos` | 5 | ⚠ noiseless |
| `/sensing/gnss/gps_vel` | 5 | ⚠ noiseless |
| `/sensing/lidar/imu` | **202.9** | ⚠ an IMU yes, a free-running Livox no |
| `/sensing/gnss/utc_time` | 1 | ⚠ trivial |
| `/sensing/gnss/imu/utc_ref` | 1 | ⚠ trivial |
| `/vehicle/engagement` | 100 | ⚠ trivial |
| `/sensing/lidar/points` | 10 | ⚠ possible — dropped |
| `/control/predicted_trajectory` | 10 | ⚠ model only |
| `/control/lateral_plan` | 10 | ⚠ model only |
| `/control/longitudinal_plan` | 10 | ⚠ model only |
| `/perception/inference_control` | 10 | ⚠ model only |
| `/perception/model_info` | latched | ⚠ model only |
| `/vehicle/can_rx` | 100 | ❌ no CAN bus |
| `/vehicle/can_tx` | 0 msgs | ❌ empty in the bag too |
| `/sensing/cabin/image_raw/ffmpeg` | 30 | ❌ no cabin |
| `/sensing/cabin/camera_info_latched` | latched | ❌ no cabin |
| `/sensing/cabin/audio_stamped` | 100 | ❌ no audio |
| `/sensing/cabin/audio_info` | 0.2 | ❌ no audio |
| `/sensing/gnss/imu/temp` | 50 | ❌ sensor health |
| `/sensing/gnss/status` | 1 | ❌ sensor health |
| `/diagnostics` | 5.7 | ❌ the simulator's logs, not the vehicle's |
| `/rosout` | 4.0 | ❌ the simulator's logs, not the vehicle's |

## Rates: the simulator tick is the ceiling on all of them

`env.step` **is** the world tick — the policy is called once per step, every camera buffer is
redrawn on each one, and nothing in MetaDrive publishes between two steps. So the bag's rates are
not free choices; they are decimations of `--step-hz`.

- **`--step-hz 100` covers everything up to 100 Hz.** `step_config` returns
  `physics_world_step_size 0.01, decision_repeat 1`. That serves the 100 Hz vehicle topics
  directly, the 50 Hz GNSS every 2nd step, the 20 Hz cameras every 5th, and 10 Hz and 5 Hz and
  1 Hz cleanly under that.
- **Two topics sit above it**: `/sensing/gnss/imu/velocity` at 200 Hz and `/sensing/lidar/imu` at
  202.9 Hz. `--step-hz 200` is representable, but 202.9 Hz is not a decimation of anything and
  never will be — a free-running Livox clock is not the simulator's clock. Interpolate, or publish
  at the tick and say so in the bag's metadata.
- **`decision_repeat 1` is a known special case, not a neutral setting.** `drive.py` carries two
  monkeypatches for it: recording captures nothing at repeat 1, and a replayed car never finds its
  ride height. Both are already installed; the point is that 100 Hz and 200 Hz are the regime
  where they matter.
- **`tick_rate` in a rig spec is read and not honoured.** Buffers redraw once per `env.step`
  whatever the spec asks for, so a rig declaring 0.1 s draws every 0.01 s on a 100 Hz dataset.
  Per-camera rates in a bag have to come from decimating afterwards, not from trusting the spec.
- **Two rates in the real bag are not divisors of anything** — `/tf` at 86.6 Hz and
  `/localization/odometry` at 43.9 Hz (−12.2% against its nominal 50). Those are real-world
  aggregation and jitter. Publish at the nearest achievable rate; do not synthesise the jitter.
- **A dataset may only be replayed at the rate it was written at**, and `drive.py` refuses the
  mismatch rather than warning. A 100 Hz bag needs `workspaces/<ws>/scenarionet-100hz`, not the
  10 Hz one.

## What sits behind each verdict

### The cameras (✅, with four things to get right)

- **`rigs/cams.txt` is already the right rig** — six 512×288 views plus a 1280×720 forward wide,
  which maps onto the bag's six `image_raw` topics with one spare. `rigs/README.md` is the
  reference for the format, which is CARLA's; the conversion into MetaDrive's frame is an x/y swap
  and a sign flip on yaw, and a non-zero `pitch` or `roll` is refused rather than guessed.
- **The spec's own numbers aim two of its four side cameras against their names.** The measurement
  is in `docs/scenario-datapoints.md`; which pair is wrong is not this repo's call. A bag built off
  the spec inherits whichever answer is chosen, so choose it deliberately — a mirrored side camera
  is invisible in a bag and poisonous in training.
- **Nine image buffers is the ceiling** (`MAX_IMAGE_BUFFERS`, `tools/camera_rig.py`), and it is a
  refusal rather than a warning because a rig one camera over the line looks like it works and then
  fails intermittently on a run somebody is relying on. Seven cameras fits; seven plus a depth and
  a semantic view of each does not.
- **`camera_rig.py` wires `rgb_camera` only.** Depth and semantic views are different MetaDrive
  sensor classes, reachable from `tools/sensor_survey.py` and `tools/policy_client.py` but not from
  a rig spec.

### GNSS and INS (⚠ noiseless)

The conversion exists and is tested. `tools/geodesy.py` takes the azimuthal-equidistant origin out
of the map WKT and turns simulator metres into latitude and longitude with no dependency;
`policy_client.py`'s `gps` block already emits latitude, longitude, altitude and the raw MetaDrive
position together, and its `imu` block emits linear and angular velocity, roll, pitch, heading and
speed off the physics body.

What none of that has is **error**. A real `ekf_nav` carries multipath, dropouts, and the lag of a
filter catching up. A synthesised one is the true pose with a different name on it, so anything
that learns or is evaluated on the difference between GNSS and ground truth will find no
difference at all.

### `/sensing/lidar/points` (⚠ possible, and dropped)

MetaDrive has a `PointCloudLidar` and this repo already drives it — `tools/sensor_survey.py`
records a `point-cloud.npy` and `tools/view_point_cloud.py` displays it. Two constraints:

- **A point cloud must not cross the policy socket as uint8.** `depth` and `point-cloud` inherit a
  `_format` that *converts* rather than reformats, and a measured cloud runs −18476.9 to
  +11030.2 m. Neither raises. See `docs/reference/sensors-and-observations.md`.
- It is a **rendered** cloud, not a Livox scan pattern — no dual returns, no intensity off a real
  material, no motion distortion across the sweep.

### The planning and model topics (⚠ Stage 9)

`/control/predicted_trajectory`, `lateral_plan`, `longitudinal_plan`,
`/perception/inference_control` and `model_info` only exist when a model is at the wheel. That path
is built and documented in `docs/reference/openpilot-and-the-model.md` — including that six
conversions stand between the model and the car and not one of them raises when it is wrong, which
is why `scripts/av3-probe.sh` runs before anything steers.

Under `--agent-policy idm` or `replay` these five have no source at all and should be **absent**
from the bag rather than filled with zeros.

### The ten that cannot be produced

CAN, cabin camera, cabin audio, GNSS hardware health, and the ROS logs — each a real physical thing
the simulator does not have. Emitting plausible-looking frames for any of them makes the bag say
something untrue about the vehicle, which is worse than a missing topic: a consumer can test for a
topic that is not there, and cannot test for one that is there and invented.

## What this bag gains that no real bag has

Alongside whichever topics get written, the drive export carries:

- **A box on every object, every frame** — pedestrians, cyclists, cones, barriers, other cars —
  out of the recorded scenario's `tracks`.
- **Which light was which colour, every frame**, out of `dynamic_map_states`.
- **The ego's own true pose**, which is the thing the eleven GNSS topics are approximating.

Two things to hold onto when timing that against the drive:

- **Past the last recorded frame, MetaDrive removes replayed pedestrians and cyclists** and keeps
  static objects, and `--lights tape` freezes on its last colour. A drive that outruns its tape
  loses its walkers mid-bag with nothing logged; `drive.py` prints a note when the overrun is
  material, and `--lights live` keeps cycling.
- **There are two clocks** — `sim_step_seconds(env)` is how far one `env.step` advances the
  simulator, `data_step_seconds(scenario)` is how far one recorded frame covers — and they are
  equal only when the dataset was converted at the rate it is being driven at. Bag timestamps come
  off the first; the ground-truth tracks are indexed by the second.

## Related

**How many of these 55 are actually written today:** ask, rather than reading a number here.

```bash
uv run python tools/ros_probe.py --coverage            # what the code can write
uv run python tools/ros_probe.py bags/j1-lights --coverage   # what a bag actually holds
```

The 55 rows above live in code as `ros_schema.RIG_TOPICS`, and every count — the 45, the
24/21/10 split, how many topics each remaining phase lands — is **derived from them**, including
`ros_schema.MISSING_DEFINITIONS`. That is deliberate: a figure written into this file goes stale
in silence, and did. `/sensing/gnss/imu_data` was producible and missing from the code's table
altogether, one character from the `/sensing/gnss/imu/data` we do publish; `/sensing/gnss/imu/temp`
and `/sensing/gnss/status` sat in it as though a `.msg` were all that stood between a simulator
and a real receiver's temperature. Neither was visible to any check, because nothing
cross-referenced the prose against the code. Now nothing can: `tests/unit/test_ros_schema.py`'s
`TestTheRigCoverageLedger` asserts the 55 rows partition and that our own ground-truth topics are
never counted as coverage of a bag that has none.

**The plan for the rest** is `docs/implementation-plan/stage-11-a-complete-ros-bag.md`, in five
remaining phases.

| file | covers |
|---|---|
| `docs/implementation-plan/stage-11-a-complete-ros-bag.md` | the gap between this table and what is written, in six testable phases |
| `docs/reference/sensors-and-observations.md` | the four sensor modalities, the policy socket, what may cross the wire, what a recording holds |
| `docs/reference/running-the-simulator.md` | the two clocks, `--step-hz` / `--decision-hz`, what a step costs, the step budget |
| `docs/reference/openpilot-and-the-model.md` | the model at the wheel, and the six conversions between it and the car |
| `rigs/README.md` · `docs/scenario-datapoints.md` | the rig format, and the two side cameras aimed against their names |
| `docs/step-timing-rows.md` | what a seven-camera rig costs per step, which is what sets how long a bag takes to make |
