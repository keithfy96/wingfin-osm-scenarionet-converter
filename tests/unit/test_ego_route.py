"""Turning two chosen lanes into the car MetaDrive drives.

The geometry assertions here are about things MetaDrive does *silently* when they are wrong:
truncate a trajectory at a 100 m jump, treat a short route as a static car and succeed on
frame one, or accept a lane change that is really a sideways teleport. None of those raise
anything, so they have to be caught on this side.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from osm_scenario.conversion import _lane_change_moves, _lane_neighbours
from osm_scenario.ego_route import (
    ACCEL_MPS2,
    BRAKE_MPS2,
    LATERAL_ACCEL_MPS2,
    MAX_JOIN_M,
    MAX_VERTEX_TURN_DEG,
    TIME_STEP_S,
    RouteError,
    _refuse_reversals,
    ego_track,
    plan_route,
    route_polyline,
    route_summary,
    speed_profile,
)
from osm_scenario.lane_model import ConnectorFeature, LaneFeature, Point2D, PreliminaryLaneModel

WIDTH = 4.0
_METADATA = {
    "generator_version": "test",
    "lane_model_schema_version": 1,
    "source_checksum": "source",
    "projected_graph_checksum": "graph",
    "configuration_checksum": "config",
    "generation_fingerprint": "fingerprint",
    "coordinate_system_wkt": "EPSG:4326",
}


def _line(x0: float, x1: float, y: float = 0.0) -> list[Point2D]:
    return [Point2D(x=x0, y=y), Point2D(x=x1, y=y)]


def _surface(x0: float, x1: float, y: float = 0.0) -> list[Point2D]:
    half = WIDTH / 2
    return [
        Point2D(x=x0, y=y - half),
        Point2D(x=x1, y=y - half),
        Point2D(x=x1, y=y + half),
        Point2D(x=x0, y=y + half),
        Point2D(x=x0, y=y - half),
    ]


def _lane(identifier: str, x0: float, x1: float, y: float = 0.0, **update: Any) -> LaneFeature:
    lane = LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=["1", "2", "0"],
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=WIDTH,
        speed_limit_kph=36.0,  # 10 m/s, so a sample is exactly 1 m apart
        centerline=_line(x0, x1, y),
        polygon=_surface(x0, x1, y),
        boundaries=[],
    )
    return lane.model_copy(update=update) if update else lane


def _chain(**update: Any) -> PreliminaryLaneModel:
    """`a` runs into `b` through connector `c`, `b` continues into `d`.

    `a2` runs alongside `a` and has no exits of its own, so the only way out of it is
    across into `a` - which is what makes the lane-change assertions unambiguous.

    The same shape as `web/test/route/path.test.ts`'s fixture; the route each expects is
    the same, which is what stops the page offering drives the converter refuses.
    """
    a = _lane("a", 0.0, 100.0, exit_lanes=["c"], lane_count=2, left_neighbor="a2")
    a2 = _lane("a2", 0.0, 100.0, y=WIDTH, lane_index=1, lane_count=2, right_neighbor="a")
    b = _lane("b", 110.0, 210.0, entry_lanes=["c"], exit_lanes=["d"], source_edge=["2", "3", "0"])
    d = _lane("d", 210.0, 310.0, entry_lanes=["b"], source_edge=["3", "4", "0"])
    connector = ConnectorFeature(
        identifier="c",
        junction_node_id="900",
        from_lane_id="a",
        to_lane_id="b",
        from_way_id="200",
        to_way_id="201",
        movement="through",
        turn_angle_degrees=0.0,
        status="active",
        # Bulges off the straight line, so a route that skipped the connector would be
        # measurably shorter rather than merely differently shaped.
        centerline=[Point2D(x=100.0, y=0.0), Point2D(x=105.0, y=6.0), Point2D(x=110.0, y=0.0)],
        polygon=_surface(100.0, 110.0),
    )
    model = PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [item.model_dump() for item in (a, a2, b, d)],
            "connectors": [connector.model_dump()],
        }
    )
    return model.model_copy(update=update) if update else model


def _free(identifier: str, points: list[tuple[float, float]], **update: Any) -> LaneFeature:
    """A lane along any line, for the geometry cases a horizontal one cannot express."""
    line = [Point2D(x=x, y=y) for x, y in points]
    return _lane(identifier, 0.0, 1.0).model_copy(
        update={"identifier": identifier, "centerline": line, **update}
    )


def _corner(connector_line: list[Point2D] | None = None) -> PreliminaryLaneModel:
    """`n` runs north and `e` runs east, meeting at a 90° left turn.

    The two lane lines stop 4.2 m apart on different sides of the junction, which is the
    shape a real one takes: each is offset sideways off the road it belongs to, so neither
    reaches the shared OSM node. `connector_line` defaults to the marker
    `topology.connector_curve` actually produces where the lanes are that far apart - the
    quadratic Bezier bent around the node - so a test that drives this fixture drives the
    same geometry `junction-1` does.
    """
    north = _free("n", [(0.0, 0.0), (0.0, 60.0)], exit_lanes=["c"])
    east = _free(
        "e", [(3.0, 63.0), (63.0, 63.0)], entry_lanes=["c"], source_edge=["2", "3", "0"]
    )
    node = (0.0, 63.0)
    default = [
        Point2D(
            x=(1 - t) ** 2 * 0.0 + 2 * (1 - t) * t * node[0] + t**2 * 3.0,
            y=(1 - t) ** 2 * 60.0 + 2 * (1 - t) * t * node[1] + t**2 * 63.0,
        )
        for t in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    connector = ConnectorFeature(
        identifier="c",
        junction_node_id="900",
        from_lane_id="n",
        to_lane_id="e",
        from_way_id="200",
        to_way_id="201",
        movement="left",
        turn_angle_degrees=90.0,
        status="active",
        centerline=connector_line if connector_line is not None else default,
        polygon=_surface(0.0, 3.0),
    )
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [north.model_dump(), east.model_dump()],
            "connectors": [connector.model_dump()],
        }
    )


def _headings(line: np.ndarray) -> np.ndarray:
    """The direction of each segment of a line, in degrees."""
    step = np.diff(line, axis=0)
    return np.degrees(np.arctan2(step[:, 1], step[:, 0]))


def _turns(line: np.ndarray) -> np.ndarray:
    """How far the line turns at each vertex, in degrees, shortest way round."""
    return np.abs((np.diff(_headings(line)) + 180.0) % 360.0 - 180.0)


def _tightest_radius_m(line: np.ndarray) -> float:
    """The tightest bend on a line, measured as turn per metre travelled."""
    step = np.linalg.norm(np.diff(line, axis=0), axis=1)
    turn = np.radians(_turns(line))
    span = (step[:-1] + step[1:]) / 2.0
    bending = turn > 1e-9
    if not bending.any():
        return math.inf
    return float((span[bending] / turn[bending]).min())


def _plan(model: PreliminaryLaneModel, start: str, end: str, name: str = "r") -> Any:
    return plan_route(
        model=model,
        neighbours=_lane_neighbours(model),
        moves=_lane_change_moves(model),
        name=name,
        start_lane=start,
        end_lane=end,
    )


# --- choosing the drive -------------------------------------------------------------------


def test_a_route_follows_the_chain_of_lanes() -> None:
    route = _plan(_chain(), "a", "d")
    assert route.lanes == ("a", "b", "d")
    assert route.lane_changes == ()


def test_a_lane_change_is_recorded_as_the_step_into_that_lane() -> None:
    """`a2` has no exits, so reaching anything at all means moving across into `a` first.

    The index is into `lanes`, not a lane of its own: the car is on `a2`, then on `a`. The
    matching client assertion is in `web/test/route/path.test.ts`.
    """
    route = _plan(_chain(), "a2", "d")
    assert route.lanes == ("a2", "a", "b", "d")
    assert route.lane_changes == (1,)


def test_a_pair_with_no_drive_between_them_says_so() -> None:
    """Most pairs have none - 22,217 of 80,940 in `junction-1` - so this is the normal answer."""
    with pytest.raises(RouteError, match="no drive exists"):
        _plan(_chain(), "d", "a")


def test_a_route_that_starts_where_it_ends_is_refused() -> None:
    with pytest.raises(RouteError, match="starts and ends on the same lane"):
        _plan(_chain(), "a", "a")


def test_a_lane_that_is_not_in_the_model_is_named() -> None:
    with pytest.raises(RouteError, match="not a lane"):
        _plan(_chain(), "a", "somewhere-else")


def test_the_same_endpoints_always_give_the_same_route() -> None:
    """The track is the scenario's content, so an unstable route would churn its checksum."""
    model = _chain()
    first = _plan(model, "a2", "d")
    second = _plan(model, "a2", "d")
    assert first.lanes == second.lanes
    assert first.distance_m == second.distance_m


# --- the geometry that gets driven --------------------------------------------------------


def test_a_junction_turn_leaves_and_arrives_along_the_lanes() -> None:
    """The turn is tangent to both lanes, so there is no corner at either end of it.

    This is the defect that shipped. The connector is a marker, not a driving line: its
    Bezier is bent around the OSM node rather than along either lane, so splicing it in put
    a corner at each end. Measured on `junction-1` before the fix, a 90° turn was taken as
    two 82° corners with 28° of curve between them.
    """
    model = _corner()
    line = route_polyline(model=model, route_lanes=("n", "e"), lane_changes=())
    headings = _headings(line)
    assert headings[0] == pytest.approx(90.0), "the drive does not leave along lane n"
    assert headings[-1] == pytest.approx(0.0), "the drive does not arrive along lane e"
    # The whole 90° is spread over the curve rather than taken at its ends.
    assert _turns(line).max() < 10.0


def test_a_junction_turn_is_wide_enough_to_drive() -> None:
    """A 90° turn through 1.8 m of radius is tighter than a car can physically turn.

    That is what the connector marker asked for: 2.81 m of path at the median for the whole
    turn. The radius here is chosen so the turn can be taken at a sensible speed, and the
    speed profile then works out what that speed is.
    """
    model = _corner()
    line = route_polyline(model=model, route_lanes=("n", "e"), lane_changes=())
    assert _tightest_radius_m(line) > 5.0


def test_a_connector_that_retraces_its_approach_is_not_driven() -> None:
    """Where two lanes already meet, `connector_curve` returns a stub back up the approach.

    44 of `junction-1`'s 83 active connectors are that shape, because OSM splits a way
    whenever a tag changes and both lane lines then sit on the same point. Splicing the stub
    in made the car drive three metres, jump back to the start of them and drive them again -
    one sample travelling backwards, and a heading reversed by 180° that
    `ReplayEgoCarPolicy` plays back exactly as recorded.
    """
    model = _pair(100.0, 100.0, connector=[Point2D(x=97.0, y=0.0), Point2D(x=100.0, y=0.0)])
    line = route_polyline(model=model, route_lanes=("p", "q"), lane_changes=())
    forward = np.diff(line[:, 0])
    assert (forward > 0).all(), "the route doubles back on itself"
    assert line[-1][0] == pytest.approx(200.0)


def test_a_lane_that_starts_behind_the_last_one_does_not_reverse_the_car() -> None:
    """Lane lines are offset sideways off their way, so at a bend they overlap slightly.

    0.26 m to 0.75 m on `junction-1`'s own routes. Concatenated that is one sample driven
    backwards; the overlap is taken off the front of the next lane instead.
    """
    model = _pair(100.0, 99.0)
    line = route_polyline(model=model, route_lanes=("p", "q"), lane_changes=())
    assert (np.diff(line[:, 0]) > 0).all()


def test_a_drive_that_snaps_round_is_refused() -> None:
    """The mirror of the hole check. A hole was caught from the start; a reversal was not.

    Tested against the guard itself rather than through a fixture, because with the joins
    fixed nothing reaches it any more: an overlap is trimmed off the front of the next lane
    and a gap wide enough to double back is refused as a hole first. It stays because that
    is exactly what was true of the *hole* check before a reversal ever shipped, and the
    shape it catches - a marker spliced in backwards - was in every build for a fortnight.
    """
    doubling_back = np.array([[0.0, 0.0], [10.0, 0.0], [7.0, 0.0], [20.0, 0.0]])
    with pytest.raises(RouteError, match="turns 180"):
        _refuse_reversals(doubling_back)


def test_a_lane_change_crosses_over_rather_than_jumping_sideways() -> None:
    """Concatenating two parallel centrelines would step a lane width in zero distance."""
    model = _chain()
    route = _plan(model, "a2", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    assert line[0][1] == pytest.approx(WIDTH), "the route does not start in lane a2"
    assert line[-1][1] == pytest.approx(0.0), "the route never reaches lane a"
    # Crossed gradually rather than in one step, and never sideways faster than forwards.
    lateral = np.abs(np.diff(line[:, 1]))
    assert lateral.max() < WIDTH / 4
    assert (np.abs(np.diff(line[:, 0])) > lateral).all()


def test_a_lane_change_is_spread_over_enough_road_to_take_at_speed() -> None:
    """Crammed into a few metres a change is a swerve, and the car has to crawl through it.

    Before the manoeuvre was sized, `junction-1`'s only lane change forced the recorded car
    down to 8.6 km/h on a 50 km/h road.
    """
    model = _chain()
    route = _plan(model, "a2", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    crossing = line[np.abs(np.diff(line[:, 1], prepend=line[0][1])) > 1e-9]
    assert crossing[-1][0] - crossing[0][0] > 20.0
    assert _tightest_radius_m(line) > 40.0


def test_the_track_is_sampled_at_metadrives_own_step() -> None:
    """0.1 s is what `parse_object_state` assumes when it differentiates positions.

    Sampled in time rather than at a fixed spacing, which is what makes the speed profile
    visible at all: a slower stretch simply gets more samples per metre. So the count comes
    from the duration, not from the distance.
    """
    model = _chain()
    route = _plan(model, "a", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    track = ego_track(route=route, polyline=line)
    steps = np.linalg.norm(np.diff(track["state"]["position"][:, :2], axis=0), axis=1)
    # 36 kph is 10 m/s, so 0.1 s is at most 1 m. Corners shorten the chord slightly.
    assert steps.max() <= route.speed_mps * TIME_STEP_S + 1e-9
    assert len(track["state"]["position"]) == math.floor(route.duration_s / TIME_STEP_S) + 1


def test_the_track_carries_every_array_metadrive_checks() -> None:
    """Shapes read off `ScenarioDescription._check_object_state_dict`, not from a guess."""
    model = _chain()
    route = _plan(model, "a", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    track = ego_track(route=route, polyline=line)
    steps = len(track["state"]["position"])

    assert track["type"] == "VEHICLE"
    # Must equal the key the track is stored under, or `_check_object_state_dict` fails.
    assert track["metadata"]["object_id"] == "ego"
    state = track["state"]
    assert state["position"].shape == (steps, 3)
    assert state["velocity"].shape == (steps, 2)
    for key in ("heading", "valid", "length", "width", "height"):
        assert state[key].shape == (steps,), key
    assert state["valid"].all()
    # A car with no width is a car MetaDrive spawns with no width.
    assert state["width"][0] > 0 and state["length"][0] > state["width"][0]


def test_the_recorded_speed_matches_the_recorded_movement() -> None:
    """`velocity` is read directly rather than differentiated, so it can disagree silently."""
    model = _chain()
    route = _plan(model, "a", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    state = ego_track(route=route, polyline=line)["state"]
    speeds = np.linalg.norm(state["velocity"], axis=1)
    assert speeds.max() <= route.speed_mps + 1e-9

    # How far the car moved between two samples is how fast it says it was going. Slightly
    # less through a bend, because the straight line between two samples is a chord; never
    # more, which would be a car outrunning its own velocity.
    moved = np.linalg.norm(np.diff(state["position"][:, :2], axis=0), axis=1)
    expected = (speeds[:-1] + speeds[1:]) / 2.0 * TIME_STEP_S
    assert (moved <= expected + 1e-6).all()
    assert np.allclose(moved, expected, rtol=1e-3, atol=1e-3)
    # The last part-step is dropped rather than stretched, so the track can finish up to one
    # step short of the end of the line and never beyond it.
    assert route.distance_m - route.speed_mps * TIME_STEP_S <= moved.sum() <= route.distance_m


def test_the_car_slows_for_a_turn_and_speeds_up_again() -> None:
    """A 90° junction taken at the speed limit teaches an agent that it can do the same."""
    model = _corner()
    line = route_polyline(model=model, route_lanes=("n", "e"), lane_changes=())
    _, travelled, speed = speed_profile(line, cruise_mps=10.0)
    assert speed[0] == pytest.approx(10.0), "the car does not start at the cruising speed"
    assert speed[-1] == pytest.approx(10.0), "the car never picks up again after the turn"
    slowest = int(speed.argmin())
    assert speed[slowest] < 6.0, "the car does not slow for the turn at all"
    # And it slows *before* the turn rather than at it, which is what the two passes buy.
    braking = travelled[slowest] - travelled[int(np.argmax(speed < 9.9))]
    assert braking > 5.0


def test_the_recorded_car_is_never_asked_to_do_the_impossible() -> None:
    """Every number in the track is differentiated by something downstream.

    MetaDrive turns positions into an angular velocity, an IDM ego is scored against the
    line, and a training agent learns from both. A track that corners at 11 g reads as a
    valid recording to all of them.
    """
    model = _corner()
    route = _plan(model, "n", "e")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    state = ego_track(route=route, polyline=line)["state"]
    speed = np.linalg.norm(state["velocity"], axis=1)
    turn = np.abs((np.diff(state["heading"]) + np.pi) % (2 * np.pi) - np.pi)

    assert np.degrees(turn).max() < MAX_VERTEX_TURN_DEG
    # Measured over a 0.1 s step, which is coarser than the profile is worked out on, so a
    # curvature peak reads a little high - hence the margin rather than the bare cap.
    assert (turn / TIME_STEP_S * speed[:-1]).max() < LATERAL_ACCEL_MPS2 * 1.2
    along = np.diff(speed) / TIME_STEP_S
    assert along.max() <= ACCEL_MPS2 + 1e-6
    assert along.min() >= -BRAKE_MPS2 - 1e-6


def _pair(
    first_end: float, second_start: float, *, connector: list[Point2D] | None = None
) -> PreliminaryLaneModel:
    """`p` continues straight into `q`, with whatever gap between them is asked for.

    A continuation rather than a junction unless `connector` is given, so there is no
    crossing to bridge the gap - which is the shape a hole actually takes.
    """
    p = _lane("p", 0.0, first_end, exit_lanes=["q"])
    q = _lane(
        "q", second_start, second_start + 100.0, entry_lanes=["p"], source_edge=["2", "3", "0"]
    )
    connectors = []
    if connector is not None:
        connectors.append(
            ConnectorFeature(
                identifier="pq",
                junction_node_id="900",
                from_lane_id="p",
                to_lane_id="q",
                from_way_id="200",
                to_way_id="201",
                movement="through",
                turn_angle_degrees=0.0,
                status="active",
                centerline=connector,
                polygon=_surface(first_end - 3.0, first_end),
            ).model_dump()
        )
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [p.model_dump(), q.model_dump()],
            "connectors": connectors,
        }
    )


def test_a_hole_between_two_lanes_is_refused() -> None:
    """A gap at a join is invisible once the track is resampled.

    Every sample ends up a fixed distance apart, so a hole does not survive as a jump - it
    becomes a smooth line driven across open ground, and nothing downstream objects. The
    join is the only place it can be seen.
    """
    model = _pair(100.0, 300.0)
    with pytest.raises(RouteError, match="200 m gap before lane q"):
        route_polyline(model=model, route_lanes=("p", "q"), lane_changes=())


def test_a_single_long_straight_lane_is_not_mistaken_for_a_hole() -> None:
    """Lane polylines here carry two or three points, so 400 m between two of them is a road.

    Checking step length instead of the joins would refuse `junction-1`'s own geometry: an
    earlier version of this did exactly that, and rejected a route whose first lane is one
    straight 155 m segment.
    """
    model = _pair(400.0, 400.0)
    line = route_polyline(model=model, route_lanes=("p", "q"), lane_changes=())
    longest = float(np.linalg.norm(np.diff(line, axis=0), axis=1).max())
    assert longest == pytest.approx(400.0)
    assert longest > MAX_JOIN_M * 10


def test_a_route_too_short_to_drive_is_refused() -> None:
    """Below 2 m `_is_arrive_destination` is true on the first frame: the car counts as static."""
    model = _chain()
    route = _plan(model, "a", "d")
    stub = route.__class__(**{**route.__dict__, "distance_m": 0.5})
    with pytest.raises(RouteError, match="static"):
        ego_track(route=stub, polyline=np.array([[0.0, 0.0], [0.5, 0.0]]))


def test_the_summary_says_the_route_was_generated() -> None:
    """Nothing in the OSM says a car drove here, and the pickle should not imply one did."""
    summary = route_summary(_plan(_chain(), "a2", "d"))
    assert summary["source"] == "generated"
    assert summary["lane_changes"] == 1
    assert summary["junction_movements"] == 2
    # The drive slows for its turns, so one speed does not describe it and the duration is
    # longer than distance over the cruising speed. Both figures are reported for exactly
    # that reason - a reader who divides one by the other should be able to see why.
    assert summary["slowest_kph"] <= summary["speed_kph"]
    cruising = summary["distance_m"] / (summary["speed_kph"] / 3.6)
    # Both figures are rounded for reading, so they meet to the rounding rather than exactly.
    assert summary["duration_s"] >= cruising - 0.01
