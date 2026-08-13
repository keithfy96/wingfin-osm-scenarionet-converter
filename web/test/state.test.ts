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
      // Carried even though nothing was overridden: this is the value the reviewer
      // approved, and without it "accepted" says nothing about what was accepted.
      proposed_value: { maxspeed_kph: 50 },
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

  it("can set aside the warnings of a rule that also raises blockers", () => {
    // `lane_count_inference` raises a blocker where it defaulted to one lane and a
    // warning where it divided a total. Scoped to the whole rule, the validation below
    // refuses the call on the first blocker and *nothing* is ignored — which on the
    // page looked like a button that did not work.
    const mixed = [
      finding({ identifier: "w1", rule: "lane_count_inference", severity: "warning" }),
      finding({ identifier: "w2", rule: "lane_count_inference", severity: "warning" }),
      finding({ identifier: "b1", rule: "lane_count_inference", severity: "blocker" }),
    ];
    const review = state(mixed);
    expect(() => review.decideBulk({ rule: "lane_count_inference" }, { status: "ignored" })).toThrow(
      DecisionError,
    );
    expect(review.statusOf("w1")).toBe("unresolved");

    expect(
      review.decideBulk({ rule: "lane_count_inference", severity: "warning" }, { status: "ignored" }),
    ).toEqual(["w1", "w2"]);
    // The blocker is untouched and still gates export, which is the point of refusing
    // to ignore it in the first place.
    expect(review.statusOf("b1")).toBe("unresolved");
    expect(review.readiness().blockers_unresolved).toBe(1);
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

  it("carries the finding's location and proposed value onto the decision it exports", () => {
    // The exported answers are read on their own — joined against a GPS track, or asked
    // what lane count was approved — so they have to place themselves and state what they
    // agreed to, rather than sending the reader back to preliminary.json for either.
    const located = finding({ identifier: "b", severity: "blocker" });
    const review = state([located]);
    review.decide("b", { status: "accepted" });

    const submission = review.toSubmission();
    expect(submission.submission_version).toBe(4);
    expect(submission.decisions[0]?.location).toEqual(located.location);
    expect(submission.decisions[0]?.source_ids).toEqual(located.source_ids);
    expect(submission.decisions[0]?.proposed_value).toEqual(located.proposed_value);
  });

  it("keeps an override's own value distinct from what was proposed", () => {
    // Both are recorded, because a number the generator inferred and a number the
    // reviewer typed are not the same record even when they are the same number.
    const target = finding({ identifier: "c", rule: "lane_count_inference" });
    const review = state([target]);
    review.decide("c", { status: "overridden", value: { lane_count: 3 } });

    const decision = review.toSubmission().decisions[0];
    expect(decision?.value).toEqual({ lane_count: 3 });
    expect(decision?.proposed_value).toEqual(target.proposed_value);
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
