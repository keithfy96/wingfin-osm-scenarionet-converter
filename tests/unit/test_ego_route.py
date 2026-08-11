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
    MAX_JOIN_M,
    TIME_STEP_S,
    RouteError,
    ego_track,
    plan_route,
    route_polyline,
    route_summary,
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


def test_a_junction_is_crossed_by_the_connector_rather_than_cut_across() -> None:
    """Without the connector the car drives the straight line between two lanes.

    Here that is the difference between 10 m of gap and the connector's 15.6 m bulge, and
    on a real turn it is the difference between the road and the kerb.
    """
    model = _chain()
    route = _plan(model, "a", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    apex = np.array([105.0, 6.0])
    assert np.isclose(line, apex).all(axis=1).any(), "the connector's apex is not on the route"


def test_a_lane_change_crosses_over_rather_than_jumping_sideways() -> None:
    """Concatenating two parallel centrelines would step a lane width in zero distance."""
    model = _chain()
    route = _plan(model, "a2", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    steps = np.linalg.norm(np.diff(line, axis=0), axis=1)
    lateral = np.abs(np.diff(line[:, 1]))
    crossing = lateral > WIDTH / 2
    assert crossing.any(), "the route never leaves the lane it started in"
    # Every metre of lateral movement is paid for with at least a metre of travel.
    assert (steps[crossing] > lateral[crossing]).all()


def test_the_track_is_sampled_at_metadrives_own_step() -> None:
    """0.1 s is what `parse_object_state` assumes when it differentiates positions."""
    model = _chain()
    route = _plan(model, "a", "d")
    line = route_polyline(model=model, route_lanes=route.lanes, lane_changes=route.lane_changes)
    track = ego_track(route=route, polyline=line)
    steps = np.linalg.norm(np.diff(track["state"]["position"][:, :2], axis=0), axis=1)
    # 36 kph is 10 m/s, so 0.1 s is 1 m. Corners shorten the chord slightly.
    assert steps.max() <= route.speed_mps * TIME_STEP_S + 1e-9
    assert len(track["state"]["position"]) == math.ceil(route.distance_m / steps.max()) + 1


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
    assert np.allclose(speeds, route.speed_mps)

    # Samples sit a fixed distance apart *along* the route, so the straight line between two
    # of them is that distance on a straight and slightly less through a bend. Never more:
    # a sample further apart than the recorded speed allows would be a car outrunning its
    # own velocity.
    spacing = route.speed_mps * TIME_STEP_S
    moved = np.linalg.norm(np.diff(state["position"][:, :2], axis=0), axis=1)
    assert moved.max() <= spacing + 1e-9
    assert moved.sum() == pytest.approx(route.distance_m, rel=1e-3)


def _pair(first_end: float, second_start: float) -> PreliminaryLaneModel:
    """`p` continues straight into `q`, with whatever gap between them is asked for.

    A continuation rather than a junction, so there is no connector to bridge it - which is
    the shape a hole actually takes.
    """
    p = _lane("p", 0.0, first_end, exit_lanes=["q"])
    q = _lane(
        "q", second_start, second_start + 100.0, entry_lanes=["p"], source_edge=["2", "3", "0"]
    )
    return PreliminaryLaneModel.model_validate(
        {"metadata": _METADATA, "lanes": [p.model_dump(), q.model_dump()]}
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
    # Both are rounded for reading, so they agree to the rounding rather than exactly.
    assert summary["duration_s"] == pytest.approx(summary["distance_m"] / 10.0, abs=0.01)
