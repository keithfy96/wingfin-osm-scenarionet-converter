// Building and validating the actors.json this page exports.
//
// MetaDrive never reads this file. It is an exchange between the browser and
// `osm-scenario convert --actors`, which turns each entry into a `tracks` entry the stock
// `ScenarioTrafficManager` replays - the same arrangement `routes.json` and `signals.json`
// already use.
//
// Every rule here has a twin in `osm_scenario/actors.py`, and the twin is the one that
// decides. Checking in the browser too is not belt and braces: it is the difference between
// being told which actor is wrong while it is still on screen, and being told at convert
// time with nothing to click on.

import type { ActorIdentity, ActorKind, ActorsFile, DrawnActor } from "./types.js";

export class ActorsFileError extends Error {}

/** Matches `actors._ACTOR_NAME`, which matches `_ROUTE_NAME` and `_GROUP_NAME`.
 *
 * An actor name is a track key, and a track key reaches MetaDrive's logs. One rule for all
 * three Stage 6 files so a person does not have to remember three. */
export const ACTOR_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/;

/** Kinds that walk. The others stand where they are put. Mirrors `actors.BODIES`. */
export const MOVING: ReadonlySet<ActorKind> = new Set<ActorKind>(["pedestrian", "cyclist"]);

export const KINDS: readonly ActorKind[] = ["pedestrian", "cyclist", "cone", "barrier"];

export function nameProblem(name: string, taken: Iterable<string>): string | null {
  if (!ACTOR_NAME.test(name)) {
    return "Use 1-40 letters, digits or hyphens: the name becomes this actor's track key.";
  }
  // The recorded car's own key. Two tracks under one key is one track, and the survivor
  // would be whichever the converter wrote second.
  if (name === "ego") {
    return "'ego' is the recorded car's own track key. Pick another name.";
  }
  for (const existing of taken) {
    if (existing === name) return `There is already an actor called ${name}.`;
  }
  return null;
}

export function serializeActors(
  identity: ActorIdentity,
  actors: DrawnActor[],
  version: number,
): string {
  const file: ActorsFile = { actors_version: version, identity, actors };
  return `${JSON.stringify(file, null, 2)}\n`;
}

function isPair(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "number" &&
    typeof value[1] === "number" &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

function readOne(entry: unknown, index: number): DrawnActor {
  const actor = entry as Partial<DrawnActor>;
  if (!actor || typeof actor !== "object") {
    throw new ActorsFileError(`Actor ${index} is not an object.`);
  }
  if (typeof actor.name !== "string") {
    throw new ActorsFileError(`Actor ${index} has no name.`);
  }
  if (!KINDS.includes(actor.kind as ActorKind)) {
    throw new ActorsFileError(
      `Actor ${index} (${actor.name}) has kind ${String(actor.kind)}; expected one of ${KINDS.join(", ")}.`,
    );
  }
  const kind = actor.kind as ActorKind;

  if (!MOVING.has(kind)) {
    if (!isPair(actor.position)) {
      throw new ActorsFileError(`Actor ${index} (${actor.name}) is a ${kind} and needs a position.`);
    }
    return {
      name: actor.name,
      kind,
      position: actor.position,
      heading_rad: typeof actor.heading_rad === "number" ? actor.heading_rad : 0,
    };
  }

  if (!Array.isArray(actor.path) || actor.path.length < 2 || !actor.path.every(isPair)) {
    throw new ActorsFileError(
      `Actor ${index} (${actor.name}) needs a path of at least two [lat, lon] points.`,
    );
  }
  if (typeof actor.speed_mps !== "number" || !(actor.speed_mps >= 0.05)) {
    throw new ActorsFileError(
      `Actor ${index} (${actor.name}) needs a speed of at least 0.05 m/s.`,
    );
  }
  const waits = Array.isArray(actor.waits) ? actor.waits : [];
  for (const [position, wait] of waits.entries()) {
    if (
      !wait ||
      typeof wait.at_m !== "number" ||
      typeof wait.seconds !== "number" ||
      wait.at_m < 0 ||
      wait.seconds < 0
    ) {
      throw new ActorsFileError(`Actor ${index} (${actor.name}) wait ${position} is not usable.`);
    }
  }
  const out: DrawnActor = {
    name: actor.name,
    kind,
    path: actor.path,
    speed_mps: actor.speed_mps,
    start_delay_s: typeof actor.start_delay_s === "number" ? actor.start_delay_s : 0,
  };
  if (waits.length) out.waits = waits;
  if (typeof actor.crossing_width_m === "number") out.crossing_width_m = actor.crossing_width_m;
  return out;
}

/** Read an actors file back, refusing one drawn on a different lane model.
 *
 * The identity check matters more here than it does for routes, and is the only check
 * available. A route names lane ids, so a stale one at least *can* name something that has
 * gone; an actor path is geometry, so a stale one names nothing and fails silently - it just
 * puts a pedestrian somewhere else, quite possibly in a live carriageway.
 */
export function parseActors(
  raw: string,
  identity: ActorIdentity,
  version: number,
): DrawnActor[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new ActorsFileError("File is not valid JSON.");
  }
  const candidate = parsed as Partial<ActorsFile>;
  if (!candidate || typeof candidate !== "object") {
    throw new ActorsFileError("File is not an actor plan.");
  }
  if (candidate.actors_version !== version) {
    throw new ActorsFileError(
      `Unsupported actors_version ${String(candidate.actors_version)}; this page reads ${version}.`,
    );
  }
  const found = candidate.identity;
  if (!found || typeof found !== "object") {
    throw new ActorsFileError("File has no identity block, so it cannot be checked.");
  }
  if (found.generation_fingerprint !== identity.generation_fingerprint) {
    throw new ActorsFileError(
      "These actors were drawn on a different generation of the map. Place them again on this one.",
    );
  }
  if (found.reviewed_lane_model_sha256 !== identity.reviewed_lane_model_sha256) {
    throw new ActorsFileError(
      "These actors were drawn on a lane model that has since been re-reviewed. Place them again.",
    );
  }
  if (!Array.isArray(candidate.actors)) {
    throw new ActorsFileError("File has no actors array.");
  }

  const out: DrawnActor[] = [];
  const seen = new Set<string>();
  for (const [index, entry] of candidate.actors.entries()) {
    const actor = readOne(entry, index);
    const problem = nameProblem(actor.name, seen);
    if (problem) throw new ActorsFileError(`Actor ${index}: ${problem}`);
    seen.add(actor.name);
    out.push(actor);
  }
  return out;
}

/** Metres along a drawn path, on the sphere. Used for the wait positions and the panel's
 * length readout, so the page and `actors.py` agree about where "12 m along" is. */
export function pathLengthM(path: readonly [number, number][]): number {
  let total = 0;
  for (let index = 1; index < path.length; index += 1) {
    const before = path[index - 1];
    const after = path[index];
    if (before && after) total += haversineM(before, after);
  }
  return total;
}

const EARTH_R_M = 6371008.8;

export function haversineM(a: readonly [number, number], b: readonly [number, number]): number {
  const toRad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * toRad;
  const dLon = (b[1] - a[1]) * toRad;
  const lat1 = a[0] * toRad;
  const lat2 = b[0] * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_R_M * Math.asin(Math.min(1, Math.sqrt(h)));
}
