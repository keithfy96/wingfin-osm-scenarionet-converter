import { describe, expect, it } from "vitest";

import { clearDraft, draftKey, loadDraft, saveDraft } from "../src/persistence.js";
import { ReviewState } from "../src/state.js";
import { compareIdentity, parseSubmission, serializeSubmission, SubmissionError } from "../src/submission.js";
import { finding, IDENTITY, memoryStore, payload } from "./fixtures.js";

const clock = () => "2026-08-08T12:00:00+00:00";

describe("parsing a submission", () => {
  const valid = {
    submission_version: 1,
    exported_at: clock(),
    identity: IDENTITY,
    decisions: [
      {
        finding_id: "f1",
        rule: "speed_default",
        status: "accepted",
        decided_at: clock(),
        evidence_checksum: "evidence-1",
      },
    ],
    readiness: { total: 1, resolved: 1, blockers_total: 0, blockers_unresolved: 0, ready: true },
  };

  it("accepts a well-formed export", () => {
    expect(parseSubmission(JSON.stringify(valid)).decisions).toHaveLength(1);
  });

  it("rejects a future submission_version rather than guessing at its shape", () => {
    expect(() => parseSubmission(JSON.stringify({ ...valid, submission_version: 2 }))).toThrow(
      SubmissionError,
    );
  });

  it("rejects a decision with no evidence checksum, which could not be checksum-bound", () => {
    const broken = { ...valid, decisions: [{ finding_id: "f1", status: "accepted" }] };
    expect(() => parseSubmission(JSON.stringify(broken))).toThrow(/evidence_checksum/);
  });

  it("rejects `not applicable` imported without a reason", () => {
    const broken = {
      ...valid,
      decisions: [{ ...valid.decisions[0], status: "not_applicable" }],
    };
    expect(() => parseSubmission(JSON.stringify(broken))).toThrow(/reason/);
  });

  it("rejects a file that is not JSON at all", () => {
    expect(() => parseSubmission("<html>")).toThrow(SubmissionError);
  });
});

describe("identity comparison", () => {
  it("calls a different workspace or source foreign, never loadable", () => {
    expect(compareIdentity(IDENTITY, { ...IDENTITY, workspace: "other" })).toBe("foreign");
    expect(compareIdentity(IDENTITY, { ...IDENTITY, source_checksum: "other" })).toBe("foreign");
  });

  it("calls a changed generation fingerprint regenerated, the only migration case", () => {
    expect(compareIdentity(IDENTITY, { ...IDENTITY, generation_fingerprint: "other" })).toBe(
      "regenerated",
    );
  });

  it("calls an identical identity the same", () => {
    expect(compareIdentity(IDENTITY, { ...IDENTITY })).toBe("same");
  });
});

describe("round trip", () => {
  it("survives export and re-import with every decision intact", () => {
    const review = new ReviewState(payload([finding({ severity: "blocker" })]), clock);
    review.decide("f1", { status: "overridden", value: { maxspeed_kph: 60 } });
    const text = serializeSubmission(review.toSubmission());

    const reloaded = new ReviewState(payload([finding({ severity: "blocker" })]), clock);
    const summary = reloaded.loadDecisions(parseSubmission(text).decisions);
    expect(summary.carried).toBe(1);
    expect(reloaded.decision("f1")?.value).toEqual({ maxspeed_kph: 60 });
  });
});

describe("draft persistence", () => {
  it("keys on workspace, source checksum and generation fingerprint together", () => {
    const other = { ...IDENTITY, generation_fingerprint: "fingerprint-bbb" };
    expect(draftKey(IDENTITY)).not.toBe(draftKey(other));
  });

  it("does not offer a draft saved against a different generation", () => {
    const store = memoryStore();
    saveDraft(store, IDENTITY, [], clock);
    expect(loadDraft(store, IDENTITY)).not.toBeNull();
    expect(loadDraft(store, { ...IDENTITY, generation_fingerprint: "fingerprint-bbb" })).toBeNull();
  });

  it("drops a corrupt draft instead of blocking the review", () => {
    const store = memoryStore();
    store.setItem(draftKey(IDENTITY), "{not json");
    expect(loadDraft(store, IDENTITY)).toBeNull();
  });

  it("clears on request", () => {
    const store = memoryStore();
    saveDraft(store, IDENTITY, [], clock);
    clearDraft(store, IDENTITY);
    expect(loadDraft(store, IDENTITY)).toBeNull();
  });
});
