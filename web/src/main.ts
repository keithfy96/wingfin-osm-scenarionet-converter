// Entry point. The Python renderer writes `window.__REVIEW_PAYLOAD__` and an
// #app / #map pair, then loads this bundle.

import styles from "./style.css";
import { buildPopup, FeatureIndex } from "./details.js";
import { buildOverlays, clearFocus, focusFeatures, type OverlayIndex } from "./overlays.js";
import { ReviewPanel } from "./panel.js";
import {
  clearDraft,
  debounce,
  findRecoverableDrafts,
  loadDraft,
  saveDraft,
} from "./persistence.js";
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

  const persist = debounce(() => {
    saveDraft(window.localStorage, payload.identity, state.allDecisions());
    draftStatus.textContent = `Draft saved locally at ${new Date().toLocaleTimeString()}. Drafts are not authoritative — export to hand to Stage 4.`;
  }, 400);

  panel = new ReviewPanel(app, state, payload, features, {
    onFocus(finding: Finding) {
      clearFocus(index, focused);
      // `source_ids` are raw OSM ids; the drawn source geometry is keyed
      // `way:<id>` / `node:<id>`, which is what `source_geometry_ids` already holds.
      const generated = [...new Set([...finding.affected_feature_ids, ...finding.geometry_ids])];
      const ends = features.movementEnds(generated);
      focused = [...generated, ...finding.source_geometry_ids, ...ends.entry, ...ends.exit];
      return focusFeatures(map, index, {
        generated,
        source: finding.source_geometry_ids,
        ...ends,
      });
    },
    onFocusFeature: focusFeature,
    onChanged() {
      persist();
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
      if (relation === "foreign") {
        draftStatus.textContent =
          "That review belongs to a different workspace or a different source OSM. Not loaded.";
        return;
      }
      const summary = state.loadDecisions(submission.decisions);
      const prefix =
        relation === "regenerated"
          ? "Loaded against a regenerated model. "
          : "Loaded. ";
      draftStatus.textContent =
        `${prefix}${summary.carried} decision(s) carried over, ` +
        `${summary.invalidated} invalidated by changed evidence, ` +
        `${summary.unknown} for findings that no longer exist. ` +
        `Invalidated findings are back to unresolved.`;
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
  resetButton.textContent = "Reset draft";
  resetButton.addEventListener("click", () => {
    if (!window.confirm("Discard every decision recorded in this browser?")) return;
    state.reset();
    clearDraft(window.localStorage, payload.identity);
    draftStatus.textContent = "Draft cleared.";
  });

  toolbar.append(exportButton, importButton, resetButton, importInput, draftStatus);
  app.append(toolbar);

  const draft = loadDraft(window.localStorage, payload.identity);
  if (draft?.decisions.length) {
    const summary = state.loadDecisions(draft.decisions);
    draftStatus.textContent = `Restored ${summary.carried} decision(s) from a local draft saved ${draft.saved_at}.`;
  } else {
    // Nothing for this generation. A draft from an earlier one is still this map's
    // work, so it is offered back rather than left stranded by a regenerate — but
    // through loadDecisions, which drops anything whose evidence has moved.
    const [recoverable] = findRecoverableDrafts(window.localStorage, payload.identity);
    if (recoverable) {
      const summary = state.loadDecisions(recoverable.decisions);
      draftStatus.textContent =
        `Recovered a draft saved ${recoverable.saved_at}, from an earlier generation of ` +
        `this map: ${summary.carried} decision(s) carried over, ` +
        `${summary.invalidated} invalidated by changed evidence, ` +
        `${summary.unknown} for findings that no longer exist.`;
    } else {
      draftStatus.textContent =
        "No local draft yet. Every decision autosaves to this browser as you make it.";
    }
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
