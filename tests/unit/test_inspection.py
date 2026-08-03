import hashlib
import json
from pathlib import Path

import osmnx as ox
from typer.testing import CliRunner

from osm_scenario.acquisition import acquire_osm
from osm_scenario.cli import app
from osm_scenario.config import ConverterConfig
from osm_scenario.inspection import generate_inspection
from osm_scenario.lanelet_generation import generate_preliminary_lanelet2
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


def _lanelet_workspace(tmp_path: Path) -> Path:
    workspace = _workspace(tmp_path)
    generate_preliminary_lanelet2(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    return workspace


def test_stage_3a_preliminary_inspection_is_searchable_and_checksum_bound(
    tmp_path: Path,
) -> None:
    workspace = _lanelet_workspace(tmp_path)
    preliminary = workspace / "lanelet2" / "preliminary.osm"
    preliminary_sha256 = hashlib.sha256(preliminary.read_bytes()).hexdigest()

    output = generate_inspection(
        workspace=workspace, view="lanelet2", checkpoint="preliminary"
    )

    assert output == workspace / "inspection" / "stage-3a-preliminary-audit.html"
    html = output.read_text(encoding="utf-8")
    assert "Stage 3A Preliminary Audit" in html
    assert "Road lanelets" in html
    assert "Junction connectors" in html
    assert "Correction queue lanelets" in html
    assert "Search identifier" in html
    assert "Source way ID" in html
    assert "Source node ID" in html
    report = json.loads(
        (workspace / "reports" / "inspection-stage-3a-preliminary.json").read_text()
    )
    assert report["stage"] == "3a"
    assert report["checkpoint"] == "preliminary"
    assert report["inputs"]["preliminary_lanelet2"]["sha256"] == preliminary_sha256
    assert report["layers"]["roads"] > 0
    assert report["layers"]["connectors"] > 0
    assert (workspace / "reports" / "inspection-stage-3a-preliminary.md").is_file()


def test_stage_3a_recreates_only_its_own_artifacts(tmp_path: Path) -> None:
    workspace = _lanelet_workspace(tmp_path)
    edited_audit = workspace / "inspection" / "stage-3c-edited-audit.html"
    edited_report = workspace / "reports" / "inspection-stage-3c-edited.json"
    edited_audit.parent.mkdir(parents=True, exist_ok=True)
    edited_audit.write_text("stage 3c must survive", encoding="utf-8")
    edited_report.write_text('{"stage":"3c"}\n', encoding="utf-8")
    preliminary_before = hashlib.sha256(
        (workspace / "lanelet2" / "preliminary.osm").read_bytes()
    ).hexdigest()

    first = generate_inspection(
        workspace=workspace, view="lanelet2", checkpoint="preliminary"
    )
    second = generate_inspection(
        workspace=workspace, view="lanelet2", checkpoint="preliminary"
    )

    assert first == second
    assert edited_audit.read_text(encoding="utf-8") == "stage 3c must survive"
    assert edited_report.read_text(encoding="utf-8") == '{"stage":"3c"}\n'
    assert hashlib.sha256(
        (workspace / "lanelet2" / "preliminary.osm").read_bytes()
    ).hexdigest() == preliminary_before


def test_stage_3a_rejects_untracked_preliminary_changes(tmp_path: Path) -> None:
    workspace = _lanelet_workspace(tmp_path)
    preliminary = workspace / "lanelet2" / "preliminary.osm"
    preliminary.write_text(preliminary.read_text() + "\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "inspect",
            "--workspace",
            str(workspace),
            "--view",
            "lanelet2",
            "--checkpoint",
            "preliminary",
        ],
    )

    assert result.exit_code == 1
    assert "does not match the Stage 2 manifest" in result.output


def test_lanelet2_inspection_requires_preliminary_checkpoint(tmp_path: Path) -> None:
    workspace = _lanelet_workspace(tmp_path)

    result = runner.invoke(
        app, ["inspect", "--workspace", str(workspace), "--view", "lanelet2"]
    )

    assert result.exit_code == 1
    assert "requires --checkpoint preliminary" in result.output


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


def test_audit_view_maps_stage_1b_findings_and_discloses_later_checks(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    output = generate_inspection(workspace=workspace, view="audit")

    assert output == workspace / "inspection" / "stage-1-audit.html"
    html = output.read_text(encoding="utf-8")
    report = json.loads((workspace / "reports" / "inspection-audit.json").read_text())
    assert "Missing lane counts" in html
    assert "Missing widths" in html
    assert "Connected components" in html
    assert "Connectivity and crossing legend" in html
    assert "Connected components (different colors)" in html
    assert "Tagged grade-separated crossings (blue markers)" in html
    assert "Ambiguous visual crossings (red markers)" in html
    assert "Different component colors show disconnected graph groups" in html
    assert "Retained traffic signals" in html
    assert "Excluded traffic signals" in html
    assert "Fully retained restrictions (purple, solid)" in html
    assert "Partial restrictions (cyan, dashed)" in html
    assert "Restriction via points (yellow markers)" not in html
    assert "color:'#6a1b9a',weight:6,opacity:.9" in html
    assert "color:'#00a6a6',weight:9,opacity:1,dashArray:'10 7'" in html
    assert "radius:9,color:'#111',weight:3,fillColor:'#ffe600',fillOpacity:1" in html
    assert "exact OSM via node" in html
    assert "showRestrictionVia(f.properties.relation_id)" in html
    assert "restrictionViaSelection.clearLayers()" in html
    assert "Stop-line candidates" in html
    assert "Direction tag conflicts" in html
    assert "Lanelet boundary shape" not in html
    assert "Lane junction connector" not in html
    assert "Signal-to-lanelet association" not in html
    assert "Inferred stop-line placement" not in html
    assert "Stage 2 Lanelet2 geometry does not exist yet" not in html
    assert "Lane inference enabled" in html
    assert "Search by OSM Way or Node ID" in html
    assert '<option value="node">Node</option>' in html
    assert "const searchableNodes=new Map" in html
    assert "Found Node" in html
    assert "radius:10,color:'#111',weight:3,fillColor:'#ffe600',fillOpacity:1" in html
    assert "Highlighted yellow" in html
    assert "is referenced by a restriction but is missing from source/map.osm" in html
    assert report["status"] == "review_required"
    assert report["search"]["indexed_source_way_count"] == 5
    assert report["search"]["indexed_source_node_count"] == 8
    assert "searchable_ways" not in report["layers"]
    assert "searchable_nodes" not in report["layers"]
    assert report["layers"]["selected"] == 2
    assert report["layers"]["missing_lanes"] == 1
    assert report["layers"]["missing_widths"] == 1
    assert report["layers"]["retained_restrictions"] == 2
    assert report["layers"]["restriction_via_points"] == 1
    assert report["layers"]["grade_separated_crossings"] == 0
    assert report["layers"]["ambiguous_crossings"] == 0
    assert "grade_separation" in report["coverage"]["source_review"]
    assert "post_stage_2" not in report["coverage"]


def test_inspect_cli_accepts_audit_view(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = runner.invoke(app, ["inspect", "--workspace", str(workspace), "--view", "audit"])

    assert result.exit_code == 0
    assert "stage-1-audit.html" in result.output


def test_inspect_cli_reports_output_and_missing_lanelet2(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = runner.invoke(app, ["inspect", "--workspace", str(workspace), "--view", "normalized"])
    assert result.exit_code == 0
    assert "Inspection created:" in result.output

    missing = runner.invoke(
        app,
        [
            "inspect",
            "--workspace",
            str(workspace),
            "--view",
            "lanelet2",
            "--checkpoint",
            "preliminary",
        ],
    )
    assert missing.exit_code == 1
    assert "Stage 3A requires completed Stage 2 artifacts" in missing.output
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
