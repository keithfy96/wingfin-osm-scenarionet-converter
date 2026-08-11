// Building and validating the routes.json this page exports.
//
// MetaDrive never reads this file. It is an exchange between the browser and
// `osm-scenario convert --routes`, which is the only thing that turns a pair of lane ids
// into a scenario - the same arrangement Stage 3 uses for `review.json`.

import type { ChosenRoute, RouteIdentity, RoutesFile } from "./types.js";

export class RoutesFileError extends Error {}

/** Matches `conversion._ROUTE_NAME`.
 *
 * The name reaches the scenario filename, which MetaDrive keys the whole dataset on and
 * accepts only when it starts `sd_`, so anything a filesystem would treat specially is
 * rejected here rather than at convert time.
 */
export const ROUTE_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/;

export function nameProblem(name: string, taken: Iterable<string>): string | null {
  if (!ROUTE_NAME.test(name)) {
    return "Use 1-40 letters, digits or hyphens: the name becomes part of the scenario filename.";
  }
  for (const existing of taken) {
    if (existing === name) return `There is already a route called ${name}.`;
  }
  return null;
}

export function serializeRoutes(identity: RouteIdentity, routes: ChosenRoute[], version: number): string {
  const file: RoutesFile = { routes_version: version, identity, routes };
  return `${JSON.stringify(file, null, 2)}\n`;
}

/** Read a routes file back, refusing one drawn on a different lane model.
 *
 * Lane ids are content addressed, so applying a stale file does not fail loudly: it either
 * names lanes that no longer exist or, worse, names lanes that now sit somewhere else. The
 * converter refuses the same mismatch; catching it here means the page can say so while
 * the routes are still on screen.
 */
export function parseRoutes(raw: string, identity: RouteIdentity, version: number): ChosenRoute[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new RoutesFileError("File is not valid JSON.");
  }
  const candidate = parsed as Partial<RoutesFile>;
  if (!candidate || typeof candidate !== "object") {
    throw new RoutesFileError("File is not a route selection.");
  }
  if (candidate.routes_version !== version) {
    throw new RoutesFileError(
      `Unsupported routes_version ${String(candidate.routes_version)}; this page reads ${version}.`,
    );
  }
  const found = candidate.identity;
  if (!found || typeof found !== "object") {
    throw new RoutesFileError("File has no identity block, so it cannot be checked.");
  }
  if (found.generation_fingerprint !== identity.generation_fingerprint) {
    throw new RoutesFileError(
      "These routes were drawn on a different generation of the map. Pick them again on this one.",
    );
  }
  if (found.reviewed_lane_model_sha256 !== identity.reviewed_lane_model_sha256) {
    throw new RoutesFileError(
      "These routes were drawn on a lane model that has since been re-reviewed. Pick them again.",
    );
  }
  if (!Array.isArray(candidate.routes)) {
    throw new RoutesFileError("File has no routes array.");
  }

  const out: ChosenRoute[] = [];
  const seen = new Set<string>();
  for (const [index, entry] of candidate.routes.entries()) {
    const route = entry as Partial<ChosenRoute>;
    if (typeof route.name !== "string" || typeof route.start_lane !== "string" || typeof route.end_lane !== "string") {
      throw new RoutesFileError(`Route ${index} is missing a name, a start or an end.`);
    }
    const problem = nameProblem(route.name, seen);
    if (problem) throw new RoutesFileError(`Route ${index}: ${problem}`);
    seen.add(route.name);
    out.push({ name: route.name, start_lane: route.start_lane, end_lane: route.end_lane });
  }
  return out;
}
