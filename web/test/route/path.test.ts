// The route search behind the Stage 6 builder.
//
// The fixture here is the same shape as `tests/unit/test_ego_route.py`'s, and
// `test_the_browser_and_python_agree_on_the_route` runs the equivalent case through the
// Python side. If these two ever disagree the page offers routes the converter refuses,
// which is the one failure a preview must not have.

import { describe, expect, it } from "vitest";
import { RouteGraph } from "../../src/route/path.js";
import type { RouteLane } from "../../src/route/types.js";

/** Lanes laid out along a line of latitude, so lengths are easy to reason about. */
function lane(id: string, from: number, to: number, extra: Partial<RouteLane> = {}): RouteLane {
  return {
    id,
    ways: ["1"],
    short: id,
    label: id,
    line: [
      [0, from],
      [0, to],
    ],
    exits: [],
    sideways: [],
    ...extra,
  };
}

/** a → b → d in a chain, with a2 running alongside a and rejoining at b. */
function chain(): RouteLane[] {
  return [
    lane("a", 0, 0.001, { exits: ["b"], sideways: ["a2"] }),
    lane("a2", 0, 0.001, { sideways: ["a"] }),
    lane("b", 0.001, 0.002, { exits: ["d"] }),
    lane("d", 0.002, 0.003),
  ];
}

describe("RouteGraph", () => {
  it("finds the drive along a chain of lanes", () => {
    const found = new RouteGraph(chain()).find("a", "d");
    expect(found?.lanes).toEqual(["a", "b", "d"]);
    expect(found?.laneChanges).toEqual([]);
  });

  it("reports a lane change as the step into that lane, not as a lane of its own", () => {
    // a2 has no exits, so the only way out of it is across into a.
    const found = new RouteGraph(chain()).find("a2", "d");
    expect(found?.lanes).toEqual(["a2", "a", "b", "d"]);
    expect(found?.laneChanges).toEqual([1]);
  });

  it("returns null when no drive exists, which is the common case here", () => {
    // Nothing leads back up the chain: the map is one-way in most places.
    expect(new RouteGraph(chain()).find("d", "a")).toBeNull();
  });

  it("refuses a route that starts where it ends", () => {
    expect(new RouteGraph(chain()).find("a", "a")).toBeNull();
  });

  it("refuses lanes it has never heard of", () => {
    expect(new RouteGraph(chain()).find("a", "nowhere")).toBeNull();
  });

  it("prefers a junction movement to a lane change when both reach the same lane", () => {
    // A lane that is both an exit and a neighbour must not be reported as a change: the
    // plainer move is the cheaper one, and Python resolves the collision the same way.
    const lanes = [lane("a", 0, 0.001, { exits: ["b"], sideways: ["b"] }), lane("b", 0.001, 0.002)];
    const found = new RouteGraph(lanes).find("a", "b");
    expect(found?.laneChanges).toEqual([]);
  });

  it("lists everywhere reachable, so an unreachable end can be shown as such", () => {
    const graph = new RouteGraph(chain());
    expect([...graph.reachableFrom("a")].sort()).toEqual(["a", "a2", "b", "d"]);
    expect([...graph.reachableFrom("d")]).toEqual(["d"]);
  });

  it("gives the same answer however the lanes are ordered", () => {
    const forward = new RouteGraph(chain()).find("a2", "d");
    const reversed = new RouteGraph([...chain()].reverse()).find("a2", "d");
    expect(reversed?.lanes).toEqual(forward?.lanes);
    expect(reversed?.laneChanges).toEqual(forward?.laneChanges);
  });
});
