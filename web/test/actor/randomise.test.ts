// What the Randomise button places, and where.
//
// The reason these are tests and not a look at the page: the failure this feature exists to
// prevent is silent. A hand-drawn pedestrian on `junction-1` sat 137 m off the ego's route
// and was valid for 68 of 960 frames, and nothing in the page, the converter or the drive
// said so. So the arithmetic that decides how many actors there are, which side of the road
// they are on and when they are standing there is pinned here rather than eyeballed.

import { describe, expect, it } from "vitest";
import { metresBetween } from "../../src/geo.js";
import {
  CYCLIST_LEAD_S,
  CYCLIST_PATH_MAX_M,
  CYCLIST_PATH_MIN_M,
  PEDESTRIAN_DWELL_S,
  MAX_DELAY_FRACTION,

  PER_KM,
  VERGE_M,
  WHOLE_MAP_CAP,
  countFor,
  generateActors,
  halfLane,
  halfWidths,
  lineLengthM,
  offsetMetres,
  type Density,
  type PlacementLane,
  type RandomiseOptions,
} from "../../src/actor/randomise.js";
import type { ActorKind, DrawnActor } from "../../src/actor/types.js";

const LAT = 3.18;
const LON = 101.6;
const KINDS: readonly ActorKind[] = ["pedestrian", "cyclist", "cone", "barrier"];

/** A straight lane running due east, `metres` long. Kerbside of a two-lane carriageway. */
function eastward(id: string, metres: number, from: [number, number] = [LAT, LON]): PlacementLane {
  return {
    id,
    line: [from, offsetMetres(from, metres, 0)],
    width_m: 3.5,
    index: 1,
    count: 2,
  };
}

const NONE: Record<ActorKind, Density> = {
  pedestrian: "none",
  cyclist: "none",
  cone: "none",
  barrier: "none",
};

function options(overrides: Partial<RandomiseOptions> = {}): RandomiseOptions {
  return {
    corridor: [eastward("a", 1000)],
    densities: { ...NONE },
    seed: 1,
    taken: [],
    speeds: { pedestrian_mps: 1.3, cyclist_mps: 5 },
    egoMps: 30 / 3.6,
    crossings: false,
    crossingWidthM: 4,
    ...overrides,
  };
}

function only(kind: ActorKind, density: Density, overrides: Partial<RandomiseOptions> = {}) {
  return generateActors(options({ densities: { ...NONE, [kind]: density }, ...overrides }));
}

describe("offsetMetres", () => {
  it("is the inverse of the distance the clients measure with", () => {
    const there = offsetMetres([LAT, LON], 40, 30);
    expect(metresBetween([LAT, LON], there)).toBeCloseTo(50, 1);
  });

  it("moves north and east in the directions those words mean", () => {
    expect(offsetMetres([LAT, LON], 0, 100)[0]).toBeGreaterThan(LAT);
    expect(offsetMetres([LAT, LON], 100, 0)[1]).toBeGreaterThan(LON);
  });
});

describe("countFor", () => {
  it("places the tabled number per kilometre", () => {
    for (const kind of KINDS) {
      for (const density of ["low", "medium", "dense"] as const) {
        expect(countFor(kind, density, 2000)).toBe(PER_KM[kind][density] * 2);
      }
    }
  });

  it("places none when none was asked for", () => {
    for (const kind of KINDS) expect(countFor(kind, "none", 5000)).toBe(0);
  });

  it("still places one on a route too short to earn one", () => {
    // 300 m of "low" pedestrian rounds to zero, and a button that quietly does nothing is
    // worse than one that places a single actor.
    expect(countFor("pedestrian", "low", 300)).toBe(1);
  });
});

describe("generateActors", () => {
  it("places nothing when every kind is none", () => {
    expect(generateActors(options())).toEqual([]);
  });

  it("places the tabled count for each kind", () => {
    for (const kind of KINDS) {
      const made = only(kind, "medium");
      expect(made).toHaveLength(PER_KM[kind].medium);
      expect(made.every((actor) => actor.kind === kind)).toBe(true);
    }
  });

  it("repeats exactly on the same seed and differs on another", () => {
    const densities = { pedestrian: "dense", cyclist: "dense", cone: "dense", barrier: "dense" } as
      Record<ActorKind, Density>;
    const first = generateActors(options({ densities, seed: 7 }));
    const again = generateActors(options({ densities, seed: 7 }));
    const other = generateActors(options({ densities, seed: 8 }));
    expect(again).toEqual(first);
    expect(other).not.toEqual(first);
    expect(other).toHaveLength(first.length);
  });

  it("never takes a name that is already spoken for", () => {
    const taken = ["ped-1", "ped-2", "bike-1"];
    const made = generateActors(
      options({ densities: { ...NONE, pedestrian: "medium", cyclist: "medium" }, taken }),
    );
    const names = made.map((actor) => actor.name);
    expect(names.some((name) => taken.includes(name))).toBe(false);
    expect(new Set(names).size).toBe(names.length);
  });

  it("gives a walker a path and a static object a position, never both", () => {
    for (const kind of ["pedestrian", "cyclist"] as const) {
      for (const actor of only(kind, "medium")) {
        expect(actor.path?.length).toBeGreaterThanOrEqual(2);
        expect(actor.position).toBeUndefined();
        expect(actor.speed_mps).toBeGreaterThan(0);
      }
    }
    for (const kind of ["cone", "barrier"] as const) {
      for (const actor of only(kind, "medium")) {
        expect(actor.position).toBeDefined();
        expect(actor.path).toBeUndefined();
        expect(actor.speed_mps).toBeUndefined();
      }
    }
  });

  it("spreads actors along the corridor rather than stacking them", () => {
    const made = only("cone", "dense");
    const lons = made.map((actor) => actor.position?.[1] ?? 0);
    expect(new Set(lons).size).toBe(made.length);
    expect(Math.max(...lons) - Math.min(...lons)).toBeGreaterThan(0.005);
  });

  it("ignores junction stubs, which are too short to place anything across", () => {
    const stubs = [eastward("s1", 3), eastward("s2", 2)];
    expect(generateActors(options({ corridor: stubs, densities: { ...NONE, cone: "dense" } })))
      .toEqual([]);
  });
});

describe("where a walker crosses", () => {
  const crossing = (): DrawnActor => only("pedestrian", "low")[0]!;

  it("spans the whole carriageway and clears both edges", () => {
    const lane = eastward("a", 1000);
    const { toKerb, toCentre } = halfWidths(lane);
    const path = crossing().path!;
    expect(lineLengthM(path)).toBeCloseTo(toKerb + toCentre + 2 * VERGE_M, 1);
    // Which is the road's own width plus a verge each side, not half of it.
    expect(lineLengthM(path)).toBeGreaterThan(lane.width_m * lane.count);
  });

  it("starts at the kerb and finishes past the centreline", () => {
    // Travel is due east, driving_side is left, so the kerb is north and the centreline is
    // south. A sign error here would put every crossing on the wrong side of the road and
    // nothing downstream would object.
    const path = crossing().path!;
    expect(path[0]![0]).toBeGreaterThan(LAT);
    expect(path[1]![0]).toBeLessThan(LAT);
  });

  it("paints a zebra only when one was asked for", () => {
    expect(crossing().crossing_width_m).toBeUndefined();
    const painted = only("pedestrian", "low", { crossings: true, crossingWidthM: 4 })[0]!;
    expect(painted.crossing_width_m).toBe(4);
  });
});

describe("when a walker is there", () => {
  it("is out in the road, not still on the kerb, when the car arrives", () => {
    // The contract, and the two bugs it replaced. Aiming the walk at the kerb rather than at
    // the lane put the pedestrian a step off the pavement as the car went by, 3.8 m away,
    // and the ego drove the whole route in exactly the 412 steps it takes with no actors at
    // all. Holding the dwell at twenty seconds and flooring the start at zero left a walker
    // thirty metres in - a four-second arrival - standing there until t=20.
    //
    // So what is pinned is where the walker *is* at the estimated arrival: at the near edge
    // of the ego's own lane, that far into a crossing that started earlier.
    const lane = eastward("a", 1000);
    const { toKerb } = halfWidths(lane);
    const toLaneEdge = toKerb + VERGE_M - halfLane(lane);
    const speed = 1.3;
    const egoMps = 30 / 3.6;
    const latest = (1000 * MAX_DELAY_FRACTION) / egoMps;
    for (const actor of only("pedestrian", "dense")) {
      const along = metresBetween([LAT, LON], [LAT, actor.path![0]![1]]);
      const arrival = along / egoMps;
      const dwell = actor.waits![0]!.seconds;
      const start = actor.start_delay_s ?? 0;
      const walked = (arrival - start - dwell) * speed;
      expect(dwell).toBeLessThanOrEqual(PEDESTRIAN_DWELL_S);
      if (start < latest) expect(walked).toBeCloseTo(toLaneEdge, 0);
      // The clamp caps when it *appears*, which is what has to fall inside the drive; how
      // long it then spends crossing is cut to the scenario like any other track.
      expect(start).toBeLessThanOrEqual(latest);
      // And it is standing there beforehand: a moving actor is valid only while it is
      // walking, and MetaDrive despawns it on the first invalid frame.
      expect(actor.waits).toHaveLength(1);
    }
  });

  it("never lets an actor's delay run past the end of the drive", () => {
    // The pace is a guess. On `junction-1`'s route-1 the default 30 km/h against an actual
    // 37 stretched a 38 s drive to an estimated 47 s, and the cyclist landed at step 350 of
    // 379 - on the map and all but over. The clamp is what stops that being possible.
    const latest = (1000 * MAX_DELAY_FRACTION) / (30 / 3.6);
    for (const kind of ["pedestrian", "cyclist"] as const) {
      const made = only(kind, "dense");
      for (const actor of made) {
        expect(actor.start_delay_s).toBeLessThanOrEqual(latest);
      }
      // And it really is the far end of the route being clamped, not a rate too low to bite.
      expect(Math.max(...made.map((actor) => actor.start_delay_s ?? 0))).toBeCloseTo(latest, 0);
    }
  });

  it("puts a rider on the road just ahead of the car", () => {
    const made = only("cyclist", "medium");
    for (const actor of made) {
      const length = lineLengthM(actor.path!);
      expect(length).toBeGreaterThanOrEqual(CYCLIST_PATH_MIN_M);
      expect(length).toBeLessThanOrEqual(CYCLIST_PATH_MAX_M);
      // Riding the way the car is going, not against it.
      expect(actor.path![1]![1]).toBeGreaterThan(actor.path![0]![1]);
      expect(actor.waits).toBeUndefined();
    }
    // Ahead by the lead time rather than by the twenty seconds a walker waits.
    const first = made[0]!;
    expect(first.start_delay_s).toBeLessThanOrEqual(
      Math.max(0, 1000 / made.length / (30 / 3.6) - CYCLIST_LEAD_S) + 1,
    );
  });

  it("scatters the timing instead when there is no route to arrive on", () => {
    const made = only("pedestrian", "medium", { egoMps: null });
    expect(made.every((actor) => (actor.start_delay_s ?? -1) >= 0)).toBe(true);
    // Still standing at the kerb: without a route the arrival is unknown, not irrelevant.
    expect(made.every((actor) => actor.waits?.length === 1)).toBe(true);
  });
});

describe("the whole-map press", () => {
  it("is capped, and keeps the mix it was asked for", () => {
    // 405 lanes is `mosque`; "dense" over all of them is thousands of actors and a file
    // nobody can edit, which is the outcome this feature exists to avoid.
    const corridor = Array.from({ length: 200 }, (_, index) =>
      eastward(`l${index}`, 200, offsetMetres([LAT, LON], 0, index * 50)),
    );
    const densities = { pedestrian: "dense", cyclist: "dense", cone: "dense", barrier: "dense" } as
      Record<ActorKind, Density>;
    const uncapped = generateActors(options({ corridor, densities, egoMps: null }));
    expect(uncapped.length).toBeGreaterThan(WHOLE_MAP_CAP);

    const capped = generateActors(
      options({ corridor, densities, egoMps: null, cap: WHOLE_MAP_CAP }),
    );
    expect(capped.length).toBeLessThanOrEqual(WHOLE_MAP_CAP);
    for (const kind of KINDS) {
      expect(capped.some((actor) => actor.kind === kind)).toBe(true);
    }
  });
});
