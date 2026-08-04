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
from pyproj import Transformer
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
    movement_family,
    movement_matches,
    restriction_roles,
    signed_turn_angle,
    uturn_evidence_status,
    via_way_resolution,
)

GENERATOR_VERSION = "direct-osm-stage2-v4"
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


def _turn_permissions(
    tags: dict[str, str],
    direction: str,
    lane_index: int,
    lane_count: int,
    driving_side: str,
) -> list[str]:
    value = tags.get(f"turn:lanes:{direction}") or tags.get("turn:lanes")
    if not value:
        return []
    lanes = value.split("|")
    tag_index = lane_count - 1 - lane_index if driving_side == "left" else lane_index
    if tag_index >= len(lanes):
        return []
    return sorted(item.strip() for item in lanes[tag_index].split(";") if item.strip())


def _is_exact_reverse(source: LaneFeature, target: LaneFeature) -> bool:
    return (
        source.source_edge[0] == target.source_edge[1]
        and source.source_edge[1] == target.source_edge[0]
    )


def _mapped_lane_index(source: LaneFeature, target_count: int) -> int:
    if source.lane_count > 1 and target_count > 1:
        return round(source.lane_index * (target_count - 1) / (source.lane_count - 1))
    return min(source.lane_index, target_count - 1)


def _is_decision_node(
    *,
    non_reverse_group_count: int,
    adjacent_node_count: int,
    has_control_or_restriction: bool,
    explicit_reverse: bool,
) -> bool:
    return (
        non_reverse_group_count > 1
        or adjacent_node_count > 2
        or has_control_or_restriction
        or explicit_reverse
    )


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
    transformer = Transformer.from_crs(
        model.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )

    def coordinates(points: list[Point2D]) -> list[list[float]]:
        return [list(transformer.transform(point.x, point.y)) for point in points]

    features: list[dict[str, Any]] = []
    for lane in model.lanes:
        properties = {
            "id": lane.identifier,
            "source_way_ids": lane.source_way_ids,
            "source_edge": lane.source_edge,
            "lane_index": lane.lane_index,
            "lane_count": lane.lane_count,
            "turn_permissions": lane.turn_permissions,
        }
        features.extend(
            [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [coordinates(lane.polygon)],
                    },
                    "properties": {**properties, "kind": "lane_polygon"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates(lane.centerline),
                    },
                    "properties": {**properties, "kind": "lane_centerline"},
                },
            ]
        )
    for connector in model.connectors:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates(connector.centerline),
                },
                "properties": {
                    "id": connector.identifier,
                    "kind": "connector",
                    "status": connector.status,
                    "movement": connector.movement,
                    "source": f"{connector.from_way_id} -> {connector.to_way_id}",
                    "from_lane_id": connector.from_lane_id,
                    "to_lane_id": connector.to_lane_id,
                    "junction_node_id": connector.junction_node_id,
                    "turn_angle_degrees": connector.turn_angle_degrees,
                },
            }
        )
    for stop_line in model.stop_lines:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates(stop_line.points),
                },
                "properties": {
                    "id": stop_line.identifier,
                    "kind": "stop_line",
                    "status": stop_line.status,
                    "source": stop_line.source_node_id,
                },
            }
        )

    feature_ids = {feature["properties"]["id"] for feature in features}
    lanes_by_way: dict[str, list[str]] = {}
    for lane in model.lanes:
        for way_id in lane.source_way_ids:
            lanes_by_way.setdefault(way_id, []).append(lane.identifier)
    restrictions = {item.identifier: item for item in model.restrictions}
    findings = []
    for finding in model.findings:
        finding_data = finding.model_dump(mode="json")
        geometry_ids = {
            identifier for identifier in finding.affected_feature_ids if identifier in feature_ids
        }
        for identifier in finding.affected_feature_ids:
            restriction = restrictions.get(identifier)
            if restriction is None:
                continue
            geometry_ids.update(restriction.forbidden_connector_ids)
            for way_id in restriction.from_way_ids + restriction.to_way_ids:
                geometry_ids.update(lanes_by_way.get(way_id, []))
        finding_data["geometry_ids"] = sorted(geometry_ids)
        findings.append(finding_data)

    payload = json.dumps(
        {
            "features": {"type": "FeatureCollection", "features": features},
            "findings": findings,
            "summary": {
                "lanes": len(model.lanes),
                "connectors": len(model.connectors),
                "signals": len(model.signals),
                "stop_lines": len(model.stop_lines),
                "restrictions": len(model.restrictions),
                "findings": len(model.findings),
            },
        },
        separators=(",", ":"),
    ).replace("</", "<\\/")
    template = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 2 Review Audit</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0;font:14px system-ui,sans-serif;color:#202428}body{display:grid;grid-template-columns:minmax(330px,420px) 1fr;background:#f4f5f6}aside{padding:14px;overflow:auto;border-right:1px solid #c8cdd1;background:#fff}h1{font-size:20px;margin:0 0 5px}h2{font-size:14px;margin:14px 0 7px}.muted{color:#687078;font-size:12px}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:10px 0}.metric{padding:7px;background:#f1f3f5;border-radius:5px;text-align:center}.metric b{display:block;font-size:16px}.filters{display:grid;gap:7px}.filters input,.filters select{width:100%;padding:7px;border:1px solid #adb5bd;border-radius:4px;background:#fff}.queue{display:grid;gap:6px;margin-top:8px}.finding{border:1px solid #d6dadd;border-left:5px solid #e67700;border-radius:5px;padding:8px;background:#fff;cursor:pointer;text-align:left}.finding.blocker{border-left-color:#c92a2a}.finding:hover,.finding.active{background:#fff3bf}.finding strong{display:block}.finding small{display:block;color:#687078;margin-top:3px}.detail{padding:9px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:5px;overflow-wrap:anywhere}.detail table,.popup-table{border-collapse:collapse;width:100%}.detail td,.popup-table td{border-bottom:1px solid #e5e7e9;padding:4px;vertical-align:top;font-size:12px}.legend{display:grid;grid-template-columns:18px 1fr;gap:6px 8px;align-items:center}.swatch{height:5px}.lane{background:#277da1}.active-connector{background:#2b8a3e}.review-connector{background:#f08c00}.forbidden-connector{background:#c92a2a}.stop-line{background:#7048e8}.highlight{background:#ffec99}.queue-note{font-size:12px;color:#687078;margin:7px 0}#map{height:100%;min-height:520px}.leaflet-popup-content{max-height:300px;overflow:auto}@media(max-width:780px){body{grid-template-columns:1fr;grid-template-rows:minmax(360px,45vh) 1fr}aside{border-right:0;border-bottom:1px solid #c8cdd1}#map{min-height:55vh}}
</style></head><body><aside><h1>Stage 2 Review Audit</h1><div class="muted">Read-only visual explanation of preliminary generation findings. Decisions are recorded later in Stage 3.</div><div class="summary" id="summary"></div><h2>Review filters</h2><div class="filters"><input id="search" placeholder="Search rule, reason, source ID, or feature ID"><select id="rule"><option value="">All rules</option></select><select id="severity"><option value="">All severities</option><option value="blocker">Blocker</option><option value="warning">Warning</option></select></div><div class="queue-note" id="queue-note"></div><div class="queue" id="queue"></div><h2>Selected finding</h2><div class="detail" id="detail">Select a review item to focus its affected geometry.</div><h2>Legend</h2><div class="legend"><span class="swatch lane"></span><span>Lane centreline</span><span class="swatch active-connector"></span><span>Active connector</span><span class="swatch review-connector"></span><span>Review-required connector</span><span class="swatch forbidden-connector"></span><span>Forbidden connector</span><span class="swatch stop-line"></span><span>Inferred stop line</span><span class="swatch highlight"></span><span>Selected finding geometry</span></div></aside><main id="map"></main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const payload=__PAYLOAD__;const reviewPriority={ambiguous_connector:0,restriction_effect_review:1,signal_lane_association:2,lane_transition_count_mismatch:3,inferred_stop_line:4,lane_count_inference:5,lane_width_default:6,speed_default:7};payload.findings.sort((a,b)=>(reviewPriority[a.rule]??99)-(reviewPriority[b.rule]??99)||a.rule.localeCompare(b.rule)||a.identifier.localeCompare(b.identifier));const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const map=L.map('map',{preferCanvas:true});L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
const groups={lane_polygon:L.layerGroup(),lane_centerline:L.layerGroup(),active:L.layerGroup(),review_required:L.layerGroup(),forbidden:L.layerGroup(),stop_line:L.layerGroup()};const byId=new Map(),allLayers=[];let selected=[];
function styleFor(p){if(p.kind==='lane_polygon')return{color:'#74c0fc',weight:1,fillColor:'#74c0fc',fillOpacity:.08};if(p.kind==='lane_centerline')return{color:'#277da1',weight:2,opacity:.75};if(p.kind==='stop_line')return{color:'#7048e8',weight:6};return{color:p.status==='forbidden'?'#c92a2a':p.status==='review_required'?'#f08c00':'#2b8a3e',weight:p.status==='review_required'?5:3,dashArray:p.status==='review_required'?'7 5':null,opacity:.9}}
function popup(p){return `<strong>${esc(p.kind)}</strong><table class="popup-table">${Object.entries(p).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table>`}
for(const feature of payload.features.features){const p=feature.properties;const layer=L.geoJSON(feature,{style:()=>styleFor(p),onEachFeature:(_f,l)=>l.bindPopup(popup(p))});layer.eachLayer(l=>{l._baseStyle=styleFor(p);allLayers.push(l);if(!byId.has(p.id))byId.set(p.id,[]);byId.get(p.id).push(l)});const key=p.kind==='connector'?p.status:p.kind;groups[key].addLayer(layer)}
groups.lane_centerline.addTo(map);groups.active.addTo(map);groups.review_required.addTo(map);groups.forbidden.addTo(map);groups.stop_line.addTo(map);L.control.layers(null,{'Lane polygons':groups.lane_polygon,'Lane centrelines':groups.lane_centerline,'Active connectors':groups.active,'Review-required connectors':groups.review_required,'Forbidden connectors':groups.forbidden,'Stop lines':groups.stop_line},{collapsed:false}).addTo(map);
const allGeometry=L.featureGroup(allLayers);if(allGeometry.getBounds().isValid())map.fitBounds(allGeometry.getBounds().pad(.04));
document.getElementById('summary').innerHTML=Object.entries(payload.summary).map(([k,v])=>`<div class="metric"><b>${v}</b>${esc(k.replaceAll('_',' '))}</div>`).join('');const rules=[...new Set(payload.findings.map(f=>f.rule))].sort();document.getElementById('rule').innerHTML+=[...rules].map(r=>`<option value="${esc(r)}">${esc(r)}</option>`).join('');
function clearSelection(){for(const l of selected)if(l.setStyle)l.setStyle(l._baseStyle);selected=[];document.querySelectorAll('.finding.active').forEach(x=>x.classList.remove('active'))}
function showFinding(id,button){clearSelection();const f=payload.findings.find(x=>x.identifier===id);button?.classList.add('active');const layers=f.geometry_ids.flatMap(x=>byId.get(x)||[]);for(const l of layers){if(l.setStyle)l.setStyle({color:'#ffd43b',weight:8,fillOpacity:.4,opacity:1});selected.push(l)}const bounds=L.featureGroup(layers).getBounds();if(bounds.isValid())map.fitBounds(bounds.pad(.35),{maxZoom:19});const rows=[['Rule',f.rule],['Severity',f.severity],['Confidence',f.confidence],['Reason',f.reason],['Source',`${f.source_type}: ${f.source_ids.join(', ')}`],['Affected IDs',f.affected_feature_ids.join(', ')||'none'],['Mapped geometry',f.geometry_ids.length],['Proposed value',JSON.stringify(f.proposed_value)],['Finding ID',f.identifier]];document.getElementById('detail').innerHTML=`<table>${rows.map(([k,v])=>`<tr><td><strong>${esc(k)}</strong></td><td>${esc(v)}</td></tr>`).join('')}</table>`+(layers.length?'':'<p class="muted">No generated geometry could be mapped for this finding.</p>')}
function renderQueue(){const q=document.getElementById('search').value.trim().toLowerCase(),rule=document.getElementById('rule').value,severity=document.getElementById('severity').value;const matches=payload.findings.filter(f=>(!rule||f.rule===rule)&&(!severity||f.severity===severity)&&(!q||JSON.stringify(f).toLowerCase().includes(q)));const shown=matches.slice(0,250);document.getElementById('queue-note').textContent=`${matches.length} matching findings${matches.length>shown.length?`; showing first ${shown.length}`:''}`;const queue=document.getElementById('queue');queue.innerHTML='';for(const f of shown){const b=document.createElement('button');b.className=`finding ${f.severity}`;b.innerHTML=`<strong>${esc(f.rule)}</strong><span>${esc(f.reason)}</span><small>${esc(f.source_type)} ${esc(f.source_ids.join(', '))} · ${f.geometry_ids.length} mapped feature(s)</small>`;b.onclick=()=>showFinding(f.identifier,b);queue.appendChild(b)}}
for(const id of ['search','rule','severity'])document.getElementById(id).addEventListener(id==='search'?'input':'change',renderQueue);renderQueue();
</script></body></html>"""
    return template.replace("__PAYLOAD__", payload)


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
                turn_permissions=_turn_permissions(
                    way.tags,
                    direction,
                    lane_index,
                    count,
                    manifest["driving_side"],
                ),
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
    direct_continuations = 0
    lane_mismatch_findings: set[tuple[str, str, str]] = set()
    restriction_nodes = {
        value
        for relation in snapshot.relations.values()
        if relation.tags.get("type") == "restriction"
        for kind, value in restriction_roles(relation)["via"]
        if kind == "node"
    }
    for node_id in sorted(set(lanes_by_start) | set(lanes_by_end)):
        incoming = sorted(lanes_by_end.get(node_id, []))
        outgoing = sorted(lanes_by_start.get(node_id, []))
        graph_node_id = next(key for key in graph.nodes if str(key) == node_id)
        adjacent_nodes = {str(neighbor) for neighbor in graph.predecessors(graph_node_id)} | {
            str(neighbor) for neighbor in graph.successors(graph_node_id)
        }
        node = snapshot.nodes.get(node_id)
        controlled_node = bool(
            node
            and (
                node.tags.get("highway") in {"traffic_signals", "stop", "give_way"}
                or node.tags.get("junction") is not None
            )
        )
        for from_id in incoming:
            source = lane_lookup[from_id]
            source_line = LineString((point.x, point.y) for point in source.centerline)
            candidates_for_lane: list[MovementCandidate] = []
            outgoing_groups: dict[tuple[str, tuple[str, ...]], list[LaneFeature]] = {}
            for to_id in outgoing:
                target = lane_lookup[to_id]
                group_key = (target.source_way_ids[0], tuple(target.source_edge))
                outgoing_groups.setdefault(group_key, []).append(target)
            non_reverse_groups = [
                targets
                for targets in outgoing_groups.values()
                if not _is_exact_reverse(source, targets[0])
            ]
            uturn_status = uturn_evidence_status(source.turn_permissions)
            explicit_reverse = uturn_status == "active"
            decision_node = _is_decision_node(
                non_reverse_group_count=len(non_reverse_groups),
                adjacent_node_count=len(adjacent_nodes),
                has_control_or_restriction=node_id in restriction_nodes or controlled_node,
                explicit_reverse=explicit_reverse,
            )
            for targets in outgoing_groups.values():
                targets.sort(key=lambda item: (item.lane_index, item.identifier))
                exact_reverse = _is_exact_reverse(source, targets[0])
                if exact_reverse and not decision_node:
                    continue
                target_index = _mapped_lane_index(source, len(targets))
                target = targets[target_index]
                if source.lane_count != len(targets):
                    mismatch_key = (
                        node_id,
                        source.source_way_ids[0],
                        target.source_way_ids[0],
                    )
                    if mismatch_key not in lane_mismatch_findings:
                        lane_mismatch_findings.add(mismatch_key)
                        findings.append(
                            _finding(
                                rule="lane_transition_count_mismatch",
                                severity="warning",
                                source_type="node",
                                source_ids=[node_id],
                                affected_feature_ids=[
                                    lane.identifier for lane in [source, *targets]
                                ],
                                proposed_value={
                                    "incoming_lane_count": source.lane_count,
                                    "outgoing_lane_count": len(targets),
                                },
                                confidence="medium",
                                reason="proportional lane-order mapping crosses a lane-count change",
                            )
                        )
                if not exact_reverse and (
                    source.source_way_ids[0] == target.source_way_ids[0] or not decision_node
                ):
                    source.exit_lanes.append(target.identifier)
                    target.entry_lanes.append(source.identifier)
                    direct_continuations += 1
                    continue
                target_line = LineString((point.x, point.y) for point in target.centerline)
                angle = signed_turn_angle(source_line, target_line)
                movement = classify_movement(angle)
                if movement == "reverse":
                    if uturn_status == "excluded":
                        continue
                    if source.lane_index != 0 and not explicit_reverse:
                        continue
                elif source.turn_permissions and not any(
                    movement_matches(permission, movement) for permission in source.turn_permissions
                ):
                    continue
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
                        to_lane_id=target.identifier,
                        from_way_id=source.source_way_ids[0],
                        to_way_id=target.source_way_ids[0],
                        movement=movement,
                        angle_degrees=angle,
                        centerline=curve,
                        ambiguous=False,
                    )
                )
            family_counts: dict[str, int] = {}
            for candidate in candidates_for_lane:
                family = movement_family(candidate.movement)
                family_counts[family] = family_counts.get(family, 0) + 1
            for candidate in candidates_for_lane:
                movement_candidates.append(
                    MovementCandidate(
                        **{
                            **candidate.__dict__,
                            "ambiguous": (
                                candidate.movement == "reverse"
                                and uturn_status == "review_required"
                            )
                            or family_counts[movement_family(candidate.movement)] > 1
                            or 30 <= abs(candidate.angle_degrees) <= 40,
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
    review_audit_path = inspection_dir / "stage-2-review-audit.html"
    model_path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    review_html = _render_review_html(model)
    inspection_path.write_text(review_html, encoding="utf-8")
    review_audit_path.write_text(review_html, encoding="utf-8")
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
            "direct_continuations": direct_continuations,
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
        ("review_audit_html", review_audit_path),
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
