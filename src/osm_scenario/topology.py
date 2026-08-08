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
    """Return the signed heading change in degrees in the range [-180, 180]."""
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
    tagged = {item for item in turn_permissions if item in {"left", "right"}}
    family = movement_family(movement)
    if len(tagged) == 1:
        turn = next(iter(tagged))
    elif family in {"left", "right"}:
        turn = family
    elif abs(angle) >= min_degrees:
        turn = "left" if angle > 0 else "right"
    else:
        return None
    return "nearside" if (turn == "left") == (driving_side == "left") else "offside"


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


def connector_curve(
    incoming: LineString, outgoing: LineString, junction_xy: tuple[float, float]
) -> LineString:
    """Create the legacy-compatible five-point quadratic Bezier connector."""
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
    points = []
    for step in range(5):
        t = step / 4
        points.append(
            (
                (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * junction_xy[0] + t**2 * end[0],
                (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * junction_xy[1] + t**2 * end[1],
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
