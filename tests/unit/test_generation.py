from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from osm_scenario.acquisition import acquire_osm
from osm_scenario.config import ConverterConfig
from osm_scenario.generation import (
    GenerationError,
    _direction_arrow,
    _directional_lane_count,
    _is_decision_node,
    _mapped_lane_index,
    _speed_kph,
    _turn_permissions,
    generate_lane_model,
)
from osm_scenario.lane_model import Point2D, PreliminaryLaneModel
from osm_scenario.normalization import normalize_workspace

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
    assert "OpenStreetMap contributors" in html
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
