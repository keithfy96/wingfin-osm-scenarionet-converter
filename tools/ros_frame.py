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


def read(env, index, sim_time_s, projection=None):
    """One frame. Called immediately after `env.step`, before anything else consumes the env."""
    return ros_schema.Frame(
        index=index,
        sim_time_s=sim_time_s,
        ego=ego_of(env),
        boxes=boxes_of(env),
        lights=lights_of(env),
        projection=projection,
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
    out = {}
    for camera in getattr(rig, "cameras", ()):
        right, forward, up = camera.position
        heading_deg = camera.hpr[0]
        out[camera.name] = (
            float(forward),
            -float(right),
            float(up),
            math.radians(float(heading_deg)),
        )
    return out
