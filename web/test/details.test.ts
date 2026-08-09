import { describe, expect, it } from "vitest";

import { FeatureIndex, laneSide } from "../src/details.js";
import type { GeoJsonFeature } from "../src/types.js";
import { finding } from "./fixtures.js";

function feature(properties: Record<string, unknown>): GeoJsonFeature {
  return { type: "Feature", geometry: { type: "LineString", coordinates: [] }, properties };
}

function lane(id: string, index: number, count: number, extra: Record<string, unknown> = {}) {
  return feature({
    id,
    kind: "lane_centerline",
    lane_index: index,
    lane_count: count,
    entry_lanes: [],
    exit_lanes: [],
    ...extra,
  });
}

function connector(id: string, from: string, to: string, movement = "through") {
  return feature({ id, kind: "connector", from_lane_id: from, to_lane_id: to, movement, status: "active" });
}

describe("laneSide", () => {
  it("names the sides centre-out, so idx0 is the offside lane", () => {
    // Indices run centre-out: idx0 hugs the centreline, idx(n-1) is kerbside.
    expect(laneSide(0, 3)).toBe("offside");
    expect(laneSide(1, 3)).toBe("middle");
    expect(laneSide(2, 3)).toBe("nearside");
    expect(laneSide(0, 1)).toBe("single lane");
  });
});

describe("FeatureIndex", () => {
  it("labels a lane the way a reviewer reads it off the map", () => {
    const index = new FeatureIndex([lane("lane-mid", 1, 3)], []);
    // Two findings of one rule share a reason verbatim; the lane label is the only
    // thing that tells them apart in the queue.
    expect(index.label("lane-mid")).toBe("lane-mid · lane 2/3 middle");
    expect(index.describe("lane-mid")).toBe("Lane lane-mid · lane 2/3 middle");
  });

  it("names the way, because two lanes at a node are routinely both 'lane 1/2 offside'", () => {
    const index = new FeatureIndex(
      [
        lane("lane-x", 0, 2, { source_way_ids: ["756118314"] }),
        lane("lane-y", 0, 2, { source_way_ids: ["39619063"] }),
      ],
      [],
    );
    expect(index.label("lane-x")).toBe("lane-x · way 756118314 lane 1/2 offside");
    expect(index.label("lane-y")).toBe("lane-y · way 39619063 lane 1/2 offside");
    expect(index.label("lane-x")).not.toBe(index.label("lane-y"));
    expect(index.shortLabel("lane-x")).toBe("lane-x way 756118314 lane 1/2");
  });

  it("takes the generator's word for which lane approaches and which is arrived at", () => {
    // A finding that names lanes rather than a connector cannot be oriented by reading
    // a from/to off the feature, so the payload states it and this must prefer that.
    const index = new FeatureIndex(
      [lane("lane-in", 1, 2), lane("lane-out", 1, 2)],
      [],
    );
    const ends = index.movementEnds(["lane-in", "lane-out"], {
      "lane-in": "approach",
      "lane-out": "destination",
    });
    expect(ends).toEqual({ entry: ["lane-in"], exit: ["lane-out"] });
  });

  it("ignores a role for a lane the finding does not name", () => {
    // Roles are keyed by id, so a stale or over-broad map must not light geometry the
    // reviewer was never asked about.
    const index = new FeatureIndex([lane("lane-in", 0, 1), lane("lane-out", 0, 1)], []);
    const ends = index.movementEnds(["lane-in"], {
      "lane-in": "approach",
      "lane-out": "destination",
    });
    expect(ends).toEqual({ entry: ["lane-in"], exit: [] });
  });

  it("still reads a bare connector's own ends when no roles are given", () => {
    const index = new FeatureIndex(
      [lane("lane-a", 0, 1), lane("lane-b", 0, 1), connector("conn-1", "lane-a", "lane-b")],
      [],
    );
    expect(index.movementEnds(["conn-1"])).toEqual({ entry: ["lane-a"], exit: ["lane-b"] });
  });

  it("resolves a bare OSM id into whichever namespace was drawn", () => {
    const index = new FeatureIndex(
      [feature({ id: "way:776021091", kind: "source_way", osm_way_id: "776021091" })],
      [],
    );
    expect(index.resolve("776021091")).toBe("way:776021091");
    expect(index.resolve("way:776021091")).toBe("way:776021091");
    expect(index.resolve("does-not-exist")).toBeNull();
    expect(index.describe("way:776021091")).toBe("OSM way 776021091");
  });

  it("folds continuations in with connector movements and drops duplicates", () => {
    const target = lane("lane-b", 0, 2);
    const source = lane("lane-a", 0, 2, { exit_lanes: ["lane-b", "lane-d", "conn-1"] });
    const index = new FeatureIndex(
      [
        source,
        target,
        lane("lane-c", 0, 1),
        lane("lane-d", 0, 1),
        connector("conn-1", "lane-a", "lane-b"),
        connector("conn-2", "lane-a", "lane-c"),
      ],
      [],
    );

    const links = index.links(source.properties, false);
    // lane-b is reached by both a connector and a continuation; it is one link, and
    // the connector describes it, since that is the movement actually generated.
    // conn-1 appears in exit_lanes but is a connector id, not a lane to travel to.
    expect(links.map((row) => row.id)).toEqual(["lane-d", "lane-b", "lane-c"]);
    expect(links.find((row) => row.id === "lane-b")?.movement).toBe("through");
    // A movement's finding names the connector, so the link has to carry its id or
    // there is no route from "this needs review" to the blocker asking about it.
    expect(links.find((row) => row.id === "lane-c")?.connectorId).toBe("conn-2");
    // A continuation is generated as no connector, so it has nothing to open.
    expect(links.find((row) => row.id === "lane-d")?.connectorId).toBeUndefined();
    expect(index.links(target.properties, true).map((row) => row.id)).toEqual(["lane-a"]);
  });

  it("describes a connector as the movement it is, so two are never alike", () => {
    const index = new FeatureIndex(
      [
        lane("lane-a", 0, 1),
        lane("lane-b", 0, 1),
        feature({
          id: "conn-1",
          kind: "connector",
          from_lane_id: "lane-a",
          to_lane_id: "lane-b",
          movement: "reverse",
          status: "review_required",
          turn_angle_degrees: -160.21,
          junction_node_id: "1928630157",
        }),
      ],
      [],
    );
    // Every ambiguous_connector finding at one node otherwise reads identically.
    expect(index.describeConnector("conn-1")).toBe(
      "lane-a lane 1/1 → lane-b lane 1/1 · reverse -160.2° at node 1928630157",
    );
    expect(index.describeConnector("lane-a")).toBeNull();
  });

  it("reads a movement's two ends off the connector, since the finding names only it", () => {
    const index = new FeatureIndex(
      [lane("lane-a", 0, 1), lane("lane-b", 0, 1), connector("conn-1", "lane-a", "lane-b")],
      [],
    );
    // An ambiguous_connector finding affects the connector alone; entry and exit are
    // the only way to say which lanes the reviewer is being asked about.
    expect(index.movementEnds(["conn-1"])).toEqual({ entry: ["lane-a"], exit: ["lane-b"] });
    // A lane has no direction of its own here, so it contributes neither end.
    expect(index.movementEnds(["lane-a"])).toEqual({ entry: [], exit: [] });
    // A connector whose far lane was never drawn must not name geometry that is absent.
    const dangling = new FeatureIndex(
      [lane("lane-a", 0, 1), connector("conn-2", "lane-a", "lane-gone")],
      [],
    );
    expect(dangling.movementEnds(["conn-2"])).toEqual({ entry: ["lane-a"], exit: [] });
  });

  it("finds a feature's findings through generated and source geometry alike", () => {
    const index = new FeatureIndex(
      [lane("lane-a", 0, 2)],
      [
        finding({ identifier: "f1", affected_feature_ids: ["lane-a"], geometry_ids: [] }),
        finding({ identifier: "f2", affected_feature_ids: [], geometry_ids: ["lane-a"] }),
        finding({ identifier: "f3", affected_feature_ids: [], geometry_ids: ["lane-z"] }),
      ],
    );
    expect(index.findingsFor("lane-a").map((item) => item.identifier)).toEqual(["f1", "f2"]);
    expect(index.findingsFor("way:776021091").map((item) => item.identifier)).toHaveLength(3);
  });
});
