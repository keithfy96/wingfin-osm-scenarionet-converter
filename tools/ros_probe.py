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
    by_topic = defaultdict(list)
    by_stamp = defaultdict(set)
    with Reader(path) as reader:
        for connection, log_time, raw in reader.messages():
            message = store.deserialize_cdr(raw, connection.msgtype)
            by_topic[connection.topic].append((log_time, message))
            by_stamp[log_time].add(connection.topic)
    for messages in by_topic.values():
        messages.sort(key=lambda pair: pair[0])
    return by_topic, by_stamp


def _yaw(orientation):
    """Yaw out of a quaternion, the inverse of `ros_schema.quaternion`."""
    x, y, z, w = orientation.x, orientation.y, orientation.z, orientation.w
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


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
    if written is not None and produced < declared:
        quiet = [row.topic for row in ledger["declared"] if row.topic not in written]
        note = f"   ({declared} declared; {', '.join(quiet)} not on the wire)"
    print(f"\n  rig topics produced      {produced} / {total}{note}", file=out)

    print("\n  absent, by the phase that lands it", file=out)
    for phase, rows in ledger["absent"].items():
        title = ros_schema.PHASE_TITLES.get(phase, "")
        print(f"    phase {phase}  {title:<44} {len(rows):>3}", file=out)
    print(f"    {'':<52} {sum(len(r) for r in ledger['absent'].values()):>3}", file=out)

    print(
        f"\n  waiting on a .msg        {ledger['definitions_missing']:>3}"
        "   or on which type the rig used; tools/ros_defs.py recovers them",
        file=out,
    )
    print(
        f"  not producible           {len(ledger['impossible']):>3}"
        "   excluded by design, each with its reason",
        file=out,
    )
    print(
        f"  simulator extras         {len(ledger['extras']):>3}"
        "   never counted against the 45 - the rig's bag has no ground truth",
        file=out,
    )
    for topic in ledger["extras"]:
        print(f"      {topic}", file=out)

    counts = ledger["verdicts"]
    print(
        f"\n  the reference bag is {sum(counts.values())} topics: "
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
        help="how many of the reference vehicle's 45 producible topics are written, and which "
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
