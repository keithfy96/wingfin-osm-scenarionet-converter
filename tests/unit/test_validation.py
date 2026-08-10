"""Stage 5 — validating the reviewed map.

Each check gets one negative that names its own code, because a validator whose failures
all look alike tells a reader that something is wrong but not what.
"""

from __future__ import annotations

import math
from typing import Any

from osm_scenario.lane_model import (
    ConnectorFeature,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
    RestrictionEffect,
    ReviewFinding,
    SignalAssociation,
    StopLine,
)
from osm_scenario.validation import (
    _boundary_report,
    _connector_issues,
    _dispositioned_osm_ids,
    _geometry_issues,
    _reference_issues,
    _restriction_issues,
    _signal_issues,
)
from osm_scenario.validation_view import render_validation_html

WIDTH = 4.0


def _straight(x0: float, x1: float) -> list[Point2D]:
    return [Point2D(x=x0, y=0.0), Point2D(x=x1, y=0.0)]


def _surface(x0: float, x1: float, width: float = WIDTH) -> list[Point2D]:
    """The rectangle `generation._lane_surface` would buffer out of that centreline.

    Built here rather than by calling shapely so the fixture states its own coordinates:
    the centreline lies exactly on this ring's long edges, which is the case the real
    tolerance exists for.
    """
    half = width / 2
    return [
        Point2D(x=x0, y=-half),
        Point2D(x=x1, y=-half),
        Point2D(x=x1, y=half),
        Point2D(x=x0, y=half),
        Point2D(x=x0, y=-half),
    ]


def _lane(identifier: str, *, x0: float = 0.0, x1: float = 50.0, **update: Any) -> LaneFeature:
    lane = LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=["1", "2", "0"],
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=WIDTH,
        speed_limit_kph=50.0,
        centerline=_straight(x0, x1),
        polygon=_surface(x0, x1),
        boundaries=[],
    )
    return lane.model_copy(update=update) if update else lane


def _connector(identifier: str, *, points: list[Point2D], **update: Any) -> ConnectorFeature:
    connector = ConnectorFeature(
        identifier=identifier,
        junction_node_id="900",
        from_lane_id="a",
        to_lane_id="b",
        from_way_id="200",
        to_way_id="201",
        movement="through",
        turn_angle_degrees=0.0,
        status="active",
        centerline=points,
        polygon=_surface(50.0, 60.0),
    )
    return connector.model_copy(update=update) if update else connector


def _model(**update: Any) -> PreliminaryLaneModel:
    """Two lanes joined by one connector, wired both ways. Valid until a test breaks it."""
    a = _lane("a", x0=0.0, x1=50.0, exit_lanes=["c"])
    b = _lane("b", x0=60.0, x1=110.0, entry_lanes=["c"], source_edge=["2", "3", "0"])
    connector = _connector("c", points=_straight(50.0, 60.0))
    model = PreliminaryLaneModel.model_validate(
        {
            "metadata": {
                "generator_version": "test",
                "lane_model_schema_version": 1,
                "source_checksum": "source",
                "projected_graph_checksum": "graph",
                "configuration_checksum": "config",
                "generation_fingerprint": "fingerprint",
                "coordinate_system_wkt": "EPSG:4326",
            },
            "lanes": [a.model_dump(), b.model_dump()],
            "connectors": [connector.model_dump()],
        }
    )
    return model.model_copy(update=update) if update else model


def _codes(issues: list[dict[str, Any]]) -> set[str]:
    return {item["code"] for item in issues}


def _issue_row(code: str, osm_id: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "osm_id": osm_id, "reason": "test", **extra}


def _page_payload(html: str) -> dict[str, Any]:
    import json
    import re

    match = re.search(r"const DATA = (\{.*?\});", html, re.S)
    assert match is not None
    return json.loads(match.group(1).replace("<\\/", "</"))


# --- geometry ---------------------------------------------------------------------------


def test_a_clean_model_reports_nothing() -> None:
    model = _model()
    assert _geometry_issues(model) == []
    assert _reference_issues(model) == []
    assert _connector_issues(model) == []
    assert _restriction_issues(model) == []
    assert _signal_issues(model) == []


def test_a_centerline_on_its_polygon_boundary_is_not_outside_it() -> None:
    """The polygon is a buffer of the centreline, so every lane in a real map touches its
    own boundary. An exact containment test would fail all 285 lanes of junction-1."""
    assert _geometry_issues(_model()) == []


def test_a_centerline_leaving_its_polygon_is_reported() -> None:
    lane = _lane("a", exit_lanes=["c"]).model_copy(
        update={"centerline": [Point2D(x=0.0, y=0.0), Point2D(x=50.0, y=WIDTH)]}
    )
    model = _model()
    model = model.model_copy(update={"lanes": [lane, model.lanes[1]]})
    assert "centerline_outside_polygon" in _codes(_geometry_issues(model))


def test_a_non_finite_coordinate_is_reported() -> None:
    """Pydantic accepts inf and nan for a bare float, so nothing upstream catches this."""
    lane = _lane("a").model_copy(
        update={"centerline": [Point2D(x=0.0, y=0.0), Point2D(x=math.inf, y=0.0)]}
    )
    model = _model()
    assert "non_finite_geometry" in _codes(
        _geometry_issues(model.model_copy(update={"lanes": [lane, model.lanes[1]]}))
    )


def test_a_self_intersecting_centerline_is_reported() -> None:
    lane = _lane("a").model_copy(
        update={
            "centerline": [
                Point2D(x=0.0, y=-1.0),
                Point2D(x=10.0, y=1.0),
                Point2D(x=10.0, y=-1.0),
                Point2D(x=0.0, y=1.0),
            ]
        }
    )
    model = _model()
    assert "self_intersecting_centerline" in _codes(
        _geometry_issues(model.model_copy(update={"lanes": [lane, model.lanes[1]]}))
    )


# --- references -------------------------------------------------------------------------


def test_an_unknown_reference_is_dangling() -> None:
    model = _model()
    broken = model.lanes[0].model_copy(update={"exit_lanes": ["c", "nowhere"]})
    assert "dangling_reference" in _codes(
        _reference_issues(model.model_copy(update={"lanes": [broken, model.lanes[1]]}))
    )


def test_a_one_sided_connector_link_is_non_reciprocal() -> None:
    """The far lane must list the connector too, or the movement exists in one direction
    only and the lane graph disagrees with itself."""
    model = _model()
    far = model.lanes[1].model_copy(update={"entry_lanes": []})
    assert "non_reciprocal_link" in _codes(
        _reference_issues(model.model_copy(update={"lanes": [model.lanes[0], far]}))
    )


def test_a_one_sided_neighbor_is_reported() -> None:
    model = _model()
    a = model.lanes[0].model_copy(update={"left_neighbor": "b"})
    assert "non_reciprocal_neighbor" in _codes(
        _reference_issues(model.model_copy(update={"lanes": [a, model.lanes[1]]}))
    )


# --- connectors ---------------------------------------------------------------------------


def test_a_stub_connector_within_the_meeting_tolerance_passes() -> None:
    """A collinear connector degenerates to a three-point stub whose far end stays on the
    incoming lane, up to 0.05 m from the outgoing lane. 32 of junction-1's 83 are stubs."""
    # The stub branch fires precisely because the two lane ends are already within 0.05 m
    # of each other, so the fixture has to put them there.
    abutting = _lane("b", x0=50.03, x1=100.0, entry_lanes=["c"], source_edge=["2", "3", "0"])
    stub = _connector(
        "c",
        points=[Point2D(x=47.0, y=0.0), Point2D(x=48.5, y=0.0), Point2D(x=50.0, y=0.0)],
    )
    model = _model()
    model = model.model_copy(update={"lanes": [model.lanes[0], abutting], "connectors": [stub]})

    assert _connector_issues(model) == []


def test_a_connector_that_misses_its_lane_is_reported() -> None:
    model = _model()
    adrift = _connector("c", points=_straight(50.0, 59.0))
    assert "connector_endpoint_gap" in _codes(
        _connector_issues(model.model_copy(update={"connectors": [adrift]}))
    )


def test_a_forbidden_connector_a_lane_still_lists_is_reported() -> None:
    """The status says the movement does not exist; the lane graph says it does."""
    model = _model()
    dead = model.connectors[0].model_copy(update={"status": "forbidden"})
    assert "inactive_connector_is_drivable" in _codes(
        _connector_issues(model.model_copy(update={"connectors": [dead]}))
    )


def test_an_active_connector_no_lane_lists_is_reported() -> None:
    model = _model()
    orphaned = [
        model.lanes[0].model_copy(update={"exit_lanes": []}),
        model.lanes[1].model_copy(update={"entry_lanes": []}),
    ]
    assert "active_connector_is_unreachable" in _codes(
        _connector_issues(model.model_copy(update={"lanes": orphaned}))
    )


# --- restrictions, signals, stop lines ----------------------------------------------------


def test_a_restriction_whose_movement_is_still_live_is_reported() -> None:
    model = _model()
    restriction = RestrictionEffect(
        identifier="r1",
        source_relation_id="7000",
        restriction="no_left_turn",
        from_way_ids=["200"],
        via_member_ids=["900"],
        to_way_ids=["201"],
        status="enforced",
        forbidden_connector_ids=["c"],
        reason="test",
    )
    issues = _restriction_issues(model.model_copy(update={"restrictions": [restriction]}))
    assert "forbidden_movement_live" in _codes(issues)


def test_an_unassociated_signal_is_reported() -> None:
    model = _model()
    signal = SignalAssociation(
        identifier="s1", source_node_id="900", lane_ids=[], status="review_required"
    )
    assert "unassociated_signal" in _codes(
        _signal_issues(model.model_copy(update={"signals": [signal]}))
    )


def test_a_stop_line_naming_no_lane_is_reported() -> None:
    model = _model()
    stop_line = StopLine(
        identifier="sl1",
        source_node_id="900",
        lane_ids=[],
        points=_straight(49.0, 51.0),
        source="inferred",
        status="review_required",
    )
    assert "invalid_stop_line" in _codes(
        _signal_issues(model.model_copy(update={"stop_lines": [stop_line]}))
    )


# --- the extract boundary -----------------------------------------------------------------


def test_a_lane_stopping_where_the_road_stops_is_the_extract_edge() -> None:
    """junction-1 has 39 of these. Reporting them as errors would bury the real thing."""
    model = _model()
    issues, facts = _boundary_report(model, terminus_nodes={"1", "3"})

    assert issues == []
    assert facts["lanes_without_exit"] == 1
    assert facts["lanes_without_entry"] == 1
    assert facts["lanes_at_the_extract_edge"] == 2


def test_a_lane_stopping_mid_road_is_an_error() -> None:
    """The source road runs through the node; the lane does not. That is a dropped link."""
    model = _model()
    issues, _ = _boundary_report(model, terminus_nodes=set())

    assert _codes(issues) == {"interior_dead_end"}
    assert len(issues) == 2


def test_a_component_touching_no_boundary_is_isolated() -> None:
    """A ring road entirely inside the extract that nothing reaches goes nowhere."""
    ring = [
        _lane("x", x0=0.0, x1=10.0, exit_lanes=["y"], entry_lanes=["y"]),
        _lane("y", x0=10.0, x1=20.0, exit_lanes=["x"], entry_lanes=["x"]),
    ]
    model = _model().model_copy(update={"lanes": ring, "connectors": []})
    issues, facts = _boundary_report(model, terminus_nodes=set())

    assert "isolated_component" in _codes(issues)
    assert facts["routing_components"] == [2]


# --- dispositions -------------------------------------------------------------------------


def _finding(identifier: str, *, source_id: str) -> ReviewFinding:
    return ReviewFinding(
        identifier=identifier,
        rule="signal_lane_association",
        severity="blocker",
        source_type="node",
        source_ids=[source_id],
        affected_feature_ids=[],
        proposed_value=[],
        confidence="low",
        reason="signal has no generated approaching lane",
        evidence_checksum="evidence",
    )


def test_a_condition_judged_not_applicable_is_dispositioned() -> None:
    """Stage 5 re-derives conditions from the model, so it re-detects what a reviewer has
    already ruled out. Making them answer it twice would make the review pointless."""
    model = _model().model_copy(update={"findings": [_finding("f-1", source_id="900")]})
    comparison = {"finding_decisions": {"f-1": {"state": "decided", "status": "not_applicable"}}}

    assert _dispositioned_osm_ids(model, comparison) == {"900": "f-1"}


def test_a_condition_merely_accepted_is_not_dispositioned() -> None:
    """Accepting says the proposal was right, not that the condition does not matter."""
    model = _model().model_copy(update={"findings": [_finding("f-1", source_id="900")]})
    comparison = {"finding_decisions": {"f-1": {"state": "decided", "status": "accepted"}}}

    assert _dispositioned_osm_ids(model, comparison) == {}


# --- what the report says it examined -------------------------------------------------


def _report(model: PreliminaryLaneModel, **update: Any) -> dict[str, Any]:
    report = {
        "status": "passed",
        "validated_lane_model": {"sha256": "0" * 64},
        "checked": {
            "lanes": len(model.lanes),
            "connectors": len(model.connectors),
            "restrictions": len(model.restrictions),
            "signals": len(model.signals),
            "stop_lines": len(model.stop_lines),
            "checks": [
                "geometry",
                "references",
                "connectors",
                "restrictions",
                "signals",
                "boundary",
            ],
        },
        "errors": [],
        "warnings": [],
        "boundary": {
            "lanes_without_exit": 1,
            "lanes_without_entry": 1,
            "lanes_at_the_extract_edge": 2,
            "lane_ids_at_the_extract_edge": ["a", "b"],
            "routing_components": [2],
        },
    }
    report.update(update)
    return report


def test_the_page_colours_a_lane_by_what_validation_made_of_it() -> None:
    model = _model()
    report = _report(
        model,
        status="failed",
        errors=[_issue_row("non_finite_geometry", "200", lane_id="a")],
    )
    payload = _page_payload(
        render_validation_html(model=model, report=report, boundary_lane_ids={"b"})
    )
    roles = {feature["id"]: feature["role"] for feature in payload["features"]}

    assert roles["a"] == "error"
    assert roles["b"] == "boundary"


def test_a_boundary_lane_is_drawn_as_such_not_as_a_defect() -> None:
    """junction-1 has 39 of these. If they drew red the page would say the map is broken."""
    model = _model()
    payload = _page_payload(
        render_validation_html(model=model, report=_report(model), boundary_lane_ids={"a", "b"})
    )

    assert {feature["role"] for feature in payload["features"]} == {"boundary"}


def test_an_issue_with_no_drawn_feature_is_marked_rather_than_dead() -> None:
    """A signal is not drawn, so its row cannot fly anywhere. Saying so beats a click that
    silently does nothing."""
    model = _model()
    report = _report(
        model, warnings=[_issue_row("unassociated_signal", "900", signal_id="s1")]
    )
    html = render_validation_html(model=model, report=report, boundary_lane_ids=set())

    assert "no drawn feature" in html
    assert "frow flat" in html


def test_rendering_the_same_report_twice_is_byte_identical() -> None:
    """The page is checksummed into the Stage 5 manifest."""
    model = _model()
    report = _report(model)
    first = render_validation_html(model=model, report=report, boundary_lane_ids={"a"})
    second = render_validation_html(model=model, report=_report(model), boundary_lane_ids={"a"})

    assert first == second
