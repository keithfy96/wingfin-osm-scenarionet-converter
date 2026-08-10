# ruff: noqa: E501
"""The Stage 6 reachability map.

Separate from `conversion` so that module keeps a real line-length check instead of
suppressing one for the sake of an embedded HTML template - the same split
`validation_view` makes for Stage 5 and `comparison_view` for Stage 4.

The scenario's `metadata.routing` records one sentence: the best starting lane and how
many lanes it reaches. As a number that is a claim to take on faith, and a misleading one -
`junction-1`'s 285 lanes are joined by 294 edges, so "reaches 79" describes a long thread,
not a network. Drawn, with each step of the search coloured by distance, the shape is
unmissable, and anyone about to spend GPU time driving a route in MetaDrive can pick a
start and an end that a car can actually get between.

The search runs in the browser, over the same adjacency this page is handed. That is the
whole correctness argument: `convert_scenario` computes `_lane_neighbours` once and gives
the identical object to the pickle and to this page, so the page cannot draw a graph the
dataset does not contain. `test_the_page_carries_the_same_graph_the_scenario_does` pins it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pyproj import Transformer

# Private helper imported across modules on purpose, for the reason `validation_view`
# gives: this is the exact projection every other stage page uses, and a Stage 6 copy
# would be a second implementation to keep in step.
from osm_scenario.comparison_view import _lonlat
from osm_scenario.lane_model import PreliminaryLaneModel

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 6 - where you can drive to</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:13px/1.5 system-ui,sans-serif;color:#212529}
  #wrap{display:flex;height:100%%}
  #map{flex:1;min-width:0}
  #side{width:360px;overflow-y:auto;padding:16px;border-left:1px solid #dee2e6;background:#f8f9fa}
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
  .verdict{font-size:15px;font-weight:600;padding:6px 10px;border-radius:3px;margin:0 0 4px;background:#d0ebff;color:#1864ab}
  select{width:100%%;padding:4px;font:inherit;margin-bottom:6px}
  .toggle{display:flex;gap:0;margin-bottom:8px}
  .toggle button{flex:1;padding:5px;font:inherit;cursor:pointer;border:1px solid #ced4da;background:#fff;color:#495057}
  .toggle button.on{background:#1c7ed6;border-color:#1c7ed6;color:#fff}
  .toggle button:first-child{border-radius:3px 0 0 3px}
  .toggle button:last-child{border-radius:0 3px 3px 0}
  .lrow{display:flex;justify-content:space-between;gap:8px;padding:3px 6px;border-left:3px solid transparent;cursor:pointer;border-radius:2px}
  .lrow:hover{background:#e9ecef}
  .lrow.sel{background:#d0ebff;border-left-color:#1c7ed6}
  .lrow.dead span.n{color:#c92a2a}
  .lrow span.n{font-variant-numeric:tabular-nums;color:#495057}
  #lanes{max-height:340px;overflow-y:auto;border:1px solid #e9ecef;border-radius:3px;background:#fff}
  #layers{font-variant-numeric:tabular-nums;font-size:11px;color:#495057;word-spacing:.2em}
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">
<h1>Stage 6 - where you can drive to</h1>
<p class='caption'>Pick a lane. The map colours everywhere a car can get to from it, by how many
lanes it has to cross to arrive. Click any lane on the map to start there instead.</p>
<p class='verdict' id='verdict'>&nbsp;</p>
<p class='muted' id='selected'>&nbsp;</p>
<div class='toggle'>
  <button id='btn-fwd' class='on'>can drive to</button>
  <button id='btn-rev'>can be reached from</button>
</div>
<h2>Selection</h2>
<table id='facts'></table>
<p class='caption'>Lanes found at each step, first step first. A row of ones is a single road with
no choices on it, however long the total.</p>
<p id='layers'></p>
<h2>Start from</h2>
<select id='way'></select>
<div id='lanes'></div>
<p class='caption' id='waynote'></p>
<h2>Steps away</h2>
<div id='legend'></div>
<h2>Read this before planning a route</h2>
%(caveats)s
</div></div>
<script>
const DATA = %(data)s;
const LANES = DATA.lanes;
const BY_ID = {};
for (const lane of LANES) BY_ID[lane.id] = lane;

// The reverse graph, built here rather than shipped: it is the same 294 edges read the
// other way round, and a second copy in the payload could disagree with the first.
const FWD = {}, REV = {};
for (const lane of LANES) { FWD[lane.id] = lane.exits; REV[lane.id] = []; }
for (const lane of LANES) for (const target of lane.exits) if (REV[target]) REV[target].push(lane.id);

const BANDS = [
  {upto: 2,        color: '#c92a2a', label: '1-2'},
  {upto: 5,        color: '#e8590c', label: '3-5'},
  {upto: 10,       color: '#f59f00', label: '6-10'},
  {upto: 20,       color: '#2f9e44', label: '11-20'},
  {upto: Infinity, color: '#1971c2', label: '21 or more'}
];
const START_COLOR = '#7048e8';
const UNREACHED = {color:'#ced4da', weight:2, opacity:.55};

function bandFor(steps) { for (const band of BANDS) if (steps <= band.upto) return band; }

/* Breadth-first, so `steps` is the fewest lanes a car must cross to arrive. Same search
   the scenario's routing metadata reports, run over the same adjacency. */
function search(startId, graph) {
  const steps = {}; steps[startId] = 0;
  let frontier = [startId];
  const layers = [];
  while (frontier.length) {
    const next = [];
    for (const id of frontier) for (const target of (graph[id] || [])) {
      if (steps[target] === undefined) { steps[target] = steps[id] + 1; next.push(target); }
    }
    if (next.length) layers.push(next.length);
    frontier = next;
  }
  return {steps: steps, layers: layers};
}

const map = L.map('map', {preferCanvas:true}).setView(DATA.center, 17);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:20, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
const layerById = {};
for (const lane of LANES) {
  const line = L.polyline(lane.line, UNREACHED).addTo(map);
  line.bindPopup(function () {
    const found = search(lane.id, FWD);
    return '<b>' + lane.label + '</b><br><code>' + lane.id + '</code><br>reaches '
      + (Object.keys(found.steps).length - 1) + ' lane(s)<br>'
      + '<a href="#lane=' + lane.id + '">start here</a>';
  });
  line.on('click', function () { select(lane.id); });
  layerById[lane.id] = line;
}
if (DATA.bounds) map.fitBounds(DATA.bounds, {padding:[24,24]});

let forward = true;
let current = null;

function text(id, value) { document.getElementById(id).textContent = value; }

function select(laneId, keepView) {
  if (!BY_ID[laneId]) return;
  current = laneId;
  const lane = BY_ID[laneId];
  const found = search(laneId, forward ? FWD : REV);
  const reached = Object.keys(found.steps).filter(function (id) { return id !== laneId; });
  const ways = {};
  for (const id of reached) for (const way of BY_ID[id].ways) ways[way] = true;

  for (const other of LANES) {
    const steps = found.steps[other.id];
    if (other.id === laneId) {
      layerById[other.id].setStyle({color:START_COLOR, weight:8, opacity:1});
    } else if (steps === undefined) {
      layerById[other.id].setStyle(UNREACHED);
    } else {
      layerById[other.id].setStyle({color: bandFor(steps).color, weight:5, opacity:.95});
    }
  }
  layerById[laneId].bringToFront();

  const verb = forward ? 'can drive to' : 'can be reached from';
  text('verdict', verb + ' ' + reached.length + ' of ' + (LANES.length - 1) + ' other lanes');
  text('selected', lane.label + '  \\u00b7  ' + laneId);
  document.getElementById('facts').innerHTML =
      "<tr><td>lanes " + verb + "</td><td class='n'>" + reached.length + "</td></tr>"
    + "<tr><td>ways they lie on</td><td class='n'>" + Object.keys(ways).length + "</td></tr>"
    + "<tr><td>furthest, in lanes crossed</td><td class='n'>" + found.layers.length + "</td></tr>"
    + "<tr><td>lanes leaving this one</td><td class='n'>" + lane.exits.length + "</td></tr>"
    + "<tr><td>lanes entering this one</td><td class='n'>" + REV[laneId].length + "</td></tr>";
  text('layers', found.layers.length ? found.layers.join('  ') : 'nothing at all');

  for (const row of document.querySelectorAll('.lrow')) {
    row.classList.toggle('sel', row.dataset.lane === laneId);
  }
  if (document.getElementById('way').value !== lane.ways[0]) {
    document.getElementById('way').value = lane.ways[0];
    fillLanes(lane.ways[0]);
  }
  if (!keepView) {
    let box = layerById[laneId].getBounds();
    for (const id of reached) box = box.extend(layerById[id].getBounds());
    map.fitBounds(box, {padding:[48,48], maxZoom:18});
  }
  if (window.location.hash !== '#lane=' + laneId) {
    history.replaceState(null, '', '#lane=' + laneId);
  }
}

function fillLanes(wayId) {
  const rows = LANES.filter(function (lane) { return lane.ways.indexOf(wayId) !== -1; });
  const counts = rows.map(function (lane) {
    return {lane: lane, reach: Object.keys(search(lane.id, FWD).steps).length - 1};
  });
  counts.sort(function (a, b) { return b.reach - a.reach; });
  document.getElementById('lanes').innerHTML = counts.map(function (item) {
    return "<div class='lrow" + (item.reach === 0 ? ' dead' : '')
      + (item.lane.id === current ? ' sel' : '') + "' data-lane='" + item.lane.id + "'>"
      + '<span>' + item.lane.short + '</span>'
      + "<span class='n'>" + item.reach + '</span></div>';
  }).join('');
  for (const row of document.querySelectorAll('#lanes .lrow')) {
    row.addEventListener('click', function () { select(row.dataset.lane); });
  }
  const reaches = counts.map(function (item) { return item.reach; });
  text('waynote', rows.length + ' lane(s) on this way, reaching from '
    + Math.min.apply(null, reaches) + ' to ' + Math.max.apply(null, reaches)
    + ' lanes. A way is not one road you drive - it is however many segments the junctions '
    + 'cut it into, and they do not all go the same places.');
}

document.getElementById('legend').innerHTML =
  "<div class='key'><span class='sw' style='border-top-color:" + START_COLOR + "'></span>the lane you picked</div>"
  + BANDS.map(function (band) {
      return "<div class='key'><span class='sw' style='border-top-color:" + band.color + "'></span>"
        + band.label + ' lanes crossed to get there</div>';
    }).join('')
  + "<div class='key'><span class='sw' style='border-top-color:" + UNREACHED.color + "'></span>no route at all</div>";

const waySelect = document.getElementById('way');
waySelect.innerHTML = DATA.ways.map(function (way) {
  return "<option value='" + way.id + "'>way " + way.id + '  -  ' + way.lanes + ' lane(s)</option>';
}).join('');
waySelect.addEventListener('change', function () { fillLanes(waySelect.value); });

document.getElementById('btn-fwd').addEventListener('click', function () {
  forward = true; this.classList.add('on');
  document.getElementById('btn-rev').classList.remove('on');
  if (current) select(current, true);
});
document.getElementById('btn-rev').addEventListener('click', function () {
  forward = false; this.classList.add('on');
  document.getElementById('btn-fwd').classList.remove('on');
  if (current) select(current, true);
});

const asked = /^#lane=(\\w+)$/.exec(window.location.hash);
const start = (asked && BY_ID[asked[1]]) ? asked[1] : DATA.default_lane;
waySelect.value = BY_ID[start].ways[0];
fillLanes(BY_ID[start].ways[0]);
select(start);
</script>
</body>
</html>
"""


def _edge_name(source_edge: list[str]) -> str:
    """The stretch of road a lane sits on, as the two OSM nodes it runs between.

    `source_edge` is the OSMnx multigraph key - two node ids and a counter that separates
    parallel edges between the same pair. The counter is 0 for all 285 lanes in
    `junction-1`, so it is shown only when it is doing work; printed always it is noise on
    every row of the lane list.
    """
    if len(source_edge) >= 3 and source_edge[2] != "0":
        return f"{source_edge[0]} → {source_edge[1]} #{source_edge[2]}"
    return f"{source_edge[0]} → {source_edge[1]}" if len(source_edge) >= 2 else "?"


def _caveats(routing: dict[str, Any], lane_count: int) -> str:
    """The facts that stop the map being read as a better-connected network than it is."""
    pairs = routing["possible_lane_pairs"]
    share = (routing["reachable_lane_pairs"] / pairs * 100) if pairs else 0.0
    return (
        f"<p>Of the {pairs:,} journeys you could ask for between two lanes here, "
        f"<b>{routing['reachable_lane_pairs']:,}</b> exist - about "
        f"<b>{share:.0f}%</b>. Pick a destination at random and it is probably not "
        "reachable from where you started.</p>"
        f"<p><b>{routing['lanes_reaching_nothing']}</b> of {lane_count} lanes lead "
        "nowhere at all: start on one and the car has no next lane. Most are lanes that "
        "run off the edge of the downloaded area rather than mistakes.</p>"
        "<p>The typical lane reaches "
        f"<b>{routing['median_reach']:.0f}</b> others. The best reaches "
        f"<b>{routing['best_start_reaches']}</b>, and that is the lane this page opens "
        "on.</p>"
        "<p class='caption'>Stage 5 counts this map in pieces too, and gets a far smaller "
        "number. That count ignores one-way direction, which is right for asking whether "
        "the map hangs together and wrong for asking whether a car can drive it. "
        "Respecting direction there are "
        f"<b>{routing['components_respecting_direction']['count']}</b> pieces, the largest "
        f"holding {routing['components_respecting_direction']['largest']} lanes. Neither "
        "number is a defect; they answer different questions.</p>"
    )


def render_reachability_html(
    *,
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    routing: dict[str, Any],
) -> str:
    """Draw the reviewed map so a person can pick a start and see where it leads.

    `neighbours` is passed in rather than recomputed: it must be the same object the
    scenario was built from, or the page and the dataset can disagree about the network.
    """
    transformer = Transformer.from_crs(
        model.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )

    lanes: list[dict[str, Any]] = []
    for lane in sorted(model.lanes, key=lambda item: item.identifier):
        ways = list(lane.source_way_ids)
        edge = _edge_name(lane.source_edge)
        lanes.append(
            {
                "id": lane.identifier,
                "ways": ways,
                # `lane_index`/`lane_count` alone do not tell two segments of the same way
                # apart - most of `junction-1` is idx0/1 - so the pair of OSM nodes the
                # segment runs between is what actually identifies it on the page.
                "short": f"idx{lane.lane_index}/{lane.lane_count} {lane.direction} · {edge}",
                "label": (
                    f"way {', '.join(ways)} · lane {lane.lane_index}/{lane.lane_count} · "
                    f"{lane.direction} · between OSM nodes {edge}"
                ),
                "line": _lonlat(lane.centerline, transformer),
                "exits": list(neighbours[lane.identifier][1]),
            }
        )

    way_counts: dict[str, int] = {}
    for entry in lanes:
        for way in entry["ways"]:
            way_counts[way] = way_counts.get(way, 0) + 1
    ways = [
        {"id": way, "lanes": count}
        for way, count in sorted(way_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    points = [point for entry in lanes for point in entry["line"]]
    if points:
        lats = [point[0] for point in points]
        lons = [point[1] for point in points]
        center = [(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
    else:
        center, bounds = [0.0, 0.0], None

    data = json.dumps(
        {
            "lanes": lanes,
            "ways": ways,
            "center": center,
            "bounds": bounds,
            "default_lane": routing["best_start_lane_id"],
        },
        separators=(",", ":"),
    )
    # A literal `</script>` inside the payload would end the block early.
    data = data.replace("</", "<\\/")
    return _TEMPLATE % {"data": data, "caveats": _caveats(routing, len(lanes))}
