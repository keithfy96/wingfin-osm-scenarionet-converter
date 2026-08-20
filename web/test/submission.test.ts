import { describe, expect, it } from "vitest";

import { ReviewState } from "../src/state.js";
import { compareIdentity, parseSubmission, serializeSubmission, SubmissionError } from "../src/submission.js";
import { finding, IDENTITY, payload } from "./fixtures.js";

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
    expect(() => parseSubmission(JSON.stringify({ ...valid, submission_version: 5 }))).toThrow(
      SubmissionError,
    );
  });

  it("still loads a version 3 review, whose decisions carry no proposed value", () => {
    // The version people already have in hand. It has to keep loading, and keep applying:
    // a schema bump that stranded a finished review would cost a whole re-review.
    const older = { ...valid, submission_version: 3 };
    const parsed = parseSubmission(JSON.stringify(older));
    expect(parsed.decisions[0]?.proposed_value).toBeUndefined();
    expect(parsed.decisions[0]?.status).toBe("accepted");
  });

  it("accepts an ignored decision, which only version 3 can carry", () => {
    const ignored = {
      ...valid,
      submission_version: 3,
      decisions: [{ ...valid.decisions[0], status: "ignored" }],
    };
    expect(parseSubmission(JSON.stringify(ignored)).decisions[0]?.status).toBe("ignored");
  });

  it("still accepts a version 1 export, which is version 2 without locations", () => {
    // Locations annotate a decision; they are not part of what was decided, so a
    // review exported before they existed must not become unreadable.
    const loaded = parseSubmission(JSON.stringify(valid));
    expect(loaded.submission_version).toBe(1);
    expect(loaded.decisions[0]?.location).toBeUndefined();
    expect(loaded.decisions[0]?.status).toBe("accepted");
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
  it("calls the same source OSM in another workspace relocated, not a different map", () => {
    expect(compareIdentity(IDENTITY, { ...IDENTITY, workspace: "other" })).toBe("relocated");
  });

  it("calls a different source OSM other-map, whatever the workspace is called", () => {
    expect(compareIdentity(IDENTITY, { ...IDENTITY, source_checksum: "other" })).toBe("other-map");
    expect(
      compareIdentity(IDENTITY, { ...IDENTITY, workspace: "other", source_checksum: "other" }),
    ).toBe("other-map");
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

  it("survives export and re-import while still unfinished", () => {
    // Partial work is the normal state of a long review; it has to be resumable on
    // another machine, not only in the browser that recorded it.
    const findings = [finding({ severity: "blocker" }), finding({ identifier: "f2" })];
    const review = new ReviewState(payload(findings), clock);
    review.decide("f2", { status: "accepted" });

    const submission = parseSubmission(serializeSubmission(review.toSubmission()));
    expect(submission.readiness.ready).toBe(false);
    expect(submission.decisions).toHaveLength(1);

    const reloaded = new ReviewState(payload(findings), clock);
    expect(reloaded.loadDecisions(submission.decisions).carried).toBe(1);
    expect(reloaded.statusOf("f2")).toBe("accepted");
    expect(reloaded.statusOf("f1")).toBe("unresolved");
  });
});

