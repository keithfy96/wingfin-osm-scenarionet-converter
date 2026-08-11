// The signals.json this page exports, and the checks that keep a stale or impossible plan
// out of the converter.

import { describe, expect, it } from "vitest";
import {
  nameProblem,
  parseSignals,
  serializeSignals,
  SignalsFileError,
  timingProblem,
} from "../../src/signal/plan-file.js";
import type { PhaseGroup, SignalIdentity } from "../../src/signal/types.js";

const IDENTITY: SignalIdentity = {
  generation_fingerprint: "fingerprint",
  reviewed_lane_model_sha256: "model-sha",
};

const LANES = new Set(["aaa", "bbb", "ccc"]);

const GROUPS: PhaseGroup[] = [
  { name: "phase-a", lanes: ["aaa"], green_seconds: 27, yellow_seconds: 3, offset_seconds: 0 },
  { name: "phase-b", lanes: ["bbb"], green_seconds: 27, yellow_seconds: 3, offset_seconds: 30 },
];

describe("phase group names", () => {
  it("accepts an ordinary one", () => {
    expect(nameProblem("phase-a", [])).toBeNull();
  });

  it.each(["", "has space", "slash/es", "-leading", "x".repeat(41)])("refuses %o", (name) => {
    expect(nameProblem(name, [])).not.toBeNull();
  });

  it("refuses a name already taken", () => {
    expect(nameProblem("a", ["a"])).toMatch(/already/);
  });
});

describe("timing within the cycle", () => {
  it("accepts green plus yellow exactly filling the cycle", () => {
    expect(
      timingProblem({ green_seconds: 57, yellow_seconds: 3, offset_seconds: 0 }, 60),
    ).toBeNull();
  });

  it("refuses green plus yellow overrunning it, which no controller could carry out", () => {
    expect(
      timingProblem({ green_seconds: 58, yellow_seconds: 3, offset_seconds: 0 }, 60),
    ).toMatch(/longer than/);
  });

  it("refuses a negative duration", () => {
    expect(
      timingProblem({ green_seconds: -1, yellow_seconds: 3, offset_seconds: 0 }, 60),
    ).toMatch(/negative/);
  });

  it("allows an offset past the cycle; the converter normalises it", () => {
    expect(
      timingProblem({ green_seconds: 10, yellow_seconds: 3, offset_seconds: 90 }, 60),
    ).toBeNull();
  });
});

describe("signals.json", () => {
  it("round-trips", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1);
    expect(parseSignals(raw, IDENTITY, 1, LANES)).toEqual({ cycleSeconds: 60, groups: GROUPS });
  });

  it("ends with a newline, like every other file this repo writes", () => {
    expect(serializeSignals(IDENTITY, 60, GROUPS, 1).endsWith("\n")).toBe(true);
  });

  it("refuses a plan drawn on a different generation of the map", () => {
    const raw = serializeSignals(
      { ...IDENTITY, generation_fingerprint: "somewhere-else" },
      60,
      GROUPS,
      1,
    );
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/different generation/);
  });

  it("refuses a plan drawn before the model was re-reviewed", () => {
    const raw = serializeSignals({ ...IDENTITY, reviewed_lane_model_sha256: "other" }, 60, GROUPS, 1);
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/re-reviewed/);
  });

  it("refuses a version it does not read", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 2);
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/signals_version/);
  });

  // The failure this catches is silent downstream: MetaDrive's `skip_missing_light` defaults
  // to true, so a light on a lane that is not in the map is dropped with a log line and no
  // light appears at all.
  it("refuses a lane that is not on this map", () => {
    const raw = serializeSignals(
      IDENTITY,
      60,
      [{ ...GROUPS[0]!, lanes: ["nowhere"] }],
      1,
    );
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/not a lane on this map/);
  });

  it("refuses one lane in two groups, which cannot show one colour", () => {
    const raw = serializeSignals(
      IDENTITY,
      60,
      [GROUPS[0]!, { ...GROUPS[1]!, lanes: ["aaa"] }],
      1,
    );
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/only show one colour/);
  });

  it("refuses a group with no lanes", () => {
    const raw = serializeSignals(IDENTITY, 60, [{ ...GROUPS[0]!, lanes: [] }], 1);
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(SignalsFileError);
  });

  it.each([
    ["not json at all", /valid JSON/],
    ['{"signals_version":1}', /identity/],
  ])("refuses %o", (raw, message) => {
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(message);
  });

  it("refuses a cycle of zero, which would divide by nothing", () => {
    const raw = JSON.stringify({ signals_version: 1, identity: IDENTITY, cycle_seconds: 0, groups: GROUPS });
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/cycle_seconds/);
  });
});
