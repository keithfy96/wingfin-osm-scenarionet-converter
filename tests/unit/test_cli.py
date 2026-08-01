import hashlib
import json
from pathlib import Path

import geopandas as gpd
import osmnx as ox
from typer.testing import CliRunner

from osm_scenario.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"


def test_top_level_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "fetch",
        "generate-lanelet2",
        "inspect",
        "validate-lanelet2",
        "convert",
        "validate-scenario",
    ):
        assert command in result.stdout


def test_fetch_requires_exactly_one_source() -> None:
    result = runner.invoke(app, ["fetch"])

    assert result.exit_code != 0
    assert "provide exactly one" in result.output
    assert "Traceback" not in result.output


def test_fetch_requires_explicit_driving_side() -> None:
    result = runner.invoke(app, ["fetch", "--osm-file", str(FIXTURE)])

    assert result.exit_code != 0
    assert "provide --driving-side" in result.output


def test_fetch_local_file_generates_reloadable_stage_1a_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "map-workspace"
    source_dir = workspace / "source"
    source_dir.mkdir(parents=True)
    source = source_dir / "map.osm"
    source.write_bytes(FIXTURE.read_bytes())
    checksum_before = hashlib.sha256(source.read_bytes()).hexdigest()

    result = runner.invoke(
        app,
        [
            "fetch",
            "--osm-file",
            str(source),
            "--workspace",
            str(workspace),
            "--driving-side",
            "left",
        ],
    )

    assert result.exit_code == 0, result.output
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum_before

    graph_path = workspace / "normalized" / "road-network.graphml"
    gpkg_path = workspace / "normalized" / "road-network.gpkg"
    manifest_path = source_dir / "manifest.json"
    graph = ox.load_graphml(graph_path)
    assert len(graph.nodes) > 0
    assert len(graph.edges) > 0
    assert graph.is_directed()
    assert any(data.get("turn:lanes") == "through|right" for *_, data in graph.edges(data=True))
    nodes = gpd.read_file(gpkg_path, layer="nodes")
    edges = gpd.read_file(gpkg_path, layer="edges")
    assert len(nodes) > 0
    assert len(edges) > 0
    assert nodes.crs is not None
    assert edges.crs is not None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["type"] == "local_file"
    assert manifest["source"]["sha256"] == checksum_before
    assert manifest["driving_side"] == "left"
    assert manifest["driving_side_source"] == "explicit_cli"
    assert manifest["graph"]["simplified"] is False
    assert manifest["artifacts"]["graphml"]["path"] == "normalized/road-network.graphml"
    for artifact in manifest["artifacts"].values():
        artifact_path = (workspace / artifact["path"]).resolve()
        assert artifact_path.is_relative_to(workspace.resolve())
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]
    assert '<relation id="20">' in source.read_text(encoding="utf-8")


def test_workspace_commands_require_workspace() -> None:
    for command in (
        "generate-lanelet2",
        "inspect",
        "validate-lanelet2",
        "convert",
        "validate-scenario",
    ):
        result = runner.invoke(app, [command])
        assert result.exit_code != 0
        assert "workspace" in result.output.lower()
        assert "Traceback" not in result.output
