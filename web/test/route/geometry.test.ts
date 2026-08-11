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

describe("routeGeometry", () => {
  const graph = new RouteGraph(LANES);

  it("includes the first lane, which an earlier estimate left out entirely", () => {
    const g = routeGeometry(graph, ["a", "b"], [], []);
    expect(g.distanceM).toBeGreaterThan(lineLength(LANES[0]!.line));
  });

  it("follows the connector across a junction instead of cutting the corner", () => {
    const withCrossing = routeGeometry(graph, ["a", "b"], [], CONNECTORS);
    const without = routeGeometry(graph, ["a", "b"], [], []);
    expect(withCrossing.distanceM).toBeGreaterThan(without.distanceM);
    // The connector's apex is on the drawn line, so the page shows the turn being taken.
    expect(withCrossing.line).toContainEqual([0.0001, 0.00105]);
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
    expect(g.changes[0]).toHaveLength(2);
  });

  it("counts the change itself, which summing the pieces alone would miss", () => {
    const g = routeGeometry(graph, ["a2", "a"], [1], []);
    const pieces = lineLength(cut(LANES[1]!.line, true, 0.35)) + lineLength(cut(LANES[0]!.line, false, 0.65));
    expect(g.distanceM).toBeGreaterThan(pieces);
  });
});
