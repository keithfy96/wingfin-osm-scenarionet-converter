import { describe, expect, it } from "vitest";

import { purgeLegacyDrafts } from "../src/legacy-drafts.js";
import { memoryStore } from "./fixtures.js";

const draftKey = (workspace: string, source: string, fingerprint: string): string =>
  ["osm-scenario.review.draft", workspace, source, fingerprint].join("|");

describe("purging drafts left by older builds", () => {
  it("removes every draft, whatever workspace or generation it belongs to", () => {
    const store = memoryStore();
    store.setItem(draftKey("junction-1", "source-aaa", "fingerprint-aaa"), "{}");
    store.setItem(draftKey("junction-1", "source-bbb", "fingerprint-bbb"), "{}");
    store.setItem(draftKey("mosque", "source-ccc", "fingerprint-ccc"), "{}");

    expect(purgeLegacyDrafts(store)).toBe(3);
    expect(store.length).toBe(0);
  });

  it("leaves keys belonging to anything else alone", () => {
    const store = memoryStore();
    store.setItem("osm-scenario.review.draft|junction-1|source-aaa|fingerprint-aaa", "{}");
    store.setItem("some-other-app.state", "keep me");

    expect(purgeLegacyDrafts(store)).toBe(1);
    expect(store.getItem("some-other-app.state")).toBe("keep me");
  });

  it("removes every match rather than stopping short when the store reindexes", () => {
    // Deleting while enumerating shifts later keys down into slots already passed,
    // so a naive single pass silently leaves half the drafts behind.
    const store = memoryStore();
    for (let index = 0; index < 8; index += 1) {
      store.setItem(draftKey("junction-1", "source-aaa", `fingerprint-${index}`), "{}");
    }

    expect(purgeLegacyDrafts(store)).toBe(8);
    expect(store.length).toBe(0);
  });

  it("reports nothing to do on a clean browser", () => {
    expect(purgeLegacyDrafts(memoryStore())).toBe(0);
  });
});
