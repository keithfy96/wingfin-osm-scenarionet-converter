"""`tools/traffic.py` - the half that is testable without an engine.

Like `test_step_timing.py`, this reaches into `tools/` from this repo's 3.10 while the module
itself runs on MetaDrive's 3.8, so anything needing a simulator is out of reach: whether cars
crash is a real drive and cannot be asserted here. What *is* reachable is the arithmetic that
decides where a car is put, and it is worth reaching for - a heading taken across a vertex
rather than along a segment points the car somewhere it is not going, and nothing raises.

`traffic.py` imports MetaDrive lazily, inside `build_manager`, precisely so this import works.
"""

import ast
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from traffic import (  # noqa: E402
    CONFLICT_WIDTH_M,
    END_MARGIN_M,
    LOST_LATERAL_M,
    MIN_GAP_M,
    STOP_MARGIN_M,
    TRAFFIC_VERSION,
    YIELD_LOOKAHEAD_M,
    YIELD_SAMPLE_M,
    TrafficError,
    _conflict,
    _cumulative,
    _look_ahead,
    _pose_at,
    _yield_brake,
    load_plan,
)


def _plan_file(tmp_path: Path, **update) -> Path:
    plan = {
        "traffic_version": TRAFFIC_VERSION,
        "identity": {"generation_fingerprint": "f", "reviewed_lane_model_sha256": "s"},
        "routes": [
            {
                "name": "traffic-000",
                "start_lane": "a",
                "end_lane": "b",
                "distance_m": 100.0,
                "speed_mps": 13.9,
                "polyline": [[0.0, 0.0], [100.0, 0.0]],
            }
        ],
    }
    plan.update(update)
    path = tmp_path / "traffic.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


# --- reading the plan ---------------------------------------------------------------------


def test_a_plan_from_this_writer_is_accepted(tmp_path: Path) -> None:
    assert len(load_plan(_plan_file(tmp_path))["routes"]) == 1


def test_a_plan_from_a_future_writer_is_refused_rather_than_guessed_at(tmp_path: Path) -> None:
    with pytest.raises(TrafficError, match="this reader understands"):
        load_plan(_plan_file(tmp_path, traffic_version=TRAFFIC_VERSION + 1))


def test_a_route_with_one_point_is_refused(tmp_path: Path) -> None:
    """A single point is not a line to drive along, and `PointLane` does not say so.

    It is built from whatever array it is handed; the failure arrives later, as a car that
    sits still or a heading taken from a zero-length segment.
    """
    broken = [{"name": "t", "start_lane": "a", "end_lane": "b", "polyline": [[0.0, 0.0]]}]
    with pytest.raises(TrafficError, match="no polyline to drive along"):
        load_plan(_plan_file(tmp_path, routes=broken))


def test_an_empty_plan_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TrafficError, match="contains no routes"):
        load_plan(_plan_file(tmp_path, routes=[]))


# --- putting a car somewhere --------------------------------------------------------------


def test_distance_along_a_line_is_measured_not_counted() -> None:
    assert _cumulative([(0.0, 0.0), (3.0, 4.0), (3.0, 9.0)]) == [0.0, 5.0, 10.0]


def test_a_pose_partway_along_a_segment_is_interpolated() -> None:
    points = [(0.0, 0.0), (10.0, 0.0)]
    position, heading = _pose_at(points, _cumulative(points), 2.5)
    assert position == pytest.approx((2.5, 0.0))
    assert heading == pytest.approx(0.0)


def test_the_heading_at_a_corner_is_the_segment_the_car_is_on() -> None:
    """Not a difference across the vertex, which is the average of two directions.

    A car sitting exactly on the corner of an L is travelling along one arm of it, and the
    bisector points into the verge. `atan2` over the *segment* is what says which arm.
    """
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    lengths = _cumulative(points)
    _, before = _pose_at(points, lengths, 9.9)
    _, after = _pose_at(points, lengths, 10.1)
    assert before == pytest.approx(0.0)
    assert after == pytest.approx(math.pi / 2)


def test_a_distance_past_the_end_lands_on_the_end() -> None:
    """Clamped rather than extrapolated. A placement is chosen inside the line's length, so
    reaching here means an off-by-one somewhere - and extrapolating would put the car off the
    end of the road with no sign that anything went wrong."""
    points = [(0.0, 0.0), (10.0, 0.0)]
    position, _ = _pose_at(points, _cumulative(points), 999.0)
    assert position == pytest.approx((10.0, 0.0))


def test_a_negative_distance_lands_on_the_start() -> None:
    points = [(0.0, 0.0), (10.0, 0.0)]
    position, _ = _pose_at(points, _cumulative(points), -5.0)
    assert position == pytest.approx((0.0, 0.0))


def test_a_zero_length_segment_does_not_divide_by_it() -> None:
    """`route_polyline` leaves repeated vertices a fraction of a micrometre apart, which is
    `ego_route.COINCIDENT_M`'s whole subject. Landing on one must not be a ZeroDivisionError."""
    points = [(0.0, 0.0), (0.0, 0.0), (10.0, 0.0)]
    position, _ = _pose_at(points, _cumulative(points), 0.0)
    assert position == pytest.approx((0.0, 0.0))


def test_the_end_margin_clears_the_radius_the_policy_retires_a_car_in() -> None:
    """`TrajectoryIDMPolicy.DEST_REGION_RADIUS` is 2 m. A car placed inside it is cleared on
    the frame it appears, so the margin has to be comfortably outside - and comfortably is
    measured against `MIN_GAP_M`, or a replacement could be placed into the same trap."""
    assert END_MARGIN_M > 2.0
    assert END_MARGIN_M > MIN_GAP_M


# --- the frame the routes are read in ---------------------------------------------------


def test_the_manager_moves_the_plan_into_the_episodes_own_frame() -> None:
    """`traffic.json` is written in the scenario file's frame; MetaDrive loads every scenario
    with `centralize=True`, which moves the world so the recorded car starts at the origin.

    Read at source rather than driven, because the manager needs an engine to exist. Measured
    on `junction-1` when this was missing: the shift is `[55.725, -75.469]`, so every car was
    placed 93.8 m from the road it was computed for - all 10 of 10 off the tarmac, a median
    47.7 m clear of it - which is the whole of "the cars drive around on the grass".
    """
    source = (REPO / "tools" / "traffic.py").read_text(encoding="utf-8")
    assert "old_origin_in_current_coordinate" in source

    tree = ast.parse(source)
    after_reset = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "after_reset"
    )
    called = [
        node.func.attr
        for node in ast.walk(after_reset)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "_localised_routes" in called, "after_reset must re-read the shift every episode"
    assert called.index("_localised_routes") < called.index("_choose_placements"), (
        "the routes must be moved into this episode's frame before any car is placed on them"
    )


def test_a_car_is_retired_when_it_passes_the_end_not_only_when_it_lands_on_it() -> None:
    """`TrajectoryIDMPolicy.arrive_destination` is a 2 m circle around the trajectory's last
    point, so a car that arrives even slightly wide never enters it - and `steering_control`
    then asks `heading_theta_at(long + 1)`, which clamps to the final segment, so the car
    drives dead straight for ever.

    Measured on `junction-1` before this test existed, three episodes of 25 cars: 36 cars ran
    past their last point and stayed, against 27 retired by the circle, two of them reaching
    245 m and 131 m clear of any road. Adding it took the worst distance a car reached off
    the tarmac from 244.85 m to 7.23 m.
    """
    source = (REPO / "tools" / "traffic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    before_step = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "before_step"
    )
    tests = [
        node.attr
        for node in ast.walk(before_step)
        if isinstance(node, ast.Attribute) and node.attr in ("arrive_destination",)
    ]
    calls = [
        node.func.attr
        for node in ast.walk(before_step)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert tests, "the policy's own arrival test must still be consulted"
    assert "_past_the_end" in calls, (
        "a car that reaches the end of its route wide is never retired by the 2 m circle"
    )
    assert "DEST_REGION_RADIUS" in source, (
        "the along-the-route margin must be the policy's own radius, not a new constant"
    )


def test_traffic_gives_way_to_the_ego_and_the_ego_is_never_the_one_braked() -> None:
    """Traffic cannot see the ego through the plan - the ego is not in it - and 5 of 16
    collisions measured over four `junction-1` episodes were with it.

    The ego is never handed a brake: under `--agent-policy replay` it is a tape and cannot
    yield at all, and under any other policy it brakes for its own reasons.
    """
    source = (REPO / "tools" / "traffic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    yielders = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_yielders"
    )
    calls = [
        node.func.attr
        for node in ast.walk(yielders)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "_ego_look_ahead" in calls
    ego_look_ahead = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_ego_look_ahead"
    )
    # Extrapolated, never read off the recorded track: the tape is the ego's future only
    # while it is being replayed, and idm / manual / remote all drive it somewhere else.
    assert "tracks" not in ast.dump(ego_look_ahead)
    assert "heading_theta" in ast.dump(ego_look_ahead)


# --- giving way -------------------------------------------------------------------------


def _line(points: list[tuple[float, float]], distance: float = 0.0):
    return _look_ahead(points, _cumulative(points), distance)


def test_a_look_ahead_is_sampled_along_the_route_and_stops_at_its_end() -> None:
    samples = _line([(0.0, 0.0), (10.0, 0.0)])
    assert len(samples) == int(10.0 / YIELD_SAMPLE_M) + 1
    assert samples[0][:2] == pytest.approx((0.0, 0.0))
    assert samples[-1][:2] == pytest.approx((10.0, 0.0))
    assert samples[:, 3] == pytest.approx(0.0)


def test_a_look_ahead_reaches_no_further_than_the_horizon() -> None:
    samples = _line([(0.0, 0.0), (1000.0, 0.0)])
    assert samples[-1][2] <= YIELD_LOOKAHEAD_M + 1e-9


def test_two_paths_that_cross_are_a_conflict_at_the_crossing() -> None:
    mine = _line([(-20.0, 0.0), (20.0, 0.0)])
    theirs = _line([(10.0, -20.0), (10.0, 20.0)])
    found = _conflict(mine, theirs)
    assert found is not None
    my_distance, their_distance = found
    # The crossing is 30 m along mine (starting at x=-20) and 20 m along theirs.
    assert my_distance == pytest.approx(30.0, abs=CONFLICT_WIDTH_M)
    assert their_distance == pytest.approx(20.0, abs=CONFLICT_WIDTH_M)


def test_two_cars_on_the_same_road_are_not_a_conflict() -> None:
    """The one case that must never be read as a crossing.

    A follower and its leader share the tarmac for the whole look-ahead, so a rule that
    called that a conflict would have each of them waiting for the other for ever. IDM
    already owns it: `get_find_front_back_objs_single_lane` tests `point_on_lane` on the
    other car's bounding box, so it sees anything ahead on its own lane whatever route that
    car is following.
    """
    assert _conflict(_line([(0.0, 0.0), (100.0, 0.0)], 0.0),
                     _line([(0.0, 0.0), (100.0, 0.0)], 20.0)) is None


def test_a_shallow_merge_is_not_a_conflict_either() -> None:
    mine = _line([(0.0, 0.0), (100.0, 0.0)])
    theirs = _line([(0.0, -3.0), (100.0, 0.5)])   # about 2 degrees
    assert _conflict(mine, theirs) is None


def test_paths_that_never_come_near_each_other_are_not_a_conflict() -> None:
    assert _conflict(_line([(0.0, 0.0), (40.0, 0.0)]),
                     _line([(0.0, 50.0), (40.0, 50.0)])) is None


def test_giving_way_can_only_ever_slow_a_car_down() -> None:
    for speed in (0.0, 5.0, 14.0):
        for room in (0.5, 3.0, 10.0, 40.0):
            assert -1.0 <= _yield_brake(speed, room) <= 0.0


def test_a_car_already_at_the_crossing_brakes_as_hard_as_it_can() -> None:
    assert _yield_brake(10.0, STOP_MARGIN_M) == -1.0
    assert _yield_brake(10.0, 0.0) == -1.0


def test_a_crossing_further_off_is_braked_for_more_gently() -> None:
    near = _yield_brake(10.0, STOP_MARGIN_M + 5.0)
    far = _yield_brake(10.0, STOP_MARGIN_M + 40.0)
    assert near < far <= 0.0


def test_a_stationary_car_is_not_asked_to_brake() -> None:
    """Its own speed is what sizes the braking, so a car that has already stopped for a
    crossing asks for nothing more - the yield holds it there by capping IDM's throttle,
    which `before_step` does by taking the smaller of the two."""
    assert _yield_brake(0.0, 10.0) == 0.0


# --- the speed profile, and the car that has lost its route ------------------------------


def test_a_plan_without_a_speed_profile_is_refused_by_name(tmp_path: Path) -> None:
    """A version 1 file loads and drives - every car simply takes every corner at MetaDrive's
    flat 40 km/h, which is the fault the profile exists to fix.

    So it is refused, and the message says what is missing and how to rebuild it rather than
    quoting two version numbers at a reader who has no reason to know what they mean.
    """
    stale = tmp_path / "traffic.json"
    stale.write_text(
        json.dumps(
            {
                "traffic_version": 1,
                "routes": [{"name": "a", "polyline": [[0.0, 0.0], [10.0, 0.0]]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TrafficError) as raised:
        load_plan(stale)
    message = str(raised.value)
    assert "speed profile" in message
    assert "osm-scenario traffic" in message


def test_the_lost_car_threshold_clears_a_junction_swing() -> None:
    """5 m is a lane and a half, which nothing driving its own route needs.

    Measured over three `junction-1` episodes with the profile in force: the lateral error is
    0.11 m at the median and 1.80 m at p90, with 7 excursions past 5 m against 11 past 3 m -
    so 3 m would pick up cars still going round a junction.
    """
    assert LOST_LATERAL_M > 3.0
    assert LOST_LATERAL_M >= CONFLICT_WIDTH_M


def test_the_speed_profile_is_applied_before_the_policy_decides() -> None:
    """`policy.target_speed` is read inside `act()`, so setting it afterwards would take
    effect a step late - and `act` only computes an acceleration on one step in five
    (`IDM_ACT_BATCH_SIZE`), so a step late is up to half a second late into a corner."""
    source = (REPO / "tools" / "traffic.py").read_text(encoding="utf-8")
    before_step = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "before_step"
    )
    body = ast.dump(before_step)
    assert "target_speed" in body
    assert body.index("target_speed") < body.index("'act'"), (
        "target_speed must be set before policy.act() reads it"
    )


def test_a_lost_car_is_not_counted_as_a_completed_route() -> None:
    """A car picked up off the grass did not complete a route.

    Counting it as one would hide the fault behind the throughput number that is used to check
    a slowing rule has not gridlocked the map.
    """
    source = (REPO / "tools" / "traffic.py").read_text(encoding="utf-8")
    after_step = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "after_step"
    )
    body = ast.dump(after_step)
    assert "cars_lost" in body
    assert "cars_retired" in body
