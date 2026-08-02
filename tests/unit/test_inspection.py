import json
from pathlib import Path

import osmnx as ox
from typer.testing import CliRunner

from osm_scenario.acquisition import acquire_osm
from osm_scenario.cli import app
from osm_scenario.config import ConverterConfig
from osm_scenario.inspection import generate_inspection
from osm_scenario.normalization import normalize_workspace
from osm_scenario.osm_source import read_osm_snapshot, select_public_driving_graph

runner = CliRunner()
FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "map-workspace"
    source = workspace / "source" / "map.osm"
    source.parent.mkdir(parents=True)
    source.write_bytes(FIXTURE.read_bytes())
    acquire_osm(workspace=workspace, driving_side="left", osm_file=source)
    normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))
    return workspace


def test_stage_1_inspection_contains_traceable_layers(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    output = generate_inspection(workspace=workspace, view="stage-1")

    html = output.read_text(encoding="utf-8")
    assert "Selected source roads" in html
    assert "Excluded source highways" in html
    assert "Stage 1B projected overlay" in html
    assert "Traffic signals" in html
    assert "operator:test" in html
    report = json.loads((workspace / "reports" / "inspection-stage-1.json").read_text())
    assert report["status"] == "passed"
    assert report["layers"]["selected"] == 2
    assert report["layers"]["excluded"] == 3
    assert report["layers"]["projected"] > 0


def test_source_view_omits_projected_layer_features(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    generate_inspection(workspace=workspace, view="source")

    output = workspace / "inspection" / "stage-1-source.html"
    html = output.read_text(encoding="utf-8")
    report = json.loads((workspace / "reports" / "inspection-source.json").read_text())
    assert report["layers"]["projected"] == 0
    assert "Stage 1B projected overlay" not in html
    assert "Stage 1B projected geometry" not in html

    second_output = generate_inspection(workspace=workspace, view="source")
    assert second_output == workspace / "inspection" / "stage-1-source.html"


def test_normalized_view_contains_only_projected_layer(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    output = generate_inspection(workspace=workspace, view="normalized")

    html = output.read_text(encoding="utf-8")
    report = json.loads((workspace / "reports" / "inspection-normalized.json").read_text())
    assert report["layers"]["projected"] > 0
    assert all(
        count == 0
        for name, count in report["layers"].items()
        if name != "projected"
    )
    assert "Stage 1B projected overlay" in html
    assert "Selected public driving road" not in html
    assert "Excluded source highway" not in html
    assert "Preflight warning" not in html


def test_inspect_cli_reports_output_and_missing_lanelet2(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = runner.invoke(app, ["inspect", "--workspace", str(workspace), "--view", "normalized"])
    assert result.exit_code == 0
    assert "Inspection created:" in result.output

    missing = runner.invoke(app, ["inspect", "--workspace", str(workspace), "--view", "lanelet2"])
    assert missing.exit_code == 1
    assert "Stage 2 has not produced" in missing.output
    assert "Traceback" not in missing.output


def test_source_audit_filters_non_driving_ways_and_preserves_all_tags(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    manifest = json.loads((workspace / "source" / "manifest.json").read_text())

    audit = manifest["road_selection"]
    assert audit["status"] == "passed"
    assert audit["policy_id"] == "public-driving-v1"
    assert audit["selected_source_ways"] == 2
    assert audit["excluded_source_ways"] == 3
    assert audit["missing_way_ids"] == []
    assert audit["direction_mismatches"] == []

    graph = ox.load_graphml(workspace / "normalized" / "road-network.graphml")
    osmids = {str(data["osmid"]) for *_, data in graph.edges(keys=True, data=True)}
    assert osmids == {"10", "11"}
    toll_edge = next(data for *_, data in graph.edges(data=True) if str(data["osmid"]) == "11")
    stored_tags = toll_edge["osm_tags_json"]
    source_tags = (json.loads(stored_tags) if isinstance(stored_tags, str) else stored_tags)["11"]
    assert source_tags["toll"] == "yes"
    assert source_tags["operator:test"] == "retained exactly"


def test_source_audit_detects_a_missing_travel_direction() -> None:
    snapshot = read_osm_snapshot(FIXTURE)
    graph = ox.graph_from_xml(FIXTURE, simplify=False, retain_all=True)
    edge = next(
        (u, v, key)
        for u, v, key, data in graph.edges(keys=True, data=True)
        if str(data["osmid"]) == "10"
    )
    graph.remove_edge(*edge)

    _, audit = select_public_driving_graph(graph, snapshot)

    assert audit["status"] == "failed"
    assert audit["direction_mismatches"]
    assert any(error["code"] == "direction_mismatch" for error in audit["errors"])
