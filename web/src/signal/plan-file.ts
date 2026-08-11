// Building and validating the signals.json this page exports.
//
// MetaDrive never reads this file. It is an exchange between the browser and
// `osm-scenario convert --signals`, which turns a set of lane ids and three numbers into
// the traffic lights in the pickle - the same arrangement the route builder uses for
// routes.json, and Stage 3 for review.json.
//
// Every check here is also made in `signal_plan.read_signal_plan`. That duplication is
// deliberate: the converter must refuse a bad plan whatever wrote it, and the page must be
// able to say why while the plan is still on screen.

import type { PhaseGroup, SignalIdentity, SignalsFile } from "./types.js";

export class SignalsFileError extends Error {}

/** Matches `signal_plan._GROUP_NAME`. */
export const GROUP_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/;

export function nameProblem(name: string, taken: Iterable<string>): string | null {
  if (!GROUP_NAME.test(name)) {
    return "Use 1-40 letters, digits or hyphens.";
  }
  for (const existing of taken) {
    if (existing === name) return `There is already a phase group called ${name}.`;
  }
  return null;
}

/** Why this group's timing does not fit the cycle, or null.
 *
 * Red is the remainder rather than a number of its own, so green plus yellow is the only
 * thing that can overrun. An all-red gap between two arms is expressed by giving both a
 * green shorter than their share - which is how a real plan expresses it too.
 */
export function timingProblem(
  group: Pick<PhaseGroup, "green_seconds" | "yellow_seconds" | "offset_seconds">,
  cycleSeconds: number,
): string | null {
  const values = [group.green_seconds, group.yellow_seconds, group.offset_seconds];
  if (values.some((value) => !Number.isFinite(value))) return "Green, yellow and offset must be numbers.";
  if (group.green_seconds < 0 || group.yellow_seconds < 0 || group.offset_seconds < 0) {
    return "Green, yellow and offset cannot be negative.";
  }
  if (group.green_seconds + group.yellow_seconds > cycleSeconds) {
    return `Green plus yellow is ${(group.green_seconds + group.yellow_seconds).toFixed(
      1,
    )} s, longer than the ${cycleSeconds.toFixed(1)} s cycle.`;
  }
  return null;
}

export function serializeSignals(
  identity: SignalIdentity,
  cycleSeconds: number,
  groups: PhaseGroup[],
  version: number,
): string {
  const file: SignalsFile = {
    signals_version: version,
    identity,
    cycle_seconds: cycleSeconds,
    groups,
  };
  return `${JSON.stringify(file, null, 2)}\n`;
}

/** Read a signal plan back, refusing one drawn on a different lane model.
 *
 * Lane ids are content addressed, so applying a stale plan does not fail loudly: it either
 * names lanes that no longer exist or, worse, names lanes that now sit somewhere else and
 * puts a red light across the wrong road.
 */
export function parseSignals(
  raw: string,
  identity: SignalIdentity,
  version: number,
  knownLanes: ReadonlySet<string>,
): { cycleSeconds: number; groups: PhaseGroup[] } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new SignalsFileError("File is not valid JSON.");
  }
  const candidate = parsed as Partial<SignalsFile>;
  if (!candidate || typeof candidate !== "object") {
    throw new SignalsFileError("File is not a signal plan.");
  }
  if (candidate.signals_version !== version) {
    throw new SignalsFileError(
      `Unsupported signals_version ${String(candidate.signals_version)}; this page reads ${version}.`,
    );
  }
  const found = candidate.identity;
  if (!found || typeof found !== "object") {
    throw new SignalsFileError("File has no identity block, so it cannot be checked.");
  }
  if (found.generation_fingerprint !== identity.generation_fingerprint) {
    throw new SignalsFileError(
      "This plan was drawn on a different generation of the map. Place the lights again on this one.",
    );
  }
  if (found.reviewed_lane_model_sha256 !== identity.reviewed_lane_model_sha256) {
    throw new SignalsFileError(
      "This plan was drawn on a lane model that has since been re-reviewed. Place the lights again.",
    );
  }
  const cycleSeconds = candidate.cycle_seconds;
  if (typeof cycleSeconds !== "number" || !Number.isFinite(cycleSeconds) || cycleSeconds <= 0) {
    throw new SignalsFileError("File has no usable cycle_seconds.");
  }
  if (!Array.isArray(candidate.groups) || candidate.groups.length === 0) {
    throw new SignalsFileError("File has no phase groups.");
  }

  const groups: PhaseGroup[] = [];
  const names = new Set<string>();
  const claimed = new Map<string, string>();
  for (const [index, entry] of candidate.groups.entries()) {
    const group = entry as Partial<PhaseGroup>;
    if (typeof group.name !== "string" || !Array.isArray(group.lanes)) {
      throw new SignalsFileError(`Phase group ${index} is missing a name or its lanes.`);
    }
    const named = nameProblem(group.name, names);
    if (named) throw new SignalsFileError(`Phase group ${index}: ${named}`);
    names.add(group.name);

    if (group.lanes.length === 0) {
      throw new SignalsFileError(`Phase group ${group.name} signals no lanes.`);
    }
    for (const lane of group.lanes) {
      if (typeof lane !== "string" || !knownLanes.has(lane)) {
        throw new SignalsFileError(
          `Phase group ${group.name} signals ${String(lane)}, which is not a lane on this map.`,
        );
      }
      const owner = claimed.get(lane);
      if (owner !== undefined) {
        throw new SignalsFileError(
          `Lane ${lane} is in both ${owner} and ${group.name}; a lane can only show one colour at a time.`,
        );
      }
      claimed.set(lane, group.name);
    }

    const timing = {
      green_seconds: Number(group.green_seconds),
      yellow_seconds: Number(group.yellow_seconds),
      offset_seconds: Number(group.offset_seconds ?? 0),
    };
    const problem = timingProblem(timing, cycleSeconds);
    if (problem) throw new SignalsFileError(`Phase group ${group.name}: ${problem}`);

    groups.push({ name: group.name, lanes: [...group.lanes], ...timing });
  }
  return { cycleSeconds, groups };
}
