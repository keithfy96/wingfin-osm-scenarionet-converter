// The drive itself, as the page draws it and the converter builds it.
//
// Distance is the number a person picks a route on, and it was wrong twice before this
// module existed - once too low by a third, once too high by a quarter - because it was
// being estimated from lane lengths rather than measured off the geometry. The fixture
// below mirrors `tests/unit/test_ego_route.py`, and the real cross-check runs both
// implementations over `junction-1` and compares metre for metre.

import { describe, expect, it } from "vitest";
import { cut, routeGeometry } from "../../src/route/geometry.js";
import { lineLength, RouteGraph } from "../../src/route/path.js";
import type { LanePair, RouteLane } from "../../src/route/types.js";

function lane(id: string, from: number, to: number, lat = 0, extra: Partial<RouteLane> = {}): RouteLane {
  return {
    id,
    ways: ["1"],
    short: id,
    label: id,
    line: [
      [lat, from],
      [lat, to],
    ],
    exits: [],
    sideways: [],
    ...extra,
  };
}

/** `a` into `b` through a connector, with `a2` alongside `a`. */
const LANES: RouteLane[] = [
  lane("a", 0, 0.001, 0, { exits: ["b"], sideways: ["a2"] }),
  lane("a2", 0, 0.001, 0.0001, { sideways: ["a"] }),
  lane("b", 0.0011, 0.002, 0, { exits: [] }),
];
const CROSSINGS: LanePair[] = [["a", "b"]];

describe("cut", () => {
  const line: [number, number][] = [
    [0, 0],
    [0, 1],
  ];

  it("keeps the run-up when asked for the head", () => {
    expect(cut(line, true, 0.35)).toEqual([
      [0, 0],
      [0, 0.35],
    ]);
  });

  it("keeps the run-out when asked for the tail", () => {
    expect(cut(line, false, 0.65)).toEqual([
      [0, 0.65],
      [0, 1],
    ]);
  });

  it("interpolates rather than snapping to a vertex, so a two-point lane still splits", () => {
    const bent: [number, number][] = [
      [0, 0],
      [0, 1],
      [0, 2],
    ];
    expect(cut(bent, true, 0.5)).toEqual([
      [0, 0],
      [0, 1],
    ]);
  });
});

/** Metres north and east of the origin, as [lat, lon]. Equatorial, so the two axes match. */
const PER_METRE = 1 / ((6_371_000 * Math.PI) / 180);
function at(north: number, east: number): [number, number] {
  return [north * PER_METRE, east * PER_METRE];
}

/** `n` runs north into `e` running east: a 90° turn, the lane ends 4.2 m apart.
 *
 * Mirrors `_corner` in `tests/unit/test_ego_route.py`. Both sides drive the same shape on
 * purpose - the page must never offer a route the converter would refuse.
 */
const CORNER: RouteLane[] = [
  { ...lane("n", 0, 0), line: [at(0, 0), at(60, 0)], exits: ["e"], sideways: [] },
  { ...lane("e", 0, 0), line: [at(63, 3), at(63, 63)], exits: [], sideways: [] },
];
const CORNER_CROSSING: LanePair[] = [["n", "e"]];

function headings(line: [number, number][]): number[] {
  const out: number[] = [];
  for (let i = 1; i < line.length; i += 1) {
    out.push((Math.atan2(line[i]![0] - line[i - 1]![0], line[i]![1] - line[i - 1]![1]) * 180) / Math.PI);
  }
  return out;
}

function worstTurnDeg(line: [number, number][]): number {
  const h = headings(line);
  let worst = 0;
  for (let i = 1; i < h.length; i += 1) {
    worst = Math.max(worst, Math.abs((((h[i]! - h[i - 1]! + 180) % 360) + 360) % 360 - 180));
  }
  return worst;
}

describe("routeGeometry", () => {
  const graph = new RouteGraph(LANES);
  const corner = new RouteGraph(CORNER);

  it("includes the first lane, which an earlier estimate left out entirely", () => {
    const g = routeGeometry(graph, ["a", "b"], [], CROSSINGS);
    expect(g.distanceM).toBeGreaterThan(lineLength(LANES[0]!.line));
  });

  it("builds a turn that leaves and arrives along the lanes rather than at an angle to them", () => {
    // The connector is a marker for the inspection map, not a driving line: its curve is
    // bent around the OSM node and tangent to neither lane, so splicing it in put a corner
    // at each end of every junction. Measured on `junction-1`, 82° at the median.
    const g = routeGeometry(corner, ["n", "e"], [], CORNER_CROSSING);
    const h = headings(g.line);
    // `headings` measures anticlockwise from due east, so north is +90 and east is 0.
    expect(h[0]).toBeCloseTo(90, 4); // along lane n
    expect(h[h.length - 1]).toBeCloseTo(0, 4); // along lane e
    expect(worstTurnDeg(g.line)).toBeLessThan(10);
  });

  it("refuses a gap too wide for a junction to span", () => {
    // Nothing saying the step crosses a junction makes the same 11 m a hole in a road
    // rather than a crossroads, and a car would drive straight over it.
    expect(() => routeGeometry(graph, ["a", "b"], [], [])).toThrow(/gap before lane b/);
  });

  it("spans a junction a road runs straight through, which has no connector", () => {
    // The case that took a screenshot to find. Generation cuts every lane back to the edge
    // of its junction, so a road going straight on across one is parted by the setback -
    // 10.00 m at node 1239566959 on `mosque`, dead straight, 3 arms. Topologically nothing
    // happens there, so there is no connector, and the page used to read "no connector" as
    // "not a junction" and refuse a drive the converter built without complaint. Which
    // steps cross a junction now comes from `ego_route.junction_crossings` in the payload.
    const straight: RouteLane[] = [
      { ...lane("u", 0, 0), line: [at(0, 0), at(0, 40)], exits: ["v"], sideways: [] },
      { ...lane("v", 0, 0), line: [at(0, 50), at(0, 90)], exits: [], sideways: [] },
    ];
    const through = new RouteGraph(straight);
    expect(() => routeGeometry(through, ["u", "v"], [], [])).toThrow(
      /leaves a 10 m gap before lane v/,
    );
    const g = routeGeometry(through, ["u", "v"], [], [["u", "v"]]);
    expect(g.distanceM).toBeGreaterThan(80);
    expect(worstTurnDeg(g.line)).toBeLessThan(1);
  });

  it("spans a lane change over one lane's worth of road, not two", () => {
    // `a` and `a2` are parallel and cover the same stretch. A route across them travels
    // that stretch once; counting both in full was the second wrong estimate.
    const g = routeGeometry(graph, ["a2", "a"], [1], []);
    const one = lineLength(LANES[0]!.line);
    expect(g.distanceM).toBeGreaterThan(one * 0.6);
    expect(g.distanceM).toBeLessThan(one * 1.2);
  });

  it("reports the change as its own piece, so it can be drawn as one", () => {
    const g = routeGeometry(graph, ["a2", "a"], [1], []);
    expect(g.changes).toHaveLength(1);
    // Drawn as a curve rather than the bare diagonal it used to be: crammed into two points
    // the manoeuvre is a corner at each end, and the recorded car has to crawl through it.
    expect(g.changes[0]!.length).toBeGreaterThan(8);
    expect(worstTurnDeg(g.line)).toBeLessThan(10);
  });

  it("counts the change itself, which summing the pieces alone would miss", () => {
    const g = routeGeometry(graph, ["a2", "a"], [1], []);
    const pieces = lineLength(cut(LANES[1]!.line, true, 0.35)) + lineLength(cut(LANES[0]!.line, false, 0.65));
    expect(g.distanceM).toBeGreaterThan(pieces);
  });
});

describe("a manoeuvre must leave room for the one after it", () => {
  // Mirrors `test_a_change_that_ends_a_short_lane_leaves_room_for_the_junction_after_it`
  // and `test_two_junctions_on_one_lane_both_get_room_to_turn` in
  // `tests/unit/test_ego_route.py`. Both sides drive the same shapes deliberately: a page
  // that drew a fold the converter had smoothed away would be offering a different drive.

  it("does not fold when a lane change ends a short lane just before a turn", () => {
    // `q` is 8 m long and `r` leaves it at a right angle, so the turn needs room the
    // crossing would otherwise use. Unreserved, this built an 82° cusp on `junction-1`.
    const lanes: RouteLane[] = [
      { ...lane("p", 0, 0), line: [at(0, 0), at(0, 8)], exits: [], sideways: ["q"] },
      { ...lane("q", 0, 0), line: [at(4, 0), at(4, 8)], exits: ["r"], sideways: ["p"] },
      { ...lane("r", 0, 0), line: [at(8, 8), at(60, 8)], exits: [], sideways: [] },
    ];
    const g = routeGeometry(new RouteGraph(lanes), ["p", "q", "r"], [1], []);
    expect(worstTurnDeg(g.line)).toBeLessThan(30);
    expect(g.line.length).toBeGreaterThan(10);
  });

  it("does not starve the second of two junctions on one lane", () => {
    const lanes: RouteLane[] = [
      { ...lane("f", 0, 0), line: [at(0, 0), at(40, 0)], exits: ["m"], sideways: [] },
      { ...lane("m", 0, 0), line: [at(42, 2), at(42, 16)], exits: ["l"], sideways: [] },
      { ...lane("l", 0, 0), line: [at(44, 18), at(90, 18)], exits: [], sideways: [] },
    ];
    const crossings: LanePair[] = [
      ["f", "m"],
      ["m", "l"],
    ];
    const g = routeGeometry(new RouteGraph(lanes), ["f", "m", "l"], [], crossings);
    expect(worstTurnDeg(g.line)).toBeLessThan(30);
  });

  it("treats two points a fraction of a millimetre apart as one point", () => {
    // The 90° readings came from a segment too short to have a direction in it. At the old
    // 1e-6 m tolerance these survived and were the worst vertex in 390 of 813 swept routes.
    const lanes: RouteLane[] = [
      { ...lane("s", 0, 0), line: [at(0, 0), at(0, 10)], exits: ["t"], sideways: [] },
      { ...lane("t", 0, 0), line: [at(0.00008, 10.00001), at(0, 30)], exits: [], sideways: [] },
    ];
    const g = routeGeometry(new RouteGraph(lanes), ["s", "t"], [], []);
    expect(worstTurnDeg(g.line)).toBeLessThan(5);
  });
});
