// The browser half of the actor plan rules. Its twin is `tests/unit/test_actors.py`, which
// is the one that decides; this is what tells a person which actor is wrong while it is
// still on screen rather than at convert time with nothing to click on.

import { describe, expect, it } from "vitest";
import {
  ActorsFileError,
  nameProblem,
  parseActors,
  parseGenerated,
  pathLengthM,
  serializeActors,
} from "../../src/actor/actors-file.js";
import type { ActorIdentity, DrawnActor } from "../../src/actor/types.js";

const IDENTITY: ActorIdentity = {
  generation_fingerprint: "fingerprint",
  reviewed_lane_model_sha256: "model-sha",
};

const WALKER: DrawnActor = {
  name: "crossing-north",
  kind: "pedestrian",
  path: [
    [3.1861, 101.6101],
    [3.1863, 101.6101],
  ],
  speed_mps: 1.3,
  start_delay_s: 0,
};

const CONE: DrawnActor = { name: "c1", kind: "cone", position: [3.1861, 101.6101], heading_rad: 0 };

function round(actors: DrawnActor[]): DrawnActor[] {
  return parseActors(serializeActors(IDENTITY, actors, 1), IDENTITY, 1);
}

describe("nameProblem", () => {
  it("accepts a plain name", () => {
    expect(nameProblem("crossing-north", [])).toBeNull();
  });

  it("refuses the recorded car's own track key", () => {
    // Two tracks under one key is one track, and the survivor would be whichever the
    // converter wrote second.
    expect(nameProblem("ego", [])).toMatch(/recorded car/);
  });

  it("refuses a name already taken", () => {
    expect(nameProblem("p1", ["p1"])).toMatch(/already an actor/);
  });

  it("refuses anything a track key cannot be", () => {
    expect(nameProblem("-leading", [])).toMatch(/letters, digits or hyphens/);
    expect(nameProblem("", [])).toMatch(/letters, digits or hyphens/);
  });
});

describe("parseActors", () => {
  it("round-trips what the page writes", () => {
    expect(round([WALKER, CONE])).toEqual([WALKER, CONE]);
  });

  it("keeps a crossing width and the waits", () => {
    const asked: DrawnActor = {
      ...WALKER,
      crossing_width_m: 4,
      waits: [{ at_m: 6, seconds: 8 }],
    };
    expect(round([asked])[0]).toEqual(asked);
  });

  it("refuses a plan drawn on a different generation of the map", () => {
    // The only check available: an actor path names nothing that could be found missing, so
    // a stale plan just puts a pedestrian somewhere else and nothing downstream notices.
    const stale = serializeActors(
      { ...IDENTITY, generation_fingerprint: "different" },
      [WALKER],
      1,
    );
    expect(() => parseActors(stale, IDENTITY, 1)).toThrow(ActorsFileError);
    expect(() => parseActors(stale, IDENTITY, 1)).toThrow(/different generation/);
  });

  it("refuses a file from another version", () => {
    expect(() => parseActors(serializeActors(IDENTITY, [WALKER], 2), IDENTITY, 1)).toThrow(
      /Unsupported actors_version/,
    );
  });

  it("refuses a walker with a one-point path", () => {
    const bad = serializeActors(IDENTITY, [{ ...WALKER, path: [[3.1861, 101.6101]] }], 1);
    expect(() => parseActors(bad, IDENTITY, 1)).toThrow(/at least two/);
  });

  it("refuses a walker with no speed", () => {
    const bad = serializeActors(IDENTITY, [{ ...WALKER, speed_mps: undefined }], 1);
    expect(() => parseActors(bad, IDENTITY, 1)).toThrow(/at least 0.05/);
  });

  it("refuses a static actor with no position", () => {
    const bad = serializeActors(IDENTITY, [{ name: "c", kind: "cone" }], 1);
    expect(() => parseActors(bad, IDENTITY, 1)).toThrow(/needs a position/);
  });

  it("refuses an unknown kind", () => {
    const bad = serializeActors(
      IDENTITY,
      [{ name: "h", kind: "horse" as never, position: [3.1861, 101.6101] }],
      1,
    );
    expect(() => parseActors(bad, IDENTITY, 1)).toThrow(/expected one of/);
  });

  it("refuses two actors under one name", () => {
    const bad = serializeActors(IDENTITY, [CONE, { ...CONE }], 1);
    expect(() => parseActors(bad, IDENTITY, 1)).toThrow(/already an actor/);
  });
});

describe("pathLengthM", () => {
  it("measures the path the wait positions are given along", () => {
    // 0.0002 degrees of latitude is about 22.2 m anywhere on earth.
    expect(
      pathLengthM([
        [3.1861, 101.6101],
        [3.1863, 101.6101],
      ]),
    ).toBeCloseTo(22.2, 0);
  });

  it("is zero for a path with nothing in it", () => {
    expect(pathLengthM([])).toBe(0);
  });
});

// What made a file, carried in the file. `osm_scenario/actors.py` reads the keys it wants by
// name and ignores this one, so it needed no version bump - `tests/unit/test_actors.py` pins
// that the converter still accepts a file carrying it.
describe("the generated block", () => {
  it("is written when there is one, and read back", () => {
    const raw = serializeActors(IDENTITY, [WALKER], 1, { seed: 835819, objects: 430 });
    expect(JSON.parse(raw).generated).toEqual({ seed: 835819, objects: 430 });
    expect(parseGenerated(raw)).toEqual({ seed: 835819, objects: 430 });
  });

  it("is left out of a file that was drawn by hand", () => {
    const raw = serializeActors(IDENTITY, [WALKER], 1, null);
    expect("generated" in JSON.parse(raw)).toBe(false);
    expect(parseGenerated(raw)).toBeNull();
  });

  it("never fails a load, whatever shape it is in", () => {
    // Provenance, not content: a file whose note is damaged still holds good actors, and
    // refusing it over a number nothing downstream reads would be the wrong trade.
    for (const bad of ["{}", "not json", '{"generated":3}', '{"generated":{"seed":"1"}}',
      '{"generated":{"seed":1}}', '{"generated":{"seed":1,"objects":null}}']) {
      expect(parseGenerated(bad)).toBeNull();
    }
  });

  it("does not stop the actors themselves being read", () => {
    const raw = serializeActors(IDENTITY, [WALKER, CONE], 1, { seed: 7, objects: 2 });
    expect(parseActors(raw, IDENTITY, 1)).toHaveLength(2);
  });
});
