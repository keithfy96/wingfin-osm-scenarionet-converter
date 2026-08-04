from dataclasses import dataclass

import pytest
from shapely.geometry import LineString

from osm_scenario.topology import (
    MovementCandidate,
    classify_movement,
    connector_curve,
    forbidden_by_node_restriction,
    signed_turn_angle,
    via_way_resolution,
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


def test_angle_and_connector_curve_are_geometry_based() -> None:
    incoming = LineString([(-1, 0), (0, 0)])
    outgoing = LineString([(0, 0), (0, 1)])
    assert signed_turn_angle(incoming, outgoing) == pytest.approx(90)
    curve = connector_curve(incoming, outgoing, (0, 0))
    assert len(curve.coords) >= 2
    assert curve.coords[-1] == (0.0, 0.0)


def test_node_no_restriction_forbids_exact_transition() -> None:
    restriction = relation("no_straight_on", (("node", "1"),))
    assert forbidden_by_node_restriction(candidate("a", "c"), restriction)
    assert not forbidden_by_node_restriction(candidate("a", "exit"), restriction)


def test_node_only_restriction_forbids_other_transition() -> None:
    restriction = relation("only_straight_on", (("node", "1"),))
    assert not forbidden_by_node_restriction(candidate("a", "c"), restriction)
    assert forbidden_by_node_restriction(candidate("a", "exit"), restriction)


def test_unique_via_way_chain_is_enforced() -> None:
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, _ = via_way_resolution(
        restriction, [candidate("a", "b", "1"), candidate("b", "c", "2")]
    )
    assert status == "enforced"
    assert removed == {1}


def test_branching_via_way_chain_requires_review() -> None:
    restriction = relation("no_straight_on", (("way", "b"),))
    status, removed, reason = via_way_resolution(
        restriction,
        [candidate("a", "b", "1"), candidate("a", "b", "9"), candidate("b", "c", "2")],
    )
    assert status == "review_required"
    assert removed == set()
    assert "branching" in reason
