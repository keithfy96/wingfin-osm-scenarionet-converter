from dataclasses import dataclass

import pytest
from shapely.geometry import LineString

from osm_scenario.topology import (
    COLLINEAR_STUB_METRES,
    MovementCandidate,
    classify_movement,
    connector_curve,
    forbidden_by_node_restriction,
    movement_family,
    movement_side,
    side_lane_index,
    signed_turn_angle,
    tagged_movement_side,
    uturn_evidence_status,
    via_way_resolution,
    way_adjacency,
)


@dataclass(frozen=True)
class Member:
    member_type: str
    reference: str
    role: str


@dataclass(frozen=True)
class Relation:
    members: tuple[Member, ...]
    tags: dict[str, str]


def relation(restriction: str, via: tuple[tuple[str, str], ...]) -> Relation:
    return Relation(
        members=(Member("way", "a", "from"),)
        + tuple(Member(kind, identifier, "via") for kind, identifier in via)
        + (Member("way", "c", "to"),),
        tags={"type": "restriction", "restriction": restriction},
    )


def candidate(source: str, target: str, node: str = "1") -> MovementCandidate:
    return MovementCandidate(
        junction_node_id=node,
        from_lane_id=f"lane-{source}",
        to_lane_id=f"lane-{target}",
        from_way_id=source,
        to_way_id=target,
        movement="through",
        angle_degrees=0,
        centerline=LineString([(0, 0), (1, 0)]),
        ambiguous=False,
    )


@pytest.mark.parametrize(
    ("angle", "movement"),
    [
        (-170, "reverse"),
        (-90, "right"),
        (-50, "slight_right"),
        (0, "through"),
        (50, "slight_left"),
        (90, "left"),
    ],
)
def test_classifies_movements(angle: float, movement: str) -> None:
    assert classify_movement(angle) == movement


def test_groups_slight_turns_with_their_movement_family() -> None:
    assert movement_family("slight_left") == "left"
    assert movement_family("slight_right") == "right"
    assert movement_family("through") == "through"


def test_side_lane_index_is_centre_out() -> None:
    assert side_lane_index("offside", 3) == 0
    assert side_lane_index("nearside", 3) == 2
    assert side_lane_index("offside", 1) == side_lane_index("nearside", 1) == 0


def test_a_tag_names_a_side_only_when_it_names_one_direction() -> None:
    # The grouping of lanes that share a side reads the tag through this, so it must
    # answer exactly what `movement_side` answers or the two would drift apart.
    assert tagged_movement_side(["right"], "left") == "offside"
    assert tagged_movement_side(["left"], "left") == "nearside"
    assert tagged_movement_side(["right"], "right") == "nearside"

    # `left;right` permits both, so it settles nothing and the geometry still decides.
    assert tagged_movement_side(["left", "right"], "left") is None
    assert tagged_movement_side(["through"], "left") is None
    assert tagged_movement_side([], "left") is None

    # A directional permission alongside `through` still names the side.
    assert tagged_movement_side(["through", "right"], "left") == "offside"


def test_movement_side_is_relative_to_the_driving_side() -> None:
    def side(angle: float, driving_side: str, permissions: list[str] | None = None) -> str | None:
        return movement_side(
            movement=classify_movement(angle),
            angle=angle,
            driving_side=driving_side,
            turn_permissions=permissions or [],
            min_degrees=10.0,
        )

    # A left turn is nearside where traffic drives on the left and offside where it does not.
    assert side(90.0, "left") == "nearside"
    assert side(90.0, "right") == "offside"
    assert side(-90.0, "left") == "offside"
    assert side(-90.0, "right") == "nearside"

    # A slip road inside the 35 degree `through` band still carries a side.
    assert classify_movement(19.254) == "through"
    assert side(19.254, "left") == "nearside"
    assert side(14.814, "left") == "nearside"

    # Straight-ahead movements carry none, and the threshold is inclusive.
    assert side(0.469, "left") is None
    assert side(9.999, "left") is None
    assert side(10.0, "left") == "nearside"
    assert side(-10.0, "left") == "offside"

    # An explicit turn:lanes value outranks the geometry, including below the threshold.
    assert side(0.5, "left", ["left"]) == "nearside"
    assert side(0.5, "left", ["right"]) == "offside"
    assert side(-90.0, "left", ["left"]) == "nearside"
    # Ambiguous or side-free tagging falls back to the angle.
    assert side(0.5, "left", ["left", "right"]) is None
    assert side(19.254, "left", ["through"]) == "nearside"


@pytest.mark.parametrize(
    ("permissions", "status"),
    [
        (["reverse"], "active"),
        (["through", "uturn"], "active"),
        (["left", "through"], "excluded"),
        ([], "review_required"),
    ],
)
def test_uturn_status_requires_positive_lane_tag_evidence(
    permissions: list[str], status: str
) -> None:
    assert uturn_evidence_status(permissions) == status


def test_angle_and_connector_curve_are_geometry_based() -> None:
    incoming = LineString([(-1, 0), (0, 0)])
    outgoing = LineString([(0, 0), (0, 1)])
    assert signed_turn_angle(incoming, outgoing) == pytest.approx(90)
    curve = connector_curve(incoming, outgoing, (0, 0))
    assert len(curve.coords) >= 2
    assert curve.coords[-1] == (0.0, 0.0)


def test_collinear_connector_is_a_stub_not_a_second_copy_of_the_lane() -> None:
    # Two lanes that already meet leave no gap to span, so the connector is only a
    # marker. Measuring it back to the previous vertex made it retrace the whole lane:
    # a straight lane has two vertices, so the one before the end is the far end.
    incoming = LineString([(0, 0), (400, 0)])
    outgoing = LineString([(400, 0), (800, 0)])
    curve = connector_curve(incoming, outgoing, (400, 0))
    assert curve.length == pytest.approx(COLLINEAR_STUB_METRES)
    assert curve.coords[-1] == (400.0, 0.0)

    # A lane shorter than the stub is not overshot.
    short = connector_curve(LineString([(0, 0), (1, 0)]), LineString([(1, 0), (5, 0)]), (1, 0))
    assert short.length == pytest.approx(1.0)
    assert short.coords[0] == (0.0, 0.0)


def test_node_no_restriction_forbids_exact_transition() -> None:
    restriction = relation("no_straight_on", (("node", "1"),))
    assert forbidden_by_node_restriction(candidate("a", "c"), restriction)
    assert not forbidden_by_node_restriction(candidate("a", "exit"), restriction)


def test_node_only_restriction_forbids_other_transition() -> None:
    restriction = relation("only_straight_on", (("node", "1"),))
    assert not forbidden_by_node_restriction(candidate("a", "c"), restriction)
    assert forbidden_by_node_restriction(candidate("a", "exit"), restriction)


def resolve(restriction: Relation, candidates: list[MovementCandidate]):
    """Resolve against the adjacency the candidates themselves describe."""
    entries, exits = way_adjacency(candidates)
    return via_way_resolution(restriction, candidates, entries, exits)


def test_unique_via_way_chain_is_enforced() -> None:
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, _ = resolve(
        restriction, [candidate("a", "b", "1"), candidate("b", "c", "2")]
    )
    assert status == "enforced"
    assert removed == {1}


def test_via_way_enforcement_accepts_multiple_lane_connectors_at_one_junction() -> None:
    restriction = relation("no_straight_on", (("way", "b"),))
    candidates = [
        candidate("a", "b", "1"),
        MovementCandidate(
            **{
                **candidate("a", "b", "1").__dict__,
                "from_lane_id": "lane-a-2",
                "to_lane_id": "lane-b-2",
            }
        ),
        candidate("b", "c", "2"),
        MovementCandidate(
            **{
                **candidate("b", "c", "2").__dict__,
                "from_lane_id": "lane-b-2",
                "to_lane_id": "lane-c-2",
            }
        ),
    ]
    status, removed, _ = resolve(restriction, candidates)
    assert status == "enforced"
    assert removed == {2, 3}


def test_branching_via_way_chain_requires_review() -> None:
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, reason = resolve(
        restriction,
        [candidate("a", "b", "1"), candidate("a", "b", "9"), candidate("b", "c", "2")],
    )
    assert status == "review_required"
    assert removed == set()
    assert "branching" in reason


def test_the_via_way_suffix_is_kept_when_something_else_feeds_the_via_way() -> None:
    """The defect Keith found: `x` never appears in the relation and lost its movement.

    A connector is one step of a route and remembers nothing either side of itself, so
    removing `b -> c` removes it for `x` too. Here `b` leads nowhere but `c`, so anyone
    entering `b` from `a` was going to make the prohibited turn and removing `a -> b`
    costs nothing.
    """
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, reason = resolve(
        restriction,
        [candidate("a", "b", "1"), candidate("x", "b", "1"), candidate("b", "c", "2")],
    )
    assert status == "enforced"
    assert removed == {0}, "the first step, not the last"
    assert "prefix removed" in reason
    assert "way x" in reason


def test_the_via_way_prefix_is_kept_when_the_via_way_leads_somewhere_else() -> None:
    """The mirror: `b -> z` is legal, so removing `a -> b` would take it with it."""
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, reason = resolve(
        restriction,
        [candidate("a", "b", "1"), candidate("b", "c", "2"), candidate("b", "z", "2")],
    )
    assert status == "enforced"
    assert removed == {1}
    assert reason == "prohibited via-way suffix removed"


def test_the_suffix_wins_when_removing_either_step_would_be_exact() -> None:
    """Identity, not taste. Every via-way restriction enforced before this change removed
    the suffix, and re-deciding a settled one would move a forbidden connector id for no
    reason and cost the review decision attached to it."""
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, reason = resolve(
        restriction, [candidate("a", "b", "1"), candidate("b", "c", "2")]
    )
    assert status == "enforced"
    assert removed == {1}
    assert reason == "prohibited via-way suffix removed"


def test_a_via_way_with_traffic_at_both_ends_is_held_for_review() -> None:
    """Neither step carries only the prohibited route, so there is nothing to deduce.

    The movements come back so the caller can hold them: leaving them alone would let the
    prohibited route be driven, and removing one would take legal traffic with it.
    """
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, reason = resolve(
        restriction,
        [
            candidate("a", "b", "1"),
            candidate("x", "b", "1"),
            candidate("b", "c", "2"),
            candidate("b", "z", "2"),
        ],
    )
    assert status == "review_required"
    assert removed == {2}, "the movements to hold, not the ones to forbid"
    assert "way x" in reason and "way z" in reason


def test_a_two_via_chain_asks_about_every_way_before_and_after_the_step() -> None:
    """No workspace has a chain this long, so this is the only cover the branch gets.

    `a -> b -> d -> c`, and `d` also leads to `z`. Removing the last step is exact only
    if nothing else feeds `b` or `d`; removing an earlier one is exact only if nothing
    later leads anywhere else, and `d -> z` is exactly that.
    """
    restriction = relation("no_straight_on", (("way", "b"), ("way", "d")))
    candidates = [
        candidate("a", "b", "1"),
        candidate("b", "d", "2"),
        candidate("d", "c", "3"),
        candidate("d", "z", "3"),
    ]
    status, removed, reason = resolve(restriction, candidates)
    assert status == "enforced"
    assert removed == {2}, "only the last step is unaffected by d -> z"
    assert reason == "prohibited via-way suffix removed"

    # Give `d` a second entry as well and no step is clean any more.
    status, _removed, reason = resolve(restriction, [*candidates, candidate("x", "d", "2")])
    assert status == "review_required"
    assert "way x" in reason and "way z" in reason
