// Entry point. The Python renderer writes `window.__REVIEW_PAYLOAD__` and an
// #app / #map pair, then loads this bundle.

import styles from "./style.css";
import { buildPopup, FeatureIndex } from "./details.js";
import { buildOverlays, clearFocus, focusFeatures, type OverlayIndex } from "./overlays.js";
import { purgeLegacyDrafts } from "./legacy-drafts.js";
import { ReviewPanel } from "./panel.js";
import { ReviewState } from "./state.js";
import { compareIdentity, parseSubmission, serializeSubmission, SubmissionError } from "./submission.js";
import type { Finding, ReviewPayload } from "./types.js";
import type { LeafletMap } from "./types-dom.js";

function injectStyles(): void {
  const node = document.createElement("style");
  node.textContent = styles as unknown as string;
  document.head.append(node);
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
  const payload = (window as unknown as { __REVIEW_PAYLOAD__?: ReviewPayload }).__REVIEW_PAYLOAD__;
  const app = document.getElementById("app");
  if (!payload || !app) return;
  injectStyles();

  const state = new ReviewState(payload);
  const map: LeafletMap = L.map("map", { preferCanvas: true });
  map.setView(payload.center, 17);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  let focused: string[] = [];
  let panel: ReviewPanel | null = null;
  const features = new FeatureIndex(payload.features, payload.findings);

  // A popup and the panel light the map the same way, so both go through here.
  const focusFeature = (identifier: string): void => {
    clearFocus(index, focused);
    // Focusing a movement lights its two ends as well, so a connector on its own is
    // still read as "from this lane, into that one".
    const ends = features.movementEnds([identifier]);
    focused = [identifier, ...ends.entry, ...ends.exit];
    focusFeatures(map, index, { generated: [identifier], source: [], ...ends });
    for (const layer of index.byId.get(identifier) ?? []) layer.openPopup?.();
  };

  const index: OverlayIndex = buildOverlays(map, payload.features, (properties) =>
    buildPopup(features, properties, {
      focus: focusFeature,
      openFinding: (finding) => panel?.select(finding.identifier),
      statusOf: (findingId) => state.statusOf(findingId),
    }),
  );

  const toolbar = document.createElement("div");
  toolbar.className = "toolbar";
  const draftStatus = document.createElement("p");
  draftStatus.className = "draft-status muted";

  panel = new ReviewPanel(app, state, payload, features, {
    onFocus(finding: Finding) {
      clearFocus(index, focused);
      // `source_ids` are raw OSM ids; the drawn source geometry is keyed
      // `way:<id>` / `node:<id>`, which is what `source_geometry_ids` already holds.
      const generated = [...new Set([...finding.affected_feature_ids, ...finding.geometry_ids])];
      const ends = features.movementEnds(generated, finding.movement_roles);
      focused = [...generated, ...finding.source_geometry_ids, ...ends.entry, ...ends.exit];
      return focusFeatures(map, index, {
        generated,
        source: finding.source_geometry_ids,
        ...ends,
      });
    },
    onFocusFeature: focusFeature,
    onChanged() {
      // Nothing is written anywhere, so the only thing worth saying after a decision
      // is how much would be lost by closing the tab.
      const decided = state.allDecisions().length;
      draftStatus.textContent =
        `${decided} decision(s) held in this tab only — nothing is saved. ` +
        "Export before you close it.";
    },
  });

  const exportButton = document.createElement("button");
  exportButton.className = "primary";
  // An unfinished review is exportable, but never under a name that hides it. The
  // label and the filename both change with readiness, so neither the click nor the
  // file on disk can be mistaken for a finished review.
  const syncExportLabel = (): void => {
    exportButton.textContent = state.readiness().ready
      ? "Export review.json"
      : "Export partial review";
  };
  syncExportLabel();
  state.subscribe(syncExportLabel);

  exportButton.addEventListener("click", () => {
    try {
      const submission = state.toSubmission();
      const ready = submission.readiness.ready;
      download(ready ? "review.json" : "review.partial.json", serializeSubmission(submission));
      draftStatus.textContent = ready
        ? "Exported review.json. Pass this file to `osm-scenario apply-review`."
        : `Exported review.partial.json — ${submission.readiness.resolved} of ` +
          `${submission.readiness.total} findings decided, ` +
          `${submission.readiness.blockers_unresolved} blocker(s) still unresolved. ` +
          "Stage 4 will refuse it until they are resolved.";
    } catch (caught) {
      draftStatus.textContent = caught instanceof Error ? caught.message : String(caught);
    }
  });

  const importInput = document.createElement("input");
  importInput.type = "file";
  importInput.accept = "application/json,.json";
  importInput.style.display = "none";
  importInput.addEventListener("change", async () => {
    const file = importInput.files?.[0];
    if (!file) return;
    try {
      const submission = parseSubmission(await file.text());
      const relation = compareIdentity(payload.identity, submission.identity);
      const summary = state.loadDecisions(submission.decisions);
      // No relation is refused. A decision only lands where this model asks a
      // byte-identical question, so a review from another map fills in what
      // genuinely matches and drops the rest -- which is the whole reason
      // migration belongs in Stage 3 rather than in Stage 4.
      const from = submission.identity.workspace;
      const prefix = {
        same: "Loaded. ",
        regenerated: "Loaded against a regenerated model. ",
        relocated: `Loaded from workspace ${from}, the same source OSM. `,
        "other-map": `Loaded from workspace ${from}, a different map. `,
      }[relation];
      const missing =
        relation === "other-map"
          ? `${summary.unknown} for findings this map does not have. `
          : `${summary.unknown} for findings that no longer exist. `;
      const caution =
        relation === "other-map"
          ? "Every decision carried over answers a byte-identical finding here; check them before exporting."
          : "Invalidated findings are back to unresolved.";
      draftStatus.textContent =
        `${prefix}${summary.carried} decision(s) carried over, ` +
        `${summary.invalidated} invalidated by changed evidence, ` +
        `${missing}${caution}`;
    } catch (caught) {
      draftStatus.textContent =
        caught instanceof SubmissionError ? caught.message : `Could not read that file: ${String(caught)}`;
    } finally {
      importInput.value = "";
    }
  });

  const importButton = document.createElement("button");
  importButton.textContent = "Load review.json";
  importButton.addEventListener("click", () => importInput.click());

  const resetButton = document.createElement("button");
  resetButton.className = "ghost";
  resetButton.textContent = "Clear all decisions";
  resetButton.addEventListener("click", () => {
    if (!window.confirm("Discard every decision made in this tab?")) return;
    state.reset();
    draftStatus.textContent = "Cleared. Every finding is back to unresolved.";
  });

  toolbar.append(exportButton, importButton, resetButton, importInput, draftStatus);
  app.append(toolbar);

  // The review starts empty, every time. Decisions enter it one of two ways: made
  // here, or imported from a review.json — never restored from the browser. Older
  // builds autosaved drafts to localStorage and read them back on boot, which is
  // how a stale review could reappear over a freshly generated model.
  const purged = purgeLegacyDrafts(window.localStorage);
  draftStatus.textContent =
    `${payload.findings.length} finding(s), none decided. Nothing is saved between ` +
    "visits — load a review.json to bring decisions in, and export to take them out." +
    (purged ? ` (Removed ${purged} leftover draft(s) from an older build.)` : "");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
