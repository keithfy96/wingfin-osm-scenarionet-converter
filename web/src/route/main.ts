// Entry point for the Stage 6 route builder.
//
// The Python renderer writes `window.__ROUTE_PAYLOAD__` and a #map / #side pair, then loads
// this bundle. Click a lane for the start, click another for the end, name the route and add
// it; the downloaded routes.json is what `osm-scenario convert --routes` turns into
// scenarios.

import { routeGeometry, type RouteGeometry } from "./geometry.js";
import { RouteGraph, type FoundRoute } from "./path.js";
import { nameProblem, parseRoutes, RoutesFileError, serializeRoutes } from "./routes-file.js";
import type { ChosenRoute, RouteBuilderPayload, RouteLane } from "./types.js";
import type { LeafletLayer, LeafletMap } from "../types-dom.js";

// Before anything is picked every lane is IDLE, so IDLE has to be legible on its own - the
// page asks for a click on a lane, and a lane nobody can see against OpenStreetMap's own
// road rendering cannot be clicked. FADED is the after-a-start state, where the point is
// the opposite: push the lanes that are no longer candidates into the background.
const IDLE = { color: "#343a40", weight: 3, opacity: 0.75 };
const FADED = { color: "#ced4da", weight: 2, opacity: 0.5 };
const REACHABLE = { color: "#1c7ed6", weight: 3, opacity: 0.8 };
const ROUTE = { color: "#7048e8", weight: 7, opacity: 1 };
const CHANGE = { color: "#f59f00", weight: 7, opacity: 1 };
const START = { color: "#2f9e44", weight: 8, opacity: 1 };
const END = { color: "#c92a2a", weight: 8, opacity: 1 };

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function download(filename: string, contents: string): void {
  const blob = new Blob([contents], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function boot(): void {
  const supplied = (window as unknown as { __ROUTE_PAYLOAD__?: RouteBuilderPayload })
    .__ROUTE_PAYLOAD__;
  const side = document.getElementById("side");
  if (!supplied || !side) return;
  const payload: RouteBuilderPayload = supplied;

  const graph = new RouteGraph(payload.lanes);
  const byId = new Map(payload.lanes.map((lane) => [lane.id, lane]));

  // Canvas hit-testing accepts a click within half the line's weight of it, which for a
  // 3 px lane is a 3 px target - unusably precise for something the page asks you to click.
  // `tolerance` widens the catch without thickening the drawing.
  const renderer = L.canvas({ tolerance: 10 });
  const map: LeafletMap = L.map("map", { preferCanvas: true, renderer });
  map.setView(payload.center, 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  if (payload.bounds) map.fitBounds(L.latLngBounds(payload.bounds));

  let start: string | null = null;
  let end: string | null = null;
  let reachable: Set<string> | null = null;
  let found: FoundRoute | null = null;
  let geometry: RouteGeometry | null = null;
  const chosen: ChosenRoute[] = [];

  // The drive drawn as one line, spliced through junctions and across changes - not just
  // the lanes it uses. It is the difference between showing which roads are involved and
  // showing the path the car takes, and it is the same path the converter builds.
  const drawn: LeafletLayer[] = [];

  const layers = new Map<string, LeafletLayer>();
  for (const lane of payload.lanes) {
    const line = L.polyline(lane.line, { ...IDLE, renderer });
    line.on("click", () => pick(lane));
    line.bindPopup?.(() => {
      const box = element("div");
      box.append(element("div", undefined, lane.label));
      box.append(element("code", undefined, lane.id));
      return box;
    });
    line.addTo(map);
    layers.set(lane.id, line);
  }

  // --- panel ------------------------------------------------------------------------
  const status = element("p", "verdict");
  const detail = element("p", "muted");
  const nameRow = element("div", "namerow");
  const nameInput = element("input");
  nameInput.type = "text";
  nameInput.placeholder = "route name, e.g. kenanga-offramp";
  const addButton = element("button", "primary", "Add route");
  const nameNote = element("p", "caption");
  nameRow.append(nameInput, addButton);

  const list = element("div", "routes");
  const listNote = element("p", "caption");
  const saveButton = element("button", "primary", "Download routes.json");
  const clearButton = element("button", undefined, "Clear selection");
  const loadLabel = element("label", "loadbtn", "Load an existing routes.json");
  const loadInput = element("input");
  loadInput.type = "file";
  loadInput.accept = "application/json,.json";
  loadLabel.append(loadInput);

  const mount = document.getElementById("panel");
  mount?.append(
    status,
    detail,
    clearButton,
    element("h2", undefined, "Name this route"),
    nameRow,
    nameNote,
    element("h2", undefined, "Routes to build"),
    list,
    listNote,
    saveButton,
    loadLabel,
  );

  function styleFor(laneId: string): Record<string, unknown> {
    if (laneId === start) return START;
    if (laneId === end) return END;
    // The route is drawn as its own line on top, so a lane on it only needs to stop
    // fading into the background.
    if (found?.lanes.includes(laneId)) return REACHABLE;
    if (reachable) return reachable.has(laneId) ? REACHABLE : FADED;
    return IDLE;
  }

  function redraw(): void {
    for (const [laneId, layer] of layers) layer.setStyle?.({ ...styleFor(laneId) });
    for (const layer of drawn.splice(0)) layer.remove?.();
    if (geometry) {
      drawn.push(L.polyline(geometry.line, { ...ROUTE, renderer }).addTo(map));
      for (const piece of geometry.changes) {
        drawn.push(L.polyline(piece, { ...CHANGE, renderer }).addTo(map));
      }
    }
    if (start) layers.get(start)?.bringToFront?.();
    if (end) layers.get(end)?.bringToFront?.();
  }

  function describe(lane: RouteLane | undefined): string {
    return lane ? lane.label : "nothing";
  }

  function refresh(): void {
    found = start && end ? graph.find(start, end) : null;
    geometry = found
      ? routeGeometry(graph, found.lanes, found.laneChanges, payload.connectors)
      : null;
    if (!start) {
      status.textContent = "Click a lane to start from.";
      detail.textContent = "";
    } else if (!end) {
      const count = reachable ? reachable.size - 1 : 0;
      status.textContent = `Start set. Now click where to finish.`;
      detail.textContent =
        `From ${describe(byId.get(start))}. ` +
        `${count} lane${count === 1 ? "" : "s"} are reachable from here - they are the ` +
        "ones drawn in blue. Anything still grey cannot be driven to.";
    } else if (!found) {
      status.textContent = "No drive exists between those two.";
      detail.textContent =
        "Most pairs have none, because the map is one-way in most places. Pick an end " +
        "that is drawn in blue.";
    } else {
      const junctions = found.lanes.length - 1 - found.laneChanges.length;
      status.textContent = `${geometry!.distanceM.toFixed(0)} m over ${found.lanes.length} lanes.`;
      detail.textContent =
        `${junctions} junction movement${junctions === 1 ? "" : "s"} and ` +
        `${found.laneChanges.length} lane change${found.laneChanges.length === 1 ? "" : "s"} ` +
        "(drawn in amber). The converter re-derives this route from the same map, so what " +
        "you see here is what gets built.";
    }
    addButton.disabled = !found;
    redraw();
  }

  function pick(lane: RouteLane): void {
    if (start === null || (start !== null && end !== null)) {
      start = lane.id;
      end = null;
      reachable = graph.reachableFrom(lane.id);
    } else if (lane.id === start) {
      // Clicking the start again clears it, so a misclick does not need the reset button.
      start = null;
      end = null;
      reachable = null;
    } else {
      end = lane.id;
    }
    refresh();
  }

  function renderList(): void {
    list.replaceChildren();
    for (const [index, route] of chosen.entries()) {
      const row = element("div", "rrow");
      const label = element("span", undefined, route.name);
      const where = element("span", "n", `${route.start_lane.slice(0, 8)} → ${route.end_lane.slice(0, 8)}`);
      const remove = element("button", "link", "remove");
      remove.addEventListener("click", () => {
        chosen.splice(index, 1);
        renderList();
      });
      row.addEventListener("click", (event) => {
        if (event.target === remove) return;
        start = route.start_lane;
        reachable = graph.reachableFrom(start);
        end = route.end_lane;
        refresh();
      });
      row.append(label, where, remove);
      list.append(row);
    }
    saveButton.disabled = chosen.length === 0;
    listNote.textContent =
      chosen.length === 0
        ? "Nothing added yet. Each route becomes one scenario in the dataset, and MetaDrive picks between them per episode."
        : `${chosen.length} route${chosen.length === 1 ? "" : "s"} - the dataset will hold ${chosen.length} scenario${chosen.length === 1 ? "" : "s"}, all sharing this map.`;
  }

  addButton.addEventListener("click", () => {
    if (!found || !start || !end) return;
    const name = nameInput.value.trim();
    const problem = nameProblem(name, chosen.map((route) => route.name));
    nameNote.textContent = problem ?? "";
    if (problem) return;
    chosen.push({ name, start_lane: start, end_lane: end });
    nameInput.value = "";
    renderList();
  });

  clearButton.addEventListener("click", () => {
    start = null;
    end = null;
    reachable = null;
    refresh();
  });

  saveButton.addEventListener("click", () => {
    download(
      payload.suggested_filename,
      serializeRoutes(payload.identity, chosen, payload.routes_version),
    );
  });

  loadInput.addEventListener("change", () => {
    const file = loadInput.files?.[0];
    if (!file) return;
    void file.text().then((raw) => {
      try {
        const loaded = parseRoutes(raw, payload.identity, payload.routes_version);
        chosen.splice(0, chosen.length, ...loaded);
        listNote.textContent = "";
        renderList();
      } catch (error) {
        listNote.textContent =
          error instanceof RoutesFileError ? error.message : "Could not read that file.";
      }
      loadInput.value = "";
    });
  });

  renderList();
  refresh();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
