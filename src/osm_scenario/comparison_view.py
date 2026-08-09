# ruff: noqa: E501
"""The Stage 4 comparison map.

Separate from `apply_review` so that module keeps a real line-length check instead of
suppressing one for the sake of an embedded HTML template.

The report says what changed; this says *where*. A count of forbidden connectors is hard
to judge, but a junction with every movement struck through is obvious at a glance.
"""

from __future__ import annotations

import json
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
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">%(side)s</div></div>
<script>
const DATA = %(data)s;
const STYLE = {
  unchanged:      {color:'#adb5bd', weight:2, opacity:.55},
  added:          {color:'#2b8a3e', weight:4, opacity:.95},
  removed:        {color:'#c92a2a', weight:3, opacity:.9, dashArray:'6 4'},
  forbidden:      {color:'#c92a2a', weight:3, opacity:.9, dashArray:'6 4'},
  activated:      {color:'#2b8a3e', weight:5, opacity:.95},
  review_required:{color:'#f08c00', weight:5, opacity:.95, dashArray:'8 5'},
  stranded:       {color:'#7048e8', weight:6, opacity:.95}
};
const map = L.map('map', {preferCanvas:true}).setView(DATA.center, 17);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:20, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
const groups = {};
for (const feature of DATA.features) {
  const style = STYLE[feature.change] || STYLE.unchanged;
  const layer = L.polyline(feature.line, style)
    .bindPopup('<b>' + feature.change + '</b><br>' + feature.kind + '<br><code>' + feature.id + '</code>'
               + (feature.note ? '<br>' + feature.note : ''));
  (groups[feature.change] = groups[feature.change] || L.layerGroup()).addLayer(layer);
}
const overlays = {};
for (const [name, group] of Object.entries(groups)) { group.addTo(map); overlays[name] = group; }
L.control.layers(null, overlays, {collapsed:false}).addTo(map);
if (DATA.bounds) map.fitBounds(DATA.bounds, {padding:[24,24]});
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


def _side_panel(comparison: dict[str, Any]) -> str:
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
    blocking = "".join(
        f"<li><code>{item['rule']}</code> at {', '.join(item['source_ids'])}</li>"
        for item in created["blocking"]
    )
    legend = "".join(
        f"<div class='key'><span class='sw' style='border-top-color:{colour}'></span>{label}</div>"
        for label, colour in (
            ("unchanged", "#adb5bd"),
            ("added by the review", "#2b8a3e"),
            ("removed / forbidden", "#c92a2a"),
            ("still review_required", "#f08c00"),
            ("lane with no exit left", "#7048e8"),
        )
    )
    return (
        "<h1>Stage 4 - preliminary versus reviewed</h1>"
        "<p class='muted'>What the review did to the map.</p>"
        "<h2>Features</h2>" + rows(comparison["counts"], "feature") + "<h2>Connectors</h2>"
        + rows(comparison["connector_status"], "status")
        + f"<h2>Findings resolved: {resolved['total']}</h2>"
        + f"<h2>Findings created: {created['total']}</h2>"
        + (
            f"<p>{len(created['blocking'])} block promotion:</p><ul>{blocking}</ul>"
            if created["blocking"]
            else "<p class='muted'>None of them block promotion.</p>"
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
            continue  # forbidden before the review too; not this report's subject
        else:
            change = "unchanged"
        features.append(
            {
                "id": connector.identifier,
                "kind": f"{connector.movement} at node {connector.junction_node_id} "
                f"({connector.turn_angle_degrees:+.1f} degrees)",
                "change": change,
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
        {"center": center, "bounds": bounds, "features": features}, separators=(",", ":")
    )
    # A literal `</script>` inside the payload would end the block early.
    data = data.replace("</", "<\\/")
    return _TEMPLATE % {"side": _side_panel(comparison), "data": data}
