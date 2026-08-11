// Which phase groups must not be green together, and whether a plan runs them anyway.
//
// The geometry is a plain crossroads: two movements through node "n1" that cross, and two
// that merge into one lane. Coordinates are [lat, lon] as the payload carries them, which is
// what `segmentsCross` is written against.

import { describe, expect, it } from "vitest";
import { findConflicts, greenOverlapSeconds } from "../../src/signal/conflicts.js";
import type { PhaseGroup, SignalConnector } from "../../src/signal/types.js";

const CROSSING: SignalConnector[] = [
  // west -> east, straight through the middle
  { from: "west", to: "east", junction: "n1", line: [[0, -1], [0, 1]] },
  // south -> north, straight through the middle: these two cross
  { from: "south", to: "north", junction: "n1", line: [[-1, 0], [1, 0]] },
];

const MERGING: SignalConnector[] = [
  { from: "west", to: "north", junction: "n1", line: [[0, -1], [1, 0]] },
  { from: "south", to: "north", junction: "n1", line: [[-1, 0], [1, 0]] },
];

function group(name: string, lanes: string[], offset: number, green = 25): PhaseGroup {
  return { name, lanes, green_seconds: green, yellow_seconds: 3, offset_seconds: offset };
}

describe("greenOverlapSeconds", () => {
  it("is zero for windows that do not touch", () => {
    expect(greenOverlapSeconds(group("a", [], 0), group("b", [], 30), 60)).toBe(0);
  });

  it("measures a partial overlap", () => {
    expect(greenOverlapSeconds(group("a", [], 0), group("b", [], 20), 60)).toBe(5);
  });

  // `a` is green 50 s -> 75 s, so 50-60 s and again 0-15 s; `b` is green 5 s -> 30 s. They
  // share 5-15 s, entirely in the part of `a` that wrapped. Comparing the two windows
  // without the wrap reads this as no overlap at all.
  it("sees an overlap that wraps past the end of the cycle", () => {
    expect(greenOverlapSeconds(group("a", [], 50), group("b", [], 5), 60)).toBe(10);
  });

  it("is the whole window when one group is green throughout", () => {
    expect(greenOverlapSeconds(group("a", [], 0, 60), group("b", [], 30, 10), 60)).toBe(10);
  });
});

describe("findConflicts", () => {
  it("finds two movements that cross at the same node", () => {
    const conflicts = findConflicts(
      [group("ew", ["west"], 0), group("ns", ["south"], 0)],
      60,
      CROSSING,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ a: "ew", b: "ns", junction: "n1", kind: "crossing" });
    expect(conflicts[0]!.overlapSeconds).toBe(25);
  });

  it("still reports the pair when the plan separates them, with no overlap", () => {
    const conflicts = findConflicts(
      [group("ew", ["west"], 0), group("ns", ["south"], 30)],
      60,
      CROSSING,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]!.overlapSeconds).toBe(0);
  });

  // A merge occupies one lane with two streams, which crossing geometry alone would not
  // catch: the two paths meet at their shared end rather than in the middle.
  it("finds two movements that merge into one lane", () => {
    const conflicts = findConflicts(
      [group("ew", ["west"], 0), group("ns", ["south"], 0)],
      60,
      MERGING,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ kind: "merge" });
  });

  it("ignores movements at different junctions", () => {
    const apart: SignalConnector[] = [
      { ...CROSSING[0]!, junction: "n1" },
      { ...CROSSING[1]!, junction: "n2" },
    ];
    expect(findConflicts([group("ew", ["west"], 0), group("ns", ["south"], 0)], 60, apart)).toEqual(
      [],
    );
  });

  it("ignores two movements in the same group, which are green together by design", () => {
    expect(findConflicts([group("all", ["west", "south"], 0)], 60, CROSSING)).toEqual([]);
  });

  it("ignores a movement leaving a lane with no light on it", () => {
    expect(findConflicts([group("ew", ["west"], 0)], 60, CROSSING)).toEqual([]);
  });

  it("sorts the worst overlap first", () => {
    const three: SignalConnector[] = [
      ...CROSSING,
      { from: "north", to: "south", junction: "n1", line: [[1, 0.1], [-1, 0.1]] },
    ];
    const conflicts = findConflicts(
      [group("ew", ["west"], 0), group("ns", ["south"], 30), group("sn", ["north"], 0)],
      60,
      three,
    );
    expect(conflicts.length).toBeGreaterThan(1);
    expect(conflicts[0]!.overlapSeconds).toBeGreaterThanOrEqual(conflicts[1]!.overlapSeconds);
  });
});
