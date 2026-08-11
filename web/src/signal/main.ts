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

import { findConflicts, type Conflict } from "./conflicts.js";
import { colourAt, phaseStripCss } from "./phase.js";
import { nameProblem, parseSignals, SignalsFileError, serializeSignals, timingProblem } from "./plan-file.js";
import type { PhaseGroup, SignalBuilderPayload, SignalLane } from "./types.js";
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
  const knownLanes = new Set(byId.keys());
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

  const layers = new Map<string, LeafletLayer>();
  for (const lane of payload.lanes) {
    const line = L.polyline(lane.line, { ...IDLE, renderer });
    line.on("click", () => pick(lane));
    line.bindPopup?.(() => {
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
    });
    line.addTo(map);
    layers.set(lane.id, line);
  }

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
  const saveButton = element("button", "primary", "Download signals.json");
  const loadLabel = element("label", "loadbtn", "Load an existing signals.json");
  const loadInput = element("input");
  loadInput.type = "file";
  loadInput.accept = "application/json,.json";
  loadLabel.append(loadInput);
  const loadNote = element("p", "caption");

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
      loadNote,
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
      "traffic leaving the lane, so it sits at that lane's far end.";
  }

  function renderConflicts(): void {
    conflictBox.replaceChildren();
    const conflicts = findConflicts(groups, cycleSeconds, payload.connectors);
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
        element("p", "ok", "No two groups have movements that meet at the same junction."),
      );
      return;
    }
    const heading = clashing.length === 0 ? "ok" : "bad";
    conflictBox.append(
      element(
        "p",
        heading,
        clashing.length === 0
          ? `${conflicts.length} pair${conflicts.length === 1 ? "" : "s"} meet, and this plan keeps them apart.`
          : `${clashing.length} pair${clashing.length === 1 ? " is" : "s are"} green together.`,
      ),
    );
    for (const conflict of conflicts) describeConflict(conflict);
  }

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
    conflictBox.append(row);
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
    previewSeconds = Math.min(previewSeconds, cycleSeconds);
    previewInput.value = String(previewSeconds);
    renderAll();
  });

  previewInput.addEventListener("input", () => {
    previewSeconds = Number(previewInput.value);
    previewLabel.textContent = `${previewSeconds.toFixed(1)} s`;
    redraw();
  });

  saveButton.addEventListener("click", () => {
    download(
      payload.suggested_filename,
      serializeSignals(payload.identity, cycleSeconds, groups, payload.signals_version),
    );
  });

  loadInput.addEventListener("change", () => {
    const file = loadInput.files?.[0];
    if (!file) return;
    void file.text().then((raw) => {
      try {
        const loaded = parseSignals(raw, payload.identity, payload.signals_version, knownLanes);
        cycleSeconds = loaded.cycleSeconds;
        cycleInput.value = String(cycleSeconds);
        groups.splice(0, groups.length, ...loaded.groups);
        active = groups.length > 0 ? 0 : -1;
        loadNote.textContent = "";
        renderAll();
      } catch (error) {
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
