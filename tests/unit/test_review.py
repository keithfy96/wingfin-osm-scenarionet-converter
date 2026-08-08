"""Stage 3 review view: payload assembly, identity binding, and HTML shell."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from osm_scenario.acquisition import acquire_osm
from osm_scenario.config import ConverterConfig
from osm_scenario.generation import generate_lane_model
from osm_scenario.inspection import InspectionError, generate_inspection
from osm_scenario.normalization import normalize_workspace
from osm_scenario.review import (
    PAYLOAD_VERSION,
    ReviewError,
    build_payload,
    client_source,
    generate_review,
    render_review_html,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"


def _generated_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    config = ConverterConfig(config_version=1)
    acquire_osm(workspace=workspace, driving_side="left", osm_file=FIXTURE)
    normalize_workspace(workspace=workspace, config=config)
    generate_lane_model(workspace=workspace, config=config)
    return workspace


def _embedded_payload(html: str) -> dict:
    match = re.search(r"__REVIEW_PAYLOAD__=(.*?);</script>", html, re.S)
    assert match is not None, "the page must embed a review payload"
    return json.loads(match.group(1))


def test_payload_binds_to_the_exact_source_and_generation_it_was_built_from(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    payload = build_payload(workspace)
    report = json.loads((workspace / "reports" / "lane-model-generation.json").read_text())
    manifest = json.loads((workspace / "source" / "manifest.json").read_text())

    # Every component of the identity is what a draft and a submission are keyed on,
    # so a drifting value must fail loudly rather than silently rebind a review.
    assert payload["payload_version"] == PAYLOAD_VERSION
    assert payload["identity"] == {
        "workspace": workspace.name,
        "source_checksum": manifest["source"]["sha256"],
        "generation_fingerprint": report["generation_fingerprint"],
        "generator_version": report["generator_version"],
        "lane_model_schema_version": report["lane_model_schema_version"],
        "configuration_checksum": payload["identity"]["configuration_checksum"],
        "generated_at": report["generated_at"],
    }
    assert payload["identity"]["configuration_checksum"]


def test_payload_carries_the_map_the_stage_2_audit_draws(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    payload = build_payload(workspace)
    model = json.loads((workspace / "lane-model" / "preliminary.json").read_text())

    assert len(payload["lanes"]) == len(model["lanes"])
    assert len(payload["connectors"]) == len(model["connectors"])
    assert len(payload["findings"]) == len(model["findings"])
    assert payload["features"], "the review view must draw the generated network"
    kinds = {feature["properties"].get("kind") for feature in payload["features"]}
    assert {"lane_centerline", "lane_polygon"} <= kinds
    latitude, longitude = payload["center"]
    assert -90 <= latitude <= 90 and -180 <= longitude <= 180


def test_every_finding_a_bulk_action_could_cover_carries_a_road_class(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    payload = build_payload(workspace)

    # Bulk decisions are scoped by rule *and* road class. A default-value finding
    # without one could never be swept, which is the whole point of the cohort.
    bulkable = [
        finding
        for finding in payload["findings"]
        if finding["rule"] in {"speed_default", "lane_width_default"}
    ]
    assert bulkable
    assert all(finding["road_class"] for finding in bulkable)


def test_connector_findings_inherit_the_road_class_of_the_lane_they_leave(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    payload = build_payload(workspace)
    connectors = {item["identifier"]: item for item in payload["connectors"]}
    lanes = {item["identifier"]: item for item in payload["lanes"]}

    # A connector id names neither a lane nor a way, so without the inheritance step
    # every connector finding arrives unscoped.
    for finding in payload["findings"]:
        connector_ids = [item for item in finding["affected_feature_ids"] if item in connectors]
        if not connector_ids:
            continue
        source_lane = lanes[connectors[connector_ids[0]]["from_lane_id"]]
        assert finding["road_class"] == source_lane["road_class"]


def test_review_refuses_a_model_that_disagrees_with_its_generation_report(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    model_path = workspace / "lane-model" / "preliminary.json"
    model = json.loads(model_path.read_text())
    model["metadata"]["generation_fingerprint"] = "0" * 64
    model_path.write_text(json.dumps(model))

    # Reviewing a model that is not the one the report describes would bind decisions
    # to a fingerprint that never produced this geometry.
    with pytest.raises(ReviewError, match="fingerprint"):
        build_payload(workspace)


def test_review_refuses_a_workspace_that_has_not_been_generated(tmp_path: Path) -> None:
    workspace = tmp_path / "empty"
    workspace.mkdir()
    with pytest.raises(ReviewError, match="missing"):
        build_payload(workspace)


def test_rendered_page_is_self_contained_and_cannot_break_out_of_its_script_tag(
    tmp_path: Path,
) -> None:
    workspace = _generated_workspace(tmp_path)
    payload = build_payload(workspace)
    html = render_review_html(payload)

    assert client_source() in html
    assert _embedded_payload(html)["identity"] == payload["identity"]
    # A `</script>` inside the JSON would end the tag early and break the page.
    body = html.split("__REVIEW_PAYLOAD__=", 1)[1].split(";</script>", 1)[0]
    assert "</" not in body


def test_generate_review_writes_the_stage_3_view(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    output = generate_review(workspace=workspace)

    assert output == workspace / "inspection" / "stage-3-review.html"
    assert _embedded_payload(output.read_text())["findings"]


def test_inspect_review_view_reaches_the_stage_3_renderer(tmp_path: Path) -> None:
    workspace = _generated_workspace(tmp_path)
    assert generate_inspection(workspace=workspace, view="review").name == "stage-3-review.html"


def test_inspect_review_reports_a_missing_lane_model_as_an_inspection_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "empty"
    workspace.mkdir()
    # The CLI catches InspectionError; a bare ReviewError would escape as a traceback.
    with pytest.raises(InspectionError):
        generate_inspection(workspace=workspace, view="review")
