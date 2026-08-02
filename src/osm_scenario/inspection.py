# ruff: noqa: E501
"""Generate browser-based visual checkpoints for conversion workspaces."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import osmnx as ox
from shapely.geometry import LineString, mapping

from osm_scenario.osm_source import read_osm_snapshot, road_exclusion_reason

InspectionView = Literal["source", "normalized", "stage-1", "lanelet2"]


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
