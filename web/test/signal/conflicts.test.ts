// Which phase groups must not be green together, and whether a plan runs them anyway.
//
// The geometry is a plain crossroads: two movements through node "n1" that cross, and two
// that merge into one lane. Coordinates are [lat, lon] as the payload carries them, which is
// what `segmentsCross` is written against.

import { describe, expect, it } from "vitest";
import { EARTH_RADIUS_M } from "../../src/geo.js";
import { DOWNSTREAM_M, findConflicts, greenOverlapSeconds } from "../../src/signal/conflicts.js";
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

// The fixtures above all stop at the first movement, so no lane length is ever consulted.
// The staggered cases at the bottom bring their own geometry.
const LANES = new Map<string, readonly [number, number][]>();

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
      LANES,
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
      LANES,
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
      LANES,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ kind: "merge" });
  });

  it("ignores movements at different junctions", () => {
    const apart: SignalConnector[] = [
      { ...CROSSING[0]!, junction: "n1" },
      { ...CROSSING[1]!, junction: "n2" },
    ];
    expect(findConflicts([group("ew", ["west"], 0), group("ns", ["south"], 0)], 60, apart, LANES)).toEqual(
      [],
    );
  });

  it("ignores two movements in the same group, which are green together by design", () => {
    expect(findConflicts([group("all", ["west", "south"], 0)], 60, CROSSING, LANES)).toEqual([]);
  });

  it("ignores a movement leaving a lane with no light on it", () => {
    expect(findConflicts([group("ew", ["west"], 0)], 60, CROSSING, LANES)).toEqual([]);
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
      LANES,
    );
    expect(conflicts.length).toBeGreaterThan(1);
    expect(conflicts[0]!.overlapSeconds).toBeGreaterThanOrEqual(conflicts[1]!.overlapSeconds);
  });
});

// A staggered junction, which is what `junction-1` actually is: the main road's light stops
// traffic at node "nA", and the crossing arm meets it one lane later at node "nB". Nothing
// is stopped at "nB" by the main road's own light, so a check that only compared movements
// at one node saw nothing here at all.
//
//                 nA          10 m of lane "m1"          nB
//   main ---> [ light ] --m0->m1-- ============= --m1->m2-- X-- side
//
// Distances run north-south only, so `metresBetween`'s cos(latitude) term never enters and
// one degree of latitude is exactly `1 / METRE` metres.
const METRE = 180 / (Math.PI * EARTH_RADIUS_M);

function staggered(laneMetres: number): {
  connectors: SignalConnector[];
  lanes: Map<string, readonly [number, number][]>;
} {
  const foot = -laneMetres * METRE;
  return {
    connectors: [
      { from: "m0", to: "m1", junction: "nA", line: [[2 * METRE, 0], [0, 0]] },
      { from: "m1", to: "m2", junction: "nB", line: [[foot, 0], [foot - 2 * METRE, 0]] },
      {
        from: "s0",
        to: "s1",
        junction: "nB",
        line: [[foot - METRE, -METRE], [foot - METRE, METRE]],
      },
    ],
    lanes: new Map<string, readonly [number, number][]>([["m1", [[0, 0], [foot, 0]]]]),
  };
}

describe("findConflicts across a staggered junction", () => {
  it("finds a crossing a whole lane past the light that governs it", () => {
    const { connectors, lanes } = staggered(10);
    const conflicts = findConflicts(
      [group("main", ["m0"], 0), group("side", ["s0"], 0)],
      60,
      connectors,
      lanes,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ a: "main", b: "side", junction: "nB", kind: "crossing" });
    expect(conflicts[0]!.overlapSeconds).toBe(25);
  });

  // 2 m of connector then 10 m of lane. The number is what the panel prints, so it is worth
  // pinning rather than only asserting that it is more than nothing.
  it("says how far past its own light each group's traffic gets there", () => {
    const { connectors, lanes } = staggered(10);
    const conflicts = findConflicts(
      [group("main", ["m0"], 0), group("side", ["s0"], 0)],
      60,
      connectors,
      lanes,
    );
    expect(conflicts[0]!.metres.a).toBeCloseTo(12, 3);
    expect(conflicts[0]!.metres.b).toBe(0);
  });

  it("stops following a stream once it is out of the junction", () => {
    const { connectors, lanes } = staggered(DOWNSTREAM_M + 5);
    expect(
      findConflicts([group("main", ["m0"], 0), group("side", ["s0"], 0)], 60, connectors, lanes),
    ).toEqual([]);
  });

  // Keith's rule for `junction-1`'s unsignalled off-ramp, and the reason it is a rule rather
  // than an exception: where a car goes at a fork is the driver's choice, so no timing on the
  // light behind it separates the two streams beyond.
  it("stops following a stream at a fork, where the light no longer decides", () => {
    const { connectors, lanes } = staggered(10);
    const forked: SignalConnector[] = [
      ...connectors,
      { from: "m1", to: "m3", junction: "nB", line: [[-10 * METRE, 0], [-10 * METRE, METRE]] },
    ];
    expect(
      findConflicts([group("main", ["m0"], 0), group("side", ["s0"], 0)], 60, forked, lanes),
    ).toEqual([]);
  });

  it("stops following a stream at another group's light, which is what stops it", () => {
    const { connectors, lanes } = staggered(10);
    const conflicts = findConflicts(
      [group("main", ["m0"], 0), group("middle", ["m1"], 0), group("side", ["s0"], 0)],
      60,
      connectors,
      lanes,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ a: "middle", b: "side" });
    expect(conflicts.some((one) => one.a === "main" || one.b === "main")).toBe(false);
  });

  it("does not call a fork out of one lane a conflict with itself", () => {
    const fork: SignalConnector[] = [
      { from: "d0", to: "left", junction: "nD", line: [[0, 0], [METRE, METRE]] },
      { from: "d0", to: "right", junction: "nD", line: [[METRE, 0], [0, METRE]] },
    ];
    expect(findConflicts([group("one", ["d0"], 0)], 60, fork, LANES)).toEqual([]);
  });
});
