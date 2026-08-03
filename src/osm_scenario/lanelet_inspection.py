# ruff: noqa: E501
"""Stage 3A visual inspection for a preliminary Lanelet2 map."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lanelet2 import io, projection
from lanelet2.core import BasicPoint3d

from osm_scenario.osm_source import read_osm_snapshot

REVIEW_LAYERS = {
    "ambiguous_connector": {
        "label": "Ambiguous connectors",
        "color": "#d9480f",
        "dash_array": None,
    },
    "lane_count_ambiguous": {
        "label": "Ambiguous lane counts",
        "color": "#c2255c",
        "dash_array": None,
    },
    "stop_line_inferred": {
        "label": "Inferred stop lines",
        "color": "#7048e8",
        "dash_array": "4 5",
    },
    "traffic_signal_association_review": {
        "label": "Traffic-signal associations",
        "color": "#0b7285",
        "dash_array": None,
    },
    "via_way_restriction_review": {
        "label": "Via-way restrictions",
        "color": "#212529",
        "dash_array": "10 6",
    },
}


class PreliminaryInspectionError(RuntimeError):
    """Raised when the preliminary Lanelet2 checkpoint cannot be inspected."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _coordinates(points: Any, projector: projection.LocalCartesianProjector) -> list[list[float]]:
    coordinates = []
    for point in points:
        gps = projector.reverse(BasicPoint3d(point.x, point.y, point.z))
        coordinates.append([gps.lon, gps.lat])
    return coordinates


def _feature(coordinates: list[list[float]], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": properties,
    }


def _collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _review_text(item: dict[str, Any]) -> str:
    text = f"{item['code']}: {item['reason']}"
    qualifiers = [
        f"priority={item['priority']}" if item.get("priority") else None,
        f"confidence={item['confidence']}" if item.get("confidence") else None,
    ]
    present = [qualifier for qualifier in qualifiers if qualifier]
    return f"{text} ({', '.join(present)})" if present else text


def _stringify_identifiers(properties: dict[str, Any]) -> dict[str, Any]:
    """Keep identifiers exact when the audit payload is parsed by JavaScript."""
    result = dict(properties)
    for key, value in result.items():
        if value is None:
            continue
        if key.endswith("_id"):
            result[key] = str(value)
        elif key.endswith("_ids") and isinstance(value, list):
            result[key] = [str(item) for item in value]
    return result


def _render_html(*, data: dict[str, Any], summary: dict[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    summary_payload = json.dumps(summary, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Stage 3A Preliminary Lanelet2 Audit</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    *{{box-sizing:border-box}} html,body{{height:100%;margin:0;font:14px system-ui,sans-serif;color:#202428}}
    body{{display:grid;grid-template-columns:minmax(270px,340px) 1fr;background:#f4f5f6}}
    aside{{padding:16px;overflow:auto;border-right:1px solid #c8cdd1;background:#fff}}
    h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:15px;margin:20px 0 8px}}
    .muted{{color:#5d666d}} .status{{font-weight:700;color:#9b2c1d}}
    dl{{display:grid;grid-template-columns:1fr auto;gap:6px 12px;margin:14px 0}} dt{{color:#5d666d}} dd{{margin:0;font-weight:600}}
    label{{display:block;font-weight:600;margin-bottom:5px}} .search{{display:flex;gap:6px}}
    select,input,button{{height:36px;border:1px solid #aeb6bc;background:#fff;border-radius:4px;padding:0 9px}}
    input{{min-width:0;flex:1}} button{{cursor:pointer;background:#202428;color:#fff}}
    #search-result{{min-height:38px;margin-top:7px;color:#495057}}
    .legend{{display:grid;grid-template-columns:18px 1fr;gap:8px;align-items:center}}
    .swatch{{height:5px}} .road{{background:#087f5b}} .connector{{background:#e67700}}
    .boundary{{background:#495057}} .stop{{background:#7048e8;height:8px}}
    .review-swatch{{height:8px}}
    #map{{height:100%;min-height:520px}} .leaflet-popup-content{{max-height:260px;overflow:auto}}
    table{{border-collapse:collapse}} td{{padding:3px 7px;border-bottom:1px solid #e5e7e9;vertical-align:top}}
    @media(max-width:760px){{body{{grid-template-columns:1fr;grid-template-rows:auto minmax(520px,1fr)}} aside{{border-right:0;border-bottom:1px solid #c8cdd1}}}}
  </style>
</head>
<body>
  <aside>
    <h1>Stage 3A Preliminary Audit</h1>
    <div class="muted">Read-only view of <code>lanelet2/preliminary.osm</code></div>
    <dl id="summary"></dl>
    <h2>Find generated geometry</h2>
    <label for="search-value">Search identifier</label>
    <div class="search"><select id="search-kind"><option value="lanelet">Lanelet ID</option><option value="way">Source way ID</option><option value="node">Source node ID</option><option value="relation">Source relation ID</option></select><input id="search-value" inputmode="numeric"><button id="search-button">Find</button></div>
    <div id="search-result"></div>
    <h2>Legend</h2>
    <div class="legend"><span class="swatch road"></span><span>Road lanelets</span><span class="swatch connector"></span><span>Junction connectors</span><span class="swatch boundary"></span><span>Lane boundaries</span><span class="swatch stop"></span><span>Stop lines and traffic-light geometry</span></div>
    <h2>Review queue</h2>
    <div class="legend" id="review-legend"></div>
    <p class="muted">Review overlays are available in the layer control. Click any affected road or connector to see why review is required.</p>
  </aside>
  <main id="map"></main>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const data={payload}; const summary={summary_payload};
    const map=L.map('map',{{preferCanvas:true}}); L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:21,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
    const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    function displayValue(value){{if(Array.isArray(value))return value.map(escapeHtml).join('<br>');if(typeof value==='object'&&value!==null)return escapeHtml(JSON.stringify(value));return escapeHtml(value);}}
    function popup(feature){{const p=feature.properties; return '<table>'+Object.entries(p).filter(([,v])=>v!==null&&v!==''&&(!Array.isArray(v)||v.length)).map(([k,v])=>`<tr><td>${{escapeHtml(k)}}</td><td>${{displayValue(v)}}</td></tr>`).join('')+'</table>';}}
    function lines(collection,style){{return L.geoJSON(collection,{{style,onEachFeature:(f,l)=>l.bindPopup(popup(f))}});}}
    const boundaries=lines(data.boundaries,{{color:'#495057',weight:1,opacity:.58}}).addTo(map);
    const roads=lines(data.roads,{{color:'#087f5b',weight:3,opacity:.8}}).addTo(map);
    const connectors=lines(data.connectors,{{color:'#e67700',weight:3,opacity:.8}}).addTo(map);
    const controls=lines(data.controls,{{color:'#7048e8',weight:7,opacity:1}}).addTo(map);
    const overlays={{'Road lanelets':roads,'Junction connectors':connectors,'Lane boundaries':boundaries,'Stop lines and traffic lights':controls}};
    const reviewLayers={{}};
    Object.entries(data.reviews).forEach(([code,review])=>{{const layer=lines(review.features,{{color:review.color,weight:8,opacity:.95,dashArray:review.dash_array}});reviewLayers[code]=layer;overlays[`${{review.label}} (${{review.item_count}})`]=layer;}});
    L.control.layers(null,overlays,{{collapsed:window.innerWidth<800}}).addTo(map);
    const all=L.featureGroup([roads,connectors,boundaries,controls]); const bounds=all.getBounds(); if(bounds.isValid())map.fitBounds(bounds.pad(.04));else map.setView([0,0],2);
    const layerByLanelet=new Map(); const wayIndex=new Map(); const nodeIndex=new Map(); const relationIndex=new Map();
    function addIndex(index,key,layer){{if(key===null||key===undefined)return;key=String(key);if(!index.has(key))index.set(key,[]);index.get(key).push(layer);}}
    [roads,connectors].forEach(group=>group.eachLayer(layer=>{{const p=layer.feature.properties;if(p.lanelet_id)layerByLanelet.set(String(p.lanelet_id),layer);addIndex(wayIndex,p.source_osm_way_id,layer);addIndex(nodeIndex,p.source_osm_node_id,layer);(p.review_relation_ids||[]).forEach(id=>addIndex(relationIndex,id,layer));}}));
    const selection=L.featureGroup().addTo(map);
    function find(){{selection.clearLayers();const kind=document.getElementById('search-kind').value;const value=document.getElementById('search-value').value.trim();let found=[];if(kind==='lanelet'){{const layer=layerByLanelet.get(value);if(layer)found=[layer];}}else{{const index={{way:wayIndex,node:nodeIndex,relation:relationIndex}}[kind];found=index.get(value)||[];}}const result=document.getElementById('search-result');if(!found.length){{result.textContent=`No ${{kind}} ${{value}} found in this preliminary map.`;return;}}found.forEach(layer=>L.geoJSON(layer.feature,{{style:{{color:'#ffe066',weight:11,opacity:1}}}}).addTo(selection));map.fitBounds(selection.getBounds().pad(.35));found[0].openPopup();result.textContent=`Found ${{found.length}} matching feature${{found.length===1?'':'s'}}. Highlighted yellow.`;}}
    document.getElementById('search-button').addEventListener('click',find);document.getElementById('search-value').addEventListener('keydown',e=>{{if(e.key==='Enter')find();}});
    document.getElementById('summary').innerHTML=`<dt>Status</dt><dd class="status">${{escapeHtml(summary.status)}}</dd><dt>Road lanelets</dt><dd>${{summary.road_lanelets}}</dd><dt>Connectors</dt><dd>${{summary.connector_lanelets}}</dd><dt>Boundaries</dt><dd>${{summary.boundaries}}</dd><dt>Review items</dt><dd>${{summary.review_items}}</dd><dt>Unmapped reviews</dt><dd>${{summary.unmapped_review_items}}</dd><dt>Map SHA-256</dt><dd title="${{summary.preliminary_sha256}}">${{summary.preliminary_sha256.slice(0,12)}}...</dd>`;
    document.getElementById('review-legend').innerHTML=Object.entries(data.reviews).map(([code,review])=>`<span class="review-swatch" style="background:${{escapeHtml(review.color)}}"></span><span><code>${{escapeHtml(code)}}</code>: ${{review.item_count}}</span>`).join('');
  </script>
</body>
</html>
"""


def generate_preliminary_inspection(*, workspace: Path) -> Path:
    """Render the immutable Stage 3A checkpoint from the Stage 2 preliminary map."""
    workspace = workspace.resolve()
    preliminary = workspace / "lanelet2" / "preliminary.osm"
    generation_path = workspace / "reports" / "lanelet2-generation.json"
    manifest_path = workspace / "source" / "manifest.json"
    source_path = workspace / "source" / "map.osm"
    missing = [
        path
        for path in (preliminary, generation_path, manifest_path, source_path)
        if not path.is_file()
    ]
    if missing:
        raise PreliminaryInspectionError(
            "Stage 3A requires completed Stage 2 artifacts; missing "
            + ", ".join(str(path) for path in missing)
        )

    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    preliminary_sha256 = _sha256(preliminary)
    expected_sha256 = (
        manifest.get("stage_2", {})
        .get("artifacts", {})
        .get("preliminary_lanelet2", {})
        .get("sha256")
    )
    if expected_sha256 != preliminary_sha256:
        raise PreliminaryInspectionError(
            "preliminary.osm does not match the Stage 2 manifest; rerun generate-lanelet2 "
            "or keep manual edits in lanelet2/edited.osm"
        )

    origin = generation["configuration"]["origin"]
    projector = projection.LocalCartesianProjector(
        io.Origin(origin["latitude"], origin["longitude"])
    )
    lanelet_map, parser_errors = io.loadRobust(str(preliminary), projector)
    if parser_errors:
        raise PreliminaryInspectionError(
            "Lanelet2 parser errors: " + "; ".join(str(error) for error in parser_errors)
        )

    records = {str(item["lanelet_id"]): item for item in generation["lanelets"]}
    snapshot = read_osm_snapshot(source_path)
    review_items = [
        {**item, "review_item_id": index}
        for index, item in enumerate(generation["correction_queue"])
        if item["code"] in REVIEW_LAYERS
    ]
    reviews_by_lanelet: dict[str, list[dict[str, Any]]] = {}
    reviews_by_edge: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    reviews_by_relation_way: dict[str, list[dict[str, Any]]] = {}
    for item in review_items:
        lanelet_id = item.get("generated_lanelet_id")
        if lanelet_id is not None:
            reviews_by_lanelet.setdefault(str(lanelet_id), []).append(item)
        source_edge = item.get("source_edge")
        if source_edge:
            reviews_by_edge.setdefault(tuple(map(str, source_edge)), []).append(item)
        relation_id = item.get("source_osm_relation_id")
        if relation_id is None:
            continue
        relation = snapshot.relations.get(str(relation_id))
        if relation is None:
            continue
        relation_members = [
            f"{member.role or '(no role)'}: {member.member_type} {member.reference}"
            for member in relation.members
        ]
        item["source_relation_members"] = relation_members
        for member in relation.members:
            if member.member_type == "way":
                reviews_by_relation_way.setdefault(member.reference, []).append(item)

    roads: list[dict[str, Any]] = []
    connectors: list[dict[str, Any]] = []
    review_features: dict[str, list[dict[str, Any]]] = {
        code: [] for code in REVIEW_LAYERS
    }
    mapped_review_item_ids: set[int] = set()
    for lanelet in lanelet_map.laneletLayer:
        lanelet_id = str(lanelet.id)
        record = records.get(lanelet_id, {})
        lanelet_reviews = list(reviews_by_lanelet.get(lanelet_id, []))
        source_edge = record.get("source_edge")
        if source_edge:
            lanelet_reviews.extend(reviews_by_edge.get(tuple(map(str, source_edge)), []))
        source_way_id = record.get("source_osm_way_id")
        if source_way_id is not None:
            lanelet_reviews.extend(reviews_by_relation_way.get(str(source_way_id), []))
        lanelet_reviews = list(
            {item["review_item_id"]: item for item in lanelet_reviews}.values()
        )
        mapped_review_item_ids.update(item["review_item_id"] for item in lanelet_reviews)
        properties = _stringify_identifiers(
            {
                **record,
                "lanelet_id": lanelet.id,
                "review_codes": sorted({item["code"] for item in lanelet_reviews}),
                "review_reasons": [_review_text(item) for item in lanelet_reviews],
                "review_relation_ids": sorted(
                    {
                        str(item["source_osm_relation_id"])
                        for item in lanelet_reviews
                        if item.get("source_osm_relation_id") is not None
                    }
                ),
            }
        )
        feature = _feature(_coordinates(lanelet.centerline, projector), properties)
        (connectors if record.get("kind") == "connector" else roads).append(feature)
        for code in sorted({item["code"] for item in lanelet_reviews}):
            code_items = [item for item in lanelet_reviews if item["code"] == code]
            review_features[code].append(
                _feature(
                    feature["geometry"]["coordinates"],
                    {
                        **properties,
                        "review_code": code,
                        "review_reasons": [_review_text(item) for item in code_items],
                        "source_relation_members": sorted(
                            {
                                member
                                for item in code_items
                                for member in item.get("source_relation_members", [])
                            }
                        ),
                    },
                )
            )

    boundaries = [
        _feature(
            _coordinates(line, projector),
            _stringify_identifiers(
                {
                    "linestring_id": line.id,
                    **{str(key): str(value) for key, value in line.attributes.items()},
                }
            ),
        )
        for line in lanelet_map.lineStringLayer
    ]
    controls = [
        feature
        for feature in boundaries
        if feature["properties"].get("type") in {"stop_line", "traffic_light"}
    ]
    review_counts = Counter(item["code"] for item in review_items)
    reviews = {
        code: {
            **settings,
            "item_count": review_counts.get(code, 0),
            "mapped_feature_count": len(review_features[code]),
            "features": _collection(review_features[code]),
        }
        for code, settings in REVIEW_LAYERS.items()
    }
    data = {
        "roads": _collection(roads),
        "connectors": _collection(connectors),
        "boundaries": _collection(boundaries),
        "controls": _collection(controls),
        "reviews": reviews,
    }
    unmapped_review_items = len(review_items) - len(mapped_review_item_ids)
    summary = {
        "status": generation["status"],
        "preliminary_sha256": preliminary_sha256,
        "generation_report_sha256": _sha256(generation_path),
        "road_lanelets": len(roads),
        "connector_lanelets": len(connectors),
        "boundaries": len(boundaries),
        "control_lines": len(controls),
        "review_items": len(review_items),
        "mapped_review_items": len(mapped_review_item_ids),
        "unmapped_review_items": unmapped_review_items,
        "review_codes": dict(sorted(review_counts.items())),
    }

    inspection_dir = workspace / "inspection"
    reports_dir = workspace / "reports"
    inspection_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = inspection_dir / "stage-3a-preliminary-audit.html"
    json_path = reports_dir / "inspection-stage-3a-preliminary.json"
    markdown_path = reports_dir / "inspection-stage-3a-preliminary.md"
    output_path.write_text(_render_html(data=data, summary=summary), encoding="utf-8")
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage": "3a",
        "checkpoint": "preliminary",
        "status": generation["status"],
        "inputs": {
            "preliminary_lanelet2": {
                "path": preliminary.relative_to(workspace).as_posix(),
                "sha256": preliminary_sha256,
            },
            "generation_report": {
                "path": generation_path.relative_to(workspace).as_posix(),
                "sha256": summary["generation_report_sha256"],
            },
            "source_osm": {
                "path": source_path.relative_to(workspace).as_posix(),
                "sha256": _sha256(source_path),
            },
        },
        "artifacts": {
            "html": {
                "path": output_path.relative_to(workspace).as_posix(),
                "sha256": _sha256(output_path),
            }
        },
        "summary": summary,
        "layers": {
            "roads": len(roads),
            "connectors": len(connectors),
            "boundaries": len(boundaries),
            "controls": len(controls),
            "reviews": {
                code: {
                    "items": review["item_count"],
                    "mapped_features": review["mapped_feature_count"],
                }
                for code, review in reviews.items()
            },
        },
    }
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_lines = [f"- `{code}`: {count}" for code, count in review_counts.items()]
    markdown_path.write_text(
        "\n".join(
            [
                "# Stage 3A Preliminary Lanelet2 Inspection",
                "",
                f"- Status: **{generation['status']}**",
                f"- Preliminary map: `{report['inputs']['preliminary_lanelet2']['path']}`",
                f"- Preliminary SHA-256: `{preliminary_sha256}`",
                f"- HTML: `{report['artifacts']['html']['path']}`",
                f"- Road lanelets: {len(roads)}",
                f"- Junction connectors: {len(connectors)}",
                f"- Review items: {len(review_items)}",
                f"- Unmapped review items: {unmapped_review_items}",
                "",
                "## Visual Review Queue",
                "",
                *(review_lines or ["- None"]),
                "",
                "Medium-confidence lane-count and lane-width defaults remain in the Stage 2 generation report but are not visual review overlays.",
                "",
                "This checkpoint is generated only from the Stage 2 preliminary map, source OSM, and report. It does not modify the map or any Stage 3B/3C artifact.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return output_path
