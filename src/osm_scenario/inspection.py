# ruff: noqa: E501
"""Generate browser-based visual checkpoints for conversion workspaces."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import osmnx as ox
from shapely.geometry import LineString, Point, mapping

from osm_scenario.osm_source import read_osm_snapshot, road_exclusion_reason

InspectionView = Literal["source", "normalized", "audit", "stage-1", "lanelet2"]


class InspectionError(RuntimeError):
    """Raised when a requested visual checkpoint cannot be produced."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _feature(geometry: Any, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": mapping(geometry), "properties": properties}


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _source_layers(source_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    snapshot = read_osm_snapshot(source_path)
    selected = []
    excluded = []
    signals = []

    for way in snapshot.ways.values():
        if "highway" not in way.tags:
            continue
        coordinates = [
            (snapshot.nodes[node_id].longitude, snapshot.nodes[node_id].latitude)
            for node_id in way.node_ids
            if node_id in snapshot.nodes
        ]
        if len(coordinates) < 2:
            continue
        reason = road_exclusion_reason(way.tags)
        properties = {
            "osm_id": way.identifier,
            "feature_type": "way",
            "status": "included" if reason is None else "excluded",
            "exclusion_reason": reason,
            "tags": way.tags,
        }
        target = selected if reason is None else excluded
        target.append(_feature(LineString(coordinates), properties))

    for node in snapshot.nodes.values():
        if node.tags.get("highway") != "traffic_signals" and "traffic_signals" not in node.tags:
            continue
        signals.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [node.longitude, node.latitude],
                },
                "properties": {
                    "osm_id": node.identifier,
                    "feature_type": "node",
                    "tags": node.tags,
                },
            }
        )
    return (
        _feature_collection(selected),
        _feature_collection(excluded),
        _feature_collection(signals),
    )


def _graph_edge_layers(
    graph_path: Path, warning_by_osmid: dict[str, set[str]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = ox.load_graphml(graph_path)
    edges = ox.graph_to_gdfs(graph, nodes=False, edges=True, fill_edge_geometry=True)
    if edges.crs is None:
        raise InspectionError(f"graph has no CRS: {graph_path}")
    edges = edges.to_crs("EPSG:4326")
    directed = []
    warnings = []
    for (u, v, key), row in edges.iterrows():
        osmids = row.get("osmid")
        osmids = osmids if isinstance(osmids, list) else [osmids]
        identifiers = [str(value) for value in osmids if value is not None]
        properties = {
            "osm_id": ",".join(identifiers),
            "from_node": str(u),
            "to_node": str(v),
            "edge_key": str(key),
            "oneway": str(row.get("oneway", "")),
            "highway": row.get("highway"),
        }
        directed.append(_feature(row.geometry, properties))
        codes = sorted({code for osm_id in identifiers for code in warning_by_osmid.get(osm_id, set())})
        if codes:
            warnings.append(_feature(row.geometry, {**properties, "warning_codes": codes}))
    return _feature_collection(directed), _feature_collection(warnings)


def _json_for_script(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).replace("<", "\\u003c")


def _way_feature(snapshot: Any, way_id: str, properties: dict[str, Any]) -> dict[str, Any] | None:
    way = snapshot.ways.get(str(way_id))
    if way is None:
        return None
    coordinates = [
        (snapshot.nodes[node_id].longitude, snapshot.nodes[node_id].latitude)
        for node_id in way.node_ids
        if node_id in snapshot.nodes
    ]
    if len(coordinates) < 2:
        return None
    return _feature(
        LineString(coordinates),
        {"osm_id": way.identifier, "feature_type": "way", "tags": way.tags, **properties},
    )


def _audit_map_data(source_path: Path, audit: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    snapshot = read_osm_snapshot(source_path)
    selected_ids = {
        str(way_id)
        for component in audit["connectivity"]["components"]
        for way_id in component["osm_way_ids"]
    }

    def ways(ids: Any, **properties: Any) -> dict[str, Any]:
        features = [
            feature
            for way_id in ids
            if (feature := _way_feature(snapshot, str(way_id), properties)) is not None
        ]
        return _feature_collection(features)

    component_features = []
    for component in audit["connectivity"]["components"]:
        for way_id in component["osm_way_ids"]:
            feature = _way_feature(
                snapshot,
                way_id,
                {
                    "audit_issue": "connectivity_component",
                    "component_id": component["component_id"],
                    "component_nodes": component["node_count"],
                    "is_primary_component": component["is_primary"],
                },
            )
            if feature is not None:
                component_features.append(feature)

    signal_features = []
    for signal in audit["traffic_signals"]["details"]:
        node = snapshot.nodes.get(signal["osm_node_id"])
        if node is None:
            continue
        signal_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [node.longitude, node.latitude]},
                "properties": {
                    "osm_id": node.identifier,
                    "feature_type": "node",
                    "audit_issue": "traffic_signal",
                    "retained_in_driving_graph": signal["retained_in_driving_graph"],
                    "component_id": signal["component_id"],
                    "tags": node.tags,
                },
            }
        )

    restriction_layers = {"retained_restrictions": [], "partial_restrictions": []}
    restriction_via_features = []
    for restriction in audit["turn_restrictions"]["details"]:
        layer = (
            "retained_restrictions"
            if restriction["fully_retained_way_members"]
            else "partial_restrictions"
        )
        for member in restriction["members"]:
            if member["type"] != "way":
                continue
            feature = _way_feature(
                snapshot,
                member["ref"],
                {
                    "audit_issue": "turn_restriction",
                    "relation_id": restriction["osm_relation_id"],
                    "restriction": restriction["restriction"],
                    "member_role": member["role"],
                    "fully_retained_way_members": restriction["fully_retained_way_members"],
                },
            )
            if feature is not None:
                restriction_layers[layer].append(feature)
            if member["role"] != "via":
                continue
            via_way = snapshot.ways.get(member["ref"])
            if via_way is None:
                continue
            coordinates = [
                (snapshot.nodes[node_id].longitude, snapshot.nodes[node_id].latitude)
                for node_id in via_way.node_ids
                if node_id in snapshot.nodes
            ]
            if len(coordinates) >= 2:
                via_geometry = LineString(coordinates).interpolate(0.5, normalized=True)
                restriction_via_features.append(
                    _feature(
                        via_geometry,
                        {
                            "osm_id": member["ref"],
                            "feature_type": "way",
                            "audit_issue": "turn_restriction_via",
                            "relation_id": restriction["osm_relation_id"],
                            "restriction": restriction["restriction"],
                            "via_member_type": "way",
                            "marker_location": "representative midpoint of via way",
                            "fully_retained_way_members": restriction[
                                "fully_retained_way_members"
                            ],
                            "tags": via_way.tags,
                        },
                    )
                )
        for member in restriction["members"]:
            if member["type"] != "node" or member["role"] != "via":
                continue
            via_node = snapshot.nodes.get(member["ref"])
            if via_node is None:
                continue
            restriction_via_features.append(
                _feature(
                    Point(via_node.longitude, via_node.latitude),
                    {
                        "osm_id": member["ref"],
                        "feature_type": "node",
                        "audit_issue": "turn_restriction_via",
                        "relation_id": restriction["osm_relation_id"],
                        "restriction": restriction["restriction"],
                        "via_member_type": "node",
                        "marker_location": "exact OSM via node",
                        "fully_retained_way_members": restriction[
                            "fully_retained_way_members"
                        ],
                        "tags": via_node.tags,
                    },
                )
            )

    conflict_ids = {
        part.strip()
        for warning in report["preflight"]["warnings"]
        if warning["code"] == "conflicting_direction_tags" and warning.get("osm_id")
        for part in str(warning["osm_id"]).split(",")
    }
    searchable_way_features = []
    for way_id, way in snapshot.ways.items():
        feature = _way_feature(
            snapshot,
            way_id,
            {
                "audit_issue": "way_search",
                "selected_for_driving": way_id in selected_ids,
                "selection_reason": (
                    "selected"
                    if way_id in selected_ids
                    else road_exclusion_reason(way.tags)
                    if "highway" in way.tags
                    else "not_a_highway_way"
                ),
            },
        )
        if feature is not None:
            searchable_way_features.append(feature)
    return {
        "selected": ways(selected_ids, audit_issue="selected_road"),
        "missing_lanes": ways(
            (item["osm_way_id"] for item in audit["lane_count_coverage"]["details"]),
            audit_issue="missing_lane_count",
        ),
        "missing_widths": ways(
            audit["width_coverage"]["missing_way_ids"], audit_issue="missing_width"
        ),
        "components": _feature_collection(component_features),
        "signals": _feature_collection(signal_features),
        "retained_restrictions": _feature_collection(restriction_layers["retained_restrictions"]),
        "partial_restrictions": _feature_collection(restriction_layers["partial_restrictions"]),
        "restriction_via_points": _feature_collection(restriction_via_features),
        "stop_lines": ways(
            audit["stop_line_geometry"]["candidate_way_ids"], audit_issue="stop_line_candidate"
        ),
        "direction_conflicts": ways(conflict_ids, audit_issue="conflicting_direction_tags"),
        "searchable_ways": _feature_collection(searchable_way_features),
    }


def _render_audit_html(*, data: dict[str, Any], audit: dict[str, Any]) -> str:
    payload = _json_for_script(data)
    summary_json = _json_for_script(audit)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 1B Data Audit</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{{height:100%;margin:0}} body{{font-family:system-ui,sans-serif;color:#17212b}} #panel{{position:absolute;z-index:1000;top:12px;left:54px;width:min(440px,calc(100vw - 118px));max-height:calc(100vh - 48px);overflow:auto;background:#fff;border:1px solid #9aa7b2;border-radius:6px;padding:14px;box-shadow:0 3px 14px #0003}} h1{{font-size:17px;margin:0 0 8px}} h2{{font-size:14px;margin:14px 0 6px}} p,td,th{{font-size:12px;line-height:1.35}} table{{border-collapse:collapse;width:100%}} td,th{{text-align:left;border-bottom:1px solid #d6dde3;padding:5px 4px;vertical-align:top}} .review{{color:#9a5700;font-weight:700}} .mapped{{color:#176b3a}} .manual{{color:#855900}} .way-search{{display:grid;grid-template-columns:1fr auto;gap:6px;margin:10px 0 2px}} .way-search label{{grid-column:1/-1;font-size:12px;font-weight:700}} .way-search input{{min-width:0;border:1px solid #8795a1;border-radius:4px;padding:7px 8px;font:inherit}} .way-search button{{border:1px solid #285c79;border-radius:4px;background:#285c79;color:#fff;padding:7px 12px;font-weight:700;cursor:pointer}} .way-search button:hover{{background:#17445f}} #way-search-result{{min-height:17px;margin:4px 0 10px}} .leaflet-popup-content{{max-height:320px;overflow:auto}} .tag-table{{font-size:12px}} @media(max-width:700px){{#panel{{left:10px;top:54px;width:calc(100vw - 48px);max-height:44vh}}}}
</style></head><body><aside id="panel"><h1>Stage 1B Data Audit</h1><form id="way-search" class="way-search"><label for="way-id">Search by OSM Way ID</label><input id="way-id" name="way-id" inputmode="numeric" autocomplete="off" placeholder="e.g. 1250683198"><button type="submit">Search</button></form><p id="way-search-result" role="status" aria-live="polite"></p><div id="summary"></div><h2>Source correction coverage</h2><table><thead><tr><th>Case</th><th>Coverage</th></tr></thead><tbody id="coverage"></tbody></table><p>Toggle layers at top right and click geometry for OSM IDs and source tags.</p></aside><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={payload}; const audit={summary_json}; const map=L.map('map',{{preferCanvas:true}}); L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:20,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function popup(f){{const p=f.properties||{{}},tags=p.tags||{{}};const facts=Object.entries(p).filter(([k])=>!['tags'].includes(k)).map(([k,v])=>`<tr><td>${{esc(k)}}</td><td>${{esc(v)}}</td></tr>`).join('');const tagRows=Object.keys(tags).sort().map(k=>`<tr><td>${{esc(k)}}</td><td>${{esc(tags[k])}}</td></tr>`).join('');return `<strong>OSM ${{esc(p.feature_type)}} ${{esc(p.osm_id)}}</strong><table class="tag-table">${{facts}}${{tagRows}}</table>`}}
const line=(geo,style)=>L.geoJSON(geo,{{style,onEachFeature:(f,l)=>l.bindPopup(popup(f))}}); const selected=line(data.selected,{{color:'#6b747c',weight:3,opacity:.38}}); const missingLanes=line(data.missing_lanes,{{color:'#d73027',weight:6,opacity:.9}}); const missingWidths=line(data.missing_widths,{{color:'#f08c00',weight:4,opacity:.85,dashArray:'7 5'}}); const palette=['#0077b6','#6a4c93','#2a9d8f','#bc6c25','#c2185b','#5f6f00','#8c564b','#17a2b8']; const components=L.geoJSON(data.components,{{style:f=>{{const n=Math.max(0,Number((f.properties.component_id||'1').split('-')[1])-1);return{{color:palette[n%palette.length],weight:5,opacity:.8}}}},onEachFeature:(f,l)=>l.bindPopup(popup(f))}}); const retainedSignals=L.geoJSON(data.signals,{{filter:f=>f.properties.retained_in_driving_graph,pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:7,color:'#176b3a',weight:3,fillColor:'#fff',fillOpacity:1}}),onEachFeature:(f,l)=>l.bindPopup(popup(f))}}); const excludedSignals=L.geoJSON(data.signals,{{filter:f=>!f.properties.retained_in_driving_graph,pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:7,color:'#a51d2d',weight:3,fillColor:'#fff',fillOpacity:1}}),onEachFeature:(f,l)=>l.bindPopup(popup(f))}}); const retainedRestrictions=line(data.retained_restrictions,{{color:'#6a1b9a',weight:6,opacity:.9}}); const partialRestrictions=line(data.partial_restrictions,{{color:'#00a6a6',weight:9,opacity:1,dashArray:'10 7'}}); const restrictionViaPoints=L.geoJSON(data.restriction_via_points,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:9,color:'#111',weight:3,fillColor:'#ffe600',fillOpacity:1}}),onEachFeature:(f,l)=>l.bindPopup(popup(f))}}); const stopLines=line(data.stop_lines,{{color:'#111',weight:8,opacity:.95}}); const directionConflicts=line(data.direction_conflicts,{{color:'#e6007e',weight:8,opacity:.9}}); const overlays={{'Fully retained restrictions (purple, solid)':retainedRestrictions,'Partial restrictions (cyan, dashed)':partialRestrictions,'Restriction via points (yellow markers)':restrictionViaPoints,'Selected roads':selected,'Missing lane counts':missingLanes,'Missing widths':missingWidths,'Connected components':components,'Retained traffic signals':retainedSignals,'Excluded traffic signals':excludedSignals,'Stop-line candidates':stopLines,'Direction tag conflicts':directionConflicts}}; selected.addTo(map); missingLanes.addTo(map); retainedSignals.addTo(map); excludedSignals.addTo(map); partialRestrictions.addTo(map); restrictionViaPoints.addTo(map); L.control.layers(null,overlays,{{collapsed:innerWidth<800}}).addTo(map); const bounds=selected.getBounds(); if(bounds.isValid())map.fitBounds(bounds.pad(.05));else map.setView([0,0],2);
const searchableWays=new Map(data.searchable_ways.features.map(feature=>[String(feature.properties.osm_id),feature])); const missingRestrictionWays=new Set(audit.turn_restrictions.details.flatMap(relation=>relation.missing_way_member_ids)); let searchHighlight=null; const searchResult=document.getElementById('way-search-result'); document.getElementById('way-search').addEventListener('submit',event=>{{event.preventDefault();const wayId=document.getElementById('way-id').value.trim();if(searchHighlight){{map.removeLayer(searchHighlight);searchHighlight=null}}if(!/^\\d+$/.test(wayId)){{searchResult.textContent='Enter a numeric OSM Way ID.';return}}const feature=searchableWays.get(wayId);if(!feature){{searchResult.textContent=missingRestrictionWays.has(wayId)?`Way ${{wayId}} is referenced by a restriction but is missing from source/map.osm.`:`Way ${{wayId}} is not present with drawable geometry in source/map.osm.`;return}}const outline=L.geoJSON(feature,{{style:{{color:'#111',weight:12,opacity:1}}}});const focus=L.geoJSON(feature,{{style:{{color:'#ffe600',weight:7,opacity:1}},onEachFeature:(f,l)=>l.bindPopup(popup(f))}});searchHighlight=L.layerGroup([outline,focus]).addTo(map);const focusBounds=focus.getBounds();if(focusBounds.isValid())map.fitBounds(focusBounds.pad(.35),{{maxZoom:18}});focus.eachLayer(layer=>layer.openPopup());const p=feature.properties,tags=p.tags||{{}};searchResult.textContent=`Found Way ${{wayId}}: ${{tags.name||tags.highway||'unnamed way'}} (${{p.selection_reason}}). Highlighted yellow.`}});
const lanes=audit.lane_count_coverage,widths=audit.width_coverage,signals=audit.traffic_signals,restrictions=audit.turn_restrictions,stops=audit.stop_line_geometry,connectivity=audit.connectivity; document.getElementById('summary').innerHTML=`<table><tr><th>Stage 1B</th><td>${{esc(audit.stage_1b_status)}}</td></tr><tr><th>Downstream readiness</th><td class="review">${{esc(audit.downstream_readiness)}}</td></tr><tr><th>Selected ways / edges</th><td>${{audit.selected_network.unique_osm_way_count}} / ${{audit.selected_network.directed_edge_count}}</td></tr><tr><th>Missing lanes by class</th><td>${{esc(JSON.stringify(lanes.missing_by_highway))}}</td></tr><tr><th>Components</th><td>${{connectivity.component_count}}</td></tr><tr><th>Missing widths / default</th><td>${{widths.missing_way_count}} / ${{widths.configured_default_lane_width_metres}} m</td></tr><tr><th>Signals retained / excluded</th><td>${{signals.retained_count}} / ${{signals.excluded_count}}</td></tr><tr><th>Restrictions full / partial</th><td>${{restrictions.fully_retained_way_member_count}} / ${{restrictions.partially_or_not_retained_count}}</td></tr><tr><th>Stop-line candidates</th><td>${{stops.candidate_way_count}}</td></tr><tr><th>Lane inference enabled</th><td>${{lanes.inference_enabled}}</td></tr></table>`;
const rows=[['Known lane count','mapped',`${{lanes.missing_way_count}} missing; red layer`],['Incorrect oneway','mapped','conflicting directional tags mapped; correctness still needs manual review'],['Roads connected at grade','manual','components are colored; missing joins require source review'],['Roads crossing visually','manual','verify bridge, tunnel, and layer tags before joining'],['Turn restrictions','mapped',`${{restrictions.source_count}} relations; solid full, dashed partial`],['Signal location','mapped','retained green; excluded red; placement correctness is manual'],['Known physical width','mapped',`${{widths.missing_way_count}} missing; orange dashed layer`],['Known mapped stop line','mapped',`${{stops.candidate_way_count}} candidates; black layer`]]; document.getElementById('coverage').innerHTML=rows.map(([name,state,text])=>`<tr><td>${{esc(name)}}</td><td class="${{state}}">${{esc(text)}}</td></tr>`).join('');
</script></body></html>"""


def _render_html(*, title: str, data: dict[str, Any], summary: dict[str, Any]) -> str:
    payload = _json_for_script(data)
    summary_json = _json_for_script(summary)
    legend_items = {
        "selected": ('#178a45', "Selected public driving road"),
        "excluded": ('#7f8b94', "Excluded source highway"),
        "warnings": ('#d64933', "Preflight warning"),
        "projected": ('#0077b6', "Stage 1B projected geometry"),
    }
    legend_html = "\n".join(
        f'      <p><span class="swatch" style="background:{color}"></span>{label}</p>'
        for name, (color, label) in legend_items.items()
        if summary["visible_layers"].get(name, False)
    )
    layer_labels = {
        "selected": "Selected source roads",
        "excluded": "Excluded source highways",
        "projected": "Stage 1B projected overlay",
        "warnings": "Preflight warnings",
        "directions": "Directed graph edges",
        "signals": "Traffic signals",
    }
    layer_definitions = ",\n".join(
        f"      ['{name}', '{layer_labels[name]}', {name}]"
        for name in layer_labels
        if summary["visible_layers"].get(name, False)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: system-ui, sans-serif; color: #17212b; }}
    #panel {{ position: absolute; z-index: 1000; top: 12px; left: 54px; width: min(340px, calc(100vw - 118px)); max-height: calc(100vh - 48px); overflow: auto; background: #fff; border: 1px solid #9aa7b2; border-radius: 6px; padding: 14px; box-shadow: 0 3px 14px #0003; }}
    h1 {{ font-size: 17px; margin: 0 0 8px; }}
    p, li, dt, dd {{ font-size: 13px; line-height: 1.4; }}
    dl {{ display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; margin: 10px 0; }}
    dt, dd {{ margin: 0; }}
    .status-passed {{ color: #176b3a; font-weight: 700; }}
    .status-failed {{ color: #a51d2d; font-weight: 700; }}
    .legend {{ border-top: 1px solid #d6dde3; padding-top: 9px; }}
    .swatch {{ display: inline-block; width: 22px; height: 4px; margin-right: 7px; vertical-align: middle; }}
    .leaflet-popup-content {{ max-height: 300px; overflow: auto; }}
    .tag-table {{ border-collapse: collapse; font-size: 12px; }}
    .tag-table td {{ border-bottom: 1px solid #e1e6ea; padding: 3px 6px 3px 0; vertical-align: top; }}
    @media (max-width: 700px) {{ #panel {{ left: 10px; top: 54px; width: calc(100vw - 48px); max-height: 42vh; }} }}
  </style>
</head>
<body>
  <aside id="panel"><h1>{title}</h1><div id="summary"></div>
    <div class="legend">
{legend_html}
      <p>Use the layer control at top right to isolate each transformation.</p>
    </div>
  </aside>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet-polylinedecorator@1.6.0/dist/leaflet.polylineDecorator.js"></script>
  <script>
    const data = {payload};
    const summary = {summary_json};
    const map = L.map('map', {{preferCanvas: true}});
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom: 20, attribution: '&copy; OpenStreetMap contributors'}}).addTo(map);
    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    function popup(feature) {{
      const p = feature.properties || {{}};
      const tags = p.tags || {{}};
      const rows = Object.keys(tags).sort().map(k => `<tr><td>${{escapeHtml(k)}}</td><td>${{escapeHtml(tags[k])}}</td></tr>`).join('');
      return `<strong>OSM ${{escapeHtml(p.feature_type || 'edge')}} ${{escapeHtml(p.osm_id)}}</strong>` +
        (p.exclusion_reason ? `<p>Excluded: <code>${{escapeHtml(p.exclusion_reason)}}</code></p>` : '') +
        (p.warning_codes ? `<p>Warnings: ${{escapeHtml(p.warning_codes.join(', '))}}</p>` : '') +
        (p.from_node ? `<p>Direction: ${{escapeHtml(p.from_node)}} &rarr; ${{escapeHtml(p.to_node)}}</p>` : '') +
        (rows ? `<table class="tag-table">${{rows}}</table>` : '');
    }}
    function lines(geojson, style) {{ return L.geoJSON(geojson, {{style, onEachFeature: (f,l) => l.bindPopup(popup(f))}}); }}
    const selected = lines(data.selected, {{color:'#178a45', weight:4, opacity:.86}});
    const excluded = lines(data.excluded, {{color:'#7f8b94', weight:3, opacity:.55, dashArray:'5 5'}});
    const projected = lines(data.projected, {{color:'#0077b6', weight:2, opacity:.8, dashArray:'8 4'}});
    const warnings = lines(data.warnings, {{color:'#d64933', weight:6, opacity:.8}});
    const directions = lines(data.directions, {{color:'#4b4f54', weight:1, opacity:.25}});
    directions.eachLayer(layer => {{ if (layer.getLatLngs) L.polylineDecorator(layer, {{patterns:[{{offset:'55%', repeat:0, symbol:L.Symbol.arrowHead({{pixelSize:8, polygon:false, pathOptions:{{color:'#111', weight:2}}}})}}]}}).addTo(directions); }});
    const signals = L.geoJSON(data.signals, {{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:6,color:'#111',weight:2,fillColor:'#ffd43b',fillOpacity:1}}), onEachFeature:(f,l)=>l.bindPopup(popup(f))}});
    const layerDefinitions = [
{layer_definitions}
    ];
    const overlays = {{}};
    layerDefinitions.forEach(([key, label, layer]) => {{
      if (!summary.visible_layers[key]) return;
      overlays[label] = layer;
      if (summary.enabled_layers[key]) layer.addTo(map);
    }});
    L.control.layers(null, overlays, {{collapsed:window.innerWidth < 800}}).addTo(map);
    const bounds = (summary.visible_layers.selected ? selected : projected).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds.pad(0.05)); else map.setView([0,0],2);
    document.getElementById('summary').innerHTML = `<dl><dt>Audit</dt><dd class="status-${{escapeHtml(summary.audit_status)}}">${{escapeHtml(summary.audit_status)}}</dd><dt>Selected ways</dt><dd>${{summary.selected_ways}}</dd><dt>Excluded ways</dt><dd>${{summary.excluded_ways}}</dd><dt>Graph edges</dt><dd>${{summary.directed_edges}}</dd><dt>Warnings</dt><dd>${{summary.warnings}}</dd><dt>Projection error</dt><dd>${{summary.round_trip_error}}</dd></dl>`;
  </script>
</body>
</html>
"""


def generate_inspection(*, workspace: Path, view: InspectionView) -> Path:
    """Generate an inspectable HTML checkpoint for the requested stage view."""
    workspace = workspace.resolve()
    if view == "lanelet2":
        preliminary = workspace / "lanelet2" / "preliminary.osm"
        if not preliminary.is_file():
            raise InspectionError(
                "Lanelet2 inspection is unavailable because Stage 2 has not produced "
                f"{preliminary}"
            )
        raise InspectionError("Lanelet2 inspection will be implemented with Stage 2 geometry")

    manifest_path = workspace / "source" / "manifest.json"
    report_path = workspace / "reports" / "acquisition.json"
    if not manifest_path.is_file() or not report_path.is_file():
        raise InspectionError("Stage 1 artifacts are incomplete; run fetch before inspect")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_path = workspace / manifest["source"]["path"]
    if view == "audit":
        audit_path = workspace / "reports" / "stage-1b-data-audit.json"
        if not audit_path.is_file():
            raise InspectionError(
                "Stage 1B audit is unavailable; rerun fetch to generate "
                f"{audit_path}"
            )
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        data = _audit_map_data(source_path, audit, report)
        inspection_dir = workspace / "inspection"
        reports_dir = workspace / "reports"
        inspection_dir.mkdir(parents=True, exist_ok=True)
        output_path = inspection_dir / "stage-1-audit.html"
        output_path.write_text(_render_audit_html(data=data, audit=audit), encoding="utf-8")
        inspection_report = {
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "view": view,
            "status": audit["downstream_readiness"],
            "html": output_path.relative_to(workspace).as_posix(),
            "html_sha256": _sha256(output_path),
            "summary": {
                "stage_1b_status": audit["stage_1b_status"],
                "downstream_readiness": audit["downstream_readiness"],
                "inference_enabled": audit["lane_count_coverage"]["inference_enabled"],
            },
            "layers": {
                name: len(collection["features"])
                for name, collection in data.items()
                if name != "searchable_ways"
            },
            "search": {
                "indexed_source_way_count": len(data["searchable_ways"]["features"]),
            },
            "coverage": {
                "source_review": [
                    "lane_count",
                    "oneway_and_direction",
                    "connectivity",
                    "grade_separation",
                    "turn_restrictions",
                    "traffic_signals",
                    "physical_width",
                    "mapped_stop_lines",
                ],
            },
        }
        json_path = reports_dir / "inspection-audit.json"
        markdown_path = reports_dir / "inspection-audit.md"
        json_path.write_text(json.dumps(inspection_report, indent=2, sort_keys=True) + "\n")
        markdown_path.write_text(
            "\n".join(
                [
                    "# Stage 1B Audit Inspection",
                    "",
                    f"- Downstream readiness: **{audit['downstream_readiness']}**",
                    f"- HTML: `{inspection_report['html']}`",
                    f"- Lane inference enabled: {str(audit['lane_count_coverage']['inference_enabled']).lower()}",
                    "",
                    "The map separates source-correctable evidence from checks that require manual review or Stage 2 geometry.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return output_path

    source_graph_path = workspace / manifest["artifacts"]["graphml"]["path"]
    projected_graph_path = workspace / manifest["stage_1b"]["artifacts"]["projected_graphml"]["path"]

    warning_by_osmid: dict[str, set[str]] = {}
    for warning in report["preflight"]["warnings"]:
        osm_id = warning.get("osm_id")
        if osm_id is not None:
            for value in str(osm_id).split(","):
                warning_by_osmid.setdefault(value, set()).add(warning["code"])

    selected, excluded, signals = _source_layers(source_path)
    directions, warnings = _graph_edge_layers(source_graph_path, warning_by_osmid)
    projected, _ = _graph_edge_layers(projected_graph_path, {})
    if view == "source":
        projected = _feature_collection([])
    elif view == "normalized":
        selected = _feature_collection([])
        excluded = _feature_collection([])
        signals = _feature_collection([])
        directions = _feature_collection([])
        warnings = _feature_collection([])

    visible_layers = {
        "selected": view in {"source", "stage-1"},
        "excluded": view in {"source", "stage-1"},
        "projected": view in {"normalized", "stage-1"},
        "warnings": view in {"source", "stage-1"},
        "directions": view in {"source", "stage-1"},
        "signals": view in {"source", "stage-1"},
    }
    enabled_layers = {
        **visible_layers,
        "projected": view == "normalized",
        "directions": False,
    }

    road_selection = manifest.get("road_selection", {})
    summary = {
        "audit_status": road_selection.get("status", "unknown"),
        "selected_ways": road_selection.get("selected_source_ways", len(selected["features"])),
        "excluded_ways": road_selection.get("excluded_source_ways", len(excluded["features"])),
        "directed_edges": report["feature_counts"]["edges"],
        "warnings": len(report["preflight"]["warnings"]),
        "round_trip_error": f"{report['projection']['round_trip']['maximum_error_degrees']:.3e} deg",
        "visible_layers": visible_layers,
        "enabled_layers": enabled_layers,
    }
    data = {
        "selected": selected,
        "excluded": excluded,
        "signals": signals,
        "directions": directions,
        "warnings": warnings,
        "projected": projected,
    }

    inspection_dir = workspace / "inspection"
    reports_dir = workspace / "reports"
    inspection_dir.mkdir(parents=True, exist_ok=True)
    output_name = "stage-1.html" if view == "stage-1" else f"stage-1-{view}.html"
    output_path = inspection_dir / output_name
    title = "Stage 1 Combined Inspection" if view == "stage-1" else f"Stage 1 {view.title()} Inspection"
    output_path.write_text(
        _render_html(title=title, data=data, summary=summary),
        encoding="utf-8",
    )
    inspection_report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "view": view,
        "status": "passed" if road_selection.get("status") == "passed" else "review_required",
        "html": output_path.relative_to(workspace).as_posix(),
        "html_sha256": _sha256(output_path),
        "summary": summary,
        "layers": {name: len(collection["features"]) for name, collection in data.items()},
    }
    json_path = reports_dir / f"inspection-{view}.json"
    markdown_path = reports_dir / f"inspection-{view}.md"
    json_path.write_text(json.dumps(inspection_report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(
        "\n".join(
            [
                f"# Stage 1 {view.title()} Inspection",
                "",
                f"- Status: **{inspection_report['status']}**",
                f"- HTML: `{inspection_report['html']}`",
                f"- Source audit: **{summary['audit_status']}**",
                f"- Selected source ways: {summary['selected_ways']}",
                f"- Excluded source ways: {summary['excluded_ways']}",
                f"- Directed graph edges: {summary['directed_edges']}",
                f"- Preflight warnings: {summary['warnings']}",
                f"- Projection round-trip error: {summary['round_trip_error']}",
                "",
                "Open the HTML in a browser, toggle each layer, and click features to inspect OSM evidence.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return output_path
