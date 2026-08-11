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
import pickletools
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from osm_scenario import signal_plan
from osm_scenario.apply_review import _sha256
from osm_scenario.config import ConverterConfig
from osm_scenario.conversion import (
    ConversionError,
    _lane_change_moves,
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
from osm_scenario.reachability_view import render_reachability_html
from osm_scenario.signal_plan import PhaseGroup, SignalPlan

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


def _side_by_side(**update: Any) -> PreliminaryLaneModel:
    """`_model()` with a second lane running alongside `a`, so lane changes have a subject.

    Kept separate from `_model()` rather than folded into it: a fourth lane would change
    the feature set several tests pin by name, and those assertions are about resolving
    references, which is a different subject from moving sideways.

    `a2` has no exits of its own. Without lane changes it reaches nothing at all; with them
    it reaches everything `a` reaches, which is the whole distinction in three lanes.
    """
    a = _lane(
        "a", exit_lanes=["c"], lane_index=0, lane_count=2, left_neighbor="a2"
    )
    a2 = _lane("a2", lane_index=1, lane_count=2, right_neighbor="a")
    base = _model()
    lanes = [a.model_dump(), a2.model_dump()] + [
        lane.model_dump() for lane in base.lanes if lane.identifier != "a"
    ]
    model = PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": lanes,
            "connectors": [connector.model_dump() for connector in base.connectors],
        }
    )
    return model.model_copy(update=update) if update else model


def _built(model: PreliminaryLaneModel, plan: SignalPlan | None = None) -> dict[str, Any]:
    scenario, _, _, _ = _scenario(
        model=model,
        workspace_name="test-workspace",
        manifest={"source": {"sha256": "src"}, "stage_5": {"status": "passed"}},
        model_sha256="model",
        plan=plan,
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
    scenario_paths, summary_path, mapping_path, report_path, _ = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    # Map-only: one scenario, because there are no routes to make more than one of.
    assert len(scenario_paths) == 1
    scenario = pickle.loads(scenario_paths[0].read_bytes())
    assert set(scenario["map_features"]) == {"a", "b", "d"}

    # Both index files key on the same computed filename, and it is the one MetaDrive will
    # accept - not a name we found readable.
    name = scenario_file_name(scenario["id"])
    assert scenario_paths[0].name == name
    assert pickle.loads(summary_path.read_bytes()) == {name: scenario["metadata"]}
    assert pickle.loads(mapping_path.read_bytes()) == {name: ""}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["map_features"] == 3
    assert report["scenario_files"] == [name]
    # Empty rather than absent: a map-only dataset is one MetaDrive can check and cannot
    # drive, and the report is where that difference is stated.
    assert report["routes"] == []
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


# --- traffic lights -------------------------------------------------------------------------

_PLAN = SignalPlan(
    cycle_seconds=60.0,
    groups=(
        PhaseGroup(
            name="phase-a",
            lanes=("a",),
            green_seconds=27.0,
            yellow_seconds=3.0,
            offset_seconds=0.0,
        ),
    ),
)


def test_without_a_plan_there_are_no_lights_and_nothing_claims_otherwise() -> None:
    """The default has to stay exactly what it was, because most conversions have no plan."""
    scenario = _built(_model())
    assert scenario["dynamic_map_states"] == {}
    assert "signals" not in scenario["metadata"]
    assert scenario["metadata"]["counts"]["signalled_lanes"] == 0


def test_a_light_is_keyed_on_the_lane_id_metadrive_will_look_up() -> None:
    """`ScenarioLightManager.after_reset` looks the key up in `road_network.graph`.

    MetaDrive's `skip_missing_light` defaults to True, so a key that is not a map feature is
    dropped with a log line and no light at all - a failure that looks exactly like a plan
    that was never applied.
    """
    scenario = _built(_model(), _PLAN)
    assert set(scenario["dynamic_map_states"]) == {"a"}
    assert set(scenario["dynamic_map_states"]) <= set(scenario["map_features"])


def test_the_stop_point_sits_outside_state_where_the_length_check_cannot_reach_it() -> None:
    """Everything inside `state` is asserted to be as long as the scenario.

    A three-element position there passes only on a three-step scenario, and
    `_get_episode_light_data` would read it as the old Waymo `[T, 2]` format besides.
    """
    light = _built(_model(), _PLAN)["dynamic_map_states"]["a"]
    assert "stop_point" not in light["state"]
    assert light["stop_point"].shape == (3,)
    assert light["stop_point"].dtype == np.float32
    assert set(light["state"]) == {"object_state"}


def test_the_stop_point_is_the_downstream_end_of_the_signalled_lane() -> None:
    """A light stops the traffic leaving a lane, so the wall goes where that lane ends."""
    light = _built(_model(), _PLAN)["dynamic_map_states"]["a"]
    assert light["stop_point"].tolist() == pytest.approx([50.0, 0.0, 0.0])


def test_every_state_array_is_exactly_as_long_as_the_scenario() -> None:
    scenario = _built(_model(), _PLAN)
    light = scenario["dynamic_map_states"]["a"]
    assert len(light["state"]["object_state"]) == scenario["length"]
    assert light["metadata"]["track_length"] == scenario["length"]


def test_the_plan_is_recorded_as_synthesised_rather_than_surveyed() -> None:
    """The whole reason signals were previously left out of the pickle.

    OSM records that a signal exists and no timing whatever, so a phase plan inside a
    dataset has to carry the fact that a person made it up.
    """
    metadata = _built(_model(), _PLAN)["metadata"]
    assert metadata["signals"]["source"] == "synthesised"
    assert metadata["signals"]["cycle_seconds"] == 60.0
    assert [group["name"] for group in metadata["signals"]["groups"]] == ["phase-a"]
    assert metadata["counts"]["signalled_lanes"] == 1
    assert metadata["counts"]["phase_groups"] == 1


def test_the_counts_keep_surveyed_signals_and_placed_lights_apart() -> None:
    """Two different numbers, and conflating them would hide that OSM supplied neither.

    `signals` is how many `highway=traffic_signals` nodes the survey has; `signalled_lanes`
    is how many lanes carry a light in this dataset. In `junction-1` the first is 1, at the
    edge of the extract, and the second is whatever was placed by hand.
    """
    counts = _built(_model(), _PLAN)["metadata"]["counts"]
    assert counts["signals"] == 0
    assert counts["signalled_lanes"] == 1


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
    scenario_paths, _, _, _, _ = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    schema.sanity_check(pickle.loads(scenario_paths[0].read_bytes()))


@pytest.mark.skipif(not METADRIVE_SRC.is_dir(), reason="no MetaDrive checkout on this machine")
def test_a_scenario_with_traffic_lights_passes_the_same_check(tmp_path: Path) -> None:
    """`sanity_check` runs `_check_object_state_dict` over `dynamic_map_states` too.

    That is where a `stop_point` in the wrong place fails: every array inside `state` is
    asserted to be exactly as long as the scenario, so a three-element position there passes
    only by accident on a three-step scenario.
    """
    schema = _load_metadrive_schema()
    workspace = _workspace(tmp_path, _model())
    plan_path = workspace / "signals.json"
    plan_path.write_text(
        json.dumps(
            {
                "signals_version": 1,
                "identity": {
                    "generation_fingerprint": "fingerprint",
                    "reviewed_lane_model_sha256": _sha256(
                        workspace / "lane-model" / "reviewed.json"
                    ),
                },
                "cycle_seconds": 60,
                "groups": [
                    {
                        "name": "phase-a",
                        "lanes": ["a"],
                        "green_seconds": 27,
                        "yellow_seconds": 3,
                        "offset_seconds": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    scenario_paths, _, _, _, _ = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1), signals=plan_path
    )
    scenario = pickle.loads(scenario_paths[0].read_bytes())
    assert set(scenario["dynamic_map_states"]) == {"a"}
    schema.sanity_check(scenario)


@pytest.mark.skipif(not METADRIVE_SRC.is_dir(), reason="no MetaDrive checkout on this machine")
def test_our_light_colours_are_the_ones_metadrive_defines() -> None:
    """Spelled from MetaDrive's constants, because a typo here is silent.

    `simplify_light_status` turns anything it does not recognise into `LIGHT_UNKNOWN`, which
    sets the wall's collision mask to `AllOff` - so a misspelt red is not an error, it is a
    light nothing stops for.
    """
    _load_metadrive_schema()
    from metadrive.type import MetaDriveType

    for ours, theirs in (
        (signal_plan.LIGHT_GREEN, MetaDriveType.LIGHT_GREEN),
        (signal_plan.LIGHT_YELLOW, MetaDriveType.LIGHT_YELLOW),
        (signal_plan.LIGHT_RED, MetaDriveType.LIGHT_RED),
    ):
        assert ours == theirs
        # `ScenarioTrafficLight.set_status` puts every value through this before switching
        # the model and the collision mask, so surviving it is what "MetaDrive understands
        # this colour" actually means.
        assert MetaDriveType.simplify_light_status(ours) == ours

    # The object type, not a status - it is what `_get_episode_light_data` asserts on.
    assert signal_plan._LIGHT_TYPE == MetaDriveType.TRAFFIC_LIGHT


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


# --- readable by the numpy the reader has, not the one we have ----------------------------
#
# These cannot be written as ordinary round-trip assertions, because this interpreter is
# exactly the one where the fault is invisible: numpy 2 reads its own pickles happily. What
# can be checked here is the property that makes the file portable - that the stream names
# no module the reader might not have - and that is what these look at.


def _pickled(tmp_path: Path) -> bytes:
    workspace = _workspace(tmp_path, _model())
    scenario_paths, _, _, _, _ = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    return scenario_paths[0].read_bytes()


def _modules_named_in(payload: bytes) -> set[str]:
    """Every module the unpickler will try to import to rebuild this object."""
    named = set()
    for opcode, argument, _ in pickletools.genops(payload):
        if opcode.name in {"GLOBAL", "STACK_GLOBAL", "INST"} and isinstance(argument, str):
            named.add(argument.split(" ")[0].split("\n")[0])
    return named


def test_the_pickle_names_no_module_an_older_numpy_would_not_have(tmp_path: Path) -> None:
    """The dataset must open in MetaDrive's interpreter, which is older than this one.

    numpy 2 pickles an array as a reference to `numpy._core`, a module numpy 1 does not
    have, so the dataset fails to *open* in the environment it is written for - with
    `ModuleNotFoundError`, which names nothing about the real problem. Both MetaDrive
    checkouts run Python 3.8, which cannot have numpy 2 at all, so this does not wait
    itself out.
    """
    named = _modules_named_in(_pickled(tmp_path))
    assert not {module for module in named if module.startswith("numpy._core")}, named
    # Whatever numpy is named, it is only ever the one public constructor that has kept its
    # name across both major versions.
    assert {module for module in named if module.startswith("numpy")} <= {"numpy"}


def test_geometry_survives_that_as_arrays_rather_than_lists(tmp_path: Path) -> None:
    """Portable is not enough on its own - it has to still be an array on arrival.

    MetaDrive indexes this geometry with tuples (`positions[:, :2]` in
    `parse_full_trajectory`, `polyline[neighbor_start]` in `ScenarioLane`). A shim that
    degraded arrays to lists would satisfy the test above and fail there instead, further
    from the cause.
    """
    scenario = pickle.loads(_pickled(tmp_path))
    polyline = scenario["map_features"]["a"]["polyline"]
    assert isinstance(polyline, np.ndarray)
    assert polyline.dtype == np.float64
    assert polyline.ndim == 2
    assert polyline[:, :2].shape == polyline.shape
    # `sanity_check` reads `.shape` on this one, so it is the array most likely to be
    # noticed if it degrades - which is the reason to pin the least likely one above too.
    assert scenario["metadata"]["ts"].shape == (1,)


# --- reachability -------------------------------------------------------------------------


def _routing(model: PreliminaryLaneModel) -> dict[str, Any]:
    return _reachability(_lane_neighbours(model), _lane_change_moves(model))


def test_reachability_measures_where_a_car_can_get_to() -> None:
    routing = _routing(_model())
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
    routing = _routing(_model())
    assert routing["components_respecting_direction"] == {"count": 3, "largest": 1}


# --- lane changes ---------------------------------------------------------------------------


def test_a_lane_change_is_a_way_to_get_somewhere() -> None:
    """`a2` has no exits. Only moving across into `a` gets it anywhere at all."""
    routing = _routing(_side_by_side())
    assert routing["lane_change_edges"] == 2
    assert routing["without_lane_changes"]["lanes_reaching_nothing"] == 2
    assert routing["lanes_reaching_nothing"] == 1
    # a2 -> a -> b -> d, and a -> a2, so both front lanes now reach three of the four.
    assert routing["best_start_reaches"] == 3
    assert routing["without_lane_changes"]["best_start_reaches"] == 2


def test_the_junction_only_figures_are_kept_beside_the_headline_ones() -> None:
    """Reporting either alone misleads, which is the lesson Stage 5's piece count taught."""
    model = _side_by_side()
    routing = _routing(model)
    assert routing["lane_changes_allowed"] is True
    assert "without_lane_changes" not in routing["without_lane_changes"]

    # Run it again with nothing to move sideways into. The headline figures then have to
    # equal the block the real run files under `without_lane_changes` - that is what makes
    # the block a measurement rather than a label.
    wrapper = ("lane_changes_allowed", "lane_change_edges", "without_lane_changes")
    nowhere_to_move = _reachability(_lane_neighbours(model), {})
    assert nowhere_to_move["lane_change_edges"] == 0
    assert routing["without_lane_changes"] == {
        key: value for key, value in nowhere_to_move.items() if key not in wrapper
    }


def test_a_neighbour_that_is_not_a_lane_is_refused() -> None:
    model = _side_by_side()
    model.lanes[0].left_neighbor = "nowhere"
    with pytest.raises(ConversionError, match="lane a names nowhere as its left neighbour"):
        _lane_change_moves(model)


def test_a_neighbour_facing_the_other_way_is_refused() -> None:
    """It would be a drivable edge straight into oncoming traffic."""
    model = _side_by_side()
    model.lanes[1].direction = "backward"
    with pytest.raises(ConversionError, match="not the same stretch of road"):
        _lane_change_moves(model)


def test_a_neighbour_on_another_stretch_of_road_is_refused() -> None:
    """`left_neighbor` means alongside. Anything else would teleport a car down the street."""
    model = _side_by_side()
    model.lanes[1].source_edge = ["9", "10", "0"]
    with pytest.raises(ConversionError, match="not the same stretch of road"):
        _lane_change_moves(model)


def test_a_lane_change_never_becomes_an_exit_in_the_map_features() -> None:
    """`exit_lanes` means where the lane leads. Moving sideways is not that.

    The dataset MetaDrive loads must be unchanged by any of this - only the reachability
    figures move.
    """
    features = _built(_side_by_side())["map_features"]
    assert features["a"]["exit_lanes"] == ["b"]
    assert features["a2"]["exit_lanes"] == []
    assert features["a"]["left_neighbor"] == ["a2"]
    assert features["a2"]["right_neighbor"] == ["a"]


# --- the Stage 6 reachability page ---------------------------------------------------------


def _payload(html: str) -> dict[str, Any]:
    """The `DATA` object the page's search runs over, read back out of the rendered page.

    Parsed rather than taken from the renderer's inputs, because what matters is what
    reaches the browser - a payload that failed to serialise is the failure mode this
    guards against.
    """
    start = html.index("const DATA = ") + len("const DATA = ")
    end = html.index(";\n", start)
    return json.loads(html[start:end].replace("<\\/", "</"))


def _reached_in_the_browsers_search(
    payload: dict[str, Any], start: str, *, allow_change: bool
) -> set[str]:
    """The page's breadth-first search, rewritten line for line in Python.

    There is no JavaScript test runner here, so the algorithm is pinned by keeping a twin
    of it beside the real thing and holding both to the numbers the scenario reports -
    with lane changes and without, since the page offers both.
    """
    graph = {
        lane["id"]: lane["exits"] + (lane["sideways"] if allow_change else [])
        for lane in payload["lanes"]
    }
    seen = {start}
    frontier = [start]
    while frontier:
        following = []
        for lane_id in frontier:
            for target in graph[lane_id]:
                if target not in seen:
                    seen.add(target)
                    following.append(target)
        frontier = following
    return seen - {start}


def _rendered(model: PreliminaryLaneModel) -> tuple[dict[str, Any], dict[str, Any]]:
    neighbours = _lane_neighbours(model)
    moves = _lane_change_moves(model)
    routing = _reachability(neighbours, moves)
    html = render_reachability_html(
        model=model, neighbours=neighbours, moves=moves, routing=routing
    )
    return _payload(html), routing


def test_the_page_carries_the_same_graph_the_scenario_does() -> None:
    """The page must not be able to draw a network the dataset does not contain.

    `convert_scenario` resolves the references once and hands the one result to both, so
    this is really a check that nothing re-derives them along the way.
    """
    model = _side_by_side()
    payload, _ = _rendered(model)
    assert {lane["id"]: lane["exits"] for lane in payload["lanes"]} == {
        lane_id: exits for lane_id, (_, exits) in _lane_neighbours(model).items()
    }
    assert {lane["id"]: lane["sideways"] for lane in payload["lanes"]} == _lane_change_moves(model)


def test_the_pages_search_finds_what_the_scenarios_routing_metadata_claims() -> None:
    payload, routing = _rendered(_side_by_side())
    assert payload["default_lane"] == routing["best_start_lane_id"]
    start = routing["best_start_lane_id"]
    assert len(_reached_in_the_browsers_search(payload, start, allow_change=True)) == (
        routing["best_start_reaches"]
    )


def test_the_page_can_reproduce_the_junction_only_view_exactly() -> None:
    """The checkbox is how a reader checks the lane-change claim instead of taking it.

    So the page with lane changes off must land on the number the metadata records for
    that case, not merely on a smaller one.
    """
    payload, routing = _rendered(_side_by_side())
    strict = routing["without_lane_changes"]
    reached = _reached_in_the_browsers_search(
        payload, strict["best_start_lane_id"], allow_change=False
    )
    assert len(reached) == strict["best_start_reaches"]


def test_all_three_pages_are_written_and_recorded_beside_the_dataset(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _model())
    *_, report_path, (html_path, builder_path, signal_path) = convert_scenario(
        workspace=workspace, config=ConverterConfig(config_version=1)
    )
    assert html_path == workspace / "inspection" / "stage-6-reachability.html"
    # Written even for a map-only dataset, because it is how a map-only dataset stops being
    # map-only: there is nowhere else to pick the routes that make it drivable.
    assert builder_path == workspace / "inspection" / "stage-6-route-builder.html"
    # Same argument for the lights: a dataset with none is how every dataset starts, and the
    # page is the only place a plan can be made.
    assert signal_path == workspace / "inspection" / "stage-6-signal-builder.html"
    # In `inspection/`, not in `scenarionet/`: MetaDrive reads that directory and it must
    # hold the dataset and nothing else.
    for path in (html_path, builder_path, signal_path):
        assert not (workspace / "scenarionet" / path.name).exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["artifacts"]["reachability_html"]["path"] == (
        "inspection/stage-6-reachability.html"
    )
    assert report["artifacts"]["route_builder_html"]["path"] == (
        "inspection/stage-6-route-builder.html"
    )
    assert report["artifacts"]["signal_builder_html"]["path"] == (
        "inspection/stage-6-signal-builder.html"
    )
    manifest = json.loads((workspace / "source" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage_6"]["artifacts"]["reachability_html"] == (
        report["artifacts"]["reachability_html"]
    )


def test_every_lane_is_drawn_with_a_line_and_a_way_to_name_it() -> None:
    """A lane the page cannot draw is a lane a reader cannot click, and so cannot start on."""
    model = _model()
    payload, _ = _rendered(model)
    assert len(payload["lanes"]) == len(model.lanes)
    for lane in payload["lanes"]:
        assert len(lane["line"]) >= 2
        assert lane["ways"] and lane["label"] and lane["short"]
    assert sum(way["lanes"] for way in payload["ways"]) == len(model.lanes)
