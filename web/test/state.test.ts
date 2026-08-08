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
    expect(review.decision("f1")).toEqual({
      finding_id: "f1",
      rule: "speed_default",
      status: "accepted",
      decided_at: clock(),
      evidence_checksum: "evidence-1",
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
    expect(() => review.toSubmission()).toThrow(DecisionError);

    review.decide("b", { status: "accepted" });
    expect(review.readiness()).toMatchObject({ blockers_unresolved: 0, ready: true });
    expect(review.toSubmission().decisions).toHaveLength(1);
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
