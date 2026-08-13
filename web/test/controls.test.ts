import { describe, expect, it } from "vitest";

import {
  CLEAR_EFFECT,
  controlFor,
  IGNORE_EFFECT,
  NOT_APPLICABLE_EFFECT,
} from "../src/controls.js";
import { finding } from "./fixtures.js";

const RULES = [
  "speed_default",
  "lane_width_default",
  "lane_count_inference",
  "lane_transition_count_mismatch",
  "ambiguous_connector",
  "turn_permission_geometry_conflict",
  "signal_lane_association",
  "inferred_stop_line",
  "restriction_effect_review",
];

describe("control specs", () => {
  it("states what accepting does, for every rule", () => {
    // A reviewer working through 138 blockers should never meet a button whose
    // consequence is unstated.
    for (const rule of RULES) {
      const spec = controlFor(finding({ rule }));
      expect(spec.acceptEffect, rule).toBeTruthy();
      expect(spec.question, rule).toBeTruthy();
    }
  });

  it("pairs an override effect with an override button and never one without", () => {
    // The panel renders the override row on this pairing; a spec with one and not
    // the other either hides a consequence or names a button that is not there.
    for (const rule of RULES) {
      const spec = controlFor(finding({ rule }));
      expect(Boolean(spec.overrideEffect), rule).toBe(Boolean(spec.overrideLabel));
    }
  });

  it("says plainly that removing a movement removes the turn", () => {
    const spec = controlFor(finding({ rule: "ambiguous_connector" }));
    expect(spec.overrideEffect).toContain("no vehicle makes this turn");
    expect(spec.acceptEffect).toContain("may make this turn");
  });

  it("does not claim that keeping an unenforced restriction forbids anything", () => {
    // This rule only fires when the restriction could *not* be applied. Copy written for
    // an enforced one told the reviewer that accepting was what banned the turn.
    const spec = controlFor(finding({ rule: "restriction_effect_review" }));
    expect(spec.acceptEffect).toContain("forbids nothing by itself");
    expect(spec.acceptEffect).toContain("removed no movements");
    // The override is offered but not built: Stage 4 refuses a review carrying one, and
    // a reviewer needs to know that before choosing it rather than after.
    expect(spec.overrideEffect).toContain("Stage 4 does not write restriction relations yet");
  });

  it("falls back to a spec that still states its effect", () => {
    // A rule added to the generator later must not surface as an unexplained button.
    const spec = controlFor(finding({ rule: "a_rule_added_later" }));
    expect(spec.acceptEffect).toBeTruthy();
    expect(spec.overrideLabel).toBeNull();
    expect(spec.overrideEffect).toBeNull();
  });

  it("explains the two buttons every rule shares", () => {
    expect(NOT_APPLICABLE_EFFECT).toBeTruthy();
    expect(CLEAR_EFFECT).toContain("blocks export");
  });

  it("says an ignored finding was never judged, not that it was accepted", () => {
    // The distinction is the whole reason `ignored` is its own status.
    expect(IGNORE_EFFECT).toContain("unjudged");
    expect(IGNORE_EFFECT).toContain("nobody decided it");
  });
});
