from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString

from osm_scenario.acquisition import acquire_osm
from osm_scenario.config import ConverterConfig
from osm_scenario.generation import (
    GenerationError,
    _balanced_approach_assignment,
    _balanced_merge_assignment,
    _carries_whole_carriageway,
    _direction_arrow,
    _directional_lane_count,
    _is_decision_node,
    _lane_offset,
    _mapped_lane_index,
    _merge_taper_plan,
    _side_filtered_candidates,
    _speed_kph,
    _stranded_permission_fallback,
    _tapered_line,
    _turn_permissions,
    _unproven_sharp_movement,
    generate_lane_model,
)
from osm_scenario.lane_model import (
    ConnectorFeature,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
)
from osm_scenario.normalization import normalize_workspace
from osm_scenario.topology import MovementCandidate

FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    acquire_osm(workspace=workspace, driving_side="left", osm_file=FIXTURE)
    normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))
    return workspace


def test_direction_arrow_points_downstream_from_the_centreline_midpoint() -> None:
    line = [Point2D(x=0.0, y=0.0), Point2D(x=20.0, y=0.0)]
    back_left, tip, back_right = _direction_arrow(line, 3.5)

    # The tip sits ahead of the midpoint, both barbs behind it, and the barbs
    # straddle the centreline symmetrically.
    assert tip.x > 10.0 > back_left.x
    assert back_left.x == back_right.x
    assert back_left.y == pytest.approx(-back_right.y)
    assert back_left.y != 0.0
    assert tip.y == back_left.y + back_right.y == 0.0

    # Reversing the lane reverses the arrow rather than mirroring it.
    reversed_tip = _direction_arrow([Point2D(x=20.0, y=0.0), Point2D(x=0.0, y=0.0)], 3.5)[1]
    assert reversed_tip.x < 10.0

    # The arrow never outgrows a very short lane, and a degenerate one has none.
    short = _direction_arrow([Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0)], 3.5)
    assert short[0].x >= 0.0 and short[2].x <= 1.0
    assert _direction_arrow([Point2D(x=5.0, y=5.0), Point2D(x=5.0, y=5.0)], 3.5) is None


def test_directional_lane_count_precedence_and_fallbacks() -> None:
    assert _directional_lane_count({"lanes:forward": "3", "lanes": "4"}, "forward") == (
        3,
        "explicit_directional",
        "high",
    )
    assert _directional_lane_count({"lanes": "3"}, "forward") == (
        1,
        "inferred_from_total",
        "low",
    )
    assert _directional_lane_count({}, "backward") == (1, "default_single_lane", "low")


def test_directional_lane_count_uses_total_minus_opposite_direction() -> None:
    # OSM way 334662874: lanes=4 with lanes:backward=1 leaves 3 forward lanes.
    # Halving the total would silently drop the fourth lane.
    tags = {"lanes": "4", "lanes:backward": "1"}
    assert _directional_lane_count(tags, "forward") == (3, "complementary_directional", "high")
    assert _directional_lane_count(tags, "backward") == (1, "explicit_directional", "high")

    # An odd total is exact once one direction is known, so it is no longer a blocker.
    assert _directional_lane_count({"lanes": "3", "lanes:backward": "2"}, "forward") == (
        1,
        "complementary_directional",
        "high",
    )

    # A one-way total still wins over the opposite-direction tag.
    assert _directional_lane_count(
        {"lanes": "2", "lanes:backward": "1", "oneway": "yes"}, "forward"
    ) == (2, "explicit_total_oneway", "high")

    # Contradictory tagging cannot yield a zero or negative count.
    assert _directional_lane_count({"lanes": "2", "lanes:backward": "2"}, "forward") == (
        1,
        "contradictory_directional_total",
        "low",
    )


def test_speed_parser_preserves_kph_and_converts_mph() -> None:
    assert _speed_kph("50 km/h") == 50
    assert _speed_kph("30 mph") == pytest.approx(48.28032)
    assert _speed_kph("signals") is None


def test_turn_lane_tags_follow_osm_left_to_right_order() -> None:
    tags = {"turn:lanes": "left|through|through;reverse"}
    assert _turn_permissions(tags, "forward", 0, 3, "left") == [
        "reverse",
        "through",
    ]
    assert _turn_permissions(tags, "forward", 0, 3, "right") == ["left"]


def test_lane_order_mapping_is_deterministic_across_lane_count_changes() -> None:
    class SourceLane:
        lane_count = 3

        def __init__(self, lane_index: int) -> None:
            self.lane_index = lane_index

    assert [_mapped_lane_index(SourceLane(index), 2) for index in range(3)] == [0, 0, 1]
    # Without a side the proportional mapping is unchanged by the new parameter.
    assert [_mapped_lane_index(SourceLane(index), 2, None) for index in range(3)] == [0, 0, 1]


def test_side_movements_select_the_kerbside_or_median_lane_of_the_target() -> None:
    class SourceLane:
        def __init__(self, lane_index: int, lane_count: int = 3) -> None:
            self.lane_index = lane_index
            self.lane_count = lane_count

    # Indices run centre-out, so the kerbside lane is the last one and the median is 0.
    assert _mapped_lane_index(SourceLane(0), 3, "nearside") == 2
    assert _mapped_lane_index(SourceLane(2), 3, "offside") == 0
    # The side wins regardless of where the movement started.
    assert {_mapped_lane_index(SourceLane(index), 3, "nearside") for index in range(3)} == {2}

    # A one-lane ramp merging onto a two-lane carriageway from the kerb side must not
    # land in the median lane, which is what min(lane_index, target_count - 1) gave.
    ramp = SourceLane(0, lane_count=1)
    assert _mapped_lane_index(ramp, 2) == 0
    assert _mapped_lane_index(ramp, 2, "nearside") == 1
    assert _mapped_lane_index(ramp, 3, "nearside") == 2
    # A single-lane target leaves nothing to choose.
    assert _mapped_lane_index(ramp, 1, "nearside") == 0


def _candidate(to_lane_id: str, movement: str, angle: float) -> MovementCandidate:
    return MovementCandidate(
        junction_node_id="1",
        from_lane_id="src",
        to_lane_id=to_lane_id,
        from_way_id="10",
        to_way_id="20",
        movement=movement,
        angle_degrees=angle,
        centerline=LineString([(0.0, 0.0), (1.0, 1.0)]),
        ambiguous=False,
    )


def _approach(
    lane_index: int, lane_count: int, permissions: list[str] | None = None
) -> LaneFeature:
    return LaneFeature(
        identifier="src",
        source_way_ids=["10"],
        source_edge=["1", "2", "0"],
        lane_index=lane_index,
        lane_count=lane_count,
        direction="forward",
        road_class="tertiary",
        width_m=3.5,
        speed_limit_kph=50.0,
        centerline=[Point2D(x=0.0, y=0.0), Point2D(x=0.0, y=10.0)],
        polygon=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0), Point2D(x=0.0, y=0.0)],
        boundaries=[],
        turn_permissions=permissions or [],
    )


def _filter(
    source: LaneFeature,
    candidates: list[MovementCandidate],
    *,
    has_continuation: bool = False,
) -> list[str]:
    kept = _side_filtered_candidates(
        candidates,
        source=source,
        driving_side="left",
        min_degrees=10.0,
        node_restrictions=[],
        has_continuation=has_continuation,
    )
    return [item.to_lane_id for item in kept]


def test_side_movements_are_only_emitted_from_that_side_of_the_approach() -> None:
    # A slip road leaving at 22 degrees is `through` by classification but plainly a
    # nearside departure, so only the kerbside lane of a two-lane approach may take it.
    exit_ = _candidate("exit", "through", 22.289)
    ahead = _candidate("ahead", "through", 0.469)
    assert _filter(_approach(0, 2), [exit_, ahead]) == ["ahead"]
    assert _filter(_approach(1, 2), [exit_, ahead]) == ["exit", "ahead"]

    # Driving on the left, a right turn is offside and belongs to lane 0.
    right = _candidate("right", "right", -90.0)
    assert _filter(_approach(0, 2), [right, ahead]) == ["right", "ahead"]
    assert _filter(_approach(1, 2), [right, ahead]) == ["ahead"]

    # Straight-ahead movements stay available from every lane.
    assert _filter(_approach(0, 3), [ahead]) == ["ahead"]
    assert _filter(_approach(2, 3), [ahead]) == ["ahead"]


def test_side_filter_respects_turn_tags_and_never_strands_a_lane() -> None:
    exit_ = _candidate("exit", "through", 22.289)
    # An explicit turn:lanes direction is positive evidence and outranks the geometry.
    assert _filter(_approach(0, 2, ["left"]), [exit_]) == ["exit"]

    # A lane whose only movement is wrong-side keeps it rather than going nowhere.
    assert _filter(_approach(0, 2), [exit_]) == ["exit"]
    steeper = _candidate("steeper", "left", 80.0)
    assert _filter(_approach(0, 2), [exit_, steeper]) == ["exit"]

    # Reverse candidates are left to the U-turn policy.
    reverse = _candidate("back", "reverse", 180.0)
    assert _filter(_approach(0, 2), [reverse, exit_]) == ["back"]

    # A lane that carries straight on is not stranded, so the fallback stays off and
    # the wrong-side exit is dropped. Otherwise every lane of an approach feeds the
    # exit, which is exactly what the side rule exists to prevent.
    assert _filter(_approach(0, 2), [exit_], has_continuation=True) == []
    assert _filter(_approach(1, 2), [exit_], has_continuation=True) == ["exit"]


def _fallback(
    kept: list[MovementCandidate],
    removed: list[MovementCandidate],
    *,
    has_continuation: bool = False,
) -> str | None:
    restored = _stranded_permission_fallback(kept, removed, has_continuation=has_continuation)
    return None if restored is None else restored.to_lane_id


def test_turn_tags_never_strand_a_lane_when_they_match_no_available_movement() -> None:
    # Kenanga way 1530245742: tagged `right`, but its only continuation leaves at
    # -19.36 degrees, which `classify_movement` bins as `through`. Dropping it on that
    # mismatch cut the lane off from way 776370584 entirely.
    shallow = _candidate("ahead", "through", -19.36)
    assert _fallback([], [shallow]) == "ahead"

    # The straightest rejected movement wins, and ties break on the lane id so the
    # choice is deterministic across runs.
    steeper = _candidate("steeper", "left", 80.0)
    assert _fallback([], [shallow, steeper]) == "ahead"
    tie_a = _candidate("aaa", "through", -19.36)
    tie_b = _candidate("bbb", "through", 19.36)
    assert _fallback([], [tie_b, tie_a]) == "aaa"


def test_permission_fallback_stays_off_when_the_lane_is_not_stranded() -> None:
    shallow = _candidate("ahead", "through", -19.36)
    permitted = _candidate("right", "right", -90.0)

    # Anything the tag allows means there is no disagreement to resolve.
    assert _fallback([permitted], [shallow]) is None
    # A lane that carries straight on already has somewhere to go.
    assert _fallback([], [shallow], has_continuation=True) is None
    # Nothing was rejected, so nothing is restored.
    assert _fallback([], []) is None


def test_a_taper_bends_one_end_onto_its_target_and_leaves_the_rest_alone() -> None:
    lane = LineString([(0.0, 0.0), (100.0, 0.0)])
    tapered = _tapered_line(lane, at_end=True, target=(100.0, 3.5), taper_length=30.0)

    # The moved end lands exactly on its target and the far end has not shifted.
    assert tapered.coords[-1] == pytest.approx((100.0, 3.5))
    assert tapered.coords[0] == pytest.approx((0.0, 0.0))

    # A straight lane has only two vertices, so without a hinge the blend would spread
    # over the whole 100 m instead of the last 30.
    assert any(point[0] == pytest.approx(70.0) and point[1] == pytest.approx(0.0)
               for point in tapered.coords)
    assert all(y == pytest.approx(0.0) for x, y in tapered.coords if x <= 70.0)

    # Displacement grows linearly across the taper, so the lane bends without kinking.
    detailed = _tapered_line(
        LineString([(0.0, 0.0), (85.0, 0.0), (100.0, 0.0)]),
        at_end=True,
        target=(100.0, 3.5),
        taper_length=30.0,
    )
    assert next(y for x, y in detailed.coords if x == pytest.approx(85.0)) == pytest.approx(1.75)

    # Tapering the upstream end is the mirror image.
    upstream = _tapered_line(lane, at_end=False, target=(0.0, -3.5), taper_length=30.0)
    assert upstream.coords[0] == pytest.approx((0.0, -3.5))
    assert upstream.coords[-1] == pytest.approx((100.0, 0.0))

    # A taper longer than the lane is clamped to it rather than overshooting.
    short = _tapered_line(
        LineString([(0.0, 0.0), (10.0, 0.0)]), at_end=True, target=(10.0, 3.5), taper_length=30.0
    )
    assert short.coords[0] == pytest.approx((0.0, 0.0))
    assert short.coords[-1] == pytest.approx((10.0, 3.5))


def test_only_the_minor_side_of_a_shallow_merge_yields() -> None:
    def connector(identifier: str, source: str, target: str, movement: str) -> ConnectorFeature:
        return ConnectorFeature(
            identifier=identifier,
            junction_node_id="1",
            from_lane_id=source,
            to_lane_id=target,
            from_way_id="10",
            to_way_id="20",
            movement=movement,
            turn_angle_degrees=0.0,
            status="active",
            centerline=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0)],
            polygon=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0), Point2D(x=0.0, y=0.0)],
        )

    def lane(identifier: str, lane_count: int, start: float, end: float) -> LaneFeature:
        feature = _approach(0, lane_count)
        return feature.model_copy(
            update={
                "identifier": identifier,
                "centerline": [Point2D(x=start, y=0.0), Point2D(x=end, y=0.0)],
            }
        )

    ramp = lane("ramp", 1, -20.0, 0.0)
    road = lane("road", 3, 5.25, 100.0)
    lookup = {"ramp": ramp, "road": road}

    # The side with fewer lanes yields, so the through carriageway is never bent.
    plan = _merge_taper_plan([connector("c", "ramp", "road", "through")], lookup, min_gap=0.5)
    assert plan == {("ramp", "end"): (5.25, 0.0)}

    # A real turn ends at a stop line; its connector curve is already the right shape.
    assert _merge_taper_plan([connector("c", "ramp", "road", "left")], lookup, min_gap=0.5) == {}

    # An even split has no minor side to choose, and a gap below the threshold is the
    # ordinary half-lane offset between two blocks.
    even = {"ramp": lane("ramp", 3, -20.0, 0.0), "road": road}
    assert _merge_taper_plan([connector("c", "ramp", "road", "through")], even, min_gap=0.5) == {}
    assert _merge_taper_plan([connector("c", "ramp", "road", "through")], lookup, min_gap=9.0) == {}


def test_a_way_carrying_its_whole_carriageway_is_recognised() -> None:
    assert _carries_whole_carriageway({"oneway": "yes"})
    assert _carries_whole_carriageway({"junction": "roundabout"})
    assert not _carries_whole_carriageway({"lanes": "4"})
    assert not _carries_whole_carriageway({"oneway": "no"})


def test_one_way_carriageways_are_centred_on_the_osm_centreline() -> None:
    def offsets(lane_count: int, *, centred: bool) -> list[float]:
        return [
            _lane_offset(index, lane_count=lane_count, width=3.5, side_sign=1.0, centred=centred)
            for index in range(lane_count)
        ]

    # A two-way way keeps its directional block on one side; the opposite direction
    # mirrors it, so the two together straddle the centreline.
    assert offsets(2, centred=False) == [1.75, 5.25]

    # A one-way carriageway has no opposite block, so it balances about the line. An
    # odd count leaves one lane exactly on it; a single lane is the line itself.
    assert offsets(1, centred=True) == [0.0]
    assert offsets(2, centred=True) == [-1.75, 1.75]
    assert offsets(4, centred=True) == [-5.25, -1.75, 1.75, 5.25]
    assert sum(offsets(3, centred=True)) == 0.0

    # Whichever layout applies, index 0 stays offside and the last lane kerbside.
    for centred in (True, False):
        assert offsets(4, centred=centred) == sorted(offsets(4, centred=centred))
        mirrored = [
            _lane_offset(index, lane_count=4, width=3.5, side_sign=-1.0, centred=centred)
            for index in range(4)
        ]
        assert mirrored == [-value for value in offsets(4, centred=centred)]


def _block(
    way: str, node_from: str, node_to: str, lane_count: int, bearing_degrees: float
) -> list[LaneFeature]:
    """Build one carriageway's lanes, all parallel, leaving the shared node at a bearing."""
    radians = math.radians(90.0 + bearing_degrees)
    step = (math.cos(radians), math.sin(radians))
    lanes = []
    for index in range(lane_count):
        # Indices run centre-out, so index 0 is offside and the last one is kerbside.
        offset = (index + 0.5 - 0.5 * lane_count) * 3.5
        base = (-step[1] * offset, step[0] * offset)
        span = [(base[0] + step[0] * along, base[1] + step[1] * along) for along in (0.0, 40.0)]
        if node_to == "n":  # an approach ends at the shared node rather than leaving it
            span = [(x - step[0] * 40.0, y - step[1] * 40.0) for x, y in span]
        lanes.append(
            LaneFeature(
                identifier=f"{way}-{index}",
                source_way_ids=[way],
                source_edge=[node_from, node_to, "0"],
                lane_index=index,
                lane_count=lane_count,
                direction="forward",
                road_class="tertiary",
                width_m=3.5,
                speed_limit_kph=50.0,
                centerline=[Point2D(x=x, y=y) for x, y in span],
                polygon=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0), Point2D(x=0.0, y=0.0)],
                boundaries=[],
                turn_permissions=[],
            )
        )
    return lanes


def _groups(*blocks: list[LaneFeature]) -> dict[tuple[str, tuple[str, ...]], list[LaneFeature]]:
    return {(block[0].source_way_ids[0], tuple(block[0].source_edge)): block for block in blocks}


def test_a_peeling_lane_is_not_also_the_straight_on_lane() -> None:
    # Three lanes arrive; two carry on and one leaves as a link. The counts close, so
    # every lane has exactly one destination and nothing is left to infer.
    approach = _block("10", "a", "n", 3, 0.0)
    carry_on = _block("20", "n", "c", 2, 0.0)
    link = _block("30", "n", "l", 1, 20.0)

    assignment = _balanced_approach_assignment(
        approach, _groups(carry_on, link), driving_side="left"
    )
    assert assignment is not None

    # The link departs toward the kerb, so the kerbside lane feeds it and no other.
    assert assignment[("30", ("n", "l", "0"))] == {"10-2": "30-0"}
    # The remaining lanes carry on in order rather than collapsing onto one target.
    assert assignment[("20", ("n", "c", "0"))] == {"10-1": "20-1", "10-0": "20-0"}

    landed = [target for group in assignment.values() for target in group.values()]
    assert len(landed) == len(set(landed)) == 3


def test_lane_dealing_follows_the_driving_side_not_the_geometry() -> None:
    # Identical geometry, opposite countries. A link leaving to the right is kerbside
    # where traffic drives on the right and offside where it does not, and the lane that
    # feeds it has to change with the country.
    approach = _block("10", "a", "n", 2, 0.0)
    carry_on = _block("20", "n", "c", 1, 0.0)
    link = _block("30", "n", "l", 1, -20.0)
    groups = _groups(carry_on, link)
    link_key = ("30", ("n", "l", "0"))

    on_the_right = _balanced_approach_assignment(approach, groups, driving_side="right")
    on_the_left = _balanced_approach_assignment(approach, groups, driving_side="left")
    assert on_the_right is not None and on_the_left is not None
    assert on_the_right[link_key] == {"10-1": "30-0"}  # kerbside lane
    assert on_the_left[link_key] == {"10-0": "30-0"}  # offside lane


def test_an_oversubscribed_approach_still_lets_a_lane_serve_two_movements() -> None:
    # One lane, two destinations: the capacity does not close, so a lane genuinely does
    # go both ways and the caller's proportional mapping must keep deciding.
    approach = _block("10", "a", "n", 1, 0.0)
    groups = _groups(_block("20", "n", "c", 1, 0.0), _block("30", "n", "l", 1, 20.0))
    assert _balanced_approach_assignment(approach, groups, driving_side="left") is None

    # A U-turn is not a destination that consumes a lane, so it must not unbalance one.
    approach = _block("10", "a", "n", 2, 0.0)
    back = _block("40", "n", "a", 2, 180.0)
    groups = _groups(_block("20", "n", "c", 2, 0.0), back)
    assignment = _balanced_approach_assignment(approach, groups, driving_side="left")
    assert assignment is not None
    assert set(assignment) == {("20", ("n", "c", "0"))}


def test_a_merging_link_does_not_land_on_top_of_a_running_lane() -> None:
    # A two-lane road and a one-lane link join one three-lane road. The counts close
    # exactly as they do for a diverge, so no lane may vanish and none may be shared.
    road = _block("10", "a", "n", 2, 0.0)
    link = _block("20", "b", "n", 1, -20.0)
    carry_on = _block("30", "n", "c", 3, 0.0)
    groups = _groups(carry_on)

    assignment = _balanced_merge_assignment([road, link], groups, driving_side="left")
    destination = ("30", ("n", "c", "0"))

    # The link turns toward the kerb to join, so it takes the kerbside lane and the
    # road keeps its order behind it — middle lane onto middle lane.
    assert assignment[("b", "n", "0")] == {destination: {"20-0": "30-2"}}
    assert assignment[("a", "n", "0")] == {destination: {"10-1": "30-1", "10-0": "30-0"}}

    landed = [
        target
        for per_group in assignment.values()
        for group in per_group.values()
        for target in group.values()
    ]
    assert sorted(landed) == ["30-0", "30-1", "30-2"]  # every lane fed, none twice


def test_merge_dealing_follows_the_driving_side_not_the_geometry() -> None:
    # Identical geometry, opposite countries: the link joins from the kerb side in one
    # and from the median side in the other, so it must take opposite lanes.
    road = _block("10", "a", "n", 2, 0.0)
    link = _block("20", "b", "n", 1, -20.0)
    groups = _groups(_block("30", "n", "c", 3, 0.0))
    destination = ("30", ("n", "c", "0"))

    on_the_left = _balanced_merge_assignment([road, link], groups, driving_side="left")
    on_the_right = _balanced_merge_assignment([road, link], groups, driving_side="right")
    assert on_the_left[("b", "n", "0")] == {destination: {"20-0": "30-2"}}
    assert on_the_right[("b", "n", "0")] == {destination: {"20-0": "30-0"}}


def test_only_an_unambiguous_merge_is_dealt_as_one() -> None:
    road = _block("10", "a", "n", 2, 0.0)
    link = _block("20", "b", "n", 1, -20.0)

    # The counts do not close, so a lane really is shared and the proportional
    # mapping still decides.
    assert not _balanced_merge_assignment(
        [road, link], _groups(_block("30", "n", "c", 2, 0.0)), driving_side="left"
    )

    # An approach with somewhere else to go is not merging: which of its lanes joins
    # this carriageway is exactly the question a merge assumes is already answered.
    crossroads = _groups(_block("30", "n", "c", 3, 0.0), _block("40", "n", "d", 1, 80.0))
    assert not _balanced_merge_assignment([road, link], crossroads, driving_side="left")

    # One approach is a diverge, which the balanced-approach rule already owns.
    assert not _balanced_merge_assignment(
        [road], _groups(_block("30", "n", "c", 2, 0.0)), driving_side="left"
    )


def test_sharp_movements_need_the_evidence_a_uturn_needs() -> None:
    def unproven(movement: str, angle: float, permissions: list[str] | None = None) -> bool:
        return _unproven_sharp_movement(
            _candidate("target", movement, angle),
            source=_approach(0, 1, permissions),
            min_degrees=130.0,
        )

    # The Kenanga ramp nose: 138 degrees is short of the 145 degree `reverse` band, so
    # nothing else would ever question it.
    assert unproven("right", -138.457)
    assert not unproven("right", -129.999)

    # An explicit turn:lanes permission for the movement settles it.
    assert not unproven("right", -138.457, ["right"])
    assert unproven("right", -138.457, ["left"])

    # Reverse candidates belong to the U-turn policy, not this rule.
    assert not unproven("reverse", 180.0)


def test_only_real_branch_control_or_explicit_uturn_is_a_decision_node() -> None:
    assert not _is_decision_node(
        non_reverse_group_count=1,
        adjacent_node_count=2,
        has_control_or_restriction=False,
        explicit_reverse=False,
    )
    assert _is_decision_node(
        non_reverse_group_count=2,
        adjacent_node_count=3,
        has_control_or_restriction=False,
        explicit_reverse=False,
    )
    assert _is_decision_node(
        non_reverse_group_count=1,
        adjacent_node_count=2,
        has_control_or_restriction=True,
        explicit_reverse=False,
    )
    assert _is_decision_node(
        non_reverse_group_count=1,
        adjacent_node_count=2,
        has_control_or_restriction=False,
        explicit_reverse=True,
    )


def test_lane_model_rejects_numeric_identifier() -> None:
    with pytest.raises(ValidationError):
        PreliminaryLaneModel.model_validate(
            {
                "metadata": {
                    "generator_version": "test",
                    "lane_model_schema_version": 1,
                    "source_checksum": "a",
                    "projected_graph_checksum": "b",
                    "configuration_checksum": "c",
                    "generation_fingerprint": "d",
                    "coordinate_system_wkt": "local",
                },
                "lanes": [{"identifier": 9007199254740993}],
            }
        )


def test_generate_lane_model_writes_deterministic_stage_2_artifacts(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    config = ConverterConfig(config_version=1)

    report_path = generate_lane_model(workspace=workspace, config=config)
    first_model_bytes = (workspace / "lane-model" / "preliminary.json").read_bytes()
    first = PreliminaryLaneModel.model_validate_json(first_model_bytes)
    first_manifest = json.loads((workspace / "source" / "manifest.json").read_text())

    assert report_path == workspace / "reports" / "lane-model-generation.json"
    assert first.lanes
    assert all(isinstance(lane.identifier, str) for lane in first.lanes)
    assert all(lane.polygon[0] == lane.polygon[-1] for lane in first.lanes)
    assert len({finding.identifier for finding in first.findings}) == len(first.findings)
    assert first.signals
    assert first.stop_lines
    assert not first.connectors
    assert any(lane.exit_lanes for lane in first.lanes)
    assert first.restrictions
    assert first.restrictions[0].status in {"enforced", "already_satisfied"}
    assert all(connector.from_lane_id != connector.to_lane_id for connector in first.connectors)
    assert all(connector.from_way_id != connector.to_way_id for connector in first.connectors)
    assert all(stop_line.source == "inferred" for stop_line in first.stop_lines)
    # Way 11 is a one-way motorway meeting the two-way way 10 at node 4. The two-way
    # lanes sit half a width either side of the shared node, so their midpoint is the
    # node itself, and the one-way lane must land on it rather than beside it.
    def _ends(way_id: str) -> list[Point2D]:
        return [
            end
            for lane in first.lanes
            if lane.source_way_ids == [way_id]
            for end in (lane.centerline[0], lane.centerline[-1])
        ]

    def _distance(left: Point2D, right: Point2D) -> float:
        return math.dist((left.x, left.y), (right.x, right.y))

    # A continuation carries no side, so a carriageway that merely bends must not
    # shuffle its lanes onto the kerb. Every same-count continuation keeps its index.
    by_id = {lane.identifier: lane for lane in first.lanes}
    continuations = [
        (lane, by_id[exit_id])
        for lane in first.lanes
        for exit_id in lane.exit_lanes
        if exit_id in by_id
    ]
    assert continuations
    assert all(
        source.lane_index == target.lane_index
        for source, target in continuations
        if source.lane_count == target.lane_count
    )
    # And no connector is drawn as a second copy of its lane.
    assert all(
        LineString((point.x, point.y) for point in connector.centerline).length < 12.0
        for connector in first.connectors
    )
    # Two lanes of one approach must never hand off to the same lane: a carriageway
    # that apportions its lanes across destinations cannot make one of them vanish.
    handoffs: dict[tuple[str, ...], list[str]] = {}
    for source, target in continuations:
        handoffs.setdefault(tuple(source.source_edge), []).append(target.identifier)
    assert all(len(targets) == len(set(targets)) for targets in handoffs.values())

    shared = min(_ends("11"), key=lambda p: min(_distance(p, q) for q in _ends("10")))
    near, far = sorted(_ends("10"), key=lambda q: _distance(q, shared))[:2]
    assert _distance(near, far) == pytest.approx(3.5)
    assert (near.x + far.x) / 2 == pytest.approx(shared.x)
    assert (near.y + far.y) / 2 == pytest.approx(shared.y)

    assert (workspace / "inspection" / "stage-2-review-audit.html").is_file()
    # The audit page is the only Stage 2 inspection artifact; it used to be written
    # a second time under this name, producing two byte-identical files.
    assert not (workspace / "inspection" / "stage-2-map-review.html").exists()
    html = (workspace / "inspection" / "stage-2-review-audit.html").read_text()
    assert "Stage 2 Review Audit" in html
    assert "Review filters" in html
    assert "Selected finding" in html
    assert "review_required" in html
    assert "geometry_ids" in html
    assert "lane_direction" in html
    assert "Lane direction arrows" in html
    assert "source_way" in html
    assert "source_node" in html
    assert "Source OSM ways" in html
    # Typing a bare OSM ID must resolve into the way/node namespaces, not just filter text.
    assert "function resolveId" in html
    # A connector popup must name both lanes it joins, not just their hashes.
    assert "Incoming lane" in html
    assert "Outgoing lane" in html
    assert "Entered from" in html and "Leaves to" in html
    # A lane's links carry their status, so a review-required candidate cannot be
    # mistaken for an asserted connection, and each is drawn as a lane-width band.
    assert "function laneLinks" in html and "function linkTable" in html
    assert "connector_polygon" in html
    assert "Connector bands" in html
    assert "'way:'+q" in html and "'node:'+q" in html
    assert "OpenStreetMap contributors" in html

    # Source OSM geometry must actually reach the payload, not just the template,
    # so a finding can be located on the map by the way or node it came from.
    payload = json.loads(html.split("const payload=", 1)[1].split(";const reviewPriority", 1)[0])
    kinds = {feature["properties"]["kind"] for feature in payload["features"]["features"]}
    assert {"source_way", "source_node"} <= kinds
    source_keys = {
        feature["properties"]["id"]
        for feature in payload["features"]["features"]
        if feature["properties"]["kind"] in {"source_way", "source_node"}
    }
    assert all(key.startswith(("way:", "node:")) for key in source_keys)
    mapped = [f for f in payload["findings"] if f["source_geometry_ids"]]
    assert mapped
    assert all(key in source_keys for f in mapped for key in f["source_geometry_ids"])
    report = json.loads(report_path.read_text())
    assert report["feature_counts"]["connectors"] == len(first.connectors)
    assert report["feature_counts"]["stop_lines"] == len(first.stop_lines)
    assert first_manifest["stage_2"]["generation_fingerprint"] == (
        first.metadata.generation_fingerprint
    )
    assert "review_audit_html" in first_manifest["stage_2"]["artifacts"]
    assert "review_html" not in first_manifest["stage_2"]["artifacts"]

    generate_lane_model(workspace=workspace, config=config)
    assert (workspace / "lane-model" / "preliminary.json").read_bytes() == first_model_bytes


def test_generate_lane_model_rejects_changed_stage_1_input(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source_path = workspace / "source" / "map.osm"
    source_path.write_text(source_path.read_text() + "\n", encoding="utf-8")

    with pytest.raises(GenerationError, match="source OSM checksum"):
        generate_lane_model(workspace=workspace, config=ConverterConfig(config_version=1))
