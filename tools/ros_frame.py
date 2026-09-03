"""Read one simulated step out of the env, as a `ros_schema.Frame`.

The MetaDrive-facing half of Stage 10, kept apart from both neighbours on purpose:

    ros_schema.py   Frame -> messages     no MetaDrive, no rosbags   (unit tested)
    ros_frame.py    env   -> Frame        needs a live engine        (this file)
    ros_bag.py      Frame -> bag on disk  no MetaDrive               (unit tested)

Only this file needs a simulator to run, so it is the only one that cannot be covered by
`uv run pytest`, and it is deliberately the thinnest of the three: it reads attributes and does
no arithmetic beyond what MetaDrive does not already provide.

**Python version is a real gate here, not a formality.** `drive.py` runs on `METADRIVE_PYTHON`,
which on the host is the MetaDrive checkout's **3.8** venv, while `rosbags` needs 3.10+. In the
container the two are the same 3.10 interpreter and this simply works; on the host it does not,
and it must say so before a drive starts rather than raising an ImportError three hundred frames
in. `refuse_if_unsupported()` is that check.

**Boxes come from the engine, never from the dataset's track list.** `BaseEngine.get_objects()`
(`base_engine.py:219`) returns what is actually in the world this frame. A walker whose tape has
run out has been removed from it - and from the render - so a box recovered from the tracks
would sit over empty road. That is a phantom label, and it teaches a detector to hallucinate
people, which is worse than the absence it was meant to fix.
"""

from __future__ import annotations

import contextlib
import math
import sys

import ros_schema

#: Kinds we can name. Anything else keeps MetaDrive's own class name rather than being forced
#: into one of these, so an unrecognised object is visibly unrecognised instead of silently
#: becoming a car.
FALLBACK_SIZES = {
    "PEDESTRIAN": (0.6, 0.6, 1.8),
    "CYCLIST": (1.75, 0.6, 1.8),
    "VEHICLE": (4.6, 1.85, 1.5),
}

#: What MetaDrive calls the point-cloud sensor in `config["sensors"]`, and what
#: `engine.get_sensor` is asked for. One name, used by `drive.py` to register it and by
#: `mount_lidar` to find it again.
LIDAR_SENSOR = "point_cloud"

#: Rays across a beam, and beams. 200x64 is `sensor_survey.py`'s own default, so a cloud in a
#: bag and a cloud in a survey are the same shape and can be compared without rescaling.
LIDAR_DEFAULT_SIZE = (200, 64)

#: Horizontal FOV, set on the lens explicitly. MetaDrive's `camera_fov` default is also 65, so
#: leaving it alone would look identical today and silently follow that key if it ever moved -
#: and the vertical FOV, which nothing sets directly, follows the aspect ratio from here
#: (200x64 at 65 deg measures 23.045 deg vertically).
LIDAR_FOV_DEG = 65.0

#: Where the sensor sits, in MetaDrive's ego frame: x right, y forward, z up. 0.8 m forward and
#: 1.5 m up is `metadrive.constants.DEFAULT_SENSOR_OFFSET`, which is where every other camera in
#: this repo is mounted, so the cloud and the rig's images look out from the same place.
LIDAR_MOUNT = (0.0, 0.8, 1.5)

#: Beyond this, a return is called a miss and written NaN. See `ros_schema.LidarCloud`: the
#: depth buffer's far plane is 100 km, so this is a range we declare, not one MetaDrive
#: enforces. Matches `sensor_survey.POINT_CLOUD_MAX_RANGE_M`, which was measured against the
#: same sensor. On junction-1 roughly half of every sweep is sky and falls outside it.
LIDAR_MAX_RANGE_M = 200.0


class RosFrameError(RuntimeError):
    pass


def refuse_if_unsupported():
    """Refuse a bag on an interpreter that cannot write one, before the drive starts."""
    # ruff reads `requires-python = ">=3.10"` and calls this block outdated; it is not.
    # `tools/` is the one part of the repo that also runs on the MetaDrive checkout's 3.8, which
    # is exactly the interpreter this has to catch.
    if sys.version_info < (3, 10):  # noqa: UP036
        raise RosFrameError(
            "--ros-bag needs Python 3.10 or newer for `rosbags`, and this is "
            f"{sys.version_info.major}.{sys.version_info.minor}. On the host, drive.py runs on "
            "METADRIVE_PYTHON, which points at the MetaDrive checkout's 3.8 venv. Two ways "
            "out: run it in the container (`./scripts/sim.sh ...`), where MetaDrive and this "
            "repo share one 3.10 interpreter, or set METADRIVE_PYTHON=.venv/bin/python with "
            "`uv sync --group sim --group ros` first."
        )
    try:
        import rosbags  # noqa: F401
    except ImportError:
        # Two different fixes, and telling a caller the wrong one costs a rebuild or a resync.
        # In the container the environment is baked into the image, so `uv sync` there edits an
        # environment the next `docker compose run` throws away; the image is what has to change.
        # This branch is not hypothetical in here: the image synced `sim gpu model` and not `ros`
        # from the day it was built, while three other places named the container as the way out
        # of the host's 3.8.
        import os

        raise RosFrameError(missing_group_message(os.path.exists("/.dockerenv"))) from None


def missing_group_message(in_container):
    """What to do about a missing `rosbags`, which is not the same thing in the two places."""
    if in_container:
        return (
            "--ros-bag needs the `ros` dependency group, and this image does not carry it. "
            "A `uv sync` in here would not survive the container: rebuild the image instead, "
            "with `docker compose build` on the host, after checking docker/Dockerfile syncs "
            "--group ros."
        )
    return (
        "--ros-bag needs the `ros` dependency group: uv sync --group sim --group ros "
        "(name every group you want - syncing one alone removes the others)."
    )


def projection_of(scenario):
    """The map's real-world origin, with MetaDrive's re-centring already folded in.

    Two facts out of the dataset, both recorded by the converter rather than guessed:
    `metadata.coordinate_system_wkt` is the azimuthal-equidistant CRS Stage 1 chose over the
    real OSM extract, and `metadata.old_origin_in_current_coordinate` is the shift MetaDrive
    applied when it re-centred the scenario on the ego's first position - 93.8 m on junction-1.

    Returns None when the dataset carries no CRS, which is not an error: everything except the
    GNSS channels is still worth writing, and `ros_schema.gnss_fix_message` drops those.
    """
    metadata = (scenario or {}).get("metadata") or {}
    wkt = metadata.get("coordinate_system_wkt")
    if not wkt:
        return None
    try:
        from geodesy import projection_origin

        latitude, longitude = projection_origin(wkt)
    except Exception:
        return None
    # `or (0.0, 0.0)` would be the natural way to write this and raises: the value is a numpy
    # array, and a two-element array has no truth value. It is exactly the sort of line that
    # looks right until a real dataset reaches it.
    shift = metadata.get("old_origin_in_current_coordinate")
    if shift is None or len(shift) < 2:
        shift = (0.0, 0.0)
    return ros_schema.Projection(
        origin_lat=float(latitude),
        origin_lon=float(longitude),
        offset_x=float(shift[0]),
        offset_y=float(shift[1]),
    )


def route_of(env, every_m=2.0):
    """The route being followed, sampled along its own reference trajectory.

    `ConnectorFeature.centerline` is deliberately not consulted: it is a marker, not a driving
    line, and splicing it into anything that looks like a path is a mistake this repo has
    written down twice.
    """
    navigation = getattr(env.agent, "navigation", None)
    trajectory = getattr(navigation, "reference_trajectory", None)
    if trajectory is None:
        return ()
    length = float(getattr(trajectory, "length", 0.0) or 0.0)
    if length <= 0:
        return ()
    steps = max(2, int(length / every_m) + 1)
    out = []
    for index in range(steps):
        point = trajectory.position(min(index * every_m, length), 0.0)
        out.append((float(point[0]), float(point[1])))
    return tuple(out)


def _kind(obj):
    """MetaDrive's own vocabulary, so the bag, the dataset and the actor plan use one word.

    Two different attributes, because MetaDrive uses two: traffic participants declare
    `TYPE_NAME` (`traffic_participants/pedestrian.py:18`) and static objects declare
    `CLASS_NAME` (`static_object/traffic_object.py:50,136`). Checking only the first gives
    barriers the Python class name `TrafficBarrier` instead of `TRAFFIC_BARRIER`, which is
    the word the dataset track carries - close enough to look right in a report and wrong
    enough to miss a filter.
    """
    for attribute in ("TYPE_NAME", "CLASS_NAME"):
        name = getattr(obj, attribute, None)
        if name:
            return str(name)
    return type(obj).__name__


def _size(obj, kind):
    """Length, width and height, falling back per kind rather than to a single default.

    A missing dimension written as zero would produce a degenerate box that some consumers
    silently drop and others draw as a point, so a plausible size for the kind is the safer
    wrong answer - and `ros_probe.py` reports how often it was needed.
    """
    fallback = FALLBACK_SIZES.get(kind, (1.0, 1.0, 1.0))
    length = getattr(obj, "LENGTH", None)
    width = getattr(obj, "WIDTH", None)
    height = getattr(obj, "HEIGHT", None) or getattr(obj, "height", None)
    return (
        float(length if length else fallback[0]),
        float(width if width else fallback[1]),
        float(height if height else fallback[2]),
    )


def boxes_of(env):
    """Every object in the scene except the ego, as world-frame boxes."""
    ego = env.agent
    out = []
    for identifier, obj in env.engine.get_objects().items():
        if obj is ego:
            continue
        position = getattr(obj, "position", None)
        if position is None:
            continue
        kind = _kind(obj)
        # Traffic lights are objects too, and they go on their own topic with their colour.
        # A box round a light is not a thing a detector should be asked to find.
        if kind.startswith("LIGHT") or "TrafficLight" in type(obj).__name__:
            continue
        length, width, height = _size(obj, kind)
        heading = getattr(obj, "heading_theta", None)
        z = 0.0
        origin = getattr(obj, "origin", None)
        if origin is not None:
            with contextlib.suppress(Exception):
                z = float(origin.getZ())
        out.append(
            ros_schema.Box(
                name=str(identifier),
                kind=kind,
                x=float(position[0]),
                y=float(position[1]),
                z=z,
                heading=float(heading if heading is not None else 0.0),
                length=length,
                width=width,
                height=height,
            )
        )
    out.sort(key=lambda box: box.name)
    return tuple(out)


def lights_of(env):
    """Traffic lights and their colours, straight off the light manager."""
    manager = getattr(env.engine, "light_manager", None)
    if manager is None:
        return ()
    out = []
    for light in manager.spawned_objects.values():
        position = getattr(light, "position", (0.0, 0.0))
        out.append(
            ros_schema.Light(
                name=str(getattr(light, "id", "")),
                status=str(getattr(light, "status", "")),
                x=float(position[0]),
                y=float(position[1]),
                lane=str(getattr(getattr(light, "lane", None), "index", "") or ""),
            )
        )
    out.sort(key=lambda light: light.name)
    return tuple(out)


def ego_of(env):
    agent = env.agent
    position = agent.position
    velocity = getattr(agent, "velocity", (0.0, 0.0))
    z = 0.0
    with contextlib.suppress(Exception):
        z = float(agent.origin.getZ())
    yaw_rate = 0.0
    # Bullet reports the body's angular velocity directly; differencing headings across a frame
    # would fold the frame rate into a quantity a gyroscope measures outright.
    with contextlib.suppress(Exception):
        yaw_rate = float(agent.body.getAngularVelocity()[2])
    return ros_schema.Ego(
        x=float(position[0]),
        y=float(position[1]),
        z=z,
        heading=float(agent.heading_theta),
        velocity_east=float(velocity[0]),
        velocity_north=float(velocity[1]),
        speed=float(agent.speed),
        yaw_rate=yaw_rate,
        roll=float(getattr(agent, "roll", 0.0) or 0.0),
        pitch=float(getattr(agent, "pitch", 0.0) or 0.0),
    )


def controls_of(env, policy, engaged, previous_speed=None, previous_angle_deg=None, dt=0.0):
    """What the drive commanded this step, as a `ros_schema.Controls`.

**Read off the agent, not off the `action` array `env.step` was given.** Those are the
    same numbers only under `--agent-policy remote` and `manual`, where the caller supplies the
    action. Under `idm` the policy is registered inside the environment and sets the vehicle
    directly, so the caller's array stays `[0, 0]` for the whole drive - measured: a bag written
    from it carried `steer 0.0000` and `steering_angle_deg 0.000` on every frame of a drive
    whose own measured curvature reached 0.086 1/m. `agent.steering` and `agent.throttle_brake`
    are what was actually applied (`base_vehicle.py:465`), whoever decided it.

    `max_steering` comes off the agent too, because it is a per-vehicle config value and
    hard-coding a road-wheel angle would make every `steering_angle_deg` in the bag a property
    of whichever car the config happened to name.

    `previous_speed` and `previous_angle_deg` are the last frame's, and `dt` the step between;
    together they give `a_ego` and `steering_rate_deg_s`, which are differences and not readings.
    Passed as plain floats rather than the previous `Controls`, because the speed being
    differenced is the *observed* one off the agent and does not live on a commanded-controls
    record.
    `VehicleState.a_ego`'s own comment says the vehicle does the same - differenced by the
    publisher, because the DBC declares no longitudinal accelerometer - so this follows a stated
    convention rather than inventing one. Both are 0.0 on the first frame, which is honest: no
    previous frame means no difference, not an acceleration of zero measured.
    """
    agent = env.agent
    steering = float(agent.steering)
    max_steering_deg = float(getattr(agent, "max_steering", 0.0))
    accel = 0.0
    rate = 0.0
    if dt > 0.0:
        if previous_speed is not None:
            accel = (float(agent.speed) - previous_speed) / dt
        if previous_angle_deg is not None:
            rate = (steering * max_steering_deg - previous_angle_deg) / dt
    return ros_schema.Controls(
        steering=steering,
        throttle_brake=float(agent.throttle_brake),
        max_steering_deg=max_steering_deg,
        policy=policy,
        engaged=engaged,
        accel=accel,
        steering_rate_deg_s=rate,
    )


def prediction_of(model, waypoints, frame_counter, agent):
    """One decision's model output, as a `ros_schema.ModelPrediction`.

    `waypoints` is the checkpoint's raw `(N, 8)`, **unconverted** - the mirror out of the
    model's frame happens in the builder, once, where it is named. Passing an already-mirrored
    array here would put the flip in two places and make the second one invisible.
    """
    import av3_model

    rows = getattr(waypoints, "shape", (0,))[0]
    return ros_schema.ModelPrediction(
        waypoints=waypoints,
        times_s=tuple(av3_model.waypoint_times(rows)) if rows else (),
        frame_counter=int(frame_counter),
        model_name=getattr(model, "model_name", ""),
        weight_name=getattr(model, "weight_name", ""),
        ego_x=float(agent.position[0]),
        ego_y=float(agent.position[1]),
        ego_heading=float(agent.heading_theta),
    )


def read(env, index, sim_time_s, projection=None, lidar=None, camera_packets=(), controls=None,
         prediction=None):
    """One frame. Called immediately after `env.step`, before anything else consumes the env.

    `lidar` is a `ros_schema.LidarCloud` or None, and None is the ordinary case: a cloud is read
    at the **decision** rate rather than every step, so most frames of a strided drive carry
    none and `lidar_points_message` drops the topic for those frames rather than repeating the
    last sweep under a new stamp. A held sweep republished is the same fault as a held camera
    frame republished - it tells a reader the world stood still.

    `camera_packets` is the same arrangement for the six `image_raw/ffmpeg` topics: a tuple of
    `ros_schema.CameraPacket` on a decision frame and empty on every other, and empty is the
    ordinary case for the same reason. It is literally the held-camera-frame case the sentence
    above is about.

    `prediction` is the model's own output for this decision, `None` on every drive without
    one - which is most of them, and is why the three model topics are absent rather than empty.

    `controls` is what the drive commanded - `None` when nothing is driving, which is not a
    degenerate case but the ordinary one for a replay. The three phase-5 topics are then absent
    rather than zero-filled, which `EngagementStatus`'s own comment asks for outright.
    """
    extra = {}
    if lidar is not None:
        extra["lidar"] = lidar
    if camera_packets:
        extra["camera_packets"] = tuple(camera_packets)
    return ros_schema.Frame(
        index=index,
        sim_time_s=sim_time_s,
        ego=ego_of(env),
        boxes=boxes_of(env),
        lights=lights_of(env),
        projection=projection,
        controls=controls,
        prediction=prediction,
        extra=extra,
    )


def sensor_config(size=LIDAR_DEFAULT_SIZE):
    """The `sensors=` entry that registers the point cloud, for merging into MetaDrive's config.

    `ego_centric=True`, and it does less than the name promises: it zeroes the translation and
    leaves the rotation built from the camera's *world* hpr, so the cloud comes back as metres
    from the sensor on **world** axes. `ros_schema.lidar_points_message` is what finishes the
    job. `ego_centric=False` would hand back true world coordinates, which need no rotation at
    all and for that exact reason cannot be checked - see that function.
    """
    from metadrive.component.sensors.point_cloud_lidar import PointCloudLidar

    width, height = size
    return {LIDAR_SENSOR: (PointCloudLidar, int(width), int(height), True)}


def mount_lidar(env, fov_deg=LIDAR_FOV_DEG):
    """Bolt the sensor to the ego and fix its lens. Call after each `reset()`, like `Rig.mount`.

    Mounted rather than aimed per read for the reason `camera_rig.Rig.mount` is: `perceive` with
    a `new_parent_node` re-parents the camera and steps the task manager **twice** to re-render
    it, which is a second render pass per frame for a picture the frame pass has already drawn.
    Mounted, `perceive()` reads the buffer that is already there.
    """
    sensor = env.engine.get_sensor(LIDAR_SENSOR)
    sensor.lens.setFov(float(fov_deg))
    sensor.track(env.agent.origin, LIDAR_MOUNT, (0.0, 0.0, 0.0))
    return sensor


def lidar_cloud(sensor, fov_deg=LIDAR_FOV_DEG, max_range_m=LIDAR_MAX_RANGE_M):
    """One sweep off a mounted sensor, in metres.

    **`to_float=True` is not what it sounds like, and it is the correct call here.** For an RGB
    camera `BaseCamera._format` divides by 255 to get 0-1; `DepthCamera._format`, which
    `PointCloudLidar` inherits, overrides that and returns the array **untouched**
    (`depth_camera.py:184-191`). It is the `to_float=False` branch that converts - `(ret *
    255).astype(uint8)` - and a cloud running thousands of metres does not survive it. Measured
    on junction-1: `perceive(to_float=True)` is bit-identical to `get_rgb_array_cpu()`, ratio
    exactly 1.0 on every element, while `to_float=False` comes back uint8 clipped to 0..255.

    So the fault `docs/reference/sensors-and-observations.md` records for the policy socket is
    real and is the uint8 path. Reading in-process does avoid it, and this is the line that has
    to keep doing so: `to_float=False` here would be silent.
    """
    import numpy

    return ros_schema.LidarCloud(
        points=numpy.asarray(sensor.perceive(to_float=True)),
        fov_deg=float(fov_deg),
        max_range_m=float(max_range_m),
    )


def mounts_from_rig(rig):
    """Camera mounts for `/tf_static`, converted out of MetaDrive's ego frame into REP-103.

    **Read `camera_rig.py:130-131` before touching this.** `Camera.position` is *not* the CARLA
    spec the file is parsed from - the spec is converted on the way in (`camera_rig.py:411`), and
    what is stored is already MetaDrive's ego frame:

        MetaDrive ego   x RIGHT, y FORWARD, z up, hpr[0] heading in degrees, + is LEFT
        ROS base_link   x FORWARD, y LEFT,  z up, yaw in radians,            + is LEFT

    So forward and lateral **swap**, the lateral one negates, and the yaw sign is already right
    and must be left alone. Checked against `rigs/cams.txt`: `cam_left` is spec `yaw: -55`, which
    `camera_rig.py:414` stores as `hpr[0] = +55`, and +55 in ROS is 55 degrees to the left -
    which is where a camera called `cam_left` should point.

    Every part of this is silent when wrong. Swap the axes and every camera sits on the car's
    centreline pointing sideways; negate the yaw and the left and right cameras trade places,
    which looks entirely normal until something fuses an image with the boxes.
    """
    return {
        camera.name: _ros_mount(camera.position, camera.hpr[0])
        for camera in getattr(rig, "cameras", ())
    }


def _ros_mount(position, heading_deg):
    """MetaDrive's ego frame to REP-103's, for one sensor. The only place that does the swap.

    Factored out when the lidar became the second thing mounted on the car: two copies of this
    is two chances to negate the wrong one, and a `/tf_static` where the cameras are right and
    the lidar is mirrored is exactly the sort of thing that looks fine until something fuses
    the two.
    """
    right, forward, up = position
    return (float(forward), -float(right), float(up), math.radians(float(heading_deg)))


def lidar_mount():
    """The lidar's own `/tf_static` entry, `{frame: (x, y, z, yaw)}`, ready to merge with the rig's.

    Through the same conversion the cameras go through, from the same kind of MetaDrive-frame
    tuple, so the two cannot disagree about which way is forward.
    """
    return {ros_schema.LIDAR_FRAME: _ros_mount(LIDAR_MOUNT, 0.0)}


def cameras_from_rig(rig):
    """The rig's cameras as `ros_schema.CameraSpec`, for the six `camera_info_latched` topics.

    A camera the vehicle has no counterpart for is **left out rather than given an invented
    topic** - `rigs/cams.txt`'s `cam_front_wide` is the only one in the repo, a spare buffer with
    no channel on the rig. It is still mounted, still rendered and still in `/tf_static`, where it
    is honestly a seventh camera; what it is not is one of `bag_audit.html`'s six, and adding a
    seventh `cam_sync_rig` channel would put a topic in our bag that the rig's cannot have.

    `frame_id` stays the spec's own name so that it matches `mounts_from_rig` exactly - the two
    are joined through `/tf_static`, not by both happening to spell a camera the same way. See
    `ros_schema.RIG_CAMERA_NAMES` for why that distinction has to survive.
    """
    out = []
    for camera in getattr(rig, "cameras", ()):
        rig_name = ros_schema.rig_camera_name(camera.name)
        if rig_name is None:
            continue
        out.append(
            ros_schema.CameraSpec(
                name=rig_name,
                frame_id=camera.name,
                width=int(camera.width),
                height=int(camera.height),
                fov_deg=float(camera.fov),
            )
        )
    out.sort(key=lambda camera: camera.name)
    return tuple(out)


def unmapped_cameras(rig):
    """Spec cameras with no rig topic, so a caller can say so rather than silently drop them."""
    return tuple(
        camera.name
        for camera in getattr(rig, "cameras", ())
        if ros_schema.rig_camera_name(camera.name) is None
    )
