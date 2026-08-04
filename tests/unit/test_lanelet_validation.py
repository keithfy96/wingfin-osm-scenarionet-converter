import json
from pathlib import Path

import pytest

from osm_scenario.acquisition import acquire_osm
from osm_scenario.config import ConverterConfig
from osm_scenario.lanelet_generation import generate_preliminary_lanelet2
from osm_scenario.lanelet_validation import LaneletValidationError, validate_lanelet2_workspace
from osm_scenario.normalization import normalize_workspace

FIXTURE = Path(__file__).parents[1] / "fixtures" / "osm" / "tiny.osm"


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    acquire_osm(
        workspace=workspace,
        driving_side="left",
        osm_file=FIXTURE,
        place=None,
        bbox=None,
    )
    config = ConverterConfig(config_version=1, driving_side="left")
    normalize_workspace(workspace=workspace, config=config)
    preliminary = generate_preliminary_lanelet2(workspace=workspace, config=config)
    preliminary.replace(workspace / "lanelet2" / "edited.osm")
    return workspace


def test_validation_requires_edited_map(tmp_path: Path) -> None:
    with pytest.raises(LaneletValidationError, match="reviewed map not found"):
        validate_lanelet2_workspace(tmp_path)


def test_validation_writes_checksum_bound_report_when_findings_block(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(LaneletValidationError, match="unwaived warning"):
        validate_lanelet2_workspace(workspace)

    report = json.loads((workspace / "reports" / "lanelet2-validation.json").read_text())
    assert report["stage"] == 4
    assert report["input"]["checkpoint"] == "edited"
    assert len(report["input"]["sha256"]) == 64
    assert report["counts"]["lanelets"] > 0
    assert report["native_validator"]["command"].startswith("lanelet2_validate")
    assert any(item["code"] == "review_record_missing" for item in report["findings"])
    assert any(item["code"] == "native_validator_not_run" for item in report["findings"])
