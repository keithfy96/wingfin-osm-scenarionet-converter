"""Stage 6 - convert the validated map into a map-only ScenarioNet dataset.

Reads `lane-model/reviewed.json` and writes three pickles. It never writes back into the
lane model, so a bad conversion costs nothing but the output directory.

Two things about this stage are worth knowing before reading the code.

* **`entry_lanes` / `exit_lanes` hold two kinds of id.** A continuation - the same road
  carrying on - is recorded as the next lane's id. A junction movement is recorded as a
  *connector* id, and the lane on the other side is inside that connector. ScenarioNet
  wants lane ids and nothing else, so every connector reference is swapped for the lane it
  leads to. In `junction-1` that is 422 lane ids left alone and 166 connector ids
  swapped - exactly twice the 83 active connectors, which is the arithmetic that says the
  swap is complete.

* **ScenarioNet is deliberately not a dependency, but the schema is still pinned.** The
  scenario dict is built by hand against MetaDrive's `ScenarioDescription`.
  `test_the_scenario_passes_metadrives_own_sanity_check` runs MetaDrive's real
  `sanity_check` against a converted scenario by loading its schema module directly from a
  checkout - no install, no panda3d - and skips where no checkout exists. So the field
  names below are measured against MetaDrive 0.4.3 rather than assumed, and a schema change
  fails a test rather than surfacing as a load error hours later.
"""

from __future__ import annotations

import json
import os
import pickle
import statistics
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

# Private helpers imported across modules on purpose, for the reason `validation` gives:
# these are the exact routines the earlier stages use, and a Stage 6 copy would be a
# second implementation to keep in step. `_sha256` must match what Stage 5 wrote into the
# manifest or every run reports a stale checksum.
from osm_scenario.apply_review import ApplyReviewError, _read_json, _sha256
from osm_scenario.config import ConverterConfig
from osm_scenario.lane_model import LaneFeature, PreliminaryLaneModel
from osm_scenario.stage1b_data_audit import _write_text_atomic

REPORT_VERSION = 1

SUMMARY_FILE = "dataset_summary.pkl"
MAPPING_FILE = "dataset_mapping.pkl"

# Protocol 4 rather than the interpreter default. The dataset is meant to be handed to a
# separate, lockfile-pinned ScenarioNet environment whose Python is chosen by MetaDrive's
# constraints, not ours; 4 is readable everywhere that matters and costs nothing here.
_PICKLE_PROTOCOL = 4

_LANE_TYPE = "LANE_SURFACE_STREET"
_BOUNDARY_TYPE = "ROAD_EDGE_BOUNDARY"
_FORMAT_VERSION = "1.0"

_DATASET_NAME = "osm-scenario"
_DATASET_VERSION = "v1"

# MetaDrive's coordinate frame: right-handed, metres. Nothing in MetaDrive 0.4.3 branches on
# this value - the only assignment is in its own `scenario/utils.py` - so it is a label, not
# a transform. `metadrive_processed` stays False because this dataset came from a converter,
# which is what every ScenarioNet converter records.
_COORDINATE = "metadrive"


def scenario_file_name(scenario_id: str) -> str:
    """The one filename the dataset is keyed on, in the form MetaDrive insists on.

    `ScenarioDescription.is_scenario_file` accepts a name only if it starts with `sd_` or is
    entirely digits, and `read_dataset_summary` asserts it for every entry in the summary.
    So a friendly name like `scenario.pkl` loads nowhere. Built the way MetaDrive's own
    `get_export_file_name` builds it, and derived rather than constant so the summary and
    the mapping cannot key on different strings.
    """
    return f"sd_{_DATASET_NAME}_{_DATASET_VERSION}_{scenario_id}.pkl"


class ConversionError(RuntimeError):
    """Raised when the validated map cannot be converted."""


def _read(path: Path, label: str) -> Any:
    """`_read_json`, with its failures wearing this stage's name.

    The reader is shared on purpose - one implementation of "missing or malformed JSON" -
    but it raises `ApplyReviewError`, which the Stage 6 CLI has no reason to catch. Without
    this, pointing `convert` at a workspace with no manifest prints a traceback instead of
    a sentence.
    """
    try:
        return _read_json(path, label)
    except ApplyReviewError as error:
        raise ConversionError(str(error)) from error


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    """Write a pickle in one step, or not at all.

    The text twin of this lives in `stage1b_data_audit`. A half-written pickle is worse
    than a missing one: it can still unpickle far enough to look like a scenario.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _check_stage_5(workspace: Path, model_path: Path) -> dict[str, Any]:
    """The model on disk is the one Stage 5 passed, unchanged since.

    Checked before anything is read out of the model, because converting a map that was
    edited after validation ships geometry nobody checked.
    """
    manifest = _read(workspace / "source" / "manifest.json", "Stage 1 manifest")
    stage_5 = manifest.get("stage_5")
    if not isinstance(stage_5, dict) or stage_5.get("status") != "passed":
        raise ConversionError("Stage 5 has not passed; run validate-map first")
    if not model_path.is_file():
        raise ConversionError(f"reviewed lane model not found: {model_path}")

    recorded = stage_5.get("validated_lane_model", {}).get("sha256")
    actual = _sha256(model_path)
    if recorded != actual:
        raise ConversionError(
            "reviewed lane model checksum does not match the Stage 5 manifest: the model "
            "changed after it was validated"
        )
    return manifest


def _lane_neighbours(model: PreliminaryLaneModel) -> dict[str, tuple[list[str], list[str]]]:
    """Each lane's entries and exits, as lane ids only.

    A connector reference is replaced by the lane on the far side of it. A connector that
    is not `active` is dropped rather than followed: a movement the review forbade must
    not reappear as a drivable edge just because something still names it. `junction-1`
    references none today, which is exactly when a guard is cheap to add.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    connectors = {item.identifier: item for item in model.connectors}
    resolved: dict[str, tuple[list[str], list[str]]] = {}

    for lane in model.lanes:
        sides: list[list[str]] = []
        for references, entering in ((lane.entry_lanes, True), (lane.exit_lanes, False)):
            out: list[str] = []
            for reference in references:
                if reference in lanes:
                    out.append(reference)
                    continue
                connector = connectors.get(reference)
                if connector is None:
                    raise ConversionError(
                        f"lane {lane.identifier} names {reference} as "
                        f"{'an entry' if entering else 'an exit'}, but it is neither a "
                        "lane nor a connector in this model"
                    )
                if connector.status != "active":
                    continue
                near = connector.to_lane_id if entering else connector.from_lane_id
                far = connector.from_lane_id if entering else connector.to_lane_id
                if near != lane.identifier:
                    raise ConversionError(
                        f"connector {connector.identifier} is listed on lane "
                        f"{lane.identifier} but joins {connector.from_lane_id} to "
                        f"{connector.to_lane_id}"
                    )
                if far not in lanes:
                    raise ConversionError(
                        f"connector {connector.identifier} leads to {far}, which is not a "
                        "lane in this model"
                    )
                out.append(far)
            # Duplicates are possible where a continuation and a connector name the same
            # lane. Deduplicated in first-seen order so the output is stable without
            # sorting hex ids into an order that means nothing.
            sides.append(list(dict.fromkeys(out)))
        resolved[lane.identifier] = (sides[0], sides[1])

    return resolved


def _reachability(neighbours: Mapping[str, tuple[list[str], list[str]]]) -> dict[str, Any]:
    """Where a car can actually get to, respecting one-way direction.

    Stage 5 reports `routing_components`, which uses *weakly* connected components - it
    ignores direction, so two one-way lanes pointing away from each other still count as
    one piece. That is the right measure for "is this map internally sound", and the wrong
    one for "can a route be driven here". Both are true at once and they disagree wildly:
    `junction-1` is 6 pieces weakly and 274 strongly.

    So the dataset carries the only number a person planning a MetaDrive route can use -
    the best starting lane and how far it gets.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(neighbours)
    for lane_id, (_, exits) in neighbours.items():
        for target in exits:
            graph.add_edge(lane_id, target)

    reach = {lane_id: len(nx.descendants(graph, lane_id)) for lane_id in graph}
    best = max(reach.items(), key=lambda item: (item[1], item[0]))
    strong = sorted((len(part) for part in nx.strongly_connected_components(graph)), reverse=True)
    return {
        "best_start_lane_id": best[0],
        "best_start_reaches": best[1],
        "median_reach": statistics.median(reach.values()) if reach else 0,
        "lanes_reaching_nothing": sum(1 for count in reach.values() if count == 0),
        "reachable_lane_pairs": sum(reach.values()),
        "possible_lane_pairs": len(graph) * (len(graph) - 1),
        # Summarised, not listed. `junction-1` has 274 of these and 273 are a single lane,
        # so the full list is 274 numbers carrying two facts.
        "components_respecting_direction": {
            "count": len(strong),
            "largest": strong[0] if strong else 0,
        },
    }


def _polyline(points: Any) -> np.ndarray:
    return np.array([[point.x, point.y] for point in points], dtype=np.float64)


def _lane_feature(
    lane: LaneFeature, entries: list[str], exits: list[str]
) -> dict[str, Any]:
    return {
        "type": _LANE_TYPE,
        "polyline": _polyline(lane.centerline),
        "polygon": _polyline(lane.polygon),
        "speed_limit_kmh": lane.speed_limit_kph,
        # MetaDrive's `ScenarioLane` ignores this and uses its own `VIS_LANE_WIDTH`. Kept
        # because it is the surveyed width and the field is the format's own name for it.
        "width": lane.width_m,
        "entry_lanes": entries,
        "exit_lanes": exits,
        # Lists, not bare ids: that is the shape every ScenarioNet converter writes, and an
        # empty list is how "no neighbour" is spelled. MetaDrive 0.4.3 stores these and
        # reads them nowhere - `ScenarioLane.get_lane_width` returns before it reaches the
        # only code that would - so this is convention rather than a load requirement.
        "left_neighbor": [lane.left_neighbor] if lane.left_neighbor else [],
        "right_neighbor": [lane.right_neighbor] if lane.right_neighbor else [],
    }


def _map_features(
    model: PreliminaryLaneModel, neighbours: Mapping[str, tuple[list[str], list[str]]]
) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    for lane in model.lanes:
        entries, exits = neighbours[lane.identifier]
        features[lane.identifier] = _lane_feature(lane, entries, exits)

    for lane in model.lanes:
        for boundary in lane.boundaries:
            if boundary.identifier in features:
                raise ConversionError(
                    f"boundary {boundary.identifier} on lane {lane.identifier} shares an "
                    "id with another map feature"
                )
            features[boundary.identifier] = {
                # `boundary_type` is `unknown` throughout the lane model - the source has
                # no marking survey - so the generic road edge is the strongest honest
                # claim. Naming a line style here would invent evidence.
                "type": _BOUNDARY_TYPE,
                "polyline": _polyline(boundary.points),
                "side": boundary.side,
                "lane_id": lane.identifier,
            }
    return features


def _scenario(
    *,
    model: PreliminaryLaneModel,
    workspace_name: str,
    manifest: dict[str, Any],
    model_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The scenario dict, and the reachability facts computed on the way."""
    neighbours = _lane_neighbours(model)
    features = _map_features(model, neighbours)

    dangling = {
        target
        for entries, exits in neighbours.values()
        for target in (*entries, *exits)
        if target not in features
    }
    if dangling:
        raise ConversionError(
            f"{len(dangling)} lane reference(s) survived resolution without a map "
            f"feature: {', '.join(sorted(dangling)[:5])}"
        )

    routing = _reachability(neighbours)
    # A 16-hex-digit prefix, which is how the fingerprint is written everywhere a person
    # reads it. The id ends up in the filename and in MetaDrive's logs, and the full 64
    # characters are one field away in `metadata.provenance`, so nothing is lost by not
    # spending 64 of them here.
    scenario_id = f"{workspace_name}-{model.metadata.generation_fingerprint[:16]}"
    scenario = {
        "id": scenario_id,
        "version": _FORMAT_VERSION,
        # One step, no motion. A map-only scenario still needs an envelope with a length
        # for the format to be well formed; it must not imply that anything moves.
        "length": 1,
        "tracks": {},
        "dynamic_map_states": {},
        "map_features": features,
        "metadata": {
            "scenario_id": scenario_id,
            "dataset": _DATASET_NAME,
            # The three keys `ScenarioDescription.METADATA_KEYS` requires. `ts` must be an
            # array whose shape equals `length` - `sanity_check` reads `.shape` on it, so a
            # plain list fails there rather than at load.
            "coordinate": _COORDINATE,
            "metadrive_processed": False,
            "ts": np.zeros(1, dtype=np.float64),
            "sdc_id": None,
            "map_only": True,
            "coordinate_system_wkt": model.metadata.coordinate_system_wkt,
            "counts": {
                "lanes": len(model.lanes),
                "lane_boundaries": len(features) - len(model.lanes),
                "connectors_total": len(model.connectors),
                "connectors_active": sum(1 for item in model.connectors if item.status == "active"),
                "signals": len(model.signals),
                "stop_lines": len(model.stop_lines),
                "restrictions": len(model.restrictions),
            },
            # Signals are counted, never converted. Their timing is not in the source and
            # is explicitly out of scope; a fabricated phase plan would be indistinguishable
            # from a surveyed one once it is inside a pickle.
            "routing": routing,
            "provenance": {
                "generator_version": model.metadata.generator_version,
                "generation_fingerprint": model.metadata.generation_fingerprint,
                "source_osm_sha256": manifest["source"]["sha256"],
                "reviewed_lane_model_sha256": model_sha256,
                "stage_5_status": manifest["stage_5"]["status"],
            },
        },
    }
    return scenario, routing


def convert_scenario(
    *, workspace: Path, config: ConverterConfig
) -> tuple[Path, Path, Path, Path]:
    """Convert WORKSPACE's validated lane model into a map-only ScenarioNet dataset.

    `config` is accepted for symmetry with the other stage entry points; conversion is a
    faithful restatement of the reviewed model and has nothing left to configure.
    """
    workspace = workspace.resolve()
    model_path = workspace / "lane-model" / "reviewed.json"
    manifest = _check_stage_5(workspace, model_path)
    model_sha256 = _sha256(model_path)

    model = PreliminaryLaneModel.model_validate(_read(model_path, "reviewed lane model"))
    scenario, routing = _scenario(
        model=model,
        workspace_name=workspace.name,
        manifest=manifest,
        model_sha256=model_sha256,
    )

    dataset_dir = workspace / "scenarionet"
    file_name = scenario_file_name(scenario["id"])
    scenario_path = dataset_dir / file_name
    summary_path = dataset_dir / SUMMARY_FILE
    mapping_path = dataset_dir / MAPPING_FILE

    _write_bytes_atomic(scenario_path, pickle.dumps(scenario, protocol=_PICKLE_PROTOCOL))
    _write_bytes_atomic(
        summary_path,
        pickle.dumps({file_name: scenario["metadata"]}, protocol=_PICKLE_PROTOCOL),
    )
    # An empty relative path means "beside the summary". Both index files key on the one
    # `file_name` computed above, so the two cannot drift apart.
    _write_bytes_atomic(
        mapping_path, pickle.dumps({file_name: ""}, protocol=_PICKLE_PROTOCOL)
    )

    artifacts = {}
    for name, path in (
        ("scenario", scenario_path),
        ("dataset_summary", summary_path),
        ("dataset_mapping", mapping_path),
    ):
        artifacts[name] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }

    report = {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "converted",
        "scenario_id": scenario["id"],
        "inputs": {
            "reviewed_lane_model": "lane-model/reviewed.json",
            "reviewed_lane_model_sha256": model_sha256,
        },
        "converted": scenario["metadata"]["counts"],
        "map_features": len(scenario["map_features"]),
        "routing": routing,
        # Named outside the pickle because it is the string every MetaDrive entry point
        # keys on, and the first thing to check when a load fails.
        "scenario_file": file_name,
        "artifacts": artifacts,
    }

    report_path = workspace / "reports" / "scenario-conversion.json"
    _write_text_atomic(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")

    manifest["stage_6"] = {
        "status": report["status"],
        "scenario_id": scenario["id"],
        "converted": report["converted"],
        "map_features": report["map_features"],
        "routing": routing,
        "source_lane_model": {"path": "lane-model/reviewed.json", "sha256": model_sha256},
        "artifacts": artifacts,
    }
    (workspace / "source" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return scenario_path, summary_path, mapping_path, report_path
