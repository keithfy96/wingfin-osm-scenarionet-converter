import { describe, expect, it } from "vitest";

import { findRecoverableDrafts, loadDraft, saveDraft } from "../src/persistence.js";
import type { Decision } from "../src/types.js";
import { IDENTITY, memoryStore } from "./fixtures.js";

function decision(findingId: string): Decision {
  return {
    finding_id: findingId,
    rule: "speed_default",
    status: "accepted",
    decided_at: "2026-08-09T10:00:00+00:00",
    evidence_checksum: "evidence-1",
  };
}

describe("recovering a draft across a regeneration", () => {
  it("offers a draft saved under an earlier generation of the same map", () => {
    const store = memoryStore();
    saveDraft(store, IDENTITY, [decision("f1")]);
    const regenerated = { ...IDENTITY, generation_fingerprint: "fingerprint-bbb" };

    // The fingerprint moved, so the exact key finds nothing — which is what stranded
    // real work when the generator was rebuilt mid-review.
    expect(loadDraft(store, regenerated)).toBeNull();
    const recoverable = findRecoverableDrafts(store, regenerated);
    expect(recoverable).toHaveLength(1);
    expect(recoverable[0]?.decisions.map((item) => item.finding_id)).toEqual(["f1"]);
  });

  it("never offers a draft from another workspace or another source OSM", () => {
    const store = memoryStore();
    saveDraft(store, { ...IDENTITY, workspace: "other-place" }, [decision("f1")]);
    saveDraft(store, { ...IDENTITY, source_checksum: "source-zzz" }, [decision("f2")]);

    // A different source OSM is a different map, not a stale draft of this one.
    expect(findRecoverableDrafts(store, IDENTITY)).toEqual([]);
  });

  it("prefers the most recently saved draft when a map has been regenerated twice", () => {
    const store = memoryStore();
    const draft = saveDraft(store, { ...IDENTITY, generation_fingerprint: "one" }, [
      decision("old"),
    ]);
    saveDraft(store, { ...IDENTITY, generation_fingerprint: "two" }, [decision("new")]);
    // saveDraft stamps the time itself, so make the older one unambiguously older.
    store.setItem(
      "osm-scenario.review.draft|junction-1|source-aaa|one",
      JSON.stringify({ ...draft, saved_at: "2020-01-01T00:00:00+00:00" }),
    );

    const recoverable = findRecoverableDrafts(store, IDENTITY);
    expect(recoverable.map((item) => item.decisions[0]?.finding_id)).toEqual(["new", "old"]);
  });

  it("ignores an empty or corrupt leftover rather than offering nothing useful", () => {
    const store = memoryStore();
    saveDraft(store, { ...IDENTITY, generation_fingerprint: "empty" }, []);
    store.setItem("osm-scenario.review.draft|junction-1|source-aaa|broken", "{not json");

    expect(findRecoverableDrafts(store, IDENTITY)).toEqual([]);
  });
});
