// Entry point for the Stage 6 actor builder.
//
// The Python renderer writes `window.__ACTOR_PAYLOAD__` and a #map / #side pair, then loads
// this bundle. Pick a kind, click the map to lay out where the actor goes, name it and add
// it; the downloaded actors.json is what `osm-scenario convert --actors` turns into tracks.
//
// Placement is by clicking the map, not by picking a lane, and that is the whole reason this
// page exists rather than a heuristic in the converter. A pedestrian walks where no lane is,
// and the source has nothing to derive one from: across `junction-1` and `mosque` the OSM
// carries four footways between them and not one `highway=crossing` node.

import {
  ActorsFileError,
  KINDS,
  MOVING,
  nameProblem,
  parseActors,
  pathLengthM,
  serializeActors,
} from "./actors-file.js";
import type { ActorBuilderPayload, ActorKind, ActorWait, DrawnActor } from "./types.js";
import type { LeafletLayer, LeafletMap, LeafletMouseEvent } from "../types-dom.js";

const LANE = { color: "#adb5bd", weight: 2, opacity: 0.6 };
const DRAWING = { color: "#7048e8", weight: 4, opacity: 0.95, dashArray: "6 4" };
const PLACED = { color: "#0ca678", weight: 4, opacity: 0.9 };
const SELECTED = { color: "#e8590c", weight: 6, opacity: 1 };
const VERTEX = { radius: 4, color: "#7048e8", fillColor: "#fff", fillOpacity: 1, weight: 2 };
const STATIC_DOT = { radius: 6, color: "#0ca678", fillColor: "#0ca678", fillOpacity: 0.8, weight: 2 };

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

function numberInput(value: number, step: string, width = "72px"): HTMLInputElement {
  const input = element("input");
  input.type = "number";
  input.step = step;
  input.value = String(value);
  input.style.width = width;
  return input;
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
  const supplied = (window as unknown as { __ACTOR_PAYLOAD__?: ActorBuilderPayload })
    .__ACTOR_PAYLOAD__;
  const side = document.getElementById("side");
  if (!supplied || !side) return;
  const payload: ActorBuilderPayload = supplied;

  const renderer = L.canvas({ tolerance: 10 });
  const map: LeafletMap = L.map("map", { preferCanvas: true, renderer });
  map.setView(payload.center, 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  if (payload.bounds) map.fitBounds(L.latLngBounds(payload.bounds));

  // Context only, and deliberately faint: the lanes are what an actor is placed *against*,
  // not what it is placed *on*, and drawn at full weight they read as the thing to click.
  for (const lane of payload.lanes) {
    L.polyline(lane.line, { ...LANE, renderer }).addTo(map);
  }

  const drawn: [number, number][] = [];
  const waits: ActorWait[] = [];
  const actors: DrawnActor[] = [];
  let selected = -1;
  const scratch: LeafletLayer[] = [];
  const placed: LeafletLayer[] = [];

  // --- panel ------------------------------------------------------------------------
  const kindRow = element("div", "row");
  const kindSelect = element("select");
  for (const kind of KINDS) {
    const option = element("option", undefined, kind);
    option.value = kind;
    kindSelect.append(option);
  }
  kindRow.append(element("label", undefined, "kind"), kindSelect);

  const nameInput = element("input");
  nameInput.type = "text";
  nameInput.placeholder = "actor name, e.g. crossing-north";
  const nameRow = element("div", "row");
  nameRow.append(element("label", undefined, "name"), nameInput);

  const speedInput = numberInput(payload.defaults.pedestrian_mps, "0.1");
  const delayInput = numberInput(0, "0.5");
  const movingRow = element("div", "row");
  movingRow.append(
    element("label", undefined, "speed m/s"),
    speedInput,
    element("label", undefined, "starts after s"),
    delayInput,
  );

  const crossingCheck = element("input");
  crossingCheck.type = "checkbox";
  const crossingWidth = numberInput(payload.defaults.crossing_width_m, "0.5");
  const crossingRow = element("div", "row");
  crossingRow.append(
    crossingCheck,
    element("label", undefined, "paint a crossing, width m"),
    crossingWidth,
  );

  const headingInput = numberInput(0, "5");
  const staticRow = element("div", "row");
  staticRow.append(element("label", undefined, "heading deg"), headingInput);

  const waitAt = numberInput(0, "0.5");
  const waitFor = numberInput(5, "0.5");
  const waitAdd = element("button", undefined, "add wait");
  const waitRow = element("div", "row");
  waitRow.append(
    element("label", undefined, "wait at m"),
    waitAt,
    element("label", undefined, "for s"),
    waitFor,
    waitAdd,
  );
  const waitList = element("div", "waits");

  const status = element("p", "verdict");
  const detail = element("p", "muted");
  const addButton = element("button", "primary", "Add actor");
  const undoButton = element("button", undefined, "Undo last point");
  const clearButton = element("button", undefined, "Clear");
  const problemNote = element("p", "caption");
  const buttons = element("div", "row");
  buttons.append(undoButton, clearButton);

  const list = element("div", "actors");
  const listNote = element("p", "caption");
  const saveButton = element("button", "primary", "Download actors.json");
  const loadLabel = element("label", "loadbtn", "Load an existing actors.json");
  const loadInput = element("input");
  loadInput.type = "file";
  loadInput.accept = "application/json,.json";
  loadLabel.append(loadInput);

  document
    .getElementById("panel")
    ?.append(
      status,
      detail,
      kindRow,
      nameRow,
      movingRow,
      crossingRow,
      staticRow,
      element("h2", undefined, "Waits"),
      waitRow,
      waitList,
      addButton,
      problemNote,
      buttons,
      element("h2", undefined, "Actors to build"),
      list,
      listNote,
      saveButton,
      loadLabel,
    );

  function kind(): ActorKind {
    return kindSelect.value as ActorKind;
  }

  function moving(): boolean {
    return MOVING.has(kind());
  }

  function wanted(): number {
    return moving() ? 2 : 1;
  }

  function redrawScratch(): void {
    for (const layer of scratch.splice(0)) layer.remove?.();
    if (drawn.length >= 2) {
      scratch.push(L.polyline(drawn, { ...DRAWING, renderer }).addTo(map));
    }
    for (const point of drawn) {
      scratch.push(L.circleMarker(point, { ...VERTEX, renderer }).addTo(map));
    }
  }

  function redrawPlaced(): void {
    for (const layer of placed.splice(0)) layer.remove?.();
    for (const [index, actor] of actors.entries()) {
      const style = index === selected ? SELECTED : PLACED;
      if (actor.path) {
        placed.push(L.polyline(actor.path, { ...style, renderer }).addTo(map));
      } else if (actor.position) {
        placed.push(
          L.circleMarker(actor.position, {
            ...STATIC_DOT,
            color: style.color,
            fillColor: style.color,
            renderer,
          }).addTo(map),
        );
      }
    }
  }

  function renderWaits(): void {
    waitList.replaceChildren();
    for (const [index, wait] of waits.entries()) {
      const row = element("div", "wrow");
      row.append(
        element("span", undefined, `${wait.at_m.toFixed(1)} m for ${wait.seconds.toFixed(1)} s`),
      );
      const remove = element("button", "link", "remove");
      remove.addEventListener("click", () => {
        waits.splice(index, 1);
        renderWaits();
      });
      row.append(remove);
      waitList.append(row);
    }
  }

  function refresh(): void {
    const isMoving = moving();
    movingRow.hidden = !isMoving;
    crossingRow.hidden = !isMoving;
    waitRow.hidden = !isMoving;
    waitList.hidden = !isMoving;
    staticRow.hidden = isMoving;
    undoButton.disabled = drawn.length === 0;

    const need = wanted();
    if (drawn.length < need) {
      status.textContent = isMoving
        ? "Click the map to lay out the path."
        : "Click the map to place it.";
      detail.textContent = isMoving
        ? "Click each corner in the order the actor walks them: the first click is where it " +
          "starts. Two points is enough for a straight crossing."
        : "One click. A cone or a barrier stands where you put it for the whole episode.";
    } else if (isMoving) {
      const metres = pathLengthM(drawn);
      const speed = Number(speedInput.value) || 0;
      const dwell = waits.reduce((total, wait) => total + wait.seconds, 0);
      const seconds = speed > 0 ? metres / speed + dwell : 0;
      status.textContent = `${metres.toFixed(1)} m, about ${seconds.toFixed(0)} s to walk.`;
      detail.textContent =
        `${drawn.length} points. A route shorter than that in a given scenario simply ends ` +
        "before this actor finishes, and the actor is dropped from any route that ends " +
        "before it starts.";
    } else {
      status.textContent = "Placed. Name it and add it.";
      detail.textContent = "";
    }
    addButton.disabled = drawn.length < need;
    redrawScratch();
  }

  function renderList(): void {
    list.replaceChildren();
    for (const [index, actor] of actors.entries()) {
      const row = element("div", "arow");
      row.append(element("span", undefined, `${actor.name} · ${actor.kind}`));
      const note = actor.path
        ? `${pathLengthM(actor.path).toFixed(0)} m${actor.crossing_width_m ? " · zebra" : ""}`
        : "static";
      row.append(element("span", "n", note));
      const remove = element("button", "link", "remove");
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        actors.splice(index, 1);
        if (selected >= actors.length) selected = -1;
        renderList();
      });
      row.addEventListener("click", () => {
        selected = selected === index ? -1 : index;
        renderList();
      });
      row.append(remove);
      list.append(row);
    }
    saveButton.disabled = actors.length === 0;
    const zebras = actors.filter((actor) => actor.crossing_width_m !== undefined).length;
    listNote.textContent =
      actors.length === 0
        ? "Nothing placed yet. Without --actors the dataset holds the recorded car and nothing else."
        : `${actors.length} actor${actors.length === 1 ? "" : "s"}, ${zebras} painted crossing${zebras === 1 ? "" : "s"}. Every one is written into every scenario in the dataset.`;
    redrawPlaced();
  }

  map.on("click", (event: LeafletMouseEvent) => {
    if (!moving() && drawn.length >= 1) drawn.length = 0;
    drawn.push([event.latlng.lat, event.latlng.lng]);
    refresh();
  });

  kindSelect.addEventListener("change", () => {
    drawn.length = 0;
    waits.length = 0;
    speedInput.value = String(
      kind() === "cyclist" ? payload.defaults.cyclist_mps : payload.defaults.pedestrian_mps,
    );
    renderWaits();
    refresh();
  });

  for (const input of [speedInput, delayInput]) {
    input.addEventListener("input", refresh);
  }

  waitAdd.addEventListener("click", () => {
    const at = Number(waitAt.value);
    const seconds = Number(waitFor.value);
    if (!(at >= 0) || !(seconds >= 0)) return;
    if (drawn.length >= 2 && at > pathLengthM(drawn)) {
      problemNote.textContent = "That wait is past the end of the path.";
      return;
    }
    problemNote.textContent = "";
    waits.push({ at_m: at, seconds });
    renderWaits();
    refresh();
  });

  undoButton.addEventListener("click", () => {
    drawn.pop();
    refresh();
  });

  clearButton.addEventListener("click", () => {
    drawn.length = 0;
    waits.length = 0;
    problemNote.textContent = "";
    renderWaits();
    refresh();
  });

  addButton.addEventListener("click", () => {
    const name = nameInput.value.trim();
    const problem = nameProblem(name, actors.map((actor) => actor.name));
    problemNote.textContent = problem ?? "";
    if (problem) return;
    if (drawn.length < wanted()) return;
    const first = drawn[0];
    if (!first) return;

    const actor: DrawnActor = moving()
      ? {
          name,
          kind: kind(),
          path: drawn.map((point): [number, number] => [point[0], point[1]]),
          speed_mps: Number(speedInput.value),
          start_delay_s: Number(delayInput.value),
        }
      : {
          name,
          kind: kind(),
          position: [first[0], first[1]],
          heading_rad: (Number(headingInput.value) * Math.PI) / 180,
        };
    if (moving() && waits.length) actor.waits = waits.map((wait) => ({ ...wait }));
    if (moving() && crossingCheck.checked) {
      actor.crossing_width_m = Number(crossingWidth.value);
    }
    actors.push(actor);
    nameInput.value = "";
    drawn.length = 0;
    waits.length = 0;
    renderWaits();
    renderList();
    refresh();
  });

  saveButton.addEventListener("click", () => {
    download(
      payload.suggested_filename,
      serializeActors(payload.identity, actors, payload.actors_version),
    );
  });

  loadInput.addEventListener("change", () => {
    const file = loadInput.files?.[0];
    if (!file) return;
    void file.text().then((raw) => {
      try {
        const loaded = parseActors(raw, payload.identity, payload.actors_version);
        actors.splice(0, actors.length, ...loaded);
        listNote.textContent = "";
        selected = -1;
        renderList();
      } catch (error) {
        listNote.textContent =
          error instanceof ActorsFileError ? error.message : "Could not read that file.";
      }
      loadInput.value = "";
    });
  });

  renderWaits();
  renderList();
  refresh();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
