# ruff: noqa: E501
"""The Stage 4 comparison map.

Separate from `apply_review` so that module keeps a real line-length check instead of
suppressing one for the sake of an embedded HTML template.

The report says what changed; this says *where*. A count of forbidden connectors is hard
to judge, but a junction with every movement struck through is obvious at a glance.

The page also lists the findings the reviewed model still carries. Stage 4 refuses any
decision whose effect is an OSM tag - see `_OSM_NATIVE_RULES` in `apply_review` - so the
blockers left here are the ones that can only be answered by editing the source OSM, and
that edit invalidates the whole Stage 3 review. They have to be readable in one place
before that trade is worth making. The list is deliberately read-only: Stage 4 has no
channel to apply a decision made on this page.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from pyproj import Transformer

from osm_scenario.lane_model import Point2D, PreliminaryLaneModel

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 4 - preliminary versus reviewed</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:13px/1.5 system-ui,sans-serif;color:#212529}
  #wrap{display:flex;height:100%%}
  #map{flex:1;min-width:0}
  #side{width:340px;overflow-y:auto;padding:16px;border-left:1px solid #dee2e6;background:#f8f9fa}
  h1{font-size:16px;margin:0 0 4px}
  h2{font-size:13px;margin:18px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#495057}
  table{border-collapse:collapse;width:100%%;margin-bottom:8px}
  td,th{padding:2px 6px;text-align:left;border-bottom:1px solid #e9ecef}
  td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
  .key{display:flex;align-items:center;gap:8px;margin:3px 0}
  .sw{width:22px;height:0;border-top-width:4px;border-top-style:solid;flex:none}
  .muted{color:#868e96}
  ul{margin:4px 0;padding-left:18px}
  code{font-size:11px;background:#e9ecef;padding:1px 4px;border-radius:3px}
  .caption{margin:-4px 0 10px;font-size:11px;color:#868e96}
  .frule{margin:8px 0 2px;font-size:11px;color:#495057;font-weight:600}
  .frow{padding:3px 6px;border-left:3px solid transparent;cursor:pointer;border-radius:2px}
  .frow:hover{background:#e9ecef}
  .frow.sel{background:#d0ebff;border-left-color:#1c7ed6}
  .frow .muted{display:block;font-size:11px}
  details{margin-top:4px}
  summary{cursor:pointer;color:#495057}
  #detail{margin-top:10px;padding:8px;background:#fff;border:1px solid #dee2e6;border-radius:3px;word-break:break-word}
  .dtitle{font-weight:600;margin-bottom:4px}
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">%(side)s</div></div>
<script>
const DATA = %(data)s;
const STYLE = {
  unchanged:       {color:'#adb5bd', weight:2, opacity:.55},
  added:           {color:'#2b8a3e', weight:4, opacity:.95},
  removed:         {color:'#c92a2a', weight:3, opacity:.9, dashArray:'6 4'},
  forbidden:       {color:'#c92a2a', weight:3, opacity:.9, dashArray:'6 4'},
  forbidden_before:{color:'#862e2e', weight:3, opacity:.8, dashArray:'2 5'},
  activated:       {color:'#2b8a3e', weight:5, opacity:.95},
  review_required: {color:'#f08c00', weight:5, opacity:.95, dashArray:'8 5'},
  stranded:        {color:'#7048e8', weight:6, opacity:.95}
};
// setStyle merges, so a base without dashArray cannot clear one the highlight set.
// Restoring over this reset makes every property the highlight touches explicit.
const RESET = {dashArray:null};
const HIGHLIGHT = {color:'#1c7ed6', weight:7, opacity:1, dashArray:null};
const map = L.map('map', {preferCanvas:true}).setView(DATA.center, 17);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:20, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
const groups = {};
const layersById = {};
for (const feature of DATA.features) {
  const style = STYLE[feature.change] || STYLE.unchanged;
  const layer = L.polyline(feature.line, style)
    .bindPopup('<b>' + feature.change + '</b><br>' + feature.kind + '<br><code>' + feature.id + '</code>'
               + (feature.note ? '<br>' + feature.note : ''));
  layer.__base = style;
  (layersById[feature.id] = layersById[feature.id] || []).push(layer);
  (groups[feature.change] = groups[feature.change] || L.layerGroup()).addLayer(layer);
}
const byFinding = {};
const markers = L.layerGroup();
for (const finding of DATA.findings) {
  byFinding[finding.id] = finding;
  if (finding.severity !== 'blocker' || finding.lat === null) continue;
  L.circleMarker([finding.lat, finding.lon],
    {radius:6, color:'#d6336c', weight:2, fillColor:'#d6336c', fillOpacity:.35})
    .on('click', () => select(finding.id))
    .addTo(markers);
}
const overlays = {};
for (const [name, group] of Object.entries(groups)) { group.addTo(map); overlays[name] = group; }
if (markers.getLayers().length) { markers.addTo(map); overlays['blocker findings'] = markers; }
L.control.layers(null, overlays, {collapsed:false}).addTo(map);
if (DATA.bounds) map.fitBounds(DATA.bounds, {padding:[24,24]});

let highlighted = [];
function detailRow(label, value) {
  const row = document.createElement('div');
  const key = document.createElement('b');
  key.textContent = label + ': ';
  row.append(key, document.createTextNode(value));
  return row;
}
function select(id) {
  const finding = byFinding[id];
  if (!finding) return;
  for (const layer of highlighted) layer.setStyle(Object.assign({}, RESET, layer.__base));
  highlighted = [];
  for (const featureId of finding.features) {
    for (const layer of (layersById[featureId] || [])) {
      layer.setStyle(HIGHLIGHT);
      layer.bringToFront();
      highlighted.push(layer);
    }
  }
  const spread = finding.bounds
    && (finding.bounds[0][0] !== finding.bounds[1][0] || finding.bounds[0][1] !== finding.bounds[1][1]);
  if (spread) map.fitBounds(finding.bounds, {padding:[48,48], maxZoom:19});
  else if (finding.lat !== null) map.setView([finding.lat, finding.lon], 19);
  for (const row of document.querySelectorAll('.frow')) {
    row.classList.toggle('sel', row.dataset.finding === id);
  }
  const box = document.getElementById('detail');
  box.textContent = '';
  box.classList.remove('muted');
  const title = document.createElement('div');
  title.className = 'dtitle';
  title.textContent = finding.rule;
  box.append(title);
  box.append(detailRow('severity', finding.severity));
  box.append(detailRow('confidence', finding.confidence));
  box.append(detailRow('reason', finding.reason));
  box.append(detailRow('source', finding.source_type + ' ' + finding.source_ids.join(', ')));
  box.append(detailRow('proposed', JSON.stringify(finding.proposed)));
  box.append(detailRow('affects', finding.features.length + ' generated feature(s)'));
  const identifier = document.createElement('div');
  identifier.className = 'muted';
  const code = document.createElement('code');
  code.textContent = finding.id;
  identifier.append(code);
  box.append(identifier);
}
for (const row of document.querySelectorAll('.frow')) {
  row.addEventListener('click', () => select(row.dataset.finding));
}
</script>
</body>
</html>
"""


def _lonlat(points: list[Point2D], transformer: Transformer) -> list[list[float]]:
    """Leaflet wants [lat, lon]; the transformer yields (lon, lat) under always_xy."""
    out = []
    for point in points:
        lon, lat = transformer.transform(point.x, point.y)
        out.append([lat, lon])
    return out


def _findings(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    """The reviewed model's findings, blockers first.

    Ordered here rather than in the panel so the list the reader clicks and the list the
    script indexes are the same object in the same order. The page is a checksummed
    artifact, so the order has to be a function of the model and nothing else.
    """
    out: list[dict[str, Any]] = []
    for finding in model.findings:
        data = finding.model_dump(mode="json")
        location = data.get("location")
        bounds = None
        if location and len(location.get("bbox") or []) == 4:
            # bbox is [min_lon, min_lat, max_lon, max_lat]; Leaflet wants corner pairs
            # the other way round, and transposing it silently is the whole reason
            # `GeoPoint` uses named keys.
            min_lon, min_lat, max_lon, max_lat = location["bbox"]
            bounds = [[min_lat, min_lon], [max_lat, max_lon]]
        out.append(
            {
                "id": data["identifier"],
                "rule": data["rule"],
                "severity": data["severity"],
                "confidence": data["confidence"],
                "reason": data["reason"],
                "proposed": data["proposed_value"],
                "source_type": data["source_type"],
                "source_ids": data["source_ids"],
                "features": data["affected_feature_ids"],
                # `location` is None for `edge`-scoped findings, which name graph edges
                # with no OSM geometry to place. They still list, they just do not fly to.
                "lat": location["lat"] if location else None,
                "lon": location["lon"] if location else None,
                "bounds": bounds,
            }
        )
    out.sort(key=lambda item: (item["severity"] != "blocker", item["rule"], item["id"]))
    return out


def _finding_rows(findings: list[dict[str, Any]]) -> str:
    """Findings grouped by rule, each row naming the OSM feature it came from."""
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        by_rule.setdefault(finding["rule"], []).append(finding)
    out = []
    for rule, group in by_rule.items():
        out.append(f"<div class='frule'>{escape(rule)} - {len(group)}</div>")
        for finding in group:
            refs = ", ".join(finding["source_ids"][:2])
            if len(finding["source_ids"]) > 2:
                refs += f" +{len(finding['source_ids']) - 2}"
            out.append(
                f"<div class='frow' data-finding='{escape(finding['id'])}'>"
                f"{escape(finding['source_type'])} <code>{escape(refs)}</code>"
                f"<span class='muted'>{escape(finding['reason'])}</span></div>"
            )
    return "".join(out)


def _side_panel(comparison: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    def rows(table: dict[str, dict[str, int]], head: str) -> str:
        body = "".join(
            f"<tr><td>{name}</td><td class='n'>{v['preliminary']}</td>"
            f"<td class='n'>{v['reviewed']}</td></tr>"
            for name, v in table.items()
        )
        return (
            f"<table><tr><th>{head}</th><th class='n'>before</th>"
            f"<th class='n'>after</th></tr>{body}</table>"
        )

    created = comparison["findings_the_review_created"]
    resolved = comparison["findings_the_review_resolved"]
    stranded = comparison["lanes_left_without_an_exit"]
    blockers = [finding for finding in findings if finding["severity"] == "blocker"]
    warnings = [finding for finding in findings if finding["severity"] != "blocker"]
    blocking = "".join(
        f"<li><code>{item['rule']}</code> at {', '.join(item['source_ids'])}</li>"
        for item in created["blocking"]
    )
    legend = "".join(
        f"<div class='key'><span class='sw' style='border-top-color:{colour}'></span>{label}</div>"
        for label, colour in (
            ("unchanged", "#adb5bd"),
            ("added by the review", "#2b8a3e"),
            ("forbidden by the review", "#c92a2a"),
            ("forbidden before the review", "#862e2e"),
            ("still review_required", "#f08c00"),
            ("lane with no exit left", "#7048e8"),
            ("blocker finding", "#d6336c"),
        )
    )
    return (
        "<h1>Stage 4 - preliminary versus reviewed</h1>"
        "<p class='muted'>What the review did to the map.</p>"
        "<h2>Features</h2>" + rows(comparison["counts"], "feature")
        + "<p class='caption'>before = the Stage 2 preliminary model. "
        "after = the model Stage 4 regenerated with your decisions applied.</p>"
        + "<h2>Connectors</h2>"
        + rows(comparison["connector_status"], "status")
        + "<p class='caption'>The forbidden count covers both causes: movements an OSM "
        "turn restriction already forbade, and movements this review forbade. Both are "
        "drawn, in different reds.</p>"
        + f"<h2>Findings resolved: {resolved['total']}</h2>"
        + f"<h2>Findings created: {created['total']}</h2>"
        + (
            f"<p>{len(created['blocking'])} block promotion:</p><ul>{blocking}</ul>"
            if created["blocking"]
            else "<p class='muted'>None of them block promotion.</p>"
        )
        + f"<h2>Blockers still in the model: {len(blockers)}</h2>"
        + (
            "<p class='caption'>Only an OSM tag edit can answer most of these, and that "
            "invalidates the whole review - so decide them together. Click one to find "
            "it on the map.</p>" + _finding_rows(blockers)
            if blockers
            else "<p class='muted'>None. Nothing blocks promotion.</p>"
        )
        + (
            f"<details><summary>Warnings: {len(warnings)}</summary>"
            f"{_finding_rows(warnings)}</details>"
            if warnings
            else ""
        )
        + (
            "<div id='detail' class='muted'>Select a finding to see its evidence.</div>"
            if findings
            else ""
        )
        + f"<h2>Lanes left with no exit: {len(stranded)}</h2>"
        + (
            "<ul>" + "".join(f"<li><code>{lane}</code></li>" for lane in stranded) + "</ul>"
            if stranded
            else "<p class='muted'>No lane lost its last way out. "
            f"{comparison['lanes_without_an_exit_either_way']} had none before this "
            "review and still have none - they run off the edge of the extract.</p>"
        )
        + (
            f"<h2>Lanes given an exit: {len(comparison['lanes_given_an_exit'])}</h2>"
            if comparison["lanes_given_an_exit"]
            else ""
        )
        + "<h2>Legend</h2>"
        + legend
    )


def render_comparison_html(
    *,
    preliminary: PreliminaryLaneModel,
    reviewed: PreliminaryLaneModel,
    comparison: dict[str, Any],
) -> str:
    """Draw both models on one map, each feature coloured by what the review did to it."""
    transformer = Transformer.from_crs(
        reviewed.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )
    before_lanes = {lane.identifier: lane for lane in preliminary.lanes}
    after_lanes = {lane.identifier: lane for lane in reviewed.lanes}
    stranded = set(comparison["lanes_left_without_an_exit"])
    forbidden_by_review = set(comparison["connectors"]["forbidden_by_review"])
    activated_by_review = set(comparison["connectors"]["activated_by_review"])

    features: list[dict[str, Any]] = []
    for identifier, lane in sorted(after_lanes.items()):
        change = (
            "stranded"
            if identifier in stranded
            else "added"
            if identifier not in before_lanes
            else "unchanged"
        )
        features.append(
            {
                "id": identifier,
                "kind": f"lane {lane.lane_index}/{lane.lane_count} of way "
                f"{', '.join(lane.source_way_ids)}",
                "change": change,
                "line": _lonlat(lane.centerline, transformer),
                "note": "nothing can leave this lane" if change == "stranded" else "",
            }
        )
    for identifier, lane in sorted(before_lanes.items()):
        if identifier in after_lanes:
            continue
        features.append(
            {
                "id": identifier,
                "kind": f"lane removed from way {', '.join(lane.source_way_ids)}",
                "change": "removed",
                "line": _lonlat(lane.centerline, transformer),
                "note": "",
            }
        )
    for connector in sorted(reviewed.connectors, key=lambda item: item.identifier):
        if connector.identifier in forbidden_by_review:
            change = "forbidden"
        elif connector.identifier in activated_by_review:
            change = "activated"
        elif connector.status == "review_required":
            change = "review_required"
        elif connector.status == "forbidden":
            # Forbidden before the review as well. Drawn in its own colour rather than
            # skipped: the panel counts it under `forbidden`, and a status the table
            # counts but the map omits is a number the reader cannot check.
            change = "forbidden_before"
        else:
            change = "unchanged"
        features.append(
            {
                "id": connector.identifier,
                "kind": f"{connector.movement} at node {connector.junction_node_id} "
                f"({connector.turn_angle_degrees:+.1f} degrees)",
                "change": change,
                "line": _lonlat(connector.centerline, transformer),
                "note": (
                    "an OSM turn restriction already forbade this, before the review"
                    if change == "forbidden_before"
                    else ""
                ),
            }
        )

    points = [point for feature in features for point in feature["line"]]
    if points:
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        center = [(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
    else:
        center, bounds = [0.0, 0.0], None

    findings = _findings(reviewed)
    data = json.dumps(
        {"center": center, "bounds": bounds, "features": features, "findings": findings},
        separators=(",", ":"),
    )
    # A literal `</script>` inside the payload would end the block early.
    data = data.replace("</", "<\\/")
    return _TEMPLATE % {"side": _side_panel(comparison, findings), "data": data}
