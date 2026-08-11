// The routes.json this page exports, and the identity check that keeps it honest.

import { describe, expect, it } from "vitest";
import {
  nameProblem,
  parseRoutes,
  RoutesFileError,
  serializeRoutes,
} from "../../src/route/routes-file.js";
import type { RouteIdentity } from "../../src/route/types.js";

const IDENTITY: RouteIdentity = {
  generation_fingerprint: "fingerprint",
  reviewed_lane_model_sha256: "model-sha",
};

const ROUTES = [{ name: "kenanga-offramp", start_lane: "aaa", end_lane: "bbb" }];

describe("route names", () => {
  it("accepts what a filename can carry", () => {
    expect(nameProblem("kenanga-offramp", [])).toBeNull();
  });

  it.each(["", "has space", "slash/es", "dots.", "-leading", "x".repeat(41)])(
    "refuses %o, because the name becomes part of the scenario filename",
    (name) => {
      expect(nameProblem(name, [])).not.toBeNull();
    },
  );

  it("refuses a name already taken, which would write two routes to one file", () => {
    expect(nameProblem("a", ["a"])).toMatch(/already/);
  });
});

describe("routes.json", () => {
  it("round-trips", () => {
    const raw = serializeRoutes(IDENTITY, ROUTES, 1);
    expect(parseRoutes(raw, IDENTITY, 1)).toEqual(ROUTES);
  });

  it("ends with a newline, like every other file this repo writes", () => {
    expect(serializeRoutes(IDENTITY, ROUTES, 1).endsWith("\n")).toBe(true);
  });

  it("refuses routes drawn on a different generation of the map", () => {
    const raw = serializeRoutes(
      { ...IDENTITY, generation_fingerprint: "somewhere-else" },
      ROUTES,
      1,
    );
    expect(() => parseRoutes(raw, IDENTITY, 1)).toThrow(/different generation/);
  });

  it("refuses routes drawn before the model was re-reviewed", () => {
    const raw = serializeRoutes({ ...IDENTITY, reviewed_lane_model_sha256: "other" }, ROUTES, 1);
    expect(() => parseRoutes(raw, IDENTITY, 1)).toThrow(/re-reviewed/);
  });

  it("refuses a version it does not read", () => {
    const raw = serializeRoutes(IDENTITY, ROUTES, 2);
    expect(() => parseRoutes(raw, IDENTITY, 1)).toThrow(/routes_version/);
  });

  it.each([
    ["not json at all", /valid JSON/],
    ['{"routes_version":1}', /identity/],
  ])("refuses %o", (raw, message) => {
    expect(() => parseRoutes(raw, IDENTITY, 1)).toThrow(message);
  });

  it("refuses a route missing an end", () => {
    const raw = JSON.stringify({
      routes_version: 1,
      identity: IDENTITY,
      routes: [{ name: "a", start_lane: "aaa" }],
    });
    expect(() => parseRoutes(raw, IDENTITY, 1)).toThrow(RoutesFileError);
  });
});
