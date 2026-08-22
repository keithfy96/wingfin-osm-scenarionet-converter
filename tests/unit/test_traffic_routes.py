"""Where other cars are allowed to appear, and the line they drive along.

Two things here are silent when they are wrong, which is why they are asserted rather than
looked at. A car spawned on a starved lane appears *inside* a junction, on tarmac other
traffic is crossing, and nothing raises - it is a legal position on a legal lane. And a
polyline simplified too hard still loads, still drives, and merely turns more sharply than
the geometry it was cut from; the gate is a number, so it can be checked.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from osm_scenario.conversion import _lane_neighbours
from osm_scenario.ego_route import COINCIDENT_M
from osm_scenario.lane_model import ConnectorFeature, LaneFeature, Point2D, PreliminaryLaneModel
from osm_scenario.traffic_routes import (
    MIN_ROUTE_M,
    POLYLINE_TOLERANCE_M,
    TrafficError,
    _simplify,
    entry_lanes,
    exit_lanes,
    payload,
    plan_traffic,
)

_METADATA = {
    "generator_version": "test",
    "lane_model_schema_version": 1,
    "source_checksum": "source",
    "projected_graph_checksum": "graph",
    "configuration_checksum": "config",
    "generation_fingerprint": "fingerprint",
    "coordinate_system_wkt": "EPSG:4326",
}
WIDTH = 4.0

GATE_TURN_DEG = 30.0
"""`tools/check_dataset.py:47`, restated because that module runs on MetaDrive's 3.8 and
cannot be imported from here. `MAX_VERTEX_TURN_DEG` (150 deg) is a different rule, for
lane-to-lane joins whose shape this repo did not choose."""


def _lane(identifier: str, x0: float, x1: float, edge: list[str], **update: Any) -> LaneFeature:
    half = WIDTH / 2
    lane = LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=edge,
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=WIDTH,
        speed_limit_kph=36.0,
        centerline=[Point2D(x=x0, y=0.0), Point2D(x=x1, y=0.0)],
        polygon=[
            Point2D(x=x0, y=-half),
            Point2D(x=x1, y=-half),
            Point2D(x=x1, y=half),
            Point2D(x=x0, y=half),
            Point2D(x=x0, y=-half),
        ],
        boundaries=[],
    )
    return lane.model_copy(update=update) if update else lane


def _model(lanes: list[LaneFeature], connectors: list[ConnectorFeature] | None = None):
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [item.model_dump() for item in lanes],
            "connectors": [item.model_dump() for item in (connectors or [])],
        }
    )


def _straight() -> PreliminaryLaneModel:
    """`a` -> `b` -> `c` along nodes 1,2,3,4. `a` starts the road, `c` ends it."""
    a = _lane("a", 0.0, 100.0, ["1", "2", "0"], exit_lanes=["b"])
    b = _lane("b", 100.0, 200.0, ["2", "3", "0"], entry_lanes=["a"], exit_lanes=["c"])
    c = _lane("c", 200.0, 300.0, ["3", "4", "0"], entry_lanes=["b"])
    return _model([a, b, c])


# --- where a car may appear ---------------------------------------------------------------


def test_a_lane_with_no_feeder_at_the_start_of_a_road_is_an_entry() -> None:
    model = _straight()
    kept, rejected = entry_lanes(model, _lane_neighbours(model))
    assert kept == ["a"]
    assert rejected == []


def test_a_starved_lane_inside_a_junction_is_not_an_entry() -> None:
    """`starved` has no feeder, but `a` ends at the same node it starts from.

    That is a lane the allocation left unfed in the middle of a junction, not a road the
    extract cut. A car appearing there appears on tarmac `a`'s traffic is crossing - which
    raises nothing, because it is a legal position on a legal lane.
    """
    lanes = list(_straight().lanes)
    starved = _lane("starved", 100.0, 200.0, ["2", "9", "0"])
    model = _model([*lanes, starved])
    kept, rejected = entry_lanes(model, _lane_neighbours(model))
    assert kept == ["a"]
    assert [lane_id for lane_id, _ in rejected] == ["starved"]
    assert "node 2" in rejected[0][1]


def test_a_lane_fed_only_through_a_forbidden_connector_is_still_an_entry() -> None:
    """A movement the review forbade is not a feeder.

    `_lane_neighbours` already drops a connector that is not `active`, so the lane really
    has nothing arriving on it. The node test then has to agree, or the lane would be
    counted as fed by a movement that no longer exists.
    """
    a = _lane("a", 0.0, 100.0, ["1", "2", "0"], exit_lanes=["k"])
    b = _lane("b", 110.0, 210.0, ["7", "8", "0"], entry_lanes=["k"])
    connector = ConnectorFeature(
        identifier="k",
        junction_node_id="900",
        from_lane_id="a",
        to_lane_id="b",
        from_way_id="200",
        to_way_id="201",
        movement="through",
        turn_angle_degrees=0.0,
        status="forbidden",
        centerline=[Point2D(x=100.0, y=0.0), Point2D(x=110.0, y=0.0)],
        polygon=[Point2D(x=100.0, y=-2.0), Point2D(x=110.0, y=-2.0), Point2D(x=110.0, y=2.0)],
    )
    model = _model([a, b], [connector])
    kept, _ = entry_lanes(model, _lane_neighbours(model))
    assert set(kept) == {"a", "b"}


def test_a_backward_lane_starts_at_the_far_end_of_its_own_edge() -> None:
    """`direction` decides which end of `source_edge` is upstream.

    Read the wrong way round, a backward lane's entry test asks about the node it drives
    *towards*, and the answer is a different lane's business entirely.
    """
    forward = _lane("f", 0.0, 100.0, ["1", "2", "0"])
    backward = _lane("b", 0.0, 100.0, ["2", "1", "0"], direction="backward")
    model = _model([forward, backward])
    kept, rejected = entry_lanes(model, _lane_neighbours(model))
    # Both run from node 1 to node 2, so neither has a road ending where it begins.
    assert set(kept) == {"f", "b"}
    assert rejected == []


def test_a_lane_with_nowhere_to_go_is_an_exit_however_it_got_there() -> None:
    """No node test on the exit side, deliberately: nothing *appears* there.

    A lane that leads nowhere is a fine place to stop whether the extract cut the road or
    the allocation left it with no exit, so this list is longer than the entry list and
    that is correct rather than a bug in the symmetry.
    """
    lanes = list(_straight().lanes)
    dead_end = _lane("dead", 100.0, 200.0, ["2", "9", "0"], entry_lanes=["a"])
    model = _model([*lanes, dead_end])
    assert set(exit_lanes(model, _lane_neighbours(model))) == {"c", "dead"}


# --- the pool -----------------------------------------------------------------------------


def test_a_pool_runs_from_an_entry_to_an_exit() -> None:
    plan = plan_traffic(model=_straight(), count=5, seed=0)
    assert [route.start_lane for route in plan.routes] == ["a"]
    assert [route.end_lane for route in plan.routes] == ["c"]
    assert plan.routes[0].distance_m > MIN_ROUTE_M


def test_a_map_with_nowhere_to_appear_is_refused_rather_than_left_empty() -> None:
    """A ring road: every lane is fed, so there is no honest place to put a car."""
    a = _lane("a", 0.0, 100.0, ["1", "2", "0"], entry_lanes=["b"], exit_lanes=["b"])
    b = _lane("b", 100.0, 200.0, ["2", "1", "0"], entry_lanes=["a"], exit_lanes=["a"])
    with pytest.raises(TrafficError, match="no lane in this model is a place a car may appear"):
        plan_traffic(model=_model([a, b]), count=5, seed=0)


def test_two_seeds_choose_different_pools_on_a_map_with_a_choice() -> None:
    model = _real_model()
    if model is None:
        pytest.skip("workspaces/junction-1 is not present")
    one = plan_traffic(model=model, count=12, seed=1)
    two = plan_traffic(model=model, count=12, seed=2)
    pairs = {(route.start_lane, route.end_lane) for route in one.routes}
    other = {(route.start_lane, route.end_lane) for route in two.routes}
    assert pairs != other


def test_the_same_seed_gives_the_same_pool() -> None:
    model = _real_model()
    if model is None:
        pytest.skip("workspaces/junction-1 is not present")
    one = plan_traffic(model=model, count=8, seed=3)
    two = plan_traffic(model=model, count=8, seed=3)
    assert [route.start_lane for route in one.routes] == [
        route.start_lane for route in two.routes
    ]
    assert np.array_equal(one.routes[0].polyline, two.routes[0].polyline)


# --- simplifying the line -----------------------------------------------------------------


def test_simplifying_keeps_the_ends_and_drops_the_middle_of_a_straight() -> None:
    line = np.array([[float(x), 0.0] for x in range(11)])
    assert np.array_equal(_simplify(line), np.array([[0.0, 0.0], [10.0, 0.0]]))


def test_simplifying_keeps_a_corner_that_matters() -> None:
    line = np.array([[0.0, 0.0], [5.0, 0.0], [5.0, 5.0]])
    assert np.array_equal(_simplify(line), line)


def test_simplifying_never_moves_a_point_further_than_the_tolerance() -> None:
    """Every dropped vertex must be within the tolerance of the line that replaces it.

    Checked directly rather than trusting the recursion: an off-by-one in the split index
    keeps the wrong vertex, and the result is still a plausible-looking polyline.
    """
    rng = np.random.default_rng(0)
    line = np.cumsum(rng.normal(scale=0.4, size=(400, 2)), axis=0)
    kept = _simplify(line)
    assert len(kept) < len(line)
    for point in line:
        span = kept[1:] - kept[:-1]
        length = np.hypot(span[:, 0], span[:, 1])
        length[length < 1e-12] = 1e-12
        along = np.clip(((point - kept[:-1]) * span).sum(axis=1) / length**2, 0.0, 1.0)
        nearest = kept[:-1] + along[:, None] * span
        assert np.hypot(*(point - nearest).T).min() <= POLYLINE_TOLERANCE_M + 1e-9


# --- the real map -------------------------------------------------------------------------


def _real_model() -> PreliminaryLaneModel | None:
    """`junction-1`'s reviewed model, when the workspace is there.

    `workspaces/` is gitignored, so this is absent on a clean checkout and the sweeps that
    use it skip - the same arrangement `test_ego_route` makes. A fixture cannot stand in:
    what is being swept is the awkward geometry a real map has and a built one does not.
    """
    path = Path(__file__).resolve().parents[2] / "workspaces/junction-1/lane-model/reviewed.json"
    if not path.exists():
        return None
    return PreliminaryLaneModel.model_validate(json.loads(path.read_text()))


def _worst_vertex_turn(polyline: np.ndarray) -> float:
    """The sharpest corner on a line, in degrees.

    Steps shorter than `COINCIDENT_M` are skipped for that constant's own reason: shapely
    and the curve builders leave vertices a fraction of a micrometre apart, and a bearing
    taken over 78 um is noise that reads as an exact right angle.
    """
    span = np.diff(polyline, axis=0)
    long_enough = np.hypot(span[:, 0], span[:, 1]) > COINCIDENT_M
    bearing = np.degrees(np.arctan2(span[:, 1], span[:, 0]))
    turn = np.abs((np.diff(bearing) + 180.0) % 360.0 - 180.0)
    usable = turn[long_enough[:-1] & long_enough[1:]]
    return float(usable.max()) if len(usable) else 0.0


def test_no_traffic_route_on_the_real_map_turns_more_than_the_gate_allows() -> None:
    """The same 30 deg gate `tools/check_dataset.py` holds the recorded car to.

    Traffic is steered by MetaDrive's IDM rather than replayed, so nothing *forces* the car
    onto a corner this sharp - but a polyline it cannot follow is one it will cut, and the
    cut is silent. Measured over 60 routes on `junction-1`: worst 18.3 deg before
    simplifying and 18.5 deg after, which is what pins `POLYLINE_TOLERANCE_M` at 5 mm.
    """
    model = _real_model()
    if model is None:
        pytest.skip("workspaces/junction-1 is not present")
    plan = plan_traffic(model=model, count=60, seed=1)
    worst = max(_worst_vertex_turn(route.polyline) for route in plan.routes)
    assert worst < GATE_TURN_DEG, f"worst vertex turn {worst:.1f} deg"
    # And the pool has to still be there. A version of this that reached zero by refusing
    # routes the map permits would pass the line above and be worse than what it replaced.
    assert len(plan.routes) == 60


def test_every_traffic_route_starts_where_the_pool_says_it_may() -> None:
    model = _real_model()
    if model is None:
        pytest.skip("workspaces/junction-1 is not present")
    plan = plan_traffic(model=model, count=30, seed=1)
    assert {route.start_lane for route in plan.routes} <= set(plan.entries)
    assert {route.end_lane for route in plan.routes} <= set(plan.exits)


def test_the_written_file_is_bound_to_the_lane_model_it_was_built_from() -> None:
    """Lane ids are content addressed, so a pool used on another map names lanes that exist
    somewhere else rather than failing. The identity block is what makes that a refusal."""
    model = _real_model()
    if model is None:
        pytest.skip("workspaces/junction-1 is not present")
    plan = plan_traffic(model=model, count=3, seed=1)
    written = payload(plan, model=model, model_sha256="abc", seed=1)
    assert written["identity"] == {
        "generation_fingerprint": model.metadata.generation_fingerprint,
        "reviewed_lane_model_sha256": "abc",
    }
    assert written["traffic_version"] == 1
    assert len(written["routes"]) == 3
    assert math.isfinite(written["routes"][0]["distance_m"])
