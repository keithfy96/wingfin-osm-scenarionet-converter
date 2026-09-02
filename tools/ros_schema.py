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
import time
from dataclasses import dataclass, field
from pathlib import Path

# --- the message definitions the humble typestore does not carry ------------------------
#
# Neither package is part of the ROS core, so `Stores.ROS2_HUMBLE` has never heard of them.
# `rosbags` parses `.msg` text at runtime and registers the result - no package, no colcon, no
# build step - and both were verified round-tripping through CDR before being written down here.
# Copied from the upstream definitions rather than invented; a field out of order would
# serialise silently and deserialise into nonsense.
#: The `ffmpeg_image_transport_msgs` release the camera packets are serialised against, and the
#: commit it was read from. Recorded for the same reason `SBG_DRIVER_VERSION` is: **CDR carries
#: no field names**, so a consumer decoding `image_raw/ffmpeg` with its own installed package
#: rather than with the definition in the bag has to be told which one this is.
#:
#: **The definition that stood here from stage 10 until phase 4 was wrong in four ways**, and
#: none of them could raise while no camera topic was written. It said
#: `header, encoding, uint32 width, uint32 height, uint32 pts, uint8 flags, uint64 frame_id,
#: uint8[] data`. Upstream says `header, int32 width, int32 height, string encoding, uint64 pts,
#: uint8 flags, bool is_bigendian, uint8[] data`: the fields are in a **different order**, the
#: sizes differ, there is no `frame_id`, and `is_bigendian` was missing entirely. A bag written
#: against the old text would still open - rosbag2 stores the definition beside the data, so our
#: own reader would agree with itself - and any consumer with the real package installed would
#: read the encoding string out of the width field. That is precisely the failure this file's
#: own comment warns about for `vision_msgs`, sitting in the file unexercised, and it is why a
#: unit test now pins this text character for character.
#:
#: Identical on the `humble`, `rolling` and `master` branches at the time of reading; master
#: adds explanatory comments and no fields.
FFMPEG_MSGS_VERSION = "1.1.2"
FFMPEG_MSGS_COMMIT = "5395eac7dd830245c29d13c4db9fac1574137014"

#: That definition, exactly as upstream writes it - tab, comments, column alignment and all.
#: `get_types_from_msg` ignores every part of that, and it is kept so "verbatim" is a claim a
#: reader can check by eye against the file at the commit above rather than take on trust.
FFMPEG_PACKET_MSG = (
    "std_msgs/Header header\n"
    "int32 width       # original image width\n"
    "int32 height      # original image height\n"
    "string encoding\t  # encoding used\n"
    "uint64 pts        # packet pts\n"
    "uint8  flags      # packet flags\n"
    "bool is_bigendian # true if machine stores in big endian format\n"
    "uint8[] data      # ffmpeg compressed payload\n"
)

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
    # **Verbatim from `ffmpeg_image_transport_msgs` 1.1.2, the humble branch** - the same
    # distro `Stores.ROS2_HUMBLE` pins everything else in this file to. See
    # `FFMPEG_MSGS_VERSION` below for why the provenance is recorded rather than assumed, and
    # what the definition that stood here before phase 4 got wrong.
    "ffmpeg_image_transport_msgs/msg/FFMPEGPacket": FFMPEG_PACKET_MSG,
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

#: The `sbg_driver` release `tools/sbg_msgs/` was copied from, written into every bag we produce.
#: **The field lists change between releases and CDR carries no field names**, so a consumer
#: using its own installed `sbg_driver` rather than the definitions in the bag needs to know
#: which one this is. `tools/sbg_msgs/README.md` has the measured 3.1.0-to-3.4.0 differences and
#: what to do the day a bag off the rig says it recorded a different one.
SBG_DRIVER_VERSION = "3.4.0"
SBG_DRIVER_COMMIT = "3efaf2982a3eacbbdcf6ff7ef40116a36fb3b2cc"

#: Where those twelve `.msg` files live, beside this module.
SBG_MSG_DIR = Path(__file__).resolve().parent / "sbg_msgs"


def _sbg_definitions() -> dict[str, str]:
    """The `sbg_driver` types, read off disk rather than retyped into a string literal here.

    Nine of the rig's topics are SBG types and `Stores.ROS2_HUMBLE` has never heard of the
    package, so they need the same treatment `vision_msgs` got - except that these are long, and
    "copied verbatim" is a claim somebody has to be able to check. A file that is byte-identical
    to `msg/SbgEkfNav.msg` at a named commit can be diffed against upstream in one command; the
    same text rewrapped into a Python string cannot, and rewrapping is exactly where a field
    changes order. So they stay files, comments and all - the comments being the only record of
    what each field means, which this module has to decide for every one of them. They do not
    reach the bag: `rosbags` regenerates the definition from its parsed typestore, so a written
    bag carries the field list alone. That is what a decoder needs, and it round-trips exactly.

    Seven message types plus the five nested status submessages they name. The nested names are
    unqualified upstream (`SbgEkfStatus status`, not `sbg_driver/SbgEkfStatus`); `rosbags`
    resolves those against the message's own package, which is why the loop can pass each file
    on its own without concatenating dependencies.
    """
    return {
        f"sbg_driver/msg/{path.stem}": path.read_text(encoding="utf-8")
        for path in sorted(SBG_MSG_DIR.glob("*.msg"))
    }


EXTRA_DEFINITIONS.update(_sbg_definitions())

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
        # Written, as of phase 4, on a drive that mounted a rig **and** asked for
        # `--ros-camera`: an H.264 encode is the one thing in this bag that costs real time per
        # frame, so unlike the intrinsics beside it, it is opt-in rather than automatic.
        rows.append(RigTopic(_CAM.format(name, "image_raw/ffmpeg"), 20.0, DIRECT))
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
    RigTopic("/sensing/gnss/ekf_nav", 50.0, APPROXIMATE),
    RigTopic("/sensing/gnss/ekf_quat", 50.0, APPROXIMATE),
    RigTopic("/sensing/gnss/ekf_euler", 50.0, APPROXIMATE),
    # Not `/sensing/gnss/imu/data`, which we publish as `sensor_msgs/Imu`. Two topics one
    # character apart, one covered and one forgotten - which is exactly how it went missing.
    RigTopic("/sensing/gnss/imu_data", 50.0, APPROXIMATE),
    RigTopic("/sensing/gnss/gps_pos", 5.0, APPROXIMATE),
    RigTopic("/sensing/gnss/gps_vel", 5.0, APPROXIMATE),
    RigTopic("/sensing/gnss/imu/pos_ecef", 50.0, APPROXIMATE),
    # --- the Livox IMU, and the cloud that was dropped ---
    RigTopic("/sensing/lidar/imu", 202.9, APPROXIMATE),
    RigTopic("/sensing/lidar/points", 10.0, APPROXIMATE),
    # --- the three that are trivial to emit, once their type is known ---
    RigTopic("/sensing/gnss/utc_time", 1.0, APPROXIMATE),
    RigTopic("/sensing/gnss/imu/utc_ref", 1.0, APPROXIMATE),
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
# The rate family beside each type decides which frames a topic is written on. Most are
# `state`, meaning every simulated step; the point cloud and the six camera packets are
# `sensor`, meaning the decision rate, because `frame_gate.py` re-uses the last drawn frame on
# a held step and writing it again under a new stamp would tell a model the world had frozen.
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

#: The nine of phase 2. Seven `sbg_driver` types and two that were never definition-blocked at
#: all - `pos_ecef` is a `geometry_msgs/PointStamped` and `utc_ref` a `sensor_msgs/TimeReference`,
#: both core, and both absent only because nobody had decided what the rig means by them.
#:
#: **`SBG_IMU_DATA` is not `GNSS_IMU`.** One character apart, two different types on two
#: different topics, and confusing them is how `/sensing/gnss/imu_data` fell out of the ledger
#: altogether. `GNSS_IMU` is the driver's `sensor_msgs/Imu` republication; this is the raw one.
SBG_EKF_NAV = "/sensing/gnss/ekf_nav"
SBG_EKF_QUAT = "/sensing/gnss/ekf_quat"
SBG_EKF_EULER = "/sensing/gnss/ekf_euler"
SBG_IMU_DATA = "/sensing/gnss/imu_data"
SBG_GPS_POS = "/sensing/gnss/gps_pos"
SBG_GPS_VEL = "/sensing/gnss/gps_vel"
SBG_UTC_TIME = "/sensing/gnss/utc_time"
GNSS_POS_ECEF = "/sensing/gnss/imu/pos_ecef"
GNSS_UTC_REF = "/sensing/gnss/imu/utc_ref"

#: Phase 3, and the only sensor in this bag that returns a shape rather than a number. The rig
#: carries a Livox; this is a rendered depth buffer turned into metres, which is the same kind
#: of thing seen through a much narrower window - see `LidarCloud` for what the difference is.
LIDAR_POINTS = "/sensing/lidar/points"

#: The sensor's own frame, and the first in this bag that is neither the world nor the car.
#: The points are published in it rather than in `map` because that is what a lidar topic
#: means to whatever reads it - and because a cloud written in `map` is *trivially* right: it
#: comes out of MetaDrive in world coordinates, so publishing it there would carry no claim
#: about which way the sensor is pointing and nothing could ever check one. In `lidar` the
#: claim is explicit, and `ros_probe` checks it by the only fact that can settle it - every
#: point a forward-facing sensor returns has to lie inside its own field of view.
LIDAR_FRAME = "lidar"

#: The four that need a real position, and so are dropped whole on a dataset with no projection
#: - exactly as `GNSS_FIX` already was. A bag holding `ekf_nav` on a dataset that cannot say
#: where it is would be holding a latitude of zero, off the coast of Ghana.
GEODETIC_TOPICS: tuple[str, ...] = (GNSS_FIX, SBG_EKF_NAV, SBG_GPS_POS, GNSS_POS_ECEF)

#: The six `camera_info_latched` topics, in the rig's own order. Declared always and written only
#: on a `--camera-rig` drive, because a camera that is not mounted has no intrinsics to state.
CAMERA_INFO_TOPICS: tuple[str, ...] = tuple(camera_topic(name) for name in _CAMERAS)

#: The six `image_raw/ffmpeg` topics, in the same order. Phase 4. These are the only topics in
#: the bag whose payload is not a reading but a *compressed* one, and the only ones a reader has
#: to run a decoder over before it can check anything about them - which is why `ros_probe` does
#: exactly that rather than checking the headers and calling it verified.
CAMERA_PACKET_TOPICS: tuple[str, ...] = tuple(
    camera_topic(name, "image_raw/ffmpeg") for name in _CAMERAS
)

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
    # Phase 2. Nine channels, one true pose - see `_sbg_definitions` for where the types come
    # from and `ABSENT_SCALAR` for how the fields a simulator cannot fill are written.
    SBG_EKF_NAV: ("sbg_driver/msg/SbgEkfNav", "state"),
    SBG_EKF_QUAT: ("sbg_driver/msg/SbgEkfQuat", "state"),
    SBG_EKF_EULER: ("sbg_driver/msg/SbgEkfEuler", "state"),
    SBG_IMU_DATA: ("sbg_driver/msg/SbgImuData", "state"),
    SBG_GPS_POS: ("sbg_driver/msg/SbgGpsPos", "state"),
    SBG_GPS_VEL: ("sbg_driver/msg/SbgGpsVel", "state"),
    SBG_UTC_TIME: ("sbg_driver/msg/SbgUtcTime", "state"),
    GNSS_POS_ECEF: ("geometry_msgs/msg/PointStamped", "state"),
    GNSS_UTC_REF: ("sensor_msgs/msg/TimeReference", "state"),
    # Phase 3, and the one topic here whose family is neither "state" nor "latched". A cloud is
    # written at the **decision** rate rather than every step, so on a `--step-hz 100
    # --decision-hz 20` drive it correctly holds a fifth of the messages every other channel
    # does - and "sensor" is what tells `ros_probe`'s per-frame count not to call that a hole.
    LIDAR_POINTS: ("sensor_msgs/msg/PointCloud2", "sensor"),
    # Phase 4, and `sensor` for the same reason the cloud is: a camera is read at the decision
    # rate, and `frame_gate` holds the *drawn* frame in between. Encoding the held buffer again
    # would put a second, byte-different H.264 packet on the wire for a picture that never
    # changed - not a duplicate a reader could spot, because inter-frame coding makes the second
    # one tiny and perfectly valid.
    **{topic: ("ffmpeg_image_transport_msgs/msg/FFMPEGPacket", "sensor")
       for topic in CAMERA_PACKET_TOPICS},
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

#: REP-105's global frame, and the only one in this bag that is not ours. `pos_ecef` is what
#: joins a drive on `junction-1` to anything else on the planet, so it must not hang off `map`.
EARTH_FRAME = "earth"

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

#: **"I do not produce this", for a bare float in a message with no way to say so.**
#:
#: `sensor_msgs/Imu` has a -1 for it and `sensor_msgs/NavSatFix` a covariance type, so the two
#: GNSS topics written before phase 2 could both state an absence in-band. The `sbg_driver`
#: types cannot: `SbgImuData.temp` is a float32 and nothing else, and every value a float32 can
#: hold is a temperature - including 0.0, which reads as a sensor sitting at freezing.
#:
#: NaN is the only encoding left that a consumer cannot quietly believe. It survives CDR intact
#: (verified round-tripping every field written with it), it propagates rather than averaging
#: away, and a plot of it is a gap rather than a line. The alternative is the one thing this
#: file refuses everywhere else - a plausible number nobody measured.
#:
#: **This is not the same as an accuracy of zero.** `position_accuracy` and the rest are 1-sigma
#: uncertainties, and for ground truth zero is *true*: the simulator's position is exact. Only a
#: quantity that does not exist at all gets a NaN. `/sensing/gnss/imu/temp` is excluded from the
#: 45 for precisely the reason `SbgImuData.temp` is NaN here, and the two must keep agreeing.
ABSENT_SCALAR = float("nan")

#: The drive's `t = 0`, declared rather than measured, and the GPS epoch on purpose.
#:
#: `SbgUtcTime` wants a calendar date and `SbgGpsPos` a GPS time of week; the simulator has
#: neither, only seconds since the drive began. Stamping the wall clock at conversion time would
#: make the bag claim the drive happened then - and make two runs of the same drive differ.
#: Stamping a recent-looking date would be worse, because it would be believed.
#:
#: So the drive's zero is declared to be the start of GPS week 0, 1980-01-06T00:00:00Z. Then
#: `gps_tow` is literally the elapsed time, the date fields advance correctly *within* the
#: drive, and the absolute date is a sentinel no reader mistakes for a recording session.
#: `SbgUtcTime.clock_status.clock_utc_status` carries the same statement in-band as 0, the
#: message's own "the UTC time is not known, we are just propagating internally".
GPS_EPOCH_UNIX_S = 315_964_800
SECONDS_PER_WEEK = 604_800


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
class CameraPacket:
    """One camera's compressed frame, already encoded - this file never touches pixels.

    `tools/ros_encode.py` produces these; everything here does is give one a header and a topic.
    The split is the same one `LidarCloud` follows and for the same reason: the part that can be
    silently wrong (the encode) is kept where it can be tested against a decoder, and the part
    that is only a shape is kept where it can be tested with no libraries at all.

    `name` is the rig's - `front_left` - and decides the topic, exactly as on `CameraSpec`.
    `frame_id` is the spec's own camera name and is what `/tf_static` calls the same camera, so
    a consumer joins the picture to the mount through the transform rather than by matching two
    strings that are allowed to differ. See `RIG_CAMERA_NAMES` for why they are allowed to.

    **`encoding` is the codec, not the pixel format.** `ffmpeg_image_transport`'s decoder resolves
    it with `find_id_for_encoder_or_encoding`, which accepts either an encoder name (`libx264`)
    or a codec name (`h264`); the humble-era decoder carried an explicit `libx264-> h264` map.
    `libx264` satisfies both. The newer 4-token form - `codec;av_pix_fmt;cv_bridge_fmt;ros_fmt` -
    is deliberately **not** written: it is a `master`-branch feature, and the humble decoder this
    repo pins everything else to would take the whole string as a codec name and find none.

    `pts` is a frame counter in the encoder's own time base, which is what libx264 wants and what
    the upstream publisher puts there. **The authoritative time is `header.stamp`**, the same
    stamp every other topic in the frame carries.
    """

    name: str
    frame_id: str
    width: int
    height: int
    encoding: str
    pts: int
    keyframe: bool
    """Whether a reader can start decoding here. Written into `flags` as `AV_PKT_FLAG_KEY`."""
    data: bytes


@dataclass(frozen=True)
class LidarCloud:
    """One sweep, exactly as MetaDrive hands it over - and it is not yet in any ROS frame.

    `points` is `(height, width, 3)` of **metres from the sensor on world axes**, which is a
    frame nothing in ROS has a name for. `PointCloudLidar(ego_centric=True)` zeroes the
    translation and leaves the rotation built from the camera's *world* hpr
    (`point_cloud_lidar.py:66-75`), so the origin is the sensor and the axes are the map's.
    `lidar_points_message` is what turns it into the sensor's own frame, and that rotation is
    here rather than in `ros_frame.py` for one reason: here it can be unit tested without a
    simulator, and a rotation sign is the thing in this phase most able to be wrong quietly.

    **`height` is the beam count and `width` the rays across each beam**, so the cloud stays
    organised - `height` rows of `width` points - exactly as `sensor_msgs/PointCloud2` means
    those two fields. A ray that hit nothing keeps its slot and is written NaN, which is what
    `is_dense: false` exists to say. Dropping the misses instead would compact the cloud and
    destroy the one piece of structure a lidar has.

    `fov_deg` is horizontal; the vertical follows from the aspect ratio, so a 200x64 cloud at
    65 deg spans 23.0 deg vertically. **This is a forward cone, not a sweep.** The rig's Livox
    sees far more of the world than any single rendered buffer can, and no amount of resolution
    here changes that - it is the honest limit of the approximation, not a setting.
    """

    points: object
    """`(height, width, 3)` float, metres from the sensor, on world axes."""
    fov_deg: float
    max_range_m: float
    """Beyond this a return is called a miss. **Not a MetaDrive limit** - the depth buffer's far
    plane is 100 km, so an unhit ray comes back as a point up to 18 km out rather than as
    nothing at all, and without a declared range the cloud describes the sky."""


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


# --- the SBG GNSS/INS family (phase 2) ------------------------------------------------------
#
# Nine topics off one true pose. Every value below is a re-shaping of what `odometry_message`
# and `imu_message` already publish, which is the point of the probe check that they agree:
# two channels derived from one position can only disagree through a conversion fault, and a
# conversion fault in a GNSS message is otherwise entirely silent.
#
# **ENU throughout**, matching the driver's `use_enu: true` and REP-103, and matching what
# MetaDrive's world frame already is. Read a `velocity` here as the NED the same field carries
# under the other setting and north and east trade places - a 90 degree error that still plots
# as a car driving along a road.


def _absent_vector() -> dict:
    """A `geometry_msgs/Vector3` for a quantity that does not exist. See `ABSENT_SCALAR`."""
    return _point(ABSENT_SCALAR, ABSENT_SCALAR, ABSENT_SCALAR)


def _exact_vector() -> dict:
    """A 1-sigma accuracy of zero, which for ground truth is a true statement and not a gap."""
    return _point(0.0, 0.0, 0.0)


def _device_microseconds(seconds: float) -> int:
    """`time_stamp`: microseconds since the sensor powered up, which here is the drive's start.

    Wrapped rather than allowed to overflow the field. uint32 microseconds is 71.6 minutes, and
    a drive longer than that would otherwise raise deep inside CDR serialisation on one frame
    somewhere in the middle - the wrap is what the device itself does.
    """
    return int(round(seconds * 1e6)) % 2**32


def _gps_tow_ms(seconds: float) -> int:
    """GPS time of week in milliseconds, under the declared epoch. See `GPS_EPOCH_UNIX_S`."""
    return int(round(seconds * 1000.0)) % (SECONDS_PER_WEEK * 1000)


def _ekf_status() -> dict:
    """The filter's own account of itself: a full solution, aided by nothing.

    `solution_mode` 4 is `NAV_POSITION`, the nominal mode in which attitude, velocity and
    position are all computed - which is exactly what a simulator hands over. The four
    `*_valid` flags follow: each is defined by an error bound, and ground truth meets every one.

    **Every `*_used` flag is False, and that is the honest answer rather than an oversight.**
    They say which aiding sources the Kalman filter fused, and there is no filter, no GNSS
    receiver, no magnetometer and no odometer here - there is one true pose. A True on
    `gps1_pos_used` would describe a fusion that never happened.
    """
    return {
        "solution_mode": 4,
        "attitude_valid": True,
        "heading_valid": True,
        "velocity_valid": True,
        "position_valid": True,
        "vert_ref_used": False,
        "mag_ref_used": False,
        "gps1_vel_used": False,
        "gps1_pos_used": False,
        "gps1_hdt_used": False,
        "gps2_vel_used": False,
        "gps2_pos_used": False,
        "gps2_hdt_used": False,
        "odo_used": False,
        "dvl_bt_used": False,
        "dvl_wt_used": False,
        "vel1_used": False,
        "usbl_used": False,
        "air_data_used": False,
        "zupt_used": False,
        "align_valid": True,
        "depth_used": False,
        "zaru_used": False,
    }


def ekf_nav_message(frame: Frame) -> dict | None:
    """The fused navigation solution: velocity in ENU, and the same fix `nav_sat_fix` carries.

    Latitude and longitude come through `_latlon`, the one path in this module that turns metres
    into a real position, so this and `GNSS_FIX` cannot drift apart. `altitude` is MetaDrive's
    `z` and is no more "above mean sea level" here than it is there; `undulation` is the
    geoid-to-ellipsoid separation, which this repo genuinely does not have, so it is NaN rather
    than a zero that would make `altitude + undulation` look like a height above the ellipsoid.
    """
    found = _latlon(frame)
    if found is None:
        return None
    latitude, longitude = found
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "velocity": _point(ego.velocity_east, ego.velocity_north, 0.0),
        "velocity_accuracy": _exact_vector(),
        "latitude": latitude,
        "longitude": longitude,
        "altitude": ego.z,
        "undulation": ABSENT_SCALAR,
        "position_accuracy": _exact_vector(),
        "status": _ekf_status(),
    }


def ekf_quat_message(frame: Frame) -> dict:
    """The same orientation `imu_message` publishes, in the same order, from the same call."""
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "quaternion": quaternion(ego.heading, ego.pitch, ego.roll),
        "accuracy": _exact_vector(),
        "status": _ekf_status(),
    }


def ekf_euler_message(frame: Frame) -> dict:
    """Roll, pitch and yaw, ENU - so yaw is zero pointing **east**, not north.

    That is the driver's `use_enu: true` convention and it is already MetaDrive's: `Ego.heading`
    is radians CCW from +x, and +x is east. Under NED the same number would be a bearing from
    north, putting every heading 90 degrees out with nothing to notice it.
    """
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "angle": _point(ego.roll, ego.pitch, ego.heading),
        "accuracy": _exact_vector(),
        "status": _ekf_status(),
    }


def sbg_imu_message(frame: Frame) -> dict:
    """The raw inertial message - **not** `/sensing/gnss/imu/data`, which is one character away.

    Only `gyro` is produced, and it is bit-for-bit `imu_message`'s angular velocity. Everything
    else here is a quantity a simulator does not have and this type gives no way to disclaim:

    * `accel` - the same refusal `imu_message` makes with its -1. Differencing MetaDrive's
      velocity across a frame would put simulation noise into a field a real IMU measures, and
      zeros would claim a car in free fall, with no gravity vector a real FLU accelerometer
      always reads.
    * `temp` - a physical sensor temperature. `/sensing/gnss/imu/temp` is excluded from the 45
      for this exact reason; a number here would contradict that in the same bag.
    * `delta_vel` / `delta_angle` - sculling and coning, the strapdown integrator's own
      intermediates. There is no strapdown integrator.

    All four are `ABSENT_SCALAR`. The `imu_status` flags are True because they are built-in
    tests, and an ideal sensor passes its own: there is no fault to report, which is a different
    statement from having no reading.
    """
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "imu_status": {
            "imu_com": True,
            "imu_status": True,
            "imu_accel_x": True,
            "imu_accel_y": True,
            "imu_accel_z": True,
            "imu_gyro_x": True,
            "imu_gyro_y": True,
            "imu_gyro_z": True,
            "imu_accels_in_range": True,
            "imu_gyros_in_range": True,
            "imu_gyros_use_high_scale": False,
        },
        "accel": _absent_vector(),
        "gyro": _point(0.0, 0.0, ego.yaw_rate),
        "temp": ABSENT_SCALAR,
        "delta_vel": _absent_vector(),
        "delta_angle": _absent_vector(),
    }


def gps_pos_message(frame: Frame) -> dict | None:
    """The raw GNSS position, as distinct from the fused one - here they are the same position.

    On the rig these two differ: `ekf_nav` is the filter's answer at the INS reference point and
    this is the receiver's at the antenna. There is no lever arm and no filter in a simulator,
    so they coincide, and the probe asserts it - a disagreement could only be a conversion fault.

    `num_sv_tracked` and `num_sv_used` are 0xFF, which the message's **own** comment defines as
    N/A. That is better than NaN wherever a type provides it: it is what a real receiver writes
    when it does not know, so nothing has to learn one of our conventions to read it.
    """
    found = _latlon(frame)
    if found is None:
        return None
    latitude, longitude = found
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "status": {
            "status": 0,  # SOL_COMPUTED
            "type": 2,  # SINGLE - a standalone point solution, no corrections
            "ifm": 1,  # UNKNOWN: interference monitoring is not available
            "spoofing": 1,  # UNKNOWN: spoofing detection is not available
            "osnma": 1,  # DISABLED
            **{
                flag: False
                for flag in (
                    "gps_l1_used", "gps_l2_used", "gps_l5_used",
                    "glo_l1_used", "glo_l2_used", "glo_l3_used",
                    "gal_e1_used", "gal_e5a_used", "gal_e5b_used",
                    "gal_e5alt_used", "gal_e6_used",
                    "bds_b1_used", "bds_b2_used", "bds_b3_used",
                    "qzss_l1_used", "qzss_l2_used", "qzss_l5_used",
                )
            },
        },
        "gps_tow": _gps_tow_ms(frame.sim_time_s),
        "latitude": latitude,
        "longitude": longitude,
        "altitude": frame.ego.z,
        "undulation": ABSENT_SCALAR,
        "position_accuracy": _exact_vector(),
        "num_sv_tracked": 0xFF,
        "num_sv_used": 0xFF,
        "base_station_id": 0,
        "diff_age": 0,
    }


def gps_vel_message(frame: Frame) -> dict:
    """Velocity over ground, and the course it implies.

    `course` is ENU: degrees CCW from **east**, 0 to 360, the opposite sign to the NED bearing
    the same field carries under the other setting. It is derived from the velocity in the same
    message rather than from the heading, because they are not the same thing - a car sliding
    has a course that differs from where it is pointing - and the probe checks the two agree.

    Standing still, `atan2(0, 0)` is 0, which reads as due east. There is no course when there
    is no motion; a real receiver reports the same and callers gate on speed.
    """
    ego = frame.ego
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "status": {"vel_status": 0, "vel_type": 2},  # SOL_COMPUTED, VEL_DOPPLER
        "gps_tow": _gps_tow_ms(frame.sim_time_s),
        "velocity": _point(ego.velocity_east, ego.velocity_north, 0.0),
        "velocity_accuracy": _exact_vector(),
        "course": math.degrees(math.atan2(ego.velocity_north, ego.velocity_east)) % 360.0,
        "course_acc": 0.0,
    }


def utc_time_message(frame: Frame) -> dict:
    """The receiver's clock, under the epoch this drive declares. See `GPS_EPOCH_UNIX_S`.

    The calendar fields decompose `GPS_EPOCH_UNIX_S + sim_time_s`, so elapsed time inside the
    drive is exact and the date reads 1980-01-06 - a sentinel rather than a plausible recording
    session somebody might take at face value. `clock_utc_status` is 0, the message's own "the
    UTC time is not known, we are just propagating the UTC time internally", which says so
    in-band; `clock_stable` and `clock_utc_sync` are False because there is no PPS to lock to.

    The three `clk_*` fields are clock error statistics and are 0.0 rather than NaN: there is no
    oscillator, so there is genuinely no bias, no scale factor error and no residual - the same
    reason the accuracies are zero and not absent.
    """
    whole = int(math.floor(frame.sim_time_s))
    parts = time.gmtime(GPS_EPOCH_UNIX_S + whole)
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_stamp": _device_microseconds(frame.sim_time_s),
        "clock_status": {
            "clock_stable": False,
            "clock_status": 1,  # based on the internal crystal only
            "clock_utc_sync": False,
            "clock_utc_status": 0,  # the UTC time is not known
        },
        "year": parts.tm_year,
        "month": parts.tm_mon,
        "day": parts.tm_mday,
        "hour": parts.tm_hour,
        "min": parts.tm_min,
        "sec": parts.tm_sec,
        "nanosec": int(round((frame.sim_time_s - whole) * 1e9)),
        "gps_tow": _gps_tow_ms(frame.sim_time_s),
        "clk_bias_std": 0.0,
        "clk_sf_error_std": 0.0,
        "clk_residual_error": 0.0,
    }


def pos_ecef_message(frame: Frame) -> dict | None:
    """The same fix again, as earth-centred earth-fixed metres, in the `earth` frame.

    REP-105 names that frame `earth`, and it is the one frame in this bag that is not ours: a
    consumer joining our drive to anything else on the planet does it here. Derived from the
    fix rather than from MetaDrive's metres, so it cannot disagree with `nav_sat_fix` about
    where the car is - the probe converts one into the other and checks the millimetre.

    The radial component carries `Ego.z`, which is height above MetaDrive's near-flat world and
    not height above the ellipsoid. `geodesy.geodetic_to_ecef` says the same at its end.
    """
    found = _latlon(frame)
    if found is None:
        return None
    from geodesy import geodetic_to_ecef

    latitude, longitude = found
    x, y, z = geodetic_to_ecef(latitude, longitude, frame.ego.z)
    return {"header": header(frame.sim_time_s, EARTH_FRAME), "point": _point(x, y, z)}


def utc_ref_message(frame: Frame) -> dict:
    """One clock expressed in terms of another, which is the whole job of `TimeReference`.

    `header.stamp` is the simulator's clock and `time_ref` the UTC that clock corresponds to
    under the declared epoch, so the two differ by exactly `GPS_EPOCH_UNIX_S` and a reader can
    see the offset rather than infer it. Publishing them equal would have been the quiet lie -
    it says "our clock is UTC", which is the one thing this drive cannot claim.

    `source` is where the caveat goes, in words, because a `TimeReference` has no status field.
    """
    return {
        "header": header(frame.sim_time_s, BASE_FRAME),
        "time_ref": stamp(GPS_EPOCH_UNIX_S + frame.sim_time_s),
        "source": (
            "simulated; the drive's t=0 is declared to be the GPS epoch, so this is elapsed "
            "time and not a real UTC"
        ),
    }


#: `sensor_msgs/PointField`'s FLOAT32. The datatype enum lives in the message's *constants*,
#: which `ros_bag._coerce` never walks - it builds fields, not constants - so the number has to
#: be written down. 7 is FLOAT32 in every ROS 2 distro; the enum has not moved since ROS 1.
POINTFIELD_FLOAT32 = 7

#: x, y, z as little-endian float32: three fields, four bytes each.
POINT_STEP = 12


def lidar_points_message(frame: Frame) -> dict | None:
    """The sweep in `frame.extra["lidar"]`, rotated into the sensor's own frame.

    Returns None when the drive carried no lidar, which is every drive without `--ros-lidar` -
    the same shape `gnss_fix_message` uses for a dataset with no projection, so the topic is
    dropped whole rather than written empty.

    **The rotation is the whole of this function and the only thing in it that can be wrong.**
    The cloud arrives on world axes with its origin at the sensor, so turning it into the
    sensor's frame is one rotation by minus the car's heading - after which `+x` is forward,
    `+y` is left, and REP-103 is satisfied. Get the sign backwards and the cloud is still a
    plausible road seen from a plausible car: it is the same points, rigidly rotated, and
    nothing about its shape, its density or its extent says otherwise. What says otherwise is
    the field of view. A forward-facing sensor cannot return a point behind itself, so in the
    correct frame **every** point lies within half the FOV of `+x` - measured 100.00% at
    32.41 deg against a 32.5 deg half-angle - and in the flipped one, 0.00%. That is
    `ros_probe`'s check, and it is the reason the points are published in `lidar` and not in
    `map`, where no such check exists.

    Roll and pitch are deliberately **not** removed: the ego pitches by under half a degree on
    this terrain (measured -0.48 deg), and taking them out would need the camera's world hpr
    rather than the car's heading, which is a second sign to get wrong for a hundredth of the
    error the FOV check tolerates.
    """
    cloud = frame.extra.get("lidar")
    if cloud is None:
        return None
    import numpy

    points = numpy.asarray(cloud.points, dtype=numpy.float64)
    # Minus the heading, and written as a rotation by `-heading` rather than as a transposed
    # matrix so that the sign is visible in the source instead of implied by an index order.
    cos_h, sin_h = math.cos(-frame.ego.heading), math.sin(-frame.ego.heading)
    out = numpy.empty(points.shape, dtype="<f4")
    out[..., 0] = cos_h * points[..., 0] - sin_h * points[..., 1]
    out[..., 1] = sin_h * points[..., 0] + cos_h * points[..., 1]
    out[..., 2] = points[..., 2]
    # Range is measured on the way in, where the origin really is the sensor, so it does not
    # depend on the rotation above being right.
    reach = numpy.sqrt((points * points).sum(axis=-1))
    valid = numpy.isfinite(reach) & (reach <= cloud.max_range_m)
    out[~valid] = numpy.nan
    height, width = int(out.shape[0]), int(out.shape[1])
    return {
        "header": header(frame.sim_time_s, LIDAR_FRAME),
        "height": height,
        "width": width,
        "fields": [
            {"name": name, "offset": 4 * index, "datatype": POINTFIELD_FLOAT32, "count": 1}
            for index, name in enumerate(("x", "y", "z"))
        ],
        # Little-endian, because the array's dtype says `<f4` rather than `f4`. The native
        # order is little on every machine this runs on and would be right by accident; a bag
        # is read elsewhere, and `is_bigendian` is a claim about the bytes, not about the host.
        "is_bigendian": False,
        "point_step": POINT_STEP,
        "row_step": POINT_STEP * width,
        "data": out.view(numpy.uint8).reshape(-1),
        # A claim about *this* cloud, not a constant: it says whether a reader has to test for
        # NaN before using a point. Every real sweep here has misses in it, so it is False in
        # practice - but saying so unconditionally would be saying it without having looked.
        "is_dense": bool(valid.all()),
    }


#: `AV_PKT_FLAG_KEY`, libav's own value, which is what `ffmpeg_image_transport` copies into
#: `FFMPEGPacket.flags`. A decoder that joins mid-stream looks for this and nothing else.
PACKET_FLAG_KEY = 1


def camera_packet_message(frame: Frame, rig_name: str) -> dict | None:
    """One camera's packet this frame, or None when there is not one.

    None is the ordinary case rather than an error, twice over: on a strided drive most frames
    are not decision frames and carry no packet at all, and on a drive with no `--ros-camera`
    none of them do. `messages` drops a topic whose builder returns None, so the six camera
    channels simply are not in the bag rather than being in it with holes.

    Written per camera rather than as one message with six pictures in it because that is what
    the rig does - six topics, one per lens - and a consumer subscribing to `front_left` should
    not have to receive and discard five other cameras to get it.
    """
    for packet in frame.extra.get("camera_packets", ()):
        if packet.name != rig_name:
            continue
        import numpy

        return {
            "header": header(frame.sim_time_s, packet.frame_id),
            "width": int(packet.width),
            "height": int(packet.height),
            "encoding": packet.encoding,
            "pts": int(packet.pts),
            "flags": PACKET_FLAG_KEY if packet.keyframe else 0,
            # A claim about the bytes, not about the host. H.264 is a byte stream with no
            # endianness of its own, so this is False for every packet we will ever write -
            # said explicitly rather than left to a default, because the field exists.
            "is_bigendian": False,
            # `frombuffer`, not `asarray`: `ros_bag._coerce` turns a `uint8[]` field into
            # `numpy.asarray(value, dtype=uint8)`, and that raises on a `bytes` rather than
            # reinterpreting it. Kept here so `CameraPacket.data` can stay the plain bytes an
            # encoder produces.
            "data": numpy.frombuffer(packet.data, dtype=numpy.uint8),
        }
    return None


def _camera_packet_builder(rig_name: str):
    """One builder per camera topic, so `BUILDERS` stays a plain topic -> callable table."""

    def build(frame: Frame) -> dict | None:
        return camera_packet_message(frame, rig_name)

    build.__name__ = f"camera_packet_message_{rig_name}"
    return build


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
    SBG_EKF_NAV: ekf_nav_message,
    SBG_EKF_QUAT: ekf_quat_message,
    SBG_EKF_EULER: ekf_euler_message,
    SBG_IMU_DATA: sbg_imu_message,
    SBG_GPS_POS: gps_pos_message,
    SBG_GPS_VEL: gps_vel_message,
    SBG_UTC_TIME: utc_time_message,
    GNSS_POS_ECEF: pos_ecef_message,
    GNSS_UTC_REF: utc_ref_message,
    LIDAR_POINTS: lidar_points_message,
    **{
        camera_topic(name, "image_raw/ffmpeg"): _camera_packet_builder(name)
        for name in _CAMERAS
    },
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
