import { describe, expect, it } from "vitest";

import { DecisionError, ReviewState } from "../src/state.js";
import { finding, payload } from "./fixtures.js";

const clock = () => "2026-08-08T12:00:00+00:00";

function state(findings = [finding()]) {
  return new ReviewState(payload(findings), clock);
}

describe("recording a decision", () => {
  it("keeps the evidence checksum the decision was made against", () => {
    const review = state();
    review.decide("f1", { status: "accepted" });
    // Compared whole rather than field by field, so a field added to a decision
    // has to be a deliberate change here too.
    expect(review.decision("f1")).toEqual({
      finding_id: "f1",
      rule: "speed_default",
      status: "accepted",
      decided_at: clock(),
      evidence_checksum: "evidence-1",
      location: finding().location,
      source_type: "way",
      source_ids: ["776021091"],
    });
  });

  it("refuses `not applicable` without a reason, because the plan requires one", () => {
    const review = state();
    expect(() => review.decide("f1", { status: "not_applicable" })).toThrow(DecisionError);
    expect(() => review.decide("f1", { status: "not_applicable", reason: "   " })).toThrow(DecisionError);
    review.decide("f1", { status: "not_applicable", reason: " private access " });
    expect(review.decision("f1")?.reason).toBe("private access");
  });

  it("refuses an override that carries no replacement value", () => {
    const review = state();
    expect(() => review.decide("f1", { status: "overridden" })).toThrow(DecisionError);
  });

  it("treats `unresolved` as clearing the decision rather than storing one", () => {
    const review = state();
    review.decide("f1", { status: "accepted" });
    review.decide("f1", { status: "unresolved" });
    expect(review.decision("f1")).toBeUndefined();
    expect(review.statusOf("f1")).toBe("unresolved");
  });
});

describe("bulk decisions", () => {
  const cohort = [
    finding({ identifier: "a", road_class: "secondary" }),
    finding({ identifier: "b", road_class: "secondary" }),
    finding({ identifier: "c", road_class: "residential" }),
    finding({ identifier: "d", rule: "lane_width_default", road_class: "secondary" }),
  ];

  it("applies only within one rule and one road class", () => {
    const review = state(cohort);
    const touched = review.decideBulk(
      { rule: "speed_default", roadClass: "secondary" },
      { status: "accepted" },
    );
    expect(touched).toEqual(["a", "b"]);
    expect(review.statusOf("c")).toBe("unresolved");
    expect(review.statusOf("d")).toBe("unresolved");
  });

  it("writes one record per finding so every feature stays explicit", () => {
    const review = state(cohort);
    review.decideBulk({ rule: "speed_default", roadClass: "secondary" }, { status: "accepted" });
    expect(review.allDecisions().map((entry) => entry.finding_id)).toEqual(["a", "b"]);
  });
});

describe("readiness", () => {
  it("blocks export while any blocker is unresolved, ignoring undecided warnings", () => {
    const review = state([
      finding({ identifier: "w", severity: "warning" }),
      finding({ identifier: "b", severity: "blocker", rule: "ambiguous_connector" }),
    ]);
    expect(review.readiness()).toMatchObject({ blockers_unresolved: 1, ready: false });

    // An unfinished review still exports, carrying the truth about how unfinished it
    // is. Stage 4 is the promotion gate; refusing to write the file only stranded
    // work that the reviewer had already done.
    const partial = review.toSubmission();
    expect(partial.readiness.ready).toBe(false);
    expect(partial.readiness.blockers_unresolved).toBe(1);
    expect(partial.decisions).toEqual([]);

    review.decide("b", { status: "accepted" });
    expect(review.readiness()).toMatchObject({ blockers_unresolved: 0, ready: true });
    expect(review.toSubmission().decisions).toHaveLength(1);
  });

  it("carries the finding's location onto the decision it exports", () => {
    // The exported answers are joined against a GPS track on their own, so they have
    // to place themselves rather than sending the reader back to preliminary.json.
    const located = finding({ identifier: "b", severity: "blocker" });
    const review = state([located]);
    review.decide("b", { status: "accepted" });

    const submission = review.toSubmission();
    expect(submission.submission_version).toBe(3);
    expect(submission.decisions[0]?.location).toEqual(located.location);
    expect(submission.decisions[0]?.source_ids).toEqual(located.source_ids);
  });
});

describe("ignoring a warning", () => {
  it("sets a warning aside without counting it as judged", () => {
    const review = state([
      finding({ identifier: "w", severity: "warning" }),
      finding({ identifier: "b", severity: "blocker", rule: "ambiguous_connector" }),
    ]);
    review.decide("w", { status: "ignored" });

    // "Decided" has to keep meaning judged, or the banner overstates how much of the
    // map a reviewer has actually looked at.
    expect(review.readiness()).toMatchObject({ resolved: 0, ignored: 1, ready: false });
    expect(review.statusOf("w")).toBe("ignored");
  });

  it("refuses to ignore a blocker, which would wave the Stage 4 gate through", () => {
    const review = state([finding({ identifier: "b", severity: "blocker" })]);
    expect(() => review.decide("b", { status: "ignored" })).toThrow(DecisionError);
    expect(review.statusOf("b")).toBe("unresolved");
  });

  it("drops an ignored blocker arriving from a file rather than trusting it", () => {
    const blocker = finding({ identifier: "b", severity: "blocker" });
    const review = state([blocker]);
    const summary = review.loadDecisions([
      {
        finding_id: "b",
        rule: blocker.rule,
        status: "ignored",
        decided_at: clock(),
        evidence_checksum: blocker.evidence_checksum,
      },
    ]);
    // The file is not the authority on what may be ignored.
    expect(summary).toMatchObject({ carried: 0, invalidated: 1 });
    expect(review.statusOf("b")).toBe("unresolved");
  });

  it("ignores a whole rule across every road class, or one class alone", () => {
    const review = state([
      finding({ identifier: "a", severity: "warning", road_class: "residential" }),
      finding({ identifier: "b", severity: "warning", road_class: "tertiary" }),
      finding({ identifier: "c", severity: "warning", road_class: null }),
    ]);

    // Omitting roadClass means every class; `null` still means the unclassified ones.
    expect(review.decideBulk({ rule: "speed_default" }, { status: "ignored" })).toHaveLength(3);
    review.reset();
    expect(
      review.decideBulk({ rule: "speed_default", roadClass: null }, { status: "ignored" }),
    ).toEqual(["c"]);
  });
});

describe("loading prior decisions", () => {
  const prior = [
    {
      finding_id: "f1",
      rule: "speed_default",
      status: "accepted" as const,
      decided_at: clock(),
      evidence_checksum: "evidence-1",
    },
    {
      finding_id: "moved",
      rule: "speed_default",
      status: "accepted" as const,
      decided_at: clock(),
      evidence_checksum: "stale",
    },
    {
      finding_id: "gone",
      rule: "speed_default",
      status: "accepted" as const,
      decided_at: clock(),
      evidence_checksum: "evidence-9",
    },
  ];

  it("carries over only decisions whose evidence is unchanged", () => {
    const review = state([finding(), finding({ identifier: "moved", evidence_checksum: "evidence-2" })]);
    const summary = review.loadDecisions(prior);
    expect(summary).toMatchObject({ carried: 1, invalidated: 1, unknown: 1 });
    expect(summary.invalidated_ids).toEqual(["moved"]);
    expect(review.statusOf("f1")).toBe("accepted");
    // Evidence moved under it, so it must be judged again rather than restored.
    expect(review.statusOf("moved")).toBe("unresolved");
  });
});
