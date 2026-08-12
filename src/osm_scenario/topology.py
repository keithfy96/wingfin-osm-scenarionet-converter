"""Deterministic Stage 2 movement topology and restriction resolution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString

# How far back along the incoming lane a connector marker reaches when the two lanes
# already meet. Matched to the length the Bezier form produces at a real junction, so
# collinear and turning connectors read at the same scale.
COLLINEAR_STUB_METRES = 3.0

# A node where one road simply continues into the next is not a junction, but the two ways can
# still meet at an angle - OSM puts a vertex where the road bends and the real road curves
# through it. Above this angle `generation._node_setbacks` cuts the two lanes back and a curve
# is fitted between them, so above it the two lanes no longer meet.
#
# Lives here rather than in `generation` because it is not only generation's business: once a
# join has been parted, `ego_route` has to know that the gap is deliberate rather than a hole,
# and a second copy of the number in a second module is how the two quietly stop agreeing.
BEND_FILLET_MIN_DEGREES = 8.0

# How finely a junction turn is drawn. Matched to Waymo, whose lane polylines in
# `metadrive/assets/waymo/` are sampled at a uniform 0.50 m — MetaDrive renders the road surface
# and localises the car from exactly these points, so a turn drawn with five of them is a
# polygon the car corners around rather than a curve it follows.
CONNECTOR_SAMPLE_METRES = 0.5

# Even a short slip between two nearly-aligned lanes gets enough points to read as a curve.
CONNECTOR_MIN_SEGMENTS = 8

# ...but never closer together than this. Forcing eight segments onto a 0.1 m curve puts the
# points 12 mm apart, which is below the noise in the coordinates themselves: the direction from
# one to the next becomes arbitrary and the "curve" wanders through hundreds of degrees. The
# minimum count is a floor on detail, not a licence to sample finer than the geometry exists.
CONNECTOR_MIN_SAMPLE_METRES = 0.05


@dataclass(frozen=True)
class MovementCandidate:
    junction_node_id: str
    from_lane_id: str
    to_lane_id: str
    from_way_id: str
    to_way_id: str
    movement: str
    angle_degrees: float
    centerline: LineString
    ambiguous: bool
    # Which ambiguity triggers fired, so a review finding can say why this movement
    # is held rather than leaving every one of them to one generic sentence.
    ambiguity_causes: tuple[str, ...] = ()


def signed_turn_angle(incoming: LineString, outgoing: LineString) -> float:
    """Return the signed heading change in degrees in the range [-180, 180].

    Left deliberately un-normalised at the branch cut, after trying the opposite. A dead-on
    U-turn returns -180 or +180 depending only on which side of zero the sine lands, so the
    sign there is a floating-point accident rather than a direction. It is tempting to pin it,
    but the sign reaches the U-turn *target* selection: at node 474913266 the -180 spelling
    picks lane 06f4b600 and the +180 spelling picks 86762d66, which is a different movement
    with a different id. `junction-1` already holds U-turns of both signs, so pinning to either
    value re-identifies the findings on the others and invalidates settled review decisions.

    That instability is a real defect — the choice of U-turn target should not depend on a
    float — but it is a movement-selection question, not a geometry one, and fixing it here
    would silently change which movements exist.
    """
    a0, a1 = incoming.coords[-2], incoming.coords[-1]
    b0, b1 = outgoing.coords[0], outgoing.coords[1]
    incoming_heading = math.atan2(a1[1] - a0[1], a1[0] - a0[0])
    outgoing_heading = math.atan2(b1[1] - b0[1], b1[0] - b0[0])
    return math.degrees(
        math.atan2(
            math.sin(outgoing_heading - incoming_heading),
            math.cos(outgoing_heading - incoming_heading),
        )
    )


def classify_movement(angle: float) -> str:
    absolute = abs(angle)
    if absolute >= 145:
        return "reverse"
    if absolute <= 35:
        return "through"
    if angle > 0:
        return "slight_left" if absolute < 70 else "left"
    return "slight_right" if absolute < 70 else "right"


def movement_matches(permission: str, movement: str) -> bool:
    aliases = {
        "left": {"left", "slight_left", "sharp_left"},
        "right": {"right", "slight_right", "sharp_right"},
        "through": {"through"},
        "reverse": {"reverse", "uturn"},
    }
    return movement in aliases.get(permission, {permission})


def movement_family(movement: str) -> str:
    if movement in {"left", "slight_left"}:
        return "left"
    if movement in {"right", "slight_right"}:
        return "right"
    return movement


def movement_side(
    *,
    movement: str,
    angle: float,
    driving_side: str,
    turn_permissions: list[str],
    min_degrees: float,
) -> str | None:
    """Classify a movement as leaving toward the kerb or the centreline.

    Returns `nearside` for a movement toward the kerb, `offside` for one toward the
    road centre, or `None` for a straight-ahead movement that carries no side.

    `classify_movement` treats everything within 35 degrees as `through`, so a slip
    road leaving at 20 degrees is not a `left` movement even though it plainly leaves
    to the left. The angle sign past `min_degrees` recovers that; an explicit
    `turn:lanes` value naming one direction outranks the geometry either way.
    """
    tagged = tagged_movement_side(turn_permissions, driving_side)
    if tagged is not None:
        return tagged
    family = movement_family(movement)
    if family in {"left", "right"}:
        turn = family
    elif abs(angle) >= min_degrees:
        turn = "left" if angle > 0 else "right"
    else:
        return None
    return _side_for_turn(turn, driving_side)


def _side_for_turn(turn: str, driving_side: str) -> str:
    """Which side of the destination a left or right turn lands on."""
    return "nearside" if (turn == "left") == (driving_side == "left") else "offside"


def tagged_movement_side(turn_permissions: list[str], driving_side: str) -> str | None:
    """The side an explicit `turn:lanes` value puts a lane on, or None if it does not.

    Only a value naming exactly one of left or right is decisive: `left;right` permits
    both and leaves the side to the geometry. Shared with `movement_side` so the tag's
    reading cannot drift between deciding a side and grouping the lanes that share it.
    """
    turns = {item for item in turn_permissions if item in {"left", "right"}}
    if len(turns) != 1:
        return None
    return _side_for_turn(next(iter(turns)), driving_side)


def side_lane_index(side: str, lane_count: int) -> int:
    """Index of the lane on `side` of a carriageway of `lane_count` lanes.

    Indices run centre-out, so the offside lane is always 0 and the nearside lane is
    the last one. Shared by the source filter and the target selector so the two
    cannot drift apart.
    """
    return lane_count - 1 if side == "nearside" else 0


def uturn_evidence_status(turn_permissions: list[str]) -> str:
    """Classify a plausible U-turn from lane-tag evidence alone."""
    if any(permission in {"reverse", "uturn"} for permission in turn_permissions):
        return "active"
    if turn_permissions:
        return "excluded"
    return "review_required"


def _unit_tangent(line: LineString, *, at_end: bool) -> tuple[float, float]:
    """The direction the line is travelling where it meets the junction.

    Taken from the whole last (or first) segment rather than from two adjacent coordinates:
    a generated lane usually has two points, so the segment *is* the lane, and on the few
    that have three the final segment is still the one that sets the heading a car leaves on.
    """
    if at_end:
        first, second = line.coords[-2], line.coords[-1]
    else:
        first, second = line.coords[1], line.coords[0]
    dx, dy = second[0] - first[0], second[1] - first[1]
    span = math.hypot(dx, dy)
    if span <= 0:
        raise ValueError("cannot take a tangent from a zero-length segment")
    return (dx / span, dy / span) if at_end else (-dx / span, -dy / span)


def connector_curve(
    incoming: LineString, outgoing: LineString, junction_xy: tuple[float, float]
) -> LineString:
    """The path a car actually drives across a junction, from one lane's end to the next's start.

    A cubic Bezier whose control points sit *along the two tangents* rather than on the junction
    node. That is the whole point: the curve leaves the approach in the approach's own direction
    and arrives at the exit in the exit's, so the heading is continuous at both joins by
    construction. The previous version put its single control point on the node, which produced
    a curve that met neither tangent — and because lanes were generated all the way to that same
    node, start, end and control were all within one lane width of each other and the curve
    collapsed into a ~2 m stub pointing sideways. Trimming the lanes back gives it something to
    span; this gives it the right shape once it has.

    One third of the chord is the usual handle length for a Bezier standing in for a circular
    arc: shorter and the curve cuts the corner, longer and it bulges past the kerb.

    Sampled by distance rather than at a fixed count, because a 90 degree turn and a slight
    merge need different numbers of points and MetaDrive draws the polyline exactly as given.
    `junction_xy` is no longer part of the shape and is kept for the collinear case below.
    """
    start, end = incoming.coords[-1], outgoing.coords[0]
    if math.dist(start, end) < 0.05:
        # The two lanes already meet, so there is no gap to span and the connector is
        # only a marker. Measure the stub back along the incoming line rather than to
        # its previous vertex: a straight lane has just two, so the vertex before the
        # end is the far end, and the marker would retrace the entire lane.
        approach = incoming.interpolate(
            incoming.length - min(COLLINEAR_STUB_METRES, incoming.length)
        ).coords[0]
        midpoint = ((approach[0] + start[0]) / 2, (approach[1] + start[1]) / 2)
        return LineString([approach, midpoint, start])

    entry = _unit_tangent(incoming, at_end=True)
    exit_ = _unit_tangent(outgoing, at_end=False)
    chord = math.dist(start, end)
    handle = chord / 3.0
    control_a = (start[0] + entry[0] * handle, start[1] + entry[1] * handle)
    control_b = (end[0] - exit_[0] * handle, end[1] - exit_[1] * handle)

    # The chord understates a tight turn's arc, so sample against a length that grows with how
    # far the curve has to bend: a 90 degree turn is about 1.1x its chord, a U-turn about 2.1x.
    cross = entry[0] * exit_[1] - entry[1] * exit_[0]
    dot = entry[0] * exit_[0] + entry[1] * exit_[1]
    bend = abs(math.atan2(cross, dot))
    estimated = chord * (1.0 + 0.4 * bend)
    by_spacing = int(math.ceil(estimated / CONNECTOR_SAMPLE_METRES))
    affordable = max(1, int(estimated / CONNECTOR_MIN_SAMPLE_METRES))
    steps = max(1, min(max(by_spacing, CONNECTOR_MIN_SEGMENTS), affordable))

    points = []
    for step in range(steps + 1):
        t = step / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        points.append(
            (
                a * start[0] + b * control_a[0] + c * control_b[0] + d * end[0],
                a * start[1] + b * control_a[1] + c * control_b[1] + d * end[1],
            )
        )
    deduplicated = [points[0]]
    for point in points[1:]:
        if math.dist(point, deduplicated[-1]) > 1e-6:
            deduplicated.append(point)
    if len(deduplicated) < 2:
        raise ValueError("connector geometry collapsed to a point")
    return LineString(deduplicated)


def restriction_roles(relation: Any) -> dict[str, list[tuple[str, str]]]:
    roles: dict[str, list[tuple[str, str]]] = {"from": [], "via": [], "to": []}
    for member in relation.members:
        if member.role in roles:
            roles[member.role].append((member.member_type, member.reference))
    return roles


def forbidden_by_node_restriction(candidate: MovementCandidate, relation: Any) -> bool:
    roles = restriction_roles(relation)
    from_ways = {value for kind, value in roles["from"] if kind == "way"}
    to_ways = {value for kind, value in roles["to"] if kind == "way"}
    via_nodes = {value for kind, value in roles["via"] if kind == "node"}
    if not from_ways or not to_ways or candidate.junction_node_id not in via_nodes:
        return False
    matches_to = candidate.to_way_id in to_ways
    restriction = relation.tags.get("restriction", "")
    if candidate.from_way_id not in from_ways:
        return False
    return (restriction.startswith("no_") and matches_to) or (
        restriction.startswith("only_") and not matches_to
    )


def via_way_resolution(
    relation: Any, candidates: list[MovementCandidate]
) -> tuple[str, set[int], str]:
    """Enforce a via-way restriction only when its connector chain is unique."""
    roles = restriction_roles(relation)
    from_ways = [value for kind, value in roles["from"] if kind == "way"]
    via_ways = [value for kind, value in roles["via"] if kind == "way"]
    to_ways = [value for kind, value in roles["to"] if kind == "way"]
    if len(from_ways) != 1 or not via_ways or len(to_ways) != 1:
        return "review_required", set(), "restriction members are incomplete or non-unique"
    chain = from_ways + via_ways + to_ways
    matching_steps: list[list[int]] = []
    for source, target in zip(chain, chain[1:], strict=False):
        matches = [
            index
            for index, candidate in enumerate(candidates)
            if candidate.from_way_id == source and candidate.to_way_id == target
        ]
        if not matches:
            return "review_required", set(), "via-way connector chain is missing"
        junctions = {candidates[index].junction_node_id for index in matches}
        if len(junctions) != 1:
            return "review_required", set(), "via-way connector chain is branching"
        matching_steps.append(matches)
    restriction = relation.tags.get("restriction", "")
    final_source = chain[-2]
    final_target = chain[-1]
    if restriction.startswith("no_"):
        return "enforced", set(matching_steps[-1]), "prohibited via-way suffix removed"
    if restriction.startswith("only_"):
        junction = candidates[matching_steps[-1][0]].junction_node_id
        removed = {
            index
            for index, candidate in enumerate(candidates)
            if candidate.from_way_id == final_source
            and candidate.junction_node_id == junction
            and candidate.to_way_id != final_target
        }
        return "enforced", removed, "unique via-way exit retained"
    return "review_required", set(), "unsupported restriction type"
