from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString

from osm_scenario.acquisition import acquire_osm
from osm_scenario.config import ConverterConfig
from osm_scenario.generation import (
    GenerationError,
    _ambiguity_causes,
    _ambiguity_reason,
    _balanced_approach_assignment,
    _balanced_merge_assignment,
    _carries_whole_carriageway,
    _direction_arrow,
    _directional_lane_count,
    _finding,
    _is_decision_node,
    _lane_collapse_findings,
    _lane_offset,
    _links_by_node,
    _mapped_lane_index,
    _merge_taper_plan,
    _movement_roles,
    _side_filtered_candidates,
    _speed_kph,
    _stranded_permission_fallback,
    _tagged_side_block,
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
    ReviewFinding,
)
from osm_scenario.normalization import normalize_workspace
from osm_scenario.osm_source import read_osm_snapshot
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


def _side_lane(lane_index: int, lane_count: int, permissions: list[str]) -> LaneFeature:
    return _approach(lane_index, lane_count, permissions).model_copy(
        update={"identifier": f"lane-{lane_index}"}
    )


def test_a_side_says_where_a_block_starts_not_where_every_lane_goes() -> None:
    # `turn:lanes=right|right` puts both lanes offside. Answering 0 for each hands two
    # streams of traffic one lane and starves the one beside it — the Persiaran Meranti
    # defect at node 1927184814.
    block = [_side_lane(0, 2, ["right"]), _side_lane(1, 2, ["right"])]
    assert [_mapped_lane_index(lane, 2, "offside", block) for lane in block] == [0, 1]

    # Order is kept: the lane nearer the centreline stays nearer the centreline.
    assert [_mapped_lane_index(lane, 3, "offside", block) for lane in block] == [0, 1]


def test_a_nearside_block_is_dealt_inward_from_the_kerb() -> None:
    # The mirror of the case above, and the one junction-1 cannot exercise: a left turn
    # in left-hand traffic. The kerbside lane takes the kerbside destination lane and the
    # block fills inward, so lateral order survives the turn.
    block = [_side_lane(0, 2, ["left"]), _side_lane(1, 2, ["left"])]
    assert [_mapped_lane_index(lane, 3, "nearside", block) for lane in block] == [1, 2]
    assert [_mapped_lane_index(lane, 2, "nearside", block) for lane in block] == [0, 1]


def test_a_block_with_no_room_still_collapses_rather_than_overflowing() -> None:
    # Three lanes into a two-lane destination genuinely share. The clamp keeps every
    # index inside the destination; `lane_transition_count_mismatch` reports the sharing.
    # Listed in lane-index order, so idx0 is first and the kerbside lane last. The lane
    # at the leading side gets its own destination lane and the overflow piles onto the
    # far one, rather than every lane collapsing onto the leading index as before.
    block = [_side_lane(index, 3, ["right"]) for index in range(3)]
    assert [_mapped_lane_index(lane, 2, "offside", block) for lane in block] == [0, 1, 1]
    kerb = [_side_lane(index, 3, ["left"]) for index in range(3)]
    assert [_mapped_lane_index(lane, 2, "nearside", kerb) for lane in kerb] == [0, 0, 1]


def test_a_block_of_one_maps_exactly_as_it_did_before() -> None:
    # The change has to be a strict generalisation: with one lane on the side there is no
    # block to deal, and every existing mapping must be untouched.
    single = [_side_lane(1, 3, ["right"])]
    assert _mapped_lane_index(single[0], 3, "offside", single) == 0
    assert _mapped_lane_index(single[0], 3, "offside") == 0
    # An untagged lane forms no block, so it keeps the plain side index even when it is
    # handed the block of a neighbour that is tagged.
    untagged = _side_lane(0, 2, [])
    assert _mapped_lane_index(untagged, 3, "nearside", [_side_lane(1, 2, ["left"])]) == 2


def test_only_an_explicit_turn_tag_puts_lanes_in_the_same_block() -> None:
    approach = [_side_lane(0, 2, ["right"]), _side_lane(1, 2, [])]
    block = _tagged_side_block(approach, "offside", driving_side="left")
    assert [lane.identifier for lane in block] == ["lane-0"]

    # `left;right` permits both, so it names no side and settles nothing.
    both = [_side_lane(0, 2, ["left", "right"]), _side_lane(1, 2, ["left", "right"])]
    assert _tagged_side_block(both, "offside", driving_side="left") == []


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


def _causes(
    candidate: MovementCandidate,
    *,
    source: LaneFeature,
    uturn_status: str = "review_required",
    family_count: int = 1,
) -> tuple[str, ...]:
    return _ambiguity_causes(
        candidate,
        source=source,
        uturn_status=uturn_status,
        family_count=family_count,
        sharp_movement_min_degrees=130.0,
    )


def test_ambiguity_reports_every_trigger_that_fired_not_just_the_first() -> None:
    source = _approach(0, 1)
    uturn = _candidate("dst", "reverse", -180.0)

    # A U-turn that also competes with another movement is two separate reasons to
    # look at it, and the U-turn is the one that decides whether it exists at all.
    assert _causes(uturn, source=source, family_count=2) == (
        "uturn_without_evidence",
        "competing_movements",
    )
    assert _causes(uturn, source=source, uturn_status="active") == ()
    assert _causes(_candidate("dst", "through", 35.0), source=source) == ("borderline_angle",)
    assert _causes(_candidate("dst", "right", -138.0), source=source) == (
        "unproven_sharp_movement",
    )
    # An explicit turn:lanes permission is positive evidence and settles it.
    assert _causes(_candidate("dst", "right", -138.0), source=_approach(0, 1, ["right"])) == ()


def test_naming_the_cause_does_not_change_which_movements_are_flagged() -> None:
    source = _approach(0, 1)
    for movement, angle in [
        ("reverse", -180.0),
        ("through", 0.5),
        ("through", 35.0),
        ("right", -138.0),
        ("left", 90.0),
        ("slight_right", -29.0),
    ]:
        for uturn_status in ("active", "review_required", "excluded"):
            for family_count in (1, 2):
                candidate = _candidate("dst", movement, angle)
                # The oracle is the four-clause expression this replaced. Naming a
                # trigger must not add or remove one.
                expected = (
                    (movement == "reverse" and uturn_status == "review_required")
                    or family_count > 1
                    or 30 <= abs(angle) <= 40
                    or _unproven_sharp_movement(candidate, source=source, min_degrees=130.0)
                )
                causes = _causes(
                    candidate,
                    source=source,
                    uturn_status=uturn_status,
                    family_count=family_count,
                )
                assert bool(causes) is expected


def test_the_review_reason_names_the_headline_cause_and_lists_the_rest() -> None:
    source = _approach(0, 1)
    uturn = _candidate("dst", "reverse", -160.2)
    both = MovementCandidate(
        **{
            **uturn.__dict__,
            "ambiguity_causes": _causes(uturn, source=source, family_count=2),
        }
    )
    reason = _ambiguity_reason(both, sharp_movement_min_degrees=130.0)
    assert reason.startswith("U-turn at -160.2 degrees")
    assert "also competing movements in the same turn family" in reason

    # The sharp-movement sentence predates this change and stays word for word, so
    # entries written either side of it remain comparable.
    sharp = _candidate("dst", "right", -138.0)
    sharp = MovementCandidate(
        **{**sharp.__dict__, "ambiguity_causes": _causes(sharp, source=source)}
    )
    assert _ambiguity_reason(sharp, sharp_movement_min_degrees=130.0) == (
        "movement doubles back beyond 130 degrees without an explicit turn:lanes permission"
    )


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


def _taper_connector(
    identifier: str, source: str, target: str, movement: str, status: str = "active"
) -> ConnectorFeature:
    return ConnectorFeature(
        identifier=identifier,
        junction_node_id="1",
        from_lane_id=source,
        to_lane_id=target,
        from_way_id="10",
        to_way_id="20",
        movement=movement,
        turn_angle_degrees=0.0,
        status=status,
        centerline=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0)],
        polygon=[Point2D(x=0.0, y=0.0), Point2D(x=1.0, y=0.0), Point2D(x=0.0, y=0.0)],
    )


def _taper_lane(identifier: str, lane_count: int, start: float, end: float) -> LaneFeature:
    feature = _approach(0, lane_count)
    return feature.model_copy(
        update={
            "identifier": identifier,
            "centerline": [Point2D(x=start, y=0.0), Point2D(x=end, y=0.0)],
        }
    )


def test_an_unreviewed_movement_still_pulls_its_lane_off_the_centreline() -> None:
    # A link peeling off a road it shares a lane with: the lane serves two movements, so
    # both are flagged for review, but the link still has to start on the lane that
    # feeds it rather than on the junction node.
    lookup = {
        "link": _taper_lane("link", 1, 0.0, 40.0),
        "road": _taper_lane("road", 2, -60.0, -5.25),
    }
    plan = _merge_taper_plan(
        [_taper_connector("c", "road", "link", "through", "review_required")],
        lookup,
        min_gap=0.5,
    )
    assert plan == {("link", "start"): (-5.25, 0.0)}

    # A forbidden movement does not exist, so it may not drag geometry.
    assert (
        _merge_taper_plan(
            [_taper_connector("c", "road", "link", "through", "forbidden")],
            lookup,
            min_gap=0.5,
        )
        == {}
    )


def test_an_endpoint_two_movements_disagree_about_is_left_where_osm_put_it() -> None:
    lookup = {
        "link": _taper_lane("link", 1, 0.0, 40.0),
        "road": _taper_lane("road", 2, -60.0, -5.25),
        "other": _taper_lane("other", 2, -60.0, -9.75),
    }
    same_rank = [
        _taper_connector("a", "road", "link", "through", "review_required"),
        _taper_connector("b", "other", "link", "through", "review_required"),
    ]
    # Two unreviewed movements name two different places for one endpoint. It cannot be
    # in both, and picking by connector id would settle a real disagreement by accident.
    assert _merge_taper_plan(same_rank, lookup, min_gap=0.5) == {}

    # A decided movement outranks one still awaiting review, so the tie is not a tie.
    mixed = [
        _taper_connector("a", "road", "link", "through", "review_required"),
        _taper_connector("b", "other", "link", "through", "active"),
    ]
    assert _merge_taper_plan(mixed, lookup, min_gap=0.5) == {("link", "start"): (-9.75, 0.0)}


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


def _collapse_lane(identifier: str, edge: list[str], index: int, count: int) -> LaneFeature:
    return _approach(index, count).model_copy(
        update={"identifier": identifier, "source_edge": edge, "lane_count": count}
    )


def _collapse_connector(source: str, target: str, status: str = "active") -> ConnectorFeature:
    return _taper_connector(f"{source}->{target}", source, target, "through", status)


def test_a_turn_off_a_wider_road_is_not_a_lane_count_change() -> None:
    # Persiaran Perdana at node 474929865: two lanes arrive, the kerbside one turns left
    # into a one-lane carriageway. Comparing the two roads' widths called this "2 to 1";
    # the movement is one lane into one lane and there is nothing to review.
    lookup = {
        "approach_0": _collapse_lane("approach_0", ["a", "n", "0"], 0, 2),
        "approach_1": _collapse_lane("approach_1", ["a", "n", "0"], 1, 2),
        "ahead": _collapse_lane("ahead", ["b", "n", "0"], 0, 2),
        "ahead_kerb": _collapse_lane("ahead_kerb", ["b", "n", "0"], 1, 2),
        "turn": _collapse_lane("turn", ["c", "n", "0"], 0, 1),
    }
    connectors = [
        _collapse_connector("approach_0", "ahead"),
        _collapse_connector("approach_1", "ahead_kerb"),
        _collapse_connector("approach_1", "turn"),
    ]
    assert _lane_collapse_findings(connectors, [], lookup) == []


def test_two_lanes_landing_on_one_names_every_lane_it_counts() -> None:
    # Way 756118314 is tagged `turn:lanes=right|right`, so both lanes are labelled
    # offside and collide on one target, leaving the destination's other lane starved.
    lookup = {
        "left_lane": _collapse_lane("left_lane", ["a", "n", "0"], 0, 2),
        "right_lane": _collapse_lane("right_lane", ["a", "n", "0"], 1, 2),
        "target": _collapse_lane("target", ["b", "n", "0"], 0, 2),
        "starved": _collapse_lane("starved", ["b", "n", "0"], 1, 2),
    }
    connectors = [
        _collapse_connector("left_lane", "target"),
        _collapse_connector("right_lane", "target"),
    ]
    (finding,) = _lane_collapse_findings(connectors, [], lookup)

    # The count and the highlight have to agree: every lane the numbers speak for is
    # named, so a reviewer sees two approach lanes when it says two.
    assert finding.affected_feature_ids == ["left_lane", "right_lane", "target"]
    assert finding.proposed_value == {
        "incoming_lane_count": 2,
        "outgoing_lane_count": 1,
        "destination_lane_count": 2,
    }
    assert finding.severity == "warning"


def test_a_forbidden_movement_cannot_collapse_a_lane_onto_another() -> None:
    # A restriction removed the movement, so no traffic takes it. Counting it would put
    # a reviewer in front of two lanes with nothing between them.
    lookup = {
        "left_lane": _collapse_lane("left_lane", ["a", "n", "0"], 0, 2),
        "right_lane": _collapse_lane("right_lane", ["a", "n", "0"], 1, 2),
        "target": _collapse_lane("target", ["b", "n", "0"], 0, 1),
    }
    connectors = [
        _collapse_connector("left_lane", "target"),
        _collapse_connector("right_lane", "target", status="forbidden"),
    ]
    assert _lane_collapse_findings(connectors, [], lookup) == []

    # A movement held for review still exists on the map, so it still counts.
    held = [
        _collapse_connector("left_lane", "target"),
        _collapse_connector("right_lane", "target", status="review_required"),
    ]
    assert len(_lane_collapse_findings(held, [], lookup)) == 1


def test_a_continuation_collapses_lanes_just_as_a_connector_does() -> None:
    # A continuation never becomes a movement candidate, so a carriageway that genuinely
    # narrows between two ways would go unreported if only connectors were read.
    lookup = {
        "wide_0": _collapse_lane("wide_0", ["a", "n", "0"], 0, 2),
        "wide_1": _collapse_lane("wide_1", ["a", "n", "0"], 1, 2),
        "narrow": _collapse_lane("narrow", ["b", "n", "0"], 0, 1),
    }
    (finding,) = _lane_collapse_findings(
        [], [("n", "wide_0", "narrow"), ("n", "wide_1", "narrow")], lookup
    )
    assert finding.source_ids == ["n"]
    assert finding.proposed_value["incoming_lane_count"] == 2


def _roles_finding(source_type: str, node: str, affected: list[str]) -> ReviewFinding:
    return _finding(
        rule="turn_permission_geometry_conflict",
        severity="blocker",
        source_type=source_type,
        source_ids=[node],
        affected_feature_ids=affected,
        proposed_value={},
        confidence="low",
        reason="",
    )


def test_a_finding_that_names_lanes_says_which_one_is_approached_from() -> None:
    # Every named lane is painted the same colour without this, and the reviewer cannot
    # see which lane turns into which — the whole question the finding asks.
    links = {"n": {("lane-in", "lane-out")}}
    assert _movement_roles(_roles_finding("node", "n", ["lane-in", "lane-out"]), links) == {
        "lane-in": "approach",
        "lane-out": "destination",
    }

    # A collapse names several approach lanes; each is an approach.
    merge = {"n": {("lane-a", "lane-x"), ("lane-b", "lane-x")}}
    assert _movement_roles(
        _roles_finding("node", "n", ["lane-a", "lane-b", "lane-x"]), merge
    ) == {"lane-a": "approach", "lane-b": "approach", "lane-x": "destination"}


def test_a_way_scoped_finding_is_never_oriented() -> None:
    # `speed_default` names every lane along a way, and consecutive edges of one way are
    # joined by continuations. Orienting those would chain dozens of lanes into a
    # sequence that says nothing about any movement.
    links = {"n": {("lane-in", "lane-out")}}
    assert _movement_roles(_roles_finding("way", "n", ["lane-in", "lane-out"]), links) == {}


def test_a_lane_at_both_ends_of_a_chain_is_left_uncoloured() -> None:
    # Either colour would be half true, so it keeps the plain highlight instead.
    links = {"n": {("lane-a", "lane-b"), ("lane-b", "lane-c")}}
    assert _movement_roles(
        _roles_finding("node", "n", ["lane-a", "lane-b", "lane-c"]), links
    ) == {"lane-a": "approach", "lane-c": "destination"}


def test_nothing_is_oriented_without_a_link_between_two_named_lanes() -> None:
    # A finding whose lanes have no movement between them must not be given a direction;
    # the connector path is left to answer, or nothing is.
    links = {"n": {("lane-in", "somewhere-else")}}
    assert _movement_roles(_roles_finding("node", "n", ["lane-in", "lane-out"]), links) == {}


def test_a_forbidden_movement_is_not_a_link_between_two_lanes() -> None:
    # A restriction removed it, so nothing travels between those lanes. Counting it
    # would colour one lane as feeding another it cannot reach.
    def model(status: str) -> SimpleNamespace:
        return SimpleNamespace(
            lanes=[
                SimpleNamespace(identifier="lane-in", exit_lanes=[], source_edge=["n", "x", "0"]),
                SimpleNamespace(identifier="lane-out", exit_lanes=[], source_edge=["x", "y", "0"]),
            ],
            connectors=[
                SimpleNamespace(
                    status=status,
                    junction_node_id="x",
                    from_lane_id="lane-in",
                    to_lane_id="lane-out",
                )
            ],
        )

    assert _links_by_node(model("forbidden")) == {}  # type: ignore[arg-type]
    assert _links_by_node(model("review_required")) == {  # type: ignore[arg-type]
        "x": {("lane-in", "lane-out")}
    }

    # A continuation carries no connector, so it has to be read off the lane itself.
    carries_on = SimpleNamespace(
        lanes=[
            SimpleNamespace(
                identifier="lane-in", exit_lanes=["lane-out"], source_edge=["n", "x", "0"]
            ),
            SimpleNamespace(identifier="lane-out", exit_lanes=[], source_edge=["x", "y", "0"]),
        ],
        connectors=[],
    )
    assert _links_by_node(carries_on) == {"x": {("lane-in", "lane-out")}}  # type: ignore[arg-type]


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


def test_a_way_is_asked_once_per_answer_not_once_per_lane(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    generate_lane_model(workspace=workspace, config=ConverterConfig(config_version=1))
    model = PreliminaryLaneModel.model_validate_json(
        (workspace / "lane-model" / "preliminary.json").read_bytes()
    )

    # Way 10 runs nodes 1-2-3-4, so it spans several graph edges and generates several
    # lanes. Lane count, width and speed are properties of the way, and a decision on
    # one writes a tag onto the way — so each is one question however long the road is.
    for rule in ("lane_width_default", "speed_default"):
        for way_id in {tuple(f.source_ids) for f in model.findings if f.rule == rule}:
            matching = [
                finding
                for finding in model.findings
                if finding.rule == rule and tuple(finding.source_ids) == way_id
            ]
            assert len(matching) == 1, f"{rule} asked {len(matching)} times for {way_id}"

    # Lane count is per direction, so a two-way road is two questions and no more.
    for way_id in {tuple(f.source_ids) for f in model.findings if f.rule == "lane_count_inference"}:
        matching = [
            finding
            for finding in model.findings
            if finding.rule == "lane_count_inference" and tuple(finding.source_ids) == way_id
        ]
        directions = [finding.proposed_value["direction"] for finding in matching]  # type: ignore[index]
        assert len(directions) == len(set(directions))

    # The merged finding still names every lane it covers, and names each one once.
    lanes_of_way: dict[str, set[str]] = {}
    for lane in model.lanes:
        for way_id in lane.source_way_ids:
            lanes_of_way.setdefault(way_id, set()).add(lane.identifier)
    width = next(
        finding for finding in model.findings if finding.rule == "lane_width_default"
    )
    assert width.affected_feature_ids == sorted(set(width.affected_feature_ids))
    assert set(width.affected_feature_ids) == lanes_of_way[width.source_ids[0]]


def test_edges_that_disagree_still_produce_separate_findings() -> None:
    # The guard on the merge: only identical questions collapse. Two edges of one way
    # proposing different lane counts are two different questions and stay two
    # findings, because the proposed value is part of what identifies them.
    shared = {
        "rule": "lane_count_inference",
        "severity": "blocker",
        "source_type": "way",
        "source_ids": ["10"],
        "confidence": "low",
        "reason": "default_single_lane",
    }
    one = _finding(
        **shared,  # type: ignore[arg-type]
        affected_feature_ids=["lane-a"],
        proposed_value={"direction": "forward", "lane_count": 1},
    )
    two = _finding(
        **shared,  # type: ignore[arg-type]
        affected_feature_ids=["lane-b"],
        proposed_value={"direction": "forward", "lane_count": 2},
    )
    assert one.identifier != two.identifier
    assert one.evidence_checksum != two.evidence_checksum


def test_findings_carry_the_wgs84_geometry_they_came_from(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    generate_lane_model(workspace=workspace, config=ConverterConfig(config_version=1))
    model = PreliminaryLaneModel.model_validate_json(
        (workspace / "lane-model" / "preliminary.json").read_bytes()
    )
    located = [finding for finding in model.findings if finding.location is not None]
    assert located, "findings sourced from OSM ways and nodes must be placeable"

    # tiny.osm states its coordinates as literals, so these are exact, not approximate.
    # Way 10 runs nodes 1..4 and a way-sourced finding copies them in that order.
    way_sourced = next(
        finding
        for finding in located
        if finding.source_type == "way" and finding.source_ids == ["10"]
    )
    location = way_sourced.location
    assert location is not None
    assert [source.ref for source in location.sources] == ["way:10"]
    assert [(point.lat, point.lon) for point in location.sources[0].coordinates] == [
        (3.1500, 101.7000),
        (3.1501, 101.7001),
        (3.1502, 101.7002),
        (3.1503, 101.7003),
    ]

    node_sourced = next(
        (finding for finding in located if finding.source_type == "node"), None
    )
    if node_sourced is not None:
        assert node_sourced.location is not None
        assert len(node_sourced.location.sources) == 1
        assert len(node_sourced.location.sources[0].coordinates) == 1

    for finding in located:
        place = finding.location
        assert place is not None
        points = [point for source in place.sources for point in source.coordinates]
        minimum_lon, minimum_lat, maximum_lon, maximum_lat = place.bbox
        assert all(minimum_lon <= point.lon <= maximum_lon for point in points)
        assert all(minimum_lat <= point.lat <= maximum_lat for point in points)
        # The point is one the way actually passes through; a bbox centre can sit off
        # the road entirely where a way bends.
        assert any(
            point.lat == place.lat and point.lon == place.lon for point in points
        )


def test_a_location_never_reaches_the_evidence_a_decision_was_made_against(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    generate_lane_model(workspace=workspace, config=ConverterConfig(config_version=1))
    model = PreliminaryLaneModel.model_validate_json(
        (workspace / "lane-model" / "preliminary.json").read_bytes()
    )
    finding = next(item for item in model.findings if item.location is not None)

    # A review recorded before findings had coordinates must survive regeneration.
    # Rebuilding the same finding without its location must not move the checksum.
    rebuilt = _finding(
        rule=finding.rule,
        severity=finding.severity,
        source_type=finding.source_type,
        source_ids=finding.source_ids,
        affected_feature_ids=finding.affected_feature_ids,
        proposed_value=finding.proposed_value,
        confidence=finding.confidence,
        reason=finding.reason,
    )
    assert rebuilt.location is None
    assert rebuilt.evidence_checksum == finding.evidence_checksum
    assert rebuilt.identifier == finding.identifier


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

    # Searching an OSM id must highlight it even when Stage 2 drew nothing from it —
    # that is the case a reviewer is checking when the id produced no geometry, and it
    # used to highlight nothing and say nothing. So the index covers the whole snapshot,
    # not just the ways lanes came from and the nodes movements were built at.
    snapshot = read_osm_snapshot(workspace / "source" / "map.osm")
    index = payload["search_index"]
    drawable = {way_id for way_id, way in snapshot.ways.items() if len(way.node_ids) >= 2}
    assert set(index["ways"]) == drawable
    assert set(index["nodes"]) == set(snapshot.nodes)
    assert {f"way:{key}" for key in index["ways"]} | {
        f"node:{key}" for key in index["nodes"]
    } >= source_keys
    undrawn = {f"way:{key}" for key in index["ways"]} - source_keys
    assert undrawn, "fixture must keep a way Stage 2 drew nothing from, or this proves nothing"
    assert all(len(way["line"]) >= 2 for way in index["ways"].values())
    assert all(len(node["point"]) == 2 for node in index["nodes"].values())
    # The highlight is drawn into its own pane rather than restyling the layer already
    # on the map: source ways sit at the bottom of the z-order, so a restyled way went
    # yellow underneath the lane and connector geometry covering it.
    assert "createPane('focus')" in html and "function drawFocus" in html
    # An id that matches nothing has to say so; silence reads as a typo.
    assert "in source/map.osm" in html
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
