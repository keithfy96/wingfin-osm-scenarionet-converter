# ruff: noqa: E501
"""The Stage 5 validation map.

Separate from `validation` so that module keeps a real line-length check instead of
suppressing one for the sake of an embedded HTML template - the same split
`comparison_view` makes for Stage 4.

The report says whether the map passed; this says where it looked. Its real subject is
the **boundary rule**, the one place Stage 5 makes a judgement rather than a measurement:
it reclassifies dead-end lanes as the edge of the extract when their end node terminates
every source way containing it. As a number in a report that is a claim to take on faith.
Drawn, it is checkable in a glance - the boundary lanes should form the rim of the
extract, and any sitting in the interior means the rule is wrong.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from pyproj import Transformer

# Private helper imported across modules on purpose, for the reason given in
# `apply_review`: this is the exact projection Stage 4's page uses, and a Stage 5 copy
# would be a second implementation to keep in step.
from osm_scenario.comparison_view import _lonlat
from osm_scenario.lane_model import PreliminaryLaneModel

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 5 - map validation</title>
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
  code{font-size:11px;background:#e9ecef;padding:1px 4px;border-radius:3px}
  .caption{margin:-4px 0 10px;font-size:11px;color:#868e96}
  .verdict{font-size:15px;font-weight:600;padding:6px 10px;border-radius:3px;margin:0 0 10px}
  .verdict.passed{background:#d3f9d8;color:#2b8a3e}
  .verdict.failed{background:#ffe3e3;color:#c92a2a}
  .frule{margin:8px 0 2px;font-size:11px;color:#495057;font-weight:600}
  .frow{padding:3px 6px;border-left:3px solid transparent;cursor:pointer;border-radius:2px}
  .frow:hover{background:#e9ecef}
  .frow.sel{background:#d0ebff;border-left-color:#1c7ed6}
  .frow.flat{cursor:default}
  .frow.flat:hover{background:none}
  .frow .muted{display:block;font-size:11px}
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">%(side)s</div></div>
<script>
const DATA = %(data)s;
const STYLE = {
  clean:    {color:'#adb5bd', weight:2, opacity:.5},
  boundary: {color:'#1c7ed6', weight:3, opacity:.9, dashArray:'6 4'},
  warning:  {color:'#f08c00', weight:5, opacity:.95},
  error:    {color:'#c92a2a', weight:6, opacity:.95}
};
const RESET = {dashArray:null};
const HIGHLIGHT = {color:'#7048e8', weight:8, opacity:1, dashArray:null};
const map = L.map('map', {preferCanvas:true}).setView(DATA.center, 17);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:20, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
const groups = {};
const layersById = {};
for (const feature of DATA.features) {
  const style = STYLE[feature.role] || STYLE.clean;
  const layer = L.polyline(feature.line, style)
    .bindPopup('<b>' + feature.role + '</b><br>' + feature.kind + '<br><code>' + feature.id + '</code>'
               + (feature.note ? '<br>' + feature.note : ''));
  layer.__base = style;
  (layersById[feature.id] = layersById[feature.id] || []).push(layer);
  (groups[feature.role] = groups[feature.role] || L.layerGroup()).addLayer(layer);
}
const overlays = {};
for (const [name, group] of Object.entries(groups)) { group.addTo(map); overlays[name] = group; }
L.control.layers(null, overlays, {collapsed:false}).addTo(map);
if (DATA.bounds) map.fitBounds(DATA.bounds, {padding:[24,24]});

const byIssue = {};
for (const issue of DATA.issues) byIssue[issue.key] = issue;
let highlighted = [];
function select(key) {
  const issue = byIssue[key];
  if (!issue) return;
  for (const layer of highlighted) layer.setStyle(Object.assign({}, RESET, layer.__base));
  highlighted = [];
  const bounds = [];
  for (const featureId of issue.features) {
    for (const layer of (layersById[featureId] || [])) {
      layer.setStyle(HIGHLIGHT);
      layer.bringToFront();
      highlighted.push(layer);
      bounds.push(layer.getBounds());
    }
  }
  if (bounds.length) {
    let box = bounds[0];
    for (const other of bounds.slice(1)) box = box.extend(other);
    map.fitBounds(box, {padding:[64,64], maxZoom:19});
  }
  for (const row of document.querySelectorAll('.frow')) {
    row.classList.toggle('sel', row.dataset.issue === key);
  }
}
for (const row of document.querySelectorAll('.frow')) {
  row.addEventListener('click', () => select(row.dataset.issue));
}
</script>
</body>
</html>
"""


def _issue_features(issue: dict[str, Any]) -> list[str]:
    """The generated features an issue points at, so a row can highlight something."""
    return [
        str(issue[field])
        for field in ("lane_id", "connector_id", "signal_id", "stop_line_id")
        if issue.get(field)
    ]


def _issue_rows(issues: list[dict[str, Any]], prefix: str) -> str:
    """Issues grouped by code, each row naming the OSM feature it came from."""
    by_code: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, issue in enumerate(issues):
        by_code.setdefault(issue["code"], []).append((index, issue))
    out = []
    for code, group in by_code.items():
        out.append(f"<div class='frule'>{escape(code)} - {len(group)}</div>")
        for index, issue in group:
            osm_id = issue.get("osm_id") or "-"
            located = issue.get("_locatable", True)
            note = escape(issue["reason"]) + (
                "" if located else " (no drawn feature - find it by its OSM id)"
            )
            out.append(
                f"<div class='{'frow' if located else 'frow flat'}' "
                f"data-issue='{prefix}-{index}'>"
                f"OSM <code>{escape(str(osm_id))}</code>"
                f"<span class='muted'>{note}</span></div>"
            )
    return "".join(out)


def _side_panel(report: dict[str, Any]) -> str:
    checked = report["checked"]
    boundary = report["boundary"]
    errors = report["errors"]
    warnings = report["warnings"]
    status = report["status"]

    scope = "".join(
        f"<tr><td>{name}</td><td class='n'>{checked[key]}</td></tr>"
        for name, key in (
            ("lanes", "lanes"),
            ("connectors", "connectors"),
            ("restrictions", "restrictions"),
            ("signals", "signals"),
            ("stop lines", "stop_lines"),
        )
    )
    legend = "".join(
        f"<div class='key'><span class='sw' style='border-top-color:{colour}'></span>{label}</div>"
        for label, colour in (
            ("passed every check", "#adb5bd"),
            ("dead end the extract explains", "#1c7ed6"),
            ("carries a dispositioned warning", "#f08c00"),
            ("carries an error", "#c92a2a"),
        )
    )
    return (
        "<h1>Stage 5 - map validation</h1>"
        f"<p class='verdict {escape(status)}'>{escape(status)}</p>"
        f"<p class='muted'>Model <code>{escape(report['validated_lane_model']['sha256'][:16])}</code></p>"
        "<h2>Checked</h2>"
        f"<table><tr><th>feature</th><th class='n'>examined</th></tr>{scope}</table>"
        + f"<p class='caption'>{len(checked['checks'])} checks ran: "
        + ", ".join(f"<code>{escape(name)}</code>" for name in checked["checks"])
        + ". A count of zero errors means nothing without these; they are what says the "
        "check ran at all.</p>"
        + f"<h2>Errors: {len(errors)}</h2>"
        + (
            _issue_rows(errors, "e")
            if errors
            else "<p class='muted'>None. The map is fit to convert.</p>"
        )
        + f"<h2>Warnings: {len(warnings)}</h2>"
        + (
            "<p class='caption'>True, and judged not applicable by the review. Listed, "
            "not counted against the map.</p>" + _issue_rows(warnings, "w")
            if warnings
            else "<p class='muted'>None.</p>"
        )
        + "<h2>Network edges</h2>"
        + f"<p>Nothing leaves {boundary['lanes_without_exit']} lane(s); nothing enters "
        f"{boundary['lanes_without_entry']}. Of those, "
        f"<b>{boundary['lanes_at_the_extract_edge']}</b> stop where the source road also "
        "stops.</p>"
        + "<p class='caption'>Those are drawn blue. They should form the rim of the "
        "extract - any blue lane in the middle of the network means the boundary rule is "
        "wrong, and that is what this map is for.</p>"
        + f"<p>Routing components: {len(boundary['routing_components'])} "
        f"(sizes <code>{escape(json.dumps(boundary['routing_components']))}</code>)</p>"
        + "<h2>Legend</h2>"
        + legend
    )


def render_validation_html(
    *, model: PreliminaryLaneModel, report: dict[str, Any], boundary_lane_ids: set[str]
) -> str:
    """Draw the reviewed map, each lane coloured by what validation made of it."""
    transformer = Transformer.from_crs(
        model.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )
    # Only lanes and connectors get drawn, so an issue against a signal or a stop line has
    # nothing to fly to. Those rows are still listed - the issue is real - but they are
    # marked, because a row that looks clickable and does nothing reads as a broken page.
    drawable = {lane.identifier for lane in model.lanes} | {
        connector.identifier for connector in model.connectors
    }
    flagged: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    for prefix, bucket in (("e", report["errors"]), ("w", report["warnings"])):
        for index, issue in enumerate(bucket):
            features = _issue_features(issue)
            issue["_locatable"] = any(item in drawable for item in features)
            issues.append({"key": f"{prefix}-{index}", "features": features})
            for identifier in features:
                # An error outranks a warning on the same feature: the worse verdict is
                # the one a reader has to act on.
                if prefix == "e" or identifier not in flagged:
                    flagged[identifier] = "error" if prefix == "e" else "warning"

    features: list[dict[str, Any]] = []
    for lane in sorted(model.lanes, key=lambda item: item.identifier):
        role = flagged.get(lane.identifier) or (
            "boundary" if lane.identifier in boundary_lane_ids else "clean"
        )
        features.append(
            {
                "id": lane.identifier,
                "kind": f"lane {lane.lane_index}/{lane.lane_count} of way "
                f"{', '.join(lane.source_way_ids)}",
                "role": role,
                "line": _lonlat(lane.centerline, transformer),
                "note": (
                    "stops where the source road also stops - the edge of the extract"
                    if role == "boundary"
                    else ""
                ),
            }
        )
    for connector in sorted(model.connectors, key=lambda item: item.identifier):
        role = flagged.get(connector.identifier)
        if role is None:
            continue
        features.append(
            {
                "id": connector.identifier,
                "kind": f"{connector.movement} at node {connector.junction_node_id}",
                "role": role,
                "line": _lonlat(connector.centerline, transformer),
                "note": "",
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

    data = json.dumps(
        {"center": center, "bounds": bounds, "features": features, "issues": issues},
        separators=(",", ":"),
    )
    # A literal `</script>` inside the payload would end the block early.
    data = data.replace("</", "<\\/")
    return _TEMPLATE % {"side": _side_panel(report), "data": data}
