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
the ten `sbg_driver` GNSS messages), the topic is **left out rather than published with a
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

#: Topics in `bag_audit.html` this module cannot write, and why. Not a todo list - each one
#: needs a `.msg` definition only Keith has, and guessing at it is worse than omitting it.
MISSING_DEFINITIONS: dict[str, str] = {
    "/vehicle/state": "wingfin message; type not in the audit",
    "/vehicle/engagement": "wingfin message; type not in the audit",
    "/vehicle/actuators_output": "wingfin message; type not in the audit",
    "/control/actuators": "wingfin message; type not in the audit",
    "/sensing/camera/*/meta": "wingfin message; type not in the audit",
    "/sensing/gnss/ekf_nav": "sbg_driver/SbgEkfNav",
    "/sensing/gnss/ekf_quat": "sbg_driver/SbgEkfQuat",
    "/sensing/gnss/ekf_euler": "sbg_driver/SbgEkfEuler",
    "/sensing/gnss/gps_pos": "sbg_driver/SbgGpsPos",
    "/sensing/gnss/gps_vel": "sbg_driver/SbgGpsVel",
    "/sensing/gnss/status": "sbg_driver/SbgStatus",
    "/sensing/gnss/utc_time": "sbg_driver/SbgUtcTime",
    "/sensing/gnss/imu/utc_ref": "sensor_msgs/TimeReference, but the rig's exact use is unknown",
    "/sensing/gnss/imu/pos_ecef": "geometry_msgs/PointStamped in most SBG drivers; unconfirmed",
    "/sensing/gnss/imu/temp": "a physical sensor temperature; nothing in a simulator produces it",
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


#: Topic -> builder, for everything written every frame. `ROUTE` and `TF_STATIC` are absent
#: deliberately: they are latched, written once per episode by `ros_bag.py`.
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
