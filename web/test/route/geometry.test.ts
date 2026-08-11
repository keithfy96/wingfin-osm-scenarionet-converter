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
import type { RouteConnector, RouteLane } from "../../src/route/types.js";

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
const CONNECTORS: RouteConnector[] = [
  { from: "a", to: "b", line: [[0, 0.001], [0.0001, 0.00105], [0, 0.0011]] },
];

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
const CORNER_CROSSING: RouteConnector[] = [{ from: "n", to: "e", line: [at(60, 0), at(63, 3)] }];

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
    const g = routeGeometry(graph, ["a", "b"], [], CONNECTORS);
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
    // Without a connector saying the step crosses a junction, the same 11 m is a hole in a
    // road rather than a crossroads, and a car would drive straight over it.
    expect(() => routeGeometry(graph, ["a", "b"], [], [])).toThrow(/gap before lane b/);
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
