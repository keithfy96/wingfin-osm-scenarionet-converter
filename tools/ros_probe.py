"""Prove a written bag is right, before anything trains on it.

    uv run python tools/ros_probe.py bags/junction-1-001
    uv run python tools/ros_probe.py bags/junction-1-001 --workspace workspaces/junction-1
    uv run python tools/ros_probe.py --coverage

In the spirit of `scripts/av3-probe.sh`, and for the same reason: **a drive statistic cannot
settle a sign question.** Every fault this looks for produces a bag that opens, deserialises and
renders - and is wrong. A heading 180 degrees out still plots a car on a road. A twist published
in the world frame is exactly correct while the car drives east. A GNSS reading that skipped
MetaDrive's re-centring shift is 93.8 m along a road that really exists.

So each check below is a **relationship between two independently produced quantities**, not a
value read against a constant. The car's heading is checked against the direction it actually
moved; the twist against the change in position; the GNSS against the odometry it was derived
beside; the transform against the pose. Any single one of them can be wrong in isolation and
look fine; they cannot all agree while any is wrong.

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from itertools import groupby
from pathlib import Path

import geodesy

# Module scope, not inside `probe()`. It used to be imported twice, under `if clouds:` and under
# `if streams:`, and read a third time by the steering-sign check further down -- which is in the
# same function, so on a bag with neither a point cloud nor camera packets the name was never
# bound and that check died with `UnboundLocalError: local variable 'numpy' referenced before
# assignment`. It took this long to surface because the check needs a bag that is **self-driven**
# (a replay bag writes no vehicle state, so it never runs) and has **no lidar and no cameras** --
# which is the plainest useful bag there is, and the one a rig would record first.
#
# Nothing was being deferred in any case: numpy is a base dependency in pyproject, not an optional
# group, unlike `rosbags` -- whose absence `ros_frame.refuse_if_unsupported` turns into a sentence
# and which is why the lazy-import habit exists in these files at all.
import numpy
import ros_audit
import ros_schema

EXTENT_PAD_M = 50.0
"""How far outside its own OSM extent a fix may land. See the containment check for why."""

FORWARD_MIN_M = 0.05
"""Below this a frame is a car standing still, and its direction of travel means nothing."""


def _typestore():
    from rosbags.typesys import Stores, get_types_from_msg, get_typestore

    store = get_typestore(Stores.ROS2_HUMBLE)
    extra = {}
    for name, text in ros_schema.EXTRA_DEFINITIONS.items():
        extra.update(get_types_from_msg(text, name))
    store.register(extra)
    return store


def load(path):
    """Every message, deserialised, grouped by topic - and by the stamp it claims."""
    # Before `Reader`, which raises a bare FileNotFoundError from inside the library. This tier
    # reads what the tier before it recorded, so "there is no bag there yet" is the likeliest
    # thing to be wrong; `ros_audit` owns the message so the two readers cannot drift.
    ros_audit.refuse_if_missing(path)

    from rosbags.rosbag2 import Reader

    store = _typestore()

    class _ByTopic(defaultdict):
        """A `defaultdict` that also remembers each topic's declared type.

        A plain `defaultdict` has no `__dict__`, so the type map cannot simply be attached to
        one; and returning a third value would change a signature two other call sites and a
        test already unpack. A one-line subclass keeps both.
        """

        types: dict[str, str] = {}

    by_topic = _ByTopic(list)
    by_topic.types = {}
    by_stamp = defaultdict(set)
    with Reader(path) as reader:
        for connection, log_time, raw in reader.messages():
            message = store.deserialize_cdr(raw, connection.msgtype)
            by_topic.types[connection.topic] = connection.msgtype
            by_topic[connection.topic].append((log_time, message))
            by_stamp[log_time].add(connection.topic)
    for messages in by_topic.values():
        messages.sort(key=lambda pair: pair[0])
    return by_topic, by_stamp


def stale_types(by_topic) -> dict[str, tuple[str, str]]:
    """Topics whose type in this bag is not the type this module now declares.

    **A bag outlives the code that wrote it**, and this repo has now changed a type once:
    `/sensing/gnss/pose` carried a `geometry_msgs/PoseStamped` until 2026-09-03 and carries a
    `sensor_msgs/NavSatFix` after it. Every check downstream reads fields by name, so an older
    bag met the new checks with an `AttributeError` and a traceback - which says nothing about
    the bag and looks like a broken tool.

    Reporting it is the useful behaviour and it is also a real finding: a bag whose types no
    longer match the code is a bag whose consumers were built against something else.
    """
    found = getattr(by_topic, "types", {})
    return {
        topic: (was, ros_schema.TOPICS[topic][0])
        for topic, was in found.items()
        if topic in ros_schema.TOPICS and was != ros_schema.TOPICS[topic][0]
    }


def _yaw(orientation):
    """Yaw out of a quaternion, the inverse of `ros_schema.quaternion`."""
    x, y, z, w = orientation.x, orientation.y, orientation.z, orientation.w
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _metres(lat_a, lon_a, lat_b, lon_b):
    """Roughly how far apart two fixes are, for reporting a difference that ought to be zero.

    Deliberately crude - a degree of latitude as a flat 111.32 km, longitude scaled by the
    cosine. Every caller is comparing two channels derived from one `_latlon` call and so
    expects bit-identical floats; this exists to turn "not equal" into a number a person can
    read, not to measure a distance. Anything that needs real metres uses `geodesy`.
    """
    scale = math.cos(math.radians(lat_a))
    return math.hypot(lat_a - lat_b, (lon_a - lon_b) * scale) * 111_320.0


class Checks:
    def __init__(self):
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((bool(ok), name, detail))
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -  ' + detail if detail else ''}")
        return bool(ok)

    @property
    def failed(self):
        return [name for ok, name, _ in self.results if not ok]


def probe(path, workspace=None, out=sys.stdout):
    by_topic, by_stamp = load(path)
    checks = Checks()
    print(f"{path}", file=out)

    stale = stale_types(by_topic)
    for topic, (was, now) in sorted(stale.items()):
        print(
            f"        STALE  {topic} carries {was}; this repo now writes {now}.\n"
            "               The bag predates the correction - re-record it. Checks that read "
            "this topic are skipped.",
            file=out,
        )

    odom = by_topic.get(ros_schema.ODOMETRY, [])
    tf = by_topic.get(ros_schema.TF, [])
    clock = by_topic.get(ros_schema.CLOCK, [])

    # --- 1. nothing lost, and nothing arrived twice -------------------------------------
    per_frame = [t for t in by_topic if ros_schema.TOPICS.get(t, ("", ""))[1] == "state"]
    counts = {topic: len(by_topic[topic]) for topic in per_frame}
    checks.check(
        "every per-frame topic holds the same number of messages",
        len(set(counts.values())) <= 1,
        ", ".join(f"{t.split('/')[-1]}={n}" for t, n in sorted(counts.items())),
    )
    stamps = sorted({t for t, _ in clock})
    checks.check(
        "time is strictly increasing, with no repeated frame",
        len(stamps) == len(clock),
        f"{len(clock)} messages, {len(stamps)} distinct stamps",
    )

    # --- 2. one instant, one stamp, every topic ----------------------------------------
    complete = [s for s, topics in by_stamp.items() if set(per_frame) <= topics]
    checks.check(
        "every frame's topics share one identical stamp",
        len(complete) == len(clock),
        f"{len(complete)} complete frames of {len(clock)}",
    )

    # --- 3. the transform and the odometry are the same car ----------------------------
    worst_gap = 0.0
    for (_, o), (_, t) in zip(odom, tf, strict=False):
        transform = t.transforms[0].transform
        worst_gap = max(
            worst_gap,
            abs(transform.translation.x - o.pose.pose.position.x),
            abs(transform.translation.y - o.pose.pose.position.y),
            abs(_wrap(_yaw(transform.rotation) - _yaw(o.pose.pose.orientation))),
        )
    checks.check("/tf and /localization/odometry agree", worst_gap < 1e-9, f"worst {worst_gap:.2e}")

    # --- 4. the car faces the way it is going -------------------------------------------
    # The sign check that needs no staged turn: over any frame where the car actually moved,
    # the heading it reports must point along the direction it moved. A flipped yaw, a
    # left-for-right swap or a degrees-for-radians slip all break this and nothing else.
    worst_heading = 0.0
    compared = 0
    for (_, a), (_, b) in zip(odom, odom[1:], strict=False):
        dx = b.pose.pose.position.x - a.pose.pose.position.x
        dy = b.pose.pose.position.y - a.pose.pose.position.y
        if math.hypot(dx, dy) < FORWARD_MIN_M:
            continue
        compared += 1
        drift = _wrap(math.atan2(dy, dx) - _yaw(a.pose.pose.orientation))
        worst_heading = max(worst_heading, abs(drift))
    checks.check(
        "the heading points along the direction of travel",
        compared > 0 and worst_heading < math.radians(25),
        f"worst {math.degrees(worst_heading):.1f} deg over {compared} moving frames",
    )

    # --- 5. the twist is the car's own, not the world's ---------------------------------
    # Rotating the body-frame twist back out by the heading must reproduce the world-frame
    # motion. Publishing the world vector in a child-frame field passes only while heading ~ 0.
    worst_twist = 0.0
    for (ta, a), (tb, b) in zip(odom, odom[1:], strict=False):
        dt = (tb - ta) / 1e9
        if dt <= 0:
            continue
        dx = (b.pose.pose.position.x - a.pose.pose.position.x) / dt
        dy = (b.pose.pose.position.y - a.pose.pose.position.y) / dt
        if math.hypot(dx, dy) < 0.5:
            continue
        yaw = _yaw(a.pose.pose.orientation)
        forward, left = a.twist.twist.linear.x, a.twist.twist.linear.y
        east = forward * math.cos(yaw) - left * math.sin(yaw)
        north = forward * math.sin(yaw) + left * math.cos(yaw)
        worst_twist = max(worst_twist, math.hypot(east - dx, north - dy))
    checks.check(
        "the twist is in the car's frame and matches its motion",
        worst_twist < 2.0,
        f"worst {worst_twist:.2f} m/s",
    )

    # --- 6. the labels are the scene, not the track list --------------------------------
    objects = by_topic.get(ros_schema.OBJECTS, [])
    seen = {d.id for _, m in objects for d in m.detections}
    kinds = {d.results[0].hypothesis.class_id for _, m in objects for d in m.detections}
    per_frame_counts = [len(m.detections) for _, m in objects]
    checks.check(
        "objects were labelled at all",
        bool(seen),
        f"{len(seen)} distinct ids, kinds {sorted(kinds)}",
    )
    checks.check(
        "every box has a real size",
        all(
            d.bbox.size.x > 0 and d.bbox.size.y > 0 and d.bbox.size.z > 0
            for _, m in objects
            for d in m.detections
        ),
    )
    if per_frame_counts:
        print(
            f"        objects per frame: min {min(per_frame_counts)} "
            f"max {max(per_frame_counts)} last {per_frame_counts[-1]}",
            file=out,
        )

    # --- 6b. the traffic lights, which until 2026-09-02 had never carried a message ------
    #
    # Not extent checks: a light's position comes off the live engine in the same frame as
    # everything else, so there is no separate shift for it to miss. What *can* go wrong is
    # quieter. `ros_frame.lights_of` reads `.id`, `.status` and `.position` with `getattr`
    # defaults, so a MetaDrive rename yields empty strings and zero positions and nothing raises;
    # and `ScenarioLightManager.after_reset` drops any light whose lane is missing from the road
    # network, warns, and carries on, because `skip_missing_light` defaults True.
    lights = by_topic.get(ros_schema.TRAFFIC_LIGHTS, [])
    if lights:
        per_frame = [len(m.lights) for _, m in lights]
        phases = defaultdict(list)
        for _, message in lights:
            for light in message.lights:
                phases[light.id].append(light.status)
        checks.check(
            "no traffic light disappears part-way through",
            len(set(per_frame)) == 1,
            f"{per_frame[0]} lights on every one of {len(lights)} frames"
            if len(set(per_frame)) == 1
            else f"count varies: {sorted(set(per_frame))}",
        )
        named = [
            light_id
            for light_id, states in phases.items()
            if light_id and all(state for state in states)
        ]
        checks.check(
            "every light has an id and a colour",
            len(named) == len(phases),
            f"{len(named)} of {len(phases)} - an empty one means a getattr default fired",
        )
        # A light frozen on one colour for a whole recording is what a tape that never advanced
        # looks like, and it is indistinguishable from a working light in every other check.
        frozen = sorted(light_id for light_id, states in phases.items() if len(set(states)) < 2)
        checks.check(
            "every light changes colour at least once",
            not frozen,
            f"{len(phases) - len(frozen)} of {len(phases)} change"
            + (f"; frozen: {', '.join(f[:8] for f in frozen)}" if frozen else ""),
        )
        # Printed, not checked: which lights share a phase is a property of the signal plan, and
        # whether two conflicting approaches are ever green together is the question the rviz2
        # view exists to answer. Asserting it here would need the conflict matrix, which the bag
        # does not carry.
        for light_id, states in sorted(phases.items()):
            runs = [(state, len(list(group))) for state, group in groupby(states)]
            shown = " -> ".join(f"{s.replace('TRAFFIC_LIGHT_', '')}x{n}" for s, n in runs)
            print(f"        light {light_id[:8]}  {shown}", file=out)
    else:
        print("        no traffic lights in this bag", file=out)

    # --- 7. GNSS is the same car, in the real world -------------------------------------
    fixes = by_topic.get(ros_schema.GNSS_FIX, [])
    if fixes:
        # The two the vehicle publishes, against each other. `/sensing/gnss/pose` is the
        # driver's own republication of the fix and `/sensing/gnss/imu/nav_sat_fix` the
        # IMU-framed one; both are built here from one position, so they must be the same float.
        #
        # **This is the check that was missing.** `/sensing/gnss/pose` carried a
        # `geometry_msgs/PoseStamped` from stage 10 until 2026-09-03 - our x, y, z and a
        # quaternion, on a topic the vehicle publishes a latitude and longitude on. Nothing
        # raised, because CDR carries no field names and a subscriber decodes whatever arrives.
        # A relationship check between two channels built from one truth is the only shape of
        # test that catches it, which is why every check in this file is one.
        poses = (
            {(m.header.stamp.sec, m.header.stamp.nanosec): m
             for _, m in by_topic.get(ros_schema.GNSS_POSE, [])}
            if ros_schema.GNSS_POSE not in stale
            else {}
        )
        if poses:
            worst_pose = max(
                (_metres(fix.latitude, fix.longitude, poses[key].latitude, poses[key].longitude)
                 for _, fix in fixes
                 if (key := (fix.header.stamp.sec, fix.header.stamp.nanosec)) in poses),
                default=None,
            )
            checks.check(
                "gnss/pose and nav_sat_fix are one position, and both are a fix",
                worst_pose == 0.0,
                f"{len(poses)} poses paired against {len(fixes)} fixes, "
                f"worst {worst_pose:.3e} m" if worst_pose is not None
                else "no pose messages sharing a stamp with a fix",
            )
        latitudes = [m.latitude for _, m in fixes]
        longitudes = [m.longitude for _, m in fixes]
        checks.check(
            "the fix moves with the car rather than sitting still",
            max(latitudes) != min(latitudes) or max(longitudes) != min(longitudes),
            f"lat {min(latitudes):.6f}..{max(latitudes):.6f}, "
            f"lon {min(longitudes):.6f}..{max(longitudes):.6f}",
        )
        if workspace:
            box = _osm_bounds(Path(workspace))
            if box:
                south, west, north, east = box
                # Padded, and the size of the pad is the whole argument. This check exists to
                # catch a skipped `old_origin_in_current_coordinate` - 93.8 m on junction-1 -
                # not to police the bbox. A lane legitimately overhangs the extent by a few
                # metres, because osmnx clips ways while lane geometry is offset outward from
                # the centreline; measured on junction-1 the ego reaches 10 m past minlat.
                # So the pad is wide enough to allow the overhang and far under the error.
                pad = EXTENT_PAD_M / 111_320.0
                inside = sum(
                    1
                    for lat, lon in zip(latitudes, longitudes, strict=False)
                    if south - pad <= lat <= north + pad and west - pad <= lon <= east + pad
                )
                checks.check(
                    f"every fix lands on the map (within {EXTENT_PAD_M:.0f} m of its OSM extent)",
                    inside == len(latitudes),
                    f"{inside} of {len(latitudes)} inside {south:.4f}..{north:.4f} N, "
                    f"{west:.4f}..{east:.4f} E",
                )
    else:
        print("        no GNSS in this bag (the dataset carried no projection)", file=out)

    # --- 8. the cameras: a lens on one topic and a mount on another, checked against each
    # other rather than against the spec they were both built from ------------------------
    infos = {
        topic: by_topic[topic][-1][1]
        for topic in ros_schema.CAMERA_INFO_TOPICS
        if by_topic.get(topic)
    }
    if infos:
        transforms = {
            t.child_frame_id: t
            for _, message in by_topic.get(ros_schema.TF_STATIC, [])
            for t in message.transforms
        }
        checks.check(
            "every camera_info is latched - exactly one message, not one per frame",
            all(len(by_topic[topic]) == 1 for topic in infos),
            ", ".join(f"{t.split('/')[-2]}={len(by_topic[t])}" for t in sorted(infos)),
        )
        # The join. `camera_info` names a frame and `tf_static` defines one, and the two were
        # built from opposite ends of `camera_rig.Camera` - so a camera present in one and
        # absent from the other is a rig that was only half converted. Nothing else notices:
        # both topics deserialise perfectly on their own.
        orphans = sorted(
            m.header.frame_id for m in infos.values() if m.header.frame_id not in transforms
        )
        checks.check(
            "every camera_info's frame has a transform in tf_static",
            not orphans,
            f"{len(infos)} cameras, {len(transforms)} static transforms"
            + (f"; no transform for {', '.join(orphans)}" if orphans else ""),
        )
        # The intrinsics, checked as a relationship rather than against a remembered focal
        # length: a square-pixel pinhole has fx == fy, its principal point at the centre of the
        # image, and `p` equal to `k` with a zero translation column. Get the FOV-to-focal
        # conversion wrong and every one of those still holds - so the horizontal angle each
        # `k` implies is printed, to be read against the spec's own `fov`.
        bad = []
        for topic, message in sorted(infos.items()):
            fx, fy = message.k[0], message.k[4]
            cx, cy = message.k[2], message.k[5]
            square = abs(fx - fy) < 1e-6
            centred = (
                abs(cx - message.width / 2.0) < 1e-6 and abs(cy - message.height / 2.0) < 1e-6
            )
            projected = (
                abs(message.p[0] - fx) < 1e-6
                and abs(message.p[2] - cx) < 1e-6
                and abs(message.p[3]) < 1e-12
                and abs(message.p[7]) < 1e-12
            )
            if not (square and centred and projected):
                bad.append(topic.split("/")[-2])
            fov = 2.0 * math.degrees(math.atan((message.width / 2.0) / fx)) if fx else 0.0
            vertical = 2.0 * math.degrees(math.atan((message.height / 2.0) / fx)) if fx else 0.0
            print(
                f"        {topic.split('/')[-2]:<14} {message.width}x{message.height}  "
                f"f={fx:.1f}px  fov {fov:.1f} deg h / {vertical:.1f} deg v  "
                f"frame {message.header.frame_id}",
                file=out,
            )
        checks.check(
            "each K is a square-pixel pinhole centred on its image, and P agrees with it",
            not bad,
            "fx==fy, principal point at the centre, P = K with a zero translation column"
            + (f"; wrong on {', '.join(bad)}" if bad else ""),
        )
        # Printed and deliberately **not** checked. Which way a camera faces is the spec file's
        # business, and `rigs/cams.txt` contradicts itself about it - `+yaw` is right on its
        # front pair and left on its back pair, so two of its four side cameras are named
        # backwards under either reading (`camera_rig.Camera.aim` has the measurement). Failing
        # a bag for faithfully carrying that would be blaming the writer for the input; hiding
        # it would be worse. `rigs/av3.txt`, generated with both columns agreeing, prints none.
        mounts = {}
        for topic, message in infos.items():
            found = transforms.get(message.header.frame_id)
            if found is not None:
                yaw = _yaw(found.transform.rotation)
                mounts[topic.split("/")[-2]] = (message.header.frame_id, yaw)
        for name, claimed, aimed in ros_schema.camera_side_disagreements(mounts):
            print(
                f"        NAME/AIM  {mounts[name][0]} publishes as {name}, which claims "
                f"{claimed}, and is mounted aiming {aimed} - the spec's labels disagree with "
                "its yaw column. tf_static carries the geometry.",
                file=out,
            )
    else:
        print("        no cameras in this bag (the drive mounted no --camera-rig)", file=out)

    # --- 9. the SBG family: nine channels, one true pose --------------------------------
    #
    # Every one of these is derived from the position and velocity two other topics already
    # carry, so the only way they can be wrong is a conversion - a swapped north and east, a
    # bearing measured from the wrong axis, a second path to latitude that drifted from the
    # first. Not one of those raises anything, and each produces a bag that plots a car on a
    # road. So every check here is one channel against another rather than against a constant.
    navs = by_topic.get(ros_schema.SBG_EKF_NAV, [])
    if navs:
        print(f"        sbg_driver {ros_schema.SBG_DRIVER_VERSION} definitions, "
              f"from tools/sbg_msgs/", file=out)
        fixes_by_stamp = {
            (m.header.stamp.sec, m.header.stamp.nanosec): m
            for _, m in by_topic.get(ros_schema.GNSS_FIX, [])
        }
        # One position, reached twice. `ekf_nav` is the filter's answer and `nav_sat_fix` the
        # receiver's, and on the rig they differ by a lever arm; here there is neither a filter
        # nor an antenna, so they must be the same float. Anything else means a second
        # conversion path appeared, and a second path is how 93.8 m of origin shift goes missing.
        worst_fix = 0.0
        paired = 0
        for _, nav in navs:
            fix = fixes_by_stamp.get((nav.header.stamp.sec, nav.header.stamp.nanosec))
            if fix is None:
                continue
            paired += 1
            worst_fix = max(worst_fix, _metres(nav.latitude, nav.longitude, fix.latitude,
                                               fix.longitude))
        checks.check(
            "ekf_nav and nav_sat_fix are the same position on every frame",
            paired == len(navs) and worst_fix == 0.0,
            f"{paired} of {len(navs)} paired by stamp, worst {worst_fix:.3e} m",
        )
        # The raw solution against the fused one. Same argument, third channel.
        poss = {(m.header.stamp.sec, m.header.stamp.nanosec): m
                for _, m in by_topic.get(ros_schema.SBG_GPS_POS, [])}
        worst_pos = max(
            (_metres(nav.latitude, nav.longitude, poss[key].latitude, poss[key].longitude)
             for _, nav in navs
             if (key := (nav.header.stamp.sec, nav.header.stamp.nanosec)) in poss),
            default=None,
        )
        checks.check(
            "gps_pos carries the same position as ekf_nav",
            worst_pos == 0.0,
            f"{len(poss)} raw fixes, worst {worst_pos:.3e} m" if worst_pos is not None
            else "no gps_pos messages to compare",
        )
        # ECEF, converted back. This is the one channel that leaves our own frame entirely, so
        # it is checked by round trip rather than by equality: the metres it holds must be the
        # metres the fix's own latitude and longitude produce.
        ecefs = {(m.header.stamp.sec, m.header.stamp.nanosec): m
                 for _, m in by_topic.get(ros_schema.GNSS_POS_ECEF, [])}
        worst_ecef = 0.0
        for _, nav in navs:
            found = ecefs.get((nav.header.stamp.sec, nav.header.stamp.nanosec))
            if found is None:
                continue
            x, y, z = geodesy.geodetic_to_ecef(nav.latitude, nav.longitude, nav.altitude)
            worst_ecef = max(worst_ecef, math.dist((x, y, z),
                                                   (found.point.x, found.point.y, found.point.z)))
        checks.check(
            "pos_ecef is where ekf_nav's own latitude and longitude put it",
            len(ecefs) == len(navs) and worst_ecef < 1e-3,
            f"{len(ecefs)} of {len(navs)}, worst {worst_ecef:.3e} m, frame "
            + (next(iter(ecefs.values())).header.frame_id if ecefs else "-"),
        )
        # Orientation, told three ways. `ekf_quat` and `/sensing/gnss/imu/data` come from one
        # call so they must be bit-identical; `ekf_euler` is the same rotation as three angles,
        # and reading its yaw as a bearing from north rather than from east is a 90 degree error
        # that leaves the car pointing plausibly down a different road.
        quats = {(m.header.stamp.sec, m.header.stamp.nanosec): m
                 for _, m in by_topic.get(ros_schema.SBG_EKF_QUAT, [])}
        eulers = {(m.header.stamp.sec, m.header.stamp.nanosec): m
                  for _, m in by_topic.get(ros_schema.SBG_EKF_EULER, [])}
        imus = {(m.header.stamp.sec, m.header.stamp.nanosec): m
                for _, m in by_topic.get(ros_schema.GNSS_IMU, [])}
        worst_yaw, worst_orientation = 0.0, 0.0
        for key, quat in quats.items():
            if key in eulers:
                worst_yaw = max(worst_yaw, abs(_wrap(_yaw(quat.quaternion) - eulers[key].angle.z)))
            if key in imus:
                worst_orientation = max(worst_orientation, max(
                    abs(getattr(quat.quaternion, axis) - getattr(imus[key].orientation, axis))
                    for axis in ("x", "y", "z", "w")
                ))
        checks.check(
            "ekf_quat, ekf_euler and imu/data all describe one rotation",
            len(quats) == len(navs) and worst_yaw < 1e-9 and worst_orientation == 0.0,
            f"worst yaw {math.degrees(worst_yaw):.2e} deg, "
            f"worst quaternion component {worst_orientation:.2e}",
        )
        # Course against the velocity in the same message. ENU: zero pointing east and counting
        # counter-clockwise, the opposite sign to the NED bearing the same field carries under
        # the driver's other setting. `course` is a float32, hence the tolerance.
        vels = by_topic.get(ros_schema.SBG_GPS_VEL, [])
        worst_course = 0.0
        moving = 0
        for _, vel in vels:
            if math.hypot(vel.velocity.x, vel.velocity.y) < FORWARD_MIN_M:
                continue
            moving += 1
            expected = math.degrees(math.atan2(vel.velocity.y, vel.velocity.x)) % 360.0
            worst_course = max(worst_course, abs(_wrap(math.radians(vel.course - expected))))
        checks.check(
            "gps_vel's course is the direction its own velocity points, measured from east",
            moving and worst_course < 1e-3,
            f"{moving} of {len(vels)} moving, worst {math.degrees(worst_course):.2e} deg",
        )
        # The absences, pinned so that a later hand cannot quietly replace them with a plausible
        # zero. A temperature of 0.0 reads as a sensor at freezing; an acceleration of 0.0 reads
        # as a car in free fall with no gravity. `/sensing/gnss/imu/temp` is excluded from the
        # 45 for exactly the reason `SbgImuData.temp` is NaN, and the two have to keep agreeing.
        raws = [m for _, m in by_topic.get(ros_schema.SBG_IMU_DATA, [])]
        absent = [
            (name, value)
            for m in raws[:1]
            for name, value in (("temp", m.temp), ("accel.x", m.accel.x),
                                ("delta_vel.x", m.delta_vel.x),
                                ("delta_angle.x", m.delta_angle.x))
        ] + [("ekf_nav.undulation", navs[0][1].undulation)]
        checks.check(
            "what a simulator does not have is NaN, never a plausible zero",
            all(math.isnan(value) for _, value in absent),
            ", ".join(f"{name}={'nan' if math.isnan(v) else v}" for name, v in absent),
        )
        # The gyro is the one quantity `SbgImuData` does produce, and it is the same number
        # `/sensing/gnss/imu/data` publishes as its angular velocity.
        raw_by_stamp = {(m.header.stamp.sec, m.header.stamp.nanosec): m for m in raws}
        worst_gyro = max(
            (abs(raw.gyro.z - imus[key].angular_velocity.z)
             for key, raw in raw_by_stamp.items() if key in imus),
            default=None,
        )
        checks.check(
            "imu_data's gyro is imu/data's angular velocity - one character apart, one number",
            worst_gyro == 0.0,
            f"{len(raws)} raw messages, worst {worst_gyro:.2e} rad/s"
            if worst_gyro is not None else "nothing to compare",
        )
        # The two clocks. `utc_ref` exists to relate the simulator's clock to a UTC, and the
        # offset between them must be the epoch the drive declares rather than zero - zero says
        # "our clock is UTC", which is the one thing a simulated drive cannot claim.
        refs = [m for _, m in by_topic.get(ros_schema.GNSS_UTC_REF, [])]
        offsets = {m.time_ref.sec - m.header.stamp.sec for m in refs}
        utcs = [m for _, m in by_topic.get(ros_schema.SBG_UTC_TIME, [])]
        declared = {(m.year, m.month, m.day) for m in utcs}
        checks.check(
            "utc_ref offsets the sim clock by the declared epoch, and utc_time agrees with it",
            offsets == {ros_schema.GPS_EPOCH_UNIX_S}
            and declared == {(1980, 1, 6)}
            and all(m.clock_status.clock_utc_status == 0 for m in utcs),
            f"offset {sorted(offsets)} s, date {sorted(declared)}, "
            "clock_utc_status 0 (the UTC time is not known)",
        )
    else:
        print("        no SBG channels in this bag (the dataset carried no projection)", file=out)

    # --- 10. the point cloud: the one payload here that is a shape rather than a number ----
    clouds = [m for _, m in by_topic.get(ros_schema.LIDAR_POINTS, [])]
    if clouds:
        stated = ros_audit.notes(path).get("lidar") or {}
        half_fov = math.radians(float(stated.get("fov_deg", 65.0))) / 2.0
        max_range = float(stated.get("max_range_m", 200.0))
        print(
            f"        lidar: {len(clouds)} sweeps, {clouds[0].height} beams x "
            f"{clouds[0].width} rays, {math.degrees(half_fov) * 2:g} deg cone, "
            f"{max_range:g} m range, frame {clouds[0].header.frame_id}",
            file=out,
        )

        def _xyz(message):
            """One sweep as `(height, width, 3)` metres, straight off the wire."""
            return numpy.frombuffer(message.data.tobytes(), dtype="<f4").reshape(
                message.height, message.width, 3
            )

        # The cloud is *organised*, and that is a claim about the bytes: height rows of width
        # points, three float32 each. A reader that trusts `height`/`width` and finds a payload
        # that is not exactly that reads garbage without an error anywhere.
        shapes = {
            (
                m.height * m.width * m.point_step == len(m.data),
                m.point_step,
                m.row_step == m.point_step * m.width,
                tuple((f.name, f.offset, f.datatype, f.count) for f in m.fields),
                m.is_bigendian,
            )
            for m in clouds
        }
        checks.check(
            "the cloud is organised, and the payload is the size the header claims",
            shapes
            == {
                (
                    True,
                    ros_schema.POINT_STEP,
                    True,
                    (("x", 0, 7, 1), ("y", 4, 7, 1), ("z", 8, 7, 1)),
                    False,
                )
            },
            f"{clouds[0].height}x{clouds[0].width} points, {clouds[0].point_step} bytes each, "
            f"x/y/z float32 little-endian, {len(clouds[0].data)} bytes a sweep",
        )

        # **The check the frame choice exists for.** A forward-facing sensor cannot return a
        # point behind itself, so in its own frame every point sits within half the FOV of +x.
        # De-rotating by the wrong sign leaves a cloud that is still a plausible road seen from
        # a plausible car - same points, rigidly rotated - and this is the only thing in the bag
        # that says so: measured 100.00% inside at 32.41 deg against a 32.5 deg half-angle with
        # the sign right, and 0.00% with it flipped.
        worst_bearing, ahead, counted = 0.0, 0, 0
        for message in clouds:
            points = _xyz(message)
            finite = numpy.isfinite(points).all(axis=-1)
            x, y = points[..., 0][finite], points[..., 1][finite]
            counted += int(x.size)
            ahead += int((x > 0).sum())
            if x.size:
                worst_bearing = max(worst_bearing, float(numpy.abs(numpy.arctan2(y, x)).max()))
        checks.check(
            "every point the lidar returns is in front of it, inside its own field of view",
            counted > 0
            and ahead == counted
            and worst_bearing <= half_fov + math.radians(0.5),
            f"{ahead} of {counted} points ahead, worst bearing "
            f"{math.degrees(worst_bearing):.2f} deg against a "
            f"{math.degrees(half_fov):.2f} deg half-angle",
        )

        # A miss keeps its slot and is NaN - that is what `is_dense: false` means, and it is
        # why the sweep stays organised. Two ways to get this wrong and neither raises: writing
        # the far-plane point as if it were a return (a wall of scenery 18 km away), or writing
        # zeros (a dense ball of points at the sensor's own origin).
        worst_reach, missed, half_nan = 0.0, 0, 0
        for message in clouds:
            points = _xyz(message)
            nans = numpy.isnan(points).sum(axis=-1)
            missed += int((nans == 3).sum())
            half_nan += int(((nans > 0) & (nans < 3)).sum())
            finite = numpy.isfinite(points).all(axis=-1)
            if finite.any():
                reach = numpy.sqrt((points[finite].astype(numpy.float64) ** 2).sum(axis=-1))
                worst_reach = max(worst_reach, float(reach.max()))
        checks.check(
            "a ray that hit nothing keeps its slot and is NaN, and no return is beyond range",
            missed > 0
            and half_nan == 0
            and worst_reach <= max_range
            and all(m.is_dense is False for m in clouds),
            f"{missed} misses of {counted + missed}, {half_nan} part-NaN points, "
            f"furthest return {worst_reach:.1f} m of {max_range:g} m allowed",
        )

        # **A blind cloud is well-formed.** Past the image-buffer ceiling
        # (`camera_rig.MAX_BUFFERS_WITH_POINT_CLOUD`) the depth buffer comes back at its far
        # plane, so every ray is a point 18 km out, the range gate turns all of them into NaN,
        # and what reaches the bag is 364 correctly-shaped sweeps of nothing. Every other check
        # here passes on that bag - the shape is right, the misses are NaN, the sweeps all
        # differ. Only the share of rays that hit says which run it was: measured on junction-1,
        # 48.5-57.7% with the cloud inside the ceiling and 0.10-0.27% past it. The floor is set
        # between the two rather than near either, because it is separating a working sensor
        # from a dead one, not measuring how much scenery a junction has.
        share = 100.0 * counted / max(1, counted + missed)
        checks.check(
            "the sweep has returns in it rather than being a buffer of sky",
            share >= 5.0,
            f"{share:.2f}% of rays hit something within {max_range:g} m "
            f"(a working sensor measures 48.5-57.7% here, a blind one 0.1-0.3%)",
        )

        # A mounted camera is read out of the buffer the frame pass already filled. Read it the
        # wrong way - or never re-read it - and every sweep in the bag is the same sweep, which
        # is a perfectly well-formed cloud that says the world stood still.
        digests = {message.data.tobytes() for message in clouds}
        checks.check(
            "each sweep is read afresh rather than one buffer republished",
            len(digests) == len(clouds),
            f"{len(digests)} distinct sweeps in {len(clouds)} messages",
        )

        # And the cloud belongs to the same drive the GNSS does. Put each sweep back into the
        # world with the pose the bag itself carries for that frame, and it has to land on the
        # map the drive was generated from - the containment check the fixes already get,
        # applied to the one channel that reaches 200 m off the car.
        mount = next(
            (
                t.transform.translation
                for _, msg in by_topic.get(ros_schema.TF_STATIC, [])
                for t in msg.transforms
                if t.child_frame_id == ros_schema.LIDAR_FRAME
            ),
            None,
        )
        box = _osm_bounds(Path(workspace)) if workspace else None
        if box and mount is not None and fixes:
            south, west, north, east = box
            # The sensor's own range is part of the pad, and has to be: a car driving legally
            # near the edge of the extract sees 200 m past it, so those returns are outside the
            # OSM bounding box and are not wrong. What is left for this to catch is a cloud that
            # is not where the car is at all - which is the whole of what it is for, the ego's
            # own containment being checked against a tighter bound a few checks above.
            pad = (EXTENT_PAD_M + max_range) / 111_320.0
            poses = {(o.header.stamp.sec, o.header.stamp.nanosec): o for _, o in odom}
            located = {(f.header.stamp.sec, f.header.stamp.nanosec): f for _, f in fixes}
            outside, total = 0, 0
            for message in clouds:
                key = (message.header.stamp.sec, message.header.stamp.nanosec)
                if key not in poses or key not in located:
                    continue
                pose, fix = poses[key].pose.pose, located[key]
                yaw = _yaw(pose.orientation)
                points = _xyz(message).reshape(-1, 3).astype(numpy.float64)
                points = points[numpy.isfinite(points).all(axis=-1)]
                # Sensor frame -> base_link (the mount) -> map (the ego's own pose).
                east_m = points[:, 0] + mount.x
                north_m = points[:, 1] + mount.y
                turned_e = east_m * math.cos(yaw) - north_m * math.sin(yaw)
                turned_n = east_m * math.sin(yaw) + north_m * math.cos(yaw)
                # Metres to degrees about the ego's own fix. Flat-earth over 200 m at this
                # latitude is sub-metre, and the extent is padded by far more than that.
                scale = math.cos(math.radians(fix.latitude))
                latitudes = fix.latitude + turned_n / 111_320.0
                longitudes = fix.longitude + turned_e / (111_320.0 * scale)
                total += int(latitudes.size)
                outside += int(
                    (
                        (latitudes < south - pad)
                        | (latitudes > north + pad)
                        | (longitudes < west - pad)
                        | (longitudes > east + pad)
                    ).sum()
                )
            checks.check(
                "the cloud lands on the map (within its own range of the OSM extent, "
                f"{EXTENT_PAD_M + max_range:.0f} m)",
                total > 0 and outside == 0,
                f"{total - outside} of {total} returns inside {south:.4f}..{north:.4f} N, "
                f"{west:.4f}..{east:.4f} E",
            )
        # The vehicle's own version of the same sweep, and the only pair of channels in this
        # bag carrying one quantity in two encodings. `soa+zstd` is a byte-plane transpose the
        # vehicle documents nowhere, worked out by decoding one of its messages - so the check
        # that matters is that ours decodes back to exactly the cloud beside it. A transpose
        # applied the wrong way round still decompresses, still has the right length, and still
        # parses as floats; what it does not do is come back equal.
        packed = by_topic.get(ros_schema.LIDAR_POINTS_COMPRESSED, [])
        if packed:
            import zstandard

            plain_by_stamp = {(m.header.stamp.sec, m.header.stamp.nanosec): m for m in clouds}
            wrong, checked = [], 0
            for _, message in packed[:: max(1, len(packed) // 10)]:
                key = (message.header.stamp.sec, message.header.stamp.nanosec)
                twin = plain_by_stamp.get(key)
                if twin is None:
                    wrong.append("a compressed sweep has no uncompressed twin")
                    continue
                raw = zstandard.ZstdDecompressor().stream_reader(
                    bytes(message.compressed_data)
                ).read(1 << 30)
                count = message.height * message.width
                if len(raw) != count * message.point_step:
                    wrong.append(f"decompressed to {len(raw)} not {count * message.point_step}")
                    continue
                buffer = numpy.frombuffer(raw, dtype=numpy.uint8)
                offset, recovered = 0, {}
                for name, _datatype, field_width in ros_schema.COMPRESSED_FIELDS:
                    plane = buffer[offset : offset + field_width * count]
                    offset += field_width * count
                    recovered[name] = (
                        plane if field_width == 1
                        else plane.reshape(field_width, count).T.copy().view(
                            "<f4" if field_width == 4 else "<f8"
                        ).ravel()
                    )
                original = numpy.asarray(twin.data).view("<f4").reshape(-1, 3)
                for index, axis in enumerate("xyz"):
                    a = numpy.nan_to_num(recovered[axis], nan=-9e9)
                    b = numpy.nan_to_num(original[:, index], nan=-9e9)
                    if not numpy.array_equal(a, b):
                        wrong.append(f"{axis} differs from the uncompressed sweep")
                checked += 1
            checks.check(
                "the compressed sweep de-shuffles back to exactly the sweep beside it",
                checked > 0 and not wrong,
                "; ".join(sorted(set(wrong)))
                or f"{checked} sweeps sampled, all identical after zstd + byte-plane transpose",
            )
            first = packed[0][1]
            layout = [(f.name, f.offset, f.datatype) for f in first.fields]
            expected, running = [], 0
            for name, datatype, field_width in ros_schema.COMPRESSED_FIELDS:
                expected.append((name, running, datatype))
                running += field_width
            checks.check(
                "the compressed cloud has the vehicle's own layout, field for field",
                layout == expected
                and first.point_step == ros_schema.COMPRESSED_POINT_STEP
                and first.format == ros_schema.COMPRESSED_FORMAT
                and first.header.frame_id == ros_schema.COMPRESSED_LIDAR_FRAME,
                f"{first.format!r} in {first.header.frame_id}, {len(layout)} fields at "
                f"step {first.point_step}",
            )
    else:
        print("        no lidar in this bag (--ros-lidar was not asked for)", file=out)

    # --- 11. the camera packets: the only payload here that has to be decoded to be read ----
    #
    # Every other check in this file reads a number off the wire and compares it with another
    # number. These cannot: an H.264 packet is opaque, and a bag full of well-formed packets
    # carrying the wrong pixels opens, plays and renders. So this section actually runs the
    # decoder, which is the only thing that can tell the difference.
    streams = {
        topic: [m for _, m in by_topic.get(topic, [])]
        for topic in ros_schema.CAMERA_PACKET_TOPICS
        if by_topic.get(topic)
    }
    if streams:
        stated = ros_audit.notes(path).get("cameras") or {}
        first = next(iter(streams.values()))
        print(
            f"        cameras: {len(streams)} streams, {len(first)} packets each, "
            f"{first[0].width}x{first[0].height}, {first[0].encoding}"
            + (f" crf {stated['crf']}" if "crf" in stated else ""),
            file=out,
        )

        # Every stream is the same length, and it is the length of the cloud's - the other
        # `sensor`-family channel. A camera that stopped being read half way through leaves a
        # bag that plays perfectly and is missing the second half of one view.
        lengths = {topic: len(messages) for topic, messages in streams.items()}
        checks.check(
            "every camera stream holds the same number of packets",
            len(set(lengths.values())) == 1,
            ", ".join(f"{t.split('/')[-3]}={n}" for t, n in sorted(lengths.items())),
        )

        # The packet's own header against the lens on the latched topic beside it. Two topics
        # produced from the same `CameraSpec` by two different code paths - one at
        # `start_episode`, one per frame through the encoder - so a mismatch means one of them
        # is describing a camera that is not the one being drawn.
        mismatched = []
        for topic, messages in streams.items():
            info_topic = topic.replace("image_raw/ffmpeg", "camera_info_latched")
            info = [m for _, m in by_topic.get(info_topic, [])]
            if not info:
                mismatched.append(f"{topic.split('/')[-3]}: no camera_info")
                continue
            wrong = [
                m
                for m in messages
                if (m.width, m.height) != (info[0].width, info[0].height)
                or m.header.frame_id != info[0].header.frame_id
            ]
            if wrong:
                mismatched.append(
                    f"{topic.split('/')[-3]}: {len(wrong)} packets disagree with camera_info"
                )
        checks.check(
            "each packet says the same size and frame as its own camera_info",
            not mismatched,
            "; ".join(mismatched) or f"{len(streams)} streams agree",
        )

        # A reader joining mid-bag decodes nothing until the next keyframe, so where they are is
        # a property of the bag rather than of the encoder's mood. The first packet has to be
        # one, or the stream cannot be decoded from its own beginning.
        gop = round(
            float(stated.get("keyframe_seconds", 1.0)) * float(stated.get("rate_hz", 10.0))
        )
        bad_keys = []
        for topic, messages in streams.items():
            name = topic.split("/")[-3]
            keys = [i for i, m in enumerate(messages) if m.flags & ros_schema.PACKET_FLAG_KEY]
            widest = max(
                (b - a for a, b in zip(keys, keys[1:], strict=False)), default=len(messages)
            )
            if not keys or keys[0] != 0:
                bad_keys.append(f"{name}: first keyframe at {keys[:1] or 'never'}")
            elif gop > 0 and len(messages) > gop and widest > gop:
                bad_keys.append(f"{name}: {widest} frames between keyframes")
        checks.check(
            f"every stream opens on a keyframe and keeps one at least every {gop} frames",
            not bad_keys,
            "; ".join(bad_keys) or f"{len(streams)} streams, keyframe every {gop} frames",
        )

        # **The rate, against the rate the bag says it wrote at.** This is the structural half
        # of the held-frame question: `frame_gate` re-uses the last drawn picture on a step
        # between two decisions, so a stream written every step carries one re-encode of a held
        # buffer in every gap. Those re-encodes are not detectable in the bytes - inter-frame
        # coding makes each one tiny and perfectly valid - but they are impossible to hide from
        # a clock, because there are `stride` times too many of them.
        declared_hz = float(stated.get("rate_hz", 0.0) or 0.0)
        gaps = sorted(
            b - a
            for topic in streams
            for a, b in zip(
                [t for t, _ in by_topic[topic]], [t for t, _ in by_topic[topic]][1:], strict=False
            )
        )
        measured_hz = 1e9 / gaps[len(gaps) // 2] if gaps else 0.0
        checks.check(
            "the packets arrive at the decision rate the bag declares, not the step rate",
            declared_hz > 0 and abs(measured_hz - declared_hz) <= 0.01 * declared_hz,
            f"{measured_hz:.2f} Hz measured against {declared_hz:.2f} Hz declared",
        )

        # And then the one that needs the decoder. Everything above is a header check, and a
        # header check cannot see a picture that is blank, stale, or the wrong camera's.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import ros_encode
        except ImportError:  # pragma: no cover - `av` is in the same group as `rosbags`
            ros_encode = None
        if ros_encode is None:
            print(
                "        (no `av` here, so the pictures were not decoded - "
                "uv sync --group sim --group ros)",
                file=out,
            )
        else:
            undecodable, stuck, blank = [], [], []
            for topic, messages in streams.items():
                name = topic.split("/")[-3]
                frames = ros_encode.decode(
                    [m.data.tobytes() for m in messages], messages[0].width, messages[0].height
                )
                if len(frames) != len(messages) or any(
                    f.shape != (messages[0].height, messages[0].width, 3) for f in frames
                ):
                    undecodable.append(
                        f"{name}: {len(frames)} pictures out of {len(messages)} packets"
                    )
                    continue
                # The same question again, this time from the pictures. **Equality is the
                # wrong test and was measured to be**: re-encoding one identical source frame
                # ten times does *not* produce ten identical decoded frames, because a keyframe
                # and the P-frames after it quantise differently - measured 0 exact repeats and
                # 47.2 to 61.4 dB between consecutive pictures, against 24.8 dB for a stream
                # that was actually moving. So the test is the *median* gap, and it is the
                # median rather than the worst because a car stopped at a red really does draw
                # the same picture twice and that is not a fault.
                gaps = sorted(
                    ros_encode.psnr(a, b)
                    for a, b in zip(frames, frames[1:], strict=False)
                )
                middle = gaps[len(gaps) // 2] if gaps else 0.0
                if middle > 40.0:
                    stuck.append(f"{name}: median {middle:.1f} dB between consecutive pictures")
                # And a camera that rendered nothing encodes to a flat grey that is perfectly
                # well-formed. Standard deviation over the whole stream, not per frame: a real
                # drive past a wall is legitimately flat for a while.
                spread = float(numpy.std(numpy.asarray(frames[:: max(1, len(frames) // 20)])))
                if spread < 1.0:
                    blank.append(f"{name}: whole stream is flat, std {spread:.2f}")
            checks.check(
                "every packet decodes back to a picture of the declared size",
                not undecodable,
                "; ".join(undecodable)
                or f"{sum(len(m) for m in streams.values())} packets decoded across "
                f"{len(streams)} streams",
            )
            checks.check(
                "the pictures move - no stream is one held buffer re-encoded",
                not stuck,
                "; ".join(stuck)
                or f"{len(streams)} streams under the 40 dB ceiling (a held buffer "
                "measures 47-61 dB between consecutive pictures, a moving one 25)",
            )
            checks.check(
                "the cameras drew a scene rather than an empty buffer",
                not blank,
                "; ".join(blank) or f"{len(streams)} streams carry image content",
            )
    else:
        print("        no camera packets in this bag (--ros-camera was not asked for)", file=out)

    # --- 12. the car about itself: commanded against observed ----------------------------
    #
    # The three phase-5 topics are the only ones here built from what the drive *commanded*
    # rather than from what it observed, which is what makes them checkable: a command and its
    # consequence are two independently produced quantities, and every check below is one
    # against the other. The vehicle's own recording could not settle any of this - its car
    # never moves, `v_ego` max 0.00 m/s over all 63 minutes - so these run on our bags.
    states = by_topic.get(ros_schema.VEHICLE_STATE, [])
    if states:
        actuators = {(m.header.stamp.sec, m.header.stamp.nanosec): m
                     for _, m in by_topic.get(ros_schema.CONTROL_ACTUATORS, [])}
        print(f"        vehicle: {len(states)} states, {len(actuators)} actuator commands, "
              f"policy {by_topic[ros_schema.VEHICLE_ENGAGEMENT][0][1].alert_text1}", file=out)

        # One number, one path. `VehicleState.steering_angle_deg` and `ActuatorsOutput`'s are
        # both `Controls.steering_angle_deg`, so a difference means a second conversion appeared
        # - and a second conversion is where the 57.3 the type's own comment warns about lands.
        worst_angle = max(
            (abs(state.steering_angle_deg.value - actuators[key].steering_angle_deg)
             for _, state in states
             if (key := (state.header.stamp.sec, state.header.stamp.nanosec)) in actuators),
            default=None,
        )
        checks.check(
            "the wheel angle the car reports is the one it was commanded",
            worst_angle is not None and worst_angle < 1e-4,
            f"worst {worst_angle:.3e} deg over {len(actuators)} paired frames"
            if worst_angle is not None else "no actuator command shares a stamp with a state",
        )

        # `v_ego` against the twist this bag already publishes - the same speed reached by two
        # builders off one `Ego`.
        odoms = {(m.header.stamp.sec, m.header.stamp.nanosec): m for _, m in odom}
        worst_speed = max(
            (abs(state.v_ego.value - math.hypot(odoms[key].twist.twist.linear.x,
                                                odoms[key].twist.twist.linear.y))
             for _, state in states
             if (key := (state.header.stamp.sec, state.header.stamp.nanosec)) in odoms),
            default=None,
        )
        checks.check(
            "v_ego is the same speed the odometry twist carries",
            worst_speed is not None and worst_speed < 1e-6,
            f"worst {worst_speed:.3e} m/s" if worst_speed is not None else "no paired odometry",
        )

        # **The sign, and the only check here that could not be written from the type alone.**
        # Commanded steering against *measured* curvature: a left command that produces a right
        # curve is the fault that put a car smoothly into oncoming traffic on the openpilot
        # bridge, and nothing in a header can see it. Correlation rather than a per-frame
        # comparison because a real car's response lags its command; measured +0.90 on a
        # junction-1 IDM drive.
        turning = [(state.steering_angle_deg.value, actuators[key].curvature)
                   for _, state in states
                   if (key := (state.header.stamp.sec, state.header.stamp.nanosec)) in actuators
                   and abs(state.steering_angle_deg.value) > 1.0
                   and state.v_ego.value > 2.0]
        correlation = None
        if len(turning) > 30:
            angles = numpy.array([a for a, _ in turning])
            curves = numpy.array([c for _, c in turning])
            if angles.std() > 0 and curves.std() > 0:
                correlation = float(numpy.corrcoef(angles, curves)[0, 1])
        checks.check(
            "steering left curves the car left - the command agrees with the measurement",
            correlation is not None and correlation > 0.5,
            f"corr(steering_angle_deg, measured curvature) = {correlation:+.3f} "
            f"over {len(turning)} turning frames" if correlation is not None
            else f"only {len(turning)} frames were both turning and moving",
        )

        # The convention the type provides for "no data", and the reason this bag can be honest
        # about an EPS and a set of body sensors it does not have. Frame 0 is skipped: this
        # drive's clock starts at zero, so on that one frame a filled field is indistinguishable
        # from an unfilled one.
        unfilled = ("steering_torque", "steering_pressed", "door_open", "seatbelt_unlatched",
                    "blindspot_left", "blindspot_right", "cruise_speed")
        wrong = [
            name for name in unfilled
            for _, state in states[1:]
            if (getattr(state, name).stamp.sec, getattr(state, name).stamp.nanosec) != (0, 0)
        ]
        checks.check(
            "what the simulator does not have carries a zero stamp, not a plausible default",
            not wrong,
            f"{len(unfilled)} fields unfilled on all {len(states) - 1} frames after the first"
            if not wrong else f"filled anyway: {sorted(set(wrong))}",
        )
    else:
        print("        nothing drove this bag (--agent-policy replay writes no vehicle state)",
              file=out)

    print(file=out)
    if checks.failed:
        print(f"  {len(checks.failed)} FAILED: {', '.join(checks.failed)}", file=out)
    else:
        print(f"  all {len(checks.results)} checks passed", file=out)
    return not checks.failed


def coverage(path=None, out=sys.stdout):
    """How much of the reference vehicle's bag this one covers, and what stands in the way.

    **A topic present is not a topic correct**, and this counts names. Every other check in this
    file is a relationship between two independently produced quantities, because that is the
    only kind that catches the faults a bag can carry; this one is a ledger, and is useful for
    exactly one thing - knowing whether a phase of stage 11 landed what it claimed.

    With a bag it reports what actually reached the wire. Without one it reports what the code
    declares, which is the higher number: `/tf_static` is declared always and written only when
    a camera rig supplied mounts.
    """
    written = None
    if path is not None:
        by_topic, _ = load(path)
        written = set(by_topic)
        print(f"{path}", file=out)
    ledger = ros_schema.rig_coverage(written)
    total = ledger["producible"]
    produced, declared = len(ledger["produced"]), len(ledger["declared"])

    note = ""
    quiet = []
    if written is not None and produced < declared:
        quiet = [row.topic for row in ledger["declared"] if row.topic not in written]
        note = f"   ({declared} declared, {len(quiet)} not on the wire)"
    print(f"\n  rig topics produced      {produced} / {total}{note}", file=out)
    # Listed on their own lines rather than inline: a drive with no `--camera-rig` leaves seven
    # declared topics unwritten, and seven full `cam_sync_rig` paths on one line is a wrapped
    # mess that hides the one thing this note exists to say.
    for topic in quiet:
        print(f"      declared, not written   {topic}", file=out)

    if ledger["absent"]:
        print("\n  absent, by the phase that lands it", file=out)
        for phase, rows in ledger["absent"].items():
            title = ros_schema.PHASE_TITLES.get(phase, "")
            print(f"    phase {phase}  {title:<44} {len(rows):>3}", file=out)
        print(f"    {'':<52} {sum(len(r) for r in ledger['absent'].values()):>3}", file=out)
    else:
        # A header over an empty table and a total of zero reads as a broken report. Every
        # producible topic is declared, so the only gap left is the declared-against-written one
        # printed above, which is a property of the *drive* rather than of the code.
        print("\n  nothing is absent - every producible topic has a builder", file=out)

    print(
        f"\n  waiting on a .msg        {ledger['definitions_missing']:>3}"
        + (
            "   every type the vehicle publishes is vendored, verbatim from its own recording"
            if not ledger["definitions_missing"]
            else "   recover them: tools/ros_defs.py <a bag off the vehicle> "
            "--write tools/<pkg> --package <pkg>"
        ),
        file=out,
    )
    print(
        f"  not producible           {len(ledger['impossible']):>3}"
        "   excluded by design, each with its reason",
        file=out,
    )
    print(
        f"  simulator extras         {len(ledger['extras']):>3}"
        "   never counted against the target - the vehicle records no ground truth",
        file=out,
    )
    for topic in ledger["extras"]:
        print(f"      {topic}", file=out)

    counts = ledger["verdicts"]
    reference = ros_schema._reference()
    print(
        f"\n  the reference bag is {reference['bag']} - {sum(counts.values())} topics, "
        f"{reference['message_count']} messages over {reference['duration_s']} s: "
        f"{counts[ros_schema.DIRECT]} direct, {counts[ros_schema.APPROXIMATE]} approximate, "
        f"{counts[ros_schema.IMPOSSIBLE]} not producible.  docs/rosbag.md argues each row.",
        file=out,
    )
    return True


def _osm_bounds(workspace):
    """The bounding box of the workspace's own source OSM, for the GNSS containment check."""
    source = workspace / "source" / "map.osm"
    if not source.exists():
        return None
    import re

    head = source.read_text(errors="replace")[:4096]
    # Both quote styles: osmnx writes `"`, JOSM writes `'`, and junction-1's was hand-edited
    # in JOSM. Matching only one silently skips the check rather than failing it.
    found = re.search(
        r"<bounds[^>]*minlat=['\"]([-\d.]+)['\"][^>]*minlon=['\"]([-\d.]+)['\"]"
        r"[^>]*maxlat=['\"]([-\d.]+)['\"][^>]*maxlon=['\"]([-\d.]+)['\"]",
        head,
    )
    if not found:
        return None
    south, west, north, east = (float(v) for v in found.groups())
    return south, west, north, east


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Optional, because `--coverage` is a question about the code rather than about a bag: it
    # answers "how much of the rig's bag can this repo write" with nothing recorded yet.
    parser.add_argument("bag", nargs="?", default=None)
    parser.add_argument(
        "--workspace",
        default=None,
        help="the workspace the dataset came from, so the GNSS can be checked against its own "
        "source OSM extent rather than merely being self-consistent",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="how many of the reference vehicle's 36 producible topics are written, and which "
        "stage-11 phase lands each of the rest. Runs with or without a bag; with one it counts "
        "what actually reached the wire rather than what the code declares.",
    )
    arguments = parser.parse_args(argv)
    if arguments.bag is None and not arguments.coverage:
        parser.error("a bag to probe, or --coverage to report on the code alone")
    # A refusal, not a traceback -- see the same guard in `ros_audit.main`.
    try:
        if arguments.coverage:
            return 0 if coverage(arguments.bag) else 1
        return 0 if probe(arguments.bag, arguments.workspace) else 1
    except ValueError as error:
        print(f"\n  {error}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
