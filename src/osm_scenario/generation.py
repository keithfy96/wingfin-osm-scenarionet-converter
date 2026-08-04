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
    ConnectorFeature,
    GenerationMetadata,
    LaneBoundary,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
    RestrictionEffect,
    ReviewFinding,
    SignalAssociation,
    StopLine,
)
from osm_scenario.osm_source import ONEWAY_VALUES, read_osm_snapshot
from osm_scenario.topology import (
    MovementCandidate,
    classify_movement,
    connector_curve,
    forbidden_by_node_restriction,
    movement_matches,
    restriction_roles,
    signed_turn_angle,
    via_way_resolution,
)

GENERATOR_VERSION = "direct-osm-stage2-v2"
LANE_MODEL_SCHEMA_VERSION = 2


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
    for connector in model.connectors:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[point.x, point.y] for point in connector.centerline],
                },
                "properties": {
                    "id": connector.identifier,
                    "kind": "connector",
                    "status": connector.status,
                    "movement": connector.movement,
                    "source": f"{connector.from_way_id} -> {connector.to_way_id}",
                },
            }
        )
    for stop_line in model.stop_lines:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[point.x, point.y] for point in stop_line.points],
                },
                "properties": {
                    "id": stop_line.identifier,
                    "kind": "stop_line",
                    "status": stop_line.status,
                    "source": stop_line.source_node_id,
                },
            }
        )
    payload = json.dumps({"type": "FeatureCollection", "features": features}).replace("</", "<\\/")
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Stage 2 map review</title>
<link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\">
<style>html,body,#map{{height:100%;margin:0}} .summary{{position:absolute;z-index:1000;top:12px;right:12px;background:white;padding:10px;border-radius:6px;font:14px sans-serif}}</style></head>
<body><div id=\"map\"></div><div class=\"summary\">Stage 2 read-only inspection<br>Lanes: {len(model.lanes)}<br>Connectors: {len(model.connectors)}<br>Signals: {len(model.signals)}<br>Stop lines: {len(model.stop_lines)}<br>Restrictions: {len(model.restrictions)}<br>Findings: {len(model.findings)}</div>
<script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script><script>
const data={payload}; const map=L.map('map',{{crs:L.CRS.Simple}}); const layer=L.geoJSON(data,{{style:f=>f.properties.kind==='lane'?{{color:'#277da1',weight:1,fillOpacity:.25}}:f.properties.kind==='stop_line'?{{color:'#f9c74f',weight:5}}:{{color:f.properties.status==='forbidden'?'#9b2226':f.properties.status==='review_required'?'#f8961e':'#43aa8b',weight:4,dashArray:f.properties.status==='review_required'?'6 4':null}},onEachFeature:(f,l)=>l.bindPopup('<b>'+f.properties.kind+'</b><br>'+f.properties.id+'<br>'+JSON.stringify(f.properties))}}).addTo(map); map.fitBounds(layer.getBounds().pad(.05));
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
    movement_candidates: list[MovementCandidate] = []
    for node_id in sorted(set(lanes_by_start) | set(lanes_by_end)):
        incoming = sorted(lanes_by_end.get(node_id, []))
        outgoing = sorted(lanes_by_start.get(node_id, []))
        for from_id in incoming:
            source = lane_lookup[from_id]
            source_line = LineString((point.x, point.y) for point in source.centerline)
            candidates_for_lane: list[MovementCandidate] = []
            for to_id in outgoing:
                if from_id == to_id:
                    continue
                target = lane_lookup[to_id]
                target_line = LineString((point.x, point.y) for point in target.centerline)
                angle = signed_turn_angle(source_line, target_line)
                movement = classify_movement(angle)
                if source.turn_permissions and not any(
                    movement_matches(permission, movement) for permission in source.turn_permissions
                ):
                    continue
                graph_node_id = next(key for key in graph.nodes if str(key) == node_id)
                curve = connector_curve(
                    source_line,
                    target_line,
                    (
                        float(graph.nodes[graph_node_id]["x"]),
                        float(graph.nodes[graph_node_id]["y"]),
                    ),
                )
                candidates_for_lane.append(
                    MovementCandidate(
                        junction_node_id=node_id,
                        from_lane_id=from_id,
                        to_lane_id=to_id,
                        from_way_id=source.source_way_ids[0],
                        to_way_id=target.source_way_ids[0],
                        movement=movement,
                        angle_degrees=angle,
                        centerline=curve,
                        ambiguous=False,
                    )
                )
            ambiguous = len(candidates_for_lane) > 1
            for candidate in candidates_for_lane:
                movement_candidates.append(
                    MovementCandidate(
                        **{
                            **candidate.__dict__,
                            "ambiguous": ambiguous or 30 <= abs(candidate.angle_degrees) <= 40,
                        }
                    )
                )

    relation_status: dict[str, tuple[str, set[int], str]] = {}
    forbidden_indexes: set[int] = set()
    for relation in sorted(snapshot.relations.values(), key=lambda item: item.identifier):
        if relation.tags.get("type") != "restriction":
            continue
        roles = restriction_roles(relation)
        via_way_ids = [value for kind, value in roles["via"] if kind == "way"]
        if via_way_ids:
            status, removed, reason = via_way_resolution(relation, movement_candidates)
        else:
            removed = {
                index
                for index, candidate in enumerate(movement_candidates)
                if forbidden_by_node_restriction(candidate, relation)
            }
            status = "enforced" if removed else "already_satisfied"
            reason = (
                "node-via restriction removed matching movement"
                if removed
                else "prohibited node-via movement was already absent"
            )
        relation_status[relation.identifier] = (status, removed, reason)
        forbidden_indexes.update(removed)

    connectors: list[ConnectorFeature] = []
    for index, candidate in enumerate(movement_candidates):
        connector_id = deterministic_id(
            "connector",
            candidate.junction_node_id,
            candidate.from_lane_id,
            candidate.to_lane_id,
        )
        width = min(
            lane_lookup[candidate.from_lane_id].width_m,
            lane_lookup[candidate.to_lane_id].width_m,
        )
        status = (
            "forbidden"
            if index in forbidden_indexes
            else "review_required"
            if candidate.ambiguous
            else "active"
        )
        connectors.append(
            ConnectorFeature(
                identifier=connector_id,
                junction_node_id=candidate.junction_node_id,
                from_lane_id=candidate.from_lane_id,
                to_lane_id=candidate.to_lane_id,
                from_way_id=candidate.from_way_id,
                to_way_id=candidate.to_way_id,
                movement=candidate.movement,
                turn_angle_degrees=round(candidate.angle_degrees, 3),
                status=status,
                centerline=_points(candidate.centerline),
                polygon=_polygon_points(
                    candidate.centerline.buffer(width / 2, cap_style="flat", join_style="mitre")
                ),
            )
        )
        if status == "active":
            lane_lookup[candidate.from_lane_id].exit_lanes.append(connector_id)
            lane_lookup[candidate.to_lane_id].entry_lanes.append(connector_id)
        elif status == "review_required":
            findings.append(
                _finding(
                    rule="ambiguous_connector",
                    severity="blocker",
                    source_type="node",
                    source_ids=[candidate.junction_node_id],
                    affected_feature_ids=[connector_id],
                    proposed_value={
                        "movement": candidate.movement,
                        "to_lane_id": candidate.to_lane_id,
                    },
                    confidence="low",
                    reason="movement has multiple or borderline geometric interpretations",
                )
            )

    signals: list[SignalAssociation] = []
    for node in sorted(snapshot.nodes.values(), key=lambda item: item.identifier):
        if node.tags.get("highway") != "traffic_signals" and "traffic_signals" not in node.tags:
            continue
        associated = sorted(set(lanes_by_end.get(node.identifier, [])))
        status = "mapped" if associated else "review_required"
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
                    reason="signal has no generated approaching lane",
                )
            )

    stop_lines: list[StopLine] = []
    for signal in signals:
        for lane_id in signal.lane_ids:
            lane = lane_lookup[lane_id]
            line = LineString((point.x, point.y) for point in lane.centerline)
            distance = max(0.0, line.length - 2.0)
            point = line.interpolate(distance)
            before = line.interpolate(max(0.0, distance - 0.25))
            dx, dy = point.x - before.x, point.y - before.y
            norm = math.hypot(dx, dy) or 1.0
            half = lane.width_m / 2
            endpoints = [
                Point2D(x=point.x - dy / norm * half, y=point.y + dx / norm * half),
                Point2D(x=point.x + dy / norm * half, y=point.y - dx / norm * half),
            ]
            stop_line_id = deterministic_id("stop-line", signal.source_node_id, lane_id)
            stop_lines.append(
                StopLine(
                    identifier=stop_line_id,
                    source_node_id=signal.source_node_id,
                    lane_ids=[lane_id],
                    points=endpoints,
                    source="inferred",
                    status="review_required",
                )
            )
            findings.append(
                _finding(
                    rule="inferred_stop_line",
                    severity="warning",
                    source_type="node",
                    source_ids=[signal.source_node_id],
                    affected_feature_ids=[stop_line_id, lane_id],
                    proposed_value={"distance_upstream_m": 2.0},
                    confidence="medium",
                    reason="no explicit stop-line geometry was available",
                )
            )

    restrictions: list[RestrictionEffect] = []
    for relation in sorted(snapshot.relations.values(), key=lambda item: item.identifier):
        if relation.tags.get("type") != "restriction":
            continue
        typed_roles = restriction_roles(relation)
        roles = {
            role: [reference for _kind, reference in members]
            for role, members in typed_roles.items()
        }
        status, removed, reason = relation_status[relation.identifier]
        forbidden_ids = [connectors[index].identifier for index in sorted(removed)]
        restriction = RestrictionEffect(
            identifier=deterministic_id("restriction-effect", relation.identifier),
            source_relation_id=relation.identifier,
            restriction=relation.tags.get("restriction", "unknown"),
            from_way_ids=roles["from"],
            via_member_ids=roles["via"],
            to_way_ids=roles["to"],
            status=status,
            forbidden_connector_ids=forbidden_ids,
            reason=reason,
        )
        restrictions.append(restriction)
        if status == "review_required":
            findings.append(
                _finding(
                    rule="restriction_effect_review",
                    severity="blocker",
                    source_type="relation",
                    source_ids=[relation.identifier],
                    affected_feature_ids=forbidden_ids,
                    proposed_value=restriction.model_dump(mode="json"),
                    confidence="low",
                    reason=reason,
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
        connectors=connectors,
        signals=signals,
        stop_lines=stop_lines,
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
            "connectors": len(connectors),
            "signals": len(signals),
            "stop_lines": len(stop_lines),
            "restrictions": len(restrictions),
            "active_connectors": sum(item.status == "active" for item in connectors),
            "forbidden_connectors": sum(item.status == "forbidden" for item in connectors),
            "review_required_connectors": sum(
                item.status == "review_required" for item in connectors
            ),
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
