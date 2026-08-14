"""Stage 3 review view: the stateful decision surface over a generated lane model.

The map itself comes from :func:`osm_scenario.generation.build_review_payload`, the
same builder the read-only Stage 2 audit uses. This module adds the identity a
review is bound to, the per-finding scoping the client needs for bulk actions, and
the single-file HTML shell that hosts the compiled client.

The client is TypeScript under ``web/`` compiled by ``web/build.mjs``. Node is
build-time only: the compiled bundle is committed beside this module, so an
installed CLI never needs it.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from osm_scenario.generation import build_review_payload
from osm_scenario.lane_model import PreliminaryLaneModel
from osm_scenario.osm_source import read_osm_snapshot

PAYLOAD_VERSION = 2
CLIENT_ASSET = "review-client.js"

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 3 Review — {workspace}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
</head><body>
<aside id="app"></aside><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>window.__REVIEW_PAYLOAD__={payload};</script>
<script>{client}</script>
</body></html>
"""


class ReviewError(RuntimeError):
    """Raised when the Stage 3 review view cannot be produced."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReviewError(f"Required artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def client_source() -> str:
    """The compiled review client, or a clear error when it was never built."""
    try:
        return resources.files("osm_scenario.assets").joinpath(CLIENT_ASSET).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:  # pragma: no cover - packaging fault
        raise ReviewError(
            f"The review client bundle is missing ({CLIENT_ASSET}). "
            "Run `npm install && npm run build` in web/ to rebuild it."
        ) from error


def _identity(
    *,
    workspace: Path,
    model: PreliminaryLaneModel,
    report: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "workspace": workspace.name,
        "source_checksum": manifest["source"]["sha256"],
        "generation_fingerprint": report["generation_fingerprint"],
        "generator_version": report["generator_version"],
        "lane_model_schema_version": report["lane_model_schema_version"],
        "configuration_checksum": model.metadata.configuration_checksum,
        "generated_at": report["generated_at"],
    }


def _road_class_index(model: PreliminaryLaneModel) -> dict[str, str]:
    """Road class per feature id, for scoping bulk decisions and labelling findings.

    Covers lanes, their source ways, and connectors. A connector names neither a lane
    nor a way in its own id, so it inherits the class of the lane it leaves — without
    that, every connector finding would arrive unscoped.
    """
    index = {lane.identifier: lane.road_class for lane in model.lanes}
    for lane in model.lanes:
        for way_id in lane.source_way_ids:
            index.setdefault(way_id, lane.road_class)
    for connector in model.connectors:
        road_class = index.get(connector.from_lane_id) or index.get(connector.to_lane_id)
        if road_class is not None:
            index.setdefault(connector.identifier, road_class)
    return index


def _center(model: PreliminaryLaneModel, features: list[dict[str, Any]]) -> list[float]:
    """Mean of the drawn lane centrelines, so the view opens on the network."""
    longitudes: list[float] = []
    latitudes: list[float] = []
    for feature in features:
        if feature["properties"].get("kind") != "lane_centerline":
            continue
        for longitude, latitude in feature["geometry"]["coordinates"]:
            longitudes.append(longitude)
            latitudes.append(latitude)
    if not longitudes:
        raise ReviewError("The lane model has no drawable centrelines to centre the map on.")
    return [sum(latitudes) / len(latitudes), sum(longitudes) / len(longitudes)]


def build_payload(workspace: Path) -> dict[str, Any]:
    """Assemble everything the review client needs from a generated workspace."""
    model_path = workspace / "lane-model" / "preliminary.json"
    report = _read_json(workspace / "reports" / "lane-model-generation.json")
    manifest = _read_json(workspace / "source" / "manifest.json")
    model = PreliminaryLaneModel.model_validate(_read_json(model_path))

    if report["status"] != "passed":
        raise ReviewError(
            f"Stage 2 generation status is {report['status']!r}; regenerate before reviewing."
        )
    if report["generation_fingerprint"] != model.metadata.generation_fingerprint:
        raise ReviewError(
            "The lane model and its generation report disagree on the fingerprint. "
            "Re-run `osm-scenario generate-map` before reviewing."
        )

    snapshot = read_osm_snapshot(workspace / "source" / "map.osm")
    shared = build_review_payload(model, snapshot)
    features = shared["features"]["features"]
    road_classes = _road_class_index(model)

    findings: list[dict[str, Any]] = []
    for finding in shared["findings"]:
        # Bulk decisions are scoped by rule *and* road class, so every finding has to
        # carry one. Prefer the feature it affects; fall back to what it came from.
        road_class = next(
            (
                road_classes[item]
                for item in [*finding["affected_feature_ids"], *finding["source_ids"]]
                if item in road_classes
            ),
            None,
        )
        findings.append({**finding, "road_class": road_class})

    return {
        "payload_version": PAYLOAD_VERSION,
        "identity": _identity(
            workspace=workspace, model=model, report=report, manifest=manifest
        ),
        "center": _center(model, features),
        "features": features,
        "findings": findings,
        "lanes": [
            {
                "identifier": lane.identifier,
                "source_way_ids": lane.source_way_ids,
                "lane_index": lane.lane_index,
                "lane_count": lane.lane_count,
                "direction": lane.direction,
                "road_class": lane.road_class,
                "width_m": lane.width_m,
                "speed_limit_kph": lane.speed_limit_kph,
                "turn_permissions": lane.turn_permissions,
                "entry_lanes": lane.entry_lanes,
                "exit_lanes": lane.exit_lanes,
            }
            for lane in model.lanes
        ],
        "connectors": [
            {
                "identifier": connector.identifier,
                "junction_node_id": connector.junction_node_id,
                "from_lane_id": connector.from_lane_id,
                "to_lane_id": connector.to_lane_id,
                "from_way_id": connector.from_way_id,
                "to_way_id": connector.to_way_id,
                "movement": connector.movement,
                "turn_angle_degrees": connector.turn_angle_degrees,
                "status": connector.status,
            }
            for connector in model.connectors
        ],
        "counts": shared["summary"],
    }


def render_review_html(payload: dict[str, Any]) -> str:
    """Wrap the payload and the compiled client in one self-contained page."""
    encoded = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.format(
        workspace=payload["identity"]["workspace"],
        payload=encoded,
        client=client_source(),
    )


def generate_review(*, workspace: Path) -> Path:
    """Write the Stage 3 review view and return its path."""
    output = workspace / "inspection" / "stage-3-review.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_review_html(build_payload(workspace)), encoding="utf-8")
    return output
