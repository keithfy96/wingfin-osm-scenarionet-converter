# ruff: noqa: E501
"""Stage 2 automatic lane-geometry generation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import osmnx as ox
from shapely.geometry import LineString, Polygon

from osm_scenario.config import ConverterConfig
from osm_scenario.ids import deterministic_id
from osm_scenario.lane_model import (
    GenerationMetadata,
    LaneBoundary,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
    RestrictionEffect,
    ReviewFinding,
    SignalAssociation,
)
from osm_scenario.osm_source import ONEWAY_VALUES, read_osm_snapshot

GENERATOR_VERSION = "stage2-foundation-v1"
LANE_MODEL_SCHEMA_VERSION = 1


class GenerationError(RuntimeError):
    """Raised when Stage 2 inputs are unsafe or generation cannot complete."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_checksum(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _positive_int(value: object) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _positive_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("meters", "").replace("meter", "").strip()
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) and result > 0 else None


def _speed_kph(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    factor = 1.609344 if "mph" in text else 1.0
    number = text.replace("km/h", "").replace("kph", "").replace("mph", "").strip()
    try:
        result = float(number) * factor
    except ValueError:
        return None
    return result if math.isfinite(result) and result > 0 else None


def _way_ids(data: dict[str, Any]) -> list[str]:
    value = data.get("osmid")
    if isinstance(value, (list, tuple, set)):
        return sorted(str(item) for item in value)
    return [str(value)] if value is not None else []


def _edge_geometry(graph: Any, u: Any, v: Any, data: dict[str, Any]) -> LineString:
    geometry = data.get("geometry")
    if geometry is None:
        geometry = LineString(
            [
                (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
                (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
            ]
        )
    if not isinstance(geometry, LineString) or geometry.is_empty or geometry.length <= 0:
        raise GenerationError(f"edge {u}->{v} has invalid line geometry")
    start = (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"]))
    if math.dist(geometry.coords[0], start) > math.dist(geometry.coords[-1], start):
        geometry = LineString(reversed(geometry.coords))
    return geometry


def _edge_direction(way: Any, u: str, v: str) -> str:
    pairs = list(zip(way.node_ids, way.node_ids[1:], strict=False))
    return "forward" if (u, v) in pairs else "backward"


def _directional_lane_count(tags: dict[str, str], direction: str) -> tuple[int, str, str]:
    explicit = _positive_int(tags.get(f"lanes:{direction}"))
    if explicit is not None:
        return explicit, "explicit_directional", "high"
    total = _positive_int(tags.get("lanes"))
    oneway = tags.get("oneway") in ONEWAY_VALUES or tags.get("junction") == "roundabout"
    if oneway and total is not None:
        return total, "explicit_total_oneway", "high"
    if total is not None:
        count = max(1, total // 2)
        confidence = "medium" if total % 2 == 0 else "low"
        return count, "inferred_from_total", confidence
    return 1, "default_single_lane", "low"


def _turn_permissions(tags: dict[str, str], direction: str, lane_index: int) -> list[str]:
    value = tags.get(f"turn:lanes:{direction}") or tags.get("turn:lanes")
    if not value:
        return []
    lanes = value.split("|")
    if lane_index >= len(lanes):
        return []
    return sorted(item.strip() for item in lanes[lane_index].split(";") if item.strip())


def _points(line: LineString) -> list[Point2D]:
    return [Point2D(x=float(x), y=float(y)) for x, y in line.coords]


def _polygon_points(polygon: Polygon) -> list[Point2D]:
    return [Point2D(x=float(x), y=float(y)) for x, y in polygon.exterior.coords]


def _finding(
    *,
    rule: str,
    severity: str,
    source_type: str,
    source_ids: list[str],
    affected_feature_ids: list[str],
    proposed_value: object,
    confidence: str,
    reason: str,
) -> ReviewFinding:
    evidence = {
        "rule": rule,
        "source_type": source_type,
        "source_ids": source_ids,
        "affected_feature_ids": affected_feature_ids,
        "proposed_value": proposed_value,
        "reason": reason,
    }
    return ReviewFinding(
        identifier=deterministic_id(
            "finding", rule, source_type, *source_ids, *affected_feature_ids
        ),
        rule=rule,
        severity=severity,
        source_type=source_type,
        source_ids=source_ids,
        affected_feature_ids=affected_feature_ids,
        proposed_value=proposed_value,
        confidence=confidence,
        reason=reason,
        evidence_checksum=_canonical_checksum(evidence),
    )


def _render_review_html(model: PreliminaryLaneModel) -> str:
    features = []
    for lane in model.lanes:
        polygon = [[point.x, point.y] for point in lane.polygon]
        centerline = [[point.x, point.y] for point in lane.centerline]
        features.extend(
            [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [polygon]},
                    "properties": {"id": lane.identifier, "kind": "lane"},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": centerline},
                    "properties": {"id": lane.identifier, "kind": "centerline"},
                },
            ]
        )
    payload = json.dumps({"type": "FeatureCollection", "features": features}).replace("</", "<\\/")
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Stage 2 map review</title>
<link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\">
<style>html,body,#map{{height:100%;margin:0}} .summary{{position:absolute;z-index:1000;top:12px;right:12px;background:white;padding:10px;border-radius:6px;font:14px sans-serif}}</style></head>
<body><div id=\"map\"></div><div class=\"summary\">Lanes: {len(model.lanes)}<br>Findings: {len(model.findings)}</div>
<script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script><script>
const data={payload}; const map=L.map('map',{{crs:L.CRS.Simple}}); const layer=L.geoJSON(data,{{style:f=>f.properties.kind==='lane'?{{color:'#277da1',weight:1,fillOpacity:.25}}:{{color:'#f94144',weight:2}}}}).addTo(map); map.fitBounds(layer.getBounds().pad(.05));
</script></body></html>"""


def generate_lane_model(*, workspace: Path, config: ConverterConfig) -> Path:
    """Generate deterministic preliminary lane geometry from Stage 1 artifacts."""
    workspace = workspace.resolve()
    manifest_path = workspace / "source" / "manifest.json"
    if not manifest_path.is_file():
        raise GenerationError("Stage 1 manifest is missing; run fetch first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stage_1b", {}).get("status") != "passed":
        raise GenerationError("Stage 1B has not passed")

    source_path = workspace / manifest["source"]["path"]
    graph_artifact = manifest["stage_1b"]["artifacts"]["projected_graphml"]
    graph_path = workspace / graph_artifact["path"]
    for label, path, expected in (
        ("source OSM", source_path, manifest["source"]["sha256"]),
        ("projected GraphML", graph_path, graph_artifact["sha256"]),
    ):
        if not path.is_file() or _sha256(path) != expected:
            raise GenerationError(f"{label} checksum does not match the Stage 1 manifest")

    graph = ox.load_graphml(graph_path)
    snapshot = read_osm_snapshot(source_path)
    config_payload = config.model_dump(mode="json")
    config_checksum = _canonical_checksum(config_payload)
    fingerprint = _canonical_checksum(
        {
            "generator_version": GENERATOR_VERSION,
            "schema_version": LANE_MODEL_SCHEMA_VERSION,
            "source_checksum": manifest["source"]["sha256"],
            "graph_checksum": graph_artifact["sha256"],
            "configuration_checksum": config_checksum,
        }
    )

    lanes: list[LaneFeature] = []
    findings: list[ReviewFinding] = []
    lanes_by_start: dict[str, list[str]] = {}
    lanes_by_end: dict[str, list[str]] = {}
    for u, v, key, data in sorted(
        graph.edges(keys=True, data=True), key=lambda item: tuple(map(str, item[:3]))
    ):
        way_ids = _way_ids(data)
        if not way_ids:
            continue
        way = snapshot.ways.get(way_ids[0])
        if way is None:
            raise GenerationError(f"projected edge references missing OSM way {way_ids[0]}")
        direction = _edge_direction(way, str(u), str(v))
        count, count_reason, count_confidence = _directional_lane_count(way.tags, direction)
        width_total = _positive_float(way.tags.get("width"))
        width = (
            width_total / max(_positive_int(way.tags.get("lanes")) or count, 1)
            if width_total
            else config.lane_width_defaults.vehicle
        )
        speed = _speed_kph(way.tags.get("maxspeed")) or config.speed_defaults_kph.get(
            way.tags.get("highway", ""), config.default_speed_kph
        )
        base = _edge_geometry(graph, u, v, data)
        created: list[str] = []
        side_sign = 1.0 if manifest["driving_side"] == "left" else -1.0
        for lane_index in range(count):
            offset = side_sign * (lane_index + 0.5) * width
            center = base.offset_curve(offset, join_style="mitre")
            if not isinstance(center, LineString) or center.is_empty:
                center = base
            polygon = center.buffer(width / 2, cap_style="flat", join_style="mitre")
            left = center.offset_curve(width / 2, join_style="mitre")
            right = center.offset_curve(-width / 2, join_style="mitre")
            lane_id = deterministic_id("lane", *way_ids, str(u), str(v), str(key), str(lane_index))
            lane = LaneFeature(
                identifier=lane_id,
                source_way_ids=way_ids,
                source_edge=[str(u), str(v), str(key)],
                lane_index=lane_index,
                lane_count=count,
                direction=direction,
                road_class=way.tags.get("highway", "unknown"),
                width_m=width,
                speed_limit_kph=speed,
                centerline=_points(center),
                polygon=_polygon_points(polygon),
                boundaries=[
                    LaneBoundary(
                        identifier=deterministic_id("boundary", lane_id, "left"),
                        side="left",
                        points=_points(left),
                    ),
                    LaneBoundary(
                        identifier=deterministic_id("boundary", lane_id, "right"),
                        side="right",
                        points=_points(right),
                    ),
                ],
                turn_permissions=_turn_permissions(way.tags, direction, lane_index),
            )
            lanes.append(lane)
            created.append(lane_id)
            lanes_by_start.setdefault(str(u), []).append(lane_id)
            lanes_by_end.setdefault(str(v), []).append(lane_id)
        for lane_index, _lane_id in enumerate(created):
            if side_sign > 0:
                lane_lookup_left = created[lane_index + 1] if lane_index + 1 < count else None
                lane_lookup_right = created[lane_index - 1] if lane_index > 0 else None
            else:
                lane_lookup_left = created[lane_index - 1] if lane_index > 0 else None
                lane_lookup_right = created[lane_index + 1] if lane_index + 1 < count else None
            lanes[-count + lane_index].left_neighbor = lane_lookup_left
            lanes[-count + lane_index].right_neighbor = lane_lookup_right
        if count_reason not in {"explicit_directional", "explicit_total_oneway"}:
            findings.append(
                _finding(
                    rule="lane_count_inference",
                    severity="blocker" if count_confidence == "low" else "warning",
                    source_type="way",
                    source_ids=way_ids,
                    affected_feature_ids=created,
                    proposed_value={"direction": direction, "lane_count": count},
                    confidence=count_confidence,
                    reason=count_reason,
                )
            )
        if width_total is None:
            findings.append(
                _finding(
                    rule="lane_width_default",
                    severity="warning",
                    source_type="way",
                    source_ids=way_ids,
                    affected_feature_ids=created,
                    proposed_value=width,
                    confidence="medium",
                    reason="no usable explicit OSM width",
                )
            )
        if _speed_kph(way.tags.get("maxspeed")) is None:
            findings.append(
                _finding(
                    rule="speed_default",
                    severity="warning",
                    source_type="way",
                    source_ids=way_ids,
                    affected_feature_ids=created,
                    proposed_value=speed,
                    confidence="medium",
                    reason="no usable explicit OSM maxspeed",
                )
            )

    lane_lookup = {lane.identifier: lane for lane in lanes}
    for node_id in sorted(set(lanes_by_start) | set(lanes_by_end)):
        incoming = lanes_by_end.get(node_id, [])
        outgoing = lanes_by_start.get(node_id, [])
        for lane_id in incoming:
            lane_lookup[lane_id].exit_lanes = sorted(
                candidate for candidate in outgoing if candidate != lane_id
            )
        for lane_id in outgoing:
            lane_lookup[lane_id].entry_lanes = sorted(
                candidate for candidate in incoming if candidate != lane_id
            )

    signals: list[SignalAssociation] = []
    for node in sorted(snapshot.nodes.values(), key=lambda item: item.identifier):
        if node.tags.get("highway") != "traffic_signals" and "traffic_signals" not in node.tags:
            continue
        associated = sorted(
            set(lanes_by_start.get(node.identifier, []) + lanes_by_end.get(node.identifier, []))
        )
        status = "mapped" if len(associated) == 1 else "review_required"
        association_id = deterministic_id("signal-association", node.identifier, *associated)
        signals.append(
            SignalAssociation(
                identifier=association_id,
                source_node_id=node.identifier,
                lane_ids=associated,
                status=status,
            )
        )
        if status == "review_required":
            findings.append(
                _finding(
                    rule="signal_lane_association",
                    severity="blocker",
                    source_type="node",
                    source_ids=[node.identifier],
                    affected_feature_ids=associated,
                    proposed_value=associated,
                    confidence="low",
                    reason="signal has zero or multiple candidate lanes",
                )
            )

    restrictions: list[RestrictionEffect] = []
    for relation in sorted(snapshot.relations.values(), key=lambda item: item.identifier):
        if relation.tags.get("type") != "restriction":
            continue
        roles: dict[str, list[str]] = {"from": [], "via": [], "to": []}
        for member in relation.members:
            if member.role in roles:
                roles[member.role].append(member.reference)
        restriction = RestrictionEffect(
            identifier=deterministic_id("restriction-effect", relation.identifier),
            source_relation_id=relation.identifier,
            restriction=relation.tags.get("restriction", "unknown"),
            from_way_ids=roles["from"],
            via_member_ids=roles["via"],
            to_way_ids=roles["to"],
        )
        restrictions.append(restriction)
        affected = sorted(
            lane.identifier
            for lane in lanes
            if set(lane.source_way_ids) & set(roles["from"] + roles["to"])
        )
        findings.append(
            _finding(
                rule="restriction_effect_review",
                severity="blocker",
                source_type="relation",
                source_ids=[relation.identifier],
                affected_feature_ids=affected,
                proposed_value=restriction.model_dump(mode="json"),
                confidence="low",
                reason="restriction enforcement is not implemented in the Stage 2 foundation",
            )
        )

    metadata = GenerationMetadata(
        generator_version=GENERATOR_VERSION,
        lane_model_schema_version=LANE_MODEL_SCHEMA_VERSION,
        source_checksum=manifest["source"]["sha256"],
        projected_graph_checksum=graph_artifact["sha256"],
        configuration_checksum=config_checksum,
        generation_fingerprint=fingerprint,
        coordinate_system_wkt=manifest["stage_1b"]["projection"]["local_crs_wkt"],
    )
    model = PreliminaryLaneModel(
        metadata=metadata,
        lanes=lanes,
        signals=signals,
        restrictions=restrictions,
        findings=sorted(findings, key=lambda item: item.identifier),
    )

    lane_model_dir = workspace / "lane-model"
    reports_dir = workspace / "reports"
    inspection_dir = workspace / "inspection"
    for directory in (lane_model_dir, reports_dir, inspection_dir):
        directory.mkdir(parents=True, exist_ok=True)
    model_path = lane_model_dir / "preliminary.json"
    report_path = reports_dir / "lane-model-generation.json"
    inspection_path = inspection_dir / "stage-2-map-review.html"
    model_path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    inspection_path.write_text(_render_review_html(model), encoding="utf-8")
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "generator_version": GENERATOR_VERSION,
        "lane_model_schema_version": LANE_MODEL_SCHEMA_VERSION,
        "generation_fingerprint": fingerprint,
        "input_checksums": {
            "source_osm": manifest["source"]["sha256"],
            "projected_graphml": graph_artifact["sha256"],
            "configuration": config_checksum,
        },
        "feature_counts": {
            "lanes": len(lanes),
            "connectors": 0,
            "signals": len(signals),
            "restrictions": len(restrictions),
            "findings": len(findings),
        },
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = {}
    for name, path in (
        ("preliminary_lane_model", model_path),
        ("generation_report", report_path),
        ("review_html", inspection_path),
    ):
        artifacts[name] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    manifest["stage_2"] = {
        "status": "passed",
        "generator_version": GENERATOR_VERSION,
        "lane_model_schema_version": LANE_MODEL_SCHEMA_VERSION,
        "generation_fingerprint": fingerprint,
        "input_checksums": report["input_checksums"],
        "artifacts": artifacts,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report_path
