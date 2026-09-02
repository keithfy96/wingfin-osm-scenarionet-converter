"""One simulator frame, as ROS 2 messages, under the reference rig's own topic names.

    from ros_schema import Frame, Ego, Box, messages, TOPICS

**This module imports neither MetaDrive nor `rosbags`, deliberately.** It takes a plain `Frame`
and returns plain nested dicts, so every rule about frames, signs, units and stamps is testable
under `uv run pytest` without a simulator, a GPU or a bag writer. `ros_bag.py` turns those dicts
into CDR; `drive.py` fills the `Frame` from the env. Neither of those two files decides anything
about content, and that split is the point: a sign error is silent (see `heading` below), so the
part that can be got wrong has to be the part that is cheapest to test.

Topic names, message types and rates follow `bag_audit.html` - the audit of the real vehicle's
`ros2_mig_phase_5_p1` bag - so a simulated bag and a recorded one are interchangeable to
whatever reads them. Where that bag carries a message type we do not have a definition for
(`/vehicle/state`, `/vehicle/engagement`, `/control/actuators`, the six `.../meta` channels and
the `sbg_driver` GNSS family), the topic is **left out rather than published with a
different type under the same name**: a subscriber that deserialises `wingfin_msgs/VehicleState`
would fail on a `geometry_msgs/TwistStamped` wearing its topic name, which is worse than an
absent topic. `MISSING_DEFINITIONS` lists them; add the `.msg` text to `EXTRA_DEFINITIONS` and
they light up.

Two conventions are load-bearing and both were read out of the source rather than assumed:

* **MetaDrive's world frame is ENU and needs no rotation for `map`.** `heading_theta` is radians
  CCW from +x and `velocity` is `(east, north)` (`policy_client.py:253`, `av3_model.py:332`),
  which is what REP-103 asks for. This is *not* the same convention as `camera_rig.py:255`,
  which describes how a CARLA-shaped rig spec is mounted on the car - ego-local, and mirrored.
  Mixing the two is the exact shape of the openpilot sign fault, where one end counted left
  positive and the other negative and the car drove smoothly into oncoming traffic with nothing
  raising anything. `ros_probe.py` measures it on a known turn instead of trusting this
  paragraph.
* **Latitude and longitude are real, and exact.** `geodesy.aeqd_inverse` reproduces PROJ to
  0.000000 m over +/-900 m of the origin (its own docstring records the comparison), and the
  dataset carries the projection Stage 1 chose in `metadata.coordinate_system_wkt`. The trap is
  `old_origin_in_current_coordinate`: MetaDrive re-centres every scenario on the ego's first
  position, 93.8 m on junction-1, and a reading that skips the shift is wrong by that much while
  looking entirely plausible. `Frame.projection` carries the corrected origin so the subtraction
  happens once, in `drive.py`, rather than at every call site.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --- the message definitions the humble typestore does not carry ------------------------
#
# Neither package is part of the ROS core, so `Stores.ROS2_HUMBLE` has never heard of them.
# `rosbags` parses `.msg` text at runtime and registers the result - no package, no colcon, no
# build step - and both were verified round-tripping through CDR before being written down here.
# Copied from the upstream definitions rather than invented; a field out of order would
# serialise silently and deserialise into nonsense.
EXTRA_DEFINITIONS: dict[str, str] = {
    "vision_msgs/msg/ObjectHypothesis": "string class_id\nfloat64 score\n",
    "vision_msgs/msg/ObjectHypothesisWithPose": (
        "vision_msgs/ObjectHypothesis hypothesis\ngeometry_msgs/PoseWithCovariance pose\n"
    ),
    "vision_msgs/msg/BoundingBox3D": "geometry_msgs/Pose center\ngeometry_msgs/Vector3 size\n",
    "vision_msgs/msg/Detection3D": (
        "std_msgs/Header header\nvision_msgs/ObjectHypothesisWithPose[] results\n"
        "vision_msgs/BoundingBox3D bbox\nstring id\n"
    ),
    "vision_msgs/msg/Detection3DArray": (
        "std_msgs/Header header\nvision_msgs/Detection3D[] detections\n"
    ),
    "ffmpeg_image_transport_msgs/msg/FFMPEGPacket": (
        "std_msgs/Header header\nstring encoding\nuint32 width\nuint32 height\n"
        "uint32 pts\nuint8 flags\nuint64 frame_id\nuint8[] data\n"
    ),
    # Ours, for a topic the reference bag does not have. A traffic light is a position and a
    # colour name, and no ROS core message carries that: `visualization_msgs/Marker` would put
    # the state in an RGBA value, which renders nicely and is poor training data - "was this
    # light red" should not be a floating-point comparison. Inventing a type is safe here in a
    # way it would not be for one of the rig's own topics, because rosbag2 writes the message
    # definition text into the bag itself, so a reader decodes this without our package.
    "wingfin_msgs/msg/TrafficLight": (
        "string id\nstring status\nstring lane\ngeometry_msgs/Point position\n"
    ),
    "wingfin_msgs/msg/TrafficLightArray": (
        "std_msgs/Header header\nwingfin_msgs/TrafficLight[] lights\n"
    ),
}

# --- the rig's own bag, as data ------------------------------------------------------------
#
# `bag_audit.html`'s 55 topics, one row each, moved here out of `docs/rosbag.md` so that the
# coverage figure is **derived rather than maintained**. A prose ledger and a code table drift
# apart silently, and they had: `/sensing/gnss/imu_data` was producible and missing from
# `MISSING_DEFINITIONS` entirely, while `/sensing/gnss/imu/temp` and `/sensing/gnss/status` sat
# in it as though a `.msg` were all that stood in the way of a simulator reporting a real
# receiver's temperature. Both were found by script against this table; neither was visible to
# any check that existed, because nothing cross-referenced the two.
#
# So `MISSING_DEFINITIONS` below is now **computed from these rows**, and the same rows answer
# "how many of the rig's topics do we write". `tools/ros_probe.py --coverage` prints it.

#: The three verdicts, and the whole basis of the 45. `docs/rosbag.md` argues each one.
DIRECT = "direct"
"""MetaDrive holds the quantity outright: the pixels, the pose, the commanded controls."""

APPROXIMATE = "approximate"
"""Producible, but as truth rather than measurement - no noise, no lag, no dropouts."""

IMPOSSIBLE = "impossible"
"""A real physical thing the simulator does not have. Excluded from the target by design.

Emitting plausible-looking frames for one of these makes the bag say something untrue about the
vehicle, which is worse than a missing topic: a consumer can test for a topic that is not there,
and cannot test for one that is there and invented.
"""


@dataclass(frozen=True)
class RigTopic:
    """One row of the reference vehicle's bag, and what stands between us and it."""

    topic: str
    hz: float | None
    """The rate the rig's own bag ran it at, measured in `bag_audit.html`. None is latched.

    Not a rate we necessarily reach: `env.step` **is** the world tick, so every rate here is a
    decimation of `--step-hz`, and `/sensing/gnss/imu/velocity` at 200 Hz and
    `/sensing/lidar/imu` at 202.9 Hz sit above any rate this repo drives at. A free-running
    Livox clock is not the simulator's clock and never will be.
    """
    verdict: str
    needs: str = ""
    """What stands between us and this topic - or, for an `IMPOSSIBLE` one, why nothing ever
    will. Empty exactly when we already write it."""
    phase: int | None = None
    """The stage-11 phase that lands it. None when it is written already, or never will be."""
    definition: str = ""
    """The `.msg` we do not have, if that is what is missing. Feeds `MISSING_DEFINITIONS`."""

    @property
    def produced(self) -> bool:
        return self.verdict != IMPOSSIBLE and self.phase is None

    @property
    def producible(self) -> bool:
        return self.verdict != IMPOSSIBLE


_WINGFIN = "wingfin message; recover with tools/ros_defs.py off a rig bag"

#: The rig's six cameras, under the names its own bag gives them. Every camera topic is
#: `/sensing/camera/cam_sync_rig/<one of these>/<channel>`.
_CAMERAS = ("front_left", "front_middle", "front_right", "rear_left", "rear_middle", "rear_right")
_CAM = "/sensing/camera/cam_sync_rig/{}/{}"

#: A rig spec's camera names, translated to the rig's. **Only `rigs/cams.txt` needs a row here**
#: - `rigs/av3.txt` was generated from the checkpoint's own `camera_order` and already uses the
#: rig's six names, so it passes through this table untouched, and so will any future spec that
#: names its cameras after the vehicle rather than after the file.
#:
#: A spec camera with no row gets **no rig topic at all**, which is the right answer for
#: `cams.txt`'s seventh camera `cam_front_wide`: it is a spare 1280x720 buffer with no counterpart
#: on the vehicle, and inventing a seventh `cam_sync_rig` channel for it would put a topic in the
#: bag that the rig's bag cannot have. It is still mounted, still rendered, and still in
#: `/tf_static`; it simply is not one of the six.
#:
#: **The names and the geometry disagree in `cams.txt`, and this table does not paper over it.**
#: `camera_rig.Camera.aim` records why: that file reads `+yaw` as *right* on its front pair and
#: as *left* on its back pair, so two of its four side cameras are named backwards whichever
#: convention is chosen, and `cam_back_left` is mounted pointing rear-**right**. Mapping by name
#: keeps the author's labelling; the mount that reaches `/tf_static` is the geometry either way,
#: and `camera_side_disagreements` names every camera where the two do not agree rather than
#: letting one of them win in silence.
RIG_CAMERA_NAMES: dict[str, str] = {
    "cam_front": "front_middle",
    "cam_left": "front_left",
    "cam_right": "front_right",
    "cam_back": "rear_middle",
    "cam_back_left": "rear_left",
    "cam_back_right": "rear_right",
    **{name: name for name in _CAMERAS},
}


def camera_topic(rig_name: str, channel: str = "camera_info_latched") -> str:
    """One camera channel's topic, under the rig's own naming."""
    return _CAM.format(rig_name, channel)


def rig_camera_name(spec_name: str) -> str | None:
    """The rig's name for a spec's camera, or None when it has no counterpart on the vehicle."""
    return RIG_CAMERA_NAMES.get(spec_name)


def _camera_rows() -> tuple[RigTopic, ...]:
    """The eighteen camera rows, which are three topics per camera and differ only by phase."""
    rows = []
    for name in _CAMERAS:
        rows.append(
            RigTopic(
                _CAM.format(name, "image_raw/ffmpeg"),
                20.0,
                DIRECT,
                needs="the H.264 encoder; FFMPEGPacket is already defined",
                phase=4,
            )
        )
        rows.append(
            RigTopic(
                _CAM.format(name, "meta"),
                20.0,
                DIRECT,
                needs="a .msg for the rig's own type",
                phase=5,
                definition=_WINGFIN,
            )
        )
        # Written, as of phase 1 - but only on a drive that mounted a rig, exactly like
        # `/tf_static`. `rig_coverage` reports "declared" and "on the wire" as two numbers for
        # that reason; 14 declared and 8 written is a correct pair and not a discrepancy.
        rows.append(RigTopic(_CAM.format(name, "camera_info_latched"), None, DIRECT))
    return tuple(rows)


#: All 55, in the order `docs/rosbag.md` argues them. **24 direct, 21 approximate, 10 not
#: producible**, and 55 - 10 = 45 is the target the phases are counted against.
RIG_TOPICS: tuple[RigTopic, ...] = _camera_rows() + (
    # --- the three we already write outright ---
    RigTopic("/tf", 86.6, DIRECT),
    RigTopic("/tf_static", None, DIRECT),
    RigTopic("/localization/odometry", 43.9, DIRECT),
    # --- the car's own state and controls: `drive.py:2478` holds all of it already ---
    RigTopic("/vehicle/state", 100.0, DIRECT, needs="a .msg", phase=5, definition=_WINGFIN),
    RigTopic(
        "/vehicle/actuators_output", 100.0, DIRECT, needs="a .msg", phase=5, definition=_WINGFIN
    ),
    RigTopic("/control/actuators", 100.0, DIRECT, needs="a .msg", phase=5, definition=_WINGFIN),
    # --- GNSS/INS: eleven channels, one true pose. Every one of them noiseless ---
    RigTopic("/sensing/gnss/pose", 50.0, APPROXIMATE),
    RigTopic("/sensing/gnss/imu/data", 50.0, APPROXIMATE),
    RigTopic("/sensing/gnss/imu/velocity", 200.0, APPROXIMATE),
    RigTopic("/sensing/gnss/imu/nav_sat_fix", 5.0, APPROXIMATE),
    RigTopic(
        "/sensing/gnss/ekf_nav", 50.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgEkfNav",
    ),
    RigTopic(
        "/sensing/gnss/ekf_quat", 50.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgEkfQuat",
    ),
    RigTopic(
        "/sensing/gnss/ekf_euler", 50.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgEkfEuler",
    ),
    # Not `/sensing/gnss/imu/data`, which we publish as `sensor_msgs/Imu`. Two topics one
    # character apart, one covered and one forgotten - which is exactly how it went missing.
    RigTopic(
        "/sensing/gnss/imu_data", 50.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgImuData",
    ),
    RigTopic(
        "/sensing/gnss/gps_pos", 5.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgGpsPos",
    ),
    RigTopic(
        "/sensing/gnss/gps_vel", 5.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgGpsVel",
    ),
    RigTopic(
        "/sensing/gnss/imu/pos_ecef", 50.0, APPROXIMATE, needs="one more standard conversion",
        phase=2, definition="geometry_msgs/PointStamped in most SBG drivers; unconfirmed",
    ),
    # --- the Livox IMU, and the cloud that was dropped ---
    RigTopic("/sensing/lidar/imu", 202.9, APPROXIMATE),
    RigTopic(
        "/sensing/lidar/points", 10.0, APPROXIMATE,
        needs="reading the point-cloud sensor in-process; PointCloud2 is core", phase=3,
    ),
    # --- the three that are trivial to emit, once their type is known ---
    RigTopic(
        "/sensing/gnss/utc_time", 1.0, APPROXIMATE, needs="a .msg", phase=2,
        definition="sbg_driver/SbgUtcTime",
    ),
    RigTopic(
        "/sensing/gnss/imu/utc_ref", 1.0, APPROXIMATE, needs="the rig's use of it", phase=2,
        definition="sensor_msgs/TimeReference, but the rig's exact use is unknown",
    ),
    RigTopic("/vehicle/engagement", 100.0, APPROXIMATE, needs="a .msg", phase=5,
             definition=_WINGFIN),
    # --- the five that only exist when a model is driving: `--agent-policy remote` ---
    RigTopic("/control/predicted_trajectory", 10.0, APPROXIMATE, needs="a .msg and a model",
             phase=5, definition=_WINGFIN),
    RigTopic("/control/lateral_plan", 10.0, APPROXIMATE, needs="a .msg and a model", phase=5,
             definition=_WINGFIN),
    RigTopic("/control/longitudinal_plan", 10.0, APPROXIMATE, needs="a .msg and a model",
             phase=5, definition=_WINGFIN),
    RigTopic("/perception/inference_control", 10.0, APPROXIMATE, needs="a .msg and a model",
             phase=5, definition=_WINGFIN),
    RigTopic("/perception/model_info", None, APPROXIMATE, needs="a .msg and a model", phase=5,
             definition=_WINGFIN),
    # --- the ten that stay absent on purpose. 45 of 45 is not parity, and must not claim to be
    RigTopic("/vehicle/can_rx", 100.0, IMPOSSIBLE,
             needs="no CAN bus; synthesised DBC frames would be fabrication"),
    RigTopic("/vehicle/can_tx", None, IMPOSSIBLE,
             needs="no CAN bus, and empty in the rig's own bag anyway"),
    RigTopic("/sensing/cabin/image_raw/ffmpeg", 30.0, IMPOSSIBLE, needs="no cabin, no driver"),
    RigTopic("/sensing/cabin/camera_info_latched", None, IMPOSSIBLE,
             needs="no cabin, no driver"),
    RigTopic("/sensing/cabin/audio_stamped", 100.0, IMPOSSIBLE, needs="no audio"),
    RigTopic("/sensing/cabin/audio_info", 0.2, IMPOSSIBLE, needs="no audio"),
    RigTopic("/sensing/gnss/imu/temp", 50.0, IMPOSSIBLE,
             needs="a physical sensor temperature; nothing in a simulator produces it"),
    RigTopic("/sensing/gnss/status", 1.0, IMPOSSIBLE,
             needs="the receiver's own health; nothing in a simulator produces it"),
    RigTopic("/diagnostics", 5.7, IMPOSSIBLE,
             needs="would be the simulator's logs, not the vehicle's"),
    RigTopic("/rosout", 4.0, IMPOSSIBLE, needs="would be the simulator's logs, not the vehicle's"),
)

#: Topics of ours that the rig's bag does not have, and **which must never be counted against
#: the 45**. The rig recorded no ground truth - nothing in its 55 is a labelled object, and
#: `/perception/inference_control` is the model's own configuration rather than an answer. These
#: four are the entire point of building a bag out of a simulator, and a coverage report that
#: took credit for them would be flattering itself.
SIMULATOR_EXTRAS: tuple[str, ...] = (
    "/clock",
    "/perception/objects",
    "/perception/traffic_lights",
    "/planning/route",
)

#: Topics in `bag_audit.html` this module cannot write for want of a `.msg`, and what each needs.
#: **Derived from `RIG_TOPICS`** rather than kept by hand - see the note above that block for the
#: two defects that cost.
#:
#: Guessing at a definition is worse than omitting the topic: a subscriber that deserialises
#: `wingfin_msgs/VehicleState` would fail on a `geometry_msgs/TwistStamped` wearing its topic
#: name. Nothing here needs the rig running or the wingfin source package, though - rosbag2
#: writes each type's full `.msg` text into the bag itself, so one `.mcap` and
#: `uv run python tools/ros_defs.py <bag>` recovers every one of them verbatim.
MISSING_DEFINITIONS: dict[str, str] = {
    row.topic: row.definition for row in RIG_TOPICS if row.definition
}

# --- topic table -------------------------------------------------------------------------
#
# `stride_of` decides which frames a topic is written on. Everything here is `state`, meaning
# every simulated step; the camera topics `ros_encode.py` adds are `image`, meaning the decision
# rate, because `frame_gate.py:202` re-uses the last drawn frame on a held step and writing it
# again under a new stamp would tell a model the world had frozen.
CLOCK = "/clock"
TF = "/tf"
TF_STATIC = "/tf_static"
ODOMETRY = "/localization/odometry"
OBJECTS = "/perception/objects"
TRAFFIC_LIGHTS = "/perception/traffic_lights"
ROUTE = "/planning/route"
GNSS_FIX = "/sensing/gnss/imu/nav_sat_fix"
GNSS_POSE = "/sensing/gnss/pose"
GNSS_IMU = "/sensing/gnss/imu/data"
GNSS_VELOCITY = "/sensing/gnss/imu/velocity"
LIDAR_IMU = "/sensing/lidar/imu"

#: The six `camera_info_latched` topics, in the rig's own order. Declared always and written only
#: on a `--camera-rig` drive, because a camera that is not mounted has no intrinsics to state.
CAMERA_INFO_TOPICS: tuple[str, ...] = tuple(camera_topic(name) for name in _CAMERAS)

#: `topic -> (message type, rate family)`. The family is what `ros_bag.py` groups by and what a
#: bag's own metadata reports, so a reader can tell a 10 Hz channel that was written at 10 Hz
#: from one that merely happened to have ten messages a second.
TOPICS: dict[str, tuple[str, str]] = {
    CLOCK: ("rosgraph_msgs/msg/Clock", "state"),
    TF: ("tf2_msgs/msg/TFMessage", "state"),
    TF_STATIC: ("tf2_msgs/msg/TFMessage", "latched"),
    ODOMETRY: ("nav_msgs/msg/Odometry", "state"),
    OBJECTS: ("vision_msgs/msg/Detection3DArray", "state"),
    TRAFFIC_LIGHTS: ("wingfin_msgs/msg/TrafficLightArray", "state"),
    ROUTE: ("nav_msgs/msg/Path", "latched"),
    GNSS_FIX: ("sensor_msgs/msg/NavSatFix", "state"),
    GNSS_POSE: ("geometry_msgs/msg/PoseStamped", "state"),
    GNSS_IMU: ("sensor_msgs/msg/Imu", "state"),
    GNSS_VELOCITY: ("geometry_msgs/msg/TwistStamped", "state"),
    LIDAR_IMU: ("sensor_msgs/msg/Imu", "state"),
    # One per camera, latched: intrinsics do not change during an episode. `sensor_msgs/CameraInfo`
    # is core, so unlike every other topic still absent these needed no definition - only a rig.
    **{topic: ("sensor_msgs/msg/CameraInfo", "latched") for topic in CAMERA_INFO_TOPICS},
}

#: `topic -> RigTopic`, for the two callers that need to look one up by name.
RIG_BY_TOPIC: dict[str, RigTopic] = {row.topic: row for row in RIG_TOPICS}

#: What each stage-11 phase lands, keyed by the `phase` on a row. Titles match the plan's own
#: ladder so that "phase 2 is done" means the same thing in both places.
PHASE_TITLES: dict[int, str] = {
    1: "camera_info_latched x6, /tf_static exercised",
    2: "the SBG GNSS family",
    3: "/sensing/lidar/points",
    4: "image_raw/ffmpeg - the encoder",
    5: "the fifteen rig-typed topics",
}


def rig_coverage(written: set[str] | None = None) -> dict:
    """How much of the rig's bag we produce, counted off `RIG_TOPICS` rather than maintained.

    `written` is the set of topics actually found in a bag. Without one this reports what the
    code *declares*; with one it reports what reached the wire, and the two differ by design -
    `/tf_static` is declared and written only when a camera rig supplied mounts
    (`ros_bag.py:251`), so "8 declared, 7 on the wire" is a correct pair of numbers and not a
    discrepancy.

    **`SIMULATOR_EXTRAS` are counted separately and never against the 45.** They are ground
    truth the rig's own bag does not contain, so crediting them would be marking our own paper.

    The `absent` breakdown is keyed by the phase that lands each topic, because that is the
    acceptance criterion for everything after this one: a phase is done when this count moves by
    the number that phase claimed.
    """
    producible = [row for row in RIG_TOPICS if row.producible]
    declared = [row for row in producible if row.topic in TOPICS]
    if written is None:
        produced = list(declared)
    else:
        produced = [row for row in declared if row.topic in written]
    absent: dict[int, list[RigTopic]] = {}
    for row in producible:
        if row.phase is not None:
            absent.setdefault(row.phase, []).append(row)
    extras = [topic for topic in SIMULATOR_EXTRAS if topic in TOPICS]
    return {
        "producible": len(producible),
        "declared": declared,
        "produced": produced,
        "absent": dict(sorted(absent.items())),
        "impossible": [row for row in RIG_TOPICS if not row.producible],
        "extras": extras if written is None else [t for t in extras if t in written],
        "definitions_missing": len(MISSING_DEFINITIONS),
        "verdicts": {
            verdict: sum(1 for row in RIG_TOPICS if row.verdict == verdict)
            for verdict in (DIRECT, APPROXIMATE, IMPOSSIBLE)
        },
    }


#: REP-105: `map` is the world, `base_link` is the car. Cameras hang off `base_link` in
#: `tf_static`; `ros_encode.py` names them.
MAP_FRAME = "map"
BASE_FRAME = "base_link"

#: **"Unknown" is all zeros. -1 means something else entirely, and the two are not swappable.**
#: MetaDrive has no notion of a sensor's own uncertainty and inventing one would be inventing a
#: fact, so every quantity here that we *do* publish carries a zero covariance - the ROS
#: convention for "measured, uncertainty not modelled". `sensor_msgs/NavSatFix` has said so since
#: this file was written (`position_covariance_type: 0`, zeros); odometry and the detections said
#: -1, which does not mean the same thing.
#:
#: -1 in element 0 is defined only by `sensor_msgs/Imu`, and it means *this publisher does not
#: produce this quantity at all*. Putting it on a pose we produce exactly says "ignore my pose",
#: and a -1 on the diagonal is not positive-semidefinite: rviz2's Odometry display reports
#: `Negative eigenvalue found for position` once a frame and draws no ellipse. That warning is
#: how this was found - no numeric check in `ros_probe.py` reads a covariance, and none should,
#: because the fault is in what the number *claims* rather than in what it is.
UNKNOWN_COVARIANCE = [0.0] * 36
UNKNOWN_COVARIANCE_3X3 = [0.0] * 9

#: The one quantity we genuinely do not produce. See `imu_message`.
ABSENT_QUANTITY_3X3 = [-1.0] + [0.0] * 8


@dataclass(frozen=True)
class Ego:
    """The car this frame, in MetaDrive's own world frame - which is already ENU."""

    x: float
    y: float
    z: float
    heading: float
    """Radians CCW from +x. `+` is a LEFT turn, matching REP-103 yaw and `signed_turn_angle`."""
    velocity_east: float
    velocity_north: float
    speed: float
    yaw_rate: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0


@dataclass(frozen=True)
class Box:
    """One object actually present in the scene this frame.

    Built from what the engine holds, never from the dataset's track list. A walker whose tape
    has ended is gone from the render as well as from the world, so a box carried on from the
    tracks would be a label over empty road - which teaches a detector to hallucinate people,
    and is worse than no label at all.
    """

    name: str
    kind: str
    x: float
    y: float
    z: float
    heading: float
    length: float
    width: float
    height: float


@dataclass(frozen=True)
class Light:
    """A traffic light and the colour it is showing, as `light_manager` reports it.

    `status` is MetaDrive's own `MetaDriveType.LIGHT_*` string, passed through unaltered rather
    than mapped onto some enum of ours: the dataset, the signal plan and the bag then all say
    the same word, and nobody has to hold a translation table to check one against another.
    """

    name: str
    status: str
    x: float
    y: float
    z: float = 0.0
    lane: str = ""


@dataclass(frozen=True)
class CameraSpec:
    """One mounted camera, as much of it as `sensor_msgs/CameraInfo` describes.

    `name` is the rig's - `front_left` - and decides the topic. `frame_id` is the spec's own
    camera name, and is what `/tf_static` calls the same camera, so the two are joined by the
    transform rather than by matching strings. **They are deliberately allowed to differ**: on
    `rigs/cams.txt` the labels and the geometry disagree (see `RIG_CAMERA_NAMES`), and a reader
    that wants to know where `rear_left` actually points follows `frame_id` into the transform
    instead of trusting a name.
    """

    name: str
    frame_id: str
    width: int
    height: int
    fov_deg: float
    """Horizontal, matching `camera_rig.Camera.fov` and panda3d's one-argument `setFov`."""


@dataclass(frozen=True)
class Projection:
    """What turns metres into latitude and longitude, with the origin shift already applied.

    `origin_lat` / `origin_lon` come from `metadata.coordinate_system_wkt` via
    `geodesy.projection_origin`; `offset_x` / `offset_y` are
    `metadata.old_origin_in_current_coordinate`. Carrying the shift here rather than at the call
    site is deliberate - it is 93.8 m on junction-1, and every reading that forgets it is wrong
    by that much while looking perfectly reasonable.
    """

    origin_lat: float
    origin_lon: float
    offset_x: float
    offset_y: float


@dataclass(frozen=True)
class Frame:
    """Everything one `env.step` produced, before any of it becomes a message."""

    index: int
    sim_time_s: float
    ego: Ego
    boxes: tuple[Box, ...] = ()
    lights: tuple[Light, ...] = ()
    route: tuple[tuple[float, float], ...] = ()
    projection: Projection | None = None
    extra: dict = field(default_factory=dict)


def stamp(seconds: float) -> dict:
    """Simulator seconds as a `builtin_interfaces/Time`.

    Rounded rather than truncated, and taken **once per frame** by `messages()`, so that every
    topic of one frame carries a bit-identical stamp. MetaDrive's bridge instead stamps each
    stream with the wall clock when it arrives at the far end of a socket, which puts the camera
    and the boxes of one instant tens of milliseconds apart - half a metre at 50 km/h, baked
    into the labels.
    """
    nanos = round(seconds * 1e9)
    return {"sec": int(nanos // 1_000_000_000), "nanosec": int(nanos % 1_000_000_000)}


def header(seconds: float, frame_id: str) -> dict:
    return {"stamp": stamp(seconds), "frame_id": frame_id}


def quaternion(yaw: float, pitch: float = 0.0, roll: float = 0.0) -> dict:
    """Roll-pitch-yaw to a quaternion, ROS order (x, y, z, w), yaw CCW-positive."""
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    return {
        "x": sr * cp * cy - cr * sp * sy,
        "y": cr * sp * cy + sr * cp * sy,
        "z": cr * cp * sy - sr * sp * cy,
        "w": cr * cp * cy + sr * sp * sy,
    }


def _point(x: float, y: float, z: float = 0.0) -> dict:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _pose(x: float, y: float, z: float, yaw: float) -> dict:
    return {"position": _point(x, y, z), "orientation": quaternion(yaw)}


# --- the messages themselves ---------------------------------------------------------------


def _transform(seconds: float, parent: str, child: str, x, y, z, yaw) -> dict:
    return {
        "header": header(seconds, parent),
        "child_frame_id": child,
        "transform": {
            "translation": _point(x, y, z),
            "rotation": quaternion(yaw),
        },
    }


def clock_message(frame: Frame) -> dict:
    return {"clock": stamp(frame.sim_time_s)}


def tf_message(frame: Frame) -> dict:
    """`map` -> `base_link`, the only dynamic transform a simulated car needs.

    Straight through with no rotation, because MetaDrive's world is already ENU. If that ever
    stops being true the failure is silent, which is why `ros_probe.py` drives a known turn and
    checks the sign rather than re-reading this line.
    """
    ego = frame.ego
    return {
        "transforms": [
            _transform(frame.sim_time_s, MAP_FRAME, BASE_FRAME, ego.x, ego.y, ego.z, ego.heading)
        ]
    }


def tf_static_message(seconds: float, mounts: dict[str, tuple[float, float, float, float]]) -> dict:
    """`base_link` -> each camera, written once.

    `mounts` is `{frame_id: (x, y, z, yaw)}` in the car's own frame. Note this is where
    `camera_rig.py`'s CARLA-shaped spec has to be converted rather than copied: its `x` is
    forward and `+yaw` is right, MetaDrive's is not. `ros_encode.py` does the conversion in one
    place and this function takes the result.
    """
    return {
        "transforms": [
            _transform(seconds, BASE_FRAME, name, x, y, z, yaw)
            for name, (x, y, z, yaw) in sorted(mounts.items())
        ]
    }


def focal_length_px(width: int, fov_deg: float) -> float:
    """The pinhole focal length, in pixels, of a camera `width` wide with a horizontal `fov_deg`.

    `camera_rig.mount` sets the lens with panda3d's one-argument `Lens.setFov`, which takes the
    **horizontal** angle and lets the vertical follow the aspect ratio - so this is the whole
    intrinsic model, and `fy == fx` because the pixels are square. There is no distortion to
    describe: MetaDrive's `RGBCamera` is rectilinear, which is why `rigs/av3.txt` records in its
    own header that the four fisheye corners are rendered unwarped at a fallback FOV.
    """
    return (width / 2.0) / math.tan(math.radians(fov_deg) / 2.0)


def camera_info_message(seconds: float, camera: CameraSpec) -> dict:
    """One camera's intrinsics, latched.

    **`d` is empty, and that is a statement, not an omission.** `plumb_bob` with five zeros would
    say "measured a distortion and it came out zero", which no calibration ever does; an empty `d`
    against an empty `distortion_model` is ROS's way of saying the publisher does not model one.
    A rectilinear render genuinely has none, and a consumer that undistorts against five fabricated
    zeros gets the same picture back - but one that reads the model as evidence the rig was
    calibrated has been told something untrue, which is the same rule `UNKNOWN_COVARIANCE` follows.

    `p` is `k` with a zero translation column: there is no stereo baseline, because there is no
    stereo pair. `r` is the identity for the same reason - nothing here is rectified against
    anything else.
    """
    focal = focal_length_px(camera.width, camera.fov_deg)
    centre_x, centre_y = camera.width / 2.0, camera.height / 2.0
    return {
        "header": header(seconds, camera.frame_id),
        "height": int(camera.height),
        "width": int(camera.width),
        "distortion_model": "",
        "d": [],
        "k": [focal, 0.0, centre_x, 0.0, focal, centre_y, 0.0, 0.0, 1.0],
        "r": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "p": [focal, 0.0, centre_x, 0.0, 0.0, focal, centre_y, 0.0, 0.0, 0.0, 1.0, 0.0],
        "binning_x": 0,
        "binning_y": 0,
        "roi": {
            "x_offset": 0,
            "y_offset": 0,
            "height": 0,
            "width": 0,
            "do_rectify": False,
        },
    }


#: How far off straight ahead or straight behind a mount has to point before it counts as a side
#: camera. `rigs/cams.txt` and `rigs/av3.txt` put their side cameras between 53.7 and 125 degrees,
#: so nothing real is near this line and it exists only to keep a nominally-forward camera with a
#: fraction of a degree of yaw from being called "left".
SIDE_DEGREES = 10.0


def aim_side(yaw_rad: float) -> str:
    """Which side a mount actually points: `left`, `right`, or `middle` for fore-and-aft."""
    turn = ((math.degrees(yaw_rad) + 180.0) % 360.0) - 180.0
    if abs(turn) < SIDE_DEGREES or abs(abs(turn) - 180.0) < SIDE_DEGREES:
        return "middle"
    return "left" if turn > 0 else "right"


def named_side(rig_name: str) -> str:
    """Which side a rig camera's own name claims: `front_left` -> `left`."""
    return rig_name.rsplit("_", 1)[-1]


def camera_side_disagreements(mounts: dict[str, tuple[str, float]]) -> list[tuple[str, str, str]]:
    """Every camera whose rig topic and whose mount disagree about which way it faces.

    `mounts` is `{rig name: (frame_id, yaw in radians)}`. Returns `(rig name, claimed, aimed)`.

    **This is reported and never corrected, in either direction.** Renaming the topic to match the
    geometry would contradict the spec's own labels; rotating the mount to match the topic would
    put a camera somewhere the drive never rendered from. `rigs/cams.txt` yields exactly two rows
    here - its back pair - because that file reads `+yaw` as right at the front and as left at the
    back and cannot be made self-consistent by choosing a convention. `rigs/av3.txt` yields none.
    """
    out = []
    for name, (_frame_id, yaw) in sorted(mounts.items()):
        claimed, aimed = named_side(name), aim_side(yaw)
        if claimed != aimed:
            out.append((name, claimed, aimed))
    return out


def odometry_message(frame: Frame) -> dict:
    """Where the car is and how fast, in `map`.

    `twist` is in `base_link` per REP-105 - so the world-frame `(east, north)` velocity is
    rotated into the car's frame here. Publishing a world-frame velocity in a child-frame field
    is a mistake that looks right whenever the car happens to be driving east.
    """
    ego = frame.ego
    cos_h, sin_h = math.cos(-ego.heading), math.sin(-ego.heading)
    forward = ego.velocity_east * cos_h - ego.velocity_north * sin_h
    left = ego.velocity_east * sin_h + ego.velocity_north * cos_h
    return {
        "header": header(frame.sim_time_s, MAP_FRAME),
        "child_frame_id": BASE_FRAME,
        "pose": {
            "pose": _pose(ego.x, ego.y, ego.z, ego.heading),
            "covariance": list(UNKNOWN_COVARIANCE),
        },
        "twist": {
            "twist": {
                "linear": _point(forward, left, 0.0),
                "angular": _point(0.0, 0.0, ego.yaw_rate),
            },
            "covariance": list(UNKNOWN_COVARIANCE),
        },
    }


def objects_message(frame: Frame) -> dict:
    """Every object in the scene, as 3D boxes in `map`.

    Positions are world-frame rather than ego-relative, unlike MetaDrive's example bridge, which
    subtracts the ego and rotates. World-frame plus `/tf` is strictly more information - a
    consumer that wants ego-relative gets it from the transform, and one that wants world
    coordinates cannot recover them from an ego-relative box without a pose the bridge never
    published.
    """
    return {
        "header": header(frame.sim_time_s, MAP_FRAME),
        "detections": [
            {
                "header": header(frame.sim_time_s, MAP_FRAME),
                "results": [
                    {
                        "hypothesis": {"class_id": box.kind, "score": 1.0},
                        "pose": {
                            "pose": _pose(box.x, box.y, box.z, box.heading),
                            "covariance": list(UNKNOWN_COVARIANCE),
                        },
                    }
                ],
                "bbox": {
                    "center": _pose(box.x, box.y, box.z, box.heading),
                    "size": _point(box.length, box.width, box.height),
                },
                "id": box.name,
            }
            for box in frame.boxes
        ],
    }


def traffic_lights_message(frame: Frame) -> dict:
    return {
        "header": header(frame.sim_time_s, MAP_FRAME),
        "lights": [
            {
                "id": light.name,
                "status": light.status,
                "lane": light.lane,
                "position": _point(light.x, light.y, light.z),
            }
            for light in frame.lights
        ],
    }


def route_message(frame: Frame) -> dict:
    """The planned route as a path in `map`. Latched: it does not change during an episode."""
    return {
        "header": header(frame.sim_time_s, MAP_FRAME),
        "poses": [
            {"header": header(frame.sim_time_s, MAP_FRAME), "pose": _pose(x, y, 0.0, 0.0)}
            for x, y in frame.route
        ],
    }


def _latlon(frame: Frame) -> tuple[float, float] | None:
    """Real WGS 84 latitude and longitude, or None when the dataset carried no projection.

    The import is local because `geodesy` is only needed on this path, and a bag of everything
    else is still worth writing on a dataset with no `coordinate_system_wkt`.
    """
    if frame.projection is None:
        return None
    from geodesy import aeqd_inverse

    projection = frame.projection
    return aeqd_inverse(
        projection.origin_lat,
        projection.origin_lon,
        frame.ego.x - projection.offset_x,
        frame.ego.y - projection.offset_y,
    )


def gnss_fix_message(frame: Frame) -> dict | None:
    """A GNSS fix, which is **truth rather than a measurement** - and says so.

    `status.service` is GPS and `status.status` is 0 (`STATUS_FIX`), because there is always a
    fix; there is no noise, no multipath, no dropout and no lag. `position_covariance_type` is 0
    (`COVARIANCE_TYPE_UNKNOWN`) rather than a fabricated variance: a simulator that reported
    2 cm of uncertainty would be inventing a number, and a model trained against it would learn
    to trust GNSS in a way the real receiver never earns. The bag's own metadata records
    `noise_model: none` for the same reason.

    Altitude is the car's height above MetaDrive's near-flat world, not elevation above the
    ellipsoid, and is not usable as either.
    """
    found = _latlon(frame)
    if found is None:
        return None
    latitude, longitude = found
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "status": {"status": 0, "service": 1},
        "latitude": latitude,
        "longitude": longitude,
        "altitude": frame.ego.z,
        "position_covariance": [0.0] * 9,
        "position_covariance_type": 0,
    }


def gnss_pose_message(frame: Frame) -> dict:
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, MAP_FRAME),
        "pose": _pose(ego.x, ego.y, ego.z, ego.heading),
    }


def imu_message(frame: Frame) -> dict:
    """Orientation and rates in `base_link`.

    Linear acceleration is **not** synthesised: MetaDrive exposes velocity, and differencing it
    across a frame to fake an accelerometer would put simulation noise into a field a real IMU
    measures directly. It is the one field here carrying `ABSENT_QUANTITY_3X3`, the -1 that
    `sensor_msgs/Imu` defines as "this publisher does not produce this quantity".

    Orientation and angular velocity are produced, and exactly, so they carry zeros - "measured,
    uncertainty not modelled" - and not the -1 they used to. A -1 there told every consumer to
    discard a heading that is ground truth.
    """
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "orientation": quaternion(ego.heading, ego.pitch, ego.roll),
        "orientation_covariance": list(UNKNOWN_COVARIANCE_3X3),
        "angular_velocity": _point(0.0, 0.0, ego.yaw_rate),
        "angular_velocity_covariance": list(UNKNOWN_COVARIANCE_3X3),
        "linear_acceleration": _point(0.0, 0.0, 0.0),
        "linear_acceleration_covariance": list(ABSENT_QUANTITY_3X3),
    }


def velocity_message(frame: Frame) -> dict:
    ego = frame.ego
    cos_h, sin_h = math.cos(-ego.heading), math.sin(-ego.heading)
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "twist": {
            "linear": _point(
                ego.velocity_east * cos_h - ego.velocity_north * sin_h,
                ego.velocity_east * sin_h + ego.velocity_north * cos_h,
                0.0,
            ),
            "angular": _point(0.0, 0.0, ego.yaw_rate),
        },
    }


#: Topic -> builder, for everything written every frame. `ROUTE`, `TF_STATIC` and the six
#: `CAMERA_INFO_TOPICS` are absent deliberately: they are latched, written once per episode by
#: `ros_bag.py`.
BUILDERS = {
    CLOCK: clock_message,
    TF: tf_message,
    ODOMETRY: odometry_message,
    OBJECTS: objects_message,
    TRAFFIC_LIGHTS: traffic_lights_message,
    GNSS_FIX: gnss_fix_message,
    GNSS_POSE: gnss_pose_message,
    GNSS_IMU: imu_message,
    GNSS_VELOCITY: velocity_message,
    LIDAR_IMU: imu_message,
}


def messages(frame: Frame, topics: set[str] | None = None) -> list[tuple[str, str, dict]]:
    """Every per-frame message for one frame, as `(topic, message type, content)`.

    One pass, one stamp. A builder returning None - only `gnss_fix_message`, on a dataset with
    no projection - drops that topic for the whole drive rather than leaving a gap in it, which
    is why `ros_bag.py` counts what it wrote per topic instead of assuming the frame count.
    """
    wanted = TOPICS.keys() if topics is None else topics
    out = []
    for topic in wanted:
        builder = BUILDERS.get(topic)
        if builder is None:
            continue
        content = builder(frame)
        if content is None:
            continue
        out.append((topic, TOPICS[topic][0], content))
    return out
