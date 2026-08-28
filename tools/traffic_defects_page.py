# ruff: noqa: E501
"""Draw every traffic-line defect on the map, so a person can look instead of reading tables.

Reads `lane-model/reviewed.json` and `traffic/traffic.json`, measures the *drawn* lines the
same way the session's analysis did, and writes `inspection/traffic-defects.html` - the same
Leaflet-over-OSM page the stage pages use, with one toggleable layer per defect class:

- **U-turn stretches**: >= 140 deg of heading change inside 30 m of arc, with how many
  routes take the spot and the tightest radius on it.
- **Bends under the car's steering lock** (2.9424 m - wheelbase 2.469 / tan 40 deg): a car
  physically cannot follow the line there, whatever its speed.
- **Wrong-way runs**: the drawn line inside a lane's width while opposed to it by > 120 deg.
- **Off-road runs**: the drawn line more than a texel (0.125 m) outside the sealed road
  surface - the same union `conversion.py` exports.
- **Forbidden reverse connectors** near any flagged spot, because a U-turn assembled from
  two legal lefts stands right next to the direct reverse the model forbids.

Regenerate after any fix and compare: the page is the visual form of the acceptance metric.

Usage:
    uv run python tools/traffic_defects_page.py workspaces/junction-1 workspaces/mosque
"""

from __future__ import annotations

import html
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shapely import STRtree  # noqa: E402
from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from osm_scenario.conversion import (  # noqa: E402
    _lane_change_moves,
    _lane_neighbours,
    _map_features,
    _road_union,
    _sealed_surfaces,
    _stub_lanes,
)
from osm_scenario.ego_route import plan_route  # noqa: E402
from osm_scenario.lane_model import PreliminaryLaneModel  # noqa: E402
from osm_scenario.lane_payload import build_lane_payload  # noqa: E402

CAR_LOCK_RADIUS_M = 2.9424
UTURN_DEG = 140.0
UTURN_WINDOW_M = 30.0
RUN_MIN_M = 2.0
TEXEL_M = 0.125


def _resample(poly: np.ndarray, step: float = 1.0) -> np.ndarray:
    gaps = np.linalg.norm(np.diff(poly, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(gaps)])
    if arc[-1] < step:
        return poly
    grid = np.arange(0.0, arc[-1], step)
    return np.column_stack([np.interp(grid, arc, poly[:, 0]), np.interp(grid, arc, poly[:, 1])])


def _radii(points: np.ndarray) -> np.ndarray:
    """Circumradius per interior vertex of a 0.5 m-resampled line."""
    a, b, c = points[:-2], points[1:-1], points[2:]
    ab = np.linalg.norm(b - a, axis=1)
    bc = np.linalg.norm(c - b, axis=1)
    ca = np.linalg.norm(c - a, axis=1)
    cross = np.abs((b - a)[:, 0] * (c - a)[:, 1] - (b - a)[:, 1] * (c - a)[:, 0])
    with np.errstate(divide="ignore", invalid="ignore"):
        return ab * bc * ca / np.maximum(2 * cross, 1e-12)


class _Measured:
    """Everything the page shows for one workspace, in planar model coordinates."""

    def __init__(self, workspace: Path) -> None:
        self.model = PreliminaryLaneModel.model_validate(
            json.loads((workspace / "lane-model" / "reviewed.json").read_text())
        )
        self.neighbours = _lane_neighbours(self.model)
        self.moves = _lane_change_moves(self.model)
        built = _map_features(self.model, self.neighbours, self.moves)
        features = built.features
        _sealed_surfaces(features)
        surfaces = [
            Polygon(np.asarray(f["polygon"]))
            for f in features.values()
            if f.get("polygon") is not None and len(f["polygon"]) >= 3
        ]
        self.road = _road_union(surfaces)
        stubs = _stub_lanes(self.model)
        self.lane_lines: list[LineString] = []
        self.lane_meta: list[tuple[str, float]] = []
        for lane in self.model.lanes:
            if lane.identifier in stubs:
                continue
            pts = np.array([(p.x, p.y) for p in lane.centerline])
            if len(pts) < 2:
                continue
            self.lane_lines.append(LineString(pts))
            self.lane_meta.append((lane.identifier, lane.width_m))
        self.tree = STRtree(self.lane_lines)
        self.traffic = json.loads((workspace / "traffic" / "traffic.json").read_text())
        self.chains: dict[str, list[str]] = {}
        for route in self.traffic["routes"]:
            try:
                planned = plan_route(
                    model=self.model,
                    neighbours=self.neighbours,
                    moves=self.moves,
                    name=route["name"],
                    start_lane=route["start_lane"],
                    end_lane=route["end_lane"],
                )
                self.chains[route["name"]] = list(planned.lanes)
            except Exception:  # noqa: BLE001 - a chain is context for a popup, never a gate
                self.chains[route["name"]] = []

    def _wrong_way_sample(self, point: np.ndarray, heading: np.ndarray) -> bool:
        p = Point(point)
        index = int(self.tree.nearest(p))
        line = self.lane_lines[index]
        _, width = self.lane_meta[index]
        if line.distance(p) > width / 2 + 0.5:
            return False
        along = line.project(p)
        before = line.interpolate(max(0.0, along - 0.5))
        after = line.interpolate(min(line.length, along + 0.5))
        direction = np.array([after.x - before.x, after.y - before.y])
        norm = float(np.linalg.norm(direction))
        return norm > 1e-9 and float(np.dot(direction / norm, heading)) < -0.5

    def runs(self) -> tuple[list[dict], list[dict]]:
        """Wrong-way and off-road runs >= RUN_MIN_M, as line stretches per route."""
        wrong, off = [], []
        for route in self.traffic["routes"]:
            samples = _resample(np.array(route["polyline"]))
            headings = np.diff(samples, axis=0)
            headings = headings / np.maximum(np.linalg.norm(headings, axis=1, keepdims=True), 1e-9)
            state = {"wrong": None, "off": None}
            sinks = {"wrong": wrong, "off": off}
            for i in range(len(headings)):
                flags = {
                    "wrong": self._wrong_way_sample(samples[i], headings[i]),
                    "off": self.road.distance(Point(samples[i])) > TEXEL_M,
                }
                for kind, flagged in flags.items():
                    if flagged:
                        if state[kind] is None:
                            state[kind] = [samples[i]]
                        else:
                            state[kind].append(samples[i])
                    elif state[kind] is not None:
                        if len(state[kind]) >= RUN_MIN_M:
                            sinks[kind].append({"route": route["name"], "points": np.array(state[kind])})
                        state[kind] = None
            for kind in state:
                if state[kind] is not None and len(state[kind]) >= RUN_MIN_M:
                    sinks[kind].append({"route": route["name"], "points": np.array(state[kind])})
        return wrong, off

    def sublock_spots(self) -> list[dict]:
        """Places any route bends tighter than the car's lock, clustered within 12 m."""
        spots: list[dict] = []
        for route in self.traffic["routes"]:
            dense = _resample(np.array(route["polyline"]), 0.5)
            if len(dense) < 3:
                continue
            radii = _radii(dense)
            for i in np.where(radii < CAR_LOCK_RADIUS_M)[0]:
                where = dense[1:-1][i]
                for spot in spots:
                    if np.linalg.norm(spot["pos"] - where) < 12.0:
                        spot["routes"].add(route["name"])
                        if radii[i] < spot["radius"]:
                            spot["radius"] = float(radii[i])
                            spot["pos"] = where
                        break
                else:
                    spots.append({"pos": where, "radius": float(radii[i]), "routes": {route["name"]}})
        for spot in spots:
            spot["near"] = self._nearest_lane_label(spot["pos"])
            spot["chains"] = self._chains_through(spot["pos"], spot["routes"])
        return spots

    def uturn_spots(self) -> list[dict]:
        """Stretches where a route's heading swings >= UTURN_DEG inside UTURN_WINDOW_M."""
        spots: list[dict] = []
        window = int(UTURN_WINDOW_M)
        for route in self.traffic["routes"]:
            samples = _resample(np.array(route["polyline"]))
            steps = np.diff(samples, axis=0)
            headings = np.arctan2(steps[:, 1], steps[:, 0])
            for i in range(len(headings) - window):
                swung = abs(math.degrees((headings[i + window] - headings[i] + math.pi) % (2 * math.pi) - math.pi))
                if swung < UTURN_DEG:
                    continue
                stretch = samples[i : i + window + 1]
                dense = _resample(stretch, 0.5)
                tightest = float(_radii(dense).min()) if len(dense) >= 3 else float("inf")
                centre = stretch[len(stretch) // 2]
                for spot in spots:
                    if np.linalg.norm(spot["pos"] - centre) < 20.0:
                        spot["routes"].add(route["name"])
                        spot["turn"] = max(spot["turn"], swung)
                        spot["radius"] = min(spot["radius"], tightest)
                        break
                else:
                    spots.append(
                        {"pos": centre, "points": stretch, "turn": swung, "radius": tightest, "routes": {route["name"]}}
                    )
                break  # one report per route per spot; the cluster gathers the rest
        for spot in spots:
            spot["chains"] = self._chains_through(spot["pos"], spot["routes"])
        return spots

    def reverse_connectors_near(self, spots: list[np.ndarray]) -> list[dict]:
        out = []
        for connector in self.model.connectors:
            if connector.movement != "reverse" or connector.status != "forbidden":
                continue
            pts = np.array([(p.x, p.y) for p in connector.centerline]) if connector.centerline else None
            if pts is None or not len(pts):
                continue
            if any(float(np.min(np.linalg.norm(pts - s, axis=1))) < 40.0 for s in spots):
                out.append(
                    {
                        "points": pts,
                        "label": f"forbidden reverse {connector.identifier[:8]}: "
                        f"{connector.from_lane_id[:8]} → {connector.to_lane_id[:8]} "
                        f"({connector.turn_angle_degrees:.0f}°, node {connector.junction_node_id})",
                    }
                )
        return out

    def _nearest_lane_label(self, pos: np.ndarray) -> str:
        p = Point(pos)
        index = int(self.tree.nearest(p))
        lane_id, _ = self.lane_meta[index]
        lane = next(lane for lane in self.model.lanes if lane.identifier == lane_id)
        return f"lane {lane_id[:8]} (ways {', '.join(lane.source_way_ids)})"

    def _chains_through(self, pos: np.ndarray, routes: set[str]) -> list[str]:
        lanes = {lane.identifier: lane for lane in self.model.lanes}
        rendered = []
        for name in sorted(routes):
            chain = self.chains.get(name, [])
            near = []
            for lane_id in chain:
                pts = np.array([(p.x, p.y) for p in lanes[lane_id].centerline])
                if float(np.min(np.linalg.norm(pts - pos, axis=1))) < 25.0:
                    near.append(lane_id[:8])
            if near:
                rendered.append(f"{name}: {' → '.join(near)}")
        return rendered


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Traffic defects - %(workspace)s</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:13px/1.5 system-ui,sans-serif;color:#212529}
  #wrap{display:flex;height:100%%}
  #map{flex:1;min-width:0}
  #side{width:400px;overflow-y:auto;padding:16px;border-left:1px solid #dee2e6;background:#f8f9fa}
  h1{font-size:16px;margin:0 0 4px}
  h2{font-size:13px;margin:18px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#495057}
  .muted{color:#868e96}
  code{font-size:11px;background:#e9ecef;padding:1px 4px;border-radius:3px;word-break:break-all}
  .key{display:flex;align-items:center;gap:8px;margin:3px 0}
  .sw{width:22px;height:0;border-top-width:5px;border-top-style:solid;flex:none}
  label.layer{display:flex;align-items:center;gap:8px;margin:4px 0;cursor:pointer}
  ul{margin:4px 0;padding-left:18px}
  li{margin:2px 0}
  .popchain{font-size:11px;margin:2px 0}
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="side">
    <h1>Traffic-line defects — %(workspace)s</h1>
    <p class="muted">Measured on <code>traffic/traffic.json</code> (seed %(seed)s, %(count)s routes)
    against the reviewed lane model and the sealed road surface the converter exports.
    These are the <b>drawn</b> lines the traffic cars are asked to follow; a car tracking a
    bend under its %(lock).2f m steering lock leaves the line however slowly it goes.</p>
    <h2>Layers</h2>
    <div id="layers"></div>
    <h2>Findings</h2>
    <div id="findings"></div>
  </div>
</div>
<script>window.__DEFECTS__=%(data)s;</script>
<script>
(function(){
  const D = window.__DEFECTS__;
  const map = L.map('map', {preferCanvas: true});
  map.setView(D.center, 16);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom: 20, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
  if (D.bounds) map.fitBounds(L.latLngBounds(D.bounds));

  const laneLayer = L.layerGroup();
  for (const lane of D.lanes) {
    L.polyline(lane.line, {color:'#adb5bd', weight:2, opacity:0.7})
      .bindPopup(`<div>${lane.label}</div><code>${lane.id}</code>`).addTo(laneLayer);
  }

  function group(items, style, popup) {
    const layer = L.layerGroup();
    for (const item of items) {
      L.polyline(item.line, style).bindPopup(popup(item)).addTo(layer);
    }
    return layer;
  }
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  const chains = item => (item.chains||[]).map(c => `<div class="popchain"><code>${esc(c)}</code></div>`).join('');

  const uturnLayer = L.layerGroup();
  for (const u of D.uturns) {
    L.polyline(u.line, {color:'#7048e8', weight:7, opacity:0.9}).addTo(uturnLayer);
    L.circleMarker(u.pos, {radius:9, color:'#7048e8', fillColor:'#7048e8', fillOpacity:0.6})
      .bindPopup(`<b>U-turn: ${u.turn.toFixed(0)}&deg; inside 30 m</b><br>` +
                 `${u.routes.length} route(s), tightest radius ${u.radius.toFixed(2)} m<br>` + chains(u))
      .addTo(uturnLayer);
  }

  const sublockLayer = L.layerGroup();
  for (const s of D.sublock) {
    L.circleMarker(s.pos, {radius:9, color:'#c92a2a', fillColor:'#c92a2a', fillOpacity:0.7})
      .bindPopup(`<b>Bend under the steering lock: r ${s.radius.toFixed(2)} m</b><br>` +
                 `${s.routes.length} route(s) &middot; ${esc(s.near)}<br>` + chains(s))
      .addTo(sublockLayer);
  }

  const wrongLayer = group(D.wrongway, {color:'#e03131', weight:5, opacity:0.9},
    w => `<b>wrong-way ${w.m.toFixed(0)} m</b> on ${w.route}`);
  const offLayer = group(D.offroad, {color:'#f59f00', weight:5, opacity:0.9},
    o => `<b>off-road ${o.m.toFixed(0)} m</b> on ${o.route}`);
  const reverseLayer = group(D.reverse, {color:'#0b7285', weight:4, opacity:0.9, dashArray:'6 6'},
    r => esc(r.label));

  const layers = [
    ['Lanes', laneLayer, '#adb5bd', true],
    [`U-turn stretches (${D.uturns.length})`, uturnLayer, '#7048e8', true],
    [`Bends under the lock (${D.sublock.length})`, sublockLayer, '#c92a2a', true],
    [`Wrong-way runs (${D.wrongway.length})`, wrongLayer, '#e03131', true],
    [`Off-road runs (${D.offroad.length})`, offLayer, '#f59f00', true],
    [`Forbidden reverse connectors (${D.reverse.length})`, reverseLayer, '#0b7285', true],
  ];
  const layersDiv = document.getElementById('layers');
  for (const [name, layer, colour, on] of layers) {
    if (on) layer.addTo(map);
    const label = document.createElement('label');
    label.className = 'layer';
    const box = document.createElement('input');
    box.type = 'checkbox'; box.checked = on;
    box.addEventListener('change', () => box.checked ? layer.addTo(map) : map.removeLayer(layer));
    const sw = document.createElement('span');
    sw.className = 'sw'; sw.style.borderTopColor = colour;
    label.append(box, sw, document.createTextNode(name));
    layersDiv.append(label);
  }

  const findings = document.getElementById('findings');
  function section(title, rows, focus) {
    const h = document.createElement('h2'); h.textContent = title;
    const ul = document.createElement('ul');
    for (const [text, pos] of rows) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = '#'; a.textContent = text;
      a.addEventListener('click', e => { e.preventDefault(); map.setView(pos, 18); });
      li.append(a); ul.append(li);
    }
    if (rows.length) { findings.append(h, ul); }
  }
  section('U-turns', D.uturns.map(u => [
    `${u.turn.toFixed(0)}° by ${u.routes.length} routes, r ${u.radius.toFixed(2)} m`, u.pos]));
  section('Bends under the lock', D.sublock.map(s => [
    `r ${s.radius.toFixed(2)} m, ${s.routes.length} routes`, s.pos]));
  section('Wrong-way runs', D.wrongway.map(w => [`${w.m.toFixed(0)} m on ${w.route}`, w.line[0]]));
  section('Off-road runs', D.offroad.map(o => [`${o.m.toFixed(0)} m on ${o.route}`, o.line[0]]));
})();
</script>
</body>
</html>
"""


def _build_page(workspace: Path) -> Path:
    from pyproj import Transformer

    measured = _Measured(workspace)
    transformer = Transformer.from_crs(
        measured.model.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )

    def latlng(points: np.ndarray) -> list[list[float]]:
        lon, lat = transformer.transform(points[:, 0], points[:, 1])
        return [[float(a), float(b)] for a, b in zip(lat, lon, strict=True)]

    payload = build_lane_payload(model=measured.model, neighbours=measured.neighbours, moves=measured.moves)
    wrong, off = measured.runs()
    sublock = measured.sublock_spots()
    uturns = measured.uturn_spots()
    flagged = [s["pos"] for s in sublock] + [u["pos"] for u in uturns]
    reverse = measured.reverse_connectors_near(flagged)

    data = {
        "center": payload["center"],
        "bounds": payload.get("bounds"),
        "lanes": [{"id": lane["id"], "line": lane["line"], "label": lane["label"]} for lane in payload["lanes"]],
        "uturns": [
            {
                "line": latlng(u["points"]),
                "pos": latlng(u["pos"].reshape(1, 2))[0],
                "turn": u["turn"],
                "radius": u["radius"],
                "routes": sorted(u["routes"]),
                "chains": u["chains"],
            }
            for u in uturns
        ],
        "sublock": [
            {
                "pos": latlng(s["pos"].reshape(1, 2))[0],
                "radius": s["radius"],
                "routes": sorted(s["routes"]),
                "near": s["near"],
                "chains": s["chains"],
            }
            for s in sublock
        ],
        "wrongway": [
            {"line": latlng(w["points"]), "route": w["route"], "m": float(len(w["points"]))} for w in wrong
        ],
        "offroad": [
            {"line": latlng(o["points"]), "route": o["route"], "m": float(len(o["points"]))} for o in off
        ],
        "reverse": [{"line": latlng(r["points"]), "label": r["label"]} for r in reverse],
    }
    rendered = _TEMPLATE % {
        "workspace": html.escape(workspace.name),
        "seed": measured.traffic["generated"].get("seed"),
        "count": len(measured.traffic["routes"]),
        "lock": CAR_LOCK_RADIUS_M,
        "data": json.dumps(data).replace("</", "<\\/"),
    }
    out = workspace / "inspection" / "traffic-defects.html"
    out.write_text(rendered)
    print(
        f"{out}: {len(uturns)} u-turn spot(s), {len(sublock)} sub-lock spot(s), "
        f"{len(wrong)} wrong-way run(s), {len(off)} off-road run(s), "
        f"{len(reverse)} forbidden reverse connector(s) nearby"
    )
    return out


if __name__ == "__main__":
    targets = [Path(arg) for arg in sys.argv[1:]] or [Path("workspaces/junction-1")]
    for target in targets:
        _build_page(target.resolve())
