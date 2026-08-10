"""Stage 6 — converting the validated map into a map-only ScenarioNet dataset.

The gate tests use a real workspace directory because the gate's whole job is to read the
manifest off disk; the rest work on a model in memory, because the conversion itself never
touches the filesystem.
"""

from __future__ import annotations

import importlib.util
import json
import math
import pickle
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from osm_scenario.config import ConverterConfig
from osm_scenario.conversion import (
    ConversionError,
    _lane_neighbours,
    _reachability,
    _scenario,
    convert_scenario,
    scenario_file_name,
)
from osm_scenario.lane_model import (
    ConnectorFeature,
    LaneBoundary,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
)

WIDTH = 4.0


def _straight(x0: float, x1: float) -> list[Point2D]:
    return [Point2D(x=x0, y=0.0), Point2D(x=x1, y=0.0)]


def _surface(x0: float, x1: float, width: float = WIDTH) -> list[Point2D]:
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


def _connector(identifier: str, **update: Any) -> ConnectorFeature:
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
        centerline=_straight(50.0, 60.0),
        polygon=_surface(50.0, 60.0),
    )
    return connector.model_copy(update=update) if update else connector


_METADATA = {
    "generator_version": "test",
    "lane_model_schema_version": 1,
    "source_checksum": "source",
    "projected_graph_checksum": "graph",
    "configuration_checksum": "config",
    "generation_fingerprint": "fingerprint",
    "coordinate_system_wkt": "EPSG:4326",
}


def _model(**update: Any) -> PreliminaryLaneModel:
    """Lane `a` joins lane `b` through connector `c`, and `b` continues into `d`.

    So the fixture carries both kinds of reference the converter has to tell apart: a
    connector id at the junction, and a bare lane id for the continuation.
    """
    a = _lane("a", x0=0.0, x1=50.0, exit_lanes=["c"])
    b = _lane(
        "b",
        x0=60.0,
        x1=110.0,
        entry_lanes=["c"],
        exit_lanes=["d"],
        source_edge=["2", "3", "0"],
    )
    d = _lane("d", x0=110.0, x1=160.0, entry_lanes=["b"], source_edge=["3", "4", "0"])
    model = PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [a.model_dump(), b.model_dump(), d.model_dump()],
            "connectors": [_connector("c").model_dump()],
        }
    )
    return model.model_copy(update=update) if update else model


def _built(model: PreliminaryLaneModel) -> dict[str, Any]:
    scenario, _ = _scenario(
        model=model,
        workspace_name="test-workspace",
        manifest={"source": {"sha256": "src"}, "stage_5": {"status": "passed"}},
        model_sha256="model",
    )
    return scenario


# --- the gate ---------------------------------------------------------------------------


def _workspace(tmp_path: Path, model: PreliminaryLaneModel, **stage_5: Any) -> Path:
    workspace = tmp_path / "junction-x"
    (workspace / "source").mkdir(parents=True)
    (workspace / "lane-model").mkdir(parents=True)
    model_path = workspace / "lane-model" / "reviewed.json"
    model_path.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    import hashlib

    manifest: dict[str, Any] = {"source": {"sha256": "src", "path": "source/map.osm"}}
    if stage_5.get("present", True):
        manifest["stage_5"] = {
            "status": stage_5.get("status", "passed"),
            "validated_lane_model": {
                "sha256": stage_5.get(
                    "sha256", hashlib.sha256(model_path.read_bytes()).hexdigest()
                )
            },
        }
    (workspace / "source" / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return workspace


def test_conversion_refuses_a_workspace_that_never_ran_stage_5(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _model(), present=False)
    with pytest.raises(ConversionError, match="Stage 5 has not passed"):
        convert_scenario(workspace=workspace, config=ConverterConfig(config_version=1))


def test_conversion_refuses_a_failed_validation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _model(), status="failed")
    with pytest.raises(ConversionError, match="Stage 5 has not passed"):
        convert_scenario(workspace=workspace, config=ConverterConfig(config_version=1))


def test_conversion_refuses_a_model_edited_after_validation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _model(), sha256="a-checksum-from-some-other-model")
    with pytest.raises(ConversionError, match="changed after it was validated"):
        convert_scenario(workspace=workspace, config=ConverterConfig(config_version=1))


def test_a_passing_workspace_converts_and_records_stage_6(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _model())
    scenario_path, summary_path, mapping_path, report_path = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    scenario = pickle.loads(scenario_path.read_bytes())
    assert set(scenario["map_features"]) == {"a", "b", "d"}

    # Both index files key on the same computed filename, and it is the one MetaDrive will
    # accept - not a name we found readable.
    name = scenario_file_name(scenario["id"])
    assert scenario_path.name == name
    assert pickle.loads(summary_path.read_bytes()) == {name: scenario["metadata"]}
    assert pickle.loads(mapping_path.read_bytes()) == {name: ""}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["map_features"] == 3
    assert report["scenario_file"] == name
    manifest = json.loads((workspace / "source" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage_6"]["status"] == "converted"
    assert manifest["stage_6"]["scenario_id"] == scenario["id"]


def test_the_scenario_file_is_named_the_way_metadrive_demands() -> None:
    """`ScenarioDescription.is_scenario_file` accepts `sd_*` or all-digits, nothing else.

    `read_dataset_summary` asserts it for every entry in the summary, so a friendly name
    like `scenario.pkl` produces a dataset that loads nowhere.
    """
    name = scenario_file_name("junction-1-abc123")
    assert name.startswith("sd_")
    assert name.endswith(".pkl")


# --- the two kinds of id ------------------------------------------------------------------


def test_a_connector_on_an_exit_becomes_the_lane_it_leads_to() -> None:
    entries, exits = _lane_neighbours(_model())["a"]
    assert entries == []
    # Not "c". The reviewer's map names the connector; ScenarioNet wants the far lane.
    assert exits == ["b"]


def test_a_connector_on_an_entry_becomes_the_lane_it_comes_from() -> None:
    entries, exits = _lane_neighbours(_model())["b"]
    assert entries == ["a"]
    # And the plain lane id on the other side is passed through untouched.
    assert exits == ["d"]


def test_a_forbidden_connector_is_dropped_rather_than_followed() -> None:
    model = _model(connectors=[_connector("c", status="forbidden")])
    neighbours = _lane_neighbours(model)
    assert neighbours["a"][1] == []
    assert neighbours["b"][0] == []
    # The lanes survive; only the movement the review forbade is gone.
    assert set(_built(model)["map_features"]) == {"a", "b", "d"}


def test_an_unknown_reference_names_the_lane_that_holds_it() -> None:
    model = _model(lanes=[*_model().lanes[:2], _lane("d", entry_lanes=["nowhere"])])
    with pytest.raises(ConversionError, match="lane d names nowhere as an entry"):
        _lane_neighbours(model)


def test_a_connector_listed_on_the_wrong_lane_is_refused() -> None:
    model = _model(connectors=[_connector("c", to_lane_id="d")])
    with pytest.raises(ConversionError, match="connector c is listed on lane b"):
        _lane_neighbours(model)


# --- the scenario ------------------------------------------------------------------------


def test_the_scenario_carries_no_traffic() -> None:
    scenario = _built(_model())
    assert scenario["tracks"] == {}
    assert scenario["dynamic_map_states"] == {}
    assert scenario["length"] == 1
    assert scenario["metadata"]["map_only"] is True


def test_every_lane_a_feature_points_at_is_itself_a_feature() -> None:
    scenario = _built(_model())
    features = scenario["map_features"]
    referenced = {
        target
        for feature in features.values()
        if feature["type"] == "LANE_SURFACE_STREET"
        for target in (*feature["entry_lanes"], *feature["exit_lanes"])
    }
    assert referenced <= set(features)
    assert referenced == {"a", "b", "d"}


def test_boundaries_become_their_own_features() -> None:
    boundary = LaneBoundary(identifier="edge-1", side="left", points=_straight(0.0, 50.0))
    model = _model(lanes=[_lane("a", boundaries=[boundary])])
    features = _built(model)["map_features"]
    assert features["edge-1"]["type"] == "ROAD_EDGE_BOUNDARY"
    assert features["edge-1"]["lane_id"] == "a"
    assert features["edge-1"]["polyline"].shape == (2, 2)


def test_a_boundary_sharing_a_lane_id_is_refused() -> None:
    boundary = LaneBoundary(identifier="b", side="left", points=_straight(0.0, 50.0))
    model = _model(lanes=[*_model().lanes[:2], _lane("d", boundaries=[boundary])])
    with pytest.raises(ConversionError, match="shares an id"):
        _built(model)


def test_provenance_names_the_model_it_was_built_from() -> None:
    provenance = _built(_model())["metadata"]["provenance"]
    assert provenance["reviewed_lane_model_sha256"] == "model"
    assert provenance["generation_fingerprint"] == "fingerprint"
    assert provenance["source_osm_sha256"] == "src"
    assert provenance["stage_5_status"] == "passed"


def test_metadata_carries_the_three_keys_metadrive_requires() -> None:
    """`ScenarioDescription.METADATA_KEYS`, and the shape `sanity_check` reads off `ts`.

    Stated here as well as in the sanity-check test so the requirement survives on a
    machine with no MetaDrive checkout, where that test skips.
    """
    scenario = _built(_model())
    metadata = scenario["metadata"]
    assert {"metadrive_processed", "coordinate", "ts"} <= set(metadata)
    assert metadata["metadrive_processed"] is False
    assert metadata["ts"].shape == (scenario["length"],)


def test_neighbours_are_lists_even_when_there_is_no_neighbour() -> None:
    lanes = [_lane("a", left_neighbor=None, right_neighbor="b"), _lane("b")]
    features = _built(_model(lanes=lanes, connectors=[]))["map_features"]
    assert features["a"]["left_neighbor"] == []
    assert features["a"]["right_neighbor"] == ["b"]


# --- MetaDrive's own schema -----------------------------------------------------------------

METADRIVE_SRC = Path("/home/keith/Desktop/work/wingfin/metadrive/metadrive")


def _load_metadrive_schema() -> Any:
    """MetaDrive's `ScenarioDescription`, loaded from a checkout without installing it.

    A plain `import metadrive...` runs `metadrive/__init__.py`, which pulls in panda3d, the
    renderer. Checking a data structure needs none of that, and adding a 27-package
    dependency to pin one schema is a bad trade - the Stage 6 spec explicitly keeps
    MetaDrive out of this converter's dependencies.

    So the three package levels are registered as bare modules carrying only `__path__`,
    `metadrive.utils.math.norm` is supplied directly (the one function the schema imports
    from a package whose `__init__` needs panda3d), and the two real modules are loaded by
    file path. If MetaDrive reorganises these files this raises rather than silently
    passing, which is the point.
    """
    for name, path in (
        ("metadrive", METADRIVE_SRC),
        ("metadrive.scenario", METADRIVE_SRC / "scenario"),
        ("metadrive.utils", METADRIVE_SRC / "utils"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        sys.modules[name] = module
    stub = types.ModuleType("metadrive.utils.math")
    stub.norm = lambda x, y: math.sqrt(x * x + y * y)  # type: ignore[attr-defined]
    sys.modules["metadrive.utils.math"] = stub

    def load(name: str, path: Path) -> Any:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    load("metadrive.type", METADRIVE_SRC / "type.py")
    schema = load(
        "metadrive.scenario.scenario_description",
        METADRIVE_SRC / "scenario" / "scenario_description.py",
    )
    return schema.ScenarioDescription


@pytest.mark.skipif(not METADRIVE_SRC.is_dir(), reason="no MetaDrive checkout on this machine")
def test_the_scenario_passes_metadrives_own_sanity_check(tmp_path: Path) -> None:
    """The gate that stops this converter drifting from the format it targets.

    Everything else in this file checks what we meant to write. This checks that MetaDrive
    agrees, using MetaDrive's code rather than our reading of it.
    """
    schema = _load_metadrive_schema()
    workspace = _workspace(tmp_path, _model())
    scenario_path, _, _, _ = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    schema.sanity_check(pickle.loads(scenario_path.read_bytes()))


@pytest.mark.skipif(not METADRIVE_SRC.is_dir(), reason="no MetaDrive checkout on this machine")
def test_our_feature_types_are_the_ones_metadrive_defines() -> None:
    """Spelled from MetaDrive's constants, not from a reading of someone else's dataset."""
    _load_metadrive_schema()
    from metadrive.type import MetaDriveType

    scenario = _built(_model(lanes=[_lane("a", boundaries=[
        LaneBoundary(identifier="edge-1", side="left", points=_straight(0.0, 50.0))
    ])]))
    types_used = {feature["type"] for feature in scenario["map_features"].values()}
    assert types_used == {"LANE_SURFACE_STREET", "ROAD_EDGE_BOUNDARY"}
    assert MetaDriveType.is_lane("LANE_SURFACE_STREET")
    # `has_type` covers object types and never sees a map feature, so the boundary is
    # checked against the constant MetaDrive actually names it with.
    assert MetaDriveType.BOUNDARY_LINE == "ROAD_EDGE_BOUNDARY"


# --- reachability -------------------------------------------------------------------------


def test_reachability_measures_where_a_car_can_get_to() -> None:
    routing = _reachability(_lane_neighbours(_model()))
    assert routing["best_start_lane_id"] == "a"
    assert routing["best_start_reaches"] == 2
    assert routing["lanes_reaching_nothing"] == 1
    assert routing["reachable_lane_pairs"] == 3
    assert routing["possible_lane_pairs"] == 6


def test_one_way_lanes_are_not_counted_as_mutually_reachable() -> None:
    """The distinction Stage 5's `routing_components` cannot make.

    All three lanes are one weakly connected piece, and a reader of that number alone
    would conclude a car can drive between any two of them. It cannot: nothing returns.
    """
    routing = _reachability(_lane_neighbours(_model()))
    assert routing["components_respecting_direction"] == {"count": 3, "largest": 1}
