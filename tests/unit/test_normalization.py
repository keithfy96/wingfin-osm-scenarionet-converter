import hashlib
import json
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pytest
from pyproj import CRS

from osm_scenario.acquisition import acquire_osm
from osm_scenario.config import ConverterConfig, CoordinateOrigin
from osm_scenario.normalization import NormalizationError, normalize_workspace

FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_1a_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "map-workspace"
    source = workspace / "source" / "map.osm"
    source.parent.mkdir(parents=True)
    source.write_bytes(FIXTURE.read_bytes())
    acquire_osm(workspace=workspace, driving_side="left", osm_file=source)
    return workspace, source


def test_normalize_workspace_projects_and_reports_from_saved_graph(tmp_path: Path) -> None:
    workspace, source = _stage_1a_workspace(tmp_path)
    source_checksum = _sha256(source)

    report_path = normalize_workspace(
        workspace=workspace,
        config=ConverterConfig(config_version=1),
    )

    assert _sha256(source) == source_checksum
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["projection"]["source_crs"] == "EPSG:4326"
    assert report["projection"]["origin"]["source"] == "network_geometry_centroid"
    assert report["projection"]["axis_order"] == "x=east,y=north"
    assert report["projection"]["units"] == "metre"
    assert report["projection"]["round_trip"]["maximum_error_degrees"] <= 1e-9
    assert report["preflight"]["errors"] == []
    assert report["preflight"]["discarded_features"] == []
    assert report["preflight"]["inferred_values"] == []
    assert report["source_audit"]["status"] == "passed"
    assert report["stage_1a_to_1b_parity"]["status"] == "passed"

    graph_path = workspace / "normalized" / "road-network-local.graphml"
    gpkg_path = workspace / "normalized" / "road-network-local.gpkg"
    projected_graph = ox.load_graphml(graph_path)
    assert CRS.from_user_input(projected_graph.graph["crs"]).is_projected
    assert max(abs(float(data["x"])) for _, data in projected_graph.nodes(data=True)) < 100
    nodes = gpd.read_file(gpkg_path, layer="nodes")
    edges = gpd.read_file(gpkg_path, layer="edges")
    assert nodes.crs is not None and nodes.crs.is_projected
    assert edges.crs is not None and edges.crs.is_projected
    assert len(nodes) == report["feature_counts"]["nodes"]
    assert len(edges) == report["feature_counts"]["edges"]

    manifest = json.loads((workspace / "source" / "manifest.json").read_text())
    assert manifest["stage_1b"]["status"] == "passed"
    for artifact in manifest["stage_1b"]["artifacts"].values():
        artifact_path = workspace / artifact["path"]
        assert _sha256(artifact_path) == artifact["sha256"]


def test_normalize_workspace_uses_explicit_origin(tmp_path: Path) -> None:
    workspace, _ = _stage_1a_workspace(tmp_path)
    config = ConverterConfig(
        config_version=1,
        coordinate_origin=CoordinateOrigin(latitude=3.15, longitude=101.7),
    )

    report_path = normalize_workspace(workspace=workspace, config=config)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["projection"]["origin"] == {
        "latitude": 3.15,
        "longitude": 101.7,
        "source": "explicit_config",
    }


def test_normalize_workspace_is_offline_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, source = _stage_1a_workspace(tmp_path)
    config = ConverterConfig(config_version=1)
    source_checksum = _sha256(source)

    def reject_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("normalization attempted network access")

    monkeypatch.setattr(ox, "graph_from_place", reject_network)
    monkeypatch.setattr(ox, "graph_from_bbox", reject_network)
    first_report = normalize_workspace(workspace=workspace, config=config)
    second_report = normalize_workspace(workspace=workspace, config=config)

    assert first_report == second_report
    assert _sha256(source) == source_checksum
    assert json.loads(second_report.read_text(encoding="utf-8"))["status"] == "passed"


def test_normalize_workspace_rejects_changed_stage_1a_graph(tmp_path: Path) -> None:
    workspace, _ = _stage_1a_workspace(tmp_path)
    graph_path = workspace / "normalized" / "road-network.graphml"
    graph_path.write_text(graph_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(NormalizationError, match="checksum"):
        normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))


def test_normalize_workspace_requires_stage_1a_manifest(tmp_path: Path) -> None:
    with pytest.raises(NormalizationError, match="manifest not found"):
        normalize_workspace(
            workspace=tmp_path / "missing",
            config=ConverterConfig(config_version=1),
        )
