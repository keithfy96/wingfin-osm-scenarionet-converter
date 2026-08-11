"""Turn a chosen start and end lane into the ego track MetaDrive drives.

MetaDrive has no route format. `ScenarioEnv` is wired to `TrajectoryNavigation`, whose whole
input is `tracks[sdc_id]["state"]["position"]` - an array of positions it walks. So "give the
map a route" means "give the map a car that was recorded driving it", and this module is
what invents that car.

Three things about the geometry are worth knowing before reading the code.

* **A junction hop is not a straight line between two lanes.** Every junction movement went
  through a connector, and `ConnectorFeature.centerline` is the path across the junction. It
  is spliced in, or the ego cuts the corner and drives over the kerb.

* **A lane change is not a teleport.** Concatenating a lane's centreline with its
  neighbour's would step sideways by a lane width in zero distance. The outgoing lane is cut
  at its midpoint and the neighbour resumed from its midpoint, so the transition is a
  diagonal - which is what a lane change looks like. `_lane_change_moves` has already
  refused any neighbour that is not the same stretch of road running the same way, so the
  two centrelines are parallel and comparable in length.

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

# How far apart two consecutive pieces of a route may be where they join.
#
# Lanes in this model meet exactly - a continuation starts where the last one ended, and a
# connector starts on its approach - so a real join measures 0. Anything more is a hole the
# car would drive straight across, and because the track is resampled at a fixed spacing a
# hole does not survive as a visible jump: it becomes a smooth line over open ground, with
# nothing downstream to complain. This is the only place it can be caught.
#
# Checked at the joins rather than on the finished polyline. A single lane can legitimately
# be one straight 155 m segment between two vertices, so step length alone cannot tell a
# long road from a gap. Lane changes are excluded because their whole point is a deliberate
# gap, which `_cut` builds and `test_a_lane_change_crosses_over_rather_than_jumping_sideways`
# pins.
MAX_JOIN_M = 5.0

# Below this, `ScenarioEnv._is_arrive_destination` returns true on the first frame, because
# `reference_trajectory.length < 2` is its "vehicle is static" case. An episode that succeeds
# before it starts is worse than one that fails.
MIN_ROUTE_M = 2.0

# How much of each lane a change is spread over, as a fraction of its length either side of
# the midpoint. Cutting both lanes at the *same* point would leave the car stepping a full
# lane width sideways at constant longitude - a teleport, not a lane change. At 0.15 the
# manoeuvre occupies the middle 30% of the two lanes, so on a 100 m stretch it takes about
# 30 m, which is what one looks like. Nothing in the source sets it; it is presentation of a
# move the source does permit, not a claim about how anyone drove.
_CHANGE_HALF_SPAN = 0.15


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

    @property
    def duration_s(self) -> float:
        return self.distance_m / self.speed_mps


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


def _connector_index(model: PreliminaryLaneModel) -> dict[tuple[str, str], np.ndarray]:
    """The geometry of every active junction movement, keyed by the lanes it joins."""
    index: dict[tuple[str, str], np.ndarray] = {}
    for connector in model.connectors:
        if connector.status != "active":
            continue
        index.setdefault(
            (connector.from_lane_id, connector.to_lane_id), _xy(connector.centerline)
        )
    return index


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
    return Route(
        name=name,
        start_lane=start_lane,
        end_lane=end_lane,
        lanes=tuple(chain),
        lane_changes=changes,
        # Measured off the geometry below rather than off the graph weights, which carry the
        # lane-change penalty and so are a search cost, not a distance.
        distance_m=_length_of(route_polyline(model=model, route_lanes=chain, lane_changes=changes)),
        speed_mps=speed_kph / 3.6,
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
    connectors = _connector_index(model)
    changing = set(lane_changes)

    pieces: list[np.ndarray] = []

    def add(piece: np.ndarray, *, joined: bool, what: str) -> None:
        """Append a piece, refusing a join that is really a hole. See `MAX_JOIN_M`."""
        if joined and pieces:
            gap = float(np.linalg.norm(pieces[-1][-1] - piece[0]))
            if gap > MAX_JOIN_M:
                raise RouteError(
                    f"the route leaves a {gap:.0f} m gap before {what}. The lanes do not "
                    "meet there, and a car would drive straight across it"
                )
        pieces.append(piece)

    for position, lane_id in enumerate(route_lanes):
        centre = _xy(lanes[lane_id].centerline)
        arriving_by_change = position in changing
        leaving_by_change = (position + 1) in changing

        if arriving_by_change:
            centre = _cut(centre, keep_head=False, at=0.5 + _CHANGE_HALF_SPAN)
        elif position > 0:
            crossing = connectors.get((route_lanes[position - 1], lane_id))
            if crossing is not None and len(crossing) >= 2:
                add(crossing, joined=True, what=f"the junction into lane {lane_id}")
        if leaving_by_change:
            centre = _cut(centre, keep_head=True, at=0.5 - _CHANGE_HALF_SPAN)
        # A lane arrived at by changing across is meant to start away from where the last
        # piece ended - that offset is the manoeuvre, not a fault.
        add(centre, joined=not arriving_by_change, what=f"lane {lane_id}")

    return _drop_repeats(np.vstack(pieces))


def ego_track(*, route: Route, polyline: np.ndarray) -> dict[str, Any]:
    """The recorded car, resampled at MetaDrive's own step.

    Shape read off `ScenarioDescription._check_object_state_dict`: every state array is the
    scenario's length, 2-D arrays may not be empty in their second axis, and the metadata's
    `object_id` has to equal the key the track is stored under.
    """
    samples = _resample(polyline, spacing=route.speed_mps * TIME_STEP_S)
    if len(samples) < 2:
        raise RouteError(f"route {route.name!r} is too short to drive: {route.distance_m:.1f} m")
    if route.distance_m < MIN_ROUTE_M:
        raise RouteError(
            f"route {route.name!r} is {route.distance_m:.1f} m long. Below "
            f"{MIN_ROUTE_M:.0f} m MetaDrive treats the car as static and the episode "
            "succeeds on its first frame"
        )

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
        [np.cos(heading) * route.speed_mps, np.sin(heading) * route.speed_mps], axis=1
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


def _resample(polyline: np.ndarray, *, spacing: float) -> np.ndarray:
    """Points every `spacing` metres along a polyline, ends included."""
    steps = np.linalg.norm(np.diff(polyline, axis=0), axis=1)
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(travelled[-1])
    if total <= 0 or spacing <= 0:
        return polyline
    count = max(int(math.ceil(total / spacing)), 1)
    wanted = np.linspace(0.0, total, count + 1)
    return np.stack(
        [
            np.interp(wanted, travelled, polyline[:, 0]),
            np.interp(wanted, travelled, polyline[:, 1]),
        ],
        axis=1,
    )


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
        "duration_s": round(route.duration_s, 2),
    }
