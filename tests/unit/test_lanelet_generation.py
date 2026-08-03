import hashlib
import json
from pathlib import Path

from lanelet2 import io, projection
from typer.testing import CliRunner

from osm_scenario.acquisition import acquire_osm
from osm_scenario.cli import app
from osm_scenario.config import ConverterConfig
from osm_scenario.lanelet_generation import generate_preliminary_lanelet2
from osm_scenario.normalization import normalize_workspace

FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"
runner = CliRunner()


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "map-workspace"
    source = workspace / "source" / "map.osm"
    source.parent.mkdir(parents=True)
    source.write_bytes(FIXTURE.read_bytes())
    acquire_osm(workspace=workspace, driving_side="left", osm_file=source)
    normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))
    return workspace, source


def test_generate_preliminary_lanelet2_is_reloadable_and_traceable(tmp_path: Path) -> None:
    workspace, source = _workspace(tmp_path)
    source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    output = generate_preliminary_lanelet2(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )

    assert output == workspace / "lanelet2" / "preliminary.osm"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_checksum
    report = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())
    origin = report["configuration"]["origin"]
    lanelet_map, errors = io.loadRobust(
        str(output),
        projection.LocalCartesianProjector(io.Origin(origin["latitude"], origin["longitude"])),
    )
    assert errors == []
    assert len(lanelet_map.laneletLayer) == (
        report["counts"]["road_lanelets"] + report["counts"]["connector_lanelets"]
    )
    assert report["counts"]["road_lanelets"] > 0
    assert report["counts"]["connector_lanelets"] > 0
    assert report["counts"]["traffic_light_associations"] > 0
    assert report["counts"]["inferred_stop_lines"] > 0
    assert report["parser_errors"] == []
    assert all(
        record["source_osm_way_id"] in {"10", "11"}
        for record in report["lanelets"]
        if record["kind"] == "road"
    )
    assert all(lanelet.leftBound and lanelet.rightBound for lanelet in lanelet_map.laneletLayer)
    assert any(item["code"] == "lane_count_inferred" for item in report["inferences"])
    assert any(item["code"] == "stop_line_inferred" for item in report["inferences"])
    assert (workspace / "reports" / "lanelet2-generation.md").is_file()


def test_generate_preliminary_lanelet2_recreates_with_stable_ids(tmp_path: Path) -> None:
    workspace, source = _workspace(tmp_path)
    source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    generate_preliminary_lanelet2(workspace=workspace, config=ConverterConfig(config_version=1))
    first = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())
    first_ids = [item["lanelet_id"] for item in first["lanelets"]]
    generate_preliminary_lanelet2(workspace=workspace, config=ConverterConfig(config_version=1))
    second = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())

    assert [item["lanelet_id"] for item in second["lanelets"]] == first_ids
    assert len(first_ids) == len(set(first_ids))
    assert all(identifier > 0 for identifier in first_ids)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_checksum
    manifest = json.loads((workspace / "source" / "manifest.json").read_text())
    assert (
        manifest["stage_2"]["artifacts"]["preliminary_lanelet2"]["sha256"]
        == hashlib.sha256((workspace / "lanelet2" / "preliminary.osm").read_bytes()).hexdigest()
    )


def test_generate_preliminary_lanelet2_reuses_nearby_mapped_stop_line(tmp_path: Path) -> None:
    workspace = tmp_path / "map-workspace"
    source = workspace / "source" / "map.osm"
    source.parent.mkdir(parents=True)
    source.write_text(
        FIXTURE.read_text().replace(
            '  <relation id="20">',
            """  <way id="15">
    <nd ref="1" />
    <nd ref="3" />
    <tag k="road_marking" v="stop_line" />
  </way>
  <relation id="20">""",
        )
    )
    acquire_osm(workspace=workspace, driving_side="left", osm_file=source)
    normalize_workspace(workspace=workspace, config=ConverterConfig(config_version=1))

    generate_preliminary_lanelet2(workspace=workspace, config=ConverterConfig(config_version=1))

    report = json.loads((workspace / "reports" / "lanelet2-generation.json").read_text())
    assert report["counts"]["mapped_stop_lines"] > 0
    assert report["counts"]["inferred_stop_lines"] == 0
    associations = [
        item
        for item in report["correction_queue"]
        if item["code"] == "traffic_signal_association_review"
    ]
    assert associations
    assert all(item["source_osm_stop_line_way_id"] == "15" for item in associations)


def test_generate_lanelet2_cli_runs_stage_2(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    result = runner.invoke(app, ["generate-lanelet2", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert "Stage 2 preliminary Lanelet2 created" in result.output
    assert (workspace / "lanelet2" / "preliminary.osm").is_file()
