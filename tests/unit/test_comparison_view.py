"""Stage 4 — the comparison page, as a thing a reader can check.

The page is a checksummed artifact, so what it must guarantee is narrow: every finding
the model carries reaches it, in an order that is a function of the model, and every
status the side panel counts is a status the map actually draws.
"""

from __future__ import annotations

import json
import re
from typing import Any

from osm_scenario.apply_review import _comparison
from osm_scenario.comparison_view import render_comparison_html
from osm_scenario.lane_model import (
    ConnectorFeature,
    FindingLocation,
    FindingSource,
    GenerationMetadata,
    GeoPoint,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
    ReviewFinding,
)


def _lane(identifier: str, *, exits: list[str] | None = None) -> LaneFeature:
    return LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=["1", "2", "0"],
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=3.5,
        speed_limit_kph=50.0,
        centerline=[Point2D(x=101.61, y=3.18), Point2D(x=101.62, y=3.19)],
        polygon=[Point2D(x=101.61, y=3.18)],
        boundaries=[],
        exit_lanes=exits or ["somewhere"],
    )


def _connector(identifier: str, *, status: str) -> ConnectorFeature:
    return ConnectorFeature(
        identifier=identifier,
        junction_node_id="900",
        from_lane_id="a",
        to_lane_id="b",
        from_way_id="200",
        to_way_id="201",
        movement="right",
        turn_angle_degrees=-88.4,
        status=status,  # type: ignore[arg-type]
        centerline=[Point2D(x=101.61, y=3.18), Point2D(x=101.615, y=3.185)],
        polygon=[Point2D(x=101.61, y=3.18)],
    )


def _finding(identifier: str, *, rule: str, severity: str, located: bool = True) -> ReviewFinding:
    return ReviewFinding(
        identifier=identifier,
        rule=rule,
        severity=severity,  # type: ignore[arg-type]
        source_type="way" if located else "edge",
        source_ids=["200"],
        affected_feature_ids=["lane-1"],
        # `lane_count_inference` is a way-level question keyed on `direction`, so the
        # comparison refuses a proposal without one. See `_WAY_LEVEL_QUESTIONS`.
        proposed_value={"direction": "forward", "lane_count": 1},
        confidence="low",
        reason="default_single_lane",
        evidence_checksum="evidence",
        location=(
            FindingLocation(
                lat=3.18,
                lon=101.61,
                bbox=[101.60, 3.17, 101.62, 3.19],
                sources=[
                    FindingSource(ref="way:200", coordinates=[GeoPoint(lat=3.18, lon=101.61)])
                ],
            )
            if located
            else None
        ),
    )


def _model(
    *,
    findings: list[ReviewFinding] | None = None,
    connectors: list[ConnectorFeature] | None = None,
) -> PreliminaryLaneModel:
    return PreliminaryLaneModel(
        metadata=GenerationMetadata(
            generator_version="test",
            lane_model_schema_version=1,
            source_checksum="source",
            projected_graph_checksum="graph",
            configuration_checksum="config",
            generation_fingerprint="fingerprint",
            # The renderer projects to EPSG:4326, so the fixture states a CRS rather
            # than the empty WKT the comparison-only tests get away with.
            coordinate_system_wkt="EPSG:4326",
        ),
        lanes=[_lane("lane-1")],
        connectors=connectors or [],
        findings=findings or [],
    )


def _render(model: PreliminaryLaneModel, *, applied: dict[str, Any] | None = None) -> str:
    comparison = _comparison(
        preliminary=model,
        reviewed=model,
        applied=applied
        or {
            "forbidden_connector_ids": [],
            "activated_connector_ids": [],
            "left_open_as_not_applicable": [],
        },
    )
    return render_comparison_html(preliminary=model, reviewed=model, comparison=comparison)


def _payload(html: str) -> dict[str, Any]:
    match = re.search(r"const DATA = (\{.*?\});", html, re.S)
    assert match is not None
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_every_finding_reaches_the_page() -> None:
    model = _model(
        findings=[
            _finding("f-blocker", rule="lane_count_inference", severity="blocker"),
            _finding("f-warning", rule="speed_default", severity="warning"),
        ]
    )
    html = _render(model)

    assert [item["id"] for item in _payload(html)["findings"]] == ["f-blocker", "f-warning"]
    assert html.count("class='frow'") == 2
    assert "<h2>Blockers still in the model: 1</h2>" in html
    assert "<summary>Warnings: 1</summary>" in html


def test_blockers_sort_ahead_of_warnings_whatever_order_the_model_holds() -> None:
    """The reader sees the 27 that block promotion first, not whichever generated first."""
    model = _model(
        findings=[
            _finding("z-warning", rule="speed_default", severity="warning"),
            _finding("a-blocker", rule="turn_permission_geometry_conflict", severity="blocker"),
        ]
    )
    severities = [item["severity"] for item in _payload(_render(model))["findings"]]
    assert severities == ["blocker", "warning"]


def test_a_finding_with_no_geometry_still_lists() -> None:
    """`edge`-scoped findings carry no location. They list; they just do not fly to."""
    model = _model(
        findings=[
            _finding("f-edge", rule="lane_count_inference", severity="blocker", located=False)
        ]
    )
    finding = _payload(_render(model))["findings"][0]

    assert finding["lat"] is None
    assert finding["bounds"] is None
    assert "class='frow'" in _render(model)


def test_a_finding_carries_the_features_it_affects() -> None:
    """Without these the row cannot highlight anything when the reader clicks it."""
    model = _model(findings=[_finding("f", rule="lane_count_inference", severity="blocker")])
    payload = _payload(_render(model))
    drawn = {feature["id"] for feature in payload["features"]}

    assert payload["findings"][0]["features"] == ["lane-1"]
    assert set(payload["findings"][0]["features"]) <= drawn


def test_a_connector_forbidden_before_the_review_is_still_drawn() -> None:
    """The side panel counts it under `forbidden`; a status counted but not drawn is a
    number the reader cannot check against the map."""
    model = _model(connectors=[_connector("c-osm", status="forbidden")])
    payload = _payload(_render(model))
    changes = {feature["id"]: feature["change"] for feature in payload["features"]}

    assert changes["c-osm"] == "forbidden_before"


def test_a_connector_the_review_forbade_is_told_apart_from_one_osm_already_had() -> None:
    model = _model(
        connectors=[
            _connector("c-osm", status="forbidden"),
            _connector("c-review", status="forbidden"),
        ]
    )
    html = _render(
        model,
        applied={
            "forbidden_connector_ids": ["c-review"],
            "activated_connector_ids": [],
            "left_open_as_not_applicable": [],
        },
    )
    changes = {feature["id"]: feature["change"] for feature in _payload(html)["features"]}

    assert changes["c-review"] == "forbidden"
    assert changes["c-osm"] == "forbidden_before"


def test_rendering_the_same_model_twice_is_byte_identical() -> None:
    """The page is checksummed into the Stage 4 manifest, so it cannot carry a set
    iteration order or a timestamp."""
    model = _model(
        findings=[
            _finding("f-2", rule="speed_default", severity="warning"),
            _finding("f-1", rule="lane_count_inference", severity="blocker"),
        ],
        connectors=[_connector("c", status="forbidden")],
    )
    assert _render(model) == _render(model)
