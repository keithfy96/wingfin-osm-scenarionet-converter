// The signals.json this page exports, and the checks that keep a stale or impossible plan
// out of the converter.

import { describe, expect, it } from "vitest";
import {
  inspectSignals,
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

// Where each lane's light was drawn. `inspectSignals` is handed the map's current stop
// points in the same shape, so a lane that has moved shows up as a distance.
const DRAWN: Record<string, [number, number]> = {
  aaa: [3.1848, 101.6122],
  bbb: [3.1852, 101.6130],
  ccc: [3.1860, 101.6141],
};
const HERE = new Map<string, [number, number] | null>(Object.entries(DRAWN));

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
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN);
    expect(parseSignals(raw, IDENTITY, 1, LANES)).toEqual({ cycleSeconds: 60, groups: GROUPS });
  });

  it("ends with a newline, like every other file this repo writes", () => {
    expect(serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN).endsWith("\n")).toBe(true);
  });

  it("refuses a plan drawn on a different generation of the map", () => {
    const raw = serializeSignals(
      { ...IDENTITY, generation_fingerprint: "somewhere-else" },
      60,
      GROUPS,
      1,
      DRAWN,
    );
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/different generation/);
  });

  it("refuses a plan drawn before the model was re-reviewed", () => {
    const raw = serializeSignals({ ...IDENTITY, reviewed_lane_model_sha256: "other" }, 60, GROUPS, 1, DRAWN);
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/re-reviewed/);
  });

  it("refuses a version it does not read", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 2, DRAWN);
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
      DRAWN,
    );
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/not a lane on this map/);
  });

  it("refuses one lane in two groups, which cannot show one colour", () => {
    const raw = serializeSignals(
      IDENTITY,
      60,
      [GROUPS[0]!, { ...GROUPS[1]!, lanes: ["aaa"] }],
      1,
      DRAWN,
    );
    expect(() => parseSignals(raw, IDENTITY, 1, LANES)).toThrow(/only show one colour/);
  });

  it("refuses a group with no lanes", () => {
    const raw = serializeSignals(IDENTITY, 60, [{ ...GROUPS[0]!, lanes: [] }], 1, DRAWN);
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

// --- loading a plan onto a map it was not drawn on -------------------------------------------
//
// The refusal above is right and stays. What it was not is *usable*: the fingerprint moves on
// any full Stage 1 rerun, because osmnx stamps a build timestamp into the graphml it writes and
// that checksum feeds `generation_fingerprint` - so a plan is refused far more often than the
// map has actually changed, and every light gets placed again by hand for nothing.
//
// `inspectSignals` is the path that says what is actually different, so a person can decide.
// What it must never do is decide for them, and it must never turn a fatal fault into a warning.

const OTHER_MAP: SignalIdentity = {
  generation_fingerprint: "elsewhere",
  reviewed_lane_model_sha256: "other-model",
};

describe("inspecting a plan against this map", () => {
  it("reports nothing at all for a plan drawn on this map", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN);
    const report = inspectSignals(raw, IDENTITY, 1, HERE);
    expect(report.identityProblems).toEqual([]);
    expect(report.missingLanes).toEqual([]);
    expect(report.movedLanes).toEqual([]);
    expect(report.droppedGroups).toEqual([]);
    expect(report.plan).toEqual({ cycleSeconds: 60, groups: GROUPS });
  });

  it("reports both identity fields rather than throwing on the first", () => {
    const raw = serializeSignals(OTHER_MAP, 60, GROUPS, 1, DRAWN);
    const report = inspectSignals(raw, IDENTITY, 1, HERE);
    expect(report.identityProblems.map((p) => p.field)).toEqual(["generation", "lane model"]);
    expect(report.identityProblems[0]).toMatchObject({ was: "elsewhere", now: "fingerprint" });
    // The plan itself still comes back whole - that is the point of looking.
    expect(report.plan.groups).toEqual(GROUPS);
  });

  it("collects a lane the map no longer has, and keeps the rest of its group", () => {
    const raw = serializeSignals(
      OTHER_MAP,
      60,
      [{ ...GROUPS[0]!, lanes: ["aaa", "vanished"] }],
      1,
      DRAWN,
    );
    const report = inspectSignals(raw, OTHER_MAP, 1, HERE);
    expect(report.missingLanes).toEqual([{ group: "phase-a", lane: "vanished" }]);
    expect(report.plan.groups[0]!.lanes).toEqual(["aaa"]);
    expect(report.droppedGroups).toEqual([]);
  });

  it("drops a group that has lost every lane, and names it", () => {
    const raw = serializeSignals(
      OTHER_MAP,
      60,
      [GROUPS[0]!, { ...GROUPS[1]!, lanes: ["gone-a", "gone-b"] }],
      1,
      DRAWN,
    );
    const report = inspectSignals(raw, OTHER_MAP, 1, HERE);
    expect(report.droppedGroups).toEqual(["phase-b"]);
    expect(report.plan.groups.map((g) => g.name)).toEqual(["phase-a"]);
  });

  // The check that distinguishes a map that was merely rebuilt from one that actually moved.
  // A lane id is `deterministic_id("lane", *ways, u, v, key, lane_index)` and carries no
  // lane_count, so an id can outlive the position it named.
  it("reports a light whose lane kept its id but not its place", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN);
    const moved = new Map(HERE);
    moved.set("bbb", [3.1852 + 0.0001, 101.613]);          // ~11 m north
    const report = inspectSignals(raw, IDENTITY, 1, moved);
    expect(report.movedLanes).toHaveLength(1);
    expect(report.movedLanes[0]!.lane).toBe("bbb");
    expect(report.movedLanes[0]!.metres).toBeGreaterThan(10);
    expect(report.movedLanes[0]!.metres).toBeLessThan(12);
  });

  it("ignores projection wobble rather than calling it movement", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN);
    const jittered = new Map(HERE);
    jittered.set("bbb", [3.1852 + 0.000001, 101.613]);     // ~0.1 m
    expect(inspectSignals(raw, IDENTITY, 1, jittered).movedLanes).toEqual([]);
  });

  it("says it cannot tell, for a file written before lights were recorded", () => {
    const raw = JSON.stringify({
      signals_version: 1,
      identity: OTHER_MAP,
      cycle_seconds: 60,
      groups: GROUPS,
    });
    const report = inspectSignals(raw, IDENTITY, 1, HERE);
    expect(report.records).toBe(false);
    expect(report.movedLanes).toEqual([]);
  });

  it("knows a file that does record them", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN);
    expect(inspectSignals(raw, IDENTITY, 1, HERE).records).toBe(true);
  });

  // Nothing below is a question of *which* map, so none of it becomes adoptable.
  it.each([
    ["not json at all", /valid JSON/],
    ['{"signals_version":9,"identity":{},"cycle_seconds":60,"groups":[]}', /signals_version/],
  ])("still throws on %o", (raw, message) => {
    expect(() => inspectSignals(raw, IDENTITY, 1, HERE)).toThrow(message);
  });

  it("still throws on one lane in two groups", () => {
    const raw = serializeSignals(
      OTHER_MAP,
      60,
      [GROUPS[0]!, { ...GROUPS[1]!, lanes: ["aaa"] }],
      1,
      DRAWN,
    );
    expect(() => inspectSignals(raw, IDENTITY, 1, HERE)).toThrow(/only show one colour/);
  });

  it("still throws on a timing that cannot fit the cycle", () => {
    const raw = serializeSignals(
      OTHER_MAP,
      60,
      [{ ...GROUPS[0]!, green_seconds: 90 }],
      1,
      DRAWN,
    );
    expect(() => inspectSignals(raw, IDENTITY, 1, HERE)).toThrow(/longer than/);
  });
});

describe("drawn_at", () => {
  it("is written into the file, so a later load can measure against it", () => {
    const file = JSON.parse(serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN));
    expect(file.drawn_at).toEqual(DRAWN);
  });

  it("does not disturb the round trip the converter reads", () => {
    const raw = serializeSignals(IDENTITY, 60, GROUPS, 1, DRAWN);
    expect(parseSignals(raw, IDENTITY, 1, LANES)).toEqual({ cycleSeconds: 60, groups: GROUPS });
  });
});
