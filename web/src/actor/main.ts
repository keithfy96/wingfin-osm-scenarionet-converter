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
  parseGenerated,
  pathLengthM,
  serializeActors,
} from "./actors-file.js";
import {
  DENSITIES,
  WHOLE_MAP_CAP,
  corridorLengthM,
  countFor,
  generateActors,
  lineLengthM,
  type Density,
} from "./randomise.js";
import type {
  ActorBuilderPayload,
  ActorKind,
  ActorLane,
  ActorWait,
  DrawnActor,
  GeneratedNote,
} from "./types.js";
import { RouteGraph } from "../route/path.js";
import { RoutesFileError, parseRoutes } from "../route/routes-file.js";
import type { ChosenRoute } from "../route/types.js";
import type { LeafletLayer, LeafletMap, LeafletMouseEvent } from "../types-dom.js";

const LANE = { color: "#adb5bd", weight: 2, opacity: 0.6 };
const CORRIDOR = { color: "#1c7ed6", weight: 5, opacity: 0.5 };
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
  // Which actor is being edited, or -1. Selecting one - in the list or by clicking it on the
  // map - turns the form above into a live editor of that actor: every change is written
  // straight back into the list, so there is no Save to forget and nothing to lose by
  // clicking away. `drawn` and `waits` hold its geometry while it is selected, which is the
  // same buffer a brand new actor is laid out in.
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
  // Its own row under the finish button, not beside Undo and Clear: those two are about the
  // shape being drawn and this one throws the actor away, and a destructive control sitting
  // in a row of reversible ones is a misclick waiting to happen. Named after whatever is
  // selected, so what is about to go is on the button rather than held in your head.
  const deleteButton = element("button", "danger", "Delete this actor");
  const deleteRow = element("div", "row");
  deleteRow.hidden = true;
  deleteRow.append(deleteButton);
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

  // --- randomise ---------------------------------------------------------------------
  const densities: Record<ActorKind, Density> = {
    pedestrian: "medium",
    cyclist: "low",
    cone: "none",
    barrier: "low",
  };
  const densityRows: HTMLElement[] = [];
  for (const kind of KINDS) {
    const select = element("select");
    for (const density of DENSITIES) {
      const option = element("option", undefined, density);
      option.value = density;
      select.append(option);
    }
    select.value = densities[kind];
    select.addEventListener("change", () => {
      densities[kind] = select.value as Density;
      describeCount();
    });
    const row = element("div", "row");
    row.append(element("label", undefined, kind), select);
    densityRows.push(row);
  }

  const routeLabel = element("label", "loadbtn", "Load a routes.json");
  const routeInput = element("input");
  routeInput.type = "file";
  routeInput.accept = "application/json,.json";
  routeLabel.append(routeInput);
  const routeSelect = element("select");
  const routeRow = element("div", "row");
  routeRow.hidden = true;
  routeRow.append(element("label", undefined, "route"), routeSelect);
  const routeNote = element("p", "caption");
  // Said before Generate, not after it. Reporting the whole-map spread in the result note
  // was too late to be useful: a press with no route loaded scattered 149 actors over the
  // map, only 31 of them within 25 m of the route, and the first sign of it was the map.
  const NO_ROUTE =
    `No route loaded, so Generate will spread actors over the whole map - trimmed to the ` +
    `"at most" number below - and most will be nowhere near the drive. Load the routes.json ` +
    `you convert with to put them on it.`;
  routeNote.textContent = NO_ROUTE;

  const seedInput = numberInput(1, "1", "72px");
  const newSeedButton = element("button", undefined, "new seed");
  const paceInput = numberInput(30, "1", "72px");
  const seedRow = element("div", "row");
  seedRow.append(
    element("label", undefined, "seed"),
    seedInput,
    newSeedButton,
    element("label", undefined, "ego averages km/h"),
    paceInput,
  );
  // How many to place, exactly - a target rather than a ceiling, so it scales the densities
  // both ways. One number for a route press and a whole-map one alike.
  const countInput = numberInput(WHOLE_MAP_CAP, "1", "72px");
  const countRow = element("div", "row");
  countRow.append(
    element("label", undefined, "exactly"),
    countInput,
    element("label", undefined, "objects"),
  );
  // What the densities alone come to on whatever is loaded. Recomputed rather than
  // remembered, because it moves when a density or the route does.
  const countNote = element("p", "caption");
  // Provenance of a loaded file. Its own line, and it never writes into the boxes above:
  // reading what made a file is not the same as asking to make another one like it.
  const loadedNote = element("p", "caption");

  function askedFor(): number {
    const metres = corridorLengthM(corridor !== null && corridor.length > 0 ? corridor : payload.lanes);
    return KINDS.reduce((sum, kind) => sum + countFor(kind, densities[kind], metres), 0);
  }

  function describeCount(): void {
    countNote.textContent =
      `These densities come to ${askedFor()} objects on ` +
      (corridor !== null && corridor.length > 0 ? "the loaded route" : "the whole map") +
      ". Set a number to place exactly that many - more or fewer - split between the kinds " +
      "in the same proportions. 0 places however many the densities ask for. The seed only " +
      "decides where they go, never how many.";
  }
  const zebraCheck = element("input");
  zebraCheck.type = "checkbox";
  const zebraRow = element("div", "row");
  zebraRow.append(zebraCheck, element("label", undefined, "paint a crossing at each walker"));
  const generateButton = element("button", "primary", "Generate");
  const generateNote = element("p", "caption");

  // The route's lanes, in travel order, or null while none is loaded. Kept as the payload's
  // own lane objects rather than ids: the placement needs each one's width and index.
  let corridor: ActorLane[] | null = null;
  let loadedRoutes: ChosenRoute[] = [];
  const corridorLayers: LeafletLayer[] = [];
  // Which entries in `actors` this button put there, so pressing it again replaces its own
  // work and leaves anything drawn by hand alone.
  const generated = new Set<string>();
  // What the last press decided, written into the downloaded file. Adopted from a loaded
  // file so that opening one and saving it straight back does not lose its provenance.
  let lastGenerated: GeneratedNote | null = null;
  const byId = new Map(payload.lanes.map((lane) => [lane.id, lane]));
  const graph = new RouteGraph(payload.lanes);

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
      deleteRow,
      problemNote,
      buttons,
      element("h2", undefined, "Randomise"),
      ...densityRows,
      routeLabel,
      routeRow,
      routeNote,
      seedRow,
      countRow,
      countNote,
      loadedNote,
      zebraRow,
      generateButton,
      generateNote,
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
    // Orange while editing something already placed, purple while laying out a new one. The
    // two are different acts and the legend names both; drawing an edit in the "new actor"
    // colour is how you lose track of which of the two you are in the middle of.
    const line = selected >= 0 ? { ...SELECTED, dashArray: "6 4" } : DRAWING;
    const dot = selected >= 0 ? { ...VERTEX, color: SELECTED.color } : VERTEX;
    if (drawn.length >= 2) {
      scratch.push(L.polyline(drawn, { ...line, renderer }).addTo(map));
    }
    for (const point of drawn) {
      scratch.push(L.circleMarker(point, { ...dot, renderer }).addTo(map));
    }
  }

  function redrawPlaced(): void {
    for (const layer of placed.splice(0)) layer.remove?.();
    for (const [index, actor] of actors.entries()) {
      // The selected one is drawn from the live buffer by `redrawScratch` instead, because it
      // is what moves as you edit. Drawing it here as well would leave a stale copy of where
      // it used to be sitting under the one that follows the clicks.
      if (index === selected) continue;
      // `bubblingMouseEvents: false`, and it is not optional: this page listens for clicks on
      // the map to place points, so without it picking an actor off the map would also drop a
      // corner into whatever is being drawn underneath it.
      const shape = actor.path
        ? L.polyline(actor.path, { ...PLACED, renderer, bubblingMouseEvents: false })
        : actor.position
          ? L.circleMarker(actor.position, {
              ...STATIC_DOT,
              renderer,
              bubblingMouseEvents: false,
            })
          : null;
      if (!shape) continue;
      shape.on("click", () => select(index));
      placed.push(shape.addTo(map));
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
        applyEdit();
      });
      row.append(remove);
      waitList.append(row);
    }
  }

  function refresh(): void {
    const isMoving = moving();
    const current = editing();
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
      status.textContent = current
        ? `Editing ${current.name} - ${metres.toFixed(1)} m, about ${seconds.toFixed(0)} s.`
        : `${metres.toFixed(1)} m, about ${seconds.toFixed(0)} s to walk.`;
      detail.textContent = current
        ? "Every change applies as you make it. Click the map to add a corner at the end of " +
          "the path, Undo to take one off, Clear to draw it again from scratch."
        : `${drawn.length} points. A route shorter than that in a given scenario simply ends ` +
          "before this actor finishes, and the actor is dropped from any route that ends " +
          "before it starts.";
    } else {
      status.textContent = current ? `Editing ${current.name}.` : "Placed. Name it and add it.";
      detail.textContent = current
        ? "Every change applies as you make it. Click the map to move it."
        : "";
    }
    // One button, two jobs, because they are the same button in the person's head: the thing
    // you press when you have finished with the form. Adding needs enough points; finishing
    // an edit never does, since the edit is already applied.
    addButton.textContent = current ? "Done editing" : "Add actor";
    addButton.disabled = !current && drawn.length < need;
    deleteRow.hidden = !current;
    if (current) deleteButton.textContent = `Delete ${current.name}`;
    redrawScratch();
  }

  function renderList(): void {
    list.replaceChildren();
    for (const [index, actor] of actors.entries()) {
      const row = element("div", index === selected ? "arow on" : "arow");
      row.append(element("span", undefined, `${actor.name} · ${actor.kind}`));
      const note = actor.path
        ? `${pathLengthM(actor.path).toFixed(0)} m${actor.crossing_width_m ? " · zebra" : ""}`
        : "static";
      row.append(element("span", "n", note));
      const remove = element("button", "link", "remove");
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeActor(index);
      });
      row.addEventListener("click", () => {
        if (index === selected) deselect();
        else select(index);
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

  // --- selecting and editing ----------------------------------------------------------

  /** Take one out of the list, from wherever it was asked for - the row's own remove link or
   * the Delete button on the actor being edited. One function because the bookkeeping below
   * is the part that is easy to get wrong, and two copies of it would drift. */
  function removeActor(index: number): void {
    const actor = actors[index];
    if (!actor) return;
    // Also forgotten as generated, so a hand-drawn actor that later takes the same name is
    // not swept up by the next Generate.
    generated.delete(actor.name);
    actors.splice(index, 1);
    // `selected` is an index into a list that just got shorter. Removing the one being edited
    // ends the edit; removing anything above it slides it down by one, and not saying so is
    // how an edit silently continues on a different actor.
    if (index === selected) {
      deselect();
      return;
    }
    if (index < selected) selected -= 1;
    renderList();
  }

  function editing(): DrawnActor | null {
    return selected >= 0 ? (actors[selected] ?? null) : null;
  }

  /** Load an actor into the form and start editing it.
   *
   * Its geometry goes into the same `drawn` buffer a new actor is laid out in, so every
   * control that already works on a new actor - the map, Undo, Clear, the wait rows - works
   * on this one without knowing an edit is happening.
   */
  function select(index: number): void {
    const actor = actors[index];
    if (!actor) return;
    selected = index;
    kindSelect.value = actor.kind;
    nameInput.value = actor.name;
    drawn.length = 0;
    const geometry = actor.path ?? (actor.position ? [actor.position] : []);
    for (const point of geometry) drawn.push([point[0], point[1]]);
    waits.length = 0;
    for (const wait of actor.waits ?? []) waits.push({ ...wait });
    speedInput.value = String(
      actor.speed_mps ??
        (actor.kind === "cyclist"
          ? payload.defaults.cyclist_mps
          : payload.defaults.pedestrian_mps),
    );
    delayInput.value = String(actor.start_delay_s ?? 0);
    headingInput.value = String(Math.round(((actor.heading_rad ?? 0) * 180) / Math.PI));
    crossingCheck.checked = actor.crossing_width_m !== undefined;
    crossingWidth.value = String(actor.crossing_width_m ?? payload.defaults.crossing_width_m);
    editNote(null);
    renderWaits();
    renderList();
    refresh();
  }

  /** The one line under the form: whatever is wrong right now, or else where this actor came
   * from. Written through one function because the two share a line and an edit would
   * otherwise silently wipe the warning that the next Generate is about to replace it. */
  function editNote(problem: string | null): void {
    const current = editing();
    problemNote.textContent =
      problem ??
      (current && generated.has(current.name)
        ? "This one came from Generate. Edit it freely - but pressing Generate again " +
          "replaces everything it placed, this included."
        : "");
  }

  /** Forget the selection and the buffer it filled. Does not redraw; `deselect` does. */
  function clearSelection(): void {
    selected = -1;
    drawn.length = 0;
    waits.length = 0;
    nameInput.value = "";
    problemNote.textContent = "";
  }

  function deselect(): void {
    clearSelection();
    renderWaits();
    renderList();
    refresh();
  }

  /** Write the form back into the selected actor, taking whatever is currently valid.
   *
   * There is no Save button, deliberately: an edit lands the moment it is made, so selecting
   * something else or clicking the map can never strand a half-finished change. The two
   * things that go momentarily invalid while being typed - a name that is blank or already
   * taken, and a path with too few points to be one - are simply not committed, and the actor
   * keeps what it had until they are valid again.
   */
  function applyEdit(): void {
    const current = editing();
    if (!current) return;
    const isMoving = moving();
    const next: DrawnActor = { name: current.name, kind: kind() };

    const name = nameInput.value.trim();
    // Its own name is not a clash with itself, so it comes out of the taken list.
    const problem = nameProblem(
      name,
      actors.filter((_, index) => index !== selected).map((actor) => actor.name),
    );
    if (!problem) next.name = name;

    const points = drawn.map((point): [number, number] => [point[0], point[1]]);
    if (isMoving) {
      // A path needs two points. Switching a cone to a pedestrian therefore waits for the
      // second click rather than inventing one, and the actor stays a cone until then.
      const path = points.length >= 2 ? points : current.path;
      if (!path) return;
      next.path = path;
      next.speed_mps = Number(speedInput.value);
      next.start_delay_s = Number(delayInput.value);
      if (waits.length) next.waits = waits.map((wait) => ({ ...wait }));
      if (crossingCheck.checked) next.crossing_width_m = Number(crossingWidth.value);
    } else {
      const at = points[0] ?? current.position ?? current.path?.[0];
      if (!at) return;
      next.position = [at[0], at[1]];
      next.heading_rad = (Number(headingInput.value) * Math.PI) / 180;
    }

    if (next.name !== current.name && generated.delete(current.name)) {
      // The rename follows into the generated set, or the next press leaves the renamed actor
      // behind as a stray while hunting for one that no longer exists.
      generated.add(next.name);
    }
    actors[selected] = next;
    editNote(problem);
    renderList();
  }

  /** Applied and redrawn, which is what every control on the form wants after a change. */
  function touch(): void {
    applyEdit();
    refresh();
  }

  function redrawCorridor(): void {
    for (const layer of corridorLayers.splice(0)) layer.remove?.();
    for (const lane of corridor ?? []) {
      corridorLayers.push(L.polyline(lane.line, { ...CORRIDOR, renderer }).addTo(map));
    }
  }

  /** Resolve the selected route into a corridor, and say what happened.
   *
   * `routes.json` names only a route's two ends, so the lane sequence is found here with the
   * same search and the same weights `ego_route` uses at convert time - a second path-finder
   * would be a page offering a drive the converter would not build.
   */
  function chooseRoute(): void {
    corridor = null;
    // The corridor decides what the densities come to, so every path out of here refreshes
    // the count note - including the two that leave the corridor null.
    const chosen = loadedRoutes[routeSelect.selectedIndex];
    if (!chosen) {
      routeNote.textContent = NO_ROUTE;
      redrawCorridor();
      describeCount();
      return;
    }
    const found = graph.find(chosen.start_lane, chosen.end_lane);
    if (!found) {
      routeNote.textContent = `No drive from ${chosen.name}'s start to its end on this map. ${NO_ROUTE}`;
      redrawCorridor();
      describeCount();
      return;
    }
    const lanes = found.lanes
      .map((id) => byId.get(id))
      .filter((lane): lane is ActorLane => lane !== undefined);
    corridor = lanes;
    const metres = lanes.reduce((total, lane) => total + lineLengthM(lane.line), 0);
    routeNote.textContent =
      `route ${chosen.name} · ${lanes.length} lanes · about ${metres.toFixed(0)} m. ` +
      "Actors will be placed along it, so the car meets all of them.";
    redrawCorridor();
    describeCount();
  }

  routeInput.addEventListener("change", () => {
    const file = routeInput.files?.[0];
    if (!file) return;
    void file.text().then((raw) => {
      try {
        loadedRoutes = parseRoutes(raw, payload.identity, payload.routes_version);
        routeSelect.replaceChildren();
        for (const route of loadedRoutes) {
          const option = element("option", undefined, route.name);
          option.value = route.name;
          routeSelect.append(option);
        }
        // One route needs no picker; more than one does, and hiding it either way would
        // leave a file with three routes silently using whichever is first.
        routeRow.hidden = loadedRoutes.length < 2;
        chooseRoute();
      } catch (error) {
        loadedRoutes = [];
        corridor = null;
        routeRow.hidden = true;
        redrawCorridor();
        routeNote.textContent = `${
          error instanceof RoutesFileError ? error.message : "Could not read that file."
        } ${NO_ROUTE}`;
      }
      routeInput.value = "";
    });
  });

  routeSelect.addEventListener("change", chooseRoute);

  /** Replace whatever the last press placed with a fresh scene at the settings on screen. */
  function regenerate(): void {
    // Its own previous work, and only that. A hand-drawn actor is never swept up, so the
    // button composes with the map rather than replacing it.
    for (let index = actors.length - 1; index >= 0; index -= 1) {
      const actor = actors[index];
      if (actor && generated.has(actor.name)) actors.splice(index, 1);
    }
    generated.clear();
    // An edit in progress may be on one of the actors this press is about to remove, and
    // `selected` is an index into a list that is about to be rebuilt either way.
    if (selected >= 0) clearSelection();

    const route = corridor !== null && corridor.length > 0 ? corridor : null;
    const onRoute = route !== null;
    const pace = Number(paceInput.value);
    const lanes = route ?? payload.lanes;
    const seed = Math.trunc(Number(seedInput.value)) || 0;
    const objects = Math.trunc(Number(countInput.value)) || 0;
    // What the densities alone come to, so the note can say when the number overrode them.
    // `corridorLengthM` and not a length summed here: placement drops lanes under
    // `MIN_LANE_M`, and counting road it will not use would over-report every press.
    const asked = askedFor();
    const made = generateActors({
      corridor: lanes,
      densities,
      seed,
      taken: actors.map((actor) => actor.name),
      speeds: {
        pedestrian_mps: payload.defaults.pedestrian_mps,
        cyclist_mps: payload.defaults.cyclist_mps,
      },
      egoMps: onRoute && pace > 0 ? pace / 3.6 : null,
      crossings: zebraCheck.checked,
      crossingWidthM: payload.defaults.crossing_width_m,
      objects: objects > 0 ? objects : undefined,
    });
    for (const actor of made) {
      generated.add(actor.name);
      actors.push(actor);
    }
    // Recorded so the file can say what made it. `made.length` and not the box: they agree
    // whenever anything was placed, and the file should carry what happened.
    lastGenerated = made.length ? { seed, objects: made.length } : null;
    generateNote.textContent = made.length
      ? `${made.length} actor${made.length === 1 ? "" : "s"} placed ` +
        (onRoute
          ? "along the loaded route. Every start delay is an estimate of when the car gets " +
            "there - edit any of them below or in the file."
          : "across the whole map. Load a routes.json to put them where the car actually " +
            "drives.") +
        // Said, rather than left to be noticed. A press that quietly dropped 280 of the 430
        // it placed looked exactly like a press that had nothing more to place.
        // Said, rather than left to be noticed. A press that quietly dropped 280 of the 430
        // it would have placed looked exactly like one with nothing more to place.
        (made.length === asked
          ? ""
          : ` The densities on their own come to ${asked}; this number ` +
            `${made.length > asked ? "scaled them up" : "scaled them down"}.`) +
        ` Seed ${seed}. Generate again to replace them; anything you drew by hand is left alone.`
      : "Nothing to place: every kind is set to none.";
    describeCount();
    renderWaits();
    renderList();
    refresh();
  }

  generateButton.addEventListener("click", regenerate);

  // A new scene in one click. The roll is written **into the box** rather than kept to
  // itself, so what `randomise.ts` promises still holds: the number that produced what is on
  // the map is on screen, and typing it back gives the same scene again.
  newSeedButton.addEventListener("click", () => {
    seedInput.value = String(1 + Math.floor(Math.random() * 999_999));
    regenerate();
  });

  map.on("click", (event: LeafletMouseEvent) => {
    if (!moving() && drawn.length >= 1) drawn.length = 0;
    drawn.push([event.latlng.lat, event.latlng.lng]);
    touch();
  });

  kindSelect.addEventListener("change", () => {
    if (editing()) {
      // The points and the waits are the actor's, not the kind's, so they are kept across the
      // switch: a pedestrian turned into a cone stands on the first corner of its own path,
      // and turning it back restores the rest. The speed is the actor's too, so unlike a new
      // actor it is not reset to the kind's default.
      renderWaits();
      touch();
      return;
    }
    drawn.length = 0;
    waits.length = 0;
    speedInput.value = String(
      kind() === "cyclist" ? payload.defaults.cyclist_mps : payload.defaults.pedestrian_mps,
    );
    renderWaits();
    refresh();
  });

  for (const input of [speedInput, delayInput]) {
    input.addEventListener("input", touch);
  }
  // Only meaningful while something is selected; `applyEdit` returns at once otherwise.
  nameInput.addEventListener("input", applyEdit);
  headingInput.addEventListener("input", applyEdit);
  crossingWidth.addEventListener("input", applyEdit);
  crossingCheck.addEventListener("change", applyEdit);

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
    touch();
  });

  deleteButton.addEventListener("click", () => {
    if (selected >= 0) removeActor(selected);
  });

  undoButton.addEventListener("click", () => {
    drawn.pop();
    touch();
  });

  clearButton.addEventListener("click", () => {
    drawn.length = 0;
    waits.length = 0;
    problemNote.textContent = "";
    renderWaits();
    // Not `touch`: an empty buffer is not a shape, so the actor keeps the geometry it has
    // until enough points are back. Clear is "draw it again", not "delete it".
    refresh();
  });

  addButton.addEventListener("click", () => {
    // Doubles as Done editing. There is nothing to commit - the edit already is - so this
    // only puts the form back to laying out a new actor.
    if (editing()) {
      deselect();
      return;
    }
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
      serializeActors(payload.identity, actors, payload.actors_version, lastGenerated),
    );
  });

  loadInput.addEventListener("change", () => {
    const file = loadInput.files?.[0];
    if (!file) return;
    void file.text().then((raw) => {
      try {
        const loaded = parseActors(raw, payload.identity, payload.actors_version);
        actors.splice(0, actors.length, ...loaded);
        // A loaded file replaces the list, so nothing in it is this button's to replace -
        // otherwise a loaded actor sharing a generated name would vanish on the next press.
        generated.clear();
        // Reported, never applied. The boxes above are what the *next* press will do, and
        // silently overwriting them with a loaded file's settings would lose whatever was
        // being set up - so this says what made the file and leaves the controls alone.
        lastGenerated = parseGenerated(raw);
        loadedNote.textContent = lastGenerated
          ? `Loaded file seed = ${lastGenerated.seed}, no of objects = ${lastGenerated.objects}` +
            ` (${loaded.length} in the file). The boxes above are unchanged.`
          : `Loaded ${loaded.length} actor${loaded.length === 1 ? "" : "s"}; the file records ` +
            "no seed, so it was drawn by hand or written before seeds were saved.";
        listNote.textContent = "";
        // The list this edit indexed into has just been replaced wholesale.
        clearSelection();
        renderWaits();
        renderList();
        refresh();
      } catch (error) {
        listNote.textContent =
          error instanceof ActorsFileError ? error.message : "Could not read that file.";
      }
      loadInput.value = "";
    });
  });

  describeCount();
  renderWaits();
  renderList();
  refresh();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
