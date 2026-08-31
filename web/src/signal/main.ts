// Entry point for the Stage 6 signal builder.
//
// The Python renderer writes `window.__SIGNAL_PAYLOAD__` and a #map / #side pair, then loads
// this bundle. Add a phase group, click the lanes it stops, set its green, yellow and offset
// within the shared cycle; the downloaded signals.json is what
// `osm-scenario convert --signals` turns into traffic lights.
//
// A light stops the traffic *leaving* the lane it is placed on, so the wall goes at that
// lane's downstream end. That is why lanes are what gets clicked rather than junctions:
// a junction is where the conflict is, but a lane is where the light is.

import { autoPhase, AutoPhaseError } from "./auto-phase.js";
import { DOWNSTREAM_M, findConflicts, type Conflict } from "./conflicts.js";
import { colourAt, phaseStripCss } from "./phase.js";
import {
  inspectSignals,
  nameProblem,
  SignalsFileError,
  serializeSignals,
  timingProblem,
} from "./plan-file.js";
import { stopMarkerStyle, stopPoint } from "./stop-marker.js";
import type {
  PhaseGroup,
  SignalBuilderPayload,
  SignalLane,
  SignalsInspection,
} from "./types.js";
import type { LeafletLayer, LeafletMap } from "../types-dom.js";

const IDLE = { color: "#343a40", weight: 3, opacity: 0.75 };
const OTHER_GROUP = { weight: 6, opacity: 0.85 };
const IN_GROUP = { weight: 9, opacity: 1 };
const SURVEYED = { color: "#7048e8", weight: 5, opacity: 0.9, dashArray: "6 6" };

// One hue per group, in the order they are added. Beyond this the page reuses them - a
// junction with more than six phase groups is not something this page should pretend to
// help with, and the names still say which is which.
const PALETTE = ["#1c7ed6", "#e8590c", "#0ca678", "#ae3ec9", "#5c940d", "#c2255c"];

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

function numberField(value: number, step: number): HTMLInputElement {
  const input = element("input");
  input.type = "number";
  input.min = "0";
  input.step = String(step);
  input.value = String(value);
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
  const supplied = (window as unknown as { __SIGNAL_PAYLOAD__?: SignalBuilderPayload })
    .__SIGNAL_PAYLOAD__;
  const side = document.getElementById("side");
  if (!supplied || !side) return;
  const payload: SignalBuilderPayload = supplied;

  const byId = new Map(payload.lanes.map((lane) => [lane.id, lane]));
  const surveyedLanes = new Set(payload.surveyed.flatMap((signal) => signal.lanes));

  const renderer = L.canvas({ tolerance: 10 });
  const map: LeafletMap = L.map("map", { preferCanvas: true, renderer });
  map.setView(payload.center, 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  if (payload.bounds) map.fitBounds(L.latLngBounds(payload.bounds));

  let cycleSeconds = payload.defaults.cycle_seconds;
  const groups: PhaseGroup[] = [];
  let active = -1;
  // Where in the cycle the preview stands. Moving it recolours every lane on the map at
  // once, which is the only way to see whether two arms are green together.
  let previewSeconds = 0;

  function popupFor(lane: SignalLane): HTMLElement {
    const box = element("div");
    box.append(element("div", undefined, lane.label));
    box.append(element("code", undefined, lane.id));
    if (surveyedLanes.has(lane.id)) {
      box.append(
        element(
          "p",
          "caption",
          "OSM records a traffic signal associated with this lane. It carries no timing.",
        ),
      );
    }
    return box;
  }

  const layers = new Map<string, LeafletLayer>();
  for (const lane of payload.lanes) {
    const line = L.polyline(lane.line, { ...IDLE, renderer });
    line.on("click", () => pick(lane));
    line.bindPopup?.(() => popupFor(lane));
    line.addTo(map);
    layers.set(lane.id, line);
  }

  // One dot per signalled lane, at the point the wall actually goes. Kept in step with the
  // groups rather than built once: a dot on every lane in the payload would be noise on the
  // one view whose job is comparing two arms at a single moment in the cycle.
  const stopMarkers = new Map<string, LeafletLayer>();

  // Where every lane's light would go, handed to `inspectSignals` so it can say which of a
  // loaded plan's lights have moved since that plan was drawn.
  const stopPoints = new Map<string, [number, number] | null>(
    payload.lanes.map((lane) => [lane.id, stopPoint(lane)]),
  );

  // Every lane's shape, so `findConflicts` can measure how far past a light its traffic is
  // still inside the junction. Kept apart from `byId` because that is what the function
  // needs and all it needs.
  const laneLines = new Map<string, readonly [number, number][]>(
    payload.lanes.map((lane) => [lane.id, lane.line]),
  );

  // --- panel ------------------------------------------------------------------------
  const status = element("p", "verdict");
  const detail = element("p", "muted");

  const cycleRow = element("div", "row");
  const cycleInput = numberField(cycleSeconds, 1);
  cycleRow.append(element("label", undefined, "Cycle (s)"), cycleInput);

  const previewRow = element("div", "row");
  const previewInput = element("input");
  previewInput.type = "range";
  previewInput.min = "0";
  previewInput.step = "0.5";
  const previewLabel = element("span", "n");
  previewRow.append(element("label", undefined, "Preview at"), previewInput, previewLabel);

  const addGroupButton = element("button", "primary", "Add a phase group");
  const groupList = element("div", "groups");
  const conflictBox = element("div", "conflicts");
  const autoButton = element("button", "fixbtn", "Re-time to clear the clashes");
  const undoButton = element("button", "fixbtn", "Undo the re-timing");
  // What the last press did, and what it did it to. `autoUndo` is dropped the moment
  // anything else is edited, so Undo can never write over newer work.
  let autoUndo: PhaseGroup[] | null = null;
  let autoNote = "";
  let autoFailed = false;
  const saveButton = element("button", "primary", "Download signals.json");
  // A button rather than the small underlined link this was: it is the other half of the
  // pair with Download, and read as a footnote for as long as it looked like one.
  const loadLabel = element("label", "loadbtn", "Load a signals.json");
  const loadInput = element("input");
  loadInput.type = "file";
  loadInput.accept = "application/json,.json";
  loadLabel.append(loadInput);
  const loadNote = element("p", "caption");
  const loadReport = element("div", "report");
  // Stays visible after a plan drawn on another generation is adopted, until the next
  // download re-stamps it. Clearing it on the click that accepted it would hide the one
  // thing the person then has to check.
  const adoptedNote = element("p", "caption warn");

  document
    .getElementById("panel")
    ?.append(
      status,
      detail,
      element("h2", undefined, "The cycle"),
      cycleRow,
      previewRow,
      element("h2", undefined, "Phase groups"),
      addGroupButton,
      groupList,
      element("h2", undefined, "Conflicts"),
      conflictBox,
      saveButton,
      loadLabel,
      loadReport,
      loadNote,
      adoptedNote,
    );

  function groupOf(laneId: string): number {
    return groups.findIndex((group) => group.lanes.includes(laneId));
  }

  function paletteFor(index: number): string {
    return PALETTE[index % PALETTE.length] ?? "#1c7ed6";
  }

  function styleFor(laneId: string): Record<string, unknown> {
    const index = groupOf(laneId);
    const group = groups[index];
    if (index < 0 || !group) {
      return surveyedLanes.has(laneId) ? SURVEYED : IDLE;
    }
    // The colour is the light's, not the group's: at the previewed moment this lane is
    // showing green, yellow or red, and that is what has to be legible when two arms are
    // being checked against each other. The group's own hue does the identifying, as the
    // outline weight.
    return {
      ...(index === active ? IN_GROUP : OTHER_GROUP),
      color: colourAt(previewSeconds, group, cycleSeconds),
    };
  }

  function redraw(): void {
    for (const [laneId, layer] of layers) layer.setStyle?.({ ...styleFor(laneId) });
    syncStopMarkers();
  }

  /** Draw, restyle and remove the lights, following the groups.
   *
   * The lane is coloured along its whole length because that is what has to be legible from
   * across the junction; the dot is where the light *is*. MetaDrive builds a 0.25 m wall at
   * `signal_plan.stop_points` - the lane's downstream end - and nothing on this page said
   * where that was.
   */
  function syncStopMarkers(): void {
    const drawn = new Set<string>();
    for (const [index, group] of groups.entries()) {
      const colour = colourAt(previewSeconds, group, cycleSeconds);
      for (const laneId of group.lanes) {
        const lane = byId.get(laneId);
        const point = lane ? stopPoint(lane) : null;
        if (!lane || !point) continue;
        drawn.add(laneId);
        const style = stopMarkerStyle(colour, index === active);
        const existing = stopMarkers.get(laneId);
        if (existing) {
          existing.setStyle?.(style);
          existing.bringToFront?.();
          continue;
        }
        const marker = L.circleMarker(point, { ...style, renderer });
        // The dot sits on top of the line it belongs to, so it has to answer a click the same
        // way - otherwise the one place you would aim to remove a light is the one place that
        // cannot.
        marker.on("click", () => pick(lane));
        marker.bindPopup?.(() => popupFor(lane));
        marker.addTo(map);
        marker.bringToFront?.();
        stopMarkers.set(laneId, marker);
      }
    }
    for (const [laneId, marker] of stopMarkers) {
      if (drawn.has(laneId)) continue;
      marker.remove?.();
      stopMarkers.delete(laneId);
    }
  }

  function refreshStatus(): void {
    const signalled = groups.reduce((total, group) => total + group.lanes.length, 0);
    if (groups.length === 0) {
      status.textContent = "Add a phase group, then click the lanes it stops.";
      detail.textContent =
        "OSM records that a signal exists and never how it is timed, so every number on " +
        "this page is one you choose. The converter marks the result as synthesised.";
      return;
    }
    const group = groups[active];
    if (!group) {
      status.textContent = "Select a phase group to add lanes to it.";
      detail.textContent = "";
      return;
    }
    status.textContent = `${group.name}: ${group.lanes.length} lane${
      group.lanes.length === 1 ? "" : "s"
    }, ${signalled} in the plan.`;
    detail.textContent =
      "Click a lane to signal it; click it again to take the light away. A light stops " +
      "traffic leaving the lane, so it sits at that lane's far end - the circle is where " +
      "the stop line goes.";
  }

  function renderConflicts(): void {
    conflictBox.replaceChildren();
    const conflicts = findConflicts(groups, cycleSeconds, payload.connectors, laneLines);
    const clashing = conflicts.filter((conflict) => conflict.overlapSeconds > 0);
    if (groups.length < 2) {
      conflictBox.append(
        element(
          "p",
          "caption",
          "Nothing to check until there are two phase groups: a conflict is two streams " +
            "green at once.",
        ),
      );
      return;
    }
    if (conflicts.length === 0) {
      conflictBox.append(
        element(
          "p",
          "ok",
          `No two groups' traffic meets within ${DOWNSTREAM_M} m of a stop line.`,
        ),
      );
      return;
    }
    const heading = clashing.length === 0 ? "ok" : "bad";
    conflictBox.append(
      element(
        "p",
        heading,
        // Counted in meetings and not in pairs: one pair can meet twice at one node, as a
        // merge and as a crossing, and calling three rows "3 pairs" is simply wrong.
        clashing.length === 0
          ? `${conflicts.length} meeting${conflicts.length === 1 ? "" : "s"} between groups, and this plan keeps them apart.`
          : `${clashing.length} meeting${clashing.length === 1 ? " is" : "s are"} green together.`,
      ),
    );
    if (clashing.length > 0) conflictBox.append(autoButton);
    if (autoNote) conflictBox.append(element("p", autoFailed ? "bad" : "caption", autoNote));
    if (autoUndo) conflictBox.append(undoButton);
    for (const conflict of conflicts) describeConflict(conflict);
  }

  /** Stop offering to undo a re-timing that no longer describes what is on screen. */
  function forgetAutoTiming(): void {
    autoUndo = null;
    autoNote = "";
    autoFailed = false;
  }

  autoButton.addEventListener("click", () => {
    const before = groups.map((group) => ({ ...group }));
    let proposed;
    try {
      proposed = autoPhase(groups, cycleSeconds, findConflicts(groups, cycleSeconds, payload.connectors, laneLines));
    } catch (error) {
      forgetAutoTiming();
      autoFailed = true;
      autoNote = error instanceof AutoPhaseError ? error.message : "Could not re-time this plan.";
      renderAll();
      return;
    }
    // The arithmetic says the stages cannot overlap. Checking anyway costs nothing and is
    // what would catch a future change to the reach walk quietly breaking this claim - the
    // one claim the button's label makes.
    const left = findConflicts(proposed.groups, cycleSeconds, payload.connectors, laneLines).filter(
      (conflict) => conflict.overlapSeconds > 0,
    );
    if (left.length > 0) {
      forgetAutoTiming();
      autoFailed = true;
      autoNote =
        `Re-timing would still leave ${left[0]!.a} and ${left[0]!.b} green together. ` +
        "Nothing was changed.";
      renderAll();
      return;
    }

    groups.splice(0, groups.length, ...proposed.groups);
    autoUndo = before;
    autoFailed = false;
    const stages = proposed.stages
      .map((stage, index) => `stage ${index + 1} (${stage.lengthSeconds.toFixed(1)} s): ${stage.members.join(", ")}`)
      .join("; ");
    const cut = proposed.shortened
      .map((one) => `${one.name} ${one.was.toFixed(1)} to ${one.now.toFixed(1)} s`)
      .join(", ");
    autoNote =
      `Re-timed into ${proposed.stages.length} stages - ${stages}. ` +
      (cut ? `Greens shortened to fit: ${cut}.` : "No green was shortened.") +
      " Which lanes are in which group is untouched.";
    renderAll();
  });

  undoButton.addEventListener("click", () => {
    if (!autoUndo) return;
    groups.splice(0, groups.length, ...autoUndo);
    forgetAutoTiming();
    renderAll();
  });

  function describeConflict(conflict: Conflict): void {
    const row = element("div", conflict.overlapSeconds > 0 ? "crow bad" : "crow");
    const what = conflict.kind === "merge" ? "merge into one lane" : "cross";
    row.append(
      element("div", undefined, `${conflict.a} and ${conflict.b} ${what} at node ${conflict.junction}`),
      element(
        "div",
        "n",
        conflict.overlapSeconds > 0
          ? `green together for ${conflict.overlapSeconds.toFixed(1)} s of every ${cycleSeconds.toFixed(0)} s`
          : "never green together",
      ),
    );
    const where = reachNote(conflict);
    if (where) row.append(element("div", "n", where));
    conflictBox.append(row);
  }

  /** Which side of a conflict is not standing at its own stop line, and how far past it.
   *
   * A staggered junction is the whole reason the pair was found, so naming only the node
   * sends a person hunting for a light that is not there. Below 0.05 m is the stop line
   * itself and saying so would be noise on every row.
   */
  function reachNote(conflict: Conflict): string | null {
    const away = [
      { name: conflict.a, metres: conflict.metres.a },
      { name: conflict.b, metres: conflict.metres.b },
    ].filter((side) => side.metres > 0.05);
    if (away.length === 0) return null;
    return away
      .map((side) => `${side.name}'s traffic gets there ${side.metres.toFixed(1)} m past its light`)
      .join("; ");
  }

  function renderGroups(): void {
    groupList.replaceChildren();
    for (const [index, group] of groups.entries()) {
      const card = element("div", index === active ? "gcard active" : "gcard");
      card.style.borderLeftColor = paletteFor(index);

      const head = element("div", "ghead");
      const name = element("input");
      name.type = "text";
      name.value = group.name;
      name.addEventListener("change", () => {
        const problem = nameProblem(
          name.value.trim(),
          groups.filter((_, other) => other !== index).map((other) => other.name),
        );
        if (problem) {
          note.textContent = problem;
          name.value = group.name;
          return;
        }
        group.name = name.value.trim();
        note.textContent = "";
        renderAll();
      });
      const remove = element("button", "link", "remove");
      remove.addEventListener("click", () => {
        groups.splice(index, 1);
        if (active >= groups.length) active = groups.length - 1;
        forgetAutoTiming();
        renderAll();
      });
      head.append(name, remove);

      const timings = element("div", "row");
      const green = numberField(group.green_seconds, 0.5);
      const yellow = numberField(group.yellow_seconds, 0.5);
      const offset = numberField(group.offset_seconds, 0.5);
      timings.append(
        element("label", undefined, "green"),
        green,
        element("label", undefined, "yellow"),
        yellow,
        element("label", undefined, "starts at"),
        offset,
      );

      const strip = element("div", "strip");
      const note = element("p", "caption");

      const apply = (): void => {
        const candidate = {
          green_seconds: Number(green.value),
          yellow_seconds: Number(yellow.value),
          offset_seconds: Number(offset.value),
        };
        const problem = timingProblem(candidate, cycleSeconds);
        note.textContent = problem ?? "";
        if (problem) return;
        Object.assign(group, candidate);
        forgetAutoTiming();
        renderAll();
      };
      for (const field of [green, yellow, offset]) field.addEventListener("change", apply);

      strip.style.background = phaseStripCss(group, cycleSeconds);
      const counts = element(
        "p",
        "caption",
        `${group.lanes.length} lane${group.lanes.length === 1 ? "" : "s"} · red for ${(
          cycleSeconds - group.green_seconds - group.yellow_seconds
        ).toFixed(1)} s`,
      );

      card.addEventListener("click", (event) => {
        if (event.target === remove) return;
        active = index;
        renderAll();
      });
      card.append(head, timings, strip, counts, note);
      groupList.append(card);
    }
    saveButton.disabled = groups.length === 0 || groups.some((group) => group.lanes.length === 0);
  }

  function renderAll(): void {
    previewInput.max = String(cycleSeconds);
    previewLabel.textContent = `${previewSeconds.toFixed(1)} s`;
    renderGroups();
    renderConflicts();
    refreshStatus();
    redraw();
  }

  function pick(lane: SignalLane): void {
    const target = groups[active];
    if (!target) {
      status.textContent = "Add a phase group first - a light has to belong to one.";
      return;
    }
    const current = groupOf(lane.id);
    if (current === active) {
      target.lanes = target.lanes.filter((id) => id !== lane.id);
      renderAll();
      return;
    }
    // Moved rather than refused: a lane in the wrong group is the commonest mistake here,
    // and making it a two-step correction would be worse than saying what happened.
    const previous = groups[current];
    if (previous) {
      previous.lanes = previous.lanes.filter((id) => id !== lane.id);
      detail.textContent = `Moved that lane out of ${previous.name}.`;
    }
    target.lanes.push(lane.id);
    renderAll();
  }

  addGroupButton.addEventListener("click", () => {
    const index = groups.length;
    // Evenly spaced starts, so a second group is separated from the first before anything is
    // typed. It is a starting point and not a plan - the conflict panel is what says whether
    // it works.
    groups.push({
      name: `phase-${String.fromCharCode(97 + (index % 26))}`,
      lanes: [],
      green_seconds: payload.defaults.green_seconds,
      yellow_seconds: payload.defaults.yellow_seconds,
      offset_seconds: Number((((cycleSeconds / (index + 1)) * index) % cycleSeconds).toFixed(1)),
    });
    active = index;
    forgetAutoTiming();
    renderAll();
  });

  cycleInput.addEventListener("change", () => {
    const value = Number(cycleInput.value);
    if (!Number.isFinite(value) || value <= 0) {
      cycleInput.value = String(cycleSeconds);
      return;
    }
    const offending = groups.find((group) => timingProblem(group, value) !== null);
    if (offending) {
      detail.textContent = `${offending.name} does not fit a ${value} s cycle. Shorten its green first.`;
      cycleInput.value = String(cycleSeconds);
      return;
    }
    cycleSeconds = value;
    forgetAutoTiming();
    previewSeconds = Math.min(previewSeconds, cycleSeconds);
    previewInput.value = String(previewSeconds);
    renderAll();
  });

  previewInput.addEventListener("input", () => {
    previewSeconds = Number(previewInput.value);
    previewLabel.textContent = `${previewSeconds.toFixed(1)} s`;
    redraw();
  });

  /** Where every signalled lane's light currently sits, for the file to carry. */
  function drawnAt(): Record<string, [number, number]> {
    const points: Record<string, [number, number]> = {};
    for (const group of groups) {
      for (const laneId of group.lanes) {
        const lane = byId.get(laneId);
        const point = lane ? stopPoint(lane) : null;
        if (point) points[laneId] = point;
      }
    }
    return points;
  }

  saveButton.addEventListener("click", () => {
    download(
      payload.suggested_filename,
      serializeSignals(
        payload.identity,
        cycleSeconds,
        groups,
        payload.signals_version,
        drawnAt(),
      ),
    );
    // The download carries *this* map's identity, so a plan adopted from an earlier
    // generation is now one `convert --signals` accepts. That re-stamping is the whole
    // point of the round trip, and it is what the standing warning was waiting for.
    adoptedNote.textContent = "";
  });

  /** Adopt an inspected plan, whatever the report said about it. */
  function adopt(inspection: SignalsInspection): void {
    cycleSeconds = inspection.plan.cycleSeconds;
    cycleInput.value = String(cycleSeconds);
    groups.splice(0, groups.length, ...inspection.plan.groups);
    active = groups.length > 0 ? 0 : -1;
    forgetAutoTiming();
    loadNote.textContent = "";
    loadReport.replaceChildren();
    const drawnOn = inspection.identityProblems.find((p) => p.field === "generation");
    adoptedNote.textContent = drawnOn
      ? `Adopted from generation ${drawnOn.was.slice(0, 8)}. Check every light against the ` +
        "circles, then download to re-stamp it for this map."
      : "";
    renderAll();
  }

  /** What the file would do on this map, and the choice about whether to take it. */
  function renderReport(inspection: SignalsInspection): void {
    loadReport.replaceChildren();
    const signalled = inspection.plan.groups.reduce((n, g) => n + g.lanes.length, 0);
    const total = signalled + inspection.missingLanes.length;

    loadReport.append(element("p", "bad", "This plan was not drawn on this map."));
    for (const problem of inspection.identityProblems) {
      const row = element("div", "crow");
      row.append(
        element("div", undefined, problem.field),
        element("div", "n", `${problem.was.slice(0, 8)} \u2192 ${problem.now.slice(0, 8)}`),
      );
      loadReport.append(row);
    }
    loadReport.append(
      element("p", "caption", `${signalled} of ${total} lanes it names are still on this map.`),
    );
    for (const { group, lane } of inspection.missingLanes) {
      const row = element("div", "crow bad");
      row.append(element("div", undefined, `${lane} is not on this map`), element("div", "n", group));
      loadReport.append(row);
    }
    if (inspection.droppedGroups.length > 0) {
      loadReport.append(
        element(
          "p",
          "bad",
          `Dropped, having no lanes left: ${inspection.droppedGroups.join(", ")}.`,
        ),
      );
    }
    // The check that actually distinguishes a moved map from a re-stamped one. A lane id
    // carries no lane_count, so an id can survive while the lane it names sits somewhere
    // else across the carriageway - this is the only thing that would say so.
    for (const { lane, metres } of inspection.movedLanes) {
      const row = element("div", "crow bad");
      row.append(
        element("div", undefined, `${lane} has moved`),
        element("div", "n", `${metres.toFixed(1)} m`),
      );
      loadReport.append(row);
    }
    loadReport.append(
      element(
        "p",
        "caption",
        inspection.movedLanes.length > 0
          ? "A light that has moved is on a lane that kept its id but not its place. Check " +
            "those against the circles before taking this plan."
          : inspection.records
            ? "No light has moved since this plan was drawn."
            : "This file predates the record of where its lights were drawn, so nothing " +
              "here can say whether they have moved. Check them against the circles.",
      ),
    );

    const buttons = element("div", "row");
    const yes = element("button", undefined, "Load it anyway");
    const no = element("button", undefined, "Cancel");
    yes.addEventListener("click", () => adopt(inspection));
    no.addEventListener("click", () => {
      loadReport.replaceChildren();
      loadNote.textContent = "Left as it was.";
    });
    buttons.append(yes, no);
    loadReport.append(buttons);
  }

  loadInput.addEventListener("change", () => {
    const file = loadInput.files?.[0];
    if (!file) return;
    void file.text().then((raw) => {
      try {
        const inspection = inspectSignals(
          raw,
          payload.identity,
          payload.signals_version,
          stopPoints,
        );
        const clean =
          inspection.identityProblems.length === 0 &&
          inspection.missingLanes.length === 0 &&
          inspection.movedLanes.length === 0;
        // A plan that belongs to this map loads on the one click it always did. Only a plan
        // that does not gets the report and the second click.
        if (clean) {
          adopt(inspection);
        } else {
          loadNote.textContent = "";
          renderReport(inspection);
        }
      } catch (error) {
        loadReport.replaceChildren();
        loadNote.textContent =
          error instanceof SignalsFileError ? error.message : "Could not read that file.";
      }
      loadInput.value = "";
    });
  });

  // The surveyed signals, drawn once and never selected. See `SurveyedSignal`.
  for (const signal of payload.surveyed) {
    for (const laneId of signal.lanes) {
      layers.get(laneId)?.bringToFront?.();
    }
  }
  previewInput.value = "0";
  renderAll();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
