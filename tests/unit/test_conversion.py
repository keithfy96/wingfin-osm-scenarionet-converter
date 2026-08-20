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
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from osm_scenario import conversion, signal_plan
from osm_scenario.apply_review import _sha256
from osm_scenario.config import ConverterConfig
from osm_scenario.conversion import (
    _BOUNDARY_TYPE,
    _COVERED_PAINT_TOLERANCE_M,
    _DIVIDER_TYPE,
    _KERB_GAP_CLOSE_M,
    _LANE_TYPE,
    _MAX_KERB_TURN_DEG,
    _MIN_PAINT_M,
    ConversionError,
    _closed,
    _kerb_rings,
    _lane_change_moves,
    _lane_neighbours,
    _map_features,
    _reachability,
    _road_union,
    _scenario,
    _step_seconds,
    convert_scenario,
    scenario_file_name,
)
from osm_scenario.generation import MIN_TRIMMED_LANE_M
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


def _line(y: float, x0: float = 0.0, x1: float = 50.0) -> list[Point2D]:
    return [Point2D(x=x0, y=y), Point2D(x=x1, y=y)]


def _sharing_a_divider(shared_y: float = 2.0) -> PreliminaryLaneModel:
    """`_side_by_side()` with boundaries, so the line between the two lanes has a subject.

    Each lane carries its own copy of that line, which is what the real generator produces.
    `shared_y` moves `a2`'s copy off `a`'s: at the default they are one line drawn twice, and
    further apart they are two lines that both have to survive.
    """
    base = _model()
    a = _lane(
        "a",
        exit_lanes=["c"],
        lane_index=0,
        lane_count=2,
        left_neighbor="a2",
        boundaries=[
            LaneBoundary(identifier="a-left", side="left", points=_line(2.0)),
            LaneBoundary(identifier="a-right", side="right", points=_line(-2.0)),
        ],
    )
    a2 = _lane(
        "a2",
        lane_index=1,
        lane_count=2,
        right_neighbor="a",
        boundaries=[
            LaneBoundary(identifier="a2-right", side="right", points=_line(shared_y)),
            LaneBoundary(identifier="a2-left", side="left", points=_line(6.0)),
        ],
    )
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [a.model_dump(), a2.model_dump()]
            + [lane.model_dump() for lane in base.lanes if lane.identifier != "a"],
            "connectors": [connector.model_dump() for connector in base.connectors],
        }
    )


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
    # `c` is the junction turn. It is a feature in its own right because MetaDrive builds its
    # road network out of lane features and would otherwise have no surface across the junction.
    features = scenario["map_features"]
    named = {"a", "b", "c", "d"}
    assert named <= set(features)
    # Everything else is a kerb line round that turn. They belong to no lane, so they are keyed
    # on where they are rather than on a model id, and the count is the only stable handle.
    kerbs = set(features) - named
    assert len(kerbs) == scenario["metadata"]["lane_markings"]["junction_kerbs"]
    assert all(features[identifier]["type"] == "ROAD_EDGE_BOUNDARY" for identifier in kerbs)

    # Both index files key on the same computed filename, and it is the one MetaDrive will
    # accept - not a name we found readable.
    name = scenario_file_name(scenario["id"])
    assert scenario_paths[0].name == name
    assert pickle.loads(summary_path.read_bytes()) == {name: scenario["metadata"]}
    assert pickle.loads(mapping_path.read_bytes()) == {name: ""}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Three lanes, the junction turn between two of them, and the kerb round that turn.
    assert report["map_features"] == 4 + len(kerbs)
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
    # The lanes survive; only the movement the review forbade is gone. The kerb lines round them
    # are keyed on their own geometry and belong to no lane, so they are counted rather than named.
    features = _built(model)["map_features"]
    _, kerbs = _kerbs(model)
    assert set(features) - kerbs == {"a", "b", "d"}


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
    # `a` now names the turn rather than the lane beyond it, and the turn names both sides, so
    # every one of the four appears. That chain is the point: it is what gives MetaDrive a
    # continuous surface from the approach, across the junction, onto the exit.
    assert referenced == {"a", "b", "c", "d"}


def test_boundaries_become_their_own_features() -> None:
    boundary = LaneBoundary(identifier="edge-1", side="left", points=_straight(0.0, 50.0))
    model = _model(lanes=[_lane("a", boundaries=[boundary])])
    features = _built(model)["map_features"]
    assert features["edge-1"]["type"] == "ROAD_EDGE_BOUNDARY"
    assert features["edge-1"]["lane_id"] == "a"
    assert features["edge-1"]["polyline"].shape == (2, 2)


def test_a_boundary_a_lane_change_crosses_is_drawn_broken() -> None:
    """The line style is `_lane_change_moves` drawn, not a second opinion about it.

    MetaDrive names the line's ghost body after this type, so a solid line here would tell
    the simulator that every move in `metadata.routing.lane_change_edges` is a violation.
    """
    features = _built(_sharing_a_divider())["map_features"]
    assert features["a-left"]["type"] == "ROAD_LINE_BROKEN_SINGLE_WHITE"
    # Nothing lies beyond these two, so nothing may cross them. A kerb and a centreline
    # cannot come out dashed however the rest of the rule behaves.
    assert features["a-right"]["type"] == "ROAD_EDGE_BOUNDARY"
    assert features["a2-left"]["type"] == "ROAD_EDGE_BOUNDARY"


def test_the_second_copy_of_a_shared_divider_is_not_written() -> None:
    """Both lanes carry the same line, and two copies of a broken line render as a solid one.

    Each is resampled from its own first point, so the dashes land out of phase and fill each
    other's gaps - the failure looks exactly like the broken type not having been applied.
    """
    features = _built(_sharing_a_divider())["map_features"]
    assert "a-left" in features
    assert "a2-right" not in features


def test_two_copies_that_are_not_the_same_line_are_both_kept() -> None:
    """0.4 m apart is two lines, not one drawn twice, and dropping one would move the paint."""
    features = _built(_sharing_a_divider(shared_y=2.4))["map_features"]
    assert features["a-left"]["type"] == "ROAD_LINE_BROKEN_SINGLE_WHITE"
    assert features["a2-right"]["type"] == "ROAD_LINE_BROKEN_SINGLE_WHITE"


def test_lane_markings_say_the_style_was_derived_rather_than_surveyed() -> None:
    """OSM carries no marking survey, so the dataset has to admit where the style came from."""
    markings = _built(_sharing_a_divider())["metadata"]["lane_markings"]
    assert markings["source"] == "derived-from-lane-change-permissions"
    assert markings["dividers"] == 1
    assert markings["edges"] == 2
    assert markings["merged"] == 1


def test_a_lane_clamped_to_the_trim_minimum_is_paved_but_not_painted() -> None:
    """The stub is real road inside a junction, so it keeps its surface and loses its lines.

    Two junctions closer together than their setbacks leave a lane of exactly
    `MIN_TRIMMED_LANE_M`, which by construction reaches further into the junction than any
    other lane. Its markings land in the open middle - at `junction-1`'s node 1927184814,
    eighteen of them pointing four ways across a box a car turns through.
    """
    stub = _lane(
        "a",
        x0=0.0,
        x1=MIN_TRIMMED_LANE_M,
        boundaries=[
            LaneBoundary(
                identifier="edge-1", side="left", points=_straight(0.0, MIN_TRIMMED_LANE_M)
            )
        ],
    )
    features = _built(_model(lanes=[stub, *_model().lanes[1:]]))["map_features"]
    assert "edge-1" not in features
    # The lane itself is untouched: deleting it would cut the network, and MetaDrive builds
    # its road surface from the lane features alone.
    assert features["a"]["type"] == "LANE_SURFACE_STREET"


def test_a_lane_that_kept_its_setbacks_is_still_painted() -> None:
    """Just above the clamp is a short way, not a stub, and it ends outside both junctions.

    The nearest real lanes above the clamp measure 2.07 m and 2.37 m, so the criterion has to
    separate them from 2.00 m rather than round them together.
    """
    short = _lane(
        "a",
        x0=0.0,
        x1=MIN_TRIMMED_LANE_M + 0.07,
        boundaries=[
            LaneBoundary(
                identifier="edge-1",
                side="left",
                points=_straight(0.0, MIN_TRIMMED_LANE_M + 0.07),
            )
        ],
    )
    features = _built(_model(lanes=[short, *_model().lanes[1:]]))["map_features"]
    assert features["edge-1"]["type"] == "ROAD_EDGE_BOUNDARY"


def test_suppressing_a_stub_does_not_restyle_its_neighbour() -> None:
    """`_divider_boundaries` has to see the stub even though the stub will not be written.

    It reads a lane's neighbours to decide which lines are broken. Hide the stub from it and a
    *surviving* neighbour's dashes change, which is a marking moved on a road nobody touched.
    """
    model = _sharing_a_divider()
    lanes = list(model.lanes)
    lanes[0] = lanes[0].model_copy(
        update={
            "centerline": _straight(0.0, MIN_TRIMMED_LANE_M),
            "polygon": _surface(0.0, MIN_TRIMMED_LANE_M),
        }
    )
    shortened = model.model_copy(update={"lanes": lanes})
    features = _built(shortened)["map_features"]
    assert "a-left" not in features and "a-right" not in features
    # `a2-right` was the copy dropped as a duplicate of `a-left`, and stays dropped: which of
    # the two survives is decided before this, so suppression cannot resurrect it.
    assert "a2-right" not in features
    assert features["a2-left"]["type"] == "ROAD_EDGE_BOUNDARY"


def test_a_suppressed_marking_is_reported_rather_than_counted_as_a_duplicate() -> None:
    """`merged` means "the second copy of a shared line". A blank junction is a different fact."""
    model = _sharing_a_divider()
    lanes = list(model.lanes)
    lanes[0] = lanes[0].model_copy(
        update={
            "centerline": _straight(0.0, MIN_TRIMMED_LANE_M),
            "polygon": _surface(0.0, MIN_TRIMMED_LANE_M),
        }
    )
    markings = _built(model.model_copy(update={"lanes": lanes}))["metadata"]["lane_markings"]
    assert markings["junction_stubs"] == 2
    assert markings["merged"] == 1


# --- the kerb round a junction -----------------------------------------------------------


def _real_models() -> list[tuple[str, PreliminaryLaneModel]]:
    """Every reviewed model on this machine, for the checks a hand-written map cannot make.

    `workspaces/` is gitignored, so on a clean checkout this is empty and the sweeps that use
    it skip - the same arrangement `test_ego_route._real_model` makes, and for the same reason:
    what is being checked is where turns overlap each other on a real map.
    """
    root = Path(__file__).resolve().parents[2] / "workspaces"
    found = []
    for path in sorted(root.glob("*/lane-model/reviewed.json")) if root.exists() else []:
        model = PreliminaryLaneModel.model_validate(json.loads(path.read_text()))
        found.append((path.parents[1].name, model))
    return found


def _kerbs(model: PreliminaryLaneModel) -> tuple[dict[str, Any], set[str]]:
    built = _map_features(model, _lane_neighbours(model), _lane_change_moves(model))
    return built.features, built.kerbs


def _road_edge(features: dict[str, Any]) -> tuple[Any, list[LineString]]:
    """The rings a kerb could run along, and every line already drawn on the map.

    The same two things `_junction_kerb_boundaries` works from, rebuilt here from what was
    written rather than shared with it, so a test cannot pass by agreeing with the bug.
    """
    surfaces = [
        Polygon(item["polygon"])
        for item in features.values()
        if item["type"] == _LANE_TYPE and len(item["polygon"]) >= 4
    ]
    lines = [
        LineString(item["polyline"])
        for item in features.values()
        if item["type"] != _LANE_TYPE and len(item.get("polyline", ())) >= 2
    ]
    road = unary_union([shape.buffer(0) for shape in surfaces])
    return road, lines


# What `_end_alignment` returns when an end has no paint near enough to compare against. It has
# to read as *neither* a break nor a road end: a break in one continuous kerb has paint at both
# of its ends by definition, and a road end is square to paint at both. Returning 1.0 for both -
# which an earlier version did - calls every unknown a break, and a road end that the surface
# sealing moved 0.35 m clear of the line it used to touch is exactly that case.
_UNKNOWN_END = (0.0, 1.0)


def _end_alignment(arc: LineString, lines: list[LineString]) -> tuple[float, float]:
    """|cos| between `arc` and the nearest drawn line, at each of its two ends.

    A break in one continuous kerb runs *along* the paint at both ends; a bar across the end of
    a road is square to it at both. One number cannot say both, so both are returned.
    """
    points = np.asarray(arc.coords, dtype=np.float64)
    if len(points) < 2:
        return _UNKNOWN_END
    seen = []
    for tip, step in (
        (points[0], points[0] - points[min(3, len(points) - 1)]),
        (points[-1], points[-1] - points[max(-4, -len(points))]),
    ):
        span = float(np.hypot(*step))
        node = Point(tip)
        near = [line for line in lines if line.distance(node) <= 0.25]
        if span <= 1e-3 or not near:
            return _UNKNOWN_END
        line = min(near, key=lambda item: item.distance(node))
        along = line.project(node)
        back = np.asarray(line.interpolate(max(0.0, along - 1.0)).coords[0])
        ahead = np.asarray(line.interpolate(min(line.length, along + 1.0)).coords[0])
        reach = ahead - back
        length = float(np.hypot(*reach))
        if length <= 1e-3:
            return _UNKNOWN_END
        seen.append(abs(float(np.dot(step / span, reach / length))))
    return (min(seen), max(seen))


def _touches_a_slot(piece: LineString, road: Any) -> bool:
    """Whether `piece` runs along - or up to - a slot between two surfaces that fail to meet.

    Two surfaces that do not quite meet leave a slot, and a slot has road on both sides of it.
    Where one is too wide for `_KERB_GAP_CLOSE_M`, its walls are left bare on purpose, and so are
    the few centimetres at each end where the slot opens on to the real road edge - a line across
    the mouth of a hole would be a line drawn over nothing. Neither is a break in a kerb.

    So the reach goes `_KERB_SIDE_PROBE_M` past both ends, and one sample is enough. Written out
    here rather than borrowed from `conversion`, like `_end_alignment` beside it: a test that
    filtered its own input with the code under test would agree with a bug for free.
    """
    if len(piece.coords) < 2 or piece.length <= 1e-3:
        return False
    reach = 0.8
    for at in np.linspace(-reach, piece.length + reach, max(5, int(piece.length / 0.25) + 5)):
        clamped = min(max(at, 0.0), piece.length)
        here = np.asarray(piece.interpolate(clamped).coords[0])
        ahead = np.asarray(piece.interpolate(min(piece.length, clamped + 0.01)).coords[0])
        step = ahead - here
        span = float(np.hypot(*step))
        if span <= 1e-9:
            continue
        along = step / span
        here = here + along * (at - clamped)
        normal = np.array([-along[1], along[0]])
        if all(road.contains(Point(here + normal * side * reach)) for side in (1.0, -1.0)):
            return True
    return False


def _unpainted_edge(features: dict[str, Any]) -> list[tuple[LineString, tuple[float, float]]]:
    """Every stretch of road edge with no paint on it, scored at both ends.

    The tolerance is one texel - 1/16 m, `mosque`'s 2048 m terrain square against this machine's
    32768 px ceiling - because a drawn line is two of them. Measuring this at 0.20 m is what let
    the first version of the kerb ship with 154 breaks in it: at three times the width of the
    paint, an edge that renders bare passes as painted.
    """
    road, lines = _road_edge(features)
    # Sealed, like the generator: the wall of a 0.2 m notch between two surfaces is not an edge
    # anything should paint, so leaving it bare is not a break in a kerb.
    sealed = road.buffer(
        _KERB_GAP_CLOSE_M, join_style="mitre", mitre_limit=5.0
    ).buffer(-_KERB_GAP_CLOSE_M, join_style="mitre", mitre_limit=5.0)
    rings = unary_union(_kerb_rings(sealed))
    paint = unary_union(lines)
    bare = rings.difference(paint.buffer(1 / 16))
    pieces = bare.geoms if hasattr(bare, "geoms") else [bare]
    # A drawn line is 2 texels wide and centred on its polyline, so it covers a texel either
    # side. A piece that never leaves that band is not a gap in the paint - it is road edge
    # running a few centimetres beside its own line, which is what sealing a wedge at the
    # outside of a bend leaves behind. A real break has paint at its two ends and none between.
    covered = paint.buffer(2 / 16)
    return [
        (piece, _end_alignment(piece, lines))
        for piece in pieces
        if not piece.is_empty
        and piece.length > 0.02
        and not piece.difference(covered).is_empty
        # A slot too wide for the closing above still has two walls, and neither is a road edge.
        # Left bare on purpose, so counting one as a break would ask for the very marks on open
        # tarmac Keith reported.
        and not _touches_a_slot(piece, road)
    ]


def _widest(shape: Any) -> float:
    """The widest circle that fits inside `shape`, by bisection on a negative buffer.

    A hole between two surfaces is a snake rather than a blob, so its area says nothing about
    whether it can be seen. What can be seen is how far across it is at its widest.
    """
    low, high = 0.0, 3.0
    for _ in range(18):
        middle = (low + high) / 2
        if shape.buffer(-middle / 2).is_empty:
            high = middle
        else:
            low = middle
    return low


def _surfaces(features: dict[str, Any]) -> dict[str, Polygon]:
    return {
        identifier: Polygon(item["polygon"]).buffer(0)
        for identifier, item in features.items()
        if item["type"] == _LANE_TYPE and len(item["polygon"]) >= 4
    }


def test_no_hole_is_left_in_the_tarmac_wider_than_the_line_that_would_draw_on_it() -> None:
    """The road has to be whole, because a hole in it paints itself.

    Lane surfaces are each built from their own centreline, so where one edge of a road hands
    over to the next their square end caps leave a wedge of nothing between them - up to 0.687 m
    across. `terrain.frag.glsl:115` whitens everything in `5 < value < 16` and the semantic
    texture is filtered, so the blend from road (20) across the gap to ground (0) draws a line
    nobody put there. Keith saw them running into the lane he was driving in: 80 wider than a
    texel on `mosque` and 87 on `junction-1` before `_sealed_surfaces`.

    The threshold is the width of a drawn line, 1/8 m: two texels of `mosque`'s 2048 m terrain
    square against this machine's 32768 px ceiling, and about what a real road marking measures.
    A hole narrower than the paint beside it cannot read as a mark. What is left after sealing is
    0.011 m at its widest on `mosque` and 0.061 m on `junction-1`, against 0.687 m and 0.609 m
    before, and 0.008 m² and 0.060 m² of hole in total against 168.7 m² and 43.8 m².
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, _ = _kerbs(model)
        road = _road_union(list(_surfaces(features).values()))
        holes = _closed(road).difference(road)
        pieces = [
            piece
            for piece in (holes.geoms if hasattr(holes, "geoms") else [holes])
            if piece.area > 1e-9 and _widest(piece) > 1 / 8
        ]
        assert not pieces, (
            f"{name}: {len(pieces)} hole(s) in the tarmac wider than a texel, "
            f"widest {max(_widest(piece) for piece in pieces):.3f} m"
        )


def test_sealing_a_surface_only_ever_adds_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_sealed_surfaces` is the one step here that changes a feature, so it must only grow one.

    Every polygon it writes contains the polygon it was given, no feature appears or disappears,
    and the road gains exactly the area of the holes that were closed - which is what says the
    wedges went where they were meant to and nothing came along with them.

    The unsealed side is taken by turning the step off rather than by rebuilding the polygons
    here, so what is compared is this module's own output either way.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        with monkeypatch.context() as patched:
            patched.setattr(conversion, "_sealed_surfaces", lambda features: 0)
            plain = _map_features(
                model, _lane_neighbours(model), _lane_change_moves(model)
            ).features
        built = _map_features(model, _lane_neighbours(model), _lane_change_moves(model))
        sealed, grown = built.features, built.sealed
        before, after = _surfaces(plain), _surfaces(sealed)
        assert set(before) == set(after), f"{name}: sealing added or removed a surface"
        assert grown, f"{name}: nothing was sealed, so the check proved nothing"
        for identifier, shape in before.items():
            assert after[identifier].buffer(1e-9).contains(shape), (
                f"{name}: {identifier} lost area to the sealing"
            )
        road = _road_union(list(before.values()))
        holes = _closed(road).difference(road)
        gained = _road_union(list(after.values())).area - road.area
        # Only hole, and very nearly all of it. The shortfall is the few wedges whose parts do
        # not all reach a surface within `_KERB_GAP_CLOSE_M` - 0.008 m2 on `mosque` and 0.054 m2
        # on `junction-1`, none of it wider than a road marking, which the test above pins. The
        # 0.01 m2 the other way is `_SEAM_CONTACT_M`, the micrometre a part is grown by where it
        # meets its surface across a numerical sliver.
        assert gained <= holes.area + 0.01, (
            f"{name}: road gained {gained:.2f} m2 against {holes.area:.2f} m2 of hole - "
            "sealing invented tarmac"
        )
        assert holes.area - gained < 0.1, (
            f"{name}: {holes.area - gained:.3f} m2 of hole left unsealed"
        )


def test_the_road_union_does_not_quietly_lose_a_surface() -> None:
    """Plain `unary_union` drops a whole lane on both extracts. `_road_union` is why it does not.

    141.17 m² of `mosque` and 296 m² of `junction-1`, valid input, valid output, simply not
    covered - union the same shapes a second time and they appear. It matters beyond tidiness:
    a surface missing from the union is a hole in the road, and a hole in the road is where the
    kerb is drawn.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, _ = _kerbs(model)
        shapes = _surfaces(features)
        road = _road_union(list(shapes.values()))
        lost = [
            identifier
            for identifier, shape in shapes.items()
            if road.intersection(shape).area < shape.area * 0.999
        ]
        assert not lost, f"{name}: {len(lost)} surface(s) missing from the road: {lost[:3]}"


def test_a_road_whose_edges_are_already_painted_is_given_no_kerb_at_all() -> None:
    """The kerb fills gaps; where a lane's own edge lines already cover the road, there are none.

    Both ends of the pair are road ends - the road simply stops - and a bar of paint across a
    carriageway draws as a stop line, so those are left bare rather than filled.
    """
    def edges(name: str, x0: float, x1: float) -> list[LaneBoundary]:
        half = WIDTH / 2
        return [
            LaneBoundary(
                identifier=f"{name}-{side}",
                side=side,
                points=[Point2D(x=x0, y=y), Point2D(x=x1, y=y)],
            )
            for side, y in (("left", half), ("right", -half))
        ]

    lanes = [
        _lane("a", x0=0.0, x1=50.0, exit_lanes=["b"], boundaries=edges("a", 0.0, 50.0)),
        _lane(
            "b",
            x0=50.0,
            x1=100.0,
            entry_lanes=["a"],
            source_edge=["2", "3", "0"],
            boundaries=edges("b", 50.0, 100.0),
        ),
    ]
    model = PreliminaryLaneModel.model_validate(
        {"metadata": _METADATA, "lanes": [lane.model_dump() for lane in lanes], "connectors": []}
    )
    features, kerbs = _kerbs(model)
    assert kerbs == set()
    assert set(features) == {"a", "b", "a-left", "a-right", "b-left", "b-right"}


def test_a_junction_kerb_never_lands_on_road_a_car_drives_on() -> None:
    """The one thing that must not happen, checked where it would: on the real maps.

    A kerb line is the outline of a junction *facing open ground*. Anything of it that comes
    out over the drivable surface is a seam between two overlapping turns rather than a kerb -
    and it would not only look wrong: `ScenarioBlock` gives every line a ghost body and a solid
    one sets `on_white_continuous_line`, so a line here reads to a policy as a violation the
    car cannot avoid.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, kerbs = _kerbs(model)
        assert kerbs, f"{name} produced no kerb lines at all"
        road = unary_union(
            [
                Polygon(item["polygon"])
                for item in features.values()
                if item["type"] == _LANE_TYPE and len(item["polygon"]) >= 4
            ]
        ).buffer(-0.25)
        stray = [
            identifier
            for identifier in kerbs
            if road.intersects(
                LineString(features[identifier]["polyline"]).interpolate(0.5, normalized=True)
            )
        ]
        assert not stray, f"{name}: {len(stray)} kerb line(s) lie on drivable road: {stray[:3]}"


def test_no_kerb_line_has_tarmac_on_both_sides_of_it() -> None:
    """A kerb separates road from not-road. With road either side it is not a kerb.

    This is the one the test above cannot make. Lane and connector surfaces are each buffered from
    their own centreline, so a junction mouth is left with a notch 0.10-0.30 m wide; traced
    literally, the road's ring dives in along one wall and back out along the other and paints
    both. Those marks lie exactly *on* the boundary rather than inside it, so
    `test_a_junction_kerb_never_lands_on_road_a_car_drives_on` passes them - which is how 238 of
    `mosque`'s 408 kerb lines shipped as paint on open tarmac.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, kerbs = _kerbs(model)
        road, _ = _road_edge(features)
        marks = []
        for identifier in kerbs:
            around = LineString(features[identifier]["polyline"]).buffer(0.15)
            if around.difference(road).area / max(around.area, 1e-12) < 0.15:
                marks.append(identifier)
        assert not marks, (
            f"{name}: {len(marks)} kerb line(s) have road on both sides: {marks[:3]}"
        )


def test_no_kerb_line_doubles_back_on_itself() -> None:
    """`_uncreased` is what keeps a seam from being drawn as a mark across the junction."""
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, kerbs = _kerbs(model)
        for identifier in kerbs:
            points = np.asarray(features[identifier]["polyline"], dtype=np.float64)
            steps = np.diff(points, axis=0)
            spans = np.hypot(steps[:, 0], steps[:, 1])
            unit = steps[spans > 0] / spans[spans > 0][:, None]
            if len(unit) < 2:
                continue
            cross = unit[:-1, 0] * unit[1:, 1] - unit[:-1, 1] * unit[1:, 0]
            dot = unit[:-1, 0] * unit[1:, 0] + unit[:-1, 1] * unit[1:, 1]
            worst = float(np.abs(np.degrees(np.arctan2(cross, dot))).max())
            assert worst <= _MAX_KERB_TURN_DEG, f"{name}: {identifier} turns {worst:.1f} degrees"


def test_no_kerb_on_the_real_maps_is_broken_where_it_should_run_on() -> None:
    """The defect this whole pass exists to remove, asserted at zero.

    One physical kerb has to draw as one line. The version that shipped stood every arc off
    0.15 m from the line it met and threw away anything under 2 m, which left `mosque` with 154
    breaks over 276 m and `junction-1` with 186 over 292 m - a kerb chopped into larger and
    smaller pieces with holes between them, which reads as a marking that means something.

    A break is a stretch of road edge with no paint that runs *along* the paint at both of its
    ends. A bar across the end of a road does not qualify and is the test below.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, kerbs = _kerbs(model)
        assert kerbs, f"{name} produced no kerb lines at all"
        broken = [
            piece for piece, (low, _) in _unpainted_edge(features) if low > 0.6
        ]
        assert not broken, (
            f"{name}: {len(broken)} break(s) in a continuous kerb, "
            f"{sum(piece.length for piece in broken):.0f} m of gap"
        )


def test_the_end_of_a_road_is_never_painted_across() -> None:
    """A bar of paint across a carriageway is a stop line, and there is no stop line there.

    `_node_setbacks` leaves the end of every road square, so the outline of the network runs
    straight across it. Filling that gap is what "make the kerb continuous" would do if the rule
    were applied without looking, and it would put a solid line - with its ghost body - across
    road a car drives along.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        features, kerbs = _kerbs(model)
        road, _ = _road_edge(features)
        inside = road.buffer(-0.25)
        crossing = [
            identifier
            for identifier in kerbs
            if LineString(features[identifier]["polyline"])
            .interpolate(0.5, normalized=True)
            .within(inside)
        ]
        assert not crossing, f"{name}: kerb(s) across the carriageway: {crossing[:3]}"
        # And the ends really are still there to have been left alone - a run that painted over
        # all of them would pass the check above while doing the thing it guards against.
        ends = [piece for piece, (_, high) in _unpainted_edge(features) if high < 0.35]
        assert ends, f"{name}: no road end left bare, so the guard proved nothing"


def test_a_kerb_line_is_a_road_edge_and_belongs_to_no_lane() -> None:
    """Solid, like the lane edge it takes over from, and with no `lane_id` to be wrong about."""
    features, kerbs = _kerbs(_model())
    assert kerbs
    for identifier in kerbs:
        feature = features[identifier]
        assert feature["type"] == _BOUNDARY_TYPE
        assert "lane_id" not in feature and "side" not in feature


def test_the_kerb_is_counted_apart_from_the_markings_that_belong_to_lanes() -> None:
    """`edges` and `merged` are counted by type, so a kerb left in them would corrupt both."""
    model = _sharing_a_divider()
    markings = _built(model)["metadata"]["lane_markings"]
    _, kerbs = _kerbs(model)
    assert markings["junction_kerbs"] == len(kerbs)
    # `merged` is a difference against what the model holds, so a kerb counted here would push
    # it below zero rather than merely make it larger.
    assert markings["merged"] >= 0
    assert markings["edges"] == sum(
        1 for lane in model.lanes for boundary in lane.boundaries
    ) - markings["merged"] - markings["dividers"]


# --- paint that lies on tarmac a car drives on ----------------------------------------------


def _band(x0: float, x1: float, y: float, width: float = WIDTH) -> list[Point2D]:
    """A lane surface centred on `y` rather than on zero."""
    half = width / 2
    return [
        Point2D(x=x0, y=y - half),
        Point2D(x=x1, y=y - half),
        Point2D(x=x1, y=y + half),
        Point2D(x=x0, y=y + half),
        Point2D(x=x0, y=y - half),
    ]


def _alongside(
    *, x0: float, x1: float, y: float, way: str = "300", extra: list[LaneFeature] | None = None
) -> PreliminaryLaneModel:
    """One straight lane, with one or more lanes of another way laid over its left half.

    `main` runs along y=0 with its two edges at y=-2 and y=+2. A lane centred on y=+3 covers
    y=+1..+5, so `main`'s left edge is a metre inside it and its own right edge is a metre inside
    `main` - the merge, in the smallest arrangement that has one.
    """
    main = _lane(
        "main",
        boundaries=[
            LaneBoundary(identifier="main-left", side="left", points=_line(2.0)),
            LaneBoundary(identifier="main-right", side="right", points=_line(-2.0)),
        ],
    )
    joining = [
        _lane(
            "ramp",
            source_way_ids=[way],
            source_edge=["7", "8", "0"],
            centerline=_line(y, x0, x1),
            polygon=_band(x0, x1, y),
            boundaries=[
                LaneBoundary(
                    identifier="ramp-left", side="left", points=_line(y + 2.0, x0, x1)
                ),
                LaneBoundary(
                    identifier="ramp-right", side="right", points=_line(y - 2.0, x0, x1)
                ),
            ],
        ),
        *(extra or []),
    ]
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [lane.model_dump() for lane in (main, *joining)],
            "connectors": [],
        }
    )


def _painted(model: PreliminaryLaneModel) -> dict[str, float]:
    """Every painted line that belongs to a lane, by id, with the length it was drawn at."""
    built = _map_features(model, _lane_neighbours(model), _lane_change_moves(model))
    return {
        identifier: LineString(feature["polyline"]).length
        for identifier, feature in built.features.items()
        if feature["type"] in (_BOUNDARY_TYPE, _DIVIDER_TYPE)
        and feature.get("lane_id") is not None
    }


def test_a_line_is_cut_where_it_lies_on_the_lane_it_merges_with() -> None:
    """The defect Keith reported, in both directions at once.

    A merging lane and the road it joins are always different OSM ways, so neither knows the
    other is there and both draw a solid edge through it. Here the ramp covers the last 20 m of
    `main`, so `main`'s left edge is cut back to the 30 m that is still open ground and the
    ramp's own right edge, which lies inside `main` for its whole length, is not drawn at all.
    """
    painted = _painted(_alongside(x0=30.0, x1=50.0, y=3.0))
    # The cut starts one tolerance late at the covering surface's own end, which is what that
    # tolerance is: the line is left alone until it is unmistakably inside.
    assert painted["main-left"] == pytest.approx(30.0 + _COVERED_PAINT_TOLERANCE_M)
    assert painted["main-right"] == pytest.approx(50.0)
    assert painted["ramp-left"] == pytest.approx(20.0)
    assert "ramp-right" not in painted


def test_a_line_cut_in_two_gives_up_the_id_that_named_one_line() -> None:
    """One surviving piece keeps its id; two cannot both be "this lane's left edge"."""
    over = _lane(
        "second",
        source_way_ids=["400"],
        source_edge=["9", "10", "0"],
        centerline=_line(3.0, 0.0, 10.0),
        polygon=_band(0.0, 10.0, 3.0),
    )
    # Covered over x=0..10 and again over x=20..30, so what is left of `main`'s left edge is two
    # separate lines rather than one shortened one.
    painted = _painted(_alongside(x0=20.0, x1=30.0, y=3.0, extra=[over]))
    assert "main-left" not in painted
    edge = _COVERED_PAINT_TOLERANCE_M
    assert sorted(
        length
        for name, length in painted.items()
        if name not in {"main-right", "ramp-left", "ramp-right"}
    ) == pytest.approx([10.0 + 2 * edge, 20.0 + edge])


def test_two_lanes_of_one_way_keep_the_line_between_them() -> None:
    """The exclusion that stops this deleting every lane divider on the map.

    Two lanes of one way are offsets of one base line and meet exactly on their shared edge, but
    a mitre join on a curve puts a real divider up to 0.345 m inside its neighbour on `mosque`.
    So the line here is laid deeper inside than the tolerance would forgive, and survives anyway
    because the two lanes are on the same way.
    """
    model = _alongside(x0=0.0, x1=50.0, y=3.0 - 2 * _COVERED_PAINT_TOLERANCE_M, way="200")
    painted = _painted(model)
    assert painted["main-left"] == pytest.approx(50.0)
    assert painted["ramp-right"] == pytest.approx(50.0)


def test_a_lane_is_not_nibbled_by_its_own_junction_turn() -> None:
    """A lane and the turn leaving it are meant to touch, so the turn never clips its edges."""
    lane = _lane(
        "a",
        exit_lanes=["c"],
        boundaries=[
            LaneBoundary(identifier="a-left", side="left", points=_line(2.0)),
            LaneBoundary(identifier="a-right", side="right", points=_line(-2.0)),
        ],
    )
    model = _model(lanes=[lane, *(x for x in _model().lanes if x.identifier != "a")])
    # The turn laps 5 m back over the lane it leaves, which without the exclusion would take
    # 5 m off both of that lane's edges.
    lapped = model.connectors[0].model_copy(
        update={"centerline": _straight(45.0, 60.0), "polygon": _surface(45.0, 60.0)}
    )
    painted = _painted(model.model_copy(update={"connectors": [lapped]}))
    assert painted["a-left"] == pytest.approx(50.0)
    assert painted["a-right"] == pytest.approx(50.0)


def test_a_surviving_piece_shorter_than_the_needle_filter_is_not_drawn() -> None:
    over = _lane(
        "second",
        source_way_ids=["400"],
        source_edge=["9", "10", "0"],
        centerline=_line(3.0, 0.0, 50.0 - _MIN_PAINT_M / 2),
        polygon=_band(0.0, 50.0 - _MIN_PAINT_M / 2, 3.0),
    )
    painted = _painted(_alongside(x0=30.0, x1=50.0, y=3.0, extra=[over]))
    assert "main-left" not in painted


def test_a_hole_shorter_than_the_needle_filter_is_not_opened() -> None:
    """A break of a few centimetres reads as a broken line, not as a gap - so it is not made.

    The same judgement `_KERB_GAP_CLOSE_M` makes about a seam, and the histogram picks the
    number: the interior holes this cuts on the real maps measure 0.23 m and then nothing at all
    until 4.78 m.
    """
    gap = _MIN_PAINT_M / 2
    over = _lane(
        "second",
        source_way_ids=["400"],
        source_edge=["9", "10", "0"],
        centerline=_line(3.0, 0.0, 30.0 - gap),
        polygon=_band(0.0, 30.0 - gap, 3.0),
    )
    painted = _painted(_alongside(x0=30.0, x1=50.0, y=3.0, extra=[over]))
    assert "main-left" not in painted  # every part of it is covered but the bridged gap
    assert not [name for name in painted if name.startswith("main-left")]


def test_no_painted_line_on_the_real_maps_runs_through_a_lane() -> None:
    """The test that would have caught this.

    A residual run can survive where a hole under `_MIN_PAINT_M` was bridged rather than opened,
    and that is bounded by the bridging: nothing may lie on tarmac for as far as the shortest
    piece of line worth drawing.
    """
    models = _real_models()
    if not models:
        pytest.skip("workspaces/ is gitignored and not present")
    for name, model in models:
        built = _map_features(model, _lane_neighbours(model), _lane_change_moves(model))
        lanes = {lane.identifier: set(lane.source_way_ids) for lane in model.lanes}
        junction_ends: dict[str, set[str]] = {}
        shapes: dict[str, Polygon] = {}
        for identifier, feature in built.features.items():
            if feature["type"] != _LANE_TYPE:
                continue
            shapes[identifier] = Polygon(feature["polygon"]).buffer(0)
            if identifier in lanes:
                continue
            for end in (*feature["entry_lanes"], *feature["exit_lanes"]):
                junction_ends.setdefault(end, set()).add(identifier)
        worst = 0.0
        for feature in built.features.values():
            owner = feature.get("lane_id")
            if feature["type"] not in (_BOUNDARY_TYPE, _DIVIDER_TYPE) or owner is None:
                continue
            line = LineString(feature["polyline"])
            for identifier, shape in shapes.items():
                if identifier == owner or identifier in junction_ends.get(owner, ()):
                    continue
                if lanes.get(identifier, frozenset()) & lanes[owner]:
                    continue
                inside = line.intersection(shape.buffer(-_COVERED_PAINT_TOLERANCE_M))
                for part in getattr(inside, "geoms", [inside]):
                    worst = max(worst, part.length)
        assert worst < _MIN_PAINT_M, f"{name}: {worst:.3f} m of paint runs through a lane"


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

    scenario = _built(_sharing_a_divider())
    types_used = {feature["type"] for feature in scenario["map_features"].values()}
    assert types_used == {
        "LANE_SURFACE_STREET",
        "ROAD_EDGE_BOUNDARY",
        "ROAD_LINE_BROKEN_SINGLE_WHITE",
    }
    assert MetaDriveType.is_lane("LANE_SURFACE_STREET")
    # `has_type` covers object types and never sees a map feature, so the boundary is
    # checked against the constant MetaDrive actually names it with.
    assert MetaDriveType.BOUNDARY_LINE == "ROAD_EDGE_BOUNDARY"
    # The divider type has to satisfy `is_broken_line` specifically. `is_road_boundary_line`
    # is routed to `_construct_continuous_line` by `ScenarioBlock` whatever else it is, so a
    # near-miss here would draw solid and look like the feature had not been built.
    assert MetaDriveType.LINE_BROKEN_SINGLE_WHITE == "ROAD_LINE_BROKEN_SINGLE_WHITE"
    assert MetaDriveType.is_road_line("ROAD_LINE_BROKEN_SINGLE_WHITE")
    assert MetaDriveType.is_broken_line("ROAD_LINE_BROKEN_SINGLE_WHITE")


# --- the step rate -------------------------------------------------------------------------
#
# The rate changes how densely the drive is written down and nothing else. What these check is
# that claim: an unflagged conversion is byte-for-byte what it was, and a faster one is the
# same drive with more samples in it.


def _routes_file(workspace: Path, *, name: str = "r") -> Path:
    """A `routes.json` for `_model()`, carrying the identity block the converter demands."""
    path = workspace / "routes.json"
    path.write_text(
        json.dumps(
            {
                "routes_version": 1,
                "identity": {
                    "generation_fingerprint": "fingerprint",
                    "reviewed_lane_model_sha256": _sha256(
                        workspace / "lane-model" / "reviewed.json"
                    ),
                },
                "routes": [{"name": name, "start_lane": "a", "end_lane": "d"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_default_rate_and_asking_for_it_convert_to_the_same_bytes(tmp_path: Path) -> None:
    """The invariant the whole flag rests on: unflagged is what it was.

    Compared as pickled bytes rather than as dicts, because that is what a reader loads and
    what `sha256sum -c` sees. `_PortablePickler` writes deterministically, so two conversions
    of one workspace differ only if something in the scenario differs.
    """
    unflagged = _workspace(tmp_path / "one", _model())
    asked = _workspace(tmp_path / "two", _model())
    left, *_ = convert_scenario(
        workspace=unflagged,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(unflagged),
    )
    right, *_ = convert_scenario(
        workspace=asked,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(asked),
        step_hz=10.0,
    )
    assert [path.read_bytes() for path in left] == [path.read_bytes() for path in right]


def test_a_faster_rate_writes_the_same_drive_with_ten_times_the_samples(tmp_path: Path) -> None:
    slow_ws = _workspace(tmp_path / "slow", _model())
    fast_ws = _workspace(tmp_path / "fast", _model())
    slow_paths, *_ = convert_scenario(
        workspace=slow_ws,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(slow_ws),
    )
    fast_paths, _, _, report_path, _ = convert_scenario(
        workspace=fast_ws,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(fast_ws),
        step_hz=100.0,
    )
    slow = pickle.loads(slow_paths[0].read_bytes())
    fast = pickle.loads(fast_paths[0].read_bytes())

    # `ts` spacing *is* the rate - there is no `dt` key, and none is wanted.
    assert fast["metadata"]["ts"][1] - fast["metadata"]["ts"][0] == pytest.approx(0.01)
    assert fast["length"] == len(fast["metadata"]["ts"])
    assert fast["length"] == len(fast["tracks"]["ego"]["state"]["position"])
    assert fast["length"] >= (slow["length"] - 1) * 10 + 1

    # The same drive: same distance, same duration, same speeds. Only the density moved.
    report = json.loads(report_path.read_text(encoding="utf-8"))
    slow_report = json.loads(
        (slow_ws / "reports" / "scenario-conversion.json").read_text(encoding="utf-8")
    )
    for key in ("distance_m", "duration_s", "speed_kph", "slowest_kph"):
        assert report["routes"][0][key] == pytest.approx(slow_report["routes"][0][key], abs=1e-9)


def test_every_light_state_array_is_as_long_as_the_faster_scenario(tmp_path: Path) -> None:
    """`_check_object_state_dict` length-checks every array in a light's `state`.

    The tape is rebuilt at the scenario's length, so a rate that reached `ts` but not
    `light_states` would fail only on a dataset that has signals - which is most of them.
    """
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
    paths, *_ = convert_scenario(
        workspace=workspace,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(workspace),
        signals=plan_path,
        step_hz=100.0,
    )
    scenario = pickle.loads(paths[0].read_bytes())
    light = scenario["dynamic_map_states"]["a"]
    for key, value in light["state"].items():
        assert len(value) == scenario["length"], key
    # And the plan's own numbers are seconds, so they do not move with the rate.
    assert scenario["metadata"]["signals"]["cycle_seconds"] == 60


def test_the_light_says_the_same_colour_at_the_same_second_at_either_rate(
    tmp_path: Path,
) -> None:
    """A tape is indexed by step, so the colours only agree once the second is worked out."""
    slow_ws = _workspace(tmp_path / "slow", _model())
    fast_ws = _workspace(tmp_path / "fast", _model())

    def plan_for(workspace: Path) -> Path:
        path = workspace / "signals.json"
        path.write_text(
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
        return path

    slow_paths, *_ = convert_scenario(
        workspace=slow_ws,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(slow_ws),
        signals=plan_for(slow_ws),
    )
    fast_paths, *_ = convert_scenario(
        workspace=fast_ws,
        config=ConverterConfig(config_version=1),
        routes=_routes_file(fast_ws),
        signals=plan_for(fast_ws),
        step_hz=100.0,
    )
    slow = pickle.loads(slow_paths[0].read_bytes())["dynamic_map_states"]["a"]
    fast = pickle.loads(fast_paths[0].read_bytes())["dynamic_map_states"]["a"]
    shared = min(len(slow["state"]["object_state"]), len(fast["state"]["object_state"]) // 10)
    assert shared > 1
    assert (
        list(slow["state"]["object_state"][:shared])
        == list(fast["state"]["object_state"][:: 10][:shared])
    )


def test_a_rate_that_cannot_be_written_down_exactly_is_refused() -> None:
    """Refused rather than rounded: `ts` is an integer index times this number."""
    assert _step_seconds(None) == 0.1
    assert _step_seconds(100.0) == 0.01
    with pytest.raises(ConversionError, match="whole number of microseconds"):
        _step_seconds(3.0)
    for bad in (0.0, -10.0, float("nan"), float("inf")):
        with pytest.raises(ConversionError, match="positive number of hertz"):
            _step_seconds(bad)


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
    # Through the turn, not past it: `a` leads into connector `c`, and `c` leads to `b`.
    assert features["a"]["exit_lanes"] == ["c"]
    assert features["c"]["exit_lanes"] == ["b"]
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
