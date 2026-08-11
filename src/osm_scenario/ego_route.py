"""Turn a chosen start and end lane into the ego track MetaDrive drives.

MetaDrive has no route format. `ScenarioEnv` is wired to `TrajectoryNavigation`, whose whole
input is `tracks[sdc_id]["state"]["position"]` - an array of positions it walks. So "give the
map a route" means "give the map a car that was recorded driving it", and this module is
what invents that car.

Four things about the geometry are worth knowing before reading the code.

* **A junction turn is built here, not read from the connector.** `ConnectorFeature.centerline`
  looks like the path across a junction and is not one: `topology.connector_curve` builds it
  as a *marker* for the inspection map, and says so. Where the two lanes already meet - 44 of
  `junction-1`'s 83 active connectors, because OSM splits a way whenever a tag changes - the
  marker is a 3 m stub that retraces the approach, so splicing it made the car drive three
  metres, jump back, and drive them again. Where the lanes are genuinely apart, the marker is
  a quadratic Bezier bent around the OSM node and tangent to neither lane, so a 90° turn came
  out as two 82° corners with 28° of curve between them, over 2.81 m of path - an implied
  radius of 1.8 m. `_turn` builds the join from what the two lanes actually do instead: it
  cuts back into both and lays a curve between the cut points whose end tangents *are* the two
  lane directions, so the drive leaves along the road it is on and arrives along the road it
  is joining.

* **A lane change is not a teleport.** Concatenating a lane's centreline with its
  neighbour's would step sideways by a lane width in zero distance. The outgoing lane is cut
  at its midpoint and the neighbour resumed from its midpoint, so the transition is a
  diagonal - which is what a lane change looks like. `_lane_change_moves` has already
  refused any neighbour that is not the same stretch of road running the same way, so the
  two centrelines are parallel and comparable in length.

* **Speed follows the geometry.** A car does not take a 90° junction at the speed limit, and
  a track that says it did teaches an agent that it can. `speed_profile` caps the speed at
  every vertex by the curvature there, then bounds how fast it may change, so the recorded car
  slows before a turn and picks up after it.

* **The route is generated, and says so.** Nothing in the OSM says a car drove here. What
  the source does supply is every metre of the geometry, so the invention is confined to
  *which* way to go and *how fast* - both recorded in `metadata.sdc_route` rather than left
  to be inferred from the numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np

from osm_scenario.lane_model import LaneFeature, Point2D, PreliminaryLaneModel

# MetaDrive steps physics at 0.1 s and `parse_object_state` assumes the same interval when it
# differentiates positions into an angular velocity. Sampling anywhere else would make the
# recorded speed disagree with the speed the simulator infers.
TIME_STEP_S = 0.1

# A default vehicle's box. MetaDrive warns when width exceeds length, and `ScenarioEnv`
# spawns the ego at whatever size the track claims, so this is the one place a plausible
# figure is needed. Not surveyed - nothing about the car is.
EGO_LENGTH_M = 4.6
EGO_WIDTH_M = 1.85
EGO_HEIGHT_M = 1.5

# What a lane change costs in the path search, in metres of equivalent travel. It is not free
# - a route that changes lanes for no reason is worse than one that does not - but it must
# stay far below the length of a detour, or the search would rather drive a mile than move
# across. Nothing in the source sets this; it is a tie-break, and it is only ever a tie-break
# because every real alternative differs by much more.
LANE_CHANGE_COST_M = 5.0

# How far apart two consecutive pieces of a route may be where they join, and how far a
# junction may be across.
#
# Two lanes of the same road meet exactly - a continuation starts where the last one ended -
# so a real join measures 0, and anything more than `MAX_JOIN_M` is a hole the car would
# drive straight across. A junction movement is different: the two lane lines belong to
# different roads and are each offset sideways off their own, so they stop short of the
# shared node on different sides. On `junction-1` those gaps run from 1.7 m to 5.4 m, and a
# larger crossroads is wider still.
#
# Neither survives as a visible jump once the track is resampled - it becomes a smooth line
# over open ground with nothing downstream to complain - so the join is the only place
# either can be caught. Checked there rather than on the finished polyline: a single lane
# can legitimately be one straight 155 m segment between two vertices, so step length alone
# cannot tell a long road from a gap. Lane changes are excluded because their whole point is
# a deliberate sideways offset.
MAX_JOIN_M = 5.0
MAX_CROSSING_M = 20.0

# Below this, `ScenarioEnv._is_arrive_destination` returns true on the first frame, because
# `reference_trajectory.length < 2` is its "vehicle is static" case. An episode that succeeds
# before it starts is worse than one that fails.
MIN_ROUTE_M = 2.0

# The most of a lane one change may use, as a fraction of its length either side of the
# midpoint. Cutting both lanes at the *same* point would leave the car stepping a full lane
# width sideways at constant longitude - a teleport, not a lane change - and cramming the
# move into a few metres is a swerve, which the speed profile then has to slow to a crawl
# for. The manoeuvre takes as long as `SMOOTHING_RADIUS_M` asks for and no more than this.
_CHANGE_MAX_FRACTION = 0.45

# The radius a junction turn is built to, in metres. A 90° turn at 9 m takes about 14 m of
# path, which is what a car does - it starts turning before the junction and finishes after
# it. The connector marker spans 2.81 m at the median, an implied radius of 1.8 m, tighter
# than a car can physically turn. Nothing in OSM sets this: the source says which movements
# are permitted, never how one is driven, so this is presentation like `_CHANGE_HALF_SPAN`.
TURN_RADIUS_M = 9.0

# How much of a lane one turn may eat, as a fraction of the length that lane still has. A
# fixed distance would overrun the short ones - the shorter lane at a real turn in
# `junction-1` is 19.2 m at the median but 6.0 m at worst - so the radius shrinks to fit
# instead of the build failing.
MAX_TURN_TRIM_FRACTION = 0.4

# Cubic Bezier handles are derived from the turn, not from the chord. The chord rule of
# thumb (0.5523 of it) only approximates a circle when the two ends sit symmetrically on
# one, and a junction join does not: the lane lines are offset sideways as well as turned,
# so a handle sized off the chord pinches the middle of the curve - measured on `junction-1`
# at 2.7 m of radius where the geometry called for 24 m. `(4/3)·tan(θ/4)·R` is the exact
# handle for a cubic approximating a circular arc of turn θ and radius R, and
# `R = trim / tan(θ/2)` is the radius the trim was chosen for, so the two agree by
# construction. As θ goes to zero it tends to two thirds of the trim, which is what a plain
# sideways shift wants.
_HANDLE_FACTOR = 4.0 / 3.0

# How finely a turn is sampled. The track is resampled at up to 1.4 m per step, so a
# coarsely drawn arc is thrown away before MetaDrive ever sees it - which is what happened
# to the connector's five points, leaving a median of two recorded positions in a whole turn.
TURN_SAMPLE_M = 0.25

# Below this the two pieces already point the same way and no turn is built.
_STRAIGHT_DEG = 1.0

# Up to this much of a turn, a join is treated as a lane-offset artefact to be smoothed away
# at road speed rather than as a corner to be taken at corner speed. Consecutive lanes of the
# same road do not meet exactly - each is pushed sideways off the way it came from, so where
# the bearing changes at a node the two offsets are 0.26 m to 0.75 m apart on `junction-1`'s
# own routes. That step is not a manoeuvre and the car should not slow for it.
_SMOOTHING_MAX_DEG = 20.0

# The radius a shallow join is smoothed to, in metres. Chosen so that shifting sideways at
# 50 km/h stays inside `LATERAL_ACCEL_MPS2`: v² / a = 13.89² / 1.8 = 107 m. Rounded up, and
# the arithmetic is here rather than computed because this is a property of the road, not of
# whichever route happens to be driven over it.
SMOOTHING_RADIUS_M = 110.0

# How finely the speed profile is worked out. The line it runs on is mostly two-point lanes
# with 0.25 m arcs at the junctions, so a 150 m step sits next to a 0.08 m one; resampling
# first is what lets the acceleration passes and the curvature estimate mean anything.
PROFILE_SAMPLE_M = 0.25

# No vertex of a finished route may turn more than this. A road never does; a marker spliced
# in backwards turns 180°, and that is exactly what shipped. The mirror of `MAX_JOIN_M`:
# that catches a hole at a join, this catches a reversal. Well above a tight U-turn, which
# is drawn as a curve and so turns a few degrees per vertex however sharp it is.
MAX_VERTEX_TURN_DEG = 150.0

# Comfortable lateral acceleration through a bend, in m/s² - about 0.18 g, which is roughly
# the side friction urban junction design assumes and roughly what a driver uses. It is what
# decides the speed through a turn: 9 m of radius comes out at about 15 km/h.
LATERAL_ACCEL_MPS2 = 1.8

# How fast the recorded car may gain and lose speed, in m/s². Braking is allowed to be
# firmer than acceleration, as it is in a car. Without these the speed would step from the
# limit to the corner speed between two samples, which is not a drive and would differentiate
# into an impossible acceleration.
ACCEL_MPS2 = 1.2
BRAKE_MPS2 = 2.0

# The recorded car never crawls: below this a sample is shorter than a few centimetres and
# the heading taken from it turns to noise. A turn tight enough to demand less than 7 km/h is
# tighter than anything `_turn` builds.
MIN_SPEED_MPS = 2.0


class RouteError(RuntimeError):
    """Raised when a chosen start and end cannot become a route."""


@dataclass(frozen=True)
class Route:
    """A path through the lane model, and what it cost to take it."""

    name: str
    start_lane: str
    end_lane: str
    lanes: tuple[str, ...]
    lane_changes: tuple[int, ...]
    """Indices into `lanes` where the step *into* that lane was a lane change."""
    distance_m: float
    speed_mps: float
    """Cruising speed - the lowest limit along the route. Turns are taken below it."""
    duration_s: float
    """How long the drive takes once the turns are slowed for. Not distance / speed."""
    slowest_mps: float
    """The speed at the tightest turn on the route."""


def _xy(points: list[Point2D]) -> np.ndarray:
    return np.array([[point.x, point.y] for point in points], dtype=np.float64)


def _length_of(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def _cut(points: np.ndarray, *, keep_head: bool, at: float) -> np.ndarray:
    """A centreline split at `at`, a fraction of its arc length.

    `keep_head` keeps the run-up to that point, otherwise the run-out from it.
    """
    if len(points) < 2:
        return points
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    target = travelled[-1] * at
    index = int(np.searchsorted(travelled, target))
    index = max(1, min(index, len(points) - 1))
    # Interpolated rather than snapped to the nearest vertex, so a two-point lane still
    # splits where it was asked to instead of collapsing onto one of its ends.
    span = travelled[index] - travelled[index - 1]
    ratio = 0.0 if span == 0 else (target - travelled[index - 1]) / span
    split = points[index - 1] + (points[index] - points[index - 1]) * ratio
    if keep_head:
        return np.vstack([points[:index], split])
    return np.vstack([split, points[index:]])


def _trim_end(points: np.ndarray, distance: float) -> np.ndarray:
    """The line with `distance` metres taken off its far end."""
    total = _length_of(points)
    if distance <= 0.0 or total <= distance:
        return points
    return _cut(points, keep_head=True, at=(total - distance) / total)


def _trim_start(points: np.ndarray, distance: float) -> np.ndarray:
    """The line with `distance` metres taken off its near end."""
    total = _length_of(points)
    if distance <= 0.0 or total <= distance:
        return points
    return _cut(points, keep_head=False, at=distance / total)


def _unit(vector: np.ndarray, *, what: str) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length < 1e-9:
        raise RouteError(f"{what} has no direction: two of its points are the same")
    return vector / length


def _turn_between(before: np.ndarray, after: np.ndarray) -> float:
    """Signed angle from one direction to another, in radians, CCW-positive."""
    return math.atan2(
        float(before[0] * after[1] - before[1] * after[0]),
        float(before[0] * after[0] + before[1] * after[1]),
    )


def _advance_past(
    points: np.ndarray, *, origin: np.ndarray, direction: np.ndarray, what: str
) -> np.ndarray:
    """`points` with any head that lies at or behind `origin` along `direction` removed.

    Lane centrelines are offset sideways from the way they came from, so where the bearing
    changes at a node the two offsets do not meet exactly and the next lane can start a
    fraction of a metre *behind* the last one ended - 0.26 m to 0.75 m on `junction-1`'s own
    routes. Concatenated, that is one sample driven backwards and a heading reversed by 180°,
    which `ReplayEgoCarPolicy` then plays back exactly as recorded.

    Resumed at the foot of the perpendicular rather than at the first surviving vertex, so
    the overlap costs no length and the line stays continuous.
    """
    reach = (points - origin) @ direction
    if float(reach[-1]) <= 0.0:
        raise RouteError(
            f"{what} lies entirely behind the piece before it, so the route would have to "
            "drive backwards to reach it"
        )
    if float(reach[0]) > 0.0:
        return points
    index = int(np.argmax(reach > 0.0))
    before, after = float(reach[index - 1]), float(reach[index])
    ratio = (0.0 - before) / (after - before)
    foot = points[index - 1] + (points[index] - points[index - 1]) * ratio
    return np.vstack([foot, points[index:]])


def _handle_for(turn: float, trim: float) -> float:
    """How far the Bezier's control points sit off each end. See `_HANDLE_FACTOR`."""
    angle = abs(turn)
    if angle < 1e-6:
        return 2.0 * trim / 3.0
    return _HANDLE_FACTOR * math.tan(angle / 4.0) * trim / math.tan(angle / 2.0)


def _turn_curve(
    start: np.ndarray,
    start_direction: np.ndarray,
    end: np.ndarray,
    end_direction: np.ndarray,
    *,
    handle: float,
) -> np.ndarray | None:
    """A cubic Bezier leaving `start` along one direction and reaching `end` along another.

    Tangent to both, which is the whole point: the corner the connector left at each end of a
    junction was 82° at the median, and a curve that meets the lane at an angle is a corner
    however smooth it is in the middle.
    """
    chord = float(np.linalg.norm(end - start))
    if chord < 1e-6:
        return None
    control_in = start + start_direction * handle
    control_out = end - end_direction * handle
    # The control polygon bounds the curve, so its length bounds the arc - generous, and it
    # only decides how many samples to take.
    bound = chord + 2.0 * handle
    count = max(8, int(math.ceil(bound / TURN_SAMPLE_M)))
    step = np.linspace(0.0, 1.0, count + 1).reshape(-1, 1)
    return (
        (1 - step) ** 3 * start
        + 3 * (1 - step) ** 2 * step * control_in
        + 3 * (1 - step) * step**2 * control_out
        + step**3 * end
    )


def _turn(
    arriving: np.ndarray, leaving: np.ndarray, *, what: str, crossing: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Join two lanes: the trimmed approach, the turn between them, the trimmed exit.

    Returned in three pieces rather than one because the exit is trimmed at its start here
    and may be trimmed again at its end by the *next* junction, and the second trim has to be
    measured against what is left rather than against the untouched lane.
    """
    allowed = MAX_CROSSING_M if crossing else MAX_JOIN_M
    gap = float(np.linalg.norm(leaving[0] - arriving[-1]))
    if gap > allowed:
        where = "junction" if crossing else "join"
        raise RouteError(
            f"the route leaves a {gap:.0f} m gap before {what}, more than a {where} spans. "
            "The lanes do not meet there, and a car would drive straight across it"
        )

    direction_in = _unit(arriving[-1] - arriving[-2], what="the lane before " + what)
    direction_out = _unit(leaving[1] - leaving[0], what=what)
    angle = _turn_between(direction_in, direction_out)

    if abs(math.degrees(angle)) < _SMOOTHING_MAX_DEG:
        # Only a shallow join can have an overlap worth trimming: two lanes of the same road
        # that do not quite meet. On a real turn the exit lane legitimately begins behind
        # where the approach ended - that is what a sharp left or a U-turn looks like - and
        # trimming there would refuse movements the map permits.
        leaving = _advance_past(leaving, origin=arriving[-1], direction=direction_in, what=what)
        if len(leaving) < 2:
            raise RouteError(f"{what} has nothing left of it once the overlap is removed")
        direction_out = _unit(leaving[1] - leaving[0], what=what)
        angle = _turn_between(direction_in, direction_out)
    # How far the next lane's line sits to the side of the one arriving, measured across the
    # direction of travel. Distinct from `gap`, which is along it as well.
    across = leaving[0] - arriving[-1]
    sideways = abs(float(across[0] * -direction_in[1] + across[1] * direction_in[0]))
    if abs(math.degrees(angle)) < _STRAIGHT_DEG and sideways < 0.05:
        # Already pointing the same way and already in line: the road carries on, and there
        # is nothing to build. This is the common case - most nodes in OSM are a way ending
        # and the next beginning, not a junction.
        return arriving, np.empty((0, 2), dtype=np.float64), leaving

    trim = TURN_RADIUS_M * math.tan(min(abs(angle), math.radians(170.0)) / 2.0)
    if abs(math.degrees(angle)) < _SMOOTHING_MAX_DEG:
        # A cubic S that shifts `sideways` over a span L peaks at about 6·sideways/L² of
        # curvature, so the span that keeps it at `SMOOTHING_RADIUS_M` is sqrt(6·s·R) - half
        # either side of the join.
        trim = max(trim, math.sqrt(6.0 * sideways * SMOOTHING_RADIUS_M) / 2.0)
    trim = min(
        trim,
        MAX_TURN_TRIM_FRACTION * _length_of(arriving),
        MAX_TURN_TRIM_FRACTION * _length_of(leaving),
    )
    head = _trim_end(arriving, trim)
    tail = _trim_start(leaving, trim)
    curve = _turn_curve(
        head[-1], direction_in, tail[0], direction_out, handle=_handle_for(angle, trim)
    )
    if curve is None:
        return head, np.empty((0, 2), dtype=np.float64), tail
    # Both endpoints are already the last point of `head` and the first of `tail`.
    return head, curve[1:-1], tail


def _smoothing_span(sideways: float) -> float:
    """Half the distance a sideways step of `sideways` metres needs to be spread over.

    A cubic S that shifts `sideways` over a span L peaks at about 6·sideways/L² of curvature,
    so the span that keeps it at `SMOOTHING_RADIUS_M` is sqrt(6·sideways·R).
    """
    return math.sqrt(6.0 * max(sideways, 0.0) * SMOOTHING_RADIUS_M) / 2.0


def _lane_change(
    leaving: np.ndarray, joining: np.ndarray, *, what: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cross from one lane into the one beside it: the run-up, the crossing, the run-out.

    `_lane_change_moves` has already refused any neighbour that is not the same stretch of
    road running the same way, so the two centrelines are parallel and comparable in length -
    which is what lets the crossing be measured as a sideways step from one midpoint to the
    other, and spread over as much road as taking it at speed needs.
    """
    span_leaving, span_joining = _length_of(leaving), _length_of(joining)
    middle_leaving = _cut(leaving, keep_head=True, at=0.5)[-1]
    middle_joining = _cut(joining, keep_head=True, at=0.5)[-1]
    sideways = float(np.linalg.norm(middle_joining - middle_leaving))
    span = min(
        _smoothing_span(sideways),
        _CHANGE_MAX_FRACTION * span_leaving,
        _CHANGE_MAX_FRACTION * span_joining,
    )
    head = _cut(leaving, keep_head=True, at=(span_leaving / 2.0 - span) / span_leaving)
    tail = _cut(joining, keep_head=False, at=(span_joining / 2.0 + span) / span_joining)
    if len(head) < 2 or len(tail) < 2:
        return head, np.empty((0, 2), dtype=np.float64), tail
    direction_in = _unit(head[-1] - head[-2], what=f"the lane changed out of before {what}")
    direction_out = _unit(tail[1] - tail[0], what=what)
    curve = _turn_curve(
        head[-1], direction_in, tail[0], direction_out, handle=_handle_for(0.0, span)
    )
    if curve is None:
        return head, np.empty((0, 2), dtype=np.float64), tail
    return head, curve[1:-1], tail


def _refuse_reversals(line: np.ndarray) -> None:
    """Refuse a drive that snaps round, however it got that way.

    The mirror of `MAX_JOIN_M`. A hole at a join was checked from the day this module was
    written; a *reversal* never was, and 55 of `junction-1`'s 83 connectors produced one. It
    survives resampling as a 180° heading flip, and `ReplayEgoCarPolicy` sets the car's
    heading from that array without complaint, so this is the only place it can be caught.
    """
    if len(line) < 3:
        return
    direction = np.diff(line, axis=0)
    heading = np.arctan2(direction[:, 1], direction[:, 0])
    turns = np.abs(np.degrees((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi))
    worst = int(turns.argmax())
    if turns[worst] > MAX_VERTEX_TURN_DEG:
        travelled = float(np.linalg.norm(direction[: worst + 1], axis=1).sum())
        raise RouteError(
            f"the route turns {turns[worst]:.0f}° in one step, {travelled:.0f} m in at "
            f"({line[worst + 1][0]:.1f}, {line[worst + 1][1]:.1f}). A car cannot do that, "
            "and replaying it spins the recorded car on the spot"
        )


def _drop_repeats(points: np.ndarray, *, tolerance: float = 1e-6) -> np.ndarray:
    """Collapse coincident consecutive points.

    Lanes meet where one ends and the next begins, and a connector starts where its
    approach stops, so joins routinely produce a duplicate. A zero-length step has no
    direction, which would make the heading at that sample undefined.
    """
    if len(points) < 2:
        return points
    keep = [0]
    for index in range(1, len(points)):
        if float(np.linalg.norm(points[index] - points[keep[-1]])) > tolerance:
            keep.append(index)
    return points[keep]


def _graph(
    lanes: dict[str, LaneFeature],
    neighbours: dict[str, tuple[list[str], list[str]]],
    moves: dict[str, list[str]],
) -> nx.DiGraph:
    """The drivable graph, weighted by how far taking each step actually travels.

    Built from the same two relations `_reachability` reports on, so a route can only ever
    use a move the Stage 6 page also shows.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(lanes)
    lengths = {
        lane_id: _length_of(_xy(lane.centerline)) for lane_id, lane in lanes.items()
    }
    for lane_id, (_, exits) in neighbours.items():
        for target in exits:
            graph.add_edge(lane_id, target, weight=lengths[target], change=False)
    for lane_id, sideways in moves.items():
        for target in sideways:
            if graph.has_edge(lane_id, target):
                continue
            # Half the lane, because a change lands mid-way along the neighbour rather than
            # at its start - which is also how the geometry below splices it.
            graph.add_edge(
                lane_id,
                target,
                weight=lengths[target] / 2.0 + LANE_CHANGE_COST_M,
                change=True,
            )
    return graph


def plan_route(
    *,
    model: PreliminaryLaneModel,
    neighbours: dict[str, tuple[list[str], list[str]]],
    moves: dict[str, list[str]],
    name: str,
    start_lane: str,
    end_lane: str,
    speed_kph: float | None = None,
) -> Route:
    """The shortest drive from `start_lane` to `end_lane`, or why there isn't one."""
    lanes = {lane.identifier: lane for lane in model.lanes}
    for role, lane_id in (("start", start_lane), ("end", end_lane)):
        if lane_id not in lanes:
            raise RouteError(f"route {name!r} names {lane_id} as its {role}, which is not a lane")
    if start_lane == end_lane:
        raise RouteError(f"route {name!r} starts and ends on the same lane, {start_lane}")

    graph = _graph(lanes, neighbours, moves)
    try:
        chain = nx.shortest_path(graph, start_lane, end_lane, weight="weight")
    except nx.NetworkXNoPath as error:
        raise RouteError(
            f"route {name!r}: no drive exists from {start_lane} to {end_lane}. Most lane "
            "pairs have none - the map is one-way in most places - so this is a normal "
            "answer rather than a fault"
        ) from error

    changes = tuple(
        position
        for position, (before, after) in enumerate(zip(chain, chain[1:], strict=False), start=1)
        if graph.edges[before, after]["change"]
    )
    limits = [lanes[lane_id].speed_limit_kph for lane_id in chain]
    speed_kph = speed_kph if speed_kph is not None else min(limits)
    cruise_mps = speed_kph / 3.6

    # Measured off the geometry rather than off the graph weights, which carry the
    # lane-change penalty and so are a search cost, not a distance.
    polyline = route_polyline(model=model, route_lanes=chain, lane_changes=changes)
    _, travelled, speed = speed_profile(polyline, cruise_mps=cruise_mps)
    return Route(
        name=name,
        start_lane=start_lane,
        end_lane=end_lane,
        lanes=tuple(chain),
        lane_changes=changes,
        distance_m=_length_of(polyline),
        speed_mps=cruise_mps,
        # Not distance / speed: the car slows for every turn on the way, and a summary that
        # said otherwise would disagree with the track built from the same profile.
        duration_s=float(_arrival_times(travelled, speed)[-1]),
        slowest_mps=float(speed.min()),
    )


def route_polyline(
    *,
    model: PreliminaryLaneModel,
    route_lanes: tuple[str, ...] | list[str],
    lane_changes: tuple[int, ...],
) -> np.ndarray:
    """The path a car actually drives along a chain of lanes.

    Junction movements follow the connector; lane changes cross diagonally over the second
    half of one lane and the first half of the next; everything else is the lane centreline.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    changing = set(lane_changes)
    # The connectors say *which* steps cross a junction, which is all they are asked for
    # here. Their geometry is not used: `topology.connector_curve` builds a marker for the
    # inspection map, and splicing it in as a drive line is what put a 180° flip at 55 of
    # `junction-1`'s 83 movements. See the module docstring.
    crossings = {
        (connector.from_lane_id, connector.to_lane_id)
        for connector in model.connectors
        if connector.status == "active"
    }

    finished: list[np.ndarray] = []
    current: np.ndarray | None = None

    for position, lane_id in enumerate(route_lanes):
        centre = _xy(lanes[lane_id].centerline)
        if current is None:
            current = centre
            continue
        if position in changing:
            # A lane arrived at by changing across is meant to start away from where the
            # last piece ended - that offset is the manoeuvre, not a fault - so it is
            # crossed into rather than turned into.
            head, curve, tail = _lane_change(current, centre, what=f"lane {lane_id}")
        else:
            head, curve, tail = _turn(
                current,
                centre,
                what=f"lane {lane_id}",
                crossing=(route_lanes[position - 1], lane_id) in crossings,
            )
        finished.append(head)
        if len(curve):
            finished.append(curve)
        current = tail

    if current is None:
        raise RouteError("a route has to name at least one lane")
    finished.append(current)

    line = _drop_repeats(np.vstack(finished))
    _refuse_reversals(line)
    return line


def _densify(polyline: np.ndarray, *, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """The same line with a vertex every `spacing` metres, and how far along each one is."""
    steps = np.linalg.norm(np.diff(polyline, axis=0), axis=1)
    along = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(along[-1])
    if total <= spacing:
        return polyline, along
    wanted = np.linspace(0.0, total, int(math.ceil(total / spacing)) + 1)
    dense = np.stack(
        [
            np.interp(wanted, along, polyline[:, 0]),
            np.interp(wanted, along, polyline[:, 1]),
        ],
        axis=1,
    )
    return dense, wanted


def speed_profile(
    polyline: np.ndarray, *, cruise_mps: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The drive resampled evenly, how far along each point is, and how fast the car is there.

    Three passes, in the order a driver does them. The curvature at a point caps the speed
    there, because a 90° junction taken at the speed limit is a track that teaches an agent
    it can do the same. Then a forward and a backward pass bound how fast the speed may
    change, which is what puts the braking *before* the turn instead of at it.
    """
    dense, travelled = _densify(polyline, spacing=PROFILE_SAMPLE_M)
    steps = np.diff(travelled)
    limit = np.full(len(dense), float(cruise_mps), dtype=np.float64)

    if len(dense) >= 3:
        # Curvature as turn per metre, which is what the steering wheel does. The obvious
        # alternative - the circumradius of each triple - reads a polyline's *concentrated*
        # bend as if it were spread over the whole window, and on `junction-1` reported 5.4 m
        # where the path really turned through 2.7 m.
        direction = np.diff(dense, axis=0)
        heading = np.arctan2(direction[:, 1], direction[:, 0])
        turn = np.abs((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi)
        span = (steps[:-1] + steps[1:]) / 2.0
        bends = turn > 1e-9
        radius = np.full(len(turn), np.inf)
        radius[bends] = span[bends] / turn[bends]
        limit[1:-1] = np.minimum(limit[1:-1], np.sqrt(LATERAL_ACCEL_MPS2 * radius))

    limit = np.clip(limit, min(MIN_SPEED_MPS, cruise_mps), cruise_mps)

    speed = limit.copy()
    for index in range(1, len(speed)):
        reachable = math.sqrt(speed[index - 1] ** 2 + 2.0 * ACCEL_MPS2 * steps[index - 1])
        speed[index] = min(speed[index], reachable)
    for index in range(len(speed) - 2, -1, -1):
        stoppable = math.sqrt(speed[index + 1] ** 2 + 2.0 * BRAKE_MPS2 * steps[index])
        speed[index] = min(speed[index], stoppable)
    return dense, travelled, speed


def _arrival_times(travelled: np.ndarray, speed: np.ndarray) -> np.ndarray:
    """When the car reaches each vertex, from the speed at either end of each step."""
    steps = np.diff(travelled)
    mean = (speed[:-1] + speed[1:]) / 2.0
    return np.concatenate([[0.0], np.cumsum(steps / mean)])


def ego_track(*, route: Route, polyline: np.ndarray) -> dict[str, Any]:
    """The recorded car, resampled at MetaDrive's own step.

    Shape read off `ScenarioDescription._check_object_state_dict`: every state array is the
    scenario's length, 2-D arrays may not be empty in their second axis, and the metadata's
    `object_id` has to equal the key the track is stored under.
    """
    if route.distance_m < MIN_ROUTE_M:
        raise RouteError(
            f"route {route.name!r} is {route.distance_m:.1f} m long. Below "
            f"{MIN_ROUTE_M:.0f} m MetaDrive treats the car as static and the episode "
            "succeeds on its first frame"
        )
    samples, sampled_speed = _sample_in_time(polyline, cruise_mps=route.speed_mps)
    if len(samples) < 2:
        raise RouteError(f"route {route.name!r} is too short to drive: {route.distance_m:.1f} m")

    count = len(samples)
    heading = np.empty(count, dtype=np.float64)
    direction = np.diff(samples, axis=0)
    heading[:-1] = np.arctan2(direction[:, 1], direction[:, 0])
    # The last sample has nothing after it to point at, so it keeps the heading it arrived
    # with rather than an invented one.
    heading[-1] = heading[-2]

    position = np.zeros((count, 3), dtype=np.float64)
    position[:, :2] = samples
    velocity = np.stack(
        [np.cos(heading) * sampled_speed, np.sin(heading) * sampled_speed], axis=1
    )

    def constant(value: float) -> np.ndarray:
        return np.full(count, value, dtype=np.float64)

    return {
        "type": "VEHICLE",
        "state": {
            "position": position,
            "heading": heading,
            "velocity": velocity,
            "valid": np.ones(count, dtype=bool),
            "length": constant(EGO_LENGTH_M),
            "width": constant(EGO_WIDTH_M),
            "height": constant(EGO_HEIGHT_M),
        },
        "metadata": {
            "type": "VEHICLE",
            # Must equal the key this track is stored under; `_check_object_state_dict`
            # asserts it, and the traffic manager uses it to know which car not to spawn.
            "object_id": "ego",
            "track_length": count,
        },
    }


def _sample_in_time(
    polyline: np.ndarray, *, cruise_mps: float
) -> tuple[np.ndarray, np.ndarray]:
    """Where the car is every 0.1 s, and how fast it is going, along the whole drive.

    Sampled in *time* rather than at a fixed spacing, which is what makes the speed profile
    visible: a slower stretch simply gets more samples per metre. The last step is dropped
    rather than stretched - MetaDrive assumes exactly 0.1 s between samples when it
    differentiates positions, so the alternative is a final step of the wrong length - which
    leaves the recorded car up to one step short of the end of the line.
    """
    dense, travelled, speed = speed_profile(polyline, cruise_mps=cruise_mps)
    times = _arrival_times(travelled, speed)
    duration = float(times[-1])
    if duration <= 0.0:
        return polyline, np.full(len(polyline), cruise_mps, dtype=np.float64)
    wanted = np.arange(int(math.floor(duration / TIME_STEP_S)) + 1) * TIME_STEP_S
    along = np.interp(wanted, times, travelled)
    samples = np.stack(
        [
            np.interp(along, travelled, dense[:, 0]),
            np.interp(along, travelled, dense[:, 1]),
        ],
        axis=1,
    )
    return samples, np.interp(wanted, times, speed)


def route_summary(route: Route) -> dict[str, Any]:
    """What `metadata.sdc_route` records: that this was generated, and from what.

    A reader who finds a car in a scenario built from OpenStreetMap should be able to tell
    immediately that nobody drove it.
    """
    return {
        "source": "generated",
        "name": route.name,
        "start_lane": route.start_lane,
        "end_lane": route.end_lane,
        "lanes": list(route.lanes),
        "lane_changes": len(route.lane_changes),
        "junction_movements": len(route.lanes) - 1 - len(route.lane_changes),
        "distance_m": round(route.distance_m, 2),
        "speed_kph": round(route.speed_mps * 3.6, 2),
        # The turns are taken below the cruising speed, so one figure would not describe the
        # drive. A reader comparing `distance_m / speed_kph` against `duration_s` and finding
        # they disagree should be able to see why from the summary itself.
        "slowest_kph": round(route.slowest_mps * 3.6, 2),
        "duration_s": round(route.duration_s, 2),
    }
