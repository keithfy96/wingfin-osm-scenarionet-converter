// Re-timing the groups a person drew so that nothing that meets is ever green together.
//
// The conflict list is the input, so most cases here build one directly - that is what
// `findConflicts` hands over, and building it by hand keeps a timing test from also being a
// geometry test. The exception is the last block, which runs the two together, because
// "nothing is green at the same time" is the only claim the button actually makes.

import { describe, expect, it } from "vitest";
import { autoPhase, AutoPhaseError } from "../../src/signal/auto-phase.js";
import { findConflicts, type Conflict } from "../../src/signal/conflicts.js";
import type { PhaseGroup, SignalConnector } from "../../src/signal/types.js";

function group(name: string, lanes: number, green = 27, yellow = 3, offset = 0): PhaseGroup {
  return {
    name,
    lanes: Array.from({ length: lanes }, (_, index) => `${name}-${index}`),
    green_seconds: green,
    yellow_seconds: yellow,
    offset_seconds: offset,
  };
}

/** Only the group names matter to `autoPhase`; the rest is what `findConflicts` also carries. */
function meets(a: string, b: string): Conflict {
  return {
    a,
    b,
    junction: "n1",
    kind: "crossing",
    metres: { a: 0, b: 0 },
    overlapSeconds: 0,
  };
}

function timing(result: { groups: PhaseGroup[] }, name: string): [number, number, number] {
  const found = result.groups.find((one) => one.name === name)!;
  return [found.offset_seconds, found.green_seconds, found.yellow_seconds];
}

describe("autoPhase", () => {
  it("gives two groups that meet a stage each", () => {
    const result = autoPhase([group("a", 1), group("b", 1)], 60, [meets("a", "b")]);
    expect(result.stages).toHaveLength(2);
    expect(result.stages.map((stage) => stage.offsetSeconds)).toEqual([0, 30]);
    expect(timing(result, "a")[0]).not.toBe(timing(result, "b")[0]);
  });

  // `junction-1`'s shape: the side road meets both carriageways, the carriageways never meet
  // each other. Two stages, not three, and the two carriageways share the first.
  it("lets two groups that never meet share one stage", () => {
    const result = autoPhase(
      [group("phase-a", 3), group("phase-b", 3), group("phase-c", 2)],
      60,
      [meets("phase-a", "phase-c"), meets("phase-b", "phase-c")],
    );
    expect(result.stages).toHaveLength(2);
    expect(result.stages[0]!.members).toEqual(["phase-a", "phase-b"]);
    expect(result.stages[1]!.members).toEqual(["phase-c"]);
    expect(timing(result, "phase-a")).toEqual([0, 27, 3]);
    expect(timing(result, "phase-b")).toEqual([0, 27, 3]);
    expect(timing(result, "phase-c")).toEqual([30, 27, 3]);
  });

  it("gives three groups that all meet each other a stage each", () => {
    const result = autoPhase(
      [group("a", 1), group("b", 1), group("c", 1)],
      60,
      [meets("a", "b"), meets("b", "c"), meets("a", "c")],
    );
    expect(result.stages).toHaveLength(3);
    expect(result.stages.map((stage) => stage.offsetSeconds)).toEqual([0, 20, 40]);
  });

  // A real controller gives the busiest movement the head of the cycle, and on `junction-1`
  // that is the difference between leading with six lanes of dual carriageway and leading
  // with the two-lane side road.
  it("puts the stage with the most lanes first", () => {
    const result = autoPhase([group("small", 1), group("big", 5)], 60, [meets("small", "big")]);
    expect(result.stages[0]!.members).toEqual(["big"]);
    expect(timing(result, "big")[0]).toBe(0);
  });

  it("keeps a green that already fits, exactly", () => {
    const result = autoPhase([group("a", 1, 27, 3), group("b", 1, 27, 3)], 60, [meets("a", "b")]);
    expect(timing(result, "a")[1]).toBe(27);
    expect(result.shortened).toEqual([]);
  });

  it("shortens a green that will not fit, and says so", () => {
    const result = autoPhase(
      [group("a", 1, 27, 3), group("b", 1, 27, 3), group("c", 1, 27, 3)],
      60,
      [meets("a", "b"), meets("b", "c"), meets("a", "c")],
    );
    expect(timing(result, "a")[1]).toBe(17);
    expect(result.shortened).toHaveLength(3);
    expect(result.shortened[0]).toMatchObject({ was: 27, now: 17 });
  });

  // The opposite of shortening, and the reason `min` is not `=`: a short green is a choice,
  // and filling the stage would quietly undo it.
  it("leaves a green shorter than its stage alone", () => {
    const result = autoPhase([group("a", 1, 8, 3), group("b", 1, 27, 3)], 60, [meets("a", "b")]);
    expect(timing(result, "a")[1]).toBe(8);
    expect(result.shortened).toEqual([]);
  });

  it("never touches a yellow", () => {
    const result = autoPhase([group("a", 1, 27, 4), group("b", 1, 27, 2)], 60, [meets("a", "b")]);
    expect(timing(result, "a")[2]).toBe(4);
    expect(timing(result, "b")[2]).toBe(2);
  });

  it("refuses when a yellow fills a whole stage on its own", () => {
    expect(() =>
      autoPhase([group("a", 1, 5, 30), group("b", 1, 5, 3)], 60, [meets("a", "b")]),
    ).toThrow(/a's 30\.0 s yellow fills a 30\.0 s stage/);
  });

  it("refuses when there is nothing to resolve", () => {
    expect(() => autoPhase([group("a", 1), group("b", 1)], 60, [])).toThrow(AutoPhaseError);
    expect(() => autoPhase([group("a", 1)], 60, [meets("a", "b")])).toThrow(AutoPhaseError);
  });

  // Undo puts the old timings back and the button is there to be pressed again. If the
  // colouring drifted on a tie, the second press would land somewhere else.
  it("gives the same answer twice", () => {
    const groups = [group("a", 2), group("b", 2), group("c", 2), group("d", 2)];
    const conflicts = [meets("a", "b"), meets("c", "d"), meets("b", "c")];
    const once = autoPhase(groups, 60, conflicts);
    const twice = autoPhase(once.groups, 60, conflicts);
    expect(twice.groups).toEqual(once.groups);
    expect(twice.stages).toEqual(once.stages);
  });
});

// The claim on the button is that nothing meets while both are green. Everything above
// describes how it gets there; this is the only block that checks it arrived, and it uses the
// real `findConflicts` rather than the hand-built list.
describe("what the button actually promises", () => {
  // west -> east and south -> north cross at n1; south -> north and east -> north merge at n2.
  const CONNECTORS: SignalConnector[] = [
    { from: "ew-0", to: "east", junction: "n1", line: [[0, -1], [0, 1]] },
    { from: "ns-0", to: "north", junction: "n1", line: [[-1, 0], [1, 0]] },
    { from: "en-0", to: "north", junction: "n2", line: [[2, 1], [3, 0]] },
    { from: "ns2-0", to: "north", junction: "n2", line: [[2, -1], [3, 0]] },
  ];
  const LANES = new Map<string, readonly [number, number][]>();

  it("leaves nothing green together", () => {
    const drawn = [group("ew", 1), group("ns", 1), group("en", 1), group("ns2", 1)];
    const before = findConflicts(drawn, 60, CONNECTORS, LANES);
    expect(before.some((one) => one.overlapSeconds > 0)).toBe(true);

    const result = autoPhase(drawn, 60, before);
    const after = findConflicts(result.groups, 60, CONNECTORS, LANES);
    expect(after).toHaveLength(before.length);
    for (const conflict of after) expect(conflict.overlapSeconds).toBe(0);
  });

  // Three movements that pairwise cross, so three stages, over a cycle that does not divide
  // by three. 37 / 3 is 12.333..., and offsets round to 0 / 12.3 / 24.7 with stages of
  // 12.3 / 12.4 / 12.3 - which is exactly the case that comes back green-on-green if the
  // greens are taken from `cycle / stages` instead of from the rounded offsets.
  const TRIANGLE: SignalConnector[] = [
    { from: "one-0", to: "one-out", junction: "n1", line: [[-1, -1], [1, 1]] },
    { from: "two-0", to: "two-out", junction: "n1", line: [[-1, 1], [1, -1]] },
    { from: "three-0", to: "three-out", junction: "n1", line: [[-1, 0.2], [1, 0.1]] },
  ];

  it("leaves nothing green together at a cycle that does not divide evenly", () => {
    const drawn = [group("one", 1, 9, 2), group("two", 1, 9, 2), group("three", 1, 9, 2)];
    const before = findConflicts(drawn, 37, TRIANGLE, LANES);
    expect(before).toHaveLength(3);

    const result = autoPhase(drawn, 37, before);
    expect(result.stages).toHaveLength(3);
    expect(result.stages.map((stage) => stage.offsetSeconds)).toEqual([0, 12.3, 24.7]);
    for (const conflict of findConflicts(result.groups, 37, TRIANGLE, LANES)) {
      expect(conflict.overlapSeconds).toBe(0);
    }
  });
});
